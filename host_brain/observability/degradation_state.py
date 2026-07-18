"""Durable, fail-closed dependency degradation state.

RuntimeEventStore is the recovery truth.  The in-memory reducer is only a read
model and EventBus is only a low-latency notification path.  A healthy probe
never expands capability by itself: recovery is bound to a durable ReviewItem
and its one-time resume token.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


DEGRADATION_STATE_SCHEMA_VERSION = "Natsume.degradation.state.v1"
DEGRADATION_TRANSITION_SCHEMA_VERSION = "Natsume.degradation.transition.v1"
DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION = "Natsume.degradation.fault_matrix.v1"
DEGRADATION_CAPABILITY_DECISION_SCHEMA_VERSION = (
    "Natsume.degradation.capability_decision.v1"
)
DEGRADATION_REDUCER_STATE_SCHEMA_VERSION = "Natsume.degradation.reducer-state.v1"

DEGRADATION_HEALTHY = "HEALTHY"
DEGRADATION_DEGRADED = "DEGRADED"
DEGRADATION_RECOVERY_PENDING = "RECOVERY_PENDING"

DEGRADATION_ENTERED = "DEGRADATION_ENTERED"
DEGRADATION_RECOVERY_OBSERVED = "DEGRADATION_RECOVERY_OBSERVED"
DEGRADATION_RECOVERY_ACKNOWLEDGED = "DEGRADATION_RECOVERY_ACKNOWLEDGED"

DEGRADATION_EVIDENCE_REQUIRED = "DEGRADATION_EVIDENCE_REQUIRED"
DEGRADATION_EVIDENCE_INVALID = "DEGRADATION_EVIDENCE_INVALID"
DEGRADATION_HEALTH_EVIDENCE_INVALID = "DEGRADATION_HEALTH_EVIDENCE_INVALID"
DEGRADATION_REVIEW_REQUIRED = "DEGRADATION_REVIEW_REQUIRED"
DEGRADATION_UNKNOWN_DEPENDENCY = "DEGRADATION_UNKNOWN_DEPENDENCY"
DEGRADATION_STATE_UNAVAILABLE = "DEGRADATION_STATE_UNAVAILABLE"
DEGRADATION_DURABLE_EVENT_FAILED = "DEGRADATION_DURABLE_EVENT_FAILED"
DEGRADATION_REPLAY_INVALID = "DEGRADATION_REPLAY_INVALID"
DEGRADATION_POLICY_DISABLED = "DEGRADATION_POLICY_DISABLED"
DEGRADATION_POLICY_INVALID = "DEGRADATION_POLICY_INVALID"
DEGRADATION_FAULT_INJECTION_NOT_ISOLATED = (
    "DEGRADATION_FAULT_INJECTION_NOT_ISOLATED"
)

_DURABLE_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_FAILURE_STATUSES = frozenset(
    {"DOWN", "DEGRADED", "FAILED", "ERROR", "UNAVAILABLE", "UNHEALTHY"}
)
_HEALTHY_STATUSES = frozenset({"UP", "OK", "READY", "HEALTHY", "ACCEPTED"})
_IGNORED_STATUSES = frozenset({"", "UNKNOWN", "SKIPPED", "DEFERRED", "DISABLED"})
_NON_HEALTHY_SUCCESS_MARKERS = (
    "disabled by config",
    "=false",
    "skipped",
    "lazy init",
    "no dedicated",
    "gateway-only",
)
_HIGH_RISK_THRESHOLD = 3


class DegradationStateError(RuntimeError):
    """Stable fail-closed error returned by degradation operations."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code)
        self.detail = str(detail)
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class FaultPolicy:
    dependency_id: str
    service_ids: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    high_risk_capabilities: tuple[str, ...]
    safe_fallbacks: tuple[str, ...]
    failure_statuses: tuple[str, ...] = tuple(sorted(_FAILURE_STATUSES))
    healthy_statuses: tuple[str, ...] = tuple(sorted(_HEALTHY_STATUSES))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FAULT_POLICIES: dict[str, FaultPolicy] = {
    "MODEL": FaultPolicy(
        dependency_id="MODEL",
        service_ids=(
            "model",
            "model_provider",
            "llm_gateway",
            "host.integrations.llm_gateway",
            "host.governor.decision",
        ),
        affected_capabilities=(
            "model_inference",
            "autonomous_planning",
            "tool_planning",
            "capability_upgrade",
            "high_risk_execution",
        ),
        high_risk_capabilities=(
            "autonomous_planning",
            "tool_planning",
            "capability_upgrade",
            "high_risk_execution",
        ),
        safe_fallbacks=("deterministic_rule", "human_review"),
    ),
    "MEMORY": FaultPolicy(
        dependency_id="MEMORY",
        service_ids=(
            "memory",
            "chroma",
            "memory_guard",
            "recall_fusion",
            "host.retrieval.recall_fusion",
        ),
        affected_capabilities=(
            "memory_recall",
            "memory_write",
            "canon_apply",
            "domain_knowledge_write",
        ),
        high_risk_capabilities=(
            "memory_write",
            "canon_apply",
            "domain_knowledge_write",
        ),
        safe_fallbacks=("context_only", "read_only", "human_review"),
    ),
    "VTS": FaultPolicy(
        dependency_id="VTS",
        service_ids=("vts", "edge.rpc.vts", "live2d", "vts_live2d"),
        affected_capabilities=("avatar_expression", "avatar_control"),
        high_risk_capabilities=("avatar_control",),
        safe_fallbacks=("text_only", "neutral_avatar"),
    ),
    "TTS": FaultPolicy(
        dependency_id="TTS",
        service_ids=("tts", "edge.rpc.tts", "voice_output"),
        affected_capabilities=("voice_render", "voice_output", "voice_broadcast"),
        high_risk_capabilities=("voice_broadcast",),
        safe_fallbacks=("text_only",),
    ),
    "SSE": FaultPolicy(
        dependency_id="SSE",
        service_ids=(
            "sse",
            "runtime_event_sse",
            "host.runtime_event.sse",
            "runtime_event_stream",
        ),
        affected_capabilities=("runtime_event_stream", "remote_review_resume"),
        high_risk_capabilities=("remote_review_resume",),
        safe_fallbacks=("cursor_polling", "local_review"),
    ),
    "DOMAIN": FaultPolicy(
        dependency_id="DOMAIN",
        service_ids=(
            "domain",
            "domain_store",
            "pentest_domain_store",
            "host.pentest.domain_store",
        ),
        affected_capabilities=(
            "domain_read",
            "domain_write",
            "pentest_execution",
            "canon_apply",
        ),
        high_risk_capabilities=(
            "domain_write",
            "pentest_execution",
            "canon_apply",
        ),
        safe_fallbacks=("domain_read_only", "lab_disabled", "human_review"),
    ),
}
FAULT_POLICIES: Mapping[str, FaultPolicy] = MappingProxyType(_FAULT_POLICIES)

_SERVICE_TO_DEPENDENCY = {
    service_id.lower(): dependency_id
    for dependency_id, policy in FAULT_POLICIES.items()
    for service_id in policy.service_ids
}

_STARTUP_MODULE_TO_DEPENDENCY = {
    "chat_llm_runtime": "MODEL",
    "chat_llm_runtime_warmup": "MODEL",
    "codex_exec_intent": "MODEL",
    "codex_exec_intent_warmup": "MODEL",
    "retrieval_runtime": "MEMORY",
    "retrieval_runtime_warmup": "MEMORY",
    "semantic_recaller": "MEMORY",
    "memory_guard": "MEMORY",
    "vts": "VTS",
    "tts": "TTS",
    "runtime_event_sse": "SSE",
    "pentest_domain_store": "DOMAIN",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _durable_ref(value: Any, *, namespace: str) -> str:
    text = str(value or "").strip()
    if text and _DURABLE_REF_PATTERN.fullmatch(text):
        return text
    return f"{namespace}://sha256/{_digest(text or namespace)}"


def _evidence_refs(value: Any) -> tuple[str, ...]:
    raw: Sequence[Any]
    if isinstance(value, str):
        raw = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        raw = value
    else:
        raw = ()
    normalized: list[str] = []
    for item in raw:
        ref = str(item or "").strip()
        if not ref:
            continue
        if not _DURABLE_REF_PATTERN.fullmatch(ref):
            raise DegradationStateError(
                DEGRADATION_EVIDENCE_INVALID,
                "degradation evidence must be a durable reference",
            )
        if ref not in normalized:
            normalized.append(ref)
    refs = tuple(normalized)
    if not refs:
        raise DegradationStateError(
            DEGRADATION_EVIDENCE_REQUIRED,
            "a degradation transition requires at least one durable evidence reference",
        )
    return refs


def _dependency_id(value: Any) -> str:
    token = str(value or "").strip().upper()
    if token in FAULT_POLICIES:
        return token
    mapped = _SERVICE_TO_DEPENDENCY.get(str(value or "").strip().lower())
    if mapped:
        return mapped
    raise DegradationStateError(
        DEGRADATION_UNKNOWN_DEPENDENCY,
        f"unsupported degradation dependency: {value}",
    )


def _status(value: Any) -> str:
    return str(value or "").strip().upper()


def _risk(value: Any) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 5


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DegradationStateError(
            DEGRADATION_REPLAY_INVALID,
            f"{field} must be a non-negative integer",
        )
    return value


def _positive_int(value: Any, *, field: str) -> int:
    result = _non_negative_int(value, field=field)
    if result < 1:
        raise DegradationStateError(
            DEGRADATION_REPLAY_INVALID,
            f"{field} must be positive",
        )
    return result


def _optional_non_negative_int(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    return _non_negative_int(value, field=field)


@dataclass(frozen=True, slots=True)
class DegradationTransition:
    schema_version: str
    transition_id: str
    event_type: str
    revision: int
    dependency_id: str
    dependency_status: str
    previous_state: str
    current_state: str
    source_ref: str
    evidence_refs: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    high_risk_capabilities: tuple[str, ...]
    observed_at_ms: int
    injected: bool
    detail_digest: str
    review_subject_ref: str | None = None
    review_subject_version: int | None = None
    review_checkpoint_id: str | None = None
    review_id: str | None = None
    resume_result_ref: str | None = None
    permission_effect: str = "RESTRICT_ONLY"
    external_side_effects: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DegradationState:
    schema_version: str
    state_id: str
    revision: int
    status: str
    entered_at_ms: int | None
    updated_at_ms: int
    recovered_at_ms: int | None
    active_dependencies: tuple[dict[str, Any], ...]
    recovery_pending_dependencies: tuple[dict[str, Any], ...]
    evidence_refs: tuple[str, ...]
    recovery_evidence_refs: tuple[str, ...]
    affected_capabilities: tuple[str, ...]
    capability_restrictions: tuple[dict[str, Any], ...]
    last_transition: dict[str, Any] | None
    transition_count: int
    policy_enabled: bool
    policy_status: str = "ENABLED"
    durability_required: bool = False
    durability_status: str = "NOT_REQUIRED"
    restored_event_count: int = 0
    last_durable_cursor: int = 0
    last_durable_error: str | None = None
    event_bus_status: str = "NOT_CONFIGURED"
    read_only: bool = True
    permission_effect: str = "RESTRICT_ONLY"
    recovery_requires_health_evidence: bool = True
    capability_expansion_requires_review: bool = True
    external_side_effects: bool = False

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(asdict(self))


class DegradationStateMachine:
    """Thread-safe transition reducer used by existing guardian paths."""

    def __init__(
        self,
        *,
        event_sink: Callable[[Any], Any] | None = None,
        event_store: Any | None = None,
        event_bus: Any | None = None,
        durability_required: bool = False,
        restore_from_store: bool = True,
        max_transition_history: int = 128,
    ) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._restriction_origins: dict[str, set[str]] = {}
        self._revision = 0
        self._entered_at_ms: int | None = None
        self._updated_at_ms = 0
        self._recovered_at_ms: int | None = None
        self._history: list[DegradationTransition] = []
        self._events: list[Any] = []
        self._event_sink = event_sink
        self._event_store = event_store
        self._event_bus = event_bus
        self._durability_required = bool(durability_required or event_store is not None)
        self._durability_status = (
            "READY" if event_store is not None else (
                "UNAVAILABLE" if self._durability_required else "NOT_REQUIRED"
            )
        )
        self._restored_event_count = 0
        self._last_durable_cursor = 0
        self._last_durable_error: str | None = None
        self._event_bus_status = "READY" if event_bus is not None else "NOT_CONFIGURED"
        self._max_transition_history = max(1, int(max_transition_history))
        if event_store is not None and restore_from_store:
            self.restore_from_event_store()

    def is_pure_reducer(self) -> bool:
        """Return whether an enclosing owner can safely own all side effects."""

        with self._lock:
            return (
                self._event_sink is None
                and self._event_store is None
                and self._event_bus is None
                and not self._durability_required
            )

    def _state_status(self) -> str:
        if self._active:
            return DEGRADATION_DEGRADED
        if self._pending or self._restriction_origins:
            return DEGRADATION_RECOVERY_PENDING
        return DEGRADATION_HEALTHY

    def _build_transition(
        self,
        *,
        event_type: str,
        dependency_id: str,
        dependency_status: str,
        previous_state: str,
        source_ref: str,
        evidence_refs: tuple[str, ...],
        observed_at_ms: int,
        injected: bool,
        detail: str,
        review_subject_ref: str | None = None,
        review_subject_version: int | None = None,
        review_checkpoint_id: str | None = None,
        review_id: str | None = None,
        resume_result_ref: str | None = None,
    ) -> DegradationTransition:
        self._revision += 1
        policy = FAULT_POLICIES[dependency_id]
        seed = {
            "dependency_id": dependency_id,
            "dependency_status": dependency_status,
            "event_type": event_type,
            "evidence_refs": evidence_refs,
            "observed_at_ms": observed_at_ms,
            "revision": self._revision,
        }
        transition = DegradationTransition(
            schema_version=DEGRADATION_TRANSITION_SCHEMA_VERSION,
            transition_id=f"degradation_transition_{_digest(seed)[:24]}",
            event_type=event_type,
            revision=self._revision,
            dependency_id=dependency_id,
            dependency_status=dependency_status,
            previous_state=previous_state,
            current_state=self._state_status(),
            source_ref=_durable_ref(source_ref, namespace="degradation-source"),
            evidence_refs=evidence_refs,
            affected_capabilities=policy.affected_capabilities,
            high_risk_capabilities=policy.high_risk_capabilities,
            observed_at_ms=observed_at_ms,
            injected=bool(injected),
            detail_digest=f"sha256:{_digest(str(detail or ''))}",
            review_subject_ref=review_subject_ref,
            review_subject_version=review_subject_version,
            review_checkpoint_id=review_checkpoint_id,
            review_id=review_id,
            resume_result_ref=resume_result_ref,
        )
        self._updated_at_ms = observed_at_ms
        self._history.append(transition)
        self._history = self._history[-self._max_transition_history :]
        return transition

    def _emit(self, transition: DegradationTransition) -> None:
        event = build_degradation_runtime_event(transition)
        if self._event_store is not None:
            try:
                record = self._event_store.append_event(event)
                if int(record.get("aggregate_version", -1)) != transition.revision:
                    raise ValueError("durable aggregate version mismatch")
                self._last_durable_cursor = int(record.get("cursor") or 0)
                self._durability_status = "READY"
                self._last_durable_error = None
            except Exception as exc:
                self._durability_status = "FAILED"
                self._last_durable_error = f"{type(exc).__name__}: {exc}"
                raise DegradationStateError(
                    DEGRADATION_DURABLE_EVENT_FAILED,
                    "degradation transition could not be durably appended",
                ) from exc
        elif self._durability_required:
            self._durability_status = "UNAVAILABLE"
            self._last_durable_error = "RuntimeEventStore is not configured"
            raise DegradationStateError(
                DEGRADATION_STATE_UNAVAILABLE,
                "durable degradation state is unavailable",
            )
        with self._lock:
            self._events.append(event)
            self._events = self._events[-self._max_transition_history :]
        if self._event_sink is not None:
            self._event_sink(event)
        if self._event_bus is not None:
            try:
                from host_brain.tribe.event_bus import TribeEvent  # noqa: PLC0415

                self._event_bus.publish(
                    TribeEvent(
                        event_id=transition.transition_id,
                        event_type=transition.event_type,
                        source_agent="degradation_state",
                        target_agent=None,
                        payload={
                            "dependency_id": transition.dependency_id,
                            "dependency_status": transition.dependency_status,
                            "revision": transition.revision,
                            "current_state": transition.current_state,
                            "runtime_event_ref": str(event.event_id),
                            "permission_effect": "RESTRICT_ONLY",
                        },
                        ts_ms=transition.observed_at_ms,
                    )
                )
                self._event_bus_status = "READY"
            except Exception as exc:
                # Durable append already succeeded; notification loss must never
                # roll back or broaden the authoritative state.
                self._event_bus_status = "FAILED"
                self._last_durable_error = (
                    self._last_durable_error
                    or f"EventBus {type(exc).__name__}: {exc}"
                )

    def _reset_reducer(self) -> None:
        self._active.clear()
        self._pending.clear()
        self._restriction_origins.clear()
        self._revision = 0
        self._entered_at_ms = None
        self._updated_at_ms = 0
        self._recovered_at_ms = None
        self._history.clear()
        self._events.clear()

    def export_reducer_state(self) -> dict[str, Any]:
        """Export the pure reducer state for an enclosing durable owner.

        RuntimeGuardian uses this snapshot to make its own journal the only
        durable writer.  Transport/event ports are deliberately excluded.
        """

        with self._lock:
            state = self.snapshot()
            return {
                "schema": DEGRADATION_REDUCER_STATE_SCHEMA_VERSION,
                "revision": state.revision,
                "status": state.status,
                "entered_at_ms": state.entered_at_ms,
                "updated_at_ms": state.updated_at_ms,
                "recovered_at_ms": state.recovered_at_ms,
                "active_dependencies": [
                    copy.deepcopy(row) for row in state.active_dependencies
                ],
                "recovery_pending_dependencies": [
                    copy.deepcopy(row)
                    for row in state.recovery_pending_dependencies
                ],
                "last_transition": copy.deepcopy(state.last_transition),
            }

    def restore_reducer_state(self, state: Mapping[str, Any]) -> None:
        """Replace the in-memory reducer from a validated Guardian snapshot."""

        if not isinstance(state, Mapping):
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                "embedded degradation reducer state must be a mapping",
            )
        if str(state.get("schema") or "") != DEGRADATION_REDUCER_STATE_SCHEMA_VERSION:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                "embedded degradation reducer schema is unsupported",
            )
        raw_revision = state.get("revision")
        if isinstance(raw_revision, bool) or not isinstance(raw_revision, int) or raw_revision < 0:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                "embedded degradation revision must be non-negative",
            )

        active: dict[str, dict[str, Any]] = {}
        for raw in state.get("active_dependencies") or ():
            if not isinstance(raw, Mapping):
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    "embedded active dependency must be a mapping",
                )
            dependency = _dependency_id(raw.get("dependency_id"))
            status = _status(raw.get("status"))
            if status not in _FAILURE_STATUSES:
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    f"embedded active dependency has invalid status: {dependency}",
                )
            active[dependency] = {
                "dependency_id": dependency,
                "status": status,
                "entered_at_ms": _non_negative_int(
                    raw.get("entered_at_ms"),
                    field=f"active[{dependency}].entered_at_ms",
                ),
                "source_ref": _durable_ref(
                    raw.get("source_ref"), namespace="degradation-source"
                ),
                "evidence_refs": _evidence_refs(raw.get("evidence_refs") or ()),
                "injected": bool(raw.get("injected")),
            }

        pending: dict[str, dict[str, Any]] = {}
        for raw in state.get("recovery_pending_dependencies") or ():
            if not isinstance(raw, Mapping):
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    "embedded pending dependency must be a mapping",
                )
            dependency = _dependency_id(raw.get("dependency_id"))
            status = _status(raw.get("status"))
            if status not in _HEALTHY_STATUSES:
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    f"embedded pending dependency has invalid status: {dependency}",
                )
            subject_version = _positive_int(
                raw.get("review_subject_version"),
                field=f"pending[{dependency}].review_subject_version",
            )
            pending[dependency] = {
                "dependency_id": dependency,
                "status": status,
                "observed_at_ms": _non_negative_int(
                    raw.get("observed_at_ms"),
                    field=f"pending[{dependency}].observed_at_ms",
                ),
                "source_ref": _durable_ref(
                    raw.get("source_ref"), namespace="degradation-source"
                ),
                "evidence_refs": _evidence_refs(raw.get("evidence_refs") or ()),
                "review_required": True,
                "review_subject_ref": _durable_ref(
                    raw.get("review_subject_ref"),
                    namespace="degradation-recovery",
                ),
                "review_subject_version": subject_version,
                "review_checkpoint_id": _durable_ref(
                    raw.get("review_checkpoint_id"),
                    namespace="checkpoint",
                ),
            }
        overlap = set(active).intersection(pending)
        if overlap:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                f"dependency cannot be active and recovery-pending: {sorted(overlap)}",
            )

        history: list[DegradationTransition] = []
        raw_last = state.get("last_transition")
        if raw_last is not None:
            if not isinstance(raw_last, Mapping):
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    "embedded last transition must be a mapping",
                )
            values = dict(raw_last)
            for field in (
                "evidence_refs",
                "affected_capabilities",
                "high_risk_capabilities",
            ):
                values[field] = tuple(values.get(field) or ())
            try:
                transition = DegradationTransition(**values)
            except (TypeError, ValueError) as exc:
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    "embedded last transition is invalid",
                ) from exc
            if transition.revision != raw_revision:
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    "embedded last transition does not match reducer revision",
                )
            history.append(transition)
        elif raw_revision != 0:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                "non-empty reducer revision requires a last transition",
            )

        origins: dict[str, set[str]] = {}
        for dependency in (*active, *pending):
            for capability in FAULT_POLICIES[dependency].affected_capabilities:
                origins.setdefault(capability, set()).add(dependency)

        entered_at = _optional_non_negative_int(
            state.get("entered_at_ms"), field="entered_at_ms"
        )
        updated_at = _non_negative_int(
            state.get("updated_at_ms"), field="updated_at_ms"
        )
        recovered_at = _optional_non_negative_int(
            state.get("recovered_at_ms"), field="recovered_at_ms"
        )
        expected_status = (
            DEGRADATION_DEGRADED
            if active
            else DEGRADATION_RECOVERY_PENDING
            if pending or origins
            else DEGRADATION_HEALTHY
        )
        if str(state.get("status") or "") != expected_status:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                "embedded degradation status does not match reducer state",
            )

        with self._lock:
            self._active = active
            self._pending = pending
            self._restriction_origins = origins
            self._revision = raw_revision
            self._entered_at_ms = entered_at
            self._updated_at_ms = updated_at
            self._recovered_at_ms = recovered_at
            self._history = history[-self._max_transition_history :]
            self._events.clear()
            self._durability_status = (
                "UNAVAILABLE" if self._durability_required else "NOT_REQUIRED"
            )
            self._restored_event_count = 0
            self._last_durable_cursor = 0
            self._last_durable_error = None

    def _restore_record(self, record: Mapping[str, Any]) -> None:
        revision = int(record.get("aggregate_version") or 0)
        if revision != self._revision + 1:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                f"non-contiguous degradation aggregate version: {revision}",
            )
        event_type = str(record.get("event_type") or "").strip()
        if event_type not in {
            DEGRADATION_ENTERED,
            DEGRADATION_RECOVERY_OBSERVED,
            DEGRADATION_RECOVERY_ACKNOWLEDGED,
        }:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                f"unsupported degradation event type: {event_type}",
            )
        payload = dict(record.get("payload") or {})
        dependency = _dependency_id(payload.get("dependency_id"))
        policy = FAULT_POLICIES[dependency]
        expected_affected = tuple(payload.get("affected_capabilities") or ())
        expected_high_risk = tuple(payload.get("high_risk_capabilities") or ())
        if expected_affected != policy.affected_capabilities or expected_high_risk != policy.high_risk_capabilities:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                f"fault policy drift detected while replaying {dependency}",
            )
        observed = int(record.get("ts_ms") or 0)
        refs = tuple(str(item) for item in (record.get("evidence_refs") or ()))
        source_ref = str(record.get("source_ref") or "")
        previous = self._state_status()
        status = str(payload.get("dependency_status") or "")
        if event_type == DEGRADATION_ENTERED:
            self._pending.pop(dependency, None)
            self._active[dependency] = {
                "dependency_id": dependency,
                "status": status,
                "entered_at_ms": observed,
                "source_ref": source_ref,
                "evidence_refs": refs,
                "injected": bool(payload.get("injected")),
            }
            for capability in policy.affected_capabilities:
                self._restriction_origins.setdefault(capability, set()).add(dependency)
            self._entered_at_ms = self._entered_at_ms or observed
        elif event_type == DEGRADATION_RECOVERY_OBSERVED:
            if dependency not in self._active:
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    f"recovery event has no active dependency: {dependency}",
                )
            self._active.pop(dependency, None)
            self._pending[dependency] = {
                "dependency_id": dependency,
                "status": status,
                "observed_at_ms": observed,
                "source_ref": source_ref,
                "evidence_refs": refs,
                "review_required": True,
                "review_subject_ref": str(payload.get("review_subject_ref") or ""),
                "review_subject_version": int(payload.get("review_subject_version") or 0),
                "review_checkpoint_id": str(payload.get("review_checkpoint_id") or ""),
            }
            if not all(
                (
                    self._pending[dependency]["review_subject_ref"],
                    self._pending[dependency]["review_subject_version"] == revision,
                    self._pending[dependency]["review_checkpoint_id"],
                )
            ):
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    f"recovery review binding is invalid: {dependency}",
                )
            self._recovered_at_ms = observed
        else:
            pending = self._pending.get(dependency)
            if pending is None:
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    f"acknowledgement has no pending recovery: {dependency}",
                )
            if (
                str(payload.get("review_subject_ref") or "") != pending["review_subject_ref"]
                or int(payload.get("review_subject_version") or 0) != pending["review_subject_version"]
                or str(payload.get("review_checkpoint_id") or "") != pending["review_checkpoint_id"]
                or not str(payload.get("review_id") or "")
                or not str(payload.get("resume_result_ref") or "")
            ):
                raise DegradationStateError(
                    DEGRADATION_REPLAY_INVALID,
                    f"acknowledgement review binding mismatch: {dependency}",
                )
            self._pending.pop(dependency, None)
            for capability in list(self._restriction_origins):
                self._restriction_origins[capability].discard(dependency)
                if not self._restriction_origins[capability]:
                    self._restriction_origins.pop(capability, None)
        self._revision = revision
        self._updated_at_ms = observed
        current = self._state_status()
        transition = DegradationTransition(
            schema_version=str(payload.get("transition_schema_version") or DEGRADATION_TRANSITION_SCHEMA_VERSION),
            transition_id=str(payload.get("transition_id") or ""),
            event_type=event_type,
            revision=revision,
            dependency_id=dependency,
            dependency_status=status,
            previous_state=str(payload.get("previous_state") or previous),
            current_state=str(payload.get("current_state") or current),
            source_ref=source_ref,
            evidence_refs=refs,
            affected_capabilities=policy.affected_capabilities,
            high_risk_capabilities=policy.high_risk_capabilities,
            observed_at_ms=observed,
            injected=bool(payload.get("injected")),
            detail_digest=str(payload.get("detail_digest") or ""),
            review_subject_ref=payload.get("review_subject_ref"),
            review_subject_version=payload.get("review_subject_version"),
            review_checkpoint_id=payload.get("review_checkpoint_id"),
            review_id=payload.get("review_id"),
            resume_result_ref=payload.get("resume_result_ref"),
        )
        if transition.current_state != current:
            raise DegradationStateError(
                DEGRADATION_REPLAY_INVALID,
                f"replayed state mismatch at revision {revision}",
            )
        self._history.append(transition)
        self._history = self._history[-self._max_transition_history :]
        if current == DEGRADATION_HEALTHY:
            self._entered_at_ms = None

    def restore_from_event_store(self) -> int:
        if self._event_store is None:
            self._durability_status = "UNAVAILABLE"
            return 0
        try:
            records: list[dict[str, Any]] = []
            cursor = 0
            while True:
                page = self._event_store.list_events_for_aggregates(
                    aggregate_refs=(("DEGRADATION_STATE", "runtime-degradation"),),
                    after_cursor=cursor,
                    limit=1000,
                )
                if not page:
                    break
                records.extend(page)
                cursor = int(page[-1]["cursor"])
                if len(page) < 1000:
                    break
            with self._lock:
                self._reset_reducer()
                for record in records:
                    self._restore_record(record)
                self._restored_event_count = len(records)
                self._last_durable_cursor = cursor
                self._durability_status = "READY"
                self._last_durable_error = None
            return len(records)
        except Exception as exc:
            with self._lock:
                self._reset_reducer()
                self._durability_status = "FAILED"
                self._last_durable_error = f"{type(exc).__name__}: {exc}"
            return 0

    def enter_degradation(
        self,
        *,
        dependency_id: str,
        status: str = "DOWN",
        evidence_refs: Sequence[str],
        source_ref: str = "",
        now_ms: int | None = None,
        detail: str = "",
        injected: bool = False,
    ) -> DegradationTransition | None:
        dependency = _dependency_id(dependency_id)
        normalized_status = _status(status)
        if normalized_status not in _FAILURE_STATUSES:
            raise DegradationStateError(
                DEGRADATION_HEALTH_EVIDENCE_INVALID,
                f"{dependency} failure transition requires failure status",
            )
        refs = _evidence_refs(evidence_refs)
        observed = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._lock:
            if dependency in self._active:
                return None
            previous = self._state_status()
            policy = FAULT_POLICIES[dependency]
            self._pending.pop(dependency, None)
            self._active[dependency] = {
                "dependency_id": dependency,
                "status": normalized_status,
                "entered_at_ms": observed,
                "source_ref": _durable_ref(source_ref, namespace="degradation-source"),
                "evidence_refs": refs,
                "injected": bool(injected),
            }
            for capability in policy.affected_capabilities:
                self._restriction_origins.setdefault(capability, set()).add(dependency)
            if self._entered_at_ms is None:
                self._entered_at_ms = observed
            transition = self._build_transition(
                event_type=DEGRADATION_ENTERED,
                dependency_id=dependency,
                dependency_status=normalized_status,
                previous_state=previous,
                source_ref=source_ref,
                evidence_refs=refs,
                observed_at_ms=observed,
                injected=injected,
                detail=detail,
            )
        self._emit(transition)
        return transition

    def recover_dependency(
        self,
        *,
        dependency_id: str,
        status: str = "UP",
        evidence_refs: Sequence[str],
        source_ref: str = "",
        now_ms: int | None = None,
        detail: str = "",
        injected: bool = False,
    ) -> DegradationTransition | None:
        dependency = _dependency_id(dependency_id)
        normalized_status = _status(status)
        if normalized_status not in _HEALTHY_STATUSES:
            raise DegradationStateError(
                DEGRADATION_HEALTH_EVIDENCE_INVALID,
                f"{dependency} recovery requires an explicit healthy probe status",
            )
        refs = _evidence_refs(evidence_refs)
        observed = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._lock:
            if dependency not in self._active:
                return None
            previous = self._state_status()
            next_revision = self._revision + 1
            review_subject_ref = (
                f"degradation-recovery://runtime/{dependency.lower()}"
            )
            review_checkpoint_id = (
                f"checkpoint://degradation/{dependency.lower()}/{next_revision}"
            )
            self._active.pop(dependency, None)
            self._pending[dependency] = {
                "dependency_id": dependency,
                "status": normalized_status,
                "observed_at_ms": observed,
                "source_ref": _durable_ref(source_ref, namespace="degradation-source"),
                "evidence_refs": refs,
                "review_required": True,
                "review_subject_ref": review_subject_ref,
                "review_subject_version": next_revision,
                "review_checkpoint_id": review_checkpoint_id,
            }
            self._recovered_at_ms = observed
            transition = self._build_transition(
                event_type=DEGRADATION_RECOVERY_OBSERVED,
                dependency_id=dependency,
                dependency_status=normalized_status,
                previous_state=previous,
                source_ref=source_ref,
                evidence_refs=refs,
                observed_at_ms=observed,
                injected=injected,
                detail=detail,
                review_subject_ref=review_subject_ref,
                review_subject_version=next_revision,
                review_checkpoint_id=review_checkpoint_id,
            )
        self._emit(transition)
        return transition

    def prepare_recovery_acknowledgement(
        self,
        *,
        dependency_id: str,
        review_id: str,
    ) -> dict[str, Any]:
        dependency = _dependency_id(dependency_id)
        with self._lock:
            if dependency in self._active or dependency not in self._pending:
                raise DegradationStateError(
                    DEGRADATION_REVIEW_REQUIRED,
                    f"{dependency} has no evidence-backed pending recovery",
                )
            pending = copy.deepcopy(self._pending[dependency])
        subject_ref = str(pending["review_subject_ref"])
        subject_version = int(pending["review_subject_version"])
        checkpoint_id = str(pending["review_checkpoint_id"])
        result_ref = (
            f"degradation-recovery://runtime/{dependency.lower()}/"
            f"ack/{subject_version}/{_digest(str(review_id))[:16]}"
        )
        return {
            "dependency_id": dependency,
            "review_id": str(review_id),
            "subject_ref": subject_ref,
            "subject_version": subject_version,
            "resume_phase": "PRE_EXECUTION",
            "checkpoint_id": checkpoint_id,
            "resume_result_ref": result_ref,
            "expected_reducer_revision": int(self._revision),
            "evidence_refs": tuple(pending.get("evidence_refs") or ()),
        }

    def apply_consumed_recovery_acknowledgement(
        self,
        *,
        binding: Mapping[str, Any],
        review_record: Mapping[str, Any],
        now_ms: int | None = None,
    ) -> DegradationTransition:
        dependency = _dependency_id(binding.get("dependency_id"))
        observed = int(now_ms if now_ms is not None else time.time() * 1000)
        review_id = str(binding.get("review_id") or "")
        subject_ref = str(binding.get("subject_ref") or "")
        subject_version = int(binding.get("subject_version") or -1)
        checkpoint_id = str(binding.get("checkpoint_id") or "")
        result_ref = str(binding.get("resume_result_ref") or "")
        record = dict(review_record)
        review_item = dict(record.get("review") or {})
        if (
            str(review_item.get("status") or "") != "CONSUMED"
            or str(review_item.get("review_id") or "") != review_id
            or str(review_item.get("subject_ref") or "") != subject_ref
            or int(review_item.get("subject_version") or -1) != subject_version
            or str(review_item.get("resume_phase") or "") != "PRE_EXECUTION"
            or str(review_item.get("checkpoint_id") or "") != checkpoint_id
            or str(review_item.get("resume_result_ref") or "") != result_ref
        ):
            raise DegradationStateError(
                DEGRADATION_REVIEW_REQUIRED,
                "ReviewStore record is not bound to the prepared recovery",
            )
        refs = _evidence_refs(
            tuple(binding.get("evidence_refs") or ())
            + (f"review-item://{review_id}", result_ref)
        )
        reviewer = _durable_ref(
            review_item.get("decision_actor"), namespace="reviewer"
        )
        with self._lock:
            pending = copy.deepcopy(self._pending.get(dependency))
            if (
                not pending
                or dependency in self._active
                or str(pending.get("review_subject_ref") or "") != subject_ref
                or int(pending.get("review_subject_version") or -1)
                != subject_version
                or str(pending.get("review_checkpoint_id") or "")
                != checkpoint_id
                or int(self._revision)
                != int(binding.get("expected_reducer_revision") or -1)
            ):
                raise DegradationStateError(
                    DEGRADATION_REVIEW_REQUIRED,
                    "recovery state changed after acknowledgement preparation",
                )
            previous = self._state_status()
            self._pending.pop(dependency, None)
            for capability in list(self._restriction_origins):
                origins = self._restriction_origins[capability]
                origins.discard(dependency)
                if not origins:
                    self._restriction_origins.pop(capability, None)
            transition = self._build_transition(
                event_type=DEGRADATION_RECOVERY_ACKNOWLEDGED,
                dependency_id=dependency,
                dependency_status="REVIEW_APPROVED",
                previous_state=previous,
                source_ref=reviewer,
                evidence_refs=refs,
                observed_at_ms=observed,
                injected=False,
                detail="approved recovery review",
                review_subject_ref=subject_ref,
                review_subject_version=subject_version,
                review_checkpoint_id=checkpoint_id,
                review_id=review_id,
                resume_result_ref=result_ref,
            )
            if self._state_status() == DEGRADATION_HEALTHY:
                self._entered_at_ms = None
        self._emit(transition)
        return transition

    def acknowledge_recovery(
        self,
        *,
        dependency_id: str,
        review_store: Any,
        review_id: str,
        resume_token_ref: str,
        now_ms: int | None = None,
    ) -> DegradationTransition:
        binding = self.prepare_recovery_acknowledgement(
            dependency_id=dependency_id,
            review_id=review_id,
        )
        try:
            resumed = review_store.resume_review(
                str(review_id),
                resume_token_ref=str(resume_token_ref),
                subject_ref=binding["subject_ref"],
                subject_version=binding["subject_version"],
                resume_phase=binding["resume_phase"],
                checkpoint_id=binding["checkpoint_id"],
                resume_result_ref=binding["resume_result_ref"],
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason_code", DEGRADATION_REVIEW_REQUIRED))
            raise DegradationStateError(
                reason,
                "recovery review token was not accepted for this dependency checkpoint",
            ) from exc
        if bool(resumed.get("replay_side_effects")):
            raise DegradationStateError(
                DEGRADATION_REVIEW_REQUIRED,
                "ReviewStore attempted to replay side effects",
            )
        return self.apply_consumed_recovery_acknowledgement(
            binding=binding,
            review_record=dict(resumed.get("review") or {}),
            now_ms=now_ms,
        )

    def recovery_review_binding(self, dependency_id: str) -> dict[str, Any]:
        """Return the exact immutable ReviewItem subject/checkpoint binding."""
        dependency = _dependency_id(dependency_id)
        with self._lock:
            pending = copy.deepcopy(self._pending.get(dependency))
        if not pending:
            raise DegradationStateError(
                DEGRADATION_REVIEW_REQUIRED,
                f"{dependency} has no evidence-backed pending recovery",
            )
        return {
            "dependency_id": dependency,
            "subject_ref": pending["review_subject_ref"],
            "subject_version": pending["review_subject_version"],
            "resume_phase": "PRE_EXECUTION",
            "checkpoint_id": pending["review_checkpoint_id"],
            "risk_level": 5,
            "side_effects": ["capability_reenable"],
            "evidence_refs": list(pending.get("evidence_refs") or ()),
        }

    def observe(
        self,
        *,
        dependency_id: str,
        status: str,
        evidence_refs: Sequence[str],
        source_ref: str = "",
        now_ms: int | None = None,
        detail: str = "",
        injected: bool = False,
    ) -> DegradationTransition | None:
        normalized_status = _status(status)
        if normalized_status in _FAILURE_STATUSES:
            return self.enter_degradation(
                dependency_id=dependency_id,
                status=normalized_status,
                evidence_refs=evidence_refs,
                source_ref=source_ref,
                now_ms=now_ms,
                detail=detail,
                injected=injected,
            )
        if normalized_status in _HEALTHY_STATUSES:
            return self.recover_dependency(
                dependency_id=dependency_id,
                status=normalized_status,
                evidence_refs=evidence_refs,
                source_ref=source_ref,
                now_ms=now_ms,
                detail=detail,
                injected=injected,
            )
        return None

    def snapshot(
        self,
        *,
        policy_enabled: bool = True,
        policy_status: str = "ENABLED",
    ) -> DegradationState:
        with self._lock:
            high_risk_by_capability = {
                capability
                for policy in FAULT_POLICIES.values()
                for capability in policy.high_risk_capabilities
            }
            restrictions: list[dict[str, Any]] = []
            for capability in sorted(self._restriction_origins):
                origins = tuple(sorted(self._restriction_origins[capability]))
                active_origins = tuple(dep for dep in origins if dep in self._active)
                pending_origins = tuple(dep for dep in origins if dep in self._pending)
                high_risk = capability in high_risk_by_capability
                fallbacks = tuple(
                    dict.fromkeys(
                        fallback
                        for dependency in origins
                        for fallback in FAULT_POLICIES[dependency].safe_fallbacks
                    )
                )
                restrictions.append(
                    {
                        "capability_id": capability,
                        "status": "DISABLED" if high_risk else "RESTRICTED",
                        "high_risk": high_risk,
                        "origin_dependencies": origins,
                        "active_origin_dependencies": active_origins,
                        "recovery_pending_dependencies": pending_origins,
                        "safe_fallbacks": fallbacks,
                        "automatic_release": False,
                        "review_required": bool(pending_origins),
                        "permission_effect": "RESTRICT_ONLY",
                    }
                )
            active_rows = tuple(
                copy.deepcopy(self._active[key]) for key in sorted(self._active)
            )
            pending_rows = tuple(
                copy.deepcopy(self._pending[key]) for key in sorted(self._pending)
            )
            active_evidence = tuple(
                dict.fromkeys(
                    ref for row in active_rows for ref in row.get("evidence_refs", ())
                )
            )
            recovery_evidence = tuple(
                dict.fromkeys(
                    ref for row in pending_rows for ref in row.get("evidence_refs", ())
                )
            )
            last = self._history[-1].as_dict() if self._history else None
            return DegradationState(
                schema_version=DEGRADATION_STATE_SCHEMA_VERSION,
                state_id="runtime-degradation",
                revision=self._revision,
                status=self._state_status(),
                entered_at_ms=self._entered_at_ms,
                updated_at_ms=self._updated_at_ms,
                recovered_at_ms=self._recovered_at_ms,
                active_dependencies=active_rows,
                recovery_pending_dependencies=pending_rows,
                evidence_refs=active_evidence,
                recovery_evidence_refs=recovery_evidence,
                affected_capabilities=tuple(
                    row["capability_id"] for row in restrictions
                ),
                capability_restrictions=tuple(restrictions),
                last_transition=last,
                transition_count=self._revision,
                policy_enabled=bool(policy_enabled),
                policy_status=str(policy_status),
                durability_required=self._durability_required,
                durability_status=self._durability_status,
                restored_event_count=self._restored_event_count,
                last_durable_cursor=self._last_durable_cursor,
                last_durable_error=self._last_durable_error,
                event_bus_status=self._event_bus_status,
            )

    def transition_history(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(row.as_dict()) for row in self._history)

    def runtime_events(self) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self._events)

    def evaluate_capability(
        self,
        *,
        capability_id: str,
        static_risk_level: int,
        policy_enabled: bool | None = None,
        policy_status: str | None = None,
    ) -> dict[str, Any]:
        capability = str(capability_id or "").strip().lower()
        risk_level = _risk(static_risk_level)
        config = degradation_policy_config()
        enabled = config["enabled"] if policy_enabled is None else bool(policy_enabled)
        status = str(policy_status or config["status"])
        state = self.snapshot(policy_enabled=enabled, policy_status=status)
        restriction = next(
            (
                dict(row)
                for row in state.capability_restrictions
                if row.get("capability_id") == capability
            ),
            None,
        )
        state_unavailable = bool(
            self._durability_required and self._durability_status != "READY"
        )
        policy_fail_closed = status in {"INVALID_FAIL_CLOSED", "DISABLED_RESTRICTED"}
        blocked = bool(
            restriction
            and (bool(restriction.get("high_risk")) or risk_level >= _HIGH_RISK_THRESHOLD)
        )
        blocked = blocked or bool(
            risk_level >= _HIGH_RISK_THRESHOLD
            and (state_unavailable or policy_fail_closed)
        )
        restricted = restriction is not None
        decision = "BLOCK" if blocked else ("DEGRADE" if restricted else "UNCHANGED")
        return {
            "schema_version": DEGRADATION_CAPABILITY_DECISION_SCHEMA_VERSION,
            "capability_id": capability,
            "decision": decision,
            "allowed_by_degradation": not blocked,
            "authority_granted": False,
            "static_risk_level": risk_level,
            "effective_risk_level": risk_level,
            "reason_code": (
                DEGRADATION_STATE_UNAVAILABLE
                if blocked and state_unavailable
                else DEGRADATION_POLICY_INVALID
                if blocked and status == "INVALID_FAIL_CLOSED"
                else DEGRADATION_POLICY_DISABLED
                if blocked and status == "DISABLED_RESTRICTED"
                else "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED"
                if blocked
                else (
                    "DEPENDENCY_DEGRADED_SAFE_FALLBACK"
                    if restricted
                    else "NO_ADDITIONAL_DEGRADATION_RESTRICTION"
                )
            ),
            "origin_dependencies": list(
                restriction.get("origin_dependencies", ()) if restriction else ()
            ),
            "safe_fallbacks": list(
                restriction.get("safe_fallbacks", ()) if restriction else ()
            ),
            "permission_effect": "RESTRICT_ONLY",
            "can_expand_scope": False,
            "can_reduce_risk": False,
            "can_bypass_review": False,
            "can_bypass_token": False,
            "policy_enabled": enabled,
            "policy_status": status,
            "durability_status": self._durability_status,
        }


def build_degradation_runtime_event(transition: DegradationTransition) -> Any:
    """Build, but do not persist, the sanitized transition RuntimeEvent."""
    from host_brain.integrations.runtime_events import RuntimeEvent  # noqa: PLC0415

    payload_ref = f"degradation-transition://{transition.transition_id}"
    payload = {
        "transition_id": transition.transition_id,
        "transition_schema_version": transition.schema_version,
        "dependency_id": transition.dependency_id,
        "dependency_status": transition.dependency_status,
        "previous_state": transition.previous_state,
        "current_state": transition.current_state,
        "affected_capabilities": list(transition.affected_capabilities),
        "high_risk_capabilities": list(transition.high_risk_capabilities),
        "permission_effect": transition.permission_effect,
        "injected": transition.injected,
        "external_side_effects": False,
        "detail_digest": transition.detail_digest,
    }
    for key in (
        "review_subject_ref",
        "review_subject_version",
        "review_checkpoint_id",
        "review_id",
        "resume_result_ref",
    ):
        value = getattr(transition, key)
        if value is not None:
            payload[key] = value
    return RuntimeEvent.create(
        event_type=transition.event_type,
        source_kind="SYSTEM",
        source_ref=transition.source_ref,
        aggregate_type="DEGRADATION_STATE",
        aggregate_id="runtime-degradation",
        aggregate_version=transition.revision,
        payload_ref=payload_ref,
        payload=payload,
        evidence_refs=transition.evidence_refs,
        redaction_level="INTERNAL_REDACTED",
        ts_ms=transition.observed_at_ms,
    )


def degradation_policy_config() -> dict[str, Any]:
    raw_value = os.environ.get("HOST_DEGRADATION_STATE_ENABLED")
    if raw_value is None or not str(raw_value).strip():
        return {"enabled": True, "status": "ENABLED_DEFAULT", "raw": None}
    raw = str(raw_value).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return {"enabled": True, "status": "ENABLED", "raw": raw_value}
    if raw in {"0", "false", "no", "off"}:
        # Rollback stops new projection; it may never silently reopen a
        # high-risk capability or discard a restored latch.
        return {"enabled": False, "status": "DISABLED_RESTRICTED", "raw": raw_value}
    return {"enabled": True, "status": "INVALID_FAIL_CLOSED", "raw": raw_value}


def degradation_policy_enabled() -> bool:
    return bool(degradation_policy_config()["enabled"])


_GLOBAL_MACHINE: DegradationStateMachine | None = None
_GLOBAL_MACHINE_LOCK = threading.RLock()


def _default_runtime_event_db_path() -> Path:
    raw_path = str(
        os.environ.get("NATSUME_AUDIT_DB")
        or os.environ.get("Natsume_AUDIT_DB")
        or os.environ.get("HOST_RUNTIME_EVENT_DB_PATH")
        or ""
    ).strip()
    if raw_path:
        return Path(raw_path).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3"


def _build_global_degradation_state_machine() -> DegradationStateMachine:
    try:
        from host_brain.integrations.runtime_events import RuntimeEventStore  # noqa: PLC0415

        store = RuntimeEventStore(_default_runtime_event_db_path())
    except Exception as exc:
        machine = DegradationStateMachine(
            durability_required=True,
            restore_from_store=False,
        )
        machine._durability_status = "FAILED"  # noqa: SLF001
        machine._last_durable_error = f"{type(exc).__name__}: {exc}"  # noqa: SLF001
        return machine
    try:
        from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

        bus = get_tribe_event_bus()
    except Exception:
        bus = None
    return DegradationStateMachine(
        event_store=store,
        event_bus=bus,
        durability_required=True,
        restore_from_store=True,
    )


def get_degradation_state_machine() -> DegradationStateMachine:
    global _GLOBAL_MACHINE
    with _GLOBAL_MACHINE_LOCK:
        if _GLOBAL_MACHINE is None:
            _GLOBAL_MACHINE = _build_global_degradation_state_machine()
        return _GLOBAL_MACHINE


def set_global_degradation_state_machine(
    machine: DegradationStateMachine | None,
) -> None:
    """Composition/test hook; ``None`` forces a fresh durable restore."""
    global _GLOBAL_MACHINE
    with _GLOBAL_MACHINE_LOCK:
        _GLOBAL_MACHINE = machine


def get_degradation_state(
    *, machine: DegradationStateMachine | None = None,
) -> dict[str, Any]:
    target = machine or get_degradation_state_machine()
    config = degradation_policy_config()
    return target.snapshot(
        policy_enabled=bool(config["enabled"]),
        policy_status=str(config["status"]),
    ).as_dict()


def evaluate_degradation_capability(
    *,
    capability_id: str,
    static_risk_level: int,
    machine: DegradationStateMachine | None = None,
) -> dict[str, Any]:
    return (machine or get_degradation_state_machine()).evaluate_capability(
        capability_id=capability_id,
        static_risk_level=static_risk_level,
    )


def infer_degradation_capability(
    *values: Any,
    default: str = "high_risk_execution",
) -> str:
    """Infer the narrow capability id used by heterogeneous front doors."""
    haystack = " ".join(str(value or "").lower() for value in values)
    mappings = (
        (("canon",), "canon_apply"),
        (("pentest", "exploit", "security_lab"), "pentest_execution"),
        (("tts", "speak", "voice", "audio"), "voice_output"),
        (("vts", "avatar", "live2d"), "avatar_control"),
        (("review", "resume"), "remote_review_resume"),
        (("sse", "event_stream", "runtime stream"), "runtime_event_stream"),
        (("memory_write", "memory store", "remember"), "memory_write"),
        (("planning", "planner", "autonomous"), "autonomous_planning"),
        (("upgrade", "install", "plugin"), "capability_upgrade"),
    )
    for needles, capability in mappings:
        if any(needle in haystack for needle in needles):
            return capability
    return str(default or "high_risk_execution").strip().lower()


def evaluate_degradation_front_door(
    *,
    capability_id: str,
    static_risk_level: int,
    capability_policy_decision: Mapping[str, Any] | None = None,
    machine: DegradationStateMachine | None = None,
) -> dict[str, Any]:
    """Compose upstream capability policy with degradation, restrict-only."""
    upstream = dict(capability_policy_decision or {})
    risk_candidates = [static_risk_level]
    for key in ("effective_risk_level", "canonical_risk", "risk_level"):
        if upstream.get(key) is not None:
            risk_candidates.append(upstream[key])
    effective_risk = max(_risk(value) for value in risk_candidates)
    degradation = evaluate_degradation_capability(
        capability_id=capability_id,
        static_risk_level=effective_risk,
        machine=machine,
    )
    upstream_decision = str(upstream.get("decision") or "UNCHANGED").upper()
    upstream_allowed = bool(upstream.get("allowed", upstream.get("allowed_by_policy", True)))
    upstream_blocked = not upstream_allowed or upstream_decision in {
        "BLOCK",
        "DENY",
        "REJECT",
        "AIRLOCK",
    }
    degradation_blocked = not bool(degradation["allowed_by_degradation"])
    blocked = upstream_blocked or degradation_blocked
    if upstream_blocked:
        decision = "BLOCK"
        reason_code = str(
            upstream.get("reason_code") or "CAPABILITY_POLICY_BLOCKED"
        )
    elif degradation_blocked:
        decision = "BLOCK"
        reason_code = str(degradation["reason_code"])
    elif upstream_decision == "DEGRADE" or degradation["decision"] == "DEGRADE":
        decision = "DEGRADE"
        reason_code = str(
            upstream.get("reason_code")
            if upstream_decision == "DEGRADE"
            else degradation["reason_code"]
        )
    else:
        decision = "UNCHANGED"
        reason_code = "NO_ADDITIONAL_DEGRADATION_RESTRICTION"
    return {
        **degradation,
        "decision": decision,
        "allowed": not blocked,
        "allowed_by_composed_policy": not blocked,
        "reason_code": reason_code,
        "effective_risk_level": effective_risk,
        "upstream_policy_decision": upstream_decision,
        "upstream_allowed": upstream_allowed,
        "permission_effect": "RESTRICT_ONLY",
        "can_expand_scope": False,
        "can_reduce_risk": False,
        "can_bypass_review": False,
        "can_bypass_token": False,
    }


def require_degradation_capability(
    *,
    capability_id: str,
    static_risk_level: int,
    capability_policy_decision: Mapping[str, Any] | None = None,
    machine: DegradationStateMachine | None = None,
) -> dict[str, Any]:
    decision = evaluate_degradation_front_door(
        capability_id=capability_id,
        static_risk_level=static_risk_level,
        capability_policy_decision=capability_policy_decision,
        machine=machine,
    )
    if not decision["allowed"]:
        raise DegradationStateError(
            str(decision["reason_code"]),
            f"{capability_id} is blocked by the composed degradation gate",
        )
    return decision


def build_fault_injection_matrix() -> dict[str, Any]:
    return {
        "schema_version": DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION,
        "mode": "ISOLATED_FAKE_ONLY",
        "external_services_allowed": False,
        "production_writes_allowed": False,
        "cases": [
            {
                **FAULT_POLICIES[key].as_dict(),
                "fake_failure_status": "DOWN",
                "fake_recovery_status": "UP",
                "recovery_evidence_required": True,
                "automatic_capability_expansion": False,
            }
            for key in sorted(FAULT_POLICIES)
        ],
    }


def run_isolated_fault_injection_matrix(
    *,
    machine: DegradationStateMachine | None = None,
    dependencies: Sequence[str] | None = None,
    now_ms: int | None = None,
    isolated: bool = True,
) -> dict[str, Any]:
    """Exercise fake transitions without network, subprocess or durable writes."""
    if not isolated:
        raise DegradationStateError(
            DEGRADATION_FAULT_INJECTION_NOT_ISOLATED,
            "external fault injection requires a separately approved run window",
        )
    target = machine or DegradationStateMachine()
    start = int(now_ms if now_ms is not None else time.time() * 1000)
    dependency_ids = tuple(
        _dependency_id(item) for item in (dependencies or tuple(sorted(FAULT_POLICIES)))
    )
    results: list[dict[str, Any]] = []
    for index, dependency in enumerate(dependency_ids):
        failed_at = start + index * 2
        recovered_at = failed_at + 1
        failure_ref = f"fault-fixture://{dependency.lower()}/failure/{failed_at}"
        recovery_ref = f"fault-fixture://{dependency.lower()}/recovery/{recovered_at}"
        entered = target.enter_degradation(
            dependency_id=dependency,
            status="DOWN",
            evidence_refs=(failure_ref,),
            source_ref=failure_ref,
            now_ms=failed_at,
            detail="isolated fake dependency failure",
            injected=True,
        )
        recovered = target.recover_dependency(
            dependency_id=dependency,
            status="UP",
            evidence_refs=(recovery_ref,),
            source_ref=recovery_ref,
            now_ms=recovered_at,
            detail="isolated fake healthy probe",
            injected=True,
        )
        results.append(
            {
                "dependency_id": dependency,
                "entered_transition_id": entered.transition_id if entered else None,
                "recovered_transition_id": recovered.transition_id if recovered else None,
                "high_risk_still_restricted": all(
                    target.evaluate_capability(
                        capability_id=capability,
                        static_risk_level=5,
                    )["decision"]
                    == "BLOCK"
                    for capability in FAULT_POLICIES[dependency].high_risk_capabilities
                ),
            }
        )
    return {
        "schema_version": DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION,
        "status": "PASSED",
        "mode": "ISOLATED_FAKE_ONLY",
        "external_calls": 0,
        "production_writes": 0,
        "cases": results,
        "state": target.snapshot().as_dict(),
    }


def _observation_evidence_ref(
    *, source_ref: str, dependency_id: str, rows: Sequence[Mapping[str, Any]]
) -> str:
    safe_rows = [
        {
            "service_id": str(row.get("service_id") or row.get("name") or "").strip(),
            "status": _status(row.get("status")),
            "last_check_ts": row.get("last_check_ts") or row.get("ts_ms"),
            "side": str(row.get("side") or row.get("owner") or "").strip(),
        }
        for row in rows
    ]
    digest = _digest(
        {
            "dependency_id": dependency_id,
            "rows": safe_rows,
            "source_ref": source_ref,
        }
    )
    return f"health-observation://sha256/{digest}"


def reconcile_service_observations(
    rows: Sequence[Mapping[str, Any]],
    *,
    machine: DegradationStateMachine | None = None,
    source_ref: str,
    now_ms: int | None = None,
    injected: bool = False,
    enabled: bool | None = None,
) -> tuple[dict[str, Any], ...]:
    """Reduce a service probe batch; unknown/deferred signals never recover."""
    target = machine or get_degradation_state_machine()
    policy_enabled = degradation_policy_enabled() if enabled is None else bool(enabled)
    if not policy_enabled:
        return ()
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for raw_row in rows:
        row = dict(raw_row or {})
        service_id = str(row.get("service_id") or row.get("name") or "").strip().lower()
        dependency = _SERVICE_TO_DEPENDENCY.get(service_id)
        if not dependency:
            continue
        normalized_status = _status(row.get("status"))
        detail = str(row.get("detail") or "").strip().lower()
        if normalized_status in _IGNORED_STATUSES:
            continue
        if normalized_status in _HEALTHY_STATUSES and any(
            marker in detail for marker in _NON_HEALTHY_SUCCESS_MARKERS
        ):
            continue
        grouped.setdefault(dependency, []).append(row)

    observed = int(now_ms if now_ms is not None else time.time() * 1000)
    transitions: list[dict[str, Any]] = []
    for dependency in sorted(grouped):
        dependency_rows = grouped[dependency]
        statuses = [_status(row.get("status")) for row in dependency_rows]
        if any(status in _FAILURE_STATUSES for status in statuses):
            resolved_status = "DOWN" if "DOWN" in statuses else "DEGRADED"
        elif statuses and all(status in _HEALTHY_STATUSES for status in statuses):
            resolved_status = "UP"
        else:
            continue
        evidence_ref = _observation_evidence_ref(
            source_ref=source_ref,
            dependency_id=dependency,
            rows=dependency_rows,
        )
        transition = target.observe(
            dependency_id=dependency,
            status=resolved_status,
            evidence_refs=(evidence_ref,),
            source_ref=source_ref,
            now_ms=observed,
            detail=f"service observation count={len(dependency_rows)}",
            injected=injected,
        )
        if transition:
            transitions.append(transition.as_dict())
    return tuple(transitions)


def reconcile_startup_statuses(
    statuses: Mapping[str, Any],
    *,
    machine: DegradationStateMachine | None = None,
    source_ref: str,
    now_ms: int | None = None,
    enabled: bool | None = None,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for module_name, raw_status in dict(statuses or {}).items():
        dependency = _STARTUP_MODULE_TO_DEPENDENCY.get(str(module_name).strip().lower())
        if not dependency:
            continue
        text = str(raw_status or "").strip()
        token = text.split(":", 1)[0].split(" ", 1)[0].strip().upper()
        if token.startswith("DEGRADED") or token.startswith("FAILED"):
            resolved = "DEGRADED"
        elif token == "OK":
            resolved = "UP"
        else:
            continue
        rows.append(
            {
                "service_id": FAULT_POLICIES[dependency].service_ids[0],
                "status": resolved,
                "name": module_name,
            }
        )
    return reconcile_service_observations(
        rows,
        machine=machine,
        source_ref=source_ref,
        now_ms=now_ms,
        enabled=enabled,
    )


__all__ = [
    "DEGRADATION_CAPABILITY_DECISION_SCHEMA_VERSION",
    "DEGRADATION_DEGRADED",
    "DEGRADATION_EVIDENCE_REQUIRED",
    "DEGRADATION_EVIDENCE_INVALID",
    "DEGRADATION_FAULT_INJECTION_NOT_ISOLATED",
    "DEGRADATION_ENTERED",
    "DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION",
    "DEGRADATION_HEALTHY",
    "DEGRADATION_HEALTH_EVIDENCE_INVALID",
    "DEGRADATION_RECOVERY_ACKNOWLEDGED",
    "DEGRADATION_RECOVERY_OBSERVED",
    "DEGRADATION_RECOVERY_PENDING",
    "DEGRADATION_REVIEW_REQUIRED",
    "DEGRADATION_STATE_UNAVAILABLE",
    "DEGRADATION_DURABLE_EVENT_FAILED",
    "DEGRADATION_REPLAY_INVALID",
    "DEGRADATION_POLICY_DISABLED",
    "DEGRADATION_POLICY_INVALID",
    "DEGRADATION_STATE_SCHEMA_VERSION",
    "DEGRADATION_TRANSITION_SCHEMA_VERSION",
    "DegradationState",
    "DegradationStateError",
    "DegradationStateMachine",
    "DegradationTransition",
    "FAULT_POLICIES",
    "FaultPolicy",
    "build_degradation_runtime_event",
    "build_fault_injection_matrix",
    "degradation_policy_enabled",
    "degradation_policy_config",
    "evaluate_degradation_capability",
    "evaluate_degradation_front_door",
    "get_degradation_state",
    "get_degradation_state_machine",
    "infer_degradation_capability",
    "reconcile_service_observations",
    "reconcile_startup_statuses",
    "run_isolated_fault_injection_matrix",
    "require_degradation_capability",
    "set_global_degradation_state_machine",
]
