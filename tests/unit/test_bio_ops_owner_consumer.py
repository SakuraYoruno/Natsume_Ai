from __future__ import annotations

import json

from host_brain.bio_ops.owner_consumer import (
    OWNER_QUEUE_STATUS_BLOCKED,
    OWNER_QUEUE_STATUS_QUEUED,
    OWNER_QUEUE_WRITE_POLICY,
    BioOpsOwnerIntakeConsumer,
    validate_owner_intake_packet,
)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _packet(**overrides):
    payload = {
        "packet_id": "owner_intake_social_brain_agent",
        "owner_organ": "social_brain_agent",
        "item_count": 1,
        "direct_long_term_write": False,
        "write_policy": "owner_intake_packet_only",
        "import_candidates": [
            {
                "candidate_id": "candidate-1",
                "capability": "habit_compiler",
                "output_kind": "habit_candidate",
                "summary": "Reviewable habit candidate.",
                "path": "owner_inbox/social_brain_agent/candidate-1/owner_import_candidate.json",
                "owner_channel": {
                    "owner_organ": "social_brain_agent",
                    "output_kind": "habit_candidate",
                    "channel_id": "social_brain_candidate_queue",
                    "state_owner": "social_brain_agent",
                    "direct_long_term_write": False,
                    "write_policy": "owner_import_candidate_only",
                },
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_owner_intake_consumer_queues_artifact_only_items(tmp_path) -> None:
    inbox_root = tmp_path / "owner_inbox"
    queue_root = tmp_path / "owner_queues"
    packet_path = inbox_root / "social_brain_agent" / "owner_intake_packet.json"
    _write_json(packet_path, _packet())

    receipt = BioOpsOwnerIntakeConsumer(inbox_root=inbox_root, queue_root=queue_root).consume_packet(packet_path)

    assert receipt["ok"] is True
    assert receipt["queued_count"] == 1
    assert receipt["direct_long_term_write"] is False
    assert receipt["durable_write_performed"] is False
    assert receipt["write_policy"] == OWNER_QUEUE_WRITE_POLICY

    item_path = queue_root / "social_brain_agent" / "social_brain_candidate_queue" / "candidate-1" / "queue_item.json"
    assert item_path.exists()
    item = json.loads(item_path.read_text(encoding="utf-8"))
    assert item["status"] == OWNER_QUEUE_STATUS_QUEUED
    assert item["direct_long_term_write"] is False
    assert item["durable_write_performed"] is False
    assert item["write_policy"] == OWNER_QUEUE_WRITE_POLICY

    scan = BioOpsOwnerIntakeConsumer(inbox_root=inbox_root, queue_root=queue_root).scan_queue()
    assert scan["count"] == 1
    assert scan["status_counts"][OWNER_QUEUE_STATUS_QUEUED] == 1
    assert scan["items"][0]["channel_id"] == "social_brain_candidate_queue"


def test_owner_intake_consumer_blocks_mismatched_owner(tmp_path) -> None:
    inbox_root = tmp_path / "owner_inbox"
    packet_path = inbox_root / "social_brain_agent" / "owner_intake_packet.json"
    _write_json(packet_path, _packet())

    receipt = BioOpsOwnerIntakeConsumer(inbox_root=inbox_root, queue_root=tmp_path / "queues").consume_packet(
        packet_path,
        owner_organ="immune_agent",
    )

    assert receipt["ok"] is False
    assert receipt["status"] == OWNER_QUEUE_STATUS_BLOCKED
    assert receipt["reason_code"] == "BIO_OPS_OWNER_MISMATCH"


def test_owner_intake_packet_validation_blocks_direct_write_and_unsupported_channel() -> None:
    allowed, reason, _ = validate_owner_intake_packet(_packet(direct_long_term_write=True))
    assert allowed is False
    assert reason == "BIO_OPS_DIRECT_WRITE_FORBIDDEN"

    packet = _packet()
    packet["import_candidates"][0]["output_kind"] = "scar_candidate"
    allowed, reason, detail = validate_owner_intake_packet(packet)
    assert allowed is False
    assert reason == "BIO_OPS_OWNER_CHANNEL_UNSUPPORTED"
    assert "scar_candidate" in str(detail)


def test_owner_intake_consumer_dry_run_does_not_write_queue_items(tmp_path) -> None:
    inbox_root = tmp_path / "owner_inbox"
    queue_root = tmp_path / "owner_queues"
    packet_path = inbox_root / "social_brain_agent" / "owner_intake_packet.json"
    _write_json(packet_path, _packet())

    receipt = BioOpsOwnerIntakeConsumer(inbox_root=inbox_root, queue_root=queue_root).consume_all(dry_run=True)

    assert receipt["queued_count"] == 1
    assert not list(queue_root.rglob("queue_item.json"))
