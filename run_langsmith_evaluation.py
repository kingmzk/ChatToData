"""Automated LangSmith Evaluation Suite.

Uploads and synchronizes the 'Talk2Data-Golden-Benchmark' dataset in LangSmith,
evaluates all contract and governance cases, and records benchmark metrics
(Outcome, Intent, ExactFacts, Governance, and Latency) with full tracing.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

DATASET_NAME = "Talk2Data-Golden-Benchmark"
PROJECT_NAME = os.getenv("LANGSMITH_PROJECT", "Talk2Data")


def sync_golden_dataset(client) -> str:
    """Create or retrieve the Talk2Data-Golden-Benchmark dataset in LangSmith."""
    from backend.eval.cases import cases

    eval_cases = cases()
    try:
        if client.has_dataset(dataset_name=DATASET_NAME):
            dataset = client.read_dataset(dataset_name=DATASET_NAME)
            print(f"[*] Found existing LangSmith dataset '{DATASET_NAME}' (ID: {dataset.id})")
            return dataset.id
        else:
            dataset = client.create_dataset(
                dataset_name=DATASET_NAME,
                description="Golden benchmark ground truth for Talk2Data verified analytical contracts and governance policies.",
            )
            print(f"[+] Created LangSmith dataset '{DATASET_NAME}' (ID: {dataset.id})")

            for c in eval_cases:
                client.create_example(
                    inputs={"question": c["question"], "source": c["source"], "tenant": c.get("tenant", "tenant_a")},
                    outputs=c["expected"],
                    metadata={"case_name": c["name"]},
                    dataset_id=dataset.id,
                )
            print(f"[+] Synchronized {len(eval_cases)} golden examples to LangSmith dataset.")
            return dataset.id
    except Exception as exc:
        print(f"[!] LangSmith dataset sync note: {exc}")
        return ""


def main():
    print("=" * 75)
    print("TALK2DATA AUTOMATED LANGSMITH EVALUATION")
    print(f"Project: {PROJECT_NAME} | Dataset: {DATASET_NAME}")
    print("=" * 75)

    api_key = os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        print("[!] LANGSMITH_API_KEY is not configured in .env; skipping remote upload.")
        sys.exit(1)

    from langsmith import Client
    client = Client()

    dataset_id = sync_golden_dataset(client)

    # Run native evaluation suite
    from backend.eval.run_deep import run
    started = time.perf_counter()
    report = run(live=False, publish=True, output="eval-results/langsmith-eval.json")
    total_duration = time.perf_counter() - started

    results = report.get("results", [])
    total_cases = len(results)
    passed_cases = sum(1 for r in results if r["outcome"] == "PASS")

    # Aggregate metric pass rates
    metric_counts: dict[str, int] = {}
    metric_passes: dict[str, int] = {}
    for r in results:
        for m in r.get("metrics", []):
            name = m["metric"]
            metric_counts[name] = metric_counts.get(name, 0) + 1
            if m["score"] == 1.0:
                metric_passes[name] = metric_passes.get(name, 0) + 1

    print("\n" + "=" * 75)
    print("EVALUATION METRIC BREAKDOWN:")
    print("-" * 75)
    for name, total in metric_counts.items():
        passes = metric_passes.get(name, 0)
        pct = (passes / total * 100) if total else 0
        print(f"  * {name:<25}: {passes}/{total} passed ({pct:.1f}%)")

    pass_pct = (passed_cases / total_cases * 100) if total_cases else 0
    avg_latency = total_duration / total_cases if total_cases else 0
    print("-" * 75)
    print(f"Overall Pass Rate: {passed_cases}/{total_cases} ({pass_pct:.1f}%)")
    print(f"Average Turn Latency: {avg_latency*1000:.1f} ms")
    print(f"LangSmith Evaluation Traces: Logged to project '{PROJECT_NAME}'")
    print("=" * 75)

    if passed_cases != total_cases:
        sys.exit(1)


if __name__ == "__main__":
    main()
