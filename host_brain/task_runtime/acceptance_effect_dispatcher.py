"""Lease/fence based dispatcher for committed Acceptance Effect intents."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import re
from typing import Any


ACCEPTANCE_EFFECT_HANDLER_MISSING = "ACCEPTANCE_EFFECT_HANDLER_MISSING"
ACCEPTANCE_EFFECT_HANDLER_FAILED = "ACCEPTANCE_EFFECT_HANDLER_FAILED"
_DURABLE_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")

AcceptanceEffectHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | str | Awaitable[Any]]


_EFFECT_TYPE_ORDER = {
    "conversation.user_turn": 0,
    "life.foreground_completed": 1,
    "surface.reply_delivery": 2,
}


class AcceptanceEffectDispatchError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or ACCEPTANCE_EFFECT_HANDLER_FAILED)
        self.detail = str(detail or "acceptance effect dispatch failed")
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
        handler = self._handlers.get(effect_type)
        if handler is None:
            return self._fail(
                effect,
                reason_code=ACCEPTANCE_EFFECT_HANDLER_MISSING,
                detail=f"no handler registered for {effect_type or '<empty>'}",
            )
        try:
            handler_payload, preview = self._require_action_preview(effect)
            if preview is not None:
                handler_payload["_action_preview_terminal_proof"] = {
                    "service": self._action_preview_service,
                    "event_id": str(preview["event_id"]),
                    "boundary": dict(preview["_terminal_execution_boundary"]),
                }
            outcome = handler(handler_payload)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            receipt_ref = _receipt_ref(outcome, effect_id=str(effect["effect_id"]))
        except AcceptanceEffectDispatchError as exc:
            return self._fail(
                effect,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        except Exception as exc:
            reason = getattr(exc, "reason_code", None) or ACCEPTANCE_EFFECT_HANDLER_FAILED
            detail = f"{type(exc).__name__}: {str(exc)[:1800]}"
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
            "conversation.user_turn": 1,
            "life.foreground_completed": 2,
            "surface.reply_delivery": 3,
        }.get(effect_type, 5)
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
                key: value
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
                str(getattr(exc, "reason_code", None) or "ACTION_PREVIEW_INVALID"),
                str(getattr(exc, "detail", None) or "Acceptance Effect preview failed"),
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
                # dispatcher.  Do not let this dispatcher overtake it.
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


def _receipt_ref(value: Any, *, effect_id: str) -> str:
    if isinstance(value, str):
        receipt = value.strip()
    elif isinstance(value, Mapping):
        receipt = str(
            value.get("receipt_ref")
            or value.get("event_id")
            or value.get("command_id")
            or ""
        ).strip()
    else:
        receipt = ""
    if not receipt:
        suffix = str(effect_id).rsplit("/", 1)[-1]
        receipt = f"acceptance-effect-receipt://sha256/{suffix}"
    if not _DURABLE_REF.fullmatch(receipt):
        raise AcceptanceEffectDispatchError(
            ACCEPTANCE_EFFECT_HANDLER_FAILED,
            "effect handler returned a non-durable receipt reference",
        )
    return receipt


def _effect_dispatch_key(effect: Mapping[str, Any]) -> tuple[int, int, str]:
    effect_type = str(effect.get("effect_type") or "").strip()
    return (
        _EFFECT_TYPE_ORDER.get(effect_type, len(_EFFECT_TYPE_ORDER)),
        int(effect.get("created_ts_ms") or 0),
        str(effect.get("effect_id") or ""),
    )


__all__ = [
    "ACCEPTANCE_EFFECT_HANDLER_FAILED",
    "ACCEPTANCE_EFFECT_HANDLER_MISSING",
    "AcceptanceEffectDispatchError",
    "AcceptanceEffectDispatcher",
]
