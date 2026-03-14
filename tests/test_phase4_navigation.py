import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from src.agents.archivist import Archivist
from src.agents.navigator import Navigator
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import DatasetNode, Language, ModuleNode, SemanticEvidence, StorageType, TransformationNode

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ROOT = WORKSPACE_ROOT / "tests" / ".tmp"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _module(path: str, role: str, purpose: str, score: float, line_start: int, line_end: int) -> ModuleNode:
    return ModuleNode(
        path=path,
        abs_path=f"/nonexistent/{path}",
        language=Language.SQL,
        role=role,
        lines_of_code=30,
        purpose_statement=purpose,
        business_logic_score=score,
        semantic_confidence=0.9,
        semantic_evidence=[
            SemanticEvidence(
                source_phase="phase3",
                file_path=path,
                line_start=line_start,
                line_end=line_end,
                extraction_method="llm_inference",
                description=purpose,
            )
        ],
    )


def _build_phase4_artifacts(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)

    module_graph = KnowledgeGraph()
    stg_orders = _module(
        "models/staging/stg_orders.sql",
        "staging",
        "Transforms raw orders into analytics-ready staged orders.",
        0.7,
        10,
        20,
    )
    orders = _module(
        "models/marts/orders.sql",
        "mart",
        "Builds the orders mart used for downstream reporting.",
        0.85,
        12,
        32,
    )
    module_graph.add_module(stg_orders)
    module_graph.add_module(orders)
    module_graph.add_import_edge(
        "models/marts/orders.sql",
        "models/staging/stg_orders.sql",
        edge_type="DBT_REF",
        evidence={"source_file": "models/marts/orders.sql", "ref_name": "stg_orders"},
    )
    module_graph.save(root / "module_graph" / "module_graph.json")

    lineage_graph = KnowledgeGraph()
    for dataset in [
        DatasetNode(
            name="source.ecom.raw_orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_source",
            source_file="models/staging/__sources.yml",
            description="Raw order events",
            is_source_dataset=True,
        ),
        DatasetNode(
            name="model.stg_orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_model",
            source_file="models/staging/stg_orders.sql",
            is_intermediate_model=True,
        ),
        DatasetNode(
            name="model.orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_model",
            source_file="models/marts/orders.sql",
            is_sink_dataset=True,
            is_final_model=True,
        ),
    ]:
        lineage_graph.add_dataset_node(dataset)

    stg_xform = TransformationNode(
        id="sql:models/staging/stg_orders.sql",
        transformation_type="dbt_model",
        source_file="models/staging/stg_orders.sql",
        line_range=(1, 24),
        source_datasets=["source.ecom.raw_orders"],
        target_datasets=["model.stg_orders"],
    )
    orders_xform = TransformationNode(
        id="sql:models/marts/orders.sql",
        transformation_type="dbt_model",
        source_file="models/marts/orders.sql",
        line_range=(1, 40),
        source_datasets=["model.stg_orders"],
        target_datasets=["model.orders"],
    )
    lineage_graph.add_transformation_node(stg_xform)
    lineage_graph.add_transformation_node(orders_xform)
    lineage_graph.add_consumes_edge("sql:models/staging/stg_orders.sql", "source.ecom.raw_orders")
    lineage_graph.add_produces_edge("sql:models/staging/stg_orders.sql", "model.stg_orders")
    lineage_graph.add_consumes_edge("sql:models/marts/orders.sql", "model.stg_orders")
    lineage_graph.add_produces_edge("sql:models/marts/orders.sql", "model.orders")
    lineage_graph.save_lineage(root / "data_lineage" / "lineage_graph.json")

    _write_json(
        root / "module_graph" / "surveyor_stats.json",
        {
            "project_type": "dbt",
            "files_scanned": 2,
            "files_parsed_ok": 2,
            "dbt_ref_edges": 1,
            "circular_dependency_clusters": 0,
            "top_hubs": [["models/staging/stg_orders.sql", 0.51], ["models/marts/orders.sql", 0.49]],
            "high_velocity_files": [],
        },
    )
    _write_json(
        root / "data_lineage" / "hydrologist_stats.json",
        {
            "datasets_total": 3,
            "transformations_total": 2,
            "sources_registered": 1,
            "produces_edges": 2,
            "consumes_edges": 2,
        },
    )
    _write_json(
        root / "semantics" / "semantic_enrichment.json",
        {
            "purpose_statements": [
                {
                    "file_path": "models/staging/stg_orders.sql",
                    "purpose_statement": stg_orders.purpose_statement,
                    "business_logic_score": stg_orders.business_logic_score,
                    "confidence": 0.9,
                    "evidence": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "line_start": 10,
                            "line_end": 20,
                            "source_phase": "phase3",
                            "extraction_method": "llm_inference",
                            "description": stg_orders.purpose_statement,
                        }
                    ],
                },
                {
                    "file_path": "models/marts/orders.sql",
                    "purpose_statement": orders.purpose_statement,
                    "business_logic_score": orders.business_logic_score,
                    "confidence": 0.95,
                    "evidence": [
                        {
                            "file_path": "models/marts/orders.sql",
                            "line_start": 12,
                            "line_end": 32,
                            "source_phase": "phase3",
                            "extraction_method": "llm_inference",
                            "description": orders.purpose_statement,
                        }
                    ],
                },
            ]
        },
    )
    _write_json(
        root / "semantics" / "semantic_index.json",
        {
            "modules": {
                "models/staging/stg_orders.sql": {
                    "purpose": stg_orders.purpose_statement,
                    "business_logic_score": stg_orders.business_logic_score,
                    "key_concepts": ["orders", "staging", "raw_orders"],
                    "confidence": 0.9,
                },
                "models/marts/orders.sql": {
                    "purpose": orders.purpose_statement,
                    "business_logic_score": orders.business_logic_score,
                    "key_concepts": ["orders", "mart", "reporting"],
                    "confidence": 0.95,
                },
            },
            "business_logic_hotspots": [
                {
                    "file": "models/marts/orders.sql",
                    "score": 0.85,
                    "purpose": orders.purpose_statement,
                }
            ],
        },
    )
    _write_json(
        root / "semantics" / "day_one_answers.json",
        {
            "prompt_version": "phase3-day-one-v2",
            "questions": [
                {
                    "question": "What does this codebase do at a high level?",
                    "answer": "It is a dbt analytics project that stages raw orders and builds a reporting mart.",
                    "confidence": 0.92,
                    "cited_files": ["models/staging/stg_orders.sql", "models/marts/orders.sql"],
                    "citations": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "line_start": 10,
                            "line_end": 20,
                            "evidence_type": "semantic",
                            "source_phase": "phase3",
                            "extraction_method": "llm_inference",
                            "description": stg_orders.purpose_statement,
                        },
                        {
                            "file_path": "models/marts/orders.sql",
                            "line_start": 12,
                            "line_end": 32,
                            "evidence_type": "semantic",
                            "source_phase": "phase3",
                            "extraction_method": "llm_inference",
                            "description": orders.purpose_statement,
                        },
                    ],
                },
                {
                    "question": "What are the main data flows and where does data come from?",
                    "answer": "Raw orders from source.ecom.raw_orders flow into model.stg_orders and then into model.orders.",
                    "confidence": 0.9,
                    "cited_files": ["models/staging/stg_orders.sql", "models/marts/orders.sql"],
                    "citations": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "line_start": 1,
                            "line_end": 24,
                            "evidence_type": "lineage",
                            "source_phase": "phase2",
                            "extraction_method": "phase2_lineage",
                            "description": "dbt_model reads source.ecom.raw_orders and writes model.stg_orders",
                        },
                        {
                            "file_path": "models/marts/orders.sql",
                            "line_start": 1,
                            "line_end": 40,
                            "evidence_type": "lineage",
                            "source_phase": "phase2",
                            "extraction_method": "phase2_lineage",
                            "description": "dbt_model reads model.stg_orders and writes model.orders",
                        },
                    ],
                },
            ],
        },
    )
    _write_json(root / "semantics" / "reading_order.json", {"reading_order": [{"file_path": "models/staging/stg_orders.sql"}]})
    _write_json(root / "semantics" / "semantic_review_queue.json", {"semantic_review_queue": []})
    _write_json(root / "blind_spots.json", {"summary": {"total_blind_spots": 0}})
    _write_json(root / "high_risk_areas.json", {"summary": {"circular_dependency_clusters": 0}})
    _write_json(
        root / "semantic_hotspots.json",
        {
            "semantic_hotspots": [
                {
                    "file_path": "models/marts/orders.sql",
                    "hotspot_fusion_score": 0.88,
                    "purpose": orders.purpose_statement,
                    "supporting_evidence": [
                        {
                            "file_path": "models/marts/orders.sql",
                            "line_start": 12,
                            "line_end": 32,
                            "source_phase": "phase3",
                            "extraction_method": "llm_inference",
                            "description": orders.purpose_statement,
                        }
                    ],
                },
                {
                    "file_path": "models/staging/stg_orders.sql",
                    "hotspot_fusion_score": 0.75,
                    "purpose": stg_orders.purpose_statement,
                    "supporting_evidence": [
                        {
                            "file_path": "models/staging/stg_orders.sql",
                            "line_start": 10,
                            "line_end": 20,
                            "source_phase": "phase3",
                            "extraction_method": "llm_inference",
                            "description": stg_orders.purpose_statement,
                        }
                    ],
                },
            ]
        },
    )


class Phase4NavigationTests(unittest.TestCase):
    def _make_artifact_root(self) -> Path:
        root = TEST_TMP_ROOT / f"phase4_{uuid.uuid4().hex}"
        _build_phase4_artifacts(root)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_archivist_generates_living_context_from_saved_artifacts(self) -> None:
        artifact_root = self._make_artifact_root()

        result = Archivist(artifact_root).run()

        self.assertTrue(result.codebase_path.exists())
        self.assertTrue(result.onboarding_brief_path.exists())
        codebase_text = result.codebase_path.read_text(encoding="utf-8")
        self.assertIn("## Architecture Overview", codebase_text)
        self.assertIn("## Critical Path", codebase_text)
        self.assertIn("models/marts/orders.sql", codebase_text)

    @patch("src.agents.navigator.OllamaClient.is_available", return_value=False)
    def test_navigator_answers_repository_overview_with_grounded_citations(self, _mock_available) -> None:
        artifact_root = self._make_artifact_root()

        result = Navigator(artifact_root).answer_question("What does this repository do?")

        self.assertEqual(result.query_type, "repository_overview")
        self.assertIn("dbt analytics project", result.response.answer)
        self.assertGreater(result.response.confidence, 0.5)
        self.assertTrue(result.log_path and result.log_path.exists())
        self.assertTrue(result.response.citations)
        self.assertEqual(result.response.citations[0].file_path, "models/staging/stg_orders.sql")
        self.assertEqual(result.response.citations[0].line_start, 10)

    @patch("src.agents.navigator.OllamaClient.is_available", return_value=False)
    def test_navigator_answers_blast_radius_for_dataset_from_artifacts_only(self, _mock_available) -> None:
        artifact_root = self._make_artifact_root()

        result = Navigator(artifact_root).answer_question("What breaks if source.ecom.raw_orders changes?")

        self.assertEqual(result.query_type, "blast_radius")
        self.assertIn("model.stg_orders", result.response.answer)
        self.assertIn("model.orders", result.response.answer)
        self.assertTrue(any(citation.file_path == "models/staging/stg_orders.sql" for citation in result.response.citations))
        self.assertTrue(all(citation.file_path for citation in result.response.citations))

    @patch("src.agents.navigator.OllamaClient.is_available", return_value=False)
    def test_navigator_supports_find_implementation_and_explain_module(self, _mock_available) -> None:
        artifact_root = self._make_artifact_root()
        navigator = Navigator(artifact_root)

        implementation = navigator.answer_question("Where is order reporting logic?", log_query=False)
        module_explanation = navigator.answer_question("Explain models/marts/orders.sql", log_query=False)

        self.assertEqual(implementation.query_type, "find_implementation")
        self.assertTrue(implementation.response.citations)
        self.assertEqual(implementation.response.citations[0].file_path, "models/marts/orders.sql")

        self.assertEqual(module_explanation.query_type, "explain_module")
        self.assertIn("Builds the orders mart", module_explanation.response.answer)
        self.assertTrue(module_explanation.response.citations)


if __name__ == "__main__":
    unittest.main()
