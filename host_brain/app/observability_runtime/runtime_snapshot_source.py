"""Pure read source for the standalone Obs runtime-snapshot projection.

The source joins one canonical Digital Life snapshot with already materialized
operator documents.  Every dependency belongs to the calling Obs application;
this module owns no cache, writer, background task, or module-level runtime
state.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from host_brain.app.observability_runtime.live_source_projection import (
    PROACTIVE_AUTONOMY_DEFAULT_KEY,
    PROACTIVE_AUTONOMY_KIND,
    PROACTIVE_AUTONOMY_MAX_AGE_MS,
    SYS_MODE_MAX_AGE_MS,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
)
from host_brain.app.observability_runtime.projection_runtime import (
    _document_value,
    _non_negative_int,
    _state_name,
)
from host_brain.observability.projection_store import QueryProjectionStore


RUNTIME_LIFE_SNAPSHOT_NOT_FOUND = "OBS_RUNTIME_LIFE_SNAPSHOT_NOT_FOUND"
RUNTIME_LIFE_SNAPSHOT_INVALID = "OBS_RUNTIME_LIFE_SNAPSHOT_INVALID"
RUNTIME_LIFE_SNAPSHOT_PARTIAL = "OBS_RUNTIME_LIFE_SNAPSHOT_PARTIAL"
RUNTIME_LIFE_SNAPSHOT_READ_FAILED = "OBS_RUNTIME_LIFE_SNAPSHOT_READ_FAILED"

RUNTIME_SYS_MODE_NOT_READY = "OBS_RUNTIME_SYS_MODE_STATUS_NOT_READY"
RUNTIME_SYS_MODE_STALE = "OBS_RUNTIME_SYS_MODE_STATUS_STALE"
RUNTIME_SYS_MODE_SOURCE_UNAVAILABLE = (
    "OBS_RUNTIME_SYS_MODE_STATUS_SOURCE_UNAVAILABLE"
)
RUNTIME_SYS_MODE_INVALID = "OBS_RUNTIME_SYS_MODE_STATUS_INVALID"
RUNTIME_SYS_MODE_READ_FAILED = "OBS_RUNTIME_SYS_MODE_STATUS_READ_FAILED"

RUNTIME_PROACTIVE_NOT_READY = "OBS_RUNTIME_PROACTIVE_AUTONOMY_NOT_READY"
RUNTIME_PROACTIVE_STALE = "OBS_RUNTIME_PROACTIVE_AUTONOMY_STALE"
RUNTIME_PROACTIVE_SOURCE_UNAVAILABLE = (
    "OBS_RUNTIME_PROACTIVE_AUTONOMY_SOURCE_UNAVAILABLE"
)
RUNTIME_PROACTIVE_INVALID = "OBS_RUNTIME_PROACTIVE_AUTONOMY_INVALID"
RUNTIME_PROACTIVE_READ_FAILED = "OBS_RUNTIME_PROACTIVE_AUTONOMY_READ_FAILED"

RUNTIME_SNAPSHOT_SCHEMA_VERSION = "Natsume.digital_life.runtime_snapshot.v2"
RUNTIME_SNAPSHOT_SOURCE_SCOPE = "canonical-life-and-operator-projections"

_HEALTHY = "HEALTHY"
_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
_REBUILDING = "REBUILDING"
_DIGEST_MISMATCH = "DIGEST_MISMATCH"
_MAX_CLOCK_SKEW_MS = 60_000
_REQUIRED_LIFE_STATE_SECTIONS = (
    "runtime",
    "autonomy",
    "affect",
    "scene",
    "intent",
)


class LifeSnapshotReadPort(Protocol):
    """Minimum durable Life query port accepted by this source."""

    def get_snapshot(self, person_id: str) -> Any: ...


class RuntimeSnapshotSourceUnavailable(RuntimeError):
    """Typed root-source failure safe to persist or return to an operator."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or RUNTIME_LIFE_SNAPSHOT_READ_FAILED)
        self.source_scope = RUNTIME_SNAPSHOT_SOURCE_SCOPE
        super().__init__(self.reason_code)

    def as_public_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.operator.projection_source_unavailable.v1",
            "available": False,
            "reason_code": self.reason_code,
            "source_scope": self.source_scope,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSnapshotSourceRead:
    """A callback-ready public payload and its greatest observed cursor."""

    payload: dict[str, Any]
    source_cursor: int


@dataclass(frozen=True, slots=True)
class _SlotRead:
    payload: dict[str, Any]
    status: str
    reason_code: str | None
    source_cursor: int
    freshness_ts_ms: int


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "as_dict"):
        dumped = value.as_dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _finite_range(
    value: Any,
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    try:
        resolved = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if resolved != resolved or resolved in (float("inf"), float("-inf")):
        return None
    return round(max(minimum, min(maximum, resolved)), 4)


def _finite_ratio(value: Any) -> float | None:
    return _finite_range(value, minimum=0.0, maximum=1.0)


def _finite_valence(value: Any) -> float | None:
    return _finite_range(value, minimum=-1.0, maximum=1.0)


def _public_token(value: Any, *, default: str, limit: int = 80) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return text[:limit]


def _read_life_snapshot(
    reader: LifeSnapshotReadPort | Callable[[str], Any],
    person_id: str,
) -> dict[str, Any]:
    try:
        get_snapshot = getattr(reader, "get_snapshot", None)
        raw = get_snapshot(person_id) if callable(get_snapshot) else reader(person_id)
    except RuntimeSnapshotSourceUnavailable:
        raise
    except Exception as exc:
        raise RuntimeSnapshotSourceUnavailable(
            RUNTIME_LIFE_SNAPSHOT_READ_FAILED
        ) from exc

    snapshot = _mapping(raw)
    state = _mapping(snapshot.get("state"))
    version = _non_negative_int(snapshot.get("version"))
    if version <= 0:
        raise RuntimeSnapshotSourceUnavailable(RUNTIME_LIFE_SNAPSHOT_NOT_FOUND)
    if not state:
        raise RuntimeSnapshotSourceUnavailable(RUNTIME_LIFE_SNAPSHOT_INVALID)
    state_version = state.get("version")
    if state_version is not None and _non_negative_int(state_version) != version:
        raise RuntimeSnapshotSourceUnavailable(RUNTIME_LIFE_SNAPSHOT_INVALID)

    resolved_person = _public_token(
        snapshot.get("person_id") or state.get("person_id") or person_id,
        default=person_id,
        limit=240,
    )
    return {
        "person_id": resolved_person,
        "version": version,
        "state": state,
        "updated_at_ms": _non_negative_int(snapshot.get("updated_at_ms")),
    }


def _projection_slot(
    store: QueryProjectionStore,
    *,
    kind: str,
    key: str,
    now_ms: int,
    max_age_ms: int,
    not_ready_reason: str,
    stale_reason: str,
    unavailable_reason: str,
    invalid_reason: str,
    read_failed_reason: str,
) -> _SlotRead:
    try:
        document = store.get(kind, key)
    except Exception:
        return _SlotRead({}, "read_failed", read_failed_reason, 0, 0)
    if document is None:
        return _SlotRead({}, "source_unavailable", not_ready_reason, 0, 0)

    cursor = _non_negative_int(_document_value(document, "source_cursor"))
    freshness = _non_negative_int(_document_value(document, "freshness_ts_ms"))
    durable_state = _state_name(_document_value(document, "degraded_state"))
    payload = _mapping(_document_value(document, "payload", {}))
    if durable_state == _SOURCE_UNAVAILABLE:
        return _SlotRead(
            {}, "source_unavailable", unavailable_reason, cursor, freshness
        )
    if durable_state == _REBUILDING:
        return _SlotRead({}, "rebuilding", not_ready_reason, cursor, freshness)
    if durable_state == _DIGEST_MISMATCH or durable_state != _HEALTHY:
        return _SlotRead({}, "invalid", invalid_reason, cursor, freshness)
    if freshness <= 0 or freshness > now_ms + _MAX_CLOCK_SKEW_MS:
        return _SlotRead({}, "invalid", invalid_reason, cursor, freshness)
    if now_ms - freshness > max(0, int(max_age_ms)):
        return _SlotRead({}, "stale", stale_reason, cursor, freshness)
    if not payload:
        return _SlotRead({}, "invalid", invalid_reason, cursor, freshness)
    return _SlotRead(payload, "live", None, cursor, freshness)


def _life_hard_state(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    state = _mapping(snapshot.get("state"))
    runtime = _mapping(state.get("runtime"))
    autonomy = _mapping(state.get("autonomy"))
    affect = _mapping(state.get("affect"))
    scene = _mapping(state.get("scene"))
    intent = _mapping(state.get("intent"))
    embodiment = _mapping(state.get("embodiment"))

    hard_state: dict[str, Any] = {
        "life_version": _non_negative_int(snapshot.get("version")),
        "life_updated_at_ms": _non_negative_int(snapshot.get("updated_at_ms")),
        "alive_status": _public_token(
            runtime.get("alive_status") or autonomy.get("alive_status"),
            default="unknown",
        ),
        "current_attention": _public_token(
            autonomy.get("current_attention"),
            default="unknown",
            limit=120,
        ),
        "scene": _public_token(scene.get("kind"), default="unknown"),
        "intent": _public_token(intent.get("kind"), default="unknown"),
        "emotion_tag": _public_token(
            embodiment.get("emotion_tag"), default="NEUTRAL"
        ).upper(),
    }
    for name in ("energy", "fatigue", "arousal", "tension"):
        value = _finite_ratio(affect.get(name))
        if value is not None:
            hard_state[name] = value
    valence = _finite_valence(affect.get("valence"))
    if valence is not None:
        hard_state["valence"] = valence
    return hard_state


def _life_state_is_complete(snapshot: Mapping[str, Any]) -> bool:
    state = _mapping(snapshot.get("state"))
    return all(
        bool(_mapping(state.get(section)))
        for section in _REQUIRED_LIFE_STATE_SECTIONS
    )


def _public_proactive_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("available") is not True:
        return {}
    readiness = _finite_ratio(payload.get("proactive_readiness"))
    if readiness is None:
        return {}
    top_intention = _mapping(payload.get("top_intention"))
    result: dict[str, Any] = {
        "available": True,
        "proactive_readiness": readiness,
        "active_project_count": len(list(payload.get("active_projects") or [])),
    }
    if top_intention:
        result["top_intention"] = {
            "project_id": _public_token(
                top_intention.get("project_id"), default="", limit=120
            ),
            "pull": _finite_ratio(top_intention.get("pull")),
        }
    return result


@dataclass(slots=True)
class RuntimeSnapshotSourceReader:
    """App-local source joining durable Life with materialized read models."""

    store: QueryProjectionStore
    life_snapshot_reader: LifeSnapshotReadPort | Callable[[str], Any]
    person_id: str = "default"
    sys_mode_max_age_ms: int = SYS_MODE_MAX_AGE_MS
    proactive_max_age_ms: int = PROACTIVE_AUTONOMY_MAX_AGE_MS
    clock_ms: Callable[[], int] = field(
        default=lambda: int(time.time() * 1000),
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.store is None or not callable(getattr(self.store, "get", None)):
            raise TypeError("store must provide get")
        if not callable(self.life_snapshot_reader) and not callable(
            getattr(self.life_snapshot_reader, "get_snapshot", None)
        ):
            raise TypeError("life_snapshot_reader must be callable or provide get_snapshot")
        if not callable(self.clock_ms):
            raise TypeError("clock_ms must be callable")
        self.person_id = _public_token(
            self.person_id, default="default", limit=240
        )
        self.sys_mode_max_age_ms = max(0, int(self.sys_mode_max_age_ms))
        self.proactive_max_age_ms = max(0, int(self.proactive_max_age_ms))

    def read(self) -> RuntimeSnapshotSourceRead:
        now = _non_negative_int(self.clock_ms())
        life = _read_life_snapshot(self.life_snapshot_reader, self.person_id)
        hard_state = _life_hard_state(life)
        life_state_complete = _life_state_is_complete(life)

        sys_mode_slot = _projection_slot(
            self.store,
            kind=SYS_MODE_STATUS_KIND,
            key=SYS_MODE_STATUS_KEY,
            now_ms=now,
            max_age_ms=self.sys_mode_max_age_ms,
            not_ready_reason=RUNTIME_SYS_MODE_NOT_READY,
            stale_reason=RUNTIME_SYS_MODE_STALE,
            unavailable_reason=RUNTIME_SYS_MODE_SOURCE_UNAVAILABLE,
            invalid_reason=RUNTIME_SYS_MODE_INVALID,
            read_failed_reason=RUNTIME_SYS_MODE_READ_FAILED,
        )
        proactive_slot = _projection_slot(
            self.store,
            kind=PROACTIVE_AUTONOMY_KIND,
            key=PROACTIVE_AUTONOMY_DEFAULT_KEY,
            now_ms=now,
            max_age_ms=self.proactive_max_age_ms,
            not_ready_reason=RUNTIME_PROACTIVE_NOT_READY,
            stale_reason=RUNTIME_PROACTIVE_STALE,
            unavailable_reason=RUNTIME_PROACTIVE_SOURCE_UNAVAILABLE,
            invalid_reason=RUNTIME_PROACTIVE_INVALID,
            read_failed_reason=RUNTIME_PROACTIVE_READ_FAILED,
        )

        if sys_mode_slot.status == "live":
            sys_mode = _public_token(
                sys_mode_slot.payload.get("sys_mode"), default="unknown"
            ).lower()
            if sys_mode == "unknown":
                sys_mode_slot = _SlotRead(
                    {},
                    "invalid",
                    RUNTIME_SYS_MODE_INVALID,
                    sys_mode_slot.source_cursor,
                    sys_mode_slot.freshness_ts_ms,
                )
        else:
            sys_mode = "unknown"

        initiative_decision = (
            _public_proactive_state(proactive_slot.payload)
            if proactive_slot.status == "live"
            else {}
        )
        if proactive_slot.status == "live" and not initiative_decision:
            proactive_slot = _SlotRead(
                {},
                "invalid",
                RUNTIME_PROACTIVE_INVALID,
                proactive_slot.source_cursor,
                proactive_slot.freshness_ts_ms,
            )

        degraded_reasons = [
            reason
            for reason in (
                None if life_state_complete else RUNTIME_LIFE_SNAPSHOT_PARTIAL,
                sys_mode_slot.reason_code,
                proactive_slot.reason_code,
            )
            if reason
        ]
        status = "PARTIAL_DEGRADED" if degraded_reasons else "HEALTHY"
        life_cursor = max(
            _non_negative_int(life.get("updated_at_ms")),
            _non_negative_int(life.get("version")),
        )
        source_cursor = max(
            life_cursor,
            sys_mode_slot.source_cursor,
            proactive_slot.source_cursor,
        )
        slot_status = {
            "persona_user_id": "live",
            # Request-scoped topics never participate in this durable source.
            "current_topic": "not_applicable",
            "hard_state": "live" if life_state_complete else "partial",
            "runtime_summary": "partial" if degraded_reasons else "live",
            "sys_mode": sys_mode_slot.status,
            "sys_mode_status": sys_mode_slot.status,
            "initiative_decision": proactive_slot.status,
        }
        payload = {
            "schema_version": RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            "persona_user_id": str(life["person_id"]),
            "current_topic": "",
            "sys_mode": sys_mode,
            "sys_mode_status": (
                dict(sys_mode_slot.payload)
                if sys_mode_slot.status == "live"
                else {}
            ),
            "hard_state": hard_state,
            "initiative_decision": initiative_decision,
            "runtime_summary": {
                "status": status,
                "source": "durable_life_and_materialized_operator_projections",
                "life_version": _non_negative_int(life.get("version")),
                "life_updated_at_ms": _non_negative_int(
                    life.get("updated_at_ms")
                ),
                "available_slot_count": sum(
                    1 for value in slot_status.values() if value == "live"
                ),
                "slot_count": len(slot_status),
            },
            "slot_status": slot_status,
            "degraded": bool(degraded_reasons),
            "degraded_reasons": degraded_reasons,
            "timestamp": now,
        }
        return RuntimeSnapshotSourceRead(
            payload=payload,
            source_cursor=source_cursor,
        )

    def __call__(self) -> tuple[dict[str, Any], int]:
        """Projection callback adapter with no hidden dependency lookup."""

        result = self.read()
        return result.payload, result.source_cursor


__all__ = [
    "RUNTIME_LIFE_SNAPSHOT_INVALID",
    "RUNTIME_LIFE_SNAPSHOT_NOT_FOUND",
    "RUNTIME_LIFE_SNAPSHOT_PARTIAL",
    "RUNTIME_LIFE_SNAPSHOT_READ_FAILED",
    "RUNTIME_PROACTIVE_INVALID",
    "RUNTIME_PROACTIVE_NOT_READY",
    "RUNTIME_PROACTIVE_READ_FAILED",
    "RUNTIME_PROACTIVE_SOURCE_UNAVAILABLE",
    "RUNTIME_PROACTIVE_STALE",
    "RUNTIME_SNAPSHOT_SCHEMA_VERSION",
    "RUNTIME_SYS_MODE_INVALID",
    "RUNTIME_SYS_MODE_NOT_READY",
    "RUNTIME_SYS_MODE_READ_FAILED",
    "RUNTIME_SYS_MODE_SOURCE_UNAVAILABLE",
    "RUNTIME_SYS_MODE_STALE",
    "RuntimeSnapshotSourceRead",
    "RuntimeSnapshotSourceReader",
    "RuntimeSnapshotSourceUnavailable",
]
