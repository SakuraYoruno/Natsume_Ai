"""Fail-closed skill adapter for the governed Scheduler application boundary.

This synchronous adapter never creates scheduler authority, calls a legacy
scheduler instance, or loops back through HTTP.  Composition may inject an
explicit synchronous application port together with the already-evaluated
Secure Ingress authority.  The production scheduler service is asynchronous,
so injecting it directly is rejected instead of starting a private event loop.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Any

from host_brain.secure_ingress import SecureIngressAuthority


_logger = logging.getLogger("Natsume.skills.scheduler_executor")

SCHEDULER_GOVERNED_CONTEXT_REQUIRED = "SCHEDULER_GOVERNED_CONTEXT_REQUIRED"
SCHEDULER_SYNC_ADAPTER_REQUIRED = "SCHEDULER_SYNC_ADAPTER_REQUIRED"
SCHEDULER_ACTION_INVALID = "SCHEDULER_ACTION_INVALID"
SCHEDULER_PORT_FAILED = "SCHEDULER_PORT_FAILED"
SCHEDULER_PORT_RESULT_INVALID = "SCHEDULER_PORT_RESULT_INVALID"

_VALID_ACTIONS = frozenset({"add", "cancel", "list"})


def execute_scheduler_skill(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Submit a normalized skill request only through an injected governed port."""

    payload = dict(args or {})
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"scheduler::{action}")
    if action not in _VALID_ACTIONS:
        return _rejected(
            action=action,
            manifest_ref=manifest_ref,
            reason_code=SCHEDULER_ACTION_INVALID,
            detail=f"unsupported scheduler action: {action or '<empty>'}",
        )

    ctx = context if isinstance(context, dict) else {}
    port = ctx.get("governed_scheduler_port")
    authority = ctx.get("secure_ingress_authority")
    submit = getattr(port, "execute_scheduler_skill", None)
    if not callable(submit) or not _is_admissible_authority(authority):
        return _rejected(
            action=action,
            manifest_ref=manifest_ref,
            reason_code=SCHEDULER_GOVERNED_CONTEXT_REQUIRED,
            detail=(
                "explicit governed_scheduler_port and admissible "
                "SecureIngressAuthority are required"
            ),
        )

    try:
        result = submit(action=action, payload=payload, authority=authority)
    except Exception as exc:
        _logger.warning("governed scheduler port failed (action=%s): %s", action, exc)
        return _rejected(
            action=action,
            manifest_ref=manifest_ref,
            reason_code=SCHEDULER_PORT_FAILED,
            detail=str(exc),
            status="FAILED",
        )

    if inspect.isawaitable(result):
        close = getattr(result, "close", None)
        if callable(close):
            close()
        return _rejected(
            action=action,
            manifest_ref=manifest_ref,
            reason_code=SCHEDULER_SYNC_ADAPTER_REQUIRED,
            detail="async SchedulerCommandService cannot run inside a synchronous skill executor",
            status="BLOCKED",
        )
    if not isinstance(result, Mapping) or not str(result.get("status") or "").strip():
        return _rejected(
            action=action,
            manifest_ref=manifest_ref,
            reason_code=SCHEDULER_PORT_RESULT_INVALID,
            detail="governed scheduler port returned no explicit terminal status",
        )

    output = dict(result)
    output.setdefault("action", action)
    output.setdefault("manifest_ref", manifest_ref)
    return output


def _is_admissible_authority(value: Any) -> bool:
    if not isinstance(value, SecureIngressAuthority) or not value.admissible:
        return False
    return all(
        str(item or "").strip()
        for item in (
            value.request_id,
            value.principal_id,
            value.claim_ref,
            value.audit_ref,
            value.body_sha256,
        )
    )


def _rejected(
    *,
    action: str,
    manifest_ref: str,
    reason_code: str,
    detail: str,
    status: str = "REJECTED",
) -> dict[str, Any]:
    return {
        "status": status,
        "manifest_ref": manifest_ref,
        "action": action,
        "reason_code": reason_code,
        "detail": detail,
    }
