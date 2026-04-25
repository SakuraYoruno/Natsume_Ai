from __future__ import annotations

import json

from host_brain.bio_ops.owner_inbox import (
    BioOpsOwnerInbox,
    validate_owner_import_artifact,
    validate_owner_import_candidate,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _candidate(candidate_id: str, **overrides):
    payload = {
        "candidate_id": candidate_id,
        "task_id": f"task-{candidate_id}",
        "capability": "habit_compiler",
        "owner_organ": "social_brain_agent",
        "output_kind": "habit_candidate",
        "summary": "Reviewable habit candidate.",
        "payload": {"proposal": {"kind": "habit_candidate", "target": "candidate_queue"}},
        "evidence_refs": ["chat:pattern"],
        "artifacts": [],
        "risk_flags": [],
        "next_actions": ["Owner must review."],
        "status": "PENDING_REVIEW",
        "created_at_ms": 1,
    }
    payload.update(overrides)
    return payload


def _decision(candidate_id: str, **overrides):
    payload = {
        "candidate_id": candidate_id,
        "owner_organ": "social_brain_agent",
        "decision": "OWNER_ACCEPTED",
        "reason": "accepted in test",
        "direct_long_term_write": False,
        "write_policy": "owner_decision_artifact_only",
    }
    payload.update(overrides)
    return payload


def test_owner_inbox_scans_decisions_and_materializes_ready_candidates(tmp_path) -> None:
    source = tmp_path / "source"
    inbox_root = tmp_path / "owner_inbox"
    _write_json(source / "habit" / "ready" / "adoption_candidate.json", _candidate("ready"))
    _write_json(source / "habit" / "ready" / "adoption_decision.json", _decision("ready"))
    _write_json(source / "habit" / "pending" / "adoption_candidate.json", _candidate("pending"))
    _write_json(source / "habit" / "rejected" / "adoption_candidate.json", _candidate("rejected"))
    _write_json(source / "habit" / "rejected" / "adoption_decision.json", _decision("rejected", decision="OWNER_REJECTED"))
    _write_json(
        source / "habit" / "blocked" / "adoption_candidate.json",
        _candidate("blocked", payload={"proposal": {"target_path": ".env"}}),
    )
    _write_json(source / "habit" / "blocked" / "adoption_decision.json", _decision("blocked"))

    inbox = BioOpsOwnerInbox(source_root=source, inbox_root=inbox_root)
    scan = inbox.scan()

    assert scan["count"] == 4
    assert scan["status_counts"] == {
        "READY_FOR_OWNER_IMPORT": 1,
        "PENDING_REVIEW": 1,
        "REJECTED_BY_OWNER": 1,
        "BLOCKED": 1,
    }
    blocked = next(item for item in scan["items"] if item["candidate_id"] == "blocked")
    assert blocked["reason_code"] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"

    materialized = inbox.materialize_ready()
    assert materialized["materialized_count"] == 1
    assert materialized["skipped_count"] == 3
    artifact_path = inbox_root / "social_brain_agent" / "ready" / "owner_import_candidate.json"
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["direct_long_term_write"] is False
    assert artifact["write_policy"] == "owner_import_candidate_only"

    import_scan = inbox.scan_import_candidates()
    assert import_scan["status_counts"]["READY_FOR_ORGAN_CONSUMPTION"] == 1

    packets = inbox.materialize_intake_packets()
    assert packets["packet_count"] == 1
    packet_path = inbox_root / "social_brain_agent" / "owner_intake_packet.json"
    assert packet_path.exists()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["owner_organ"] == "social_brain_agent"
    assert packet["direct_long_term_write"] is False
    assert packet["write_policy"] == "owner_intake_packet_only"
    assert packet["import_candidates"][0]["candidate_id"] == "ready"


def test_owner_import_candidate_validation_blocks_owner_mismatch_and_missing_evidence() -> None:
    status, allowed, reason, detail = validate_owner_import_candidate(
        _candidate("owner-mismatch"),
        _decision("owner-mismatch", owner_organ="immune_agent"),
    )
    assert status == "BLOCKED"
    assert allowed is False
    assert reason == "BIO_OPS_OWNER_MISMATCH"
    assert "social_brain_agent" in str(detail)

    status, allowed, reason, _ = validate_owner_import_candidate(
        _candidate("no-evidence", evidence_refs=[]),
        _decision("no-evidence"),
    )
    assert status == "BLOCKED"
    assert allowed is False
    assert reason == "BIO_OPS_EVIDENCE_REQUIRED"


def test_owner_import_artifact_validation_blocks_direct_write() -> None:
    allowed, reason, detail = validate_owner_import_artifact(
        {
            "candidate_id": "direct",
            "owner_organ": "social_brain_agent",
            "candidate": _candidate("direct"),
            "decision": _decision("direct"),
            "direct_long_term_write": True,
            "write_policy": "owner_import_candidate_only",
        }
    )

    assert allowed is False
    assert reason == "BIO_OPS_DIRECT_WRITE_FORBIDDEN"
    assert "durable state" in str(detail)
