"""Durable status ownership separated from resource-management policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import threading
from typing import Any

from host_brain.contracts.resource_manager_status import (
    ResourceManagerStatusHeadV1,
    ResourceManagerStatusJournalPort,
)
from host_brain.core.ids import now_ms


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _token(value: Any, *, max_length: int = 160) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    safe = "".join(
        character
        for character in text
        if character.isalnum()
        or character in " ._:/@%+=,-[]()"
    )
    return safe[:max_length]


def _error_type(value: Any) -> str:
    text = str(value or "").strip()
    return _token(text.split(":", 1)[0], max_length=96) if text else ""


def build_durable_resource_manager_status(
    state: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a bounded operational projection without paths or user text."""

    source = dict(state)
    pressure = _mapping(_mapping(source.get("last_snapshot")).get("pressure"))
    decision = _mapping(source.get("last_decision"))
    actions = [
        {
            "action": _token(_mapping(item).get("action"), max_length=96),
            "status": _token(
                _mapping(_mapping(item).get("result")).get("status"),
                max_length=64,
            )
            or None,
            "success": _mapping(
                _mapping(item).get("result")
            ).get("success"),
            "pid_count": len(_items(_mapping(item).get("pids"))),
            "error_type": _error_type(_mapping(item).get("error")) or None,
        }
        for item in _items(source.get("last_actions"))[:8]
    ]
    gates = []
    for raw in _items(source.get("last_gate_decisions"))[:20]:
        item = _mapping(raw)
        gates.append(
            {
                "decision": _token(item.get("decision"), max_length=32),
                "reason_code": _token(
                    item.get("reason_code"),
                    max_length=96,
                )
                or None,
                "task_type": _token(
                    item.get("task_type"),
                    max_length=96,
                )
                or None,
                "priority": _token(
                    item.get("priority"),
                    max_length=32,
                )
                or None,
                "pressure_status": _token(
                    item.get("pressure_status"),
                    max_length=32,
                )
                or None,
                "uses_llm": bool(item.get("uses_llm")),
                "uses_gpu": bool(item.get("uses_gpu")),
                "retry_after_s": item.get("retry_after_s"),
                "ts_ms": item.get("ts_ms"),
            }
        )
    degradation = _mapping(source.get("degradation"))
    return {
        "schema_version": "Natsume.resource-manager.status.v1",
        "running": bool(source.get("running")),
        "interval_s": source.get("interval_s"),
        "llm_enabled": bool(source.get("llm_enabled")),
        "auto_gc_enabled": bool(source.get("auto_gc_enabled")),
        "auto_trim_enabled": bool(source.get("auto_trim_enabled")),
        "last_tick_ts_ms": int(source.get("last_tick_ts_ms") or 0),
        "tick_count": int(source.get("tick_count") or 0),
        "analysis_count": int(source.get("analysis_count") or 0),
        "auto_action_count": int(source.get("auto_action_count") or 0),
        "last_error_type": _error_type(source.get("last_error")) or None,
        "last_llm_error_type": _error_type(
            source.get("last_llm_error")
        )
        or None,
        "pressure": {
            "status": _token(pressure.get("status"), max_length=32)
            or "UNKNOWN",
            "memory_percent": pressure.get("memory_percent"),
            "vram_percent": pressure.get("vram_percent"),
            "gpu_percent": pressure.get("gpu_percent"),
            "reason_codes": [
                _token(item, max_length=96)
                for item in _items(pressure.get("reasons"))[:12]
                if _token(item, max_length=96)
            ],
        },
        "decision": {
            "source": _token(decision.get("source"), max_length=96) or None,
            "pressure_level": _token(
                decision.get("pressure_level"),
                max_length=32,
            )
            or None,
            "provider": _token(
                decision.get("provider"),
                max_length=96,
            )
            or None,
            "model": _token(decision.get("model"), max_length=128) or None,
            "confidence": decision.get("confidence"),
            "cached": bool(decision.get("cached")),
            "can_release_count": len(_items(decision.get("can_release"))),
            "cannot_release_count": len(
                _items(decision.get("cannot_release"))
            ),
            "review_count": len(
                _items(decision.get("needs_user_confirm"))
            ),
        },
        "actions": actions,
        "gate_decisions": gates,
        "degradation": {
            "status": _token(degradation.get("status"), max_length=64)
            or "UNAVAILABLE",
            "durability_status": _token(
                degradation.get("durability_status"),
                max_length=64,
            )
            or None,
            "state_read_failed": bool(
                degradation.get("state_read_failed")
            ),
        },
    }


class ResourceManagerStatusOwner:
    """Serialize recovery and append/retry for one injected journal."""

    def __init__(
        self,
        journal: ResourceManagerStatusJournalPort | None,
        *,
        state_provider: Callable[[], Mapping[str, Any]],
        restore_consumer: Callable[[Mapping[str, Any]], None],
        clock: Callable[[], int] = now_ms,
    ) -> None:
        if journal is not None and not isinstance(
            journal,
            ResourceManagerStatusJournalPort,
        ):
            raise TypeError(
                "status_journal must implement ResourceManagerStatusJournalPort"
            )
        if not callable(state_provider) or not callable(restore_consumer):
            raise TypeError("status owner state ports must be callable")
        self._journal = journal
        self._state_provider = state_provider
        self._restore_consumer = restore_consumer
        self._clock = clock
        self._lock = threading.RLock()
        self._revision = 0
        self._event_id = ""
        self._payload_digest = ""
        self._observed_at_ms = 0
        self._error_type = ""
        self._recovered = False
        self._pending: tuple[int, str, int, dict[str, Any]] | None = None

    @property
    def has_pending(self) -> bool:
        with self._lock:
            return self._pending is not None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "required": self._journal is not None,
                "status": (
                    "FAILED"
                    if self._error_type
                    else (
                        "ACTIVE"
                        if self._journal is not None
                        else "NOT_CONFIGURED"
                    )
                ),
                "runtime_scope_id": (
                    self._journal.runtime_scope_id
                    if self._journal is not None
                    else None
                ),
                "revision": self._revision,
                "event_id": self._event_id or None,
                "payload_digest": self._payload_digest or None,
                "observed_at_ms": self._observed_at_ms or None,
                "recovered": self._recovered,
                "error_type": self._error_type or None,
            }

    def _install(
        self,
        head: ResourceManagerStatusHeadV1,
        *,
        restore: bool,
    ) -> None:
        payload = dict(head.payload)
        status = payload.get("status")
        if not isinstance(status, Mapping):
            raise RuntimeError("RESOURCE_MANAGER_STATUS_HEAD_INVALID")
        if restore:
            self._restore_consumer(dict(status))
        self._revision = int(head.revision)
        self._event_id = str(head.event_id)
        self._payload_digest = str(head.payload_digest)
        self._observed_at_ms = int(head.observed_at_ms)
        self._error_type = ""
        self._recovered = True

    def recover(self) -> None:
        with self._lock:
            if self._journal is None or self._recovered:
                return
            try:
                head = self._journal.recover_head()
                if head is not None:
                    self._install(head, restore=True)
                else:
                    self._recovered = True
            except Exception as exc:
                self._error_type = type(exc).__name__
                raise

    def append(self, *, reason: str) -> None:
        journal = self._journal
        if journal is None:
            return
        normalized_reason = _token(reason, max_length=128) or "status_updated"
        with self._lock:
            if self._pending is not None:
                revision, pending_reason, observed, payload = self._pending
                try:
                    recovered = journal.append_status(
                        expected_revision=revision,
                        reason=pending_reason,
                        observed_at_ms=observed,
                        payload=payload,
                    )
                except Exception as exc:
                    self._error_type = type(exc).__name__
                    raise
                self._install(recovered, restore=False)
                self._pending = None
            observed_at_ms = int(self._clock())
            payload = build_durable_resource_manager_status(
                self._state_provider()
            )
            revision = self._revision
            self._pending = (
                revision,
                normalized_reason,
                observed_at_ms,
                payload,
            )
            try:
                head = journal.append_status(
                    expected_revision=revision,
                    reason=normalized_reason,
                    observed_at_ms=observed_at_ms,
                    payload=payload,
                )
            except Exception as exc:
                self._error_type = type(exc).__name__
                raise
            self._install(head, restore=False)
            self._pending = None


__all__ = [
    "ResourceManagerStatusOwner",
    "build_durable_resource_manager_status",
]
