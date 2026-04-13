"""State write arbiter with per-scope writer authorization."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping


class StateScope(str, Enum):
    """Logical write scopes for runtime state domains."""

    SOUL_STATE = "soul_state"
    RUNTIME_STATE = "runtime_state"
    MEMORY_STATE = "memory_state"
    EXECUTION_STATE = "execution_state"


STATE_WRITE_POLICY_VERSION = "1.0"
STATE_WRITE_DENY_REASON = "DENY_WRITE_SCOPE"

_DEFAULT_SCOPE_WRITERS: dict[StateScope, set[str]] = {
    StateScope.SOUL_STATE: {"persona_core"},
    StateScope.RUNTIME_STATE: {
        "persona_core",
        "motive_agent",
        "response_policy_agent",
        "silence_agent",
        "body_sense_agent",
        "inner_activity_agent",
    },
    StateScope.MEMORY_STATE: {
        "persona_core",
        "episodic_memory_agent",
        "semantic_memory_agent",
        "bond_memory_agent",
        "resident_memory_agent",
    },
    StateScope.EXECUTION_STATE: {
        "persona_core",
        "executive_router",
        "tool_exec_agent",
        "skill_pool_agent",
    },
}


@dataclass(frozen=True)
class StateWriteDecision:
    allowed: bool
    scope: StateScope
    writer_agent_id: str
    reason_code: str | None = None
    detail: str | None = None
    policy_version: str = STATE_WRITE_POLICY_VERSION


@dataclass(frozen=True)
class StateWriteResult:
    status: str
    scope: StateScope
    writer_agent_id: str
    written: bool
    state_version: int
    reason_code: str | None = None
    detail: str | None = None
    payload: dict[str, Any] | None = None


class StateWriteArbiter:
    """Authorize state writes and keep in-memory state snapshots by scope."""

    def __init__(
        self,
        *,
        scope_writers: Mapping[StateScope | str, set[str] | list[str] | tuple[str, ...]] | None = None,
        audit_writer: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        merged_policy = {scope: set(writers) for scope, writers in _DEFAULT_SCOPE_WRITERS.items()}
        if scope_writers:
            for raw_scope, writers in scope_writers.items():
                scope = self._coerce_scope(raw_scope)
                merged_policy[scope] = {str(item).strip() for item in list(writers or []) if str(item).strip()}
        self._scope_writers = merged_policy
        self._audit_writer = audit_writer
        self._lock = threading.RLock()
        self._state_versions: dict[StateScope, int] = {scope: 0 for scope in StateScope}
        self._state_store: dict[StateScope, dict[str, Any]] = {scope: {} for scope in StateScope}

    def get_scope_writers(self, scope: StateScope | str) -> set[str]:
        resolved = self._coerce_scope(scope)
        with self._lock:
            return set(self._scope_writers.get(resolved) or set())

    def set_scope_writers(
        self,
        scope: StateScope | str,
        writers: set[str] | list[str] | tuple[str, ...],
        *,
        replace: bool = True,
    ) -> None:
        resolved = self._coerce_scope(scope)
        normalized = {str(item).strip() for item in list(writers or []) if str(item).strip()}
        with self._lock:
            if replace:
                self._scope_writers[resolved] = normalized
            else:
                existing = set(self._scope_writers.get(resolved) or set())
                existing.update(normalized)
                self._scope_writers[resolved] = existing

    def can_write(self, *, scope: StateScope | str, writer_agent_id: str) -> StateWriteDecision:
        resolved = self._coerce_scope(scope)
        writer = str(writer_agent_id or "").strip()
        if not writer:
            return StateWriteDecision(
                allowed=False,
                scope=resolved,
                writer_agent_id=writer,
                reason_code=STATE_WRITE_DENY_REASON,
                detail="empty writer_agent_id",
            )
        with self._lock:
            allowed_writers = set(self._scope_writers.get(resolved) or set())
        if writer in allowed_writers:
            return StateWriteDecision(allowed=True, scope=resolved, writer_agent_id=writer)
        return StateWriteDecision(
            allowed=False,
            scope=resolved,
            writer_agent_id=writer,
            reason_code=STATE_WRITE_DENY_REASON,
            detail=f"writer={writer} not allowed for scope={resolved.value}",
        )

    def get_state(self, scope: StateScope | str) -> dict[str, Any]:
        resolved = self._coerce_scope(scope)
        with self._lock:
            return dict(self._state_store.get(resolved) or {})

    def get_state_version(self, scope: StateScope | str) -> int:
        resolved = self._coerce_scope(scope)
        with self._lock:
            return int(self._state_versions.get(resolved) or 0)

    def write(
        self,
        *,
        scope: StateScope | str,
        writer_agent_id: str,
        payload: Mapping[str, Any] | None,
        request_id: str = "",
        merge: bool = True,
    ) -> StateWriteResult:
        resolved = self._coerce_scope(scope)
        decision = self.can_write(scope=resolved, writer_agent_id=writer_agent_id)
        normalized_payload = dict(payload or {})
        if not decision.allowed:
            self._write_audit(
                "STATE_WRITE_DENIED",
                {
                    "request_id": str(request_id or "").strip(),
                    "scope": resolved.value,
                    "writer_agent_id": str(writer_agent_id or "").strip(),
                    "reason_code": decision.reason_code,
                    "detail": decision.detail,
                },
            )
            return StateWriteResult(
                status="DENIED",
                scope=resolved,
                writer_agent_id=str(writer_agent_id or "").strip(),
                written=False,
                state_version=self.get_state_version(resolved),
                reason_code=decision.reason_code,
                detail=decision.detail,
                payload=self.get_state(resolved),
            )

        with self._lock:
            current = dict(self._state_store.get(resolved) or {})
            if merge:
                current.update(normalized_payload)
            else:
                current = normalized_payload
            self._state_store[resolved] = current
            self._state_versions[resolved] = int(self._state_versions.get(resolved) or 0) + 1
            version = int(self._state_versions[resolved])

        self._write_audit(
            "STATE_WRITE_ALLOWED",
            {
                "request_id": str(request_id or "").strip(),
                "scope": resolved.value,
                "writer_agent_id": str(writer_agent_id or "").strip(),
                "state_version": version,
                "merge": bool(merge),
                "payload_keys": sorted(current.keys()),
            },
        )
        return StateWriteResult(
            status="OK",
            scope=resolved,
            writer_agent_id=str(writer_agent_id or "").strip(),
            written=True,
            state_version=version,
            payload=dict(current),
        )

    @staticmethod
    def _coerce_scope(raw_scope: StateScope | str) -> StateScope:
        if isinstance(raw_scope, StateScope):
            return raw_scope
        text = str(raw_scope or "").strip().lower()
        for scope in StateScope:
            if scope.value == text:
                return scope
        raise ValueError(f"unknown scope: {raw_scope!r}")

    def _write_audit(self, event: str, detail: dict[str, Any]) -> None:
        writer = self._audit_writer
        if writer is None:
            return
        try:
            writer(event, detail)
        except Exception:
            return


__all__ = [
    "STATE_WRITE_DENY_REASON",
    "STATE_WRITE_POLICY_VERSION",
    "StateScope",
    "StateWriteArbiter",
    "StateWriteDecision",
    "StateWriteResult",
]
