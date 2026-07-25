"""Canonical receipt binding for accepted Host system-mode effects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.app.acceptance_effect_handler_common import _receipt


def sys_mode_transition_handler_receipt_ref(
    payload: Mapping[str, Any],
    *,
    outcome_status: str,
) -> str:
    """Return the canonical handler receipt for one frozen mode effect."""

    value = dict(payload or {})
    value.pop("_action_preview_context", None)
    value.pop("_action_preview_terminal_proof", None)
    required = {
        "operator_command_id",
        "request_id",
        "target_mode",
        "source",
        "reason",
        "event_id",
        "runtime_scope_id",
    }
    if set(value) != required:
        raise ValueError("sys_mode transition receipt payload drifted")
    status = str(outcome_status or "").strip().upper()
    if status not in {"APPLIED", "REPLAYED", "NO_CHANGE"}:
        raise ValueError("sys_mode transition outcome status is invalid")
    return _receipt(
        f"host-sys-mode-{status.lower().replace('_', '-')}",
        {**value, "handler_outcome_status": status},
    )


__all__ = ["sys_mode_transition_handler_receipt_ref"]
