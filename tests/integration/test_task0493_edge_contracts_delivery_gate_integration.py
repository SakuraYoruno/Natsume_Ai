"""TASK-0493 aggregate Delivery Gate integration; no modules under scan are imported."""

from __future__ import annotations

from pathlib import Path

from tools.static_scan.run_all import run_static_scans


ROOT = Path(__file__).resolve().parents[2]


def test_delivery_static_scan_runs_the_exact_edge_contracts_freeze() -> None:
    aggregate = run_static_scans(ROOT)

    assert aggregate["status"] == "passed"
    assert aggregate["failures"] == []
