from __future__ import annotations

from collections import Counter
from functools import lru_cache
import json
from pathlib import Path

from tools.architecture import edge_contracts_guard as guard


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tools" / "architecture" / "edge_host_import_baseline.json"
MIGRATION = ROOT / "tools" / "architecture" / "edge_host_import_migration.json"


@lru_cache(maxsize=1)
def _repository_report() -> dict[str, object]:
    return guard.scan_report(
        ROOT,
        baseline_path=BASELINE,
        mode=guard.GuardMode.FREEZE_NEW,
    )


def test_repository_boundary_baseline_is_exact_content_addressed_and_current() -> None:
    report = _repository_report()
    counts = Counter(row["direction"] for row in report["findings"])
    kinds = Counter((row["direction"], row["kind"]) for row in report["findings"])

    assert report["passed"] is True
    assert report["violations"] == []
    assert report["finding_count"] == 7
    assert counts == {"HOST_TO_EDGE": 7}
    assert kinds[("HOST_TO_EDGE", "dynamic_risk")] == 7
    assert not [
        row for row in report["findings"] if row["direction"] == "EDGE_TO_HOST"
    ]
    assert not [
        row for row in report["findings"] if row["direction"].startswith("CONTRACTS_")
    ]
    assert report["baseline_fingerprints_sha256"] == (
        "sha256:c7911197b99c1ede561751675a20ee299fe8c9e179afa7306de02c469525963b"
    )
    assert report["current_fingerprints_sha256"] == report[
        "baseline_fingerprints_sha256"
    ]


def test_every_debt_edge_has_one_owner_target_deadline_and_content_digest() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    migration = json.loads(MIGRATION.read_text(encoding="utf-8"))
    report = _repository_report()

    assert len(baseline["expected_fingerprints"]) == 7
    assert baseline["fingerprints_sha256"] == guard.fingerprint_digest(
        baseline["expected_fingerprints"]
    )
    assert baseline["migration_sha256"] == guard.content_digest(migration)
    assert len(migration["edges"]) == 7
    assert len(report["mapped_migration_edges"]) == 7
    assert {edge["edge_id"] for edge in migration["edges"]} == set(
        report["mapped_migration_edges"]
    )
    for edge in migration["edges"]:
        assert edge["owner"]
        assert edge["migration_target"]
        assert edge["remove_by"] == "TASK-0581"
        assert edge["priority"] in {"P0", "P1", "P2"}


def test_aw06_baseline_records_edge_to_host_zero() -> None:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert all(
        not fingerprint.startswith("EDGE_TO_HOST|")
        for fingerprint in baseline["expected_fingerprints"]
    )


def test_delivery_gate_invokes_guard_in_non_downgradable_freeze_mode() -> None:
    run_all = (ROOT / "tools" / "static_scan" / "run_all.py").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github" / "workflows" / "delivery-gates.yml").read_text(
        encoding="utf-8"
    )
    scanner = (ROOT / "tools" / "architecture" / "edge_contracts_guard.py").read_text(
        encoding="utf-8"
    )

    assert "_load_edge_contracts_guard_module().scan_repo(repo_root)" in run_all
    assert "--checks static_scan,doc_gate" in workflow
    assert "scan_report(root, mode=GuardMode.FREEZE_NEW, environ={})" in scanner
    assert "EDGE_CONTRACTS_ONLY_GUARD_MODE=FREEZE_NEW" in (
        ROOT / ".env.example"
    ).read_text(encoding="utf-8")


def test_guard_scope_has_no_runtime_import_or_baseline_regeneration_backdoor() -> None:
    source = (ROOT / "tools" / "architecture" / "edge_contracts_guard.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "from host_brain",
        "import host_brain",
        "from edge_client",
        "import edge_client",
        "write_baseline",
        "update_baseline",
        "accept_current",
    )

    assert all(token not in source for token in forbidden)
    assert not (ROOT / "tools" / "architecture" / "__pycache__").is_symlink()
