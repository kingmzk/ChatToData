# Talk2Data browser verification

Date: 2026-10-09. App and worker are running at http://127.0.0.1:8000 with isolated synthetic data.

Open the visual report: http://127.0.0.1:8000/static/browser-report/index.html

## Actual live browser execution

- Revenue question: 180 USD for 2024, using Gemini 3.5 Flash.
- Gemini 3.8 Flash: comparison (180 vs 160, delta 20, growth 12.50%), clarification/resumption, forecast (200 for 2025, 220 for 2026), browser CSV/XLSX downloads, security rejection and cancellation passed.
- Live anomaly, scenario and goal seek were blocked by provider quota, not counted as successful live interpretations.
- No page JavaScript errors were captured.

## Labelled structured-input browser checks

Eight flows passed using development proposed-intent inputs: comparison, clarification, forecast, anomaly, scenario, goal seek, privacy suppression and unsupported metric decline. These inputs bypass Gemini only; they exercise real API submission, the separate worker, governance, deterministic calculations, result persistence, browser rendering and exports. Screenshots have a prominent yellow test-mode banner. These are not claimed as live natural-language accuracy tests.

CSV and XLSX downloads were independently inspected: comparison values 180, 160 and 20; DELTA is explicitly labelled. The goal-seek driver of 30.00068359375 produces 150.00341796875 under price × quantity, within 0.01 of the target. Protected revenue is suppressed when five entities fail the configured minimum of six.

## Corrections made during browser testing

- Comparison time dimensions no longer split each period into unmatchable categories.
- Missing/suppressed chart values remain null rather than crashing or appearing as numeric zero.
- Charts exclude percentage/delta diagnostics from currency time series, and standard chart specifications render in the UI.
- Clarification polling continues while its resumed job is queued or running.
- Clarification retries restore the saved pending state.
- Gemini requests use a constrained structured response schema.
- Quota/configuration failures fail promptly with a provider status; adapter retries are not multiplied by worker retries.
- Export tables label calculation type to distinguish observations from deltas.

## External blockers

Gemini 3.5 Flash reached a daily free-tier limit of 20 requests. Gemini 2.5 Flash generation returned 404 despite appearing in model discovery. Gemini 3.8 Flash passed preflight and multiple real browser questions, then hit 503/504 demand errors and quota exhaustion. Existing .env was preserved. No complete live-LLM pass or general accuracy score is claimed. The five-case Gemini 3.8 test run recorded 2 passes, 2 provider-related skips and 1 failed contextual case due to 503/504; it remains incomplete.

Use `python scratch/start_browser_demo.py` to reproduce the isolated session after setting TALK2DATA_BROWSER_MODEL and TALK2DATA_BROWSER_DB as described by its saved runtime. Screenshots, browser evidence, synthetic databases and local audit key are excluded from version control. Only screenshots, synthetic exported results and redacted test evidence are served in the visual report.

## Final regression results

145 offline tests passed; 5 live tests deliberately skipped in the final offline collection. The separate live run remains incomplete for the provider-related reasons above. Eight labelled browser fixture flows passed with zero captured JavaScript errors. Browser CSV/XLSX files and goal-seek arithmetic passed independent verification.

## Fifteen-question browser verification, including two-table joins

All 15 structured-input Chrome checks passed with zero captured JavaScript errors. The original 10 questions and five new questions exercised real API/worker execution. Gemini interpretation was bypassed due to provider quota; this is not live natural-language verification. New source `demo_join_sales` joins sales to customers, tenant-scopes both tables, and retains minimum-group threshold five. Observed North/South 2024 revenue: 700/500 USD; Business segment: 350/250 USD; growth: 40%/25%; North average sale: 70 USD; growth contribution: 66.67%/33.33%. Five new backend pipeline regressions passed. Screenshots and full observed output: `/static/browser-report/questions.html`.
