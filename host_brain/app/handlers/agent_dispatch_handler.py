"""Agent 分派 Handler：Supervised Graph 内唯一显式 Agent 分派边界（P3/P3.5）。

职责：
1. 从 context 读取图本地 allowlist（agent_dispatch_allowlist），无则用默认
2. 读取 active_agent_id，在 allowlist 内映射到具体 handler，执行后 sanitize 并写回 artifact
3. 按 supervisor_dispatch.need_judge 写入 __next_node__，供 executor 路由
约束：超出 allowlist 时安全降级，不跨图逃逸。
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_CODE, AGENT_ID_RETRIEVAL, AGENT_ID_TRANSLATE
from host_brain.app.graph_config_builder import (
    CHAT_RENDER_NODE_ID,
    JUDGE_AGENT_NODE_ID,
)

# 默认 allowlist（当图配置未提供 agent_dispatch_allowlist 时）；结构测试可断言
DEFAULT_AGENT_DISPATCH_ALLOWLIST: frozenset[str] = frozenset({AGENT_ID_CODE, AGENT_ID_TRANSLATE, AGENT_ID_RETRIEVAL})

# agent_id → handler_id（与注册名一致）
_ALLOWLIST_HANDLER_MAP: dict[str, str] = {
    AGENT_ID_CODE: "code_agent",
    AGENT_ID_TRANSLATE: "translate_agent",
    AGENT_ID_RETRIEVAL: "retrieval_agent",  # P11 新增
}


def agent_dispatch_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Agent 分派 handler：allowlist 内执行对应 handler，否则安全降级。

    Args:
        payload: 当前图 payload（含 active_agent_id、supervisor_dispatch 等）
        context: 执行上下文；可含 invoke_handler(handler_id, payload, context) 用于调用子 handler

    Returns:
        更新后的 payload（含 agent_artifacts 等）；不写 __next_node__。
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    node_id = str(ctx.get("node_id") or "agent_dispatch_node").strip()
    # P3.5：allowlist 由图配置提供（context.agent_dispatch_allowlist），无则用默认
    allowlist_raw = ctx.get("agent_dispatch_allowlist")
    allowlist = (
        frozenset(str(x).strip() for x in allowlist_raw if str(x).strip())
        if isinstance(allowlist_raw, (list, tuple))
        else DEFAULT_AGENT_DISPATCH_ALLOWLIST
    )

    active_agent_id = str(payload.get("active_agent_id") or "").strip()

    # 1. 超出 allowlist：安全降级，不调用任何子 handler，不跨图逃逸
    if active_agent_id not in allowlist:
        out = dict(payload)
        artifacts = dict(out.get("agent_artifacts") or {})
        artifacts[active_agent_id or "unknown"] = {
            "artifact_ref": f"artifact://{request_id}/agent_dispatch_fallback_{int(time.time() * 1000)}",
            "summary": "Agent 不在 Supervised 图 allowlist 内，已安全降级",
            "confidence": 0.0,
            "risk_flags": ["AGENT_NOT_IN_SUPERVISED_ALLOWLIST"],
            "ts_ms": int(time.time() * 1000),
        }
        out["agent_artifacts"] = artifacts
        _set_next_node_by_judge(out)
        return _sanitize_payload(out)

    # 2. 映射到 handler_id 并执行
    handler_id = _ALLOWLIST_HANDLER_MAP.get(active_agent_id, "")
    if not handler_id:
        return _safe_fallback(payload, request_id, "handler_id_missing")

    invoke = ctx.get("invoke_handler")
    if not callable(invoke):
        return _safe_fallback(payload, request_id, "invoke_handler_unavailable")

    try:
        child_out = invoke(handler_id, payload, ctx)
    except Exception as exc:
        return _safe_fallback(
            payload, request_id,
            f"HANDLER_ERROR:{type(exc).__name__}",
            str(exc),
        )

    # 3. 合并子 handler 输出并 sanitize（禁止透传 __next_node__ 等）
    out = dict(payload)
    if isinstance(child_out, dict):
        for k, v in child_out.items():
            if k == "agent_artifacts" and isinstance(v, dict):
                out_artifacts = dict(out.get("agent_artifacts") or {})
                out_artifacts.update(v)
                out["agent_artifacts"] = out_artifacts
            elif k not in ("__orchestration_status__", "__orchestration_reason_code__", "__orchestration_detail__"):
                out[k] = v
    _set_next_node_by_judge(out)
    return _sanitize_payload(out)


def _set_next_node_by_judge(payload: dict[str, Any]) -> None:
    """按 supervisor_dispatch.need_judge 写入 __next_node__，供 executor 路由。"""
    supervisor_info = dict(payload.get("supervisor_dispatch") or {})
    need_judge = bool(supervisor_info.get("need_judge", True))
    payload["__next_node__"] = JUDGE_AGENT_NODE_ID if need_judge else CHAT_RENDER_NODE_ID


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """剥离除 __next_node__ 外的控制字段（本 handler 有权写 __next_node__）。"""
    out = dict(payload)
    for ctrl in ("__orchestration_status__", "__orchestration_reason_code__", "__orchestration_detail__"):
        out.pop(ctrl, None)
    return out


def _safe_fallback(
    payload: dict[str, Any],
    request_id: str,
    reason: str,
    detail: str | None = None,
) -> dict[str, Any]:
    """allowlist 外或执行异常时的安全降级输出。"""
    out = dict(payload)
    artifacts = dict(out.get("agent_artifacts") or {})
    aid = str(payload.get("active_agent_id") or "unknown").strip()
    artifacts[aid] = {
        "artifact_ref": f"artifact://{request_id}/agent_dispatch_fallback_{int(time.time() * 1000)}",
        "summary": detail or reason,
        "confidence": 0.0,
        "risk_flags": [reason],
        "ts_ms": int(time.time() * 1000),
    }
    out["agent_artifacts"] = artifacts
    _set_next_node_by_judge(out)
    return _sanitize_payload(out)
