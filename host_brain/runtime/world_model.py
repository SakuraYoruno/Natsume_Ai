from __future__ import annotations

import inspect
import time
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from host_brain.runtime.environment_state import build_environment_snapshot
from host_brain.runtime.life_continuity import DEFAULT_PERSON_ID
from host_brain.persistence.world_model_query_repository import (
    WorldModelPersistenceSnapshot,
    WorldModelQueryRepository,
    WorkEnvironmentEventRecord,
)

WORLD_SNAPSHOT_SCHEMA_VERSION = "Natsume.world_projection.v1"
WORLD_PROJECTION_DISABLED = "HOST_LIFE_PROJECTION_DISABLED"
WORLD_PROJECTION_SOURCE_DEGRADED = "WORLD_PROJECTION_SOURCE_DEGRADED"

SHORT_INFERENCE_TTL_MS = 10 * 60 * 1000
ENVIRONMENT_TTL_MS = 5 * 60 * 1000
HEALTH_TTL_MS = 2 * 60 * 1000
WORK_ACTIVE_TTL_MS = 4 * 60 * 60 * 1000
WORK_HISTORY_TTL_MS = 24 * 60 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _map(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _confidence(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 3)


def _expired(now_ms: int, expires_at_ms: int) -> bool:
    return int(expires_at_ms or 0) <= int(now_ms)


def _source_ref(source: str, key: str, ref: str = "") -> str:
    safe_key = str(key or "unknown").replace(" ", "_")
    return str(ref or f"{source}://{safe_key}")


def _fact(
    *,
    key: str,
    value: Any,
    source: str,
    source_ref: str = "",
    confidence: float,
    observed_at_ms: int,
    ttl_ms: int,
    epistemic_type: str,
    now_ms: int,
    conflict_state: str = "NONE",
    conflict_detail: str = "",
    reason_codes: Sequence[str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observed = int(observed_at_ms or now_ms)
    ttl = max(1, int(ttl_ms or SHORT_INFERENCE_TTL_MS))
    expires = observed + ttl
    resolved_conflict = str(conflict_state or "NONE").upper()
    if _expired(now_ms, expires):
        resolved_conflict = "EXPIRED" if resolved_conflict == "NONE" else f"{resolved_conflict}_EXPIRED"
    return {
        "key": str(key),
        "value": value,
        "source": str(source),
        "source_ref": _source_ref(str(source), str(key), source_ref),
        "confidence": _confidence(confidence),
        "observed_at_ms": observed,
        "expires_at_ms": expires,
        "ttl_ms": ttl,
        "epistemic_type": str(epistemic_type),
        "conflict_state": resolved_conflict,
        "conflict_detail": _text(conflict_detail, 500),
        "reason_codes": [str(item) for item in list(reason_codes or []) if str(item).strip()],
        "metadata": dict(metadata or {}),
    }


def _state_observed_at(state: Mapping[str, Any], now_ms: int) -> int:
    runtime = _map(state.get("runtime"))
    clock = _map(state.get("clock"))
    return int(runtime.get("last_integrated_ms") or clock.get("last_tick_ms") or now_ms)


def _life_state_facts(
    *,
    state: Mapping[str, Any] | None,
    person_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    if state is None:
        return [
            _fact(
                key="life.state_available",
                value=False,
                source="digital_life_state",
                source_ref=f"digital_life_state://{person_id}",
                confidence=0.0,
                observed_at_ms=now_ms,
                ttl_ms=HEALTH_TTL_MS,
                epistemic_type="degraded",
                now_ms=now_ms,
                conflict_state="SOURCE_UNAVAILABLE",
                reason_codes=[WORLD_PROJECTION_SOURCE_DEGRADED],
            )
        ]
    observed = _state_observed_at(state, now_ms)
    runtime = _map(state.get("runtime"))
    clock = _map(state.get("clock"))
    affect = _map(state.get("affect"))
    needs = _map(state.get("needs"))
    work = _map(state.get("work"))
    action = _map(state.get("life_action"))
    source_ref = f"digital_life_state://{person_id}@v{int(state.get('version') or 1)}"
    return [
        _fact(
            key="life.version",
            value=int(state.get("version") or 1),
            source="digital_life_state",
            source_ref=source_ref,
            confidence=1.0,
            observed_at_ms=observed,
            ttl_ms=SHORT_INFERENCE_TTL_MS,
            epistemic_type="observed",
            now_ms=now_ms,
        ),
        _fact(
            key="life.alive_status",
            value=str(runtime.get("alive_status") or "unknown"),
            source="digital_life_state",
            source_ref=source_ref,
            confidence=0.9,
            observed_at_ms=observed,
            ttl_ms=HEALTH_TTL_MS,
            epistemic_type="derived",
            now_ms=now_ms,
        ),
        _fact(
            key="life.day_phase",
            value=str(clock.get("day_phase") or ""),
            source="digital_life_state",
            source_ref=source_ref,
            confidence=0.82,
            observed_at_ms=observed,
            ttl_ms=ENVIRONMENT_TTL_MS,
            epistemic_type="derived",
            now_ms=now_ms,
        ),
        _fact(
            key="life.affect.mood",
            value=str(affect.get("mood") or ""),
            source="digital_life_state",
            source_ref=source_ref,
            confidence=0.68,
            observed_at_ms=observed,
            ttl_ms=SHORT_INFERENCE_TTL_MS,
            epistemic_type="inferred",
            now_ms=now_ms,
            reason_codes=["LOW_CONFIDENCE_INFERENCE"],
        ),
        _fact(
            key="life.needs.contact",
            value=float(needs.get("contact") or 0.0),
            source="digital_life_state",
            source_ref=source_ref,
            confidence=0.62,
            observed_at_ms=observed,
            ttl_ms=SHORT_INFERENCE_TTL_MS,
            epistemic_type="inferred",
            now_ms=now_ms,
            reason_codes=["LOW_CONFIDENCE_INFERENCE"],
        ),
        _fact(
            key="work.active",
            value=bool(work.get("active")),
            source="digital_life_state",
            source_ref=source_ref,
            confidence=0.78,
            observed_at_ms=int(work.get("updated_at_ms") or observed),
            ttl_ms=WORK_ACTIVE_TTL_MS if bool(work.get("active")) else SHORT_INFERENCE_TTL_MS,
            epistemic_type="derived",
            now_ms=now_ms,
        ),
        _fact(
            key="life.action.current",
            value={"action_kind": _text(action.get("action_kind"), 80), "action_id": _text(action.get("action_id"), 120)},
            source="digital_life_state",
            source_ref=source_ref,
            confidence=0.66,
            observed_at_ms=observed,
            ttl_ms=SHORT_INFERENCE_TTL_MS,
            epistemic_type="inferred",
            now_ms=now_ms,
            reason_codes=["LOW_CONFIDENCE_INFERENCE"],
        ),
    ]


def _environment_facts(*, now_ms: int) -> list[dict[str, Any]]:
    env = build_environment_snapshot(now_ms=now_ms)
    source_ref = f"environment://local/{now_ms}"
    return [
        _fact(
            key="environment.day_phase",
            value=env.get("day_phase"),
            source="environment_snapshot",
            source_ref=source_ref,
            confidence=0.92,
            observed_at_ms=int(env.get("now_ms") or now_ms),
            ttl_ms=ENVIRONMENT_TTL_MS,
            epistemic_type="observed",
            now_ms=now_ms,
        ),
        _fact(
            key="environment.meal_phase",
            value=env.get("meal_phase"),
            source="environment_snapshot",
            source_ref=source_ref,
            confidence=0.72,
            observed_at_ms=int(env.get("now_ms") or now_ms),
            ttl_ms=ENVIRONMENT_TTL_MS,
            epistemic_type="inferred",
            now_ms=now_ms,
            reason_codes=["LOW_CONFIDENCE_INFERENCE"],
        ),
        _fact(
            key="environment.routine_phase",
            value=env.get("routine_phase"),
            source="environment_snapshot",
            source_ref=source_ref,
            confidence=0.7,
            observed_at_ms=int(env.get("now_ms") or now_ms),
            ttl_ms=ENVIRONMENT_TTL_MS,
            epistemic_type="inferred",
            now_ms=now_ms,
            reason_codes=["LOW_CONFIDENCE_INFERENCE"],
            metadata={"surface_facts": list(env.get("surface_facts") or [])[:4]},
        ),
    ]


def _work_event_facts(
    *,
    events: Sequence[WorkEnvironmentEventRecord],
    now_ms: int,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        observed = int(event.created_at_ms or now_ms)
        active = bool(event.active)
        ttl = WORK_ACTIVE_TTL_MS if active else WORK_HISTORY_TTL_MS
        facts.append(
            _fact(
                key="work.latest_event" if index == 0 else f"work.recent_event.{index}",
                value={
                    "event_type": str(event.event_type or ""),
                    "work_id": str(event.work_id or ""),
                    "summary": _text(event.summary, 320),
                    "urgency": str(event.urgency or "medium"),
                    "active": active,
                    "emotional_effect": dict(event.emotional_effect),
                },
                source="work_environment_event",
                source_ref=f"work_environment_event://{event.event_id}",
                confidence=0.88 if not _expired(now_ms, observed + ttl) else 0.48,
                observed_at_ms=observed,
                ttl_ms=ttl,
                epistemic_type="observed",
                now_ms=now_ms,
            )
        )
    return facts


def _health_facts(
    *,
    state: Mapping[str, Any] | None,
    person_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    if state is None:
        return [
            _fact(
                key="runtime.health.alive_status",
                value="unknown",
                source="life_runtime_health",
                source_ref=f"life_runtime_health://{person_id}",
                confidence=0.0,
                observed_at_ms=now_ms,
                ttl_ms=HEALTH_TTL_MS,
                epistemic_type="degraded",
                now_ms=now_ms,
                conflict_state="SOURCE_UNAVAILABLE",
                reason_codes=[WORLD_PROJECTION_SOURCE_DEGRADED],
            )
        ]
    runtime = _map(state.get("runtime"))
    autonomy = _map(state.get("autonomy"))
    last_integrated = int(runtime.get("last_integrated_ms") or _state_observed_at(state, now_ms))
    age_ms = max(0, now_ms - last_integrated) if last_integrated else 0
    alive_status = str(runtime.get("alive_status") or "alive")
    if age_ms > HEALTH_TTL_MS * 2:
        alive_status = "stale"
    elif age_ms > HEALTH_TTL_MS:
        alive_status = "degraded_alive"
    observed = max(0, int(now_ms - age_ms))
    return [
        _fact(
            key="runtime.health.alive_status",
            value=alive_status,
            source="life_runtime_health",
            source_ref=f"life_runtime_health://{person_id}",
            confidence=0.86,
            observed_at_ms=observed or now_ms,
            ttl_ms=HEALTH_TTL_MS,
            epistemic_type="observed",
            now_ms=now_ms,
            metadata={"age_ms": age_ms},
        ),
        _fact(
            key="runtime.health.user_input_dependency",
            value=bool(autonomy.get("user_input_required_for_progress", False)),
            source="life_runtime_health",
            source_ref=f"life_runtime_health://{person_id}",
            confidence=0.58,
            observed_at_ms=observed or now_ms,
            ttl_ms=HEALTH_TTL_MS,
            epistemic_type="inferred",
            now_ms=now_ms,
            reason_codes=["LOW_CONFIDENCE_INFERENCE"],
        ),
    ]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _state_arbiter_facts(
    *,
    state_arbiter: Any | None,
    fact_keys: Iterable[str],
    session_id: str,
    now_ms: int,
) -> list[dict[str, Any]]:
    if state_arbiter is None:
        return []
    facts: list[dict[str, Any]] = []
    for raw_key in fact_keys:
        key = str(raw_key or "").strip()
        if not key:
            continue
        try:
            result = await _maybe_await(state_arbiter.resolve(key, session_id=session_id or None))
        except Exception as exc:
            facts.append(
                _fact(
                    key=f"state_arbiter.{key}",
                    value=None,
                    source="state_arbiter",
                    source_ref=f"state_arbiter://degraded/{key}",
                    confidence=0.0,
                    observed_at_ms=now_ms,
                    ttl_ms=ENVIRONMENT_TTL_MS,
                    epistemic_type="degraded",
                    now_ms=now_ms,
                    conflict_state="SOURCE_UNAVAILABLE",
                    conflict_detail=str(exc),
                    reason_codes=[WORLD_PROJECTION_SOURCE_DEGRADED],
                )
            )
            continue
        if result is None:
            continue
        source = str(getattr(getattr(result, "source", ""), "value", getattr(result, "source", "state_arbiter")) or "state_arbiter")
        conflict = bool(getattr(result, "conflict_detected", False))
        facts.append(
            _fact(
                key=f"state_arbiter.{key}",
                value=getattr(result, "value", None),
                source="state_arbiter",
                source_ref=f"state_arbiter://{source}/{key}",
                confidence=0.64 if conflict else 0.84,
                observed_at_ms=now_ms,
                ttl_ms=SHORT_INFERENCE_TTL_MS,
                epistemic_type="adjudicated",
                now_ms=now_ms,
                conflict_state="CONFLICT" if conflict else "NONE",
                conflict_detail=str(getattr(result, "conflict_detail", "") or ""),
                reason_codes=["STATE_ARBITER_CONFLICT"] if conflict else [],
                metadata={
                    "winner_source": source,
                    "is_fallback": bool(getattr(result, "is_fallback", False)),
                    "fallback_chain": [str(getattr(item, "value", item)) for item in list(getattr(result, "fallback_chain", []) or [])],
                },
            )
        )
    return facts


def _dedupe_facts(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in facts:
        key = str(item.get("key") or "")
        if not key:
            continue
        existing = deduped.get(key)
        if existing is None or float(item.get("confidence") or 0.0) >= float(existing.get("confidence") or 0.0):
            deduped[key] = dict(item)
    return list(deduped.values())


async def build_world_snapshot(
    *,
    ledger: Any,
    person_id: str = DEFAULT_PERSON_ID,
    state_arbiter: Any | None = None,
    fact_keys: Iterable[str] | None = None,
    session_id: str = "",
    now_ms: int | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    now = int(now_ms or _now_ms())
    resolved = str(person_id or DEFAULT_PERSON_ID)
    if not enabled:
        return {
            "schema_version": WORLD_SNAPSHOT_SCHEMA_VERSION,
            "person_id": resolved,
            "generated_at_ms": now,
            "read_only": True,
            "write_api_available": False,
            "status": "DISABLED",
            "reason_code": WORLD_PROJECTION_DISABLED,
            "facts": [],
            "source_summary": {},
        }
    try:
        persistence = WorldModelQueryRepository.from_ledger(ledger).read_snapshot(
            person_id=resolved,
            work_event_limit=3,
        )
    except Exception:
        persistence = WorldModelPersistenceSnapshot(
            life_state=None,
            work_events=(),
            life_state_source_available=False,
            work_event_source_available=False,
        )
    facts: list[dict[str, Any]] = []
    facts.extend(_environment_facts(now_ms=now))
    facts.extend(
        _life_state_facts(
            state=persistence.life_state,
            person_id=resolved,
            now_ms=now,
        )
    )
    facts.extend(_work_event_facts(events=persistence.work_events, now_ms=now))
    facts.extend(
        _health_facts(
            state=persistence.life_state,
            person_id=resolved,
            now_ms=now,
        )
    )
    facts.extend(await _state_arbiter_facts(state_arbiter=state_arbiter, fact_keys=list(fact_keys or []), session_id=session_id, now_ms=now))
    deduped = _dedupe_facts(facts)
    source_summary: dict[str, int] = {}
    conflict_count = 0
    expired_count = 0
    inferred_count = 0
    for fact in deduped:
        source = str(fact.get("source") or "unknown")
        source_summary[source] = source_summary.get(source, 0) + 1
        conflict_state = str(fact.get("conflict_state") or "")
        if "CONFLICT" in conflict_state or "SOURCE_UNAVAILABLE" in conflict_state:
            conflict_count += 1
        if "EXPIRED" in conflict_state:
            expired_count += 1
        if str(fact.get("epistemic_type") or "") == "inferred":
            inferred_count += 1
    return {
        "schema_version": WORLD_SNAPSHOT_SCHEMA_VERSION,
        "person_id": resolved,
        "generated_at_ms": now,
        "read_only": True,
        "write_api_available": False,
        "write_methods": [],
        "status": "OK",
        "facts": deduped,
        "source_summary": source_summary,
        "conflict_count": conflict_count,
        "expired_count": expired_count,
        "inferred_count": inferred_count,
    }


class WorldProjectionReadModel:
    """Read-only facade for WorldProjection snapshots.

    This class intentionally exposes no write/update/apply methods.
    """

    def __init__(self, *, ledger: Any, state_arbiter: Any | None = None) -> None:
        self.ledger = ledger
        self.state_arbiter = state_arbiter

    async def snapshot(
        self,
        *,
        person_id: str = DEFAULT_PERSON_ID,
        fact_keys: Iterable[str] | None = None,
        session_id: str = "",
        now_ms: int | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        return await build_world_snapshot(
            ledger=self.ledger,
            person_id=person_id,
            state_arbiter=self.state_arbiter,
            fact_keys=fact_keys,
            session_id=session_id,
            now_ms=now_ms,
            enabled=enabled,
        )


__all__ = [
    "WORLD_SNAPSHOT_SCHEMA_VERSION",
    "WORLD_PROJECTION_DISABLED",
    "WORLD_PROJECTION_SOURCE_DEGRADED",
    "WorldProjectionReadModel",
    "build_world_snapshot",
]
