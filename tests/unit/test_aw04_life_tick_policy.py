from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from natsume_contracts.life import LifeSnapshot, digest_json
from host_brain.digital_life.domain_reducer import reduce_life_domains
from host_brain.digital_life.companion_proactive import (
    PROACTIVE_SILENCE_GRACE_MS,
    decide_companion_proactive,
)
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


def _canonical_companion_state(*, permission: str = "ALLOWED") -> dict:
    return {
        "companion_proactive_target": {
            "schema_version": "companion_proactive_target.v1",
            "target_id": "companion_target_unit",
            "self_id": "natsume_ai",
            "workspace_id": "workspace::default",
            "relationship_subject_id": "person-1",
            "session_id": "session-1",
            "platform": "qq",
            "conversation_id": "chat-1",
            "source_receipt_ref": "life-event://foreground-unit",
            "registered_at_ms": 1,
        },
        "relationship_v2": {
            "schema_version": "relationship_state.v2",
            "self_id": "natsume_ai",
            "workspace_id": "workspace::default",
            "relationship_subject_id": "person-1",
            "revision": 3,
            "trust": 0.7,
            "safety": 0.8,
            "attunement": 0.75,
            "rupture_load": 0.0,
            "repair_required": False,
            "proactive_permission": permission,
            "romantic_consent": "NOT_GRANTED",
            "exclusive_consent": "NOT_GRANTED",
            "dependency_consent": "NOT_GRANTED",
            "active_episodes": [],
            "last_aftereffect": None,
        },
        "affective": {
            "schema_version": "self_affect_state.v2",
            "self_id": "natsume_ai",
            "relationship_subject_id": "person-1",
            "revision": 4,
            "components": [],
            "active_episodes": [],
            "mood": {
                "label": "calm",
                "valence": 0.2,
                "arousal": 0.2,
                "energy": 0.7,
                "updated_at_ms": 0,
            },
            "needs": {
                "connection": 0.5,
                "rest": 0.1,
                "autonomy": 0.2,
                "play": 0.2,
                "repair": 0.0,
                "safety": 0.1,
                "updated_at_ms": 0,
            },
            "last_observation": {},
            "last_appraisal": {},
            "last_regulation": {},
            "last_resolution": {},
            "last_integrated_ms": 0,
        },
    }


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
            **_canonical_companion_state(),
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
    assert len(first["deliveries"]) == 1
    delivery = first["deliveries"][0]
    assert delivery["destination"] == "proactive.gateway"
    assert delivery["authorization"] == AUTHORIZATION
    assert delivery["payload"]["platform"] == "qq"
    assert delivery["payload"]["conversation_id"] == "chat-1"
    assert "不方便回也没关系" in delivery["payload"]["content"]
    trace = delivery["payload"]["raw_extra"]["companion_proactive"]
    assert trace["send_allowed"] is True
    assert trace["relationship_revision"] == 3
    assert trace["self_affect_revision"] == 4
    assert trace["target_id"] == "companion_target_unit"
    assert trace["content_digest"].startswith("sha256:")
    assert delivery["payload"]["raw_extra"]["scene_decision_digest"] == _scene_context()[
        "scene_identity_v2"
    ]["decision_digest"]
    assert "companion_proactive" not in first["state_patch"]
    assert first["companion_proactive_decision"]["decision_id"] == trace["decision_id"]
    assert (
        delivery["payload"]["raw_extra"]["affective_turn_plan"]["plan_id"]
        == trace["affective_turn_plan_id"]
    )
    assert (
        delivery["payload"]["raw_extra"]["affective_turn_plan"]["expression_plan"][
            "synchronization_id"
        ]
        == trace["synchronization_id"]
    )


def test_tick_without_durable_target_never_creates_external_delivery() -> None:
    snapshot = _snapshot({"clock": {"last_user_seen_ms": 1}, "proactive": {}})
    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=PROACTIVE_IDLE_THRESHOLD_MS + 10,
    )
    assert payload["deliveries"] == []


def test_tick_decision_is_reducer_owned_and_commits_with_its_outbox_intent() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    snapshot = _snapshot(
        {
            "clock": {
                "last_integrated_ms": scheduled - 60_000,
                "last_user_seen_ms": scheduled - PROACTIVE_IDLE_THRESHOLD_MS - 1,
            },
            **_canonical_companion_state(),
        }
    )
    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            **_scene_context(),
        },
    )

    reduced = reduce_life_domains(
        snapshot.state,
        person_id="person-1",
        command_id="life_tick::reducer-owned-proactive",
        command_type="tick",
        payload=payload,
        created_at_ms=scheduled,
    )

    head = reduced["companion_proactive"]
    record = payload["companion_proactive_decision"]
    assert head["last_decision_id"] == record["decision_id"]
    assert head["last_plan_id"] == record["affective_turn_plan_id"]
    assert head["last_synchronization_id"] == record["synchronization_id"]
    assert head["last_status"] == "QUEUED"
    assert head["next_allowed_at_ms"] == record["next_allowed_at_ms"]
    assert reduced["proactive"]["last_reason_code"] == record["reason_code"]


def test_tick_rejects_a_proactive_plan_with_self_affect_digest_drift() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    snapshot = _snapshot(
        {
            "clock": {
                "last_integrated_ms": scheduled - 60_000,
                "last_user_seen_ms": scheduled - PROACTIVE_IDLE_THRESHOLD_MS - 1,
            },
            **_canonical_companion_state(),
        }
    )
    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            **_scene_context(),
        },
    )
    tampered = deepcopy(payload)
    tampered["deliveries"][0]["payload"]["raw_extra"]["affective_turn_plan"][
        "self_affect_digest"
    ] = "0" * 64

    with pytest.raises(ValueError, match="affective turn plan drift"):
        reduce_life_domains(
            snapshot.state,
            person_id="person-1",
            command_id="life_tick::tampered-proactive-plan",
            command_type="tick",
            payload=tampered,
            created_at_ms=scheduled,
        )


def test_unanswered_proactive_closes_after_grace_and_slows_only_future_contact() -> None:
    scheduled = int(
        datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
    )
    state = _canonical_companion_state()
    state["relationship_v2"]["active_episodes"] = [
        {
            "schema_version": "relationship_episode.v1",
            "episode_id": "proactive-episode-silence",
            "self_id": "natsume_ai",
            "workspace_id": "workspace::default",
            "relationship_subject_id": "person-1",
            "session_id": "session-1",
            "source_kind": "PROACTIVE_MESSAGE",
            "response_receipt_ref": "life-delivery://proactive-outbox-silence",
            "response_digest": "sha256:" + "a" * 64,
            "opened_at_ms": scheduled - PROACTIVE_SILENCE_GRACE_MS - 1,
            "evidence_refs": ["life-delivery://proactive-outbox-silence"],
            "affect_repair_target": None,
        }
    ]
    snapshot = _snapshot(
        {
            "clock": {
                "last_integrated_ms": scheduled - 60_000,
                "last_user_seen_ms": scheduled - 6 * 60 * 60 * 1_000,
            },
            **state,
        }
    )

    payload = build_life_tick_payload(snapshot, scheduled_at_ms=scheduled)

    assert payload["deliveries"] == []
    elapsed = payload["companion_proactive_silence_aftereffect"]
    assert elapsed["outcome"] == "SILENCE"
    assert elapsed["user_input_ref"].startswith(
        "scheduler-proactive-silence://sha256/"
    )
    reduced = reduce_life_domains(
        snapshot.state,
        person_id="person-1",
        command_id="life_tick::silent-proactive",
        command_type="tick",
        payload=payload,
        created_at_ms=scheduled,
    )

    assert reduced["relationship_v2"]["active_episodes"] == []
    assert reduced["relationship_v2"]["last_aftereffect"]["outcome"] == "SILENCE"
    assert reduced["clock"]["last_user_seen_ms"] == snapshot.state["clock"][
        "last_user_seen_ms"
    ]
    head = reduced["companion_proactive"]
    assert head["consecutive_unanswered"] == 1
    assert head["next_allowed_at_ms"] > scheduled
    decision = decide_companion_proactive(
        reduced,
        now_ms=scheduled + 1,
        timezone_name="Asia/Shanghai",
    )
    assert decision.send_allowed is False
    assert decision.reason_code == "PROACTIVE_RECENCY_BACKOFF"


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
            **_canonical_companion_state(),
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
            **_canonical_companion_state(),
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


def test_tick_requires_explicit_canonical_proactive_permission() -> None:
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
            **_canonical_companion_state(permission="UNKNOWN"),
        }
    )

    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled,
        occurrence_payload={
            "timezone": "Asia/Shanghai",
            "delivery_authorization": AUTHORIZATION,
            **_scene_context(),
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
