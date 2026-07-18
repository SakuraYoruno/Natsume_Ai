"""Fail-closed Host startup checks without ASGI lifecycle ownership."""

from __future__ import annotations

import json
from typing import Any


def command_ingress_hmac_secrets(
    *,
    input_settings: Any,
    scheduler_settings: Any,
) -> dict[str, str]:
    configured: dict[str, str] = {}
    raw = str(input_settings.secure_ingress_hmac_secrets_json or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            configured.update(
                {
                    str(key).strip(): str(value)
                    for key, value in parsed.items()
                    if str(key).strip() and isinstance(value, str) and value
                }
            )
    edge_key = str(input_settings.edge_gateway_hmac_key_id or "").strip()
    edge_secret = str(input_settings.edge_gateway_hmac_secret or "")
    if edge_key and edge_secret:
        configured.setdefault(edge_key, edge_secret)
    scheduler_key = str(scheduler_settings.clock_key_id or "").strip()
    scheduler_secret = scheduler_settings.clock_hmac_secret.get_secret_value()
    if scheduler_key and scheduler_secret:
        configured.setdefault(scheduler_key, scheduler_secret)
    return configured


def assert_graph_cancellation_operator_startup(
    *,
    enabled: bool,
    operation_reconciliation_enabled: bool,
    effect_reconciliation_enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before route activation when cancellation authority is absent."""

    if not enabled:
        return
    if not operation_reconciliation_enabled:
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_OPERATION_RECONCILIATION"
        )
    if not effect_reconciliation_enabled:
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_EFFECT_RECONCILIATION"
        )
    _assert_operator_gateway(
        command_ingress=command_ingress,
        security=security,
        ingress_error=(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
        gateway_error="TASK_GRAPH_CANCELLATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE",
    )


def assert_task_operation_reconciliation_operator_startup(
    *,
    enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before exposing the signed operation reconciliation port."""

    if not enabled:
        return
    _assert_operator_gateway(
        command_ingress=command_ingress,
        security=security,
        ingress_error=(
            "TASK_OPERATION_RECONCILIATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
        gateway_error=(
            "TASK_OPERATION_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE"
        ),
    )


def assert_task_effect_reconciliation_operator_startup(
    *,
    enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before exposing the signed Effect reconciliation port."""

    if not enabled:
        return
    _assert_operator_gateway(
        command_ingress=command_ingress,
        security=security,
        ingress_error=(
            "TASK_EFFECT_RECONCILIATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
        gateway_error="TASK_EFFECT_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE",
    )


def assert_task_terminal_reconciliation_operator_startup(
    *,
    enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """Fail closed before exposing historical terminal-state repair."""

    if not enabled:
        return
    _assert_operator_gateway(
        command_ingress=command_ingress,
        security=security,
        ingress_error=(
            "TASK_TERMINAL_RECONCILIATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
        gateway_error=(
            "TASK_TERMINAL_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE"
        ),
    )


def assert_graph_cancellation_reconciliation_production_ready(
    *,
    enabled: bool,
    task_store: Any,
) -> None:
    """Block cancellation on unresolved Effect or terminal inventory."""

    if not enabled:
        return
    checker = getattr(
        task_store,
        "assert_effect_reconciliation_production_ready",
        None,
    )
    if not callable(checker):
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_EFFECT_PRODUCTION_GATE_UNAVAILABLE"
        )
    checker()
    terminal_checker = getattr(
        task_store,
        "assert_terminal_reconciliation_production_ready",
        None,
    )
    if not callable(terminal_checker):
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_TERMINAL_RECONCILIATION_"
            "PRODUCTION_GATE_UNAVAILABLE"
        )
    terminal_checker()


def _assert_operator_gateway(
    *,
    command_ingress: Any,
    security: Any,
    ingress_error: str,
    gateway_error: str,
) -> None:
    mode = getattr(command_ingress, "mode", None)
    mode_value = getattr(mode, "value", mode)
    if str(mode_value or "").strip().upper() != "ENFORCE":
        raise RuntimeError(ingress_error)
    composition = getattr(security, "obs_command_composition", None)
    if (
        composition is None
        or not bool(getattr(composition, "enabled", False))
        or not bool(getattr(composition, "execution_enabled", False))
        or not callable(
            getattr(getattr(composition, "ingress", None), "execute", None)
        )
    ):
        raise RuntimeError(gateway_error)


__all__ = [
    "assert_graph_cancellation_operator_startup",
    "assert_graph_cancellation_reconciliation_production_ready",
    "assert_task_effect_reconciliation_operator_startup",
    "assert_task_operation_reconciliation_operator_startup",
    "assert_task_terminal_reconciliation_operator_startup",
    "command_ingress_hmac_secrets",
]
