"""Ledger write throughput benchmark script.

Usage:
    python -m tools.bench.ledger_bench [--threads N] [--ops N] [--db PATH]

Outputs a structured JSON report with scenario / ops_per_sec / p95_latency_ms / wal_size.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger, WalConfig
from host_brain.core.ids import now_ms


def _bench_single_thread(ledger: SQLiteLedger, ops: int) -> list[float]:
    """Single-thread write benchmark. Returns list of latencies in ms."""
    latencies: list[float] = []
    for i in range(ops):
        start = time.perf_counter()
        ledger.log_error(
            request_id=f"bench_st_{i:06d}",
            stage="S_BENCH",
            reason_code="BENCH_WRITE",
            detail=f"benchmark single-thread write #{i}",
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
    return latencies


def _bench_multi_thread(ledger: SQLiteLedger, threads: int, ops_per_thread: int) -> list[float]:
    """Multi-thread write benchmark. Returns list of latencies in ms."""
    all_latencies: list[list[float]] = [[] for _ in range(threads)]
    barrier = threading.Barrier(threads)

    def worker(tid: int) -> None:
        barrier.wait()
        for i in range(ops_per_thread):
            start = time.perf_counter()
            ledger.log_error(
                request_id=f"bench_mt_{tid:03d}_{i:06d}",
                stage="S_BENCH",
                reason_code="BENCH_WRITE",
                detail=f"benchmark multi-thread write t={tid} #{i}",
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            all_latencies[tid].append(elapsed_ms)

    workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    return [lat for batch in all_latencies for lat in batch]


def _bench_mixed_read_write(ledger: SQLiteLedger, threads: int, ops_per_thread: int) -> list[float]:
    """Mixed read/write benchmark (50% read, 50% write)."""
    all_latencies: list[list[float]] = [[] for _ in range(threads)]
    barrier = threading.Barrier(threads)

    def worker(tid: int) -> None:
        barrier.wait()
        for i in range(ops_per_thread):
            start = time.perf_counter()
            if i % 2 == 0:
                ledger.log_error(
                    request_id=f"bench_mix_{tid:03d}_{i:06d}",
                    stage="S_BENCH",
                    reason_code="BENCH_MIXED",
                    detail=f"benchmark mixed write t={tid} #{i}",
                )
            else:
                _ = ledger.error_log  # read
            elapsed_ms = (time.perf_counter() - start) * 1000
            all_latencies[tid].append(elapsed_ms)

    workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    return [lat for batch in all_latencies for lat in batch]


def _compute_stats(latencies: list[float], total_duration_s: float) -> dict:
    """Compute benchmark statistics."""
    if not latencies:
        return {"ops_per_sec": 0, "p50_latency_ms": 0, "p95_latency_ms": 0, "p99_latency_ms": 0, "mean_latency_ms": 0}
    sorted_lats = sorted(latencies)
    p50_idx = int(len(sorted_lats) * 0.50)
    p95_idx = int(len(sorted_lats) * 0.95)
    p99_idx = int(len(sorted_lats) * 0.99)
    return {
        "ops_per_sec": round(len(latencies) / total_duration_s, 2) if total_duration_s > 0 else 0,
        "p50_latency_ms": round(sorted_lats[p50_idx], 3),
        "p95_latency_ms": round(sorted_lats[p95_idx], 3),
        "p99_latency_ms": round(sorted_lats[p99_idx], 3),
        "mean_latency_ms": round(statistics.mean(sorted_lats), 3),
    }


def run_benchmark(
    *,
    db_path: str | None = None,
    threads: int = 4,
    ops: int = 500,
) -> dict:
    """Run all benchmark scenarios and return structured report."""
    tmp = None
    if db_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        db_path = tmp.name
        tmp.close()

    try:
        wal_config = WalConfig()
        ledger = SQLiteLedger(db_path=db_path, check_same_thread=False, wal_config=wal_config)
        wal_health = ledger.wal_health()
        scenarios = []

        # Scenario 1: Single thread
        start = time.perf_counter()
        lats = _bench_single_thread(ledger, ops)
        duration = time.perf_counter() - start
        scenarios.append({
            "scenario": "single_thread_write",
            "threads": 1,
            "total_ops": ops,
            **_compute_stats(lats, duration),
        })

        # Scenario 2: Multi-thread write
        ops_per_thread = max(1, ops // threads)
        start = time.perf_counter()
        lats = _bench_multi_thread(ledger, threads, ops_per_thread)
        duration = time.perf_counter() - start
        scenarios.append({
            "scenario": "multi_thread_write",
            "threads": threads,
            "total_ops": ops_per_thread * threads,
            **_compute_stats(lats, duration),
        })

        # Scenario 3: Mixed read/write
        start = time.perf_counter()
        lats = _bench_mixed_read_write(ledger, threads, ops_per_thread)
        duration = time.perf_counter() - start
        scenarios.append({
            "scenario": "mixed_read_write",
            "threads": threads,
            "total_ops": ops_per_thread * threads,
            **_compute_stats(lats, duration),
        })

        # WAL checkpoint & size
        checkpoint_result = ledger.checkpoint("PASSIVE")
        wal_health_after = ledger.wal_health()
        ledger.close()

        return {
            "db_path": db_path,
            "wal_config": wal_config.as_dict(),
            "wal_health_before": wal_health,
            "wal_health_after": wal_health_after,
            "checkpoint_result": checkpoint_result,
            "scenarios": scenarios,
        }
    finally:
        if tmp is not None:
            try:
                os.unlink(db_path)
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite Ledger write throughput benchmark")
    parser.add_argument("--threads", type=int, default=4, help="Number of threads for multi-thread scenarios")
    parser.add_argument("--ops", type=int, default=500, help="Total operations per scenario")
    parser.add_argument("--db", type=str, default=None, help="Database file path (default: temp file)")
    args = parser.parse_args()

    report = run_benchmark(db_path=args.db, threads=args.threads, ops=args.ops)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
