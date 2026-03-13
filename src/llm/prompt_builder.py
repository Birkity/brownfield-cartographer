"""
Prompt builder — versioned, inspectable prompt templates for the Semanticist.

Every prompt template is a named constant so it can be logged, diffed, and
audited independently of the LLM call itself.  Templates use Python str.format()
with named placeholders.
"""

from __future__ import annotations

PURPOSE_EXTRACTION_PROMPT_VERSION = "phase3-purpose-v2"
DOC_DRIFT_PROMPT_VERSION = "phase3-doc-drift-v2"
DAY_ONE_SYNTHESIS_PROMPT_VERSION = "phase3-day-one-v2"
NAVIGATOR_REASONING_PROMPT_VERSION = "phase4-reasoning-v1"
NAVIGATOR_SYNTHESIS_PROMPT_VERSION = "phase4-synthesis-v1"

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_CODE_ANALYST = (
    "You are a senior software engineer performing codebase analysis. "
    "You produce concise, evidence-grounded JSON responses. "
    "Never hallucinate — if evidence is insufficient, say so. "
    "Always cite specific code constructs (function names, table names, patterns) as evidence."
)

SYSTEM_SYNTHESIS = (
    "You are a senior data engineering consultant helping a new team member "
    "understand a brownfield codebase. Your summaries are concise, evidence-based, "
    "and cite specific files and code. Never speculate beyond what the evidence shows."
)

SYSTEM_NAVIGATOR_REASONING = (
    "You are a repository reasoning assistant. Analyze the grounded graph and semantic "
    "evidence provided to you, but do not invent facts or citations. Keep reasoning concise."
)

SYSTEM_NAVIGATOR_SYNTHESIS = (
    "You answer repository questions using only grounded evidence that has already been "
    "retrieved from analysis artifacts. Never invent files, line numbers, datasets, or "
    "dependencies. If evidence is incomplete, say so and lower confidence."
)

# ---------------------------------------------------------------------------
# Purpose extraction
# ---------------------------------------------------------------------------

PURPOSE_EXTRACTION_PROMPT = """\
Analyze the following source file and produce a JSON object describing its purpose.

FILE PATH: {file_path}
LANGUAGE: {language}
ROLE (heuristic): {role}
LINES OF CODE: {lines_of_code}
FUNCTIONS: {functions}
CLASSES: {classes}
IMPORTS/REFS: {imports_summary}
GRAPH CONTEXT: {graph_context}
PROMPT VERSION: {prompt_version}

--- SOURCE CODE WITH ORIGINAL LINE NUMBERS ---
{code_snippet}
--- END CODE ---

Respond with ONLY a JSON object (no markdown fences, no explanation):
{{
  "purpose_statement": "<2-3 sentence description of BUSINESS purpose, not implementation details>",
  "business_logic_score": <0.0-1.0 float: how much concentrated business logic is here>,
  "key_concepts": ["<concept1>", "<concept2>", ...],
  "evidence": [
    {{
      "source_phase": "phase1|phase2|phase3",
      "file_path": "{file_path}",
      "line_start": <integer or null>,
      "line_end": <integer or null>,
      "extraction_method": "<llm_inference|phase1_symbol|phase2_lineage|heuristic>",
      "description": "<specific code construct or dataset evidence>"
    }}
  ],
  "confidence": <0.0-1.0 float>
}}
"""

# ---------------------------------------------------------------------------
# Domain clustering
# ---------------------------------------------------------------------------

DOMAIN_CLUSTERING_PROMPT = """\
Given the following list of modules with their purpose statements and metadata,
group them into logical business domains.

MODULES:
{modules_json}

Respond with ONLY a JSON object:
{{
  "domains": [
    {{
      "domain_name": "<descriptive name like 'Order Processing' or 'Data Ingestion'>",
      "description": "<1-2 sentence description>",
      "members": ["<file_path_1>", "<file_path_2>", ...],
      "reasoning": "<why these belong together>"
    }}
  ],
  "confidence": <0.0-1.0 float>
}}
"""

# ---------------------------------------------------------------------------
# Documentation drift detection
# ---------------------------------------------------------------------------

DOC_DRIFT_PROMPT = """\
Compare this module's documentation (docstrings, comments) against its actual implementation.
Determine whether the documentation accurately describes what the code does.

FILE PATH: {file_path}
PURPOSE STATEMENT (from code analysis): {purpose_statement}
PROMPT VERSION: {prompt_version}

--- DOCUMENTATION FOUND ---
{documentation}
--- END DOCUMENTATION ---

--- CODE EXCERPT ---
{code_snippet}
--- END CODE ---

Respond with ONLY a JSON object:
{{
  "drift_level": "<one of: no_drift, possible_drift, likely_drift>",
  "explanation": "<concise explanation of any mismatch>",
  "stale_references": ["<any outdated terms, function names, or concepts in docs>"],
  "evidence": [
    {{
      "source_phase": "phase3",
      "file_path": "{file_path}",
      "line_start": <integer or null>,
      "line_end": <integer or null>,
      "extraction_method": "llm_inference",
      "description": "<specific doc or code mismatch>"
    }}
  ],
  "confidence": <0.0-1.0 float>
}}
"""

# ---------------------------------------------------------------------------
# Day-One onboarding synthesis
# ---------------------------------------------------------------------------

DAY_ONE_SYNTHESIS_PROMPT = """\
You are helping a new Field Data Engineer (FDE) who just joined and needs to understand
this codebase on Day One. Using the evidence below, answer these onboarding questions.

EVIDENCE SUMMARY:
- Project type: {project_type}
- Total modules: {total_modules}
- Total datasets: {total_datasets}
- Total transformations: {total_transformations}
- Architectural hubs: {hubs}
- Circular dependencies: {cycles}
- Dead code candidates: {dead_code}
- Domain clusters: {domains}

MODULE PURPOSES (top modules by PageRank):
{top_module_purposes}

LINEAGE SUMMARY:
{lineage_summary}

BLIND SPOTS:
{blind_spots_summary}

HIGH RISK AREAS:
{high_risk_summary}

Answer each question with specific evidence citations including file path, line range when known, and evidence type.
Respond with ONLY a JSON object:
{{
  "questions": [
    {{
      "question": "What does this codebase do at a high level?",
      "answer": "<answer with evidence>",
      "cited_files": ["<file1>", "<file2>"],
      "citations": [
        {{
          "file_path": "<file1>",
          "line_start": <integer or null>,
          "line_end": <integer or null>,
          "evidence_type": "<semantic|lineage|drift|hotspot>",
          "source_phase": "phase1|phase2|phase3",
          "description": "<what this citation supports>"
        }}
      ],
      "confidence": <0.0-1.0>
    }},
    {{
      "question": "What are the main data flows and where does data come from?",
      "answer": "<answer with evidence>",
      "cited_files": ["<file1>", "<file2>"],
      "citations": [],
      "confidence": <0.0-1.0>
    }},
    {{
      "question": "What are the critical modules that a new engineer should understand first?",
      "answer": "<answer with evidence>",
      "cited_files": ["<file1>", "<file2>"],
      "citations": [],
      "confidence": <0.0-1.0>
    }},
    {{
      "question": "Where are the highest-risk areas and technical debt?",
      "answer": "<answer with evidence>",
      "cited_files": ["<file1>", "<file2>"],
      "citations": [],
      "confidence": <0.0-1.0>
    }},
    {{
      "question": "What are the blind spots — areas where the analysis may be incomplete?",
      "answer": "<answer with evidence>",
      "cited_files": ["<file1>", "<file2>"],
      "citations": [],
      "confidence": <0.0-1.0>
    }}
  ]
}}
"""

# ---------------------------------------------------------------------------
# Batch purpose extraction (multiple small files in one call)
# ---------------------------------------------------------------------------

BATCH_PURPOSE_EXTRACTION_PROMPT = """\
Analyze each source file below and return a JSON array with one purpose object per file.
Keep each purpose_statement focused on BUSINESS purpose (1-2 sentences), not implementation.

FILES TO ANALYZE:
{files_json}

Respond with ONLY a JSON array — one object per file in the SAME ORDER as input:
[
  {{
    "file_path": "<must exactly match the input file_path>",
    "purpose_statement": "<1-2 sentence business purpose>",
    "business_logic_score": <0.0-1.0 float>,
    "key_concepts": ["<concept1>", "<concept2>"],
    "evidence": [
      {{
        "source_phase": "phase1|phase2|phase3",
        "file_path": "<must exactly match the input file_path>",
        "line_start": <integer or null>,
        "line_end": <integer or null>,
        "extraction_method": "<llm_inference|phase1_symbol|phase2_lineage|heuristic>",
        "description": "<specific code constructs supporting the assessment>"
      }}
    ],
    "confidence": <0.0-1.0 float>
  }}
]
"""

# ---------------------------------------------------------------------------
# Phase 4 Navigator prompts
# ---------------------------------------------------------------------------

NAVIGATOR_REASONING_PROMPT = """\
Analyze the repository question using only the grounded evidence already retrieved from the
knowledge graph and semantic artifacts.

QUESTION: {question}
QUERY TYPE: {query_type}
PROMPT VERSION: {prompt_version}

RETRIEVED SUMMARY:
{retrieved_summary}

STRUCTURED FACTS:
{facts_json}

AVAILABLE EVIDENCE:
{citations_json}

Respond with ONLY a JSON object:
{{
  "analysis_summary": "<brief reasoning summary grounded in the supplied evidence>",
  "salient_points": ["<point1>", "<point2>"],
  "confidence": <0.0-1.0 float>
}}
"""

NAVIGATOR_SYNTHESIS_PROMPT = """\
Answer the repository question using only the grounded evidence supplied below.
Do not invent or alter citations. If the evidence is incomplete, say so clearly.

QUESTION: {question}
QUERY TYPE: {query_type}
PROMPT VERSION: {prompt_version}

RETRIEVED SUMMARY:
{retrieved_summary}

STRUCTURED FACTS:
{facts_json}

AVAILABLE CITATIONS:
{citations_json}

Respond with ONLY a JSON object:
{{
  "question": "{question}",
  "answer": "<grounded explanation that directly answers the question>",
  "confidence": <0.0-1.0 float>
}}
"""
