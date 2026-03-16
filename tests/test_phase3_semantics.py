import unittest
from pathlib import Path

from src.agents.semanticist import Semanticist, SemanticsResult
from src.analyzers.semantic_extractor import PurposeResult
from src.graph.knowledge_graph import KnowledgeGraph
from src.models.nodes import (
    DatasetNode,
    Language,
    ModuleNode,
    SemanticEvidence,
    StorageType,
    TransformationNode,
)


def make_module(path: str, language: Language = Language.SQL, velocity: int = 0) -> ModuleNode:
    return ModuleNode(
        path=path,
        abs_path=str(Path(path)),
        language=language,
        lines_of_code=20,
        change_velocity_30d=velocity,
        role="mart" if language == Language.SQL else "utility",
    )


class Phase3SemanticsTests(unittest.TestCase):
    def test_module_node_coerces_legacy_semantic_evidence_string(self) -> None:
        module = ModuleNode.model_validate({
            "path": "models/example.sql",
            "abs_path": "models/example.sql",
            "language": "sql",
            "lines_of_code": 10,
            "semantic_evidence": "legacy evidence text",
        })

        self.assertEqual(len(module.semantic_evidence), 1)
        self.assertEqual(module.semantic_evidence[0].description, "legacy evidence text")
        self.assertEqual(module.semantic_evidence[0].extraction_method, "legacy_string")

    def test_hotspot_rankings_combine_cross_phase_signals(self) -> None:
        graph = KnowledgeGraph()
        module_a = make_module("models/a.sql", velocity=9)
        module_b = make_module("models/b.sql", velocity=1)
        graph.add_module(module_a)
        graph.add_module(module_b)
        graph.add_import_edge("models/b.sql", "models/a.sql")

        graph.add_dataset_node(DatasetNode(name="model.a", storage_type=StorageType.TABLE, dataset_type="dbt_model"))
        graph.add_transformation_node(TransformationNode(
            id="sql:models/a.sql",
            transformation_type="dbt_model",
            source_file="models/a.sql",
            target_datasets=["model.a"],
            line_range=(1, 20),
        ))
        graph.add_transformation_node(TransformationNode(
            id="sql:models/consumer.sql",
            transformation_type="dbt_model",
            source_file="models/consumer.sql",
            source_datasets=["model.a"],
            line_range=(1, 15),
        ))
        graph.add_produces_edge("sql:models/a.sql", "model.a")
        graph.add_consumes_edge("sql:models/consumer.sql", "model.a")

        semantics = SemanticsResult(purpose_results=[
            PurposeResult(
                file_path="models/a.sql",
                purpose_statement="Core business logic",
                business_logic_score=0.9,
                confidence=0.9,
                evidence=[SemanticEvidence(
                    source_phase="phase3",
                    file_path="models/a.sql",
                    line_start=3,
                    line_end=8,
                    extraction_method="heuristic",
                    description="Important SQL logic",
                )],
            ),
            PurposeResult(
                file_path="models/b.sql",
                purpose_statement="Support logic",
                business_logic_score=0.1,
                confidence=0.9,
                evidence=[SemanticEvidence(
                    source_phase="phase3",
                    file_path="models/b.sql",
                    line_start=1,
                    line_end=2,
                    extraction_method="heuristic",
                    description="Support SQL logic",
                )],
            ),
        ])

        rankings = Semanticist()._compute_hotspot_rankings(graph, semantics)

        self.assertEqual(rankings[0]["file_path"], "models/a.sql")
        self.assertGreater(rankings[0]["hotspot_fusion_score"], rankings[1]["hotspot_fusion_score"])

    def test_day_one_normalization_adds_line_range_citations(self) -> None:
        graph = KnowledgeGraph()
        module = make_module("models/orders.sql")
        graph.add_module(module)
        semantics = SemanticsResult(purpose_results=[
            PurposeResult(
                file_path="models/orders.sql",
                purpose_statement="Orders mart",
                business_logic_score=0.8,
                confidence=0.9,
                evidence=[SemanticEvidence(
                    source_phase="phase3",
                    file_path="models/orders.sql",
                    line_start=10,
                    line_end=18,
                    extraction_method="phase2_lineage",
                    description="Order aggregation logic",
                )],
            )
        ])

        normalized = Semanticist()._normalize_day_one_answers({
            "questions": [{
                "question": "Critical modules?",
                "answer": "Start with orders.",
                "cited_files": ["models/orders.sql"],
                "confidence": 0.9,
            }]
        }, graph, semantics)

        citation = normalized["questions"][0]["citations"][0]
        self.assertEqual(citation["file_path"], "models/orders.sql")
        self.assertEqual(citation["line_start"], 10)
        self.assertEqual(citation["line_end"], 18)
        self.assertEqual(normalized["questions"][0]["cited_files"], ["models/orders.sql"])

    def test_day_one_normalization_backfills_missing_line_ranges(self) -> None:
        graph = KnowledgeGraph()
        module = make_module("models/orders.sql")
        graph.add_module(module)
        semantics = SemanticsResult(purpose_results=[
            PurposeResult(
                file_path="models/orders.sql",
                purpose_statement="Orders mart",
                business_logic_score=0.8,
                confidence=0.9,
                evidence=[SemanticEvidence(
                    source_phase="phase3",
                    file_path="models/orders.sql",
                    line_start=7,
                    line_end=12,
                    extraction_method="phase2_lineage",
                    description="Order logic",
                )],
            )
        ])

        normalized = Semanticist()._normalize_day_one_answers({
            "questions": [{
                "question": "Critical modules?",
                "answer": "Start with orders.",
                "cited_files": ["models/orders.sql"],
                "citations": [{
                    "file_path": "models/orders.sql",
                    "line_start": None,
                    "line_end": None,
                    "evidence_type": "semantic",
                    "source_phase": "phase3",
                    "description": "LLM cited orders",
                }],
                "confidence": 0.9,
            }]
        }, graph, semantics)

        citation = normalized["questions"][0]["citations"][0]
        self.assertEqual(citation["line_start"], 7)
        self.assertEqual(citation["line_end"], 12)

    def test_review_queue_flags_low_confidence_and_weak_hotspots(self) -> None:
        graph = KnowledgeGraph()
        module = make_module("models/risky.sql", velocity=5)
        graph.add_module(module)
        semantics = SemanticsResult(
            purpose_results=[
                PurposeResult(
                    file_path="models/risky.sql",
                    purpose_statement="Risky module",
                    business_logic_score=0.9,
                    confidence=0.4,
                    evidence=[SemanticEvidence(
                        source_phase="phase3",
                        file_path="models/risky.sql",
                        line_start=None,
                        line_end=None,
                        extraction_method="llm_inference",
                        description="Broad summary only",
                    )],
                )
            ],
            hotspot_rankings=[
                {
                    "file_path": "models/risky.sql",
                    "hotspot_fusion_score": 0.8,
                }
            ],
        )

        review_queue = Semanticist()._build_review_queue(graph, semantics)

        self.assertEqual(len(review_queue), 1)
        self.assertIn("low-confidence semantic output", review_queue[0]["reasons"])
        self.assertIn("high hotspot score but weak evidence", review_queue[0]["reasons"])

    def test_fde_day_one_answers_cover_five_questions_with_grounded_citations(self) -> None:
        graph = KnowledgeGraph()
        source_module = make_module("models/staging/stg_orders.sql", velocity=4)
        sink_module = make_module("models/marts/orders.sql", velocity=2)
        sink_module.role = "mart"
        graph.add_module(source_module)
        graph.add_module(sink_module)

        raw_orders = DatasetNode(
            name="source.ecom.raw_orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_source",
            source_file="models/staging/__sources.yml",
            is_source_dataset=True,
        )
        stg_orders = DatasetNode(
            name="model.stg_orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_model",
            source_file="models/staging/stg_orders.sql",
        )
        orders = DatasetNode(
            name="model.orders",
            storage_type=StorageType.TABLE,
            dataset_type="dbt_model",
            source_file="models/marts/orders.sql",
            is_sink_dataset=True,
        )
        graph.add_dataset_node(raw_orders)
        graph.add_dataset_node(stg_orders)
        graph.add_dataset_node(orders)

        stg_xform = TransformationNode(
            id="sql:models/staging/stg_orders.sql",
            transformation_type="dbt_model",
            source_file="models/staging/stg_orders.sql",
            source_datasets=["source.ecom.raw_orders"],
            target_datasets=["model.stg_orders"],
            line_range=(1, 20),
        )
        mart_xform = TransformationNode(
            id="sql:models/marts/orders.sql",
            transformation_type="dbt_model",
            source_file="models/marts/orders.sql",
            source_datasets=["model.stg_orders"],
            target_datasets=["model.orders"],
            line_range=(1, 24),
        )
        graph.add_transformation_node(stg_xform)
        graph.add_transformation_node(mart_xform)
        graph.add_consumes_edge(stg_xform.id, raw_orders.name)
        graph.add_produces_edge(stg_xform.id, stg_orders.name)
        graph.add_consumes_edge(mart_xform.id, stg_orders.name)
        graph.add_produces_edge(mart_xform.id, orders.name)

        semantics = SemanticsResult(
            purpose_results=[
                PurposeResult(
                    file_path="models/staging/stg_orders.sql",
                    purpose_statement="Standardizes raw orders.",
                    business_logic_score=0.7,
                    confidence=0.9,
                    evidence=[
                        SemanticEvidence(
                            source_phase="phase3",
                            file_path="models/staging/stg_orders.sql",
                            line_start=4,
                            line_end=8,
                            extraction_method="phase2_lineage",
                            description="Standardizes raw orders.",
                        )
                    ],
                ),
                PurposeResult(
                    file_path="models/marts/orders.sql",
                    purpose_statement="Builds the final orders mart.",
                    business_logic_score=0.9,
                    confidence=0.92,
                    evidence=[
                        SemanticEvidence(
                            source_phase="phase3",
                            file_path="models/marts/orders.sql",
                            line_start=5,
                            line_end=12,
                            extraction_method="phase2_lineage",
                            description="Builds the final orders mart.",
                        )
                    ],
                ),
            ],
            hotspot_rankings=[
                {"file_path": "models/marts/orders.sql", "hotspot_fusion_score": 0.88},
                {"file_path": "models/staging/stg_orders.sql", "hotspot_fusion_score": 0.74},
            ],
        )

        payload = Semanticist()._build_fde_day_one_answers(graph, semantics)

        self.assertEqual(payload["prompt_version"], "fde-day-one-v1")
        self.assertEqual(len(payload["questions"]), 5)
        self.assertEqual(
            payload["questions"][0]["question"],
            "What is the primary data ingestion path?",
        )
        self.assertTrue(payload["questions"][0]["citations"])
        self.assertIn("model.orders", payload["questions"][2]["answer"])


if __name__ == "__main__":
    unittest.main()
