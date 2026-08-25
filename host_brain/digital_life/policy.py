"""把受治理 occurrence 转换为 Life payload 的纯生产策略。

策略层只读取快照与调度输入并派生确定性命令负载；它不直接写库、不发送网关
消息。实际状态迁移由 Life reducer/CAS 执行，投递则由后续 outbox 负责。
"""

from __future__ import annotations

from host_brain.core.runtime_helpers import _copy_mapping as _mapping

from collections.abc import Mapping
from typing import Any

from natsume_contracts.life import (
    LifeDeliveryAuthorization,
    LifeSnapshot,
    RelationalAftereffectV1,
    digest_json,
)
from host_brain.digital_life.companion_proactive import (
    PROACTIVE_SILENCE_GRACE_MS,
    decide_companion_proactive,
)
from host_brain.runtime.scene_consumer_projection import (
    SceneConsumerProjectionError,
    project_scene_decision_v2,
)


PROACTIVE_IDLE_THRESHOLD_MS = 4 * 60 * 60 * 1_000
PROACTIVE_COOLDOWN_MS = 4 * 60 * 60 * 1_000
REACH_OUT_CALIBRATION_BASELINE = 0.6


def resolve_reach_out_calibration(state: Mapping[str, Any]) -> float:
    """从 canonical Life 快照读取持久化主动联系校准值，缺失时使用固定基线。"""

    calibration = _mapping(_mapping(state).get("reach_out_calibration"))
    if "value" not in calibration:
        return REACH_OUT_CALIBRATION_BASELINE
    return _clamp(calibration.get("value"), REACH_OUT_CALIBRATION_BASELINE)


def derive_inner_weather(
    affect: Mapping[str, Any],
    inner_world: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """为耦合的 affect/weather 投影派生纯 canonical 策略。

    输出是给下游展示与节奏使用的有界摘要，不会反向写入自我情感；这样“天气”
    不能成为修改情绪或触发主动联系的隐式 authority。
    """

    affect_state = _mapping(affect)
    inner_state = _mapping(inner_world)
    numeric = _mapping(inner_state.get("numeric_state"))
    fatigue = _clamp(affect_state.get("fatigue"))
    loneliness = _clamp(affect_state.get("loneliness"))
    energy = _clamp(affect_state.get("energy"), 0.6)
    agitation = _clamp(
        _float(affect_state.get("irritation")) * 0.6
        + _float(affect_state.get("tension")) * 0.6
    )
    low_energy = _clamp(1.0 - energy)
    anticipation = _clamp(
        numeric.get("anticipation_value") or inner_state.get("anticipation")
    )
    reply_brevity_bias = _clamp(max(fatigue, low_energy * 0.8, agitation * 0.7))
    memory_distortion_bias = _clamp(
        fatigue * 0.4 + loneliness * 0.3 + agitation * 0.3
    )
    reach_out_bias = _clamp(
        loneliness * 0.7 + anticipation * 0.3 - agitation * 0.2 - fatigue * 0.15
    )
    expressiveness_ceiling = _clamp(1.0 - reply_brevity_bias * 0.6)
    withdrawn = bool(reply_brevity_bias >= 0.5 and reach_out_bias < 0.5)
    if fatigue >= 0.6 or low_energy >= 0.6:
        label = "低气压·乏力"
    elif agitation >= 0.5:
        label = "闷·烦躁"
    elif loneliness >= 0.55:
        label = "空·想找人"
    elif reach_out_bias >= 0.5:
        label = "微亮·想靠近"
    else:
        label = "平稳"
    return {
        "label": label,
        "fatigue": round(fatigue, 3),
        "loneliness": round(loneliness, 3),
        "agitation": round(agitation, 3),
        "energy": round(energy, 3),
        "reply_brevity_bias": round(reply_brevity_bias, 3),
        "memory_distortion_bias": round(memory_distortion_bias, 3),
        "reach_out_bias": round(reach_out_bias, 3),
        "expressiveness_ceiling": round(expressiveness_ceiling, 3),
        "withdrawn": withdrawn,
    }


def build_life_tick_payload(
    snapshot: LifeSnapshot,
    *,
    scheduled_at_ms: int,
    occurrence_payload: Mapping[str, Any] | None = None,
    task_id: str = "life_tick",
    definition_revision: int = 1,
) -> dict[str, Any]:
    """构造确定性的 tick patch/intents，不做 I/O 或额外墙钟读取。

    ``scheduled_at_ms`` 是唯一时间基准，保证调度重放产生相同 payload。先关闭
    已到宽限期的主动联系 episode，再考虑新消息，避免沉默恢复被误判为连发催促。
    """

    now_value = max(0, int(scheduled_at_ms))
    state = dict(snapshot.state)
    clock = _mapping(state.get("clock"))
    last_integrated = max(0, int(clock.get("last_integrated_ms") or 0))
    last_user_seen = max(0, int(clock.get("last_user_seen_ms") or 0))
    elapsed_ms = max(0, now_value - last_integrated) if last_integrated else 0
    idle_ms = max(0, now_value - last_user_seen) if last_user_seen else 0
    state_patch: dict[str, Any] = {
        "clock": {
            "last_integrated_ms": now_value,
            "idle_minutes": idle_ms // 60_000,
        },
        "runtime": {
            "alive_status": "alive",
            "last_tick_ms": now_value,
            "tick_source": "scheduler_clock",
        },
    }
    payload: dict[str, Any] = {
        "scheduled_at_ms": now_value,
        "source": "scheduler_clock",
        "task_id": str(task_id or "life_tick"),
        "definition_revision": max(1, int(definition_revision)),
        "occurrence_payload": dict(occurrence_payload or {}),
        "state_patch": state_patch,
        "projection_intents": [
            {
                "target": "life.tick",
                "payload": {
                    "scheduled_at_ms": now_value,
                    "elapsed_ms": elapsed_ms,
                    "idle_ms": idle_ms,
                    "state_version_before": snapshot.version,
                },
            }
        ],
        "organ_intents": [
            {
                "target": "periodic_tick",
                "payload": {
                    "scheduled_at_ms": now_value,
                    "elapsed_ms": elapsed_ms,
                },
            }
        ],
        "deliveries": [],
    }
    silence_aftereffect = _due_companion_proactive_silence_aftereffect(
        state,
        now_ms=now_value,
    )
    if silence_aftereffect is not None:
        # Close only the stale proactive episode in this tick.  A new message
        # must wait for the following canonical tick and its persisted cadence
        # backoff; we never turn silence into a burst of follow-up contact.
        payload["companion_proactive_silence_aftereffect"] = (
            silence_aftereffect.as_dict()
        )
        return payload
    timezone_name = str(
        dict(occurrence_payload or {}).get("timezone") or "Asia/Shanghai"
    ).strip()
    proactive = None
    scene_projection = _proactive_scene_projection(occurrence_payload)
    if scene_projection is not None:
        scene_kind = str(scene_projection.decision.scene_id or "").removeprefix(
            "scene."
        ).replace("-", "_")
        scene_allowed, _scene_reason = scene_projection.evaluate_proactive(
            scene_kind or "proactive_talk"
        )
        if scene_allowed:
            proactive = _proactive_delivery(
                state,
                now_ms=now_value,
                idle_ms=idle_ms,
                timezone_name=timezone_name,
                scene_identity=scene_projection.identity.to_dict(),
            )
    authorization = _delivery_authorization(occurrence_payload)
    if proactive is not None and authorization is not None:
        proactive["authorization"] = authorization.as_dict()
        preview_context = dict(occurrence_payload or {}).get(
            "_action_preview_context"
        )
        if isinstance(preview_context, Mapping):
            proactive["payload"]["_action_preview_context"] = dict(
                preview_context
            )
        payload["deliveries"] = [proactive]
        decision_record = _mapping(
            _mapping(_mapping(proactive.get("payload")).get("raw_extra")).get(
                "companion_proactive_decision"
            )
        )
        if not decision_record:
            raise ValueError("canonical proactive delivery lacks reducer decision record")
        # The reducer, rather than a generic state patch, is the single writer
        # for cadence/proactive state.  That makes outbox intent and bounded
        # lifecycle head atomic under the Life command CAS transaction.
        payload["companion_proactive_decision"] = decision_record
    return payload


def _delivery_authorization(
    occurrence_payload: Mapping[str, Any] | None,
) -> LifeDeliveryAuthorization | None:
    """只接受可解析的显式投递授权；缺失或无效授权都不生成主动投递。"""
    context = dict(occurrence_payload or {})
    raw = context.get("delivery_authorization")
    if not isinstance(raw, Mapping):
        return None
    try:
        return LifeDeliveryAuthorization.from_mapping(raw)
    except ValueError:
        return None


def _proactive_scene_projection(
    occurrence_payload: Mapping[str, Any] | None,
) -> Any | None:
    """Verify the exact Scheduler-admitted Scene or fail closed.

    ``scene_awareness`` and any caller-supplied scene label are deliberately
    ignored.  The only accepted source is the immutable SceneDecisionV2 plus
    its exact identity copied from the Scheduler WorkOrder.
    """

    context = dict(occurrence_payload or {})
    try:
        return project_scene_decision_v2(
            context.get("scene_decision_v2"),
            expected_identity=context.get("scene_identity_v2"),
        )
    except SceneConsumerProjectionError:
        return None


def _proactive_delivery(
    state: Mapping[str, Any],
    *,
    now_ms: int,
    idle_ms: int,
    timezone_name: str,
    scene_identity: Mapping[str, Any],
) -> dict[str, Any] | None:
    """在空闲阈值、冷却与关系决策都允许时生成一条 outbox 投递意图。

    返回对象仍不是网络发送；关系上下文只给 Host 成功协调器使用，传输层必须在
    交给 Gateway 前移除，避免把内部关系状态泄露到外部通道。
    """
    proactive = _mapping(state.get("proactive"))
    if idle_ms < PROACTIVE_IDLE_THRESHOLD_MS:
        return None
    last_attempt = max(0, int(proactive.get("last_attempt_ms") or 0))
    if last_attempt and now_ms - last_attempt < PROACTIVE_COOLDOWN_MS:
        return None
    decision = decide_companion_proactive(
        state,
        now_ms=now_ms,
        timezone_name=timezone_name,
    )
    if not decision.send_allowed:
        return None
    return {
        "destination": "proactive.gateway",
        "payload": {
            "platform": decision.platform,
            "conversation_id": decision.conversation_id,
            "content": decision.content,
            "msg_type": "text",
            # This durable context is for the Host-side delivery-success
            # reconciler.  LifeDeliveryTransport removes it before any
            # Gateway receives the outbound request.
            "_companion_relationship_context": decision.as_delivery_context(),
            "raw_extra": {
                "source_kind": "canonical_life_companion_tick",
                "motive": decision.motive,
                "decision_reason_code": decision.reason_code,
                "companion_proactive": decision.as_trace(),
                "companion_proactive_decision": decision.as_state_record(),
                "affective_turn_plan": dict(decision.affective_turn_plan or {}),
                "state_version": int(state.get("version") or 0),
                "scene_id": str(scene_identity.get("scene_id") or ""),
                "scene_revision": int(scene_identity.get("revision") or 0),
                "scene_decision_digest": str(
                    scene_identity.get("decision_digest") or ""
                ),
            },
        },
    }


def _due_companion_proactive_silence_aftereffect(
    state: Mapping[str, Any],
    *,
    now_ms: int,
) -> RelationalAftereffectV1 | None:
    """将一条过期主动联系 episode 作为仅影响节奏的 ``SILENCE`` 结果返回。

    它有意从已投递的关系 episode 推导，而非缺失网络回执或模糊活跃分数；不含
    用户文本，也不能改变关系信任或联系同意。稳定身份使每条 episode 只会被
    闭合一次。
    """

    relationship = _mapping(state.get("relationship_v2"))
    candidates = [
        _mapping(item)
        for item in list(relationship.get("active_episodes") or [])
        if isinstance(item, Mapping)
        and str(_mapping(item).get("source_kind") or "").strip().upper()
        == "PROACTIVE_MESSAGE"
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            max(0, int(item.get("opened_at_ms") or 0)),
            str(item.get("episode_id") or ""),
        )
    )
    episode = candidates[0]
    opened_at_ms = max(0, int(episode.get("opened_at_ms") or 0))
    if not opened_at_ms or now_ms < opened_at_ms + PROACTIVE_SILENCE_GRACE_MS:
        return None
    workspace_id = str(relationship.get("workspace_id") or "").strip()
    subject_id = str(relationship.get("relationship_subject_id") or "").strip()
    session_id = str(episode.get("session_id") or "").strip()
    episode_id = str(episode.get("episode_id") or "").strip()
    source_receipt_ref = str(episode.get("response_receipt_ref") or "").strip()
    if not all((workspace_id, subject_id, session_id, episode_id, source_receipt_ref)):
        return None
    identity = {
        "schema": "companion-proactive-silence.v1",
        "workspace_id": workspace_id,
        "relationship_subject_id": subject_id,
        "episode_id": episode_id,
        "source_receipt_ref": source_receipt_ref,
        "observed_at_ms": now_ms,
    }
    digest = digest_json(identity)
    silence_ref = f"scheduler-proactive-silence://sha256/{digest}"
    return RelationalAftereffectV1(
        aftereffect_id=f"aftereffect_{digest[:32]}",
        episode_id=episode_id,
        workspace_id=workspace_id,
        relationship_subject_id=subject_id,
        session_id=session_id,
        source_receipt_ref=source_receipt_ref,
        user_input_ref=silence_ref,
        outcome="SILENCE",
        helpfulness=0.0,
        boundary_respected=True,
        repair_acknowledged=False,
        observed_at_ms=now_ms,
        evidence_refs=(source_receipt_ref, silence_ref),
    )


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


__all__ = [
    "PROACTIVE_COOLDOWN_MS",
    "PROACTIVE_IDLE_THRESHOLD_MS",
    "REACH_OUT_CALIBRATION_BASELINE",
    "build_life_tick_payload",
    "derive_inner_weather",
    "resolve_reach_out_calibration",
]
