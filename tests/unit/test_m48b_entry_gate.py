from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from host_brain.release.m48b_entry_gate import (
    EXPECTED_DOMAINS,
    evaluate_m48b_entry_gate,
    load_m48b_entry_gate_packet,
)
from tests.fixtures.m48b_isolation import build_m48b_isolation_harness
from tools import run_m48b_entry_gate as runner


ROOT = Path(__file__).resolve().parents[2]
PACKET = ROOT / "docs/30_devspec/evidence/m48b/M48B_ENTRY_GATE_PACKET.json"


def _packet() -> dict[str, object]:
    return deepcopy(dict(load_m48b_entry_gate_packet(PACKET)))


def test_registered_packet_is_blocked_only_on_master_approval() -> None:
    result = evaluate_m48b_entry_gate(root=ROOT, packet=_packet())

    assert result.status == "BLOCKED"
    assert result.clause_status == {
        "1": "PASS",
        "2": "BLOCKED",
        "3": "PASS",
        "4": "PASS",
        "5": "PASS",
    }
    assert [issue.reason_code for issue in result.issues] == [
        "M48B_MASTER_APPROVAL_PENDING"
    ]
    assert result.checked_isolation_domains == 5
    assert result.checked_p0_slices == 9


def test_external_master_approval_is_required_to_open_gate() -> None:
    packet = _packet()
    packet["master_approval"] = {
        "required": True,
        "status": "APPROVED",
        "approver_role": "MASTER",
        "approval_ref": "evidence://master/m48b-entry-gate/test",
        "scope": "test",
    }

    result = evaluate_m48b_entry_gate(root=ROOT, packet=packet)

    assert result.status == "OPEN"
    assert result.issues == ()
    assert set(result.clause_status.values()) == {"PASS"}


def test_missing_p0_control_fails_closed_even_with_approval() -> None:
    packet = _packet()
    packet["master_approval"] = {
        "required": True,
        "status": "APPROVED",
        "approver_role": "MASTER",
        "approval_ref": "evidence://master/m48b-entry-gate/test",
    }
    packet["p0_registry"] = list(packet["p0_registry"])[1:]

    result = evaluate_m48b_entry_gate(root=ROOT, packet=packet)

    assert result.status == "BLOCKED"
    assert result.clause_status["5"] == "FAILED"
    assert "M48B_P0_TASK_SET_INVALID" in {
        issue.reason_code for issue in result.issues
    }


def test_all_isolation_harnesses_are_ephemeral_and_no_effect(
    tmp_path: Path,
) -> None:
    secrets: set[str] = set()
    for domain in EXPECTED_DOMAINS:
        harness = build_m48b_isolation_harness(
            domain=domain,
            state_root=tmp_path / domain,
        )
        receipt = harness.sink.emit("negative-test", {"domain": domain})
        harness.assert_safe()
        secrets.add(harness.test_secret)
        assert receipt["executed"] is False
        assert harness.state_root.is_relative_to(tmp_path)

    assert len(secrets) == len(EXPECTED_DOMAINS)


def test_runner_writes_deterministic_blocked_result(tmp_path: Path) -> None:
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    first_exit, first = runner.run_entry_gate(
        root=ROOT,
        packet=PACKET,
        output=first_output,
        pretty=True,
    )
    second_exit, second = runner.run_entry_gate(
        root=ROOT,
        packet=PACKET,
        output=second_output,
        pretty=True,
    )

    assert first_exit == second_exit == runner.EXIT_BLOCKED
    assert first == second
    assert first["status"] == "BLOCKED"
    assert first_output.read_bytes() == second_output.read_bytes()
    assert json.loads(first_output.read_text(encoding="utf-8")) == first
