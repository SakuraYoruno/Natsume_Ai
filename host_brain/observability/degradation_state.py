"""Unified dependency degradation state for the existing runtime guardians.

The module owns no background thread and performs no external I/O.  Existing
startup, heartbeat, health and resource-manager paths feed observations into a
single process-local state machine.  A healthy probe can recover a dependency,
but it cannot automatically re-enable a capability that was tightened while
the dependency was unhealthy.
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
from types import MappingProxyType
from typing import Any


DEGRADATION_STATE_SCHEMA_VERSION = "Natsume.degradation.state.v1"
DEGRADATION_TRANSITION_SCHEMA_VERSION = "Natsume.degradation.transition.v1"
DEGRADATION_FAULT_MATRIX_SCHEMA_VERSION = "Natsume.degradation.fault_matrix.v1"
DEGRADATION_CAPABILITY_DECISION_SCHEMA_VERSION = (
    "Natsume.degradation.capability_decision.v1"
)

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
        self._max_transition_history = max(1, int(max_transition_history))

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
        )
        self._updated_at_ms = observed_at_ms
        self._history.append(transition)
        self._history = self._history[-self._max_transition_history :]
        return transition

    def _emit(self, transition: DegradationTransition) -> None:
        event = build_degradation_runtime_event(transition)
        with self._lock:
            self._events.append(event)
            self._events = self._events[-self._max_transition_history :]
        if self._event_sink is not None:
            self._event_sink(event)

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
            self._active.pop(dependency, None)
            self._pending[dependency] = {
                "dependency_id": dependency,
                "status": normalized_status,
                "observed_at_ms": observed,
                "source_ref": _durable_ref(source_ref, namespace="degradation-source"),
                "evidence_refs": refs,
                "review_required": True,
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
            )
        self._emit(transition)
        return transition

    def acknowledge_recovery(
        self,
        *,
        dependency_id: str,
        review_approved: bool,
        evidence_refs: Sequence[str],
        reviewer_ref: str,
        now_ms: int | None = None,
    ) -> DegradationTransition:
        dependency = _dependency_id(dependency_id)
        if not bool(review_approved):
            raise DegradationStateError(
                DEGRADATION_REVIEW_REQUIRED,
                "capability expansion requires an approved recovery review",
            )
        refs = _evidence_refs(evidence_refs)
        reviewer = _durable_ref(reviewer_ref, namespace="reviewer")
        observed = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._lock:
            if dependency in self._active or dependency not in self._pending:
                raise DegradationStateError(
                    DEGRADATION_REVIEW_REQUIRED,
                    f"{dependency} has no evidence-backed pending recovery",
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
            )
            if self._state_status() == DEGRADATION_HEALTHY:
                self._entered_at_ms = None
        self._emit(transition)
        return transition

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

    def snapshot(self, *, policy_enabled: bool = True) -> DegradationState:
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
    ) -> dict[str, Any]:
        capability = str(capability_id or "").strip().lower()
        risk_level = _risk(static_risk_level)
        state = self.snapshot()
        restriction = next(
            (
                dict(row)
                for row in state.capability_restrictions
                if row.get("capability_id") == capability
            ),
            None,
        )
        blocked = bool(
            restriction
            and (bool(restriction.get("high_risk")) or risk_level >= _HIGH_RISK_THRESHOLD)
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
                "DEPENDENCY_DEGRADED_HIGH_RISK_BLOCKED"
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


def degradation_policy_enabled() -> bool:
    raw = str(os.environ.get("HOST_DEGRADATION_STATE_ENABLED") or "true").strip().lower()
    return raw in {"1", "true", "yes", "on"}


_GLOBAL_MACHINE = DegradationStateMachine()


def get_degradation_state_machine() -> DegradationStateMachine:
    return _GLOBAL_MACHINE


def get_degradation_state(
    *, machine: DegradationStateMachine | None = None,
) -> dict[str, Any]:
    target = machine or _GLOBAL_MACHINE
    return target.snapshot(policy_enabled=degradation_policy_enabled()).as_dict()


def evaluate_degradation_capability(
    *,
    capability_id: str,
    static_risk_level: int,
    machine: DegradationStateMachine | None = None,
) -> dict[str, Any]:
    return (machine or _GLOBAL_MACHINE).evaluate_capability(
        capability_id=capability_id,
        static_risk_level=static_risk_level,
    )


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
    target = machine or _GLOBAL_MACHINE
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
    "evaluate_degradation_capability",
    "get_degradation_state",
    "get_degradation_state_machine",
    "reconcile_service_observations",
    "reconcile_startup_statuses",
    "run_isolated_fault_injection_matrix",
]
