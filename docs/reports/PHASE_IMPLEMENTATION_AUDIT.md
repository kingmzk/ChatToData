> Historical baseline: this audit predates the corrections. See IMPLEMENTATION_FIX_REPORT.md for the current status and verification.

# Talk2Data phase implementation audit

Date: 2026-10-09
Scope: static inspection of source and FINAL_ARCHITECTURAL_CONTRACT.md sections 30–31. No tests, live model calls, or browser execution were performed. Module existence is distinguished from integration and correctness. All four phases have substantive code, but none can currently be classified as complete.

| Phase | Present implementation | Assessment |
|---|---|---|
| 1 Reliable Core | Catalog, resolver, parameterized compiler, tenant predicates, intent validation, clarification, deterministic analysis, facts, fallback, audit | PARTIAL: interpretation, disclosure enforcement, semantic stability and numerical behavior have gaps |
| 2 Reliable Delivery | SQLite migrations, job queue, leases, retries, cancellation endpoint, answer versions, charts, exports, frontend, follow-up context | PARTIAL: restart, idempotency, cancellation/fencing, publication and retrieval integration gaps |
| 3 Decision Science | Forecast with holdout comparison, MAD anomaly detection, restricted declarative scenarios, bracketed goal seek; dispatch through shared pipeline | PARTIAL: temporal validation, missing baselines and solver claims need correction; inherits core disclosure gaps |
| 4 Advanced Controls | Dataset policy classes, repeated-query accounting class, disclosure ledger schema, audit hash chain/HMAC, calendar metadata helper | PARTIAL / disconnected: privacy accounting and policy checks are not invoked by the lifecycle; enrichment helper not integrated into answers |

## Foundational blockers

1. Natural-language interpretation is disconnected. lifecycle/session.py:181 parses proposed_llm_json or an empty object. GeminiIntentAssist exists but is not called by the application execution path. The browser supplies only a question and source. The demo manager also uses this lifecycle without wiring an interpreter.
2. Standalone startup is unconfigured. api/app.py:35–40 and jobs/daemon.py:26–30 instantiate empty source permissions/catalog registries and an uninitialized source gateway. The source/catalog/permission setup is only supplied manually in demo/test construction, with no shared configuration loader. Sharing the application database does not share these registries.
3. Minimum-group privacy is not enforced in the pipeline. lifecycle/session.py:490 supplies facts and tenant only, without distinct entity counts; security/disclosure.py:98 uses an empty count map. Most metrics therefore bypass minimum-group suppression, with special behavior based on salary/compensation names. DatasetDisclosurePolicy.validate_request is not invoked by the pipeline.
4. Answer retrieval checks ownership but not current source/metric/dimension permissions. api/routes.py:215 invokes storage/repository.py:473 directly instead of the stronger lifecycle retrieval method. Export checks use the latest conversation context, not immutable per-answer scope, omit dimensions, and swallow catalog lookup errors (export/export.py:35–49).

## Phase 1 gaps

- Clarification re-pins the current catalog release (lifecycle/session.py:283), allowing semantic meaning to change mid-turn.
- Sliced results sum all group metric values into a total (execution/kernel.py:84), which is invalid for grouped averages and overlapping distinct counts.
- Null/invalid numerical inputs are converted into zero (execution/kernel.py:349–358); float conversion rounds to eight decimals. Exact Decimal aggregation cannot repair precision lost in source storage or SQL arithmetic. Compiler allocation still uses CAST AS NUMERIC division in SQLite.
- Gateway does not enforce package timeout, database read-only credentials/connection mode, interruption or resource budgets. It supports an existing SQLite connection, despite broader generic-source descriptions.
- Core lifecycle audit records remain in memory; durable audit publication covers answer publication, not all terminal lifecycle events.

## Phase 2 gaps

- API question/clarification submission calls worker.process_next_job inside the request (api/routes.py:119,179), so durable acceptance is followed by synchronous execution before responding.
- Repeated submissions create and save a new turn before checking an existing idempotent job (api/routes.py:93–103). The response uses the newly allocated turn rather than the original job turn. The repository lookup omits operation and payload consistency.
- Clarification and inherited context depend on lifecycle in-memory dictionaries (session.py:68–70). The worker does not restore turns, pending intents, clarification binding or context from storage before resumption. A different worker or process restart cannot resume correctly.
- Worker saves the lifecycle ANSWERED state before transactional answer publication. This exposes a successful status before an answer exists and can overwrite cancellation or newer state. General save_turn has no job fencing or terminal-state protection.
- Fencing is limited to publication/completion. fail_job and release_job_for_clarification do not constrain writes by attempt ownership. Publication does not verify current lease expiry or recheck current source/metric permissions. Cancellation is checked before execution, without ongoing cancellation of provider/database work.
- ValidatedAnswerComposer exists but lifecycle rendering uses deterministic fallback directly. Deterministic presentation is permitted by the contract, but validated LLM composition should not be reported as integrated.
- Follow-up context is not restored into lifecycle interpretation and is not saved on successful clarification completion. Empty-context suggestions hardcode sales and years.
- Frontend hardcodes finance_dw and inserts labels/result text through innerHTML without escaping. Browser behavior remains unverified.

## Phase 3 gaps

- Forecast continuity checks only annual four-digit periods; monthly and quarterly gaps are not validated. allow_missing_as_zero skips validation without actually filling gaps. Monthly future labels fall back to +T labels. Empty history can produce a zero forecast fact instead of rejecting the analysis.
- Scenario and goal-seek baselines fall back to a driver's minimum or zero when no data/default exists (kernel.py:518–520,555–557). These are invented assumptions; missing required drivers should clarify or decline. Governed query output does not generally provide named driver values.
- Goal seek infers monotonicity from a finite sampled grid and labels targets PROVEN_UNREACHABLE_TARGET (science/goal_seek.py:341–362). Samples cannot prove global monotonicity, continuity, all roots or unattainability for the general supported expression language.
- Goal-seek facts use the adjustable driver unit for achieved output values, risking incorrect units. Rounding the reported driver can also change whether the reported solution meets tolerance.
- Science outputs share the lifecycle privacy call, but underlying population counts are absent; shared routing alone does not establish derived-result privacy.

## Phase 4 gaps and optional scope

- RepeatedQueryAccountingService uses an in-memory release list and is not connected to the persistent disclosure ledger or answer publication. Its count-difference heuristic does not establish subset/overlap relationships and misses equal-sized differing populations. Canonical scope lacks period/data version.
- Stronger audit HMAC and chaining exist, but use a hardcoded fallback key. No trusted external checkpoint prevents undetected tail deletion or rollback. Tenant-filtered verification conflicts with a global chain when tenants interleave.
- Calendar enrichment returns fiscal/calendar metadata; it does not match business events or appear in published answers.
- Presentation permits are absent. The contract explicitly makes these optional, so absence is not itself a completion blocker.
- Formal advanced privacy is not implemented; datasets needing unsupported protections must remain disabled.

## Setup/handoff gaps

No README.md, requirements.txt, pyproject.toml or .env.example was found in the project file inventory. Three migration definitions exist in storage/schema.py, but reproducible dependency installation and shared API/worker configuration instructions are missing. Existing tests and cached results are not proof of current completion; no test results are asserted by this audit.

## Recommended sequence

1. Wire the real interpreter and shared runtime configuration.
2. Enforce per-answer current authorization and actual protected-entity counts.
3. Fix durable clarification/context restoration, idempotency and all worker state fencing.
4. Correct aggregation, time-series, scenario baseline and solver behavior.
5. Integrate justified advanced controls or accurately mark them unavailable.
6. Add reproducible setup documentation, then begin regression and end-to-end verification.
