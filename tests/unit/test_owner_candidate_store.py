from __future__ import annotations

import json

from host_brain.organ_ops.owner_candidate_store import (
    OWNER_BEHAVIOR_HINT_WRITE_POLICY,
    OWNER_CANDIDATE_REVIEW_STATUS_ACCEPTED,
    OWNER_CANDIDATE_REVIEW_STATUS_REJECTED,
    OWNER_CANDIDATE_STATUS_ALREADY_IMPORTED,
    OWNER_CANDIDATE_STATUS_BLOCKED,
    OWNER_CANDIDATE_STATUS_IMPORTED,
    OWNER_CANDIDATE_STATE_DECISION_WRITE_POLICY,
    OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
    OwnerCandidateStateStore,
    validate_owner_queue_item_for_durable_import,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _queue_item(**overrides):
    payload = {
        "candidate_id": "candidate-1",
        "owner_organ": "social_brain_agent",
        "channel_id": "social_brain_candidate_queue",
        "capability": "habit_compiler",
        "output_kind": "habit_candidate",
        "summary": "Reviewable habit candidate.",
        "source_packet_path": "owner_inbox/social_brain_agent/owner_intake_packet.json",
        "source_import_candidate_path": "owner_inbox/social_brain_agent/candidate-1/owner_import_candidate.json",
        "status": "QUEUED_FOR_OWNER",
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "write_policy": "owner_queue_candidate_only",
        "created_at_ms": 1,
    }
    payload.update(overrides)
    return payload


def test_owner_candidate_store_imports_queue_item_as_owner_state_candidate(tmp_path) -> None:
    queue_root = tmp_path / "owner_queues"
    state_root = tmp_path / "owner_state"
    queue_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    _write_json(queue_path, _queue_item())

    store = OwnerCandidateStateStore(queue_root=queue_root, state_root=state_root)
    receipt = store.import_queue_item(queue_path)

    state_path = state_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "owner_candidate_state.json"
    assert receipt["ok"] is True
    assert receipt["status"] == OWNER_CANDIDATE_STATUS_IMPORTED
    assert receipt["owner_candidate_state_write_performed"] is True
    assert receipt["direct_long_term_write"] is False
    assert receipt["durable_write_performed"] is False
    assert state_path.exists()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == OWNER_CANDIDATE_STATUS_IMPORTED
    assert state["review_status"] == "PENDING_OWNER_STATE_REVIEW"
    assert state["state_owner"] == "social_brain_agent"
    assert state["write_policy"] == OWNER_DURABLE_CANDIDATE_WRITE_POLICY
    assert state["direct_long_term_write"] is False
    assert state["durable_memory_write_performed"] is False

    scan = store.scan_state()
    assert scan["count"] == 1
    assert scan["status_counts"][OWNER_CANDIDATE_STATUS_IMPORTED] == 1


def test_owner_candidate_store_blocks_direct_or_stale_write_claims() -> None:
    allowed, reason, _ = validate_owner_queue_item_for_durable_import(_queue_item(direct_long_term_write=True))
    assert allowed is False
    assert reason == "BIO_OPS_DIRECT_WRITE_FORBIDDEN"

    allowed, reason, _ = validate_owner_queue_item_for_durable_import(_queue_item(durable_write_performed=True))
    assert allowed is False
    assert reason == "BIO_OPS_DURABLE_WRITE_ALREADY_PERFORMED"

    allowed, reason, detail = validate_owner_queue_item_for_durable_import(_queue_item(write_policy="legacy"))
    assert allowed is False
    assert reason == "OWNER_QUEUE_POLICY_INVALID"
    assert detail == "legacy"


def test_owner_candidate_store_blocks_channel_and_sensitive_source(tmp_path) -> None:
    allowed, reason, detail = validate_owner_queue_item_for_durable_import(_queue_item(channel_id="wrong"))
    assert allowed is False
    assert reason == "BIO_OPS_OWNER_CHANNEL_MISMATCH"
    assert "social_brain_candidate_queue" in str(detail)

    queue_root = tmp_path / "owner_queues"
    queue_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    _write_json(queue_path, _queue_item(source_import_candidate_path=".env"))

    receipt = OwnerCandidateStateStore(queue_root=queue_root, state_root=tmp_path / "state").import_all()

    assert receipt["imported_count"] == 0
    assert receipt["blocked_count"] == 1
    assert receipt["blocked"][0]["status"] == OWNER_CANDIDATE_STATUS_BLOCKED
    assert receipt["blocked"][0]["reason_code"] == "BIO_OPS_SENSITIVE_TARGET_BLOCKED"


def test_owner_candidate_store_is_idempotent_and_dry_run_safe(tmp_path) -> None:
    queue_root = tmp_path / "owner_queues"
    state_root = tmp_path / "owner_state"
    queue_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    _write_json(queue_path, _queue_item())

    dry_run = OwnerCandidateStateStore(queue_root=queue_root, state_root=state_root).import_all(dry_run=True)
    assert dry_run["imported_count"] == 1
    assert dry_run["owner_candidate_state_write_performed"] is False
    assert not list(state_root.rglob("owner_candidate_state.json"))

    store = OwnerCandidateStateStore(queue_root=queue_root, state_root=state_root)
    first = store.import_all()
    second = store.import_all()

    assert first["imported_count"] == 1
    assert first["already_imported_count"] == 0
    assert second["imported_count"] == 0
    assert second["already_imported_count"] == 1
    assert second["already_imported"][0]["status"] == OWNER_CANDIDATE_STATUS_ALREADY_IMPORTED


def test_owner_candidate_store_accepts_state_into_runtime_hint(tmp_path) -> None:
    queue_root = tmp_path / "owner_queues"
    state_root = tmp_path / "owner_state"
    queue_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    _write_json(queue_path, _queue_item())

    store = OwnerCandidateStateStore(queue_root=queue_root, state_root=state_root)
    store.import_all()
    review = store.review_candidate_state(
        "candidate-1",
        owner_organ="social_brain_agent",
        decision="OWNER_ACCEPTED",
        operator="pytest",
        reason="accept runtime hint",
    )

    hint_path = state_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "owner_behavior_hint.json"
    decision_path = state_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "owner_candidate_decision.json"
    state_path = state_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "owner_candidate_state.json"
    assert review["ok"] is True
    assert review["status"] == OWNER_CANDIDATE_REVIEW_STATUS_ACCEPTED
    assert review["owner_runtime_hint_write_performed"] is True
    assert review["direct_long_term_write"] is False
    assert review["durable_memory_write_performed"] is False
    assert hint_path.exists()
    assert decision_path.exists()

    hint = json.loads(hint_path.read_text(encoding="utf-8"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert hint["write_policy"] == OWNER_BEHAVIOR_HINT_WRITE_POLICY
    assert hint["status"] == "ACTIVE"
    assert hint["guidance_type"] == "habit_hint"
    assert hint["direct_long_term_write"] is False
    assert decision["write_policy"] == OWNER_CANDIDATE_STATE_DECISION_WRITE_POLICY
    assert state["review_status"] == OWNER_CANDIDATE_REVIEW_STATUS_ACCEPTED

    hints = store.scan_runtime_hints()
    assert hints["count"] == 1
    assert hints["status_counts"]["ACTIVE"] == 1


def test_owner_candidate_store_rejects_state_without_active_hint(tmp_path) -> None:
    queue_root = tmp_path / "owner_queues"
    state_root = tmp_path / "owner_state"
    queue_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    _write_json(queue_path, _queue_item())

    store = OwnerCandidateStateStore(queue_root=queue_root, state_root=state_root)
    store.import_all()
    review = store.review_candidate_state("candidate-1", owner_organ="social_brain_agent", decision="OWNER_REJECTED")

    assert review["ok"] is True
    assert review["status"] == OWNER_CANDIDATE_REVIEW_STATUS_REJECTED
    assert review["owner_runtime_hint_write_performed"] is False
    assert store.scan_runtime_hints()["count"] == 0
