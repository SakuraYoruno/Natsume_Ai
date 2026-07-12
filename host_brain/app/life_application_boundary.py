"""Application adapter for the governed Digital Life command/query boundary.

The app layer deliberately knows only the frozen command and snapshot protocols.
It never calls the legacy temporal integrator and it never creates schema.  Tests
and alternate compositions can inject providers without importing the production
repository or touching the default database.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import threading
import time
from typing import Any, Protocol
import uuid


class LifeCommandServiceLike(Protocol):
    def submit(self, command: Any) -> Any: ...

    def submit_next(self, person_id: str, command_factory: Callable[[Any], Any]) -> Any: ...


class LifeSnapshotReaderLike(Protocol):
    def get_snapshot(self, person_id: str) -> Any: ...

    def list_events(
        self,
        person_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> Sequence[Any]: ...

    def get_receipt(self, command_id: str) -> Any | None: ...


LifeCommandServiceProvider = Callable[[], LifeCommandServiceLike]
LifeSnapshotReaderProvider = Callable[[], LifeSnapshotReaderLike]

_provider_lock = threading.RLock()
_command_service_provider: LifeCommandServiceProvider | None = None
_snapshot_reader_provider: LifeSnapshotReaderProvider | None = None
_default_cache_key = ""
_default_command_service: LifeCommandServiceLike | None = None
_default_snapshot_reader: LifeSnapshotReaderLike | None = None


def set_life_application_providers(
    *,
    command_service_provider: LifeCommandServiceProvider | None = None,
    snapshot_reader_provider: LifeSnapshotReaderProvider | None = None,
) -> None:
    """Install explicit app providers; passing ``None`` restores composition defaults."""

    global _command_service_provider, _snapshot_reader_provider
    with _provider_lock:
        _command_service_provider = command_service_provider
        _snapshot_reader_provider = snapshot_reader_provider


def reset_life_application_cache() -> None:
    """Clear only lazily composed defaults; injected providers remain installed."""

    global _default_cache_key, _default_command_service, _default_snapshot_reader
    with _provider_lock:
        _default_cache_key = ""
        _default_command_service = None
        _default_snapshot_reader = None


def get_life_command_service() -> LifeCommandServiceLike:
    with _provider_lock:
        provider = _command_service_provider
    if provider is not None:
        return provider()
    service, _ = _default_components()
    return service


def get_life_snapshot_reader() -> LifeSnapshotReaderLike:
    with _provider_lock:
        provider = _snapshot_reader_provider
    if provider is not None:
        return provider()
    _, reader = _default_components()
    return reader


def _default_components() -> tuple[LifeCommandServiceLike, LifeSnapshotReaderLike]:
    """Compose against the canonical ledger without private connection access or DDL."""

    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
    from host_brain.digital_life.reader import LifeSnapshotReader  # noqa: PLC0415
    from host_brain.digital_life.service import LifeCommandService  # noqa: PLC0415
    from host_brain.persistence.life_command_repository import (  # noqa: PLC0415
        LifeCommandRepository,
    )

    ledger = get_ledger()
    db_path = str(
        getattr(ledger, "canonical_db_path", "")
        or getattr(ledger, "db_path", "")
        or ""
    ).strip()
    cache_key = db_path or f"ledger:{id(ledger)}"
    global _default_cache_key, _default_command_service, _default_snapshot_reader
    with _provider_lock:
        if (
            _default_cache_key == cache_key
            and _default_command_service is not None
            and _default_snapshot_reader is not None
        ):
            return _default_command_service, _default_snapshot_reader
        repository = LifeCommandRepository.from_ledger(ledger)
        _default_command_service = LifeCommandService(repository)
        _default_snapshot_reader = LifeSnapshotReader(repository)
        _default_cache_key = cache_key
        return _default_command_service, _default_snapshot_reader


def read_life_snapshot(
    person_id: str,
    *,
    reader: LifeSnapshotReaderLike | None = None,
) -> dict[str, Any]:
    """Read one stable snapshot and normalize typed/mapping implementations."""

    resolved = str(person_id or "default").strip() or "default"
    raw = (reader or get_life_snapshot_reader()).get_snapshot(resolved)
    payload = _as_dict(raw)
    state = _mapping(payload.get("state"))
    version = _non_negative_int(payload.get("version"), default=_non_negative_int(state.get("version")))
    state.setdefault("person_id", resolved)
    state.setdefault("version", version)
    return {
        "person_id": resolved,
        "version": version,
        "state": state,
        "state_digest": str(payload.get("state_digest") or ""),
        "last_event_id": _optional_text(payload.get("last_event_id")),
        "last_event_digest": _optional_text(payload.get("last_event_digest")),
        "updated_at_ms": _non_negative_int(payload.get("updated_at_ms")),
    }


def read_life_events(
    person_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 100,
    reader: LifeSnapshotReaderLike | None = None,
) -> list[dict[str, Any]]:
    rows = (reader or get_life_snapshot_reader()).list_events(
        str(person_id or "default"),
        after_sequence=max(0, int(after_sequence or 0)),
        limit=max(1, min(int(limit or 100), 1000)),
    )
    return [_as_dict(row) for row in rows]


def submit_life_command(
    *,
    command_type: str,
    person_id: str,
    payload: Mapping[str, Any] | None = None,
    command_id: str = "",
    correlation_id: str | None = None,
    causation_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    expected_version: int | None = None,
    created_at_ms: int | None = None,
    service: LifeCommandServiceLike | None = None,
    reader: LifeSnapshotReaderLike | None = None,
) -> dict[str, Any]:
    """Submit exactly one governed command and return its stable receipt mapping."""

    from host_brain.digital_life.contracts import LifeCommand  # noqa: PLC0415

    resolved_person = str(person_id or "default").strip() or "default"
    resolved_type = str(command_type or "").strip().lower()
    if not resolved_type:
        raise ValueError("command_type is required")
    resolved_reader = reader or get_life_snapshot_reader()
    provided_command_id = str(command_id or "").strip()
    resolved_command_id = provided_command_id or (
        f"life::{resolved_type}::{uuid.uuid4().hex}"
    )
    resolved_created_at_ms = max(0, int(created_at_ms or int(time.time() * 1000)))

    def _command(
        version: int,
        *,
        honor_explicit_version: bool = True,
        applied_at_ms: int | None = None,
    ) -> Any:
        return LifeCommand(
            command_id=resolved_command_id,
            person_id=resolved_person,
            command_type=resolved_type,
            payload=dict(payload or {}),
            expected_version=(
                max(0, int(expected_version))
                if expected_version is not None and honor_explicit_version
                else max(0, int(version))
            ),
            created_at_ms=max(
                resolved_created_at_ms,
                max(0, int(applied_at_ms or 0)),
            ),
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=dict(metadata or {}),
        )

    if provided_command_id:
        existing = resolved_reader.get_receipt(provided_command_id)
        if existing is not None:
            from host_brain.persistence.life_command_repository import (  # noqa: PLC0415
                LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
                LifeCommandRepositoryError,
            )

            replayed = _as_dict(existing)
            receipt_hash = str(replayed.get("command_hash") or "").strip()
            receipt_version = _non_negative_int(replayed.get("state_version"))
            replay_candidate = _command(
                max(0, receipt_version - 1),
                honor_explicit_version=False,
            )
            if not receipt_hash or replay_candidate.command_hash != receipt_hash:
                raise LifeCommandRepositoryError(
                    LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
                    "command_id replay payload or lineage differs from durable receipt",
                )
            replayed["replayed"] = True
            return replayed

    resolved_service = service or get_life_command_service()
    submit_next = getattr(resolved_service, "submit_next", None)
    if expected_version is not None:
        snapshot = read_life_snapshot(resolved_person, reader=resolved_reader)
        receipt = resolved_service.submit(
            _command(
                int(expected_version),
                applied_at_ms=int(snapshot.get("updated_at_ms") or 0),
            )
        )
    elif callable(submit_next):
        def _next_command(snapshot: Any) -> Any:
            return _command(
                int(getattr(snapshot, "version", 0)),
                applied_at_ms=int(getattr(snapshot, "updated_at_ms", 0) or 0),
            )

        receipt = submit_next(
            resolved_person,
            _next_command,
        )
    else:
        snapshot = read_life_snapshot(resolved_person, reader=resolved_reader)
        receipt = resolved_service.submit(
            _command(
                int(snapshot["version"]),
                applied_at_ms=int(snapshot.get("updated_at_ms") or 0),
            )
        )
    return _as_dict(receipt)


def project_inner_life_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Build the legacy prompt-compatible inner-life view without advancing state."""

    envelope = _mapping(snapshot)
    state = _mapping(envelope.get("state")) if "state" in envelope else envelope
    supplied = _mapping(state.get("inner_life_snapshot"))
    if supplied:
        return supplied
    runtime = _mapping(state.get("runtime"))
    autonomy = _mapping(state.get("autonomy"))
    growth = _mapping(state.get("self_growth"))
    affect = _mapping(state.get("affect"))
    needs = _mapping(state.get("needs"))
    pressure = _mapping(state.get("life_pressure"))
    inner = _mapping(state.get("inner_world"))
    body = _mapping(state.get("embodiment"))
    organ = _mapping(state.get("organ_runtime"))
    attention_target = str(
        inner.get("attention_target")
        or inner.get("current_preoccupation")
        or autonomy.get("current_attention")
        or ""
    ).strip()
    preoccupation = str(inner.get("current_preoccupation") or "").strip()
    return {
        "being": {
            "alive_status": str(runtime.get("alive_status") or "unknown"),
            "last_integrated_ms": runtime.get("last_integrated_ms"),
            "continuity": _float(needs.get("memory_consolidation")),
        },
        "drive": {
            "contact": _float(needs.get("contact")),
            "rest": _float(needs.get("rest")),
            "repair": _float(affect.get("tension")),
            "play": _float(needs.get("play")),
            "curiosity": _float(pressure.get("work")),
            "inhibition": max(_float(pressure.get("fatigue")), _float(affect.get("irritation"))),
        },
        "attention": {
            "mode": "anchored" if attention_target else "diffuse",
            "target": attention_target,
            "stability": max(0.0, min(1.0, 1.0 - _float(affect.get("arousal")) * 0.5)),
        },
        "preoccupation": {
            "object": preoccupation,
            "pressure": max(_float(pressure.get("dream")), _float(pressure.get("relationship")) * 0.5),
            "expires_at_ms": _non_negative_int(
                inner.get("preoccupation_expires_at_ms") or inner.get("expires_at_ms")
            ),
        },
        "expression_bias": {
            "dialogue": str(inner.get("dialogue_bias") or ""),
            "voice": str(body.get("voice_tone") or ""),
            "body": str(body.get("idle_animation") or ""),
        },
        "inner_world": inner,
        "organ_runtime": {
            "organ_count": _non_negative_int(organ.get("organ_count")),
            "top_organs": list(organ.get("top_organs") or [])[:8],
            "synthesis": _mapping(organ.get("synthesis")),
        },
        "self_growth": growth,
    }


def project_life_director_pack(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Pure compatibility projection consumed by Surface prompt assembly."""

    envelope = _mapping(snapshot)
    state = _mapping(envelope.get("state")) if "state" in envelope else envelope
    if not state or int(envelope.get("version") or state.get("version") or 0) <= 0:
        return {}
    supplied = _mapping(state.get("life_director"))
    if supplied:
        return supplied
    pressure = _mapping(state.get("life_pressure"))
    work = _mapping(state.get("work"))
    inner = _mapping(state.get("inner_world"))
    action = _mapping(state.get("life_action"))
    body = _mapping(action.get("embodiment_target")) or _mapping(state.get("embodiment"))
    organ = _mapping(state.get("organ_runtime"))
    motives = [row for row in list(state.get("motives") or []) if isinstance(row, Mapping)]
    action_kind = str(action.get("action_kind") or "").strip()
    scene = _scene_from_state(work=work, inner=inner, pressure=pressure, action_kind=action_kind)
    motive = str(
        action_kind
        or (motives[0].get("motive") if motives else "")
        or "quiet_presence"
    ).strip()
    peak_pressure = max([_float(value) for value in pressure.values()] or [0.0])
    influence = "strong" if peak_pressure >= 0.66 else "medium" if peak_pressure >= 0.35 else "weak"
    channels = [str(item) for item in list(action.get("channels") or []) if str(item).strip()]
    visible = channels or ["status_panel", "dialogue"]
    growth = _mapping(state.get("self_growth"))
    projects = list(growth.get("active_projects") or state.get("self_projects") or [])
    agenda = list(inner.get("self_agenda") or [])
    environment = _mapping(state.get("environment"))
    summary = str(work.get("summary") or "").strip()[:120]
    backend = (
        f"Codex backend {str(work.get('last_event_type') or 'active')}: {summary}".strip()
        if bool(work.get("active")) or summary
        else "no active Codex backend work event"
    )
    inner_snapshot = project_inner_life_snapshot(envelope)
    return {
        "frontstage_role": "Natsume is the frontstage digital life; Codex is the work backend.",
        "codex_backend_status": backend,
        "state_version": int(envelope.get("version") or state.get("version") or 0),
        "runtime": _mapping(state.get("runtime")),
        "environment": environment,
        "private_mind_summary": {
            "attention_target": str(_mapping(inner_snapshot.get("attention")).get("target") or ""),
            "current_preoccupation": str(_mapping(inner_snapshot.get("preoccupation")).get("object") or ""),
            "felt_tendency": str(inner.get("felt_tendency") or ""),
            "dialogue_bias": str(_mapping(inner_snapshot.get("expression_bias")).get("dialogue") or ""),
            "must_not_expose_raw_inner_text": True,
        },
        "inner_life_snapshot": inner_snapshot,
        "adaptive_self_pack": {
            "current_attention": str(
                _mapping(state.get("autonomy")).get("current_attention")
                or inner.get("attention_kind")
                or ""
            ),
            "active_agenda": [dict(row) for row in agenda[:5] if isinstance(row, Mapping)],
            "active_projects": [dict(row) for row in projects[:6] if isinstance(row, Mapping)],
            "recent_growth": list(growth.get("recent_growth_signals") or [])[:5],
            "stable_habits": _growth_habits(growth),
            "style_bias": str(growth.get("current_learning_focus") or inner.get("dialogue_bias") or ""),
            "must_not_claim": [
                "不要说自己在训练模型或修改 Soul.md。",
                "不要把私有内心流、梦境或学习字段当现实事实。",
                "不要声称看见用户真实状态或屏幕。",
            ],
        },
        "scene": scene,
        "dominant_motive": motive,
        "life_pressure": pressure,
        "dialogue_influence": influence,
        "visible_obligation": visible,
        "embodiment_target": body,
        "organ_life_influence": organ,
        "style_goal": str(inner.get("dialogue_bias") or _style_goal(scene)),
        "must_not_do": [
            "Do not expose life_state, tick, organ vote, pressure, trace, or schema names.",
            "Do not say Natsume is operating terminal commands when Codex backend is doing the work.",
            "Do not turn dreams or inner monologue into real-world facts.",
        ],
    }


def project_surface_organ_context(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Project stable Life state into the historical Surface organ-context shape."""

    envelope = _mapping(snapshot)
    state = _mapping(envelope.get("state")) if "state" in envelope else envelope
    if not state or int(envelope.get("version") or state.get("version") or 0) <= 0:
        return {}
    affect = _mapping(state.get("affect"))
    pressure = _mapping(state.get("life_pressure"))
    organ = _mapping(state.get("organ_runtime"))
    return {
        "vital_signs": {
            "emotion_tag": str(_mapping(state.get("embodiment")).get("emotion_tag") or "NEUTRAL"),
            "mood_state": str(affect.get("mood") or "steady"),
            "stress_index": max(_float(affect.get("tension")), _float(pressure.get("work"))),
            "energy_reserve": _float(affect.get("energy")),
            "homeostasis_ok": max(_float(affect.get("tension")), _float(pressure.get("fatigue"))) < 0.7,
        },
        "emotion_context": {
            "emotion_tag": str(_mapping(state.get("embodiment")).get("emotion_tag") or "NEUTRAL"),
            "mood_state": str(affect.get("mood") or "steady"),
            "emotion_valence": _float(affect.get("valence")),
            "emotion_arousal": _float(affect.get("arousal")),
        },
        "organ_runtime": organ,
        "life_snapshot": {
            "person_id": str(envelope.get("person_id") or state.get("person_id") or "default"),
            "version": int(envelope.get("version") or state.get("version") or 0),
            "state_digest": str(envelope.get("state_digest") or ""),
        },
        "tribe_context_source": "life_snapshot_reader",
    }


def _scene_from_state(
    *,
    work: Mapping[str, Any],
    inner: Mapping[str, Any],
    pressure: Mapping[str, Any],
    action_kind: str,
) -> str:
    if bool(work.get("active")) or action_kind.startswith("work"):
        return "work_waiting"
    if str(inner.get("dream_residue") or "").strip() or action_kind == "dream_afterglow":
        return "dream_residue"
    if _float(pressure.get("absence")) >= 0.45 or action_kind == "soft_reconnect":
        return "reconnect"
    if _float(pressure.get("fatigue")) >= 0.55 or action_kind == "care_check":
        return "night_companion"
    return "idle"


def _style_goal(scene: str) -> str:
    return {
        "work_waiting": "像前台数字生命感受到后台工作仍在继续，但不要机械汇报。",
        "dream_residue": "只让梦境余温影响语气，不把梦境写成现实事实。",
        "reconnect": "自然承接重新靠近的感觉，不解释内部系统。",
        "night_companion": "保持低频、柔和的身体在场感。",
    }.get(scene, "像前台数字生命顺口回应，不要写成助手总结。")


def _growth_habits(growth: Mapping[str, Any]) -> list[str]:
    habits: list[str] = []
    for row in list(growth.get("recent_growth_signals") or [])[:8]:
        if not isinstance(row, Mapping):
            continue
        candidate = _mapping(row.get("candidate_delta"))
        habit = str(candidate.get("habit") or "").strip()
        if habit and habit not in habits:
            habits.append(habit)
    return habits[:6]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    converter = getattr(value, "as_dict", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, Mapping):
            return dict(converted)
    raise TypeError(f"Life boundary value is not mapping-compatible: {type(value).__name__}")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _non_negative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value if value not in (None, "") else default))
    except (TypeError, ValueError):
        return max(0, int(default))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


__all__ = [
    "LifeCommandServiceLike",
    "LifeCommandServiceProvider",
    "LifeSnapshotReaderLike",
    "LifeSnapshotReaderProvider",
    "get_life_command_service",
    "get_life_snapshot_reader",
    "project_inner_life_snapshot",
    "project_life_director_pack",
    "project_surface_organ_context",
    "read_life_events",
    "read_life_snapshot",
    "reset_life_application_cache",
    "set_life_application_providers",
    "submit_life_command",
]
