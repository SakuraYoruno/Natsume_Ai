"""Lease/fence based dispatcher for committed Acceptance Effect intents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
import inspect
import logging
import re
import secrets
from typing import Any

from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof


logger = logging.getLogger("Natsume.acceptance_effect_recovery")


ACCEPTANCE_EFFECT_HANDLER_MISSING = "ACCEPTANCE_EFFECT_HANDLER_MISSING"
ACCEPTANCE_EFFECT_HANDLER_FAILED = "ACCEPTANCE_EFFECT_HANDLER_FAILED"
ACCEPTANCE_EFFECT_RECOVERY_PENDING = "ACCEPTANCE_EFFECT_RECOVERY_PENDING"
ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED = (
    "ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED"
)
_DURABLE_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
_SCHEDULER_OPERATION_EFFECT_TYPE = "scheduler.operation.execute"
_SCHEDULER_DERIVED_CONTEXT_SCHEMA = (
    "Natsume.scheduler.action_preview_context.v2"
)
_INTERNAL_RESULT_COMMIT_EFFECT_TYPE = "internal.agent.result_commit"
_INTERNAL_DERIVED_CONTEXT_SCHEMA = (
    "Natsume.internal.action_preview_context.v2"
)
_NODE_RISK_EFFECT_TYPES = frozenset(
    {_SCHEDULER_OPERATION_EFFECT_TYPE, _INTERNAL_RESULT_COMMIT_EFFECT_TYPE}
)

AcceptanceEffectHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | str | Awaitable[Any]]
AcceptanceEffectRecoveryObserver = Callable[[Mapping[str, Any]], Any]


_EFFECT_TYPE_ORDER = {
    "host.sys_mode_transition": 0,
    "conversation.user_turn": 1,
    "life.foreground_completed": 2,
    "surface.reply_delivery": 3,
    _INTERNAL_RESULT_COMMIT_EFFECT_TYPE: 4,
}

_RECOVERY_POLL_SECONDS = 0.25


class AcceptanceEffectDispatchError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or ACCEPTANCE_EFFECT_HANDLER_FAILED)
        self.detail = str(detail or "acceptance effect dispatch failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class AcceptanceEffectOutcomeUnknown(RuntimeError):
    """Signal that a handler crossed its effect boundary without attribution.

    This is deliberately distinct from a retryable pre-effect failure.  The
    immutable Acceptance Effect payload must contain the identity needed by
    the handler to reconcile the outcome on a later exact replay.
    """

    def __init__(self, detail: str) -> None:
        self.reason_code = ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
        self.detail = str(
            detail or "acceptance effect outcome requires exact reconciliation"
        )
        super().__init__(f"{self.reason_code}: {self.detail}")


class AcceptanceEffectDispatcher:
    def __init__(
        self,
        *,
        task_store: Any,
        handlers: Mapping[str, AcceptanceEffectHandler],
        owner_id: str,
        lease_ms: int = 30_000,
        max_attempts: int = 5,
        retry_base_ms: int = 250,
        retry_max_ms: int = 30_000,
        action_preview_service: Any | None = None,
    ) -> None:
        owner = str(owner_id or "").strip()
        if not owner:
            raise ValueError("owner_id is required")
        required = (
            "list_acceptance_effect_intents",
            "claim_acceptance_effect_intent",
            "commit_acceptance_effect_intent",
            "fail_acceptance_effect_intent",
            "reschedule_acceptance_effect_intent",
        )
        if any(not callable(getattr(task_store, name, None)) for name in required):
            raise ValueError("task_store lacks Acceptance Effect queue operations")
        normalized = {
            str(effect_type or "").strip(): handler
            for effect_type, handler in dict(handlers or {}).items()
        }
        if not normalized or any(not key or not callable(handler) for key, handler in normalized.items()):
            raise ValueError("at least one callable effect handler is required")
        self._store = task_store
        self._handlers = normalized
        self._owner_id = owner
        self._lease_ms = max(1, int(lease_ms))
        self._max_attempts = max(1, int(max_attempts))
        self._retry_base_ms = max(0, int(retry_base_ms))
        self._retry_max_ms = max(self._retry_base_ms, int(retry_max_ms))
        self._action_preview_service = action_preview_service
        self._dispatch_lock = asyncio.Lock()

    async def dispatch_effect(self, effect_id: str) -> dict[str, Any] | None:
        # Inline dispatch and the recovery drain share one process owner.  A
        # lock prevents an exact-owner claim replay from invoking a public
        # handler twice while the first invocation is still in flight.
        async with self._dispatch_lock:
            return await self._dispatch_effect(effect_id)

    async def _dispatch_effect(self, effect_id: str) -> dict[str, Any] | None:
        claimed = self._store.claim_acceptance_effect_intent(
            owner_id=self._owner_id,
            lease_ms=self._lease_ms,
            effect_id=str(effect_id or "").strip(),
        )
        if claimed is None:
            return None
        effect = dict(claimed)
        effect_type = str(effect.get("effect_type") or "").strip()
        reconciliation_only = bool(effect.get("claim_reconciliation_required"))
        handler = self._handlers.get(effect_type)
        if handler is None:
            if reconciliation_only:
                return self._reschedule_reconciliation(
                    effect,
                    detail=(
                        "reclaimed Acceptance Effect has no registered "
                        f"read-only reconciler: {effect_type or '<empty>'}"
                    ),
                )
            return self._fail(
                effect,
                reason_code=ACCEPTANCE_EFFECT_HANDLER_MISSING,
                detail=f"no handler registered for {effect_type or '<empty>'}",
            )
        try:
            handler_payload, preview = self._require_action_preview(effect)
            if preview is not None:
                terminal_boundary = dict(preview["_terminal_execution_boundary"])
                if effect_type in _NODE_RISK_EFFECT_TYPES:
                    if _contains_reserved_preview_context(handler_payload):
                        raise AcceptanceEffectDispatchError(
                            "ACTION_PREVIEW_INVALID",
                            "operation Acceptance Effect payload collides with private Preview context",
                        )
                    receipt = preview.get("receipt")
                    if (
                        not isinstance(receipt, Mapping)
                        or str(receipt.get("authority_kind") or "").strip().upper()
                        != "ACCEPTANCE_EFFECT"
                    ):
                        raise AcceptanceEffectDispatchError(
                            "ACTION_PREVIEW_INVALID",
                            "operation Acceptance Effect has no exact origin receipt",
                        )
                    # This live claim context exists only inside the current
                    # handler invocation.  Persisting it would turn a short
                    # fence into replayable authority after COMMIT/expiry.
                    handler_payload["_action_preview_context"] = {
                        "schema_version": (
                            _SCHEDULER_DERIVED_CONTEXT_SCHEMA
                            if effect_type == _SCHEDULER_OPERATION_EFFECT_TYPE
                            else _INTERNAL_DERIVED_CONTEXT_SCHEMA
                        ),
                        "origin_authority_kind": "ACCEPTANCE_EFFECT",
                        "origin_event_id": str(preview["event_id"]),
                        "origin_receipt_ref": str(preview["receipt_ref"]),
                        "origin_boundary": deepcopy(terminal_boundary),
                    }
                if effect_type == "host.sys_mode_transition":
                    handler_payload["_action_preview_terminal_proof"] = (
                        ActionPreviewExecutionProof(
                            service=self._action_preview_service,
                            event_id=str(preview["event_id"]),
                            boundary=terminal_boundary,
                        )
                    )
                else:
                    handler_payload["_action_preview_terminal_proof"] = {
                        "service": self._action_preview_service,
                        "event_id": str(preview["event_id"]),
                        "boundary": terminal_boundary,
                    }
            # A reclaimed claim means that a previous owner may already have
            # crossed the downstream effect boundary.  This rule is
            # deliberately effect-type agnostic: replaying any ordinary
            # handler here could duplicate a visible conversation write,
            # Life mutation, delivery, Scheduler operation, or future effect.
            if reconciliation_only:
                reconciler = getattr(handler, "reconcile", None)
                if not callable(reconciler):
                    raise AcceptanceEffectOutcomeUnknown(
                        "reclaimed Acceptance Effect has no read-only reconciler: "
                        f"{effect_type or '<empty>'}"
                    )
                outcome = reconciler(handler_payload)
            else:
                outcome = handler(handler_payload)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            receipt_ref = _receipt_ref(outcome)
        except AcceptanceEffectOutcomeUnknown as exc:
            # An invoked handler may already have committed its external or
            # cross-authority effect.  It must never become terminal FAILED
            # merely because attribution remained unavailable after the
            # ordinary retry budget.  Persist an explicit reconciliation
            # marker and retry only through the immutable effect identity.
            return self._reschedule_reconciliation(
                effect,
                detail=_opaque_dispatch_failure(exc),
            )
        except AcceptanceEffectDispatchError as exc:
            return self._fail(
                effect,
                reason_code=_safe_reason_code(
                    exc.reason_code,
                    fallback=ACCEPTANCE_EFFECT_HANDLER_FAILED,
                ),
                detail=_opaque_dispatch_failure(exc),
            )
        except Exception as exc:
            reason = _safe_reason_code(
                getattr(exc, "reason_code", None),
                fallback=ACCEPTANCE_EFFECT_HANDLER_FAILED,
            )
            detail = _opaque_dispatch_failure(exc)
            if int(effect.get("attempt_count") or 0) >= self._max_attempts:
                return self._fail(
                    effect,
                    reason_code=str(reason),
                    detail=detail,
                )
            return self._reschedule(
                effect,
                reason_code=str(reason),
                detail=detail,
            )
        # Store/fence failures are not handler failures.  In particular, an
        # expired lease must remain reclaimable instead of being rewritten as
        # a terminal handler failure with the stale fencing token.
        return dict(
            self._store.commit_acceptance_effect_intent(
                str(effect["effect_id"]),
                owner_id=self._owner_id,
                fencing_token=int(effect["fencing_token"]),
                receipt_ref=receipt_ref,
            )
        )

    def _require_action_preview(
        self,
        effect: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload = dict(effect.get("payload") or {})
        raw_context = payload.pop("_action_preview_context", None)
        if raw_context is None:
            raise AcceptanceEffectDispatchError(
                "ACTION_PREVIEW_INVALID",
                "legacy Acceptance Effect is quarantined without Runtime Context",
            )
        if not isinstance(raw_context, Mapping):
            raise AcceptanceEffectDispatchError(
                "ACTION_PREVIEW_INVALID",
                "Acceptance Effect preview context is invalid",
            )
        service = self._action_preview_service
        if service is None:
            raise AcceptanceEffectDispatchError(
                "ACTION_PREVIEW_INVALID",
                "runtime-context Acceptance Effect has no ActionPreview authority",
            )
        context = dict(raw_context)
        if context.get("schema_version") != "Natsume.acceptance_effect.action_preview_context.v1":
            raise AcceptanceEffectDispatchError(
                "ACTION_PREVIEW_INVALID",
                "Acceptance Effect preview context schema drifted",
            )
        effect_type = str(effect.get("effect_type") or "").strip()
        risk_level = {
            "host.sys_mode_transition": 4,
            "conversation.user_turn": 1,
            "life.foreground_completed": 2,
            "surface.reply_delivery": 3,
        }.get(effect_type, 5)
        if effect_type in _NODE_RISK_EFFECT_TYPES:
            get_node = getattr(self._store, "get_node", None)
            if not callable(get_node):
                raise AcceptanceEffectDispatchError(
                    "ACTION_PREVIEW_INVALID",
                    "operation Acceptance Effect cannot reopen canonical TaskNode risk",
                )
            node = get_node(
                str(effect.get("graph_id") or ""),
                int(effect.get("graph_revision") or 0),
                str(effect.get("node_id") or ""),
            )
            node_payload = dict(node.get("node") or {}) if isinstance(node, Mapping) else {}
            raw_risk = node_payload.get("risk_level")
            if isinstance(raw_risk, bool):
                raw_risk = None
            try:
                risk_level = int(raw_risk)
            except (TypeError, ValueError):
                risk_level = 0
            if not 1 <= int(risk_level) <= 5:
                raise AcceptanceEffectDispatchError(
                    "ACTION_PREVIEW_INVALID",
                    "operation Acceptance Effect TaskNode risk is invalid",
                )
        try:
            kwargs = {
                "action_kind": f"acceptance-effect.{effect_type}",
                "action_scope": str(effect.get("effect_id") or ""),
                "risk_level": risk_level,
                "action_args": payload,
                "context_pack_digest": context["context_pack_digest"],
                "scene_identity": context["scene_identity"],
                "task_graph_id": str(effect.get("graph_id") or ""),
                "graph_revision": int(effect.get("graph_revision") or 0),
                "task_node_id": str(effect.get("node_id") or ""),
                "parent_governor_grant_ref": context["parent_governor_grant_ref"],
                "node_grant_ref": context["node_grant_ref"],
                "node_grant": {
                    "grant_ref": context["node_grant_ref"],
                    "binding_digest": context["node_grant_digest"],
                },
                "guardian_capability_id": f"acceptance_effect_{effect_type.replace('.', '_')}",
                "authority_context": {
                    "schema_version": (
                        "Natsume.action_preview."
                        "acceptance_effect_authority_request.v1"
                    ),
                    "effect_id": str(effect.get("effect_id") or ""),
                    "lease_owner_id": str(effect.get("lease_owner") or ""),
                    "fencing_token": int(effect.get("fencing_token") or 0),
                },
            }
            issued = service.issue_receipt(**kwargs)
            execution_boundary = {
                # The handler payload receives an in-process terminal proof
                # after this method returns.  Keep the durable execution
                # boundary structurally independent: aliasing ``action_args``
                # would create payload -> proof -> boundary -> payload cycles
                # and make the final Gateway/Edge reopen non-canonical.
                key: deepcopy(value)
                for key, value in kwargs.items()
                if key != "guardian_capability_id"
            }
            service.require_execution(
                event_id=issued["event_id"],
                **execution_boundary,
            )
        except AcceptanceEffectDispatchError:
            raise
        except Exception as exc:
            raise AcceptanceEffectDispatchError(
                _safe_reason_code(
                    getattr(exc, "reason_code", None),
                    fallback="ACTION_PREVIEW_INVALID",
                ),
                "Acceptance Effect preview failed; "
                + _opaque_dispatch_failure(exc),
            ) from exc
        preview = dict(issued)
        preview["_terminal_execution_boundary"] = execution_boundary
        return payload, preview

    async def dispatch_graph(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        limit: int = 100,
    ) -> tuple[dict[str, Any], ...]:
        requested_limit = min(1_000, max(1, int(limit)))
        rows = self._store.list_acceptance_effect_intents(
            graph_id=str(graph_id),
            graph_revision=int(graph_revision),
            # Fetch the complete bounded graph queue before applying the
            # semantic order.  Passing the caller's smaller limit to the store
            # could select delivery before conversation merely because its
            # opaque effect_id sorts first.
            limit=1_000,
        )
        results: list[dict[str, Any]] = []
        newly_dispatched = 0
        for row in sorted(rows, key=_effect_dispatch_key):
            status = str(row.get("status") or "")
            if status == "COMMITTED":
                results.append(dict(row))
                continue
            if status == "FAILED":
                results.append(dict(row))
                break
            if newly_dispatched >= requested_limit:
                break
            dispatched = await self.dispatch_effect(str(row.get("effect_id") or ""))
            if dispatched is None:
                # A prior semantic phase may be owned by another live
                # dispatcher (or be waiting for its durable retry time).  Do
                # not let this dispatcher overtake it, and do not collapse a
                # non-terminal durable row into an empty ``committed=0``
                # result.  Callers must be able to distinguish recovery from
                # a graph with no Acceptance Effects.
                current = self._current_graph_effect(
                    graph_id=str(graph_id),
                    graph_revision=int(graph_revision),
                    effect_id=str(row.get("effect_id") or ""),
                )
                results.append(_recovery_pending_projection(current or row))
                break
            results.append(dispatched)
            newly_dispatched += 1
            if str(dispatched.get("status") or "") != "COMMITTED":
                # Conversation -> Life -> delivery is a commit chain.  A
                # failed or retrying earlier phase closes the graph queue to
                # later public effects rather than leaking a partially
                # accepted result.
                break
        return tuple(results)

    def _current_graph_effect(
        self,
        *,
        graph_id: str,
        graph_revision: int,
        effect_id: str,
    ) -> dict[str, Any] | None:
        for current in self._store.list_acceptance_effect_intents(
            graph_id=graph_id,
            graph_revision=graph_revision,
            limit=1_000,
        ):
            if str(current.get("effect_id") or "") == effect_id:
                return dict(current)
        return None

    async def drain_ready(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        dispatch_limit = min(1_000, max(1, int(limit)))
        results: list[dict[str, Any]] = []
        candidates = [
            *self._store.list_acceptance_effect_intents(status="PENDING", limit=1_000),
            *self._store.list_acceptance_effect_intents(status="CLAIMED", limit=1_000),
        ]
        graph_order: dict[tuple[str, int], tuple[int, str]] = {}
        for row in candidates:
            key = (str(row.get("graph_id") or ""), int(row.get("graph_revision") or 0))
            durable_order = (
                int(row.get("created_ts_ms") or 0),
                str(row.get("effect_id") or ""),
            )
            graph_order[key] = min(graph_order.get(key, durable_order), durable_order)

        for (graph_id, graph_revision), _ in sorted(
            graph_order.items(), key=lambda item: (*item[1], *item[0])
        ):
            if len(results) >= dispatch_limit:
                break
            rows = self._store.list_acceptance_effect_intents(
                graph_id=graph_id,
                graph_revision=graph_revision,
                limit=1_000,
            )
            for row in sorted(rows, key=_effect_dispatch_key):
                status = str(row.get("status") or "")
                if status == "COMMITTED":
                    continue
                if status == "FAILED":
                    break
                dispatched = await self.dispatch_effect(str(row.get("effect_id") or ""))
                if dispatched is None:
                    break
                results.append(dispatched)
                if str(dispatched.get("status") or "") != "COMMITTED":
                    break
                if len(results) >= dispatch_limit:
                    break
        return tuple(results)

    def list_recovery_pending(self, *, limit: int = 1) -> tuple[dict[str, Any], ...]:
        """Return bounded durable work which still blocks clean admission."""

        bounded = min(1_000, max(1, int(limit)))
        rows = [
            *self._store.list_acceptance_effect_intents(
                status="PENDING",
                limit=bounded,
            ),
            *self._store.list_acceptance_effect_intents(
                status="CLAIMED",
                limit=bounded,
            ),
        ]
        return tuple(
            _recovery_pending_projection(row)
            for row in sorted(rows, key=_effect_dispatch_key)[:bounded]
        )

    def _fail(
        self,
        effect: Mapping[str, Any],
        *,
        reason_code: str,
        detail: str,
    ) -> dict[str, Any]:
        return dict(
            self._store.fail_acceptance_effect_intent(
                str(effect["effect_id"]),
                owner_id=self._owner_id,
                fencing_token=int(effect["fencing_token"]),
                reason_code=str(reason_code or ACCEPTANCE_EFFECT_HANDLER_FAILED)[:128],
                detail=str(detail or "acceptance effect handler failed")[:2000],
            )
        )

    def _reschedule(
        self,
        effect: Mapping[str, Any],
        *,
        reason_code: str,
        detail: str,
    ) -> dict[str, Any]:
        attempt_count = max(1, int(effect.get("attempt_count") or 1))
        delay_ms = min(
            self._retry_max_ms,
            self._retry_base_ms * (2 ** max(0, attempt_count - 1)),
        )
        return dict(
            self._store.reschedule_acceptance_effect_intent(
                str(effect["effect_id"]),
                owner_id=self._owner_id,
                fencing_token=int(effect["fencing_token"]),
                reason_code=str(reason_code or ACCEPTANCE_EFFECT_HANDLER_FAILED)[:128],
                detail=str(detail or "acceptance effect handler failed")[:2000],
                delay_ms=delay_ms,
            )
        )

    def _reschedule_reconciliation(
        self,
        effect: Mapping[str, Any],
        *,
        detail: str,
    ) -> dict[str, Any]:
        pending = self._reschedule(
            effect,
            reason_code=ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED,
            detail=detail,
        )
        return _recovery_pending_projection(pending)


class AcceptanceEffectRuntime:
    """Single app-local recovery owner for one shared dispatcher.

    Surface runtimes may use the dispatcher inline, but they never start a
    second recovery loop.  Shutdown signals the loop and then *awaits* the
    current pass.  It deliberately never timeout-cancels an effect handler:
    doing so after an external side effect has started would turn a clean
    shutdown into an unknown-effect replay hazard.
    """

    def __init__(
        self,
        *,
        dispatcher: AcceptanceEffectDispatcher,
        poll_seconds: float = _RECOVERY_POLL_SECONDS,
        startup_limit: int = 1_000,
        recovery_limit: int = 100,
        recovery_observer: AcceptanceEffectRecoveryObserver | None = None,
    ) -> None:
        if not isinstance(dispatcher, AcceptanceEffectDispatcher):
            raise TypeError("dispatcher must be AcceptanceEffectDispatcher")
        self._dispatcher = dispatcher
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._startup_limit = min(1_000, max(1, int(startup_limit)))
        self._recovery_limit = min(1_000, max(1, int(recovery_limit)))
        if recovery_observer is not None and not callable(recovery_observer):
            raise TypeError("recovery_observer must be callable")
        self._recovery_observer = recovery_observer
        self._recovery_failure_active = False
        self._recovery_failure_observed = False
        self._pending_failure_observation: dict[str, Any] | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def dispatcher(self) -> AcceptanceEffectDispatcher:
        return self._dispatcher

    @property
    def active(self) -> bool:
        task = self._task
        return task is not None and not task.done()

    async def activate(self) -> None:
        async with self._lifecycle_lock:
            if self.active:
                return
            # Startup recovery is completed before any governed surface can
            # accept a new command.
            try:
                await self._dispatcher.drain_ready(limit=self._startup_limit)
                remaining = self._dispatcher.list_recovery_pending(limit=1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._record_recovery_failure(exc)
                raise
            if remaining:
                effect_id = str(remaining[0].get("effect_id") or "<unknown>")
                pending = AcceptanceEffectDispatchError(
                    ACCEPTANCE_EFFECT_RECOVERY_PENDING,
                    f"durable Acceptance Effect still requires recovery: {effect_id}",
                )
                await self._record_recovery_failure(pending)
                raise pending
            # A previous process may have recorded a durable recovery breach.
            # The production observer resolves it only after this process has
            # completed a real startup recovery pass.
            await self._record_recovery_success(force=True)
            self._stop.clear()
            self._task = asyncio.create_task(
                self._run(),
                name="governed-acceptance-effect-recovery",
            )

    async def drain(self) -> tuple[dict[str, Any], ...]:
        return await self._dispatcher.drain_ready(limit=self._startup_limit)

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            self._stop.set()
            # Never cancel a pass which may already be executing a handler.
            await asyncio.shield(task)
            if self._task is task:
                self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._poll_seconds,
                )
                continue
            except TimeoutError:
                pass
            try:
                await self._flush_pending_failure_observation()
                await self._dispatcher.drain_ready(limit=self._recovery_limit)
                await self._record_recovery_success()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Durable rows and leases remain the source of truth.  A
                # transient store/transport failure must not kill recovery,
                # but it must be observable rather than silently swallowed.
                logger.error(
                    "ACCEPTANCE_EFFECT_RECOVERY_PASS_FAILED error_type=%s",
                    f"{type(exc).__module__}.{type(exc).__qualname__}",
                )
                await self._record_recovery_failure(exc)
                continue

    async def _record_recovery_failure(self, exc: BaseException) -> None:
        error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
        self._recovery_failure_active = True
        # One active failure owns one durable Guardian breach.  Repeated poll
        # failures must not grow the journal, and an observer outage must not
        # replace the still-pending observation with another identity.
        if self._pending_failure_observation is not None:
            await self._flush_pending_failure_observation()
            return
        if self._recovery_failure_active and getattr(
            self, "_recovery_failure_observed", False
        ):
            return
        observation = {
            "schema": "natsume.acceptance_effect.recovery_observation.v1",
            "status": "FAILED",
            "reason_code": "ACCEPTANCE_EFFECT_RECOVERY_PASS_FAILED",
            "error_type": error_type,
            # This is an unguessable event identity, not a digest of exception
            # text.  Low-entropy passwords, paths and provider payloads cannot
            # be recovered by testing candidate hashes against the journal.
            "error_digest": f"sha256:{secrets.token_hex(32)}",
        }
        self._pending_failure_observation = observation
        await self._flush_pending_failure_observation()

    async def _flush_pending_failure_observation(self) -> None:
        observation = self._pending_failure_observation
        if observation is None:
            return
        if await self._notify_recovery_observer(observation):
            if self._pending_failure_observation is observation:
                self._pending_failure_observation = None
                self._recovery_failure_observed = True

    async def _record_recovery_success(self, *, force: bool = False) -> None:
        if not force and not self._recovery_failure_active:
            return
        if self._pending_failure_observation is not None:
            return
        observation = {
            "schema": "natsume.acceptance_effect.recovery_observation.v1",
            "status": "RECOVERED",
            "reason_code": "ACCEPTANCE_EFFECT_RECOVERY_PASS_RECOVERED",
        }
        if await self._notify_recovery_observer(observation):
            self._recovery_failure_active = False
            self._recovery_failure_observed = False
        else:
            # Keep retrying the recovery projection on later successful
            # passes.  This is especially important after restart, where the
            # breach may exist only in the durable Guardian journal.
            self._recovery_failure_active = True

    async def _notify_recovery_observer(
        self,
        observation: Mapping[str, Any],
    ) -> bool:
        observer = self._recovery_observer
        if observer is None:
            return True
        try:
            result = observer(deepcopy(dict(observation)))
            if inspect.isawaitable(result):
                await result
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "ACCEPTANCE_EFFECT_RECOVERY_OBSERVER_FAILED error_type=%s",
                f"{type(exc).__module__}.{type(exc).__qualname__}",
            )
            return False


def _receipt_ref(value: Any) -> str:
    if isinstance(value, str):
        receipt = value.strip()
    elif isinstance(value, Mapping):
        receipt = str(value.get("receipt_ref") or "").strip()
    else:
        receipt = ""
    if not receipt:
        raise AcceptanceEffectDispatchError(
            ACCEPTANCE_EFFECT_HANDLER_FAILED,
            "effect handler returned no authoritative durable receipt_ref",
        )
    if not _DURABLE_REF.fullmatch(receipt):
        raise AcceptanceEffectDispatchError(
            ACCEPTANCE_EFFECT_HANDLER_FAILED,
            "effect handler returned a non-durable receipt reference",
        )
    return receipt


def _opaque_dispatch_failure(exc: BaseException) -> str:
    """Return a durable-safe incident identity without exception text.

    Provider responses, filesystem paths and credentials routinely appear in
    exception messages.  The durable effect row needs a correlation identity,
    not the raw diagnostic payload or a guessable hash of it.
    """

    error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return f"error_type={error_type} incident=sha256:{secrets.token_hex(32)}"


def _safe_reason_code(value: Any, *, fallback: str) -> str:
    candidate = str(value or "").strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", candidate):
        return candidate
    return str(fallback)


def _contains_reserved_preview_context(value: Any) -> bool:
    """Reject durable handler data that could shadow the live v2 context."""

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            if str(raw_key) == "_action_preview_context":
                return True
            if _contains_reserved_preview_context(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_reserved_preview_context(child) for child in value)
    return False


def _effect_dispatch_key(effect: Mapping[str, Any]) -> tuple[int, int, str]:
    effect_type = str(effect.get("effect_type") or "").strip()
    return (
        _EFFECT_TYPE_ORDER.get(effect_type, len(_EFFECT_TYPE_ORDER)),
        int(effect.get("created_ts_ms") or 0),
        str(effect.get("effect_id") or ""),
    )


def _recovery_pending_projection(effect: Mapping[str, Any]) -> dict[str, Any]:
    projected = dict(effect)
    durable_status = str(projected.get("status") or "PENDING").strip().upper()
    if durable_status in {"COMMITTED", "FAILED"}:
        return projected
    projected["status"] = durable_status or "PENDING"
    projected["dispatch_state"] = "RECOVERY_PENDING"
    reason_code = str(projected.get("reason_code") or "").strip().upper()
    if reason_code == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED:
        projected["dispatch_reason_code"] = reason_code
        projected["side_effect_state"] = "UNKNOWN"
        projected["reconciliation_state"] = "RECONCILIATION_REQUIRED"
        projected["reconciliation_required"] = True
    else:
        projected["dispatch_reason_code"] = (
            "ACCEPTANCE_EFFECT_CLAIM_OR_RETRY_PENDING"
        )
    projected["recovery_pending"] = True
    return projected


__all__ = [
    "ACCEPTANCE_EFFECT_HANDLER_FAILED",
    "ACCEPTANCE_EFFECT_HANDLER_MISSING",
    "ACCEPTANCE_EFFECT_RECOVERY_PENDING",
    "ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED",
    "AcceptanceEffectDispatchError",
    "AcceptanceEffectDispatcher",
    "AcceptanceEffectOutcomeUnknown",
    "AcceptanceEffectRecoveryObserver",
    "AcceptanceEffectRuntime",
]
