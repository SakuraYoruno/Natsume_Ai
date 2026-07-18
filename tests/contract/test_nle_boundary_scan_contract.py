from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = ROOT / "tools" / "static_scan" / "nle_boundary_scan.py"
POLICY = ROOT / "tools" / "static_scan" / "nle_boundary_policy.json"
FIXTURES = ROOT / "tests" / "fixtures" / "static_scan" / "nle_boundary"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("nle_boundary_scan_contract", SCAN_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scan script: {SCAN_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_t_nle_01_production_boundary_baseline_is_exact_and_current() -> None:
    scanner = _load_scanner()
    report = scanner.scan_report(ROOT, policy_path=POLICY)

    assert report["policy_id"] == "TASK-0465_NLE_BOUNDARY_CANON_APPLY"
    assert report["violations"] == []
    counts = Counter(row["rule_id"] for row in report["existing_debt"])
    assert counts == {}
    assert report["existing_debt"] == []
    for row in report["existing_debt"]:
        assert row["path"].endswith(".py")
        assert row["symbol"]
        assert row["owner"]
        assert row["reason"]
        assert row["remove_task"]
        assert row["lines"]


def test_t_nle_01_world_canon_trusted_sites_are_exact_apply_authority() -> None:
    scanner = _load_scanner()
    policy = scanner.load_policy(POLICY)
    rule = next(
        row
        for row in policy["rules"]
        if row["rule_id"] == "NLE-BND-WORLDCANON-RAW-MUTATION"
    )

    trusted_sites = {
        (row["path"], row["symbol"], row["line"])
        for row in rule["trusted_call_sites"]
    }
    assert trusted_sites == {
        (
            "host_brain/memory/canon_apply_service.py",
            "WorldCanonStore._CANON_APPLY_PERMIT",
            43,
        ),
        (
            "host_brain/memory/canon_apply_service.py",
            "WorldCanonStore._delete_authorized_document",
            1235,
        ),
        (
            "host_brain/memory/canon_apply_service.py",
            "WorldCanonStore._CANON_APPLY_PERMIT",
            1240,
        ),
        (
            "host_brain/memory/canon_apply_service.py",
            "WorldCanonStore._replace_authorized_payload",
            1243,
        ),
        (
            "host_brain/memory/canon_apply_service.py",
            "WorldCanonStore._CANON_APPLY_PERMIT",
            1248,
        ),
        (
            "host_brain/memory/canon_world_store.py",
            "CanonFile.open",
            390,
        ),
        (
            "host_brain/memory/canon_world_store.py",
            "CanonFile.replace",
            394,
        ),
        (
            "host_brain/memory/canon_world_store.py",
            "CanonFile.unlink",
            397,
        ),
        (
            "host_brain/memory/canon_world_store.py",
            "CanonFile.unlink",
            423,
        ),
        (
            "host_brain/app/observability_runtime/domain_command_ports.py",
            "WorldCanonStore.approve_proposal",
            419,
        ),
        (
            "host_brain/app/observability_runtime/domain_command_ports.py",
            "WorldCanonStore.rollback_proposal",
            459,
        ),
    }


def test_t_nle_01_stale_allowlist_is_a_contract_failure() -> None:
    scanner = _load_scanner()
    report = scanner.scan_report(
        FIXTURES / "stale_repo",
        policy_path=FIXTURES / "stale_policy.json",
    )

    assert report["existing_debt"] == []
    assert len(report["violations"]) == 1
    violation = report["violations"][0]
    assert violation["violation_type"] == scanner.STALE_ALLOWLIST
    assert violation["path"] == "edge_client/removed_dependency.py"
    assert "remove the entry" in violation["message"]
