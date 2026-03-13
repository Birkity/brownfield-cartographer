import unittest
import uuid
from pathlib import Path
import shutil

from src.agents.hydrologist import Hydrologist
from src.agents.surveyor import Surveyor

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = WORKSPACE_ROOT / "tests" / ".tmp"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def _build_minimal_dbt_repo(root: Path) -> None:
    _write(root / "dbt_project.yml", """
name: mini_project
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
        description: Raw order events
        columns:
          - name: id
""")
    _write(root / "models" / "staging" / "stg_orders.sql", """
select
  id as order_id
from {{ source('ecom', 'orders') }}
""")
    _write(root / "models" / "marts" / "orders.sql", """
select
  order_id
from {{ ref('stg_orders') }}
""")
    _write(root / "models" / "marts" / "orders.yml", """
version: 2

models:
  - name: orders
    description: Orders mart for onboarding tests
    columns:
      - name: order_id
""")
    _write(root / "seeds" / "order_status.csv", """
status
placed
shipped
""")


class Phase1Phase2IntegrationTests(unittest.TestCase):
    def _make_repo_root(self) -> Path:
        repo_root = TEST_TMP_ROOT / f"repo_{uuid.uuid4().hex}"
        repo_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(repo_root, ignore_errors=True))
        return repo_root

    def test_surveyor_detects_dbt_project_and_ref_edges(self) -> None:
        repo_root = self._make_repo_root()
        _build_minimal_dbt_repo(repo_root)

        result = Surveyor().run(repo_root)

        self.assertEqual(result.stats["project_type"], "dbt")
        self.assertEqual(result.stats["dbt_ref_edges"], 1)
        self.assertGreaterEqual(result.stats["files_scanned"], 5)

        orders_module = result.graph.get_module("models/marts/orders.sql")
        self.assertIsNotNone(orders_module)
        self.assertEqual(orders_module.dbt_refs, ["stg_orders"])

        self.assertTrue(
            result.graph._g.has_edge("models/marts/orders.sql", "models/staging/stg_orders.sql")
        )
        self.assertEqual(
            result.graph._g["models/marts/orders.sql"]["models/staging/stg_orders.sql"]["edge_type"],
            "DBT_REF",
        )

    def test_hydrologist_registers_sources_seeds_and_sql_lineage(self) -> None:
        repo_root = self._make_repo_root()
        _build_minimal_dbt_repo(repo_root)

        surveyor_result = Surveyor().run(repo_root)
        hydrologist_result = Hydrologist().run(surveyor_result.graph, repo_root)
        graph = hydrologist_result.graph

        self.assertTrue(hydrologist_result.stats["is_dbt_project"])
        self.assertEqual(hydrologist_result.stats["sources_registered"], 1)
        self.assertEqual(hydrologist_result.stats["seeds_found"], 1)
        self.assertEqual(hydrologist_result.stats["sql_files_analyzed"], 2)
        self.assertEqual(hydrologist_result.stats["transformations_total"], 2)

        self.assertIsNotNone(graph.get_dataset("source.ecom.orders"))
        self.assertIsNotNone(graph.get_dataset("model.stg_orders"))
        orders_dataset = graph.get_dataset("model.orders")
        self.assertIsNotNone(orders_dataset)
        self.assertEqual(orders_dataset.description, "Orders mart for onboarding tests")

        stg_xform = next(
            xform for xform in graph.all_transformations()
            if xform.source_file == "models/staging/stg_orders.sql"
        )
        self.assertEqual(stg_xform.line_range, (1, 3))

        self.assertTrue(graph._g.has_edge("source.ecom.orders", "sql:models/staging/stg_orders.sql"))
        self.assertEqual(
            graph._g["source.ecom.orders"]["sql:models/staging/stg_orders.sql"]["edge_type"],
            "CONSUMES",
        )
        self.assertTrue(graph._g.has_edge("sql:models/staging/stg_orders.sql", "model.stg_orders"))
        self.assertEqual(
            graph._g["sql:models/staging/stg_orders.sql"]["model.stg_orders"]["edge_type"],
            "PRODUCES",
        )


if __name__ == "__main__":
    unittest.main()
