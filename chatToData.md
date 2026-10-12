Talk2Data: User Query & Response End-to-End Architecture Flow
This document provides a comprehensive architectural and operational breakdown of how a user question is ingested, interpreted, governed, compiled, executed, verified, and presented within the Generic Metadata-Driven AI Analytics Platform (Talk2Data).
1. High-Level Summary (Simple Flow)
The platform processes every analytical request through a fail-closed, 4-stage pipeline designed to eliminate hallucinations, enforce privacy and tenant isolation, and ensure strict domain-agnostic execution:
flowchart LR
    A["👤 User Input\n(Natural Language)"] --> B["🧠 1. Intent & Governance\n• Domain-Agnostic LLM\n• Semantic Vocabulary Lock\n• Clarification Gate"]
    B --> C["⚙️ 2. Governed Execution\n• SQL Compilation (AST)\n• Physical Execution\n• Deterministic Python Kernel"]
    C --> D["🛡️ 3. Privacy & Sealing\n• k-Anonymity & Suppression\n• Sealed Fact Contract\n• Raw Row Erasure"]
    D --> E["📊 4. Grounded Delivery\n• Verified Narrative\n• Presentation Permit\n• Audit Digest"]
Simple Flow Walkthrough
User Submits Question: The user types a question in the conversational interface. The request is authenticated, mapped to tenant access rights, and recorded using a privacy-preserving question digest.
Intent Interpretation & Clarification: An LLM translates the prose into a structured intent using only approved catalog metadata (measures, dimensions, filters). If any parameter or period is ambiguous, the system prompts the user with structured clarification choices instead of guessing.
Governed Compilation & Deterministic Execution: A physical query compiler generates dialect-specific SQL with parameterized filters. The database returns raw rows to an isolated, transient plane. All calculations (aggregations, growth rates, forecasts, simulations) are executed by deterministic Python code—never by an LLM.
Evidence Sealing & Presentation: The calculated facts are cryptographically sealed. Raw rows are erased from memory. A narrative is composed and strictly verified against the sealed facts. The user retrieves the answer using a short-lived Presentation Permit.
2. Complete End-to-End Sequence Diagram
The following sequence diagram captures the detailed interaction between the frontend, API gateway, security runtime, semantic control plane, intent assist model, query compiler, physical data sources, decision science kernel, privacy guardrails, and audit outbox:
sequenceDiagram
    autonumber
    actor User as 👤 User / Client MFE
    participant Shell as 🌐 API Gateway (FastAPI)
    participant Sec as 🔐 Access & Security Runtime
    participant Life as 🔄 Conversation Lifecycle
    participant Sem as 📚 Business Semantics Plane
    participant Model as 🤖 Azure OpenAI (Intent / Composer)
    participant Kernel as ⚙️ Governed Analysis & Decision Science
    participant DB as 🗄️ Physical Source (SQL Server / PG)
    participant Privacy as 🛡️ Governed Disclosure Service
    participant Evidence as 📦 Evidence & Answer Publisher
    participant Audit as 📝 Terminal Audit Store

    %% Turn Submission
    User->>Shell: POST /api/v1/conversations/{id}/turns<br/>{question, source_id}
    Shell->>Sec: Authenticate session, CSRF & permissions
    Sec-->>Shell: ApiActor(tenant_id, subject_id)
    Shell->>Life: submit_question(actor, conv_id, question)

    %% Session & Semantic Pinning
    Life->>Life: Create Turn (Status: SUBMITTED)<br/>Compute SHA-256 Question Digest
    Life->>Audit: Record Fact: question_submitted (digest only)
    
    alt Fast-Path Check: Greeting / Non-Analytics
        Life->>Life: Transition Turn: DECLINED
        Life-->>Shell: Turn Accepted (DECLINED with greeting guidance)
        Shell-->>User: HTTP 202 Accepted (Turn status: DECLINED)
    else Analytical Query
        Life->>Sem: Pin active semantic & source release
        Sem-->>Life: SemanticSnapshotPin(release_id, epoch, source_id)
        Life->>Life: Transition Turn: INTERPRETING
        
        %% Interpretation Context & LLM
        Life->>Sem: Fetch interpretation_context(pin)
        Sem-->>Life: Allowed measures, dimensions, filters
        Life->>Model: Interpret prose with dynamic catalog prompt
        Model-->>Life: Interpretation(intent, ambiguity_code, assumptions)
        
        alt Ambiguous Query (Missing Period, Dimension, or Metric)
            Life->>Life: Transition Turn: AWAITING_CLARIFICATION<br/>Generate structured choices
            Life->>Audit: Record Fact: clarification_requested
            Life-->>Shell: Turn Accepted (AWAITING_CLARIFICATION)
            Shell-->>User: HTTP 202 Accepted (Clarification options)
            User->>Shell: POST /turns/{turn_id}/clarification<br/>{choice_id}
            Shell->>Life: apply_choice() & resume
        end

        %% Intent Validation Gate
        Life->>Kernel: validate_intent(intent, allowed_vocabulary)
        Kernel-->>Life: Validated AnalyticalIntent (Fail-Closed Gate)
        Life->>Audit: Record Fact: interpretation_selected

        %% Governed Analysis Execution
        Life->>Life: Transition Turn: ANALYZING
        Life->>Audit: Record Fact: analysis_started
        
        alt Decision Science Intent (Forecast / Anomaly / Scenario / Goal-Seek)
            Life->>Kernel: Run Decision Method (METHOD-STAT-001..004)
            Kernel->>DB: Fetch bounded baseline historical rows
            DB-->>Kernel: Transient Tabular Rows
            Kernel->>Kernel: Execute Deterministic Scientific Algorithm
            Kernel-->>Life: Decision Facts & Limitations
        else Standard Analytics (Total / Average / Trend / Comparison / Contribution)
            Life->>Sem: resolve_pinned(intent, pin)
            Sem-->>Life: ResolvedSemanticPlan (Formula, Join Edges, Grain)
            Life->>Kernel: analyze(intent, semantic_plan)
            Kernel->>Kernel: Compile AST -> PhysicalQueryPackage (Bound SQL)
            Kernel->>DB: Execute parameterized SQL with timeout
            DB-->>Kernel: Raw Tabular Rows
            Kernel->>Kernel: Deterministic Python Aggregation & Math
            Kernel-->>Life: AnalysisResult (Facts, Lineage, Erasure Receipt)
        end

        %% Privacy & Disclosure Control
        Life->>Privacy: inspect_and_protect(result, tenant_id)
        Privacy->>Privacy: Apply k-anonymity & small-group suppression
        Privacy-->>Life: Protected Result (or DENY if policy breached)

        %% Evidence Sealing & Ephemeral Memory Erasure
        Life->>Evidence: seal_evidence_input(result)
        Evidence-->>Life: Sealed Evidence Package
        Life->>Kernel: close_without_values() -> Terminal Row Erasure

        %% Answer Composition & Context Matching
        Life->>Life: Transition Turn: COMPOSING
        par Parallel Enrichment
            Life->>Life: Fetch Context Events (business calendar matches)
        and
            Life->>Life: Generate Follow-Up Graph Suggestions
        end
        
        Life->>Evidence: publish(sealed_evidence, context_events, suggestions)
        Evidence->>Model: Compose grounded narrative strictly on sealed facts
        Model-->>Evidence: Narrative Draft
        Evidence->>Evidence: Verify numeric claims match sealed facts exactly
        Evidence-->>Life: Published Answer Manifest

        %% Turn Completion & Audit Closure
        Life->>Audit: Record Fact: answer_published (manifest digest)
        Life->>Audit: Record Terminal Closure (terminal_status: completed)
        Life->>Life: Transition Turn: ANSWERED (Publish Turn)
        Life-->>Shell: Turn Accepted (ANSWERED)
        Shell-->>User: HTTP 202 Accepted (Turn ready)

        %% Answer Retrieval via Presentation Permit
        User->>Shell: POST /turns/{turn_id}/answer/presentation-permits
        Shell->>Life: issue_permit(actor, turn_id, visible_elements)
        Life-->>Shell: PresentationPermit(id, expires_at)
        Shell-->>User: Permit View {presentationPermitId}

        User->>Shell: GET /turns/{turn_id}/answer?presentationPermitId={id}
        Shell->>Evidence: get_answer(actor, turn_id, permit_id)
        Evidence-->>Shell: AnswerView (Summary, KPIs, Charts, Evidence, Citations)
        Shell-->>User: HTTP 200 OK (Rendered Analytics Dashboard Card)
    end
3. Turn Lifecycle State Machine
Every conversational interaction maintains a strictly tracked turn lifecycle state machine. Transitions are atomic, and failures trigger an immediate transition to DECLINED or FAILED accompanied by cryptographically verified ephemeral memory erasure:
stateDiagram-v2
    [*] --> SUBMITTED : User inputs question
    
    SUBMITTED --> DECLINED : Fast-path greeting or non-analytics
    SUBMITTED --> INTERPRETING : Valid analytics question & semantic pin acquired
    SUBMITTED --> FAILED : Configuration or control plane missing
    
    INTERPRETING --> AWAITING_CLARIFICATION : Ambiguous period, dimension, or metric
    AWAITING_CLARIFICATION --> INTERPRETING : User selects clarification choice
    INTERPRETING --> DECLINED : Unsupported intent or catalog rejection
    
    INTERPRETING --> ANALYZING : Intent validated against catalog vocabulary
    
    ANALYZING --> DECLINED : Privacy denial (k-anonymity) or source failure
    ANALYZING --> COMPOSING : Data execution complete & evidence sealed
    
    COMPOSING --> ANSWERED : Narrative composed, verified & published
    COMPOSING --> DECLINED : Claim verification failure (hallucination caught)
    
    ANSWERED --> [*] : Client fetches answer with Presentation Permit
    DECLINED --> [*] : Ephemeral plane erased; safe message displayed
    FAILED --> [*] : Ephemeral plane erased; error reported safely
4. In-Depth Step-by-Step Architectural Walkthrough
Phase 1: Ingestion, Authentication & Version Pinning
Endpoint: Client issues POST /api/v1/conversations/{conversationId}/turns.
Actor Authentication:DevelopmentAccessRuntime validates HTTP-only session cookies and CSRF headers.
The user is resolved to an ApiActor(tenant_id, subject_id, is_admin).
Session Submission & Audit Hashing:ConversationSessions.submit allocates a unique turn_id and records the turn in status SUBMITTED.
Privacy Guard: The raw question text is never copied into long-term audit logs. A SHA-256 question digest is calculated and logged via TerminalAuditRecorder.record_fact.
Fast-Path Greeting Detection:If the input is detected as a greeting or social phrase (is_non_analytics_input), the runtime immediately transitions the turn to DECLINED with helpful guidance, skipping database calls, catalog loading, and LLM execution.
Immutable Semantic Pinning:The runtime calls BusinessSemantics.pin(tenant_id, requested_source_id).
A SemanticSnapshotPin locks the exact release_id, semantic_epoch, and source_version.
Architecture Guarantee: If an administrator edits the catalog or updates mappings while a query is running, the running query is guaranteed reproducible, version-stable semantics.
Phase 2: Intent Interpretation & Governed Clarification Gate
Catalog Vocabulary Extraction:The runtime extracts the approved list of measures, dimensions, and filter rules for the pinned release.
Domain-Agnostic LLM Prompting:The user's prose is dispatched to Azure OpenAI via FoundryIntentAssist.
The prompt contains no hardcoded business assumptions and no database credentials or SQL schema. It receives only the approved metadata definitions.
The LLM returns a structured JSON payload:measure: Targeted metric (e.g., Order Quantity, Total Sales)
family: Calculation family (TOTAL, AVERAGE, TREND, COMPARISON, CONTRIBUTION, FORECAST, SCENARIO, GOAL_SEEK, ANOMALY)
dimensions: Slicing attributes (e.g., Product Category, Country)
current_period / comparison_period: Temporal bounds (e.g., 2024, 2023)
filters: Explicit predicate conditions
ambiguity_code: Flag indicating if disambiguation is required
Fail-Closed Clarification Loop:If ambiguity_code is present (e.g., comparison-period-required, metric-disambiguation, diagnostic-dimension-required, forecast-horizon-required, period-year-required):The turn transitions to TurnStatus.AWAITING_CLARIFICATION.
Structured, discrete options are generated from catalog assets.
The system halts execution and waits for user choice via POST /turns/{turnId}/clarification.
Platform Rule: The platform never guesses missing periods, baseline years, or dimensions.
Deterministic Intent Validation:validate_intent() executes pure Python assertions verifying:The metric exists in allowed_measures.
All dimensions exist in allowed_dimensions.
Filter operations adhere to supported operators.
If validation fails, the turn is declined cleanly.
Phase 3: Governed Semantic Resolution & SQL Compilation
Semantic Plan Compilation:SemanticResolver.resolve_pinned() translates the validated intent into a ResolvedSemanticPlan.
Determines underlying source tables, approved join paths (ApprovedJoinEdge), aggregations (SUM, AVG, COUNT), and formulas.
Physical Query Compilation:PhysicalQueryCompiler builds a PhysicalQueryPackage:Synthesizes dialect-safe SQL (T-SQL for SQL Server, PostgreSQL, etc.).
Generates parameterized clauses (@p1, @p2) to prevent SQL injection.
Applies catalog-defined row limits and query execution timeouts.
Enforces tenant schema isolation.
Phase 4: Execution & Deterministic Analytical Kernel
Secure Execution via GenericSourceGateway:The query package is dispatched to the physical database connection via CredentialBroker.
Results are streamed as transient tabular rows into ephemeral memory.
Zero-LLM Math Execution:Standard Analytics: Python kernel aggregates rows, computes variance, growth percentages, and totals using exact Decimal arithmetic.
Decision Science Suite:METHOD-STAT-001: Statistical Linear Forecast with trend extrapolation and confidence intervals.
METHOD-STAT-002: Median Absolute Deviation (MAD) Anomaly Detection.
METHOD-STAT-003: What-If Scenario Engine (adjusting drivers by % or absolute deltas).
METHOD-STAT-004: Bounded Goal Seek Optimization (solving for target values using iterative convergence).
Generates an AnalysisResult containing granular AnalysisFact items.
Phase 5: Privacy, Disclosure Controls & Memory Erasure
Governed Disclosure Inspection (FR-GOV-002):GovernedDisclosureService.inspect_and_protect() evaluates:k-Anonymity: Slices with record counts below the tenant threshold (e.g., < 5) are suppressed.
Small-Group Suppression: Prevents deductive identity re-identification.
Evidence Sealing:seal_evidence_input() converts analysis results into an immutable SealedEvidencePackage.
Contains only calculated labels, numerical values, currencies, units, formulas, and execution lineage.
Fail-Closed Transient Memory Erasure:close_without_values() is invoked immediately after sealing.
Transient database row buffers are flushed and garbage-collected.
A cryptographic erasure_receipt_digest is logged as audit evidence.
Phase 6: Answer Composition, Context Intelligence & Delivery
Context Events Matching:ContextIntelligenceService matches relevant active business calendar events (e.g., promotions, holidays, regulatory events) overlapping the analysis timeframe.
Follow-Up Suggestions:FollowUpRecommender analyzes the semantic relationship graph and previous questions to generate 3 intelligent next-turn questions.
Grounded Narrative Composition:AnswerComposer invokes Azure OpenAI with the sealed facts.
Strict Grounding Check: The generated narrative is scanned to verify that every single numeric claim exactly matches a sealed fact. If an ungrounded number is found, the system rejects the text and falls back to a deterministic template.
Publishing to Postgres Answer Store:The final answer record is written with summary, chart recommendation (kpi, bar, line, table), evidence facts, limitations, assumptions, and suggestions.
Turn status moves to ANSWERED.
Presentation Permit Security Pattern:To render the answer, the client calls POST /turns/{turnId}/answer/presentation-permits.
Server validates permissions and issues a short-lived presentationPermitId.
Client calls GET /turns/{turnId}/answer?presentationPermitId=... to load the answer payload.
Client can optionally call /answer/dataset or /export/xlsx using the same permit.
5. Architectural Invariants Summary
DimensionArchitectural RuleImplementation MechanismDomain AgnosticismZero hardcoded entity names, columns, or metricsDynamic schema discovery & metadata catalog definitionsCalculationsLLMs never perform calculations or generate loose SQLDeterministic Python kernel & parameterized AST compilerHallucination ControlNo ungrounded claims in user narrativesEvidence sealing & regex-backed numeric claim verificationAmbiguity HandlingFail-closed; never invent default dates or dimensionsTurnStatus.AWAITING_CLARIFICATION interactive choice setsData PrivacyRaw data rows never leave the ephemeral execution planeImmediate memory erasure + k-anonymity disclosure inspectionAudit IntegrityOperational logs must not leak PII or business numbersSHA-256 digests for questions, results, and erasure receiptsPresentation SecurityUI cannot access backend answers directlyShort-lived, scoped PresentationPermit tokens

