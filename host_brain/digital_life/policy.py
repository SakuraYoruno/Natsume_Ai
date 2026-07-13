"""Pure production policies that translate governed occurrences into Life payloads."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from natsume_contracts.life import (
    LifeDeliveryAuthorization,
    LifeSnapshot,
)


PROACTIVE_IDLE_THRESHOLD_MS = 4 * 60 * 60 * 1_000
PROACTIVE_COOLDOWN_MS = 4 * 60 * 60 * 1_000
REACH_OUT_CALIBRATION_BASELINE = 0.6


def resolve_reach_out_calibration(state: Mapping[str, Any]) -> float:
    """Read the durable proactive calibration from a canonical Life snapshot."""

    calibration = _mapping(_mapping(state).get("reach_out_calibration"))
    if "value" not in calibration:
        return REACH_OUT_CALIBRATION_BASELINE
    return _clamp(calibration.get("value"), REACH_OUT_CALIBRATION_BASELINE)


def derive_inner_weather(
    affect: Mapping[str, Any],
    inner_world: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure canonical policy for the coupled affect/weather projection."""

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
    """Build a deterministic tick patch/intents without I/O or wall-clock reads."""

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
    timezone_name = str(
        dict(occurrence_payload or {}).get("timezone") or "Asia/Shanghai"
    ).strip()
    proactive = _proactive_delivery(
        state,
        now_ms=now_value,
        idle_ms=idle_ms,
        timezone_name=timezone_name,
    )
    authorization = _delivery_authorization(occurrence_payload)
    if proactive is not None and authorization is not None:
        proactive["authorization"] = authorization.as_dict()
        payload["deliveries"] = [proactive]
        state_patch["proactive"] = {
            "last_attempt_ms": now_value,
            "last_status": "QUEUED",
            "last_reason_code": "LIFE_TICK_OUTBOX",
            "last_motive": "soft_reconnect",
        }
    return payload


def _delivery_authorization(
    occurrence_payload: Mapping[str, Any] | None,
) -> LifeDeliveryAuthorization | None:
    context = dict(occurrence_payload or {})
    raw = context.get("delivery_authorization")
    if not isinstance(raw, Mapping):
        return None
    try:
        return LifeDeliveryAuthorization.from_mapping(raw)
    except ValueError:
        return None


def _proactive_delivery(
    state: Mapping[str, Any],
    *,
    now_ms: int,
    idle_ms: int,
    timezone_name: str,
) -> dict[str, Any] | None:
    proactive = _mapping(state.get("proactive"))
    target = _mapping(proactive.get("last_target"))
    platform = str(target.get("platform") or "").strip()
    conversation_id = str(target.get("conversation_id") or "").strip()
    if not platform or not conversation_id or idle_ms < PROACTIVE_IDLE_THRESHOLD_MS:
        return None
    last_attempt = max(0, int(proactive.get("last_attempt_ms") or 0))
    if last_attempt and now_ms - last_attempt < PROACTIVE_COOLDOWN_MS:
        return None
    pressure = _mapping(state.get("life_pressure"))
    affect = _mapping(state.get("affect"))
    if float(pressure.get("fatigue") or 0.0) >= 0.8:
        return None
    if float(affect.get("tension") or 0.0) >= 0.85:
        return None
    try:
        zone = ZoneInfo(str(timezone_name or "UTC"))
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    hour = datetime.fromtimestamp(now_ms / 1_000.0, tz=timezone.utc).astimezone(zone).hour
    if 5 <= hour < 10:
        content = "早上好～我轻轻来看看你。"
    elif 22 <= hour or hour < 5:
        content = "夜深了，我来轻轻陪你一会儿。"
    else:
        content = "我刚好有点想起你，就来看看你在不在。"
    return {
        "destination": "proactive.gateway",
        "payload": {
            "platform": platform,
            "conversation_id": conversation_id,
            "content": content,
            "msg_type": "text",
            "raw_extra": {
                "source_kind": "life_tick",
                "motive": "soft_reconnect",
                "state_version": int(state.get("version") or 0),
            },
        },
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


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
