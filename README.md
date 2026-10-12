# ChatToData (Talk2Data)

Governed conversational analytics with an explicit semantic catalog. The supported physical source is SQLite. Standard calculations, forecasts, anomalies, scenarios and goal seek are deterministic; Gemini interprets questions and does not calculate results. Presentation uses server templates.

## Local setup (PowerShell, Python 3.11 or later)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m talk2data.demo --directory demo
Copy-Item .env.example .env
```

Edit `.env` with your Gemini API key, your explicitly selected `TALK2DATA_MODEL`, and a strong `TALK2DATA_AUDIT_KEY`. Preserve any existing `.env`; the copy command is only for a new installation. The demo contains synthetic data only. It refuses to overwrite existing demo files. To initialize or upgrade the application database, use:

```powershell
python -c "from talk2data.storage.repository import PersistentApplicationStore; PersistentApplicationStore('talk2data.db').conn.close()"
```

In one terminal:

```powershell
python -m talk2data.api.server --host 127.0.0.1 --port 8000
```

In another terminal, from the same project directory and environment:

```powershell
python -m talk2data.jobs.daemon
```

Open http://127.0.0.1:8000. Select `demo_sales`. Examples: “What was revenue in 2024?”, “Forecast annual revenue for the next two years”. Missing interpretation configuration produces a failed request, not a fabricated result. Ordinary API requests enqueue work and return 202; the standalone worker processes it. Injected demo/test managers use an embedded worker after the response is sent.

Both processes read `TALK2DATA_RUNTIME_CONFIG`. Catalog releases and source permissions come from this declarative JSON; the source connection opens read-only. Keep old catalog releases available for active turns and previously published answers. Restart processes after changing runtime configuration. Do not use development authentication outside local development.

## Configuration and production

`TALK2DATA_DB_PATH`: persistent job/answer database. `TALK2DATA_RUNTIME_CONFIG`: shared JSON configuration. `TALK2DATA_MODEL`: canonical model; `GEMINI_MODEL` is a compatibility alias. `GEMINI_API_KEY`: interpretation credential. `TALK2DATA_ENV`: development or production. Production requires `TALK2DATA_AUTH_SECRET` and `TALK2DATA_AUDIT_KEY`. Authentication verifies signed bearer tokens; production rejects client identity headers and proposed intent JSON. The browser currently uses local development identity; production bearer-token UI/login integration must be provided before serving it publicly.

The bundled bearer-token helper is for controlled deployment integration, not a complete identity provider or account-management system. Source grants are tenant scoped. Metric/dimension roles and permissions are checked before execution, publication and retrieval. Legacy answers missing their immutable analytical scope fail closed.

Protected datasets must declare `protected_entity_column` and `min_entities`, or a `disclosure_policy` specifying the protected entity and allowed roles/metrics/dimensions/operations. The pipeline counts distinct protected entities in the governed query. Conservative suppression applies to all derived outputs when any underlying group is too small. Confidential/restricted metrics without counts are suppressed. Declare `requires_repeated_query_protection` for datasets needing cross-query privacy: those datasets are disabled because the experimental count-only accounting helper is not a sufficient privacy mechanism. No differential privacy guarantee is made.

Audit events and publication are persisted in a signed hash chain. Configure a stable secret for restart verification. `verify_audit_integrity(expected_head=...)` can compare against a head digest captured in a separately trusted system; this repository does not supply independent checkpoint storage. Without a trusted checkpoint, valid tail deletion or rollback cannot be detected reliably.

## Verification

```powershell
python -m pytest -q tests --ignore=tests/test_live_gemini_integration.py
```

Live model tests are opt-in; consult `tests/test_live_gemini_integration.py` for its environment flag. Do not treat offline fixture tests as a measured general natural-language accuracy score. Browser API tests do not establish actual browser JavaScript execution.

## Analytical scope

Forecast/anomaly methods accept regular annual, quarterly or monthly period labels matching the declared frequency. Gaps are rejected unless the catalog explicitly permits zero fill. Scenario inputs require approved defaults or retrieved driver values; missing baselines are rejected. Goal seek proves unattainability only for affine models whose endpoint range excludes the target. For general models, finite sampling can find brackets but cannot prove continuity, discover every root or establish global infeasibility. Model output values remain in the output metric unit.

Decimal aggregates and allocation division avoid introducing binary floating-point arithmetic. They cannot recover precision lost in source floats or earlier expressions. Preserve exact amounts as decimal text in SQLite when exact decimal behavior is required. Non-additive group values are not summed into an overall metric. Advanced multi-source connectors, formal privacy and external checkpoint infrastructure are outside the supported deployment.

## Two-table synthetic demo

After creating the demo runtime, run `python -m talk2data.join_demo --directory browser-demo` and restart the API and worker. Select `demo_join_sales`. This adds synthetic sales and customers linked by the approved customer relationship, with tenant filtering on both tables and a minimum of five distinct customers per group. The original `demo_sales` source remains available.

## DeepEval and LangSmith

Application stage tracing uses LangSmith (`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=Talk2Data`). The API and worker both load the ignored local `.env`; restart them after changing configuration. Traces include stage names, timing and terminal status only. Questions, prompts, tenant/user identifiers, SQL, rows, facts, answers and raw exceptions are excluded. Interpretation, turn/clarification handling, compilation, execution, deterministic analysis, privacy, presentation, claimed jobs and transactional publication are nested where they execute within a parent trace. Idle polling is not traced. Telemetry failures do not repeat or fail application operations. This instrumentation is operational telemetry, separate from the signed audit trail.

Install evaluation dependencies in a separate environment:

```powershell
python -m venv .venv-evals
.venv-evals/Scripts/python -m pip install -r requirements-eval.txt
.venv-evals/Scripts/python -m talk2data.eval.run_deep
```

The 19-case synthetic DeepEval suite evaluates outcomes, intent fields, exact fact sets (including units, groups and periods), scientific diagnostics/types, evidence integrity and disclosure suppression. Cases cover totals, trends, comparisons, clarification/resumption, forecasts, anomaly detection, scenarios, goal seek, two-table joins/filters/averages/contribution, unknown metrics, tenant injection and source denial. These deterministic custom metrics require no judge model. Mathematical and privacy correctness must not be replaced by subjective LLM scores. Ordinary eval runs perform no Gemini calls or LangSmith uploads.

```powershell
# Explicit live interpretation and optional value-free score uploads
.venv-evals/Scripts/python -m talk2data.eval.run_deep --live --model gemini-3.1-flash-lite --langsmith --cases total,join_region --output eval-results/live.json
# Full regression including evaluator mutation tests
$env:LANGSMITH_TRACING='false'
$env:RUN_LIVE_LLM_TESTS='0'
.venv-evals/Scripts/python -m pytest -q
```

Live runs use the explicitly selected model without changing the app model. Provider failures stop a run and are reported as BLOCKED, never as passes. Reports separate fixture/live mode, planned/completed counts, failures, model and metric scores. Exit codes: 0 success, 1 evaluation failure, 2 provider blocked. Reports under ignored `eval-results/` contain synthetic evidence and remain local; `--langsmith` publishes case IDs, mode/model and scores only. Malformed model output fails the contracts. Non-finite values, changed dimensions/periods/units, extra facts, wrong science metadata, broken evidence seals and suppressed-diagnostic leaks are covered by mutation tests. This curated suite is not a general production accuracy estimate. Numerical tests do not establish arbitrary narrative quality or natural-language coverage outside the curated cases.

Official API references: [DeepEval custom metrics](https://deepeval.com/docs/metrics-custom) and [LangSmith manual instrumentation](https://docs.langchain.com/langsmith/annotate-code).

## Complete project guide

[Read the step by step architecture guide](docs/architecture/Talk2Data_Architecture_Guide.md). The matching Word manual and ten diagrams are in `docs/architecture/`. A local browser reading copy is served at `/static/architecture-guide/index.html`. It follows the UI and current code, and distinguishes implemented services from future MCP, vector RAG and multiagent extensions.
