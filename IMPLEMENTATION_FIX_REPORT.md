> Follow-up browser testing exposed additional defects that were corrected. See BROWSER_TEST_REPORT.md for screenshots, live-provider limitations and the latest result: 145 offline passes plus 8 labelled browser-flow passes.

# Talk2Data implementation corrections

Date: 2026-10-09. This report supersedes the historical PHASE_IMPLEMENTATION_AUDIT.md baseline.

## Corrected execution paths

- Natural-language submission now invokes the Gemini adapter with accessible metric, dimension, scenario and time-series metadata. Structured intent remains untrusted and strictly validated. Model failures remain execution failures.
- Standalone API and daemon use one shared declarative runtime configuration for catalogs, tenant-source grants and a read-only SQLite source. Added synthetic initialization, dependency manifest, sample environment and startup instructions.
- Question submission is atomic and idempotent across independent database connections. Conflicting payloads return a conflict. Ordinary service submissions return durable 202 acceptance before worker execution. The embedded development/test worker runs after the response.
- Clarification and analytical scope persist in a versioned migration. New workers restore the exact original semantic release, reference timestamp, pending intent and clarification choices. Catalog releases cannot be overwritten or mutated through returned references.
- Worker leases renew; stale attempts cannot update turn state, fail a job, pause clarification or publish. Cancellation and bounded retry/crash recovery preserve terminal state. Answer insertion, successful turn status, audit events, context and job completion commit together.
- Answer and export retrieval recheck current source, metric and dimension/filter permissions using the saved per-turn analytical scope. Answers without that scope fail closed. Dataset export prohibitions are enforced.
- Governed queries count distinct declared protected entities. Conservative suppression protects derived facts, diagnostics and prediction intervals. Dataset role/metric/dimension/operation policy is enforced before execution. Sensitive metrics without population counts are suppressed.
- Catalog filters resolve to their actual table/column definitions; child filters use tenant-scoped semi-joins. Decimal allocation division and aggregation avoid introducing floating-point arithmetic. Non-additive group values are not totaled; grouped comparisons retain category results. Average requests require an approved average metric.
- Monthly, quarterly and annual series validate their declared frequency, ordering, duplicates and missing periods. Explicit zero fill is performed rather than merely skipping validation. Empty science history and missing scenario baselines are rejected.
- Goal seek proves unreachable targets only when the expression is affine in the adjustable driver. General sampled models remain unbracketed when no bracket is found. Solver outputs retain precise values and pipeline facts use the output metric unit.
- Durable audit events are signed and chained. Public fallback signing secrets were removed. Global-chain verification handles tenant interleaving and can compare a supplied trusted head checkpoint. Database migrations run under an exclusive writer transaction during concurrent startup.
- Frontend selects accessible sources, escapes displayed values, shows assumptions and limitations, polls submitted work and offers cancellation. JavaScript syntax was checked.

## Verification

Complete offline test collection: **143 passed, 5 skipped**. The five skips are opt-in live Gemini tests; live calls were explicitly disabled. One dependency deprecation warning remains in Starlette/AnyIO. Added 20 targeted regressions for the audited defects, including independent concurrent database connections, clarification after restart/catalog change, source permission revocation, privacy counts, period gaps and the standalone worker process.

Existing export/restart fixtures now provide the authorization services and immutable answer scope required by the corrected contract. Annual fixtures explicitly declare YEAR frequency. The allocation test now expects exact decimal division instead of a NUMERIC cast. Assertions were retained and extended; tests were not deleted to obtain a pass.

## Supported limits and remaining verification

- Actual interactive browser execution and the live Gemini suite remain unverified. API/frontend integration tests and JavaScript syntax checks do not substitute for these checks.
- Cross-query/differential privacy is not supplied by the experimental count-only helper. Datasets requiring it are explicitly disabled. This is a fail-closed scope restriction, not a claim that advanced privacy is implemented.
- External trusted checkpoint storage, append-only replication and rollback protection require deployment infrastructure. Verification accepts a trusted head, but the repository does not create a separately trusted checkpoint service.
- Presentation uses deterministic server templates. Optional model prose enrichment, presentation permits and matching business-calendar events are not required or claimed as integrated features.
- The supported physical source is SQLite. Production browser login/token integration and a full identity/revocation provider are not included. Production requires configured authentication and audit secrets; development convenience identity is for local use.
- Decimal operations do not restore precision lost in source floats. Statistical intervals remain model-dependent estimates; finite goal-seek grids do not establish continuity or discover every root.

Read README.md for reproducible setup, configuration and runtime scope. Do not interpret offline fixture success as a general natural-language accuracy score or a production-readiness guarantee.
