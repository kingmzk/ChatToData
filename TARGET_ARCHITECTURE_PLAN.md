# Talk2Data Target Architecture: What and What to Do

This document serves as the master contract, roadmap, and implementation tracker for upgrading **Talk2Data** to the **Governed, Stateful Hybrid LLM Architecture**.

---

## 1. Architectural Essence & Classification

### Core Identity
Talk2Data is a **governed conversational analytics platform** that:
1. Converts natural-language questions into structured analytical intent.
2. Resolves them deterministically through an approved semantic catalog.
3. Executes tenant-scoped, read-only queries with strict isolation.
4. Performs deterministic analytical calculations (comparisons, contributions, forecasts, anomalies, scenarios).
5. Applies rigorous disclosure protection (minimum group suppression, complementary suppression).
6. Seals analytical evidence with cryptographic digests (SHA-256).
7. Transactionally publishes authorized results under fencing-token supervision.

### Strictly NOT:
- A five-agent or autonomous multi-agent application.
- An autonomous SQL agent or agentic database.
- A vector-RAG platform or MCP-based runtime.
- A system where LLMs decide authorization, tenant scope, joins, math, or publication.

---

## 2. Core Separation of Responsibilities

```
┌────────────────────────────────────────────────────────┐
│                   DURABLE WORKER                       │
│  • Job leasing & heartbeats                            │
│  • Fencing token issuance & verification               │
│  • Cancellation checks & stale execution detection     │
│  • Lease release during clarification                  │
│  • Final publication gate & atomic database commit     │
└───────────────────────────┬────────────────────────────┘
                            │ invokes / resumes (thread_id)
                            ▼
┌────────────────────────────────────────────────────────┐
│                 LANGGRAPH STATEGRAPH                   │
│  • Analytical workflow orchestration                   │
│  • Discrete node execution & conditional routing       │
│  • Persistent checkpointing via stable thread_id       │
│  • Human-in-the-loop interruption via interrupt()      │
│  • Coarse-grained privacy-safe analytics boundary      │
│  • Emits PUBLICATION CANDIDATE (no direct DB commit)   │
└────────────────────────────────────────────────────────┘
```

---

## 3. Strict LLM Call Budget & Boundaries

| Operation | Implementation | LLM Role |
| :--- | :--- | :--- |
| **Call 1 (Required)** | `intent_interpretation_node` | **Gemini**: Inters natural language into structured intent schema (`measure`, `family`, `dimensions`, `filters`, `periods`). |
| **Clarification Choice** | `clarification_resume_node` | **Deterministic Python**: Lookup server-defined `choice_id` and apply pre-approved intent patch. **0 LLM calls.** |
| **SQL & Math & Privacy** | `governed_analytics_node` | **Deterministic Python**: AST query compilation, read-only gateway, analysis kernel, disclosure suppression, evidence sealing. **0 LLM calls.** |
| **Call 2 (Optional)** | `optional_executive_summary_node` | **Gemini**: Generates template with fact placeholders (e.g., `{{north_growth}} was higher than {{south_growth}}`). |
| **Claim Guardrail** | `deterministic_claim_guardrail_node` | **Deterministic Python**: Resolves placeholders against verified fact IDs, scans prose for unapproved numbers, rejects on mismatch. **0 LLM calls.** |

**Total Normal Budget**: 1 required call, maximum 1 optional call.

---

## 4. Privacy-Safe Graph State

### Allowed in Graph Checkpoints (`GraphState`):
- `conversation_id`, `turn_id`, `job_id`, `tenant_id`, `subject_id`, `source_id`, `question`
- `semantic_release_id`, `thread_id`, `fencing_token`
- `structured_intent` (validated/parsed intent)
- `validation_status` (`PENDING`, `VALID`, `CLARIFY`, `DECLINE`, `FAILED`)
- `clarification_request` (server-approved prompts & choices)
- `semantic_plan` (approved dimensions, measures, join edges)
- `protected_evidence_reference` & `evidence_digest`
- `safe_display_facts` (facts that passed disclosure controls)
- `deterministic_presentation` (KPI cards, table rows, chart JSON)
- `verified_summary` (guardrailed executive narrative)
- `graph_status` (`RUNNING`, `INTERRUPTED`, `COMPLETED`, `FAILED`, `DECLINED`)

### Strictly FORBIDDEN in Graph Checkpoints:
- Database credentials, API keys, authentication secrets.
- Raw query result rows.
- Unsuppressed or pre-suppression analytical facts.
- Hidden population/entity counts.
- Sensitive query execution diagnostics.

---

## 5. Phase-by-Phase Implementation Checklist

- [x] **Phase 1: Define Ownership & Module Structure**
  - Created `talk2data/graph/` package.
  - Defined state ownership contracts between SQLite Application DB, Durable Worker, and LangGraph Checkpointer.

- [x] **Phase 2: Define Privacy-Safe Graph State Schema**
  - Implemented `talk2data/graph/state.py` with strict TypedDict schemas.
  - Ensured raw database records and unsuppressed numbers cannot be serialized into state.

- [x] **Phase 3: Implement Workflow Nodes Wrapping Existing Services**
  - `access_context_node`: Authorize identity, verify tenant and turn ownership.
  - `catalog_projection_node`: Project user-accessible vocabulary and pin semantic release.
  - `intent_interpretation_node`: Invoke Gemini (or provided mock/parser) to obtain structured intent (LLM Call 1).
  - `structured_intent_parser_node`: Parse intent with bounded retry, isolate provider failures.
  - `context_resolution_node`: Conversational memory & relative date resolution.
  - `governance_validator_node`: Deterministic validation gate.
  - `failure_result_node` & `decline_result_node`: Controlled terminal exit nodes.

- [x] **Phase 4: Coarse-Grained Governed Analytics Node (`governed_analytics_node`)**
  - Bundled `GovernedQueryCompiler`, `GenericSourceGateway`, `DeterministicAnalysisKernel`, `GovernedDisclosureService`, and `SealedEvidencePackage` inside one single node.
  - Kept raw rows as local stack variables; release transient buffers before returning safe display facts.

- [x] **Phase 5: Clarification Interruption & Resumption (`interrupt()`)**
  - `clarification_preparation_node`: Deterministically build approved choices.
  - LangGraph `interrupt()` call with JSON-serializable payload.
  - `clarification_resume_node`: Accept `Command(resume=...)` with user's `choice_id`, patch intent, loop back to validation.

- [x] **Phase 6: Deterministic Presentation & Candidate Packaging**
  - `deterministic_presentation_node`: Generate KPI cards, tables, chart JSON, suppression markers.
  - `publication_candidate_node`: Format `PublicationCandidate` payload.

- [x] **Phase 7: Optional Executive Summary & Deterministic Claim Guardrail**
  - `optional_executive_summary_node`: Request placeholder template from Gemini (Optional LLM Call 2).
  - `deterministic_claim_guardrail_node`: Fact ID resolution, numeric regex prose scanning, safe fallback on any mismatch.

- [x] **Phase 8: Assemble StateGraph & Durable Checkpointer**
  - `talk2data/graph/workflow.py`: Compiled graph with conditional routing and checkpointer support.
  - Verified full path routing: `VALID`, `CLARIFY`, `DECLINE`, `PROVIDER_FAILURE`.

- [x] **Phase 9: Integrate with Durable Job Worker & Publication Gate**
  - Updated `talk2data/lifecycle/session.py` to route through LangGraph when `use_graph` is enabled.
  - Handled interruption by releasing lease (`store.release_job_for_clarification`) and exiting cleanly.
  - Resumed interrupted thread on subsequent worker claim with a new `fencing_token`.
  - Enforced worker publication gate (lease, token, cancellation, permissions, evidence digest) prior to atomic commit.

- [x] **Phase 10: Verification & Test Suite**
  - Unit tests in `tests/test_target_architecture_graph.py` covering state, nodes, routing, interruption, and resume.
  - End-to-end integration tests: **161 passed**, 0 failed across full test suite.
  - Specific verification of checkpointer state hygiene (confirming no raw rows or unsuppressed facts in checkpoints).
