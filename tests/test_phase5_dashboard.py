from __future__ import annotations

import json
from pathlib import Path
import shutil
import unittest
import uuid

from pydantic import BaseModel

from src.dashboard.data_layer import (
    build_lineage_focus_dot,
    build_module_focus_dot,
    build_overview_metrics,
    coerce_day_one_citation,
    load_code_snippet,
    load_dashboard_bundle,
    module_detail,
    run_navigator_query,
)
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import (
    DatasetNode,
    DayOneCitation,
    FunctionNode,
    ImportInfo,
    Language,
    ModuleNode,
    SemanticEvidence,
    StorageType,
    TransformationNode,
)


class TestPhase5Dashboard(unittest.TestCase):
    def setUp(self) -> None:
        tmp_root = Path(__file__).resolve().parent / ".tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.root = tmp_root / f"dashboard-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.repo_root = self.root / "repo"
        self.artifact_root = self.root / "cartography" / "demo"
        (self.artifact_root / "module_graph").mkdir(parents=True, exist_ok=True)
        (self.artifact_root / "data_lineage").mkdir(parents=True, exist_ok=True)
        (self.artifact_root / "semantics").mkdir(parents=True, exist_ok=True)
        (self.artifact_root / "queries").mkdir(parents=True, exist_ok=True)

        self._write_repo_files()
        self._write_graph_artifacts()
        self._write_supporting_artifacts()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_repo_files(self) -> None:
        source_file = self.repo_root / "models" / "staging" / "stg_orders.sql"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(
            "\n".join(
                [
                    "{{ config(materialized='view') }}",
                    "",
                    "select",
                    "    id as order_id,",
                    "    customer as customer_id,",
                    "    amount / 100.0 as order_total",
                    "from raw_orders",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        macro_file = self.repo_root / "macros" / "cents_to_dollars.sql"
        macro_file.parent.mkdir(parents=True, exist_ok=True)
        macro_file.write_text(
            "{% macro cents_to_dollars(column_name) %}\n{{ column_name }} / 100.0\n{% endmacro %}\n",
            encoding="utf-8",
        )

    def _write_graph_artifacts(self) -> None:
        module_graph = KnowledgeGraph()
        orders_module = ModuleNode(
            path="models/staging/stg_orders.sql",
            abs_path=str(self.repo_root / "models" / "staging" / "stg_orders.sql"),
            language=Language.SQL,
            imports=[
                ImportInfo(module="macros/cents_to_dollars.sql", line=1),
            ],
            functions=[
                FunctionNode(
                    name="main",
                    qualified_name="main",
                    parent_module="models/staging/stg_orders.sql",
                    line=3,
                    end_line=7,
                )
            ],
            role="staging",
            is_hub=True,
            purpose_statement="Standardizes raw orders for downstream analytics.",
            business_logic_score=0.8,
            domain_cluster="Order Analytics",
            doc_drift_level="likely_drift",
            semantic_confidence=0.91,
            semantic_model_used="qwen3-coder",
            semantic_prompt_version="phase3-purpose-v2",
            semantic_fallback_used=False,
            hotspot_fusion_score=0.82,
            semantic_evidence=[
                SemanticEvidence(
                    source_phase="phase3",
                    file_path="models/staging/stg_orders.sql",
                    line_start=4,
                    line_end=6,
                    extraction_method="phase2_lineage",
                    description="Renames columns and converts cents to dollars.",
                )
            ],
            change_velocity_30d=4,
        )
        macro_module = ModuleNode(
            path="macros/cents_to_dollars.sql",
            abs_path=str(self.repo_root / "macros" / "cents_to_dollars.sql"),
            language=Language.SQL,
            role="macro",
            purpose_statement="Shared macro for currency normalization.",
            business_logic_score=0.6,
            domain_cluster="Order Analytics",
            semantic_confidence=0.88,
            hotspot_fusion_score=0.44,
            semantic_evidence=[
                SemanticEvidence(
                    source_phase="phase1",
                    file_path="macros/cents_to_dollars.sql",
                    line_start=1,
                    line_end=3,
                    extraction_method="phase1_symbol",
                    description="Macro definition for cents_to_dollars.",
                )
            ],
        )
        module_graph.add_module(orders_module)
        module_graph.add_module(macro_module)
        module_graph.add_import_edge(
            "models/staging/stg_orders.sql",
            "macros/cents_to_dollars.sql",
            edge_type="IMPORTS",
        )
        module_graph.save(self.artifact_root / "module_graph" / "module_graph.json")

        lineage_graph = KnowledgeGraph()
        source_dataset = DatasetNode(
            name="source.ecom.raw_orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_source",
            source_file="models/staging/__sources.yml",
            confidence=1.0,
            is_source_dataset=True,
        )
        staged_dataset = DatasetNode(
            name="model.stg_orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_model",
            source_file="models/staging/stg_orders.sql",
            confidence=1.0,
            is_sink_dataset=True,
        )
        transformation = TransformationNode(
            id="sql:models/staging/stg_orders.sql",
            transformation_type="dbt_model",
            source_file="models/staging/stg_orders.sql",
            line_range=(3, 7),
            source_datasets=["source.ecom.raw_orders"],
            target_datasets=["model.stg_orders"],
            confidence=1.0,
        )
        lineage_graph.add_dataset_node(source_dataset)
        lineage_graph.add_dataset_node(staged_dataset)
        lineage_graph.add_transformation_node(transformation)
        lineage_graph.add_consumes_edge(transformation.id, source_dataset.name)
        lineage_graph.add_produces_edge(transformation.id, staged_dataset.name)
        lineage_graph.save_lineage(self.artifact_root / "data_lineage" / "lineage_graph.json")

    def _write_supporting_artifacts(self) -> None:
        (self.artifact_root / "module_graph" / "surveyor_stats.json").write_text(
            json.dumps(
                {
                    "repo_root": str(self.repo_root),
                    "project_type": "dbt",
                    "files_scanned": 2,
                    "files_parsed_ok": 2,
                    "import_edges": 1,
                    "dbt_ref_edges": 0,
                    "circular_dependency_clusters": 0,
                    "top_hubs": [["models/staging/stg_orders.sql", 0.25]],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "data_lineage" / "hydrologist_stats.json").write_text(
            json.dumps(
                {
                    "sources_registered": 1,
                    "seeds_found": 0,
                    "sql_files_analyzed": 1,
                    "datasets_total": 2,
                    "transformations_total": 1,
                    "dynamic_transformations": 0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "semanticist_stats.json").write_text(
            json.dumps(
                {
                    "purpose_statements_generated": 2,
                    "domains_found": 1,
                    "semantic_hotspots": 2,
                    "review_queue_items": 1,
                    "documentation_missing_count": 1,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "semantic_enrichment.json").write_text(
            json.dumps(
                {
                    "purpose_statements": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "purpose_statement": "Standardizes raw orders for downstream analytics.",
                            "evidence": [
                                {
                                    "source_phase": "phase3",
                                    "file_path": "models/staging/stg_orders.sql",
                                    "line_start": 4,
                                    "line_end": 6,
                                    "extraction_method": "phase2_lineage",
                                    "description": "Renames columns and converts cents to dollars.",
                                }
                            ],
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "semantic_index.json").write_text(
            json.dumps(
                {
                    "modules": {
                        "models/staging/stg_orders.sql": {
                            "purpose": "Standardizes raw orders for downstream analytics.",
                            "business_logic_score": 0.8,
                            "confidence": 0.91,
                            "key_concepts": ["orders", "currency"],
                        }
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "day_one_answers.json").write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "question": "What does this codebase do at a high level?",
                            "answer": "It standardizes commerce data for analytics.",
                            "confidence": 0.9,
                            "citations": [
                                {
                                    "source_phase": "phase3",
                                    "file_path": "models/staging/stg_orders.sql",
                                    "line_start": 4,
                                    "line_end": 6,
                                    "extraction_method": "phase2_lineage",
                                    "description": "Renames columns and converts cents to dollars.",
                                    "evidence_type": "semantic",
                                }
                            ],
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "fde_day_one_answers.json").write_text(
            json.dumps(
                {
                    "prompt_version": "fde-day-one-v1",
                    "questions": [
                        {
                            "question": "What is the primary data ingestion path?",
                            "answer": "It starts at source.ecom.raw_orders and flows through stg_orders.",
                            "confidence": 0.86,
                            "citations": [
                                {
                                    "source_phase": "phase2",
                                    "file_path": "models/staging/stg_orders.sql",
                                    "line_start": 4,
                                    "line_end": 6,
                                    "extraction_method": "phase2_lineage",
                                    "description": "Transforms raw orders into the staged model.",
                                    "evidence_type": "lineage",
                                }
                            ],
                        }
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "reading_order.json").write_text(
            json.dumps(
                {
                    "reading_order": [
                        {
                            "step": 1,
                            "file_path": "models/staging/stg_orders.sql",
                            "domain": "Order Analytics",
                            "purpose": "Standardizes raw orders for downstream analytics.",
                            "business_logic_score": 0.8,
                            "hotspot_fusion_score": 0.82,
                            "reason": "core business logic",
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantics" / "semantic_review_queue.json").write_text(
            json.dumps(
                {
                    "semantic_review_queue": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "hotspot_fusion_score": 0.82,
                            "semantic_confidence": 0.91,
                            "doc_drift_level": "likely_drift",
                            "reasons": ["documentation drift (likely_drift)"],
                            "evidence": [
                                {
                                    "source_phase": "phase3",
                                    "file_path": "models/staging/stg_orders.sql",
                                    "line_start": 4,
                                    "line_end": 6,
                                    "extraction_method": "phase2_lineage",
                                    "description": "Renames columns and converts cents to dollars.",
                                    "evidence_type": "semantic",
                                }
                            ],
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "semantic_hotspots.json").write_text(
            json.dumps(
                {
                    "semantic_hotspots": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "hotspot_fusion_score": 0.82,
                            "purpose": "Standardizes raw orders for downstream analytics.",
                            "supporting_evidence": [
                                {
                                    "source_phase": "phase3",
                                    "file_path": "models/staging/stg_orders.sql",
                                    "line_start": 4,
                                    "line_end": 6,
                                    "extraction_method": "phase2_lineage",
                                    "description": "Renames columns and converts cents to dollars.",
                                }
                            ],
                        }
                    ]
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (self.artifact_root / "blind_spots.json").write_text(
            json.dumps({"summary": {"total_blind_spots": 0}}, indent=2),
            encoding="utf-8",
        )
        (self.artifact_root / "high_risk_areas.json").write_text(
            json.dumps({"high_risk_areas": []}, indent=2),
            encoding="utf-8",
        )
        (self.artifact_root / "module_graph" / "module_graph.html").write_text(
            "<html><body>module graph</body></html>",
            encoding="utf-8",
        )
        (self.artifact_root / "data_lineage" / "lineage_graph.html").write_text(
            "<html><body>lineage graph</body></html>",
            encoding="utf-8",
        )
        (self.artifact_root / "CODEBASE.md").write_text("# CODEBASE\n", encoding="utf-8")
        (self.artifact_root / "onboarding_brief.md").write_text(
            "# Onboarding Brief\n",
            encoding="utf-8",
        )
        (self.artifact_root / "queries" / "latest-query.json").write_text(
            json.dumps(
                {
                    "timestamp": "2026-03-14T10:00:00Z",
                    "query_type": "repository_overview",
                    "answer": {
                        "question": "What does this repository do?",
                        "answer": "It standardizes commerce data for analytics.",
                        "confidence": 0.9,
                        "citations": [],
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def test_dashboard_bundle_loads_artifacts_and_metrics(self) -> None:
        bundle = load_dashboard_bundle(self.artifact_root)
        metrics = build_overview_metrics(bundle)

        self.assertEqual(bundle.artifact_root, self.artifact_root)
        self.assertEqual(metrics["total_files"], 2)
        self.assertEqual(metrics["datasets"], 2)
        self.assertEqual(metrics["transformations"], 1)
        self.assertEqual(metrics["semantic_domains"], 1)
        self.assertEqual(metrics["hotspots"], 1)
        self.assertEqual(metrics["documentation_drift"], 1)
        self.assertEqual(len(bundle.query_logs), 1)
        self.assertEqual(
            bundle.fde_day_one_answers["questions"][0]["question"],
            "What is the primary data ingestion path?",
        )

    def test_focus_graphs_include_selected_nodes(self) -> None:
        bundle = load_dashboard_bundle(self.artifact_root)

        module_dot = build_module_focus_dot(bundle, "models/staging/stg_orders.sql")
        lineage_dot = build_lineage_focus_dot(bundle, "model.stg_orders")

        self.assertIn("models/staging/stg_orders.sql", module_dot)
        self.assertIn("macros/cents_to_dollars.sql", module_dot)
        self.assertIn("model.stg_orders", lineage_dot)
        self.assertIn("source.ecom.raw_orders", lineage_dot)

    def test_module_detail_and_code_snippet_are_grounded(self) -> None:
        bundle = load_dashboard_bundle(self.artifact_root)

        detail = module_detail(bundle, "models/staging/stg_orders.sql")
        snippet = load_code_snippet(bundle, "models/staging/stg_orders.sql", 4, 6)

        self.assertIsNotNone(detail)
        self.assertEqual(detail["module"].role, "staging")
        self.assertIn("model.stg_orders", detail["lineage"]["produced_datasets"])
        self.assertTrue(snippet.available)
        self.assertIn("4 |     id as order_id,", snippet.text)
        self.assertIn("6 |     amount / 100.0 as order_total", snippet.text)

    def test_run_navigator_query_uses_injected_factory(self) -> None:
        citation = DayOneCitation(
            source_phase="phase4",
            file_path="models/staging/stg_orders.sql",
            line_start=4,
            line_end=6,
            extraction_method="artifact",
            description="Grounded answer.",
            evidence_type="semantic",
        )

        class FakeResponse:
            answer = "Grounded answer."
            confidence = 0.84
            citations = [citation]

        class FakeResult:
            response = FakeResponse()
            query_type = "repository_overview"
            models_used = {"synthesis": "deepseek-v3.1"}
            log_path = self.artifact_root / "queries" / "fake.json"

        class FakeNavigator:
            def __init__(self, artifact_root: Path) -> None:
                self.artifact_root = artifact_root

            def answer_question(self, question: str) -> FakeResult:
                self.question = question
                return FakeResult()

        payload = run_navigator_query(
            self.artifact_root,
            "What does this repository do?",
            navigator_factory=FakeNavigator,
        )

        self.assertEqual(payload["answer"], "Grounded answer.")
        self.assertEqual(payload["query_type"], "repository_overview")
        self.assertEqual(payload["models_used"]["synthesis"], "deepseek-v3.1")
        self.assertEqual(payload["citations"][0]["file_path"], "models/staging/stg_orders.sql")

    def test_coerce_day_one_citation_accepts_foreign_pydantic_model(self) -> None:
        class ForeignCitation(BaseModel):
            source_phase: str
            file_path: str
            line_start: int | None = None
            line_end: int | None = None
            extraction_method: str
            description: str
            evidence_type: str = "semantic"

        foreign = ForeignCitation(
            source_phase="phase3",
            file_path="models/staging/stg_orders.sql",
            line_start=4,
            line_end=6,
            extraction_method="phase2_lineage",
            description="Renames columns and converts cents to dollars.",
            evidence_type="semantic",
        )

        citation = coerce_day_one_citation(foreign)

        self.assertIsInstance(citation, DayOneCitation)
        self.assertEqual(citation.file_path, "models/staging/stg_orders.sql")
        self.assertEqual(citation.line_start, 4)
        self.assertEqual(citation.evidence_type, "semantic")


if __name__ == "__main__":
    unittest.main()
