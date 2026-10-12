# DeepEval and LangSmith implementation report

DeepEval is integrated as an isolated synthetic evaluation harness; LangSmith stage tracing is active in the restarted local API/worker. The supplied key is stored in ignored `.env` and excluded from source, reports and trace payloads.

## Validation

- 19/19 fixture cases passed, with exact contracts for outcomes, intent, scoped facts, science diagnostics, evidence integrity and suppression.
- 18 distinct live-mode cases have passing latest results across bounded runs on `gemini-3.1-flash-lite`. Of these, 16 invoked Gemini and two verified rejection before interpretation. Unknown-metric validation remains fixture-only.
- This is a curated suite and a result across verification runs after prompt fixes, not a single clean full live run or a production accuracy estimate.
- Initial live run: two failed contracts (unrequested year breakdowns), then one provider timeout. Targeted retry: 3/3 passed. Expanded run: 11/12 passed; average sale returned correct 70 USD but wrong TOTAL family. Final targeted run: 4/4 passed after adding aggregation/family metadata and an average rule. These earlier failures and the blocked request remain in local run reports.
- Final regression: 165 passed, 5 opt-in live tests skipped. Third-party deprecation warnings remain.
- Mutation tests reject wrong numbers, dimensions, periods, units, extra facts, non-finite values, science types/diagnostics, disclosure leakage and invalid seals. Telemetry tests verify payload exclusion, original exception propagation and no operation replay on tracing failure.
- Restarted app queued join smoke: ANSWERED. LangSmith readback verified claimed jobs, turn handling, governed execution, compilation, query execution, deterministic analysis, privacy, presentation and publication stages. Recent inputs/outputs contained only approved status/score/case fields.

## Tracing boundaries

Stage traces carry timing and status. They exclude raw questions, prompts, tenant/user IDs, SQL, rows, facts, answer values and exception details. Idle polling is excluded. Explicit evaluation score uploads carry synthetic case IDs, fixture/live mode, model and scores only. Tracing does not replace the signed audit trail or authorization. Telemetry outages do not repeat or fail app operations. The live evaluation model override did not change the app's configured model.

[Open the verified LangSmith project](https://smith.langchain.com/o/221c82df-fe6f-4b68-a935-52d31a54ad05/projects/p/b41797eb-13a9-4341-b985-2b3f0b52d3d7).

## Reproduction

See README's DeepEval/LangSmith section and `requirements-eval.txt`. Run `.venv-evals/Scripts/python -m talk2data.eval.run_deep` for offline checks; add `--live --model gemini-3.1-flash-lite --langsmith --cases total,join_region` for bounded live checks and score upload. Provider failures stop the run and exit with code 2; failed contracts exit with code 1. Local JSON reports are under ignored `eval-results/`. DeepEval 4.2.8 is installed in `.venv-evals`; it is separate from the app environment. New application installs include LangSmith through `requirements.txt`.

The integration follows [DeepEval's custom metric API](https://deepeval.com/docs/metrics-custom) and [LangSmith's manual instrumentation API](https://docs.langchain.com/langsmith/annotate-code). Arbitrary narrative quality, multi-turn behavior outside clarification and natural-language coverage beyond these curated cases are not established by this suite.
