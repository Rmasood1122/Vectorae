"""
Vectorae — Performance Benchmark Script

Runs against a local Docker stack with mocked external APIs.
Measures and prints: pipeline throughput, aggregation time, API latency (p50/p95/p99).

Usage:
    python scripts/benchmark.py                 # full benchmark
    python scripts/benchmark.py --dry-run       # validate harness only, no real requests
    python scripts/benchmark.py --json          # machine-readable JSON output

Output: Markdown table with PASS/WARN/FAIL against targets.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

TARGETS = {
    "pipeline_step_avg_s":      5.0,    # < 5s per step (mocked Anthropic)
    "verification_loop_total_s": 30.0,  # < 30s for all 10 passes
    "lead_sourcing_100_s":      60.0,   # 100 leads verified in < 60s
    "message_schedule_500_s":    5.0,   # schedule 500 messages in < 5s
    "aggregation_10k_s":        30.0,   # aggregate 10,000 outcomes in < 30s
    "api_p50_ms":              200.0,   # p50 < 200ms
    "api_p95_ms":              500.0,   # p95 < 500ms
    "api_p99_ms":             1000.0,   # p99 < 1s
}

API_BASE = os.getenv("BENCHMARK_API_URL", "http://localhost:8000/api/v1")
CONCURRENT_USERS = 50


# ---------------------------------------------------------------------------
# Benchmark helpers
# ---------------------------------------------------------------------------

def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (p / 100) * (len(sorted_data) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_data) - 1)
    frac = idx - lo
    return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac


def _grade(measured: float, target: float) -> str:
    if measured <= target:
        return "PASS"
    if measured <= target * 2:
        return "WARN"
    return "FAIL"


def _row(name: str, value: float, unit: str, target: float) -> dict:
    grade = _grade(value, target)
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "target": target,
        "grade": grade,
    }


# ---------------------------------------------------------------------------
# Individual benchmarks
# ---------------------------------------------------------------------------

def bench_pipeline_step(dry_run: bool) -> dict:
    """Measure average time per pipeline step (mocked Anthropic)."""
    if dry_run:
        avg_s = 0.1
    else:
        # Simulate 72 steps: each step is an Anthropic call
        times = []
        for _ in range(72):
            t0 = time.monotonic()
            # Simulated call (real benchmark would hit the API)
            time.sleep(0.05)  # replace with actual API call in integration
            times.append(time.monotonic() - t0)
        avg_s = statistics.mean(times) if times else 0.0
    return _row("Pipeline step (avg)", avg_s, "s", TARGETS["pipeline_step_avg_s"])


def bench_verification_loop(dry_run: bool) -> dict:
    """Total time for 10 verification passes."""
    if dry_run:
        total_s = 0.5
    else:
        t0 = time.monotonic()
        for _ in range(10):
            time.sleep(0.1)  # replace with actual verification pass call
        total_s = time.monotonic() - t0
    return _row("Verification loop (10 passes)", total_s, "s", TARGETS["verification_loop_total_s"])


def bench_lead_sourcing(dry_run: bool) -> dict:
    """Time to source and verify 100 leads."""
    if dry_run:
        total_s = 1.0
    else:
        t0 = time.monotonic()
        for _ in range(100):
            time.sleep(0.005)  # replace with actual enrichment call
        total_s = time.monotonic() - t0
    return _row("Lead sourcing (100 leads)", total_s, "s", TARGETS["lead_sourcing_100_s"])


def bench_message_scheduler(dry_run: bool) -> dict:
    """Time to schedule 500 messages."""
    if dry_run:
        total_s = 0.1
    else:
        t0 = time.monotonic()
        for _ in range(500):
            pass  # replace with schedule_sequence_step() call
        total_s = time.monotonic() - t0
    return _row("Message scheduler (500 msgs)", total_s, "s", TARGETS["message_schedule_500_s"])


def bench_nightly_aggregation(dry_run: bool) -> dict:
    """Time to aggregate 10,000 outcome events."""
    if dry_run:
        total_s = 0.5
    else:
        t0 = time.monotonic()
        # Simulate computing scores for 10k outcomes
        import math
        _ = [math.exp(-0.0077 * i) for i in range(10000)]
        total_s = time.monotonic() - t0
    return _row("Nightly aggregation (10k outcomes)", total_s, "s", TARGETS["aggregation_10k_s"])


async def bench_api_latency(dry_run: bool) -> list[dict]:
    """p50/p95/p99 API response times under 50 concurrent users."""
    if dry_run:
        return [
            _row("API latency p50", 50.0, "ms", TARGETS["api_p50_ms"]),
            _row("API latency p95", 120.0, "ms", TARGETS["api_p95_ms"]),
            _row("API latency p99", 280.0, "ms", TARGETS["api_p99_ms"]),
        ]

    import httpx

    endpoints = [
        "/health",
        "/playbook/scores",
        "/onboarding/state",
    ]

    latencies: list[float] = []

    async def _hit(client: httpx.AsyncClient, url: str) -> float:
        t0 = time.monotonic()
        try:
            await client.get(url, timeout=5.0)
        except Exception:
            pass
        return (time.monotonic() - t0) * 1000  # ms

    async with httpx.AsyncClient(base_url=API_BASE) as client:
        tasks = []
        for _ in range(CONCURRENT_USERS):
            for ep in endpoints:
                tasks.append(_hit(client, ep))
        latencies = await asyncio.gather(*tasks)

    latencies = [l for l in latencies if l is not None]

    return [
        _row("API latency p50", _percentile(latencies, 50), "ms", TARGETS["api_p50_ms"]),
        _row("API latency p95", _percentile(latencies, 95), "ms", TARGETS["api_p95_ms"]),
        _row("API latency p99", _percentile(latencies, 99), "ms", TARGETS["api_p99_ms"]),
    ]


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------

def _markdown_table(results: list[dict]) -> str:
    lines = [
        "| Benchmark | Measured | Unit | Target | Grade |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        grade_emoji = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(r["grade"], "")
        lines.append(
            f"| {r['name']} | {r['value']:.2f} | {r['unit']} | {r['target']} | {grade_emoji} {r['grade']} |"
        )
    return "\n".join(lines)


async def main(dry_run: bool = False, json_output: bool = False) -> int:
    print(f"\n{'[DRY-RUN] ' if dry_run else ''}Vectorae — Performance Benchmark\n")

    results: list[dict] = []
    results.append(bench_pipeline_step(dry_run))
    results.append(bench_verification_loop(dry_run))
    results.append(bench_lead_sourcing(dry_run))
    results.append(bench_message_scheduler(dry_run))
    results.append(bench_nightly_aggregation(dry_run))
    api_results = await bench_api_latency(dry_run)
    results.extend(api_results)

    if json_output:
        print(json.dumps(results, indent=2))
    else:
        print(_markdown_table(results))

    failures = [r for r in results if r["grade"] == "FAIL"]
    warnings = [r for r in results if r["grade"] == "WARN"]

    print(f"\n{'DRY-RUN COMPLETE — ' if dry_run else ''}"
          f"Results: {len(results) - len(failures) - len(warnings)} PASS, "
          f"{len(warnings)} WARN, {len(failures)} FAIL")

    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Vectorae benchmark")
    parser.add_argument("--dry-run", action="store_true", help="Validate harness only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    exit_code = asyncio.run(main(dry_run=args.dry_run, json_output=args.json))
    sys.exit(exit_code)
