from __future__ import annotations

from datetime import datetime, timezone

from natsume_contracts.life import LifeSnapshot, digest_json
from host_brain.digital_life.policy import (
    PROACTIVE_IDLE_THRESHOLD_MS,
    build_life_tick_payload,
)
from host_brain.runtime.context_contracts import ContextConstraintV1, SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2


AUTHORIZATION = {
    "request_id": "scheduler-request-1",
    "governor_grant_ref": "governor-decision://sha256/" + "a" * 64,
    "authority_kind": "scheduler_host_command",
    "authority_principal_id": "scheduler-clock",
}


def _scene_context(*, blocked: bool = False) -> dict:
    constraints = (
        (
            ContextConstraintV1(
                code="PROACTIVE_ACTION_BLOCKED",
                disposition="BLOCK",
                origin="SCENE",
                source_ref="policy://scene/v1/test-block",
                reason="test blocked scene",
            ),
        )
        if blocked
        else ()
    )
    scene = SceneDecisionV2(
        workspace_id="tenant:life-policy",
        subject_id="person-1",
        scene_id="scene.casual-chat" if not blocked else "scene.release-maintenance",
        revision=1,
        world_snapshot_digest="1" * 64,
        context_digest="2" * 64,
        guardian_snapshot_digest="3" * 64,
        constraints=constraints,
    )
    return {
        "scene_decision_v2": scene.to_dict(),
        "scene_identity_v2": SceneIdentityV2.from_decision(scene).to_dict(),
    }


def _snapshot(state: dict, *, version: int = 3) -> LifeSnapshot:
    payload = {"person_id": "person-1", "version": version, **state}
    return LifeSnapshot(
        person_id="person-1",
        version=version,
        state=payload,
        state_digest=digest_json(payload),
    )


def test_tick_is_deterministic_and_only_emits_outbox_after_known_idle_target() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    snapshot = _snapshot(
        {
            "clock": {
                "last_integrated_ms": scheduled - 60_000,
                "last_user_seen_ms": scheduled - PROACTIVE_IDLE_THRESHOLD_MS - 1,
            },
            "proactive": {
                "last_target": {"platform": "qq", "conversation_id": "chat-1"},
                "last_attempt_ms": 0,
            },
            "life_pressure": {"fatigue": 0.2},
            "affect": {"tension": 0.1},
        }
    )

    first = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            **_scene_context(),
        },
    )
    second = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            **_scene_context(),
        },
    )

    assert first == second
    assert first["state_patch"]["clock"]["last_integrated_ms"] == scheduled
    assert first["projection_intents"][0]["target"] == "life.tick"
    assert first["organ_intents"][0]["target"] == "periodic_tick"
    assert first["deliveries"] == [
        {
            "destination": "proactive.gateway",
            "authorization": AUTHORIZATION,
            "payload": {
                "platform": "qq",
                "conversation_id": "chat-1",
                "content": "早上好～我轻轻来看看你。",
                "msg_type": "text",
                "raw_extra": {
                    "source_kind": "life_tick",
                    "motive": "soft_reconnect",
                    "state_version": 3,
                    "scene_id": "scene.casual-chat",
                    "scene_revision": 1,
                    "scene_decision_digest": _scene_context()[
                        "scene_identity_v2"
                    ]["decision_digest"],
                },
            },
        }
    ]


def test_tick_without_durable_target_never_creates_external_delivery() -> None:
    snapshot = _snapshot({"clock": {"last_user_seen_ms": 1}, "proactive": {}})
    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=PROACTIVE_IDLE_THRESHOLD_MS + 10,
    )
    assert payload["deliveries"] == []


def test_tick_with_target_but_without_grant_commits_projection_only() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    snapshot = _snapshot(
        {
            "clock": {
                "last_user_seen_ms": scheduled - PROACTIVE_IDLE_THRESHOLD_MS - 1,
            },
            "proactive": {
                "last_target": {"platform": "qq", "conversation_id": "chat-1"},
            },
        }
    )

    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={"timezone": "Asia/Shanghai"},
    )

    assert payload["projection_intents"]
    assert payload["organ_intents"]
    assert payload["deliveries"] == []
    assert "proactive" not in payload["state_patch"]


def test_tick_with_grant_but_without_frozen_scene_fails_closed() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    snapshot = _snapshot(
        {
            "clock": {
                "last_user_seen_ms": scheduled - PROACTIVE_IDLE_THRESHOLD_MS - 1,
            },
            "proactive": {
                "last_target": {"platform": "qq", "conversation_id": "chat-1"},
            },
        }
    )

    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            "scene_awareness": {"scene_tags": ["casual_chat"]},
        },
    )

    assert payload["deliveries"] == []
    assert "proactive" not in payload["state_patch"]


def test_tick_with_blocking_frozen_scene_never_creates_delivery() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    snapshot = _snapshot(
        {
            "clock": {
                "last_user_seen_ms": scheduled - PROACTIVE_IDLE_THRESHOLD_MS - 1,
            },
            "proactive": {
                "last_target": {"platform": "qq", "conversation_id": "chat-1"},
            },
        }
    )

    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            **_scene_context(blocked=True),
        },
    )

    assert payload["deliveries"] == []
    assert "proactive" not in payload["state_patch"]
