"""Canonical, non-coercive proactive-companion decisions.

The historical emotional-proactive module contains useful impulse and
disturbance modelling, but it accepts a Bond projection as one of its inputs.
That makes it unsuitable as an authority for real outbound companionship.
This adapter admits an impulse only after the canonical Life relationship
state explicitly permits proactive contact.  It supplies only bounded
canonical self-affect and relationship data to the legacy pure algorithm,
never raw conversation, sensor material, or a Bond stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from host_brain.persona.emotional_proactive import (
    EmotionalProactiveEpisode,
    build_emotional_proactive_episode,
)


_SAFE_NO_REPLY_MARKERS = ("不用回", "不必回", "不方便回", "不急着要回应")
_MANIPULATIVE_MARKERS = (
    "不回我",
    "必须回",
    "你得回",
    "只有你",
    "别丢下我",
    "离开我",
    "不理我",
    "亏欠",
)
_HOLD_REGULATION_MODES = frozenset({"GIVE_SPACE", "QUIET_PRESENCE", "DEFER"})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _unit(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(0.0, min(1.0, number))


def _signed(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    return max(-1.0, min(1.0, number))


def _integer(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class CompanionProactiveDecision:
    """One bounded decision for a scheduler-owned Life tick.

    ``content`` is returned only for the already-authorized outbox payload.
    ``as_trace`` deliberately retains its digest rather than copying text into
    an additional Life state projection.
    """

    send_allowed: bool
    reason_code: str
    platform: str = ""
    conversation_id: str = ""
    content: str = ""
    episode_id: str = ""
    motive: str = ""
    action: str = ""
    emotional_drive: float = 0.0
    disturbance_risk: float = 0.0
    self_affect_revision: int = 0
    relationship_revision: int = 0
    reason_tags: tuple[str, ...] = ()

    def as_trace(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_proactive_decision.v1",
            "send_allowed": bool(self.send_allowed),
            "reason_code": self.reason_code,
            "episode_id": self.episode_id,
            "motive": self.motive,
            "action": self.action,
            "emotional_drive": round(_unit(self.emotional_drive), 6),
            "disturbance_risk": round(_unit(self.disturbance_risk), 6),
            "self_affect_revision": _integer(self.self_affect_revision),
            "relationship_revision": _integer(self.relationship_revision),
            "reason_tags": list(self.reason_tags[:8]),
            "content_digest": (
                f"sha256:{_digest({'content': self.content})}"
                if self.content
                else ""
            ),
        }


def decide_companion_proactive(
    state: Mapping[str, Any],
    *,
    now_ms: int,
    timezone_name: str,
) -> CompanionProactiveDecision:
    """Decide whether a canonical Life aggregate may send a gentle check-in.

    This is pure and deterministic from the frozen snapshot plus scheduler
    timestamp/timezone.  It never interprets a Bond score as permission, and
    it holds rather than guessing when scope, permission, repair, or a prior
    relationship episode is unresolved.
    """

    life = _mapping(state)
    proactive = _mapping(life.get("proactive"))
    target = _mapping(proactive.get("last_target"))
    platform = str(target.get("platform") or "").strip().lower()
    conversation_id = str(target.get("conversation_id") or "").strip()
    if not platform or not conversation_id:
        return _hold("PROACTIVE_TARGET_UNAVAILABLE")

    relationship = _mapping(life.get("relationship_v2"))
    permission = str(relationship.get("proactive_permission") or "UNKNOWN").strip().upper()
    if permission != "ALLOWED":
        return _hold(
            "PROACTIVE_PERMISSION_NOT_ALLOWED",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )
    if bool(relationship.get("repair_required")):
        return _hold(
            "PROACTIVE_REPAIR_REQUIRED",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )
    if list(relationship.get("active_episodes") or []):
        return _hold(
            "PROACTIVE_RELATIONSHIP_EPISODE_OPEN",
            platform=platform,
            conversation_id=conversation_id,
            relationship=relationship,
        )

    affective = _mapping(life.get("affective"))
    mood = _mapping(affective.get("mood"))
    needs = _mapping(affective.get("needs"))
    regulation = _mapping(affective.get("last_regulation"))
    regulation_mode = str(regulation.get("mode") or "EXPRESS").strip().upper()
    if regulation_mode in _HOLD_REGULATION_MODES:
        return _hold(
            "PROACTIVE_SELF_REGULATION_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    if _unit(needs.get("rest")) >= 0.78 or _unit(mood.get("energy"), 0.62) <= 0.18:
        return _hold(
            "PROACTIVE_SELF_REST_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )

    clock = _mapping(life.get("clock"))
    local_hour = _local_hour(now_ms, timezone_name)
    episode = build_emotional_proactive_episode(
        inner_state=_canonical_inner_state(
            affective=affective,
            relationship=relationship,
            clock=clock,
        ),
        inner_weather=_canonical_inner_weather(
            affective=affective,
            relationship=relationship,
        ),
        # Never pass a Bond projection into this canonical route.
        bond_summary={},
        relationship_permission_override=_relationship_intensity(relationship),
        scene_awareness={"companion_suitable": True, "scene_tags": ()},
        now_ms=max(0, int(now_ms)),
        local_hour=local_hour,
        last_foreground_ts_ms=_integer(clock.get("last_user_seen_ms")),
        last_proactive_outbox_ts_ms=max(
            _integer(proactive.get("last_sent_ms")),
            _integer(proactive.get("last_attempt_ms")),
        ),
        # Active project titles and private inner notes are intentionally not
        # fed into a scheduler-side outbound message.
        active_projects=(),
        self_change_note="",
    )
    if not episode.send_allowed:
        return _episode_hold(
            episode,
            "PROACTIVE_IMPULSE_HELD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    content = _non_coercive_content(episode)
    if not content:
        return _episode_hold(
            episode,
            "PROACTIVE_CONTENT_SAFETY_HOLD",
            platform=platform,
            conversation_id=conversation_id,
            affective=affective,
            relationship=relationship,
        )
    return CompanionProactiveDecision(
        send_allowed=True,
        reason_code="PROACTIVE_CANONICAL_IMPULSE_READY",
        platform=platform,
        conversation_id=conversation_id,
        content=content,
        episode_id=episode.episode_id,
        motive=episode.motive,
        action=episode.chosen_action,
        emotional_drive=episode.emotional_drive,
        disturbance_risk=episode.disturbance_risk,
        self_affect_revision=_integer(affective.get("revision")),
        relationship_revision=_integer(relationship.get("revision")),
        reason_tags=tuple(str(item) for item in episode.reason_tags[:8]),
    )


def _hold(
    reason_code: str,
    *,
    platform: str = "",
    conversation_id: str = "",
    affective: Mapping[str, Any] | None = None,
    relationship: Mapping[str, Any] | None = None,
) -> CompanionProactiveDecision:
    affective_head = _mapping(affective)
    relationship_head = _mapping(relationship)
    return CompanionProactiveDecision(
        send_allowed=False,
        reason_code=reason_code,
        platform=platform,
        conversation_id=conversation_id,
        self_affect_revision=_integer(affective_head.get("revision")),
        relationship_revision=_integer(relationship_head.get("revision")),
    )


def _episode_hold(
    episode: EmotionalProactiveEpisode,
    reason_code: str,
    *,
    platform: str,
    conversation_id: str,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
) -> CompanionProactiveDecision:
    return CompanionProactiveDecision(
        send_allowed=False,
        reason_code=reason_code,
        platform=platform,
        conversation_id=conversation_id,
        episode_id=episode.episode_id,
        motive=episode.motive,
        action=episode.chosen_action,
        emotional_drive=episode.emotional_drive,
        disturbance_risk=episode.disturbance_risk,
        self_affect_revision=_integer(affective.get("revision")),
        relationship_revision=_integer(relationship.get("revision")),
        reason_tags=tuple(str(item) for item in episode.reason_tags[:8]),
    )


def _canonical_inner_state(
    *,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
    clock: Mapping[str, Any],
) -> dict[str, Any]:
    mood = _mapping(affective.get("mood"))
    needs = _mapping(affective.get("needs"))
    components = [
        _mapping(item)
        for item in list(affective.get("components") or [])
        if isinstance(item, Mapping)
    ]
    shared_joy = max(
        (
            _unit(item.get("intensity"))
            for item in components
            if str(item.get("emotion") or "").strip().upper()
            in {"SHARED_JOY", "CARE", "OPENNESS"}
        ),
        default=0.0,
    )
    return {
        "loneliness": _unit(needs.get("connection")),
        "anticipation": shared_joy,
        "continuity_score": _unit(relationship.get("attunement")),
        "drifted_valence": _signed(mood.get("valence"), 0.18),
        "drifted_arousal": _unit(mood.get("arousal"), 0.24),
        "last_interaction_ts_ms": _integer(clock.get("last_user_seen_ms")),
    }


def _canonical_inner_weather(
    *,
    affective: Mapping[str, Any],
    relationship: Mapping[str, Any],
) -> dict[str, float]:
    needs = _mapping(affective.get("needs"))
    mood = _mapping(affective.get("mood"))
    connection = _unit(needs.get("connection"))
    attunement = _unit(relationship.get("attunement"))
    energy = _unit(mood.get("energy"), 0.62)
    return {
        "reach_out_bias": round(
            _unit(connection * 0.62 + attunement * 0.25 + energy * 0.13),
            6,
        )
    }


def _relationship_intensity(relationship: Mapping[str, Any]) -> float:
    """Bound closeness intensity without treating it as contact permission."""

    return _unit(
        0.42
        + _unit(relationship.get("trust")) * 0.22
        + _unit(relationship.get("safety")) * 0.22
        + _unit(relationship.get("attunement")) * 0.14
    )


def _local_hour(now_ms: int, timezone_name: str) -> int:
    try:
        zone = ZoneInfo(str(timezone_name or "UTC"))
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return datetime.fromtimestamp(max(0, int(now_ms)) / 1_000.0, tz=timezone.utc).astimezone(zone).hour


def _non_coercive_content(episode: EmotionalProactiveEpisode) -> str:
    content = str(episode.opening_message or "").strip()[:240]
    if not content:
        return ""
    normalized = content.lower()
    if any(marker in normalized for marker in _MANIPULATIVE_MARKERS):
        return ""
    if not any(marker in content for marker in _SAFE_NO_REPLY_MARKERS):
        content = f"{content} 不方便回也没关系。"
    return content[:280]


__all__ = ["CompanionProactiveDecision", "decide_companion_proactive"]
