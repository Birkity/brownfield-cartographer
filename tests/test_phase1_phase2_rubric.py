import json
import logging
import shutil
import unittest
import uuid
from pathlib import Path

from click.testing import CliRunner

from src.agents.hydrologist import Hydrologist
from src.agents.surveyor import Surveyor
from src.analyzers.python_dataflow import analyze_python_file
from src.analyzers.sql_lineage import analyze_sql_file
from src.cli import cli
from src.orchestrator import run_phase1, run_phase2

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = WORKSPACE_ROOT / "tests" / ".tmp"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _make_temp_dir(prefix: str) -> Path:
    path = TEST_TMP_ROOT / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _build_python_repo(root: Path) -> None:
    _write(root / "requirements.txt", """
fastapi==0.110.0
""")
    _write(root / "app.py", """
from pkg.a import public_service


def main():
    return public_service(3)
""")
    _write(root / "pkg" / "__init__.py", "")
    _write(root / "pkg" / "a.py", '''
from .b import helper_value

# service helpers


class Greeter:
    """Friendly greeter."""

    def greet(self, name: str) -> str:
        if name:
            return f"hi {name}"
        return "hi"


def public_service(x: int) -> int:
    if x > 1:
        return helper_value() + x
    return x


def _private_service() -> int:
    return 0
''')
    _write(root / "pkg" / "b.py", """
from .a import Greeter


def helper_value() -> int:
    return len(Greeter.__name__)
""")
    _write(root / "utils" / "lonely.py", """
def orphan() -> str:
    return "unused"
""")
    _write(root / "notebooks" / "exploration.ipynb", """
{
  "cells": [
    {
      "cell_type": "code",
      "source": [
        "import pandas as pd\\n",
        "# notebook comment\\n",
        "df = pd.read_csv('data/orders.csv')\\n"
      ],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": [
        "%%sql\\n",
        "select * from orders\\n"
      ],
      "metadata": {},
      "outputs": []
    }
  ],
  "metadata": {},
  "nbformat": 4,
  "nbformat_minor": 5
}
""")


def _build_mixed_repo(root: Path) -> None:
    _write(root / "dbt_project.yml", """
name: mixed_project
version: "1.0"
profile: default
model-paths: ["models"]
seed-paths: ["seeds"]
""")
    _write(root / "models" / "staging" / "__sources.yml", """
version: 2

sources:
  - name: ecom
    tables:
      - name: orders
        description: Raw orders
        columns:
          - name: id
""")
    _write(root / "models" / "staging" / "stg_orders.sql", """
with src as (
  select id as order_id
  from {{ source('ecom', 'orders') }}
)

select * from src
""")
    _write(root / "models" / "marts" / "orders.sql", """
with staged as (
  select order_id
  from {{ ref('stg_orders') }}
)

select * from staged
""")
    _write(root / "models" / "marts" / "orders.yml", """
version: 2

models:
  - name: orders
    description: Orders mart for pipeline verification
    columns:
      - name: order_id
""")
    _write(root / "seeds" / "order_status.csv", """
status
placed
shipped
""")
    _write(root / "pipelines" / "load_orders.py", """
import pandas as pd

path = "raw/orders.csv"
df = pd.read_csv(path)
df.to_parquet("out/orders.parquet")
""")


def _build_macro_repo(root: Path) -> None:
    _build_mixed_repo(root)
    _write(root / "macros" / "util.sql", """
{% macro cents_to_dollars(column_name) %}
  ({{ column_name }} / 100)
{% endmacro %}
""")


class Phase1Phase2RubricTests(unittest.TestCase):
    def setUp(self) -> None:
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)

    def _cleanup_path(self, path: Path) -> None:
        shutil.rmtree(path, ignore_errors=True)

    def test_phase1_python_ast_import_graph_and_cycles(self) -> None:
        repo_root = _make_temp_dir("phase1_python_repo")
        self.addCleanup(self._cleanup_path, repo_root)
        _build_python_repo(repo_root)

        result = Surveyor().run(repo_root)

        self.assertEqual(result.stats["project_type"], "fastapi")
        self.assertEqual(result.stats["import_edges"], 3)
        self.assertEqual(result.stats["circular_dependency_clusters"], 1)

        module_a = result.graph.get_module("pkg/a.py")
        self.assertIsNotNone(module_a)
        self.assertEqual([fn.name for fn in module_a.functions], ["greet", "public_service", "_private_service"])
        self.assertEqual(module_a.functions[1].signature, "def public_service(x: int) -> int")
        self.assertEqual([cls.name for cls in module_a.classes], ["Greeter"])
        self.assertGreater(module_a.complexity_score, 1.0)
        self.assertGreater(module_a.comment_ratio, 0.0)

        self.assertTrue(result.graph._g.has_edge("app.py", "pkg/a.py"))
        self.assertTrue(result.graph._g.has_edge("pkg/a.py", "pkg/b.py"))
        self.assertTrue(result.graph._g.has_edge("pkg/b.py", "pkg/a.py"))

        notebook_module = result.graph.get_module("notebooks/exploration.ipynb")
        self.assertIsNotNone(notebook_module)
        self.assertEqual(notebook_module.language.value, "notebook")
        self.assertEqual([imp.module for imp in notebook_module.imports], ["pandas"])
        self.assertGreater(notebook_module.comment_ratio, 0.0)

    def test_phase2_python_dataflow_marks_dynamic_references_honestly(self) -> None:
        source = """
import pandas as pd

path = f"raw/{table}.csv"
frame = pd.read_csv(path)
frame.to_parquet("out/orders.parquet")
"""
        result = analyze_python_file(source, "pipelines/example.py")

        self.assertEqual(len(result.records), 2)
        self.assertEqual(result.records[0].extraction_method, "tree_sitter_ast")
        self.assertTrue(result.records[0].is_dynamic)
        self.assertEqual(result.records[0].confidence, 0.5)
        self.assertEqual(result.records[1].target, "out/orders.parquet")
        self.assertFalse(result.records[1].is_dynamic)

    def test_phase2_notebook_dataflow_is_supported(self) -> None:
        repo_root = _make_temp_dir("phase2_notebook_repo")
        self.addCleanup(self._cleanup_path, repo_root)
        _build_python_repo(repo_root)

        surveyor_result = Surveyor().run(repo_root)
        result = Hydrologist().run(surveyor_result.graph, repo_root)

        self.assertEqual(result.stats["python_files_analyzed"], 1)
        datasets = {dataset.name for dataset in result.graph.all_datasets()}
        self.assertIn("data/orders.csv", datasets)
        notebook_read = next(
            xform for xform in result.graph.all_transformations()
            if xform.source_file == "notebooks/exploration.ipynb"
        )
        self.assertEqual(notebook_read.line_range, (4, 4))

    def test_phase2_skips_macros_from_lineage_artifacts(self) -> None:
        repo_root = _make_temp_dir("phase2_macro_repo")
        output_root = _make_temp_dir("phase2_macro_output")
        self.addCleanup(self._cleanup_path, repo_root)
        self.addCleanup(self._cleanup_path, output_root)
        _build_macro_repo(repo_root)

        artifacts, graph, resolved_root = run_phase1(str(repo_root), output_dir=output_root)
        hydro_result = run_phase2(artifacts, graph, resolved_root)
        lineage_payload = json.loads(artifacts.lineage_graph_json.read_text(encoding="utf-8"))
        blind_spots_payload = json.loads(artifacts.blind_spots_json.read_text(encoding="utf-8"))

        self.assertEqual(hydro_result.stats["macro_sql_files_skipped"], 1)
        self.assertNotIn("sql:macros/util.sql", lineage_payload["transformations"])
        self.assertEqual(blind_spots_payload["summary"]["dynamic_transformations"], 0)

    def test_lineage_summary_cli_prints_sources_sinks_and_blast_radius(self) -> None:
        repo_root = _make_temp_dir("phase2_cli_repo")
        output_root = _make_temp_dir("phase2_cli_output")
        self.addCleanup(self._cleanup_path, repo_root)
        self.addCleanup(self._cleanup_path, output_root)
        _build_mixed_repo(repo_root)

        artifacts, graph, resolved_root = run_phase1(str(repo_root), output_dir=output_root)
        run_phase2(artifacts, graph, resolved_root)

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "lineage-summary",
                str(output_root),
                "--node",
                "source.ecom.orders",
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Source Datasets", result.output)
        self.assertIn("Sink Datasets", result.output)
        self.assertIn("Blast Radius: source.ecom.orders", result.output)
        logging.getLogger().handlers.clear()

    def test_phase2_sql_lineage_handles_dbt_refs_sources_ctes_and_line_range(self) -> None:
        sql = """
with upstream as (
  select *
  from {{ source('ecom', 'orders') }}
),
final as (
  select order_id
  from {{ ref('stg_orders') }}
)

select *
from final
join upstream on final.order_id = upstream.id
"""
        result = analyze_sql_file(sql, "models/marts/orders.sql", is_dbt=True)

        self.assertEqual(result.transformation_type, "dbt_model")
        self.assertEqual(result.dbt_refs, ["stg_orders"])
        self.assertEqual(result.dbt_sources, [("ecom", "orders")])
        self.assertIn("source.ecom.orders", result.upstream_tables)
        self.assertIn("model.stg_orders", result.upstream_tables)
        self.assertEqual(result.downstream_tables, ["model.orders"])
        self.assertEqual(result.line_range[0], 1)
        self.assertGreaterEqual(result.line_range[1], 12)

    def test_phase1_phase2_pipeline_writes_artifacts_and_lineage_helpers_work(self) -> None:
        repo_root = _make_temp_dir("phase12_pipeline_repo")
        output_root = _make_temp_dir("phase12_pipeline_output")
        self.addCleanup(self._cleanup_path, repo_root)
        self.addCleanup(self._cleanup_path, output_root)
        _build_mixed_repo(repo_root)

        artifacts, graph, resolved_root = run_phase1(str(repo_root), output_dir=output_root)
        hydro_result = run_phase2(artifacts, graph, resolved_root)

        self.assertTrue(artifacts.module_graph_json.exists())
        self.assertTrue(artifacts.module_graph_modules_json.exists())
        self.assertTrue(artifacts.stats_json.exists())
        self.assertTrue(artifacts.lineage_graph_json.exists())
        self.assertTrue(artifacts.hydrologist_stats_json.exists())
        self.assertTrue(artifacts.blind_spots_json.exists())
        self.assertTrue(artifacts.high_risk_json.exists())

        lineage_payload = json.loads(artifacts.lineage_graph_json.read_text(encoding="utf-8"))
        blind_spots_payload = json.loads(artifacts.blind_spots_json.read_text(encoding="utf-8"))
        high_risk_payload = json.loads(artifacts.high_risk_json.read_text(encoding="utf-8"))

        self.assertIn("source.ecom.orders", lineage_payload["datasets"])
        self.assertIn("model.stg_orders", lineage_payload["datasets"])
        self.assertIn("model.orders", lineage_payload["datasets"])
        self.assertIn("seed.order_status", lineage_payload["datasets"])
        self.assertTrue(any(edge["edge_type"] == "PRODUCES" for edge in lineage_payload["edges"]))
        self.assertTrue(any(edge["edge_type"] == "CONSUMES" for edge in lineage_payload["edges"]))

        self.assertIn("summary", blind_spots_payload)
        self.assertIn("summary", high_risk_payload)

        self.assertIn("source.ecom.orders", graph.find_sources())
        self.assertIn("model.orders", graph.find_sinks())

        blast = graph.blast_radius("source.ecom.orders")
        self.assertIn("sql:models/staging/stg_orders.sql", blast)
        self.assertIn("model.stg_orders", blast)
        self.assertIn("sql:models/marts/orders.sql", blast)
        self.assertIn("model.orders", blast)

        self.assertTrue(hydro_result.stats["is_dbt_project"])
        self.assertGreaterEqual(hydro_result.stats["datasets_total"], 5)
        self.assertGreaterEqual(hydro_result.stats["produces_edges"], 2)
        self.assertGreaterEqual(hydro_result.stats["consumes_edges"], 2)


if __name__ == "__main__":
    unittest.main()
