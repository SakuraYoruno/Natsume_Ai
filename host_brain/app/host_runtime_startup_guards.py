"""不拥有 ASGI 生命周期、但在路由发布前失败关闭的 Host 启动守卫。"""

from __future__ import annotations

import json
from typing import Any


def command_ingress_hmac_secrets(
    *,
    input_settings: Any,
    scheduler_settings: Any,
) -> dict[str, str]:
    """合并受控命令入口密钥来源，返回给组合层而不记录或暴露 secret。

    JSON 配置、Edge 网关和 scheduler 可以共存；相同 key ID 保留优先来源，避免低优先
    兼容配置在启动时静默替换轮换中的密钥。
    """
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
    terminal_reconciliation_enabled: bool,
    command_ingress: Any,
    security: Any,
) -> None:
    """取消图启用时，在路由激活前验证完整的修复与签名 authority 链。

    取消会改变已有任务事实，故 operation/effect/terminal 三类 reconciliation 及强制
    Secure Ingress 缺任一项都拒绝启动，不能仅因某个 HTTP endpoint 可注册而放行。
    """

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
    if not terminal_reconciliation_enabled:
        raise RuntimeError(
            "TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_TERMINAL_RECONCILIATION"
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
    """公开签名操作修复端口前失败关闭，要求实际可执行的受治理 command gateway。"""

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
    """公开签名 Effect 修复端口前失败关闭，避免观察端点变成无授权写入口。"""

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
    """公开历史终态修复前失败关闭；终态重写必须走签名受治理网关。"""

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
    """Effect 或终态库存未达生产就绪时阻止取消，防止留下无法收敛的副作用。"""

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
    """确认入口处于 ENFORCE 且组合出的 gateway 真能执行，不能只检查配置开关。"""
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
