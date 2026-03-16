"""P4/P6：intent_agent_entry 节点 handler — 内部实现为透明子图（intent_agent_subgraph）。

职责：
- P4：顺序执行子图节点；P6-2：带硬边界的局部循环（max_local_steps / max_retry_count / local_loop_timeout_ms）
- 写入 payload["subgraph_commit_summary"]（§4.1 冻结）
- 失败时写入 payload["subgraph_failure"]（§4.2；P6 循环边界/超时/内部错误用 LOCAL_LOOP_*）
- 写入 payload["subgraph_trace"]，含 step 级（P6-1 §3）
- 不写入 committed_route_token（仅 Governor 可写）
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.integrations.graph_contract import (
    LOCAL_LOOP_CONFIG_MAX_RETRY,
    LOCAL_LOOP_CONFIG_MAX_STEPS,
    LOCAL_LOOP_CONFIG_TIMEOUT_MS,
    LOCAL_LOOP_FAILURE_INTERNAL_ERROR,
    LOCAL_LOOP_FAILURE_MAX_STEPS,
    LOCAL_LOOP_FAILURE_TIMEOUT,
    LOCAL_LOOP_VIRTUAL_NODE_ID,
    PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY,
    PAYLOAD_KEY_SUBGRAPH_FAILURE,
    STATUS_FAILED,
    STATUS_OK,
    build_subgraph_commit_summary,
)

SUBGRAPH_ID = "intent_agent_subgraph"
SUBGRAPH_NODE_IDS = [
    "core_reasoner_primary",
    "planner_constraints",
    "tool_planner",
]

# P6-1 建议默认值（可被 node_binding 覆盖）
_DEFAULT_MAX_LOCAL_STEPS = 10
_DEFAULT_MAX_RETRY_COUNT = 2
_DEFAULT_LOCAL_LOOP_TIMEOUT_MS = 60_000


def _int_positive(value: Any, default: int) -> int:
    """解析为正整数；无效或非正时返回 default。"""
    try:
        n = int(value)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _int_non_neg(value: Any, default: int) -> int:
    """解析为非负整数；无效或负时返回 default。"""
    try:
        n = int(value)
        return n if n >= 0 else default
    except (TypeError, ValueError):
        return default


def intent_agent_entry_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行 intent_agent_subgraph，带 P6-2 局部循环边界；写 summary / failure / step 级 trace。"""
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    invoke = ctx.get("invoke_model_node")
    if not callable(invoke):
        return _emit_failure(
            payload,
            request_id=request_id,
            failed_node=SUBGRAPH_NODE_IDS[0],
            error_code="INVOKE_MODEL_UNAVAILABLE",
            safe_fallback_reason="invoke_model_node 不可用，子图未执行",
        )

    binding = ctx.get("node_binding") or {}
    max_local_steps = _int_positive(binding.get(LOCAL_LOOP_CONFIG_MAX_STEPS), _DEFAULT_MAX_LOCAL_STEPS)
    max_retry_count = _int_non_neg(binding.get(LOCAL_LOOP_CONFIG_MAX_RETRY), _DEFAULT_MAX_RETRY_COUNT)
    local_loop_timeout_ms = _int_positive(binding.get(LOCAL_LOOP_CONFIG_TIMEOUT_MS), _DEFAULT_LOCAL_LOOP_TIMEOUT_MS)

    out = dict(payload)
    subgraph_trace: list[dict[str, Any]] = []
    start_ms = int(time.time() * 1000)
    step_index = -1

    for step_index in range(max_local_steps):
        if step_index >= len(SUBGRAPH_NODE_IDS):
            break
        elapsed_ms = int(time.time() * 1000) - start_ms
        if elapsed_ms >= local_loop_timeout_ms:
            _append_step(subgraph_trace, step_index, LOCAL_LOOP_VIRTUAL_NODE_ID, STATUS_FAILED, "LOCAL_LOOP_TIMEOUT", "local loop timeout")
            return _emit_local_loop_failure(
                out, request_id, LOCAL_LOOP_FAILURE_TIMEOUT, subgraph_trace,
                safe_fallback_reason=f"局部循环超时（{elapsed_ms}ms >= {local_loop_timeout_ms}ms）",
            )

        node_id = SUBGRAPH_NODE_IDS[step_index]
        last_exc: Exception | None = None
        for attempt in range(max_retry_count + 1):
            try:
                node_out = invoke(node_id, out)
                if node_out:
                    out.update(node_out)
                    if isinstance(node_out.get("__model_calls__"), list):
                        out["__model_calls__"] = list(out.get("__model_calls__") or []) + node_out["__model_calls__"]
                _append_step(subgraph_trace, step_index, node_id, STATUS_OK, None, "subgraph node executed")
                break
            except Exception as exc:
                last_exc = exc
                _append_step(subgraph_trace, step_index, node_id, STATUS_FAILED, "SUBGRAPH_NODE_ERROR", str(exc))
                if attempt >= max_retry_count:
                    return _emit_local_loop_failure(
                        out, request_id, LOCAL_LOOP_FAILURE_INTERNAL_ERROR, subgraph_trace,
                        failed_node=node_id,
                        safe_fallback_reason=f"子图节点 {node_id} 执行异常（重试{max_retry_count}次后）: {exc!s}",
                    )
        else:
            if last_exc is not None:
                return _emit_local_loop_failure(
                    out, request_id, LOCAL_LOOP_FAILURE_INTERNAL_ERROR, subgraph_trace,
                    failed_node=node_id,
                    safe_fallback_reason=f"子图节点 {node_id} 执行异常: {last_exc!s}",
                )

    # 未在 max_local_steps 内跑完所有节点
    if step_index + 1 < len(SUBGRAPH_NODE_IDS):
        _append_step(subgraph_trace, step_index, LOCAL_LOOP_VIRTUAL_NODE_ID, STATUS_FAILED, "LOCAL_LOOP_MAX_STEPS", "max_local_steps reached")
        return _emit_local_loop_failure(
            out, request_id, LOCAL_LOOP_FAILURE_MAX_STEPS, subgraph_trace,
            safe_fallback_reason=f"局部循环达到最大步数（{max_local_steps}）未完成所有节点",
        )

    out["subgraph_trace"] = subgraph_trace
    out[PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY] = build_subgraph_commit_summary(subgraph_id=SUBGRAPH_ID, subgraph_trace=subgraph_trace)
    _strip_committed(out)
    return out


def _append_step(
    subgraph_trace: list[dict[str, Any]],
    step_index: int,
    node_id: str,
    status: str,
    reason_code: str | None,
    detail: str,
) -> None:
    """P6-1 §3：step 级信息写入 subgraph_trace（至少 step_index, node_id, ts_ms, status）。"""
    subgraph_trace.append({
        "step_index": step_index,
        "node_id": node_id,
        "ts_ms": int(time.time() * 1000),
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
    })


def _emit_local_loop_failure(
    out: dict[str, Any],
    request_id: str,
    error_code: str,
    subgraph_trace: list[dict[str, Any]],
    *,
    failed_node: str = LOCAL_LOOP_VIRTUAL_NODE_ID,
    safe_fallback_reason: str,
) -> dict[str, Any]:
    """写 subgraph_failure（LOCAL_LOOP_*）+ subgraph_trace + summary，然后 return。"""
    trace_ref = f"request_id={request_id}|subgraph_id={SUBGRAPH_ID}|trace_len={len(subgraph_trace)}"
    out[PAYLOAD_KEY_SUBGRAPH_FAILURE] = {
        "failed_node": failed_node,
        "error_code": error_code,
        "safe_fallback_reason": safe_fallback_reason,
        "trace_ref": trace_ref,
    }
    out["subgraph_trace"] = subgraph_trace
    out[PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY] = build_subgraph_commit_summary(subgraph_id=SUBGRAPH_ID, subgraph_trace=subgraph_trace)
    _strip_committed(out)
    return out


def _emit_failure(
    payload: dict[str, Any],
    *,
    request_id: str,
    failed_node: str,
    error_code: str,
    safe_fallback_reason: str,
) -> dict[str, Any]:
    """子图未执行或入口失败时的归一化输出（§4.2）；trace 含 step_index 以符合 P6 step 级口径。"""
    out = dict(payload)
    subgraph_trace = [{
        "step_index": 0,
        "node_id": failed_node,
        "ts_ms": int(time.time() * 1000),
        "status": STATUS_FAILED,
        "reason_code": error_code,
        "detail": safe_fallback_reason,
    }]
    out["subgraph_trace"] = subgraph_trace
    out[PAYLOAD_KEY_SUBGRAPH_FAILURE] = {
        "failed_node": failed_node,
        "error_code": error_code,
        "safe_fallback_reason": safe_fallback_reason,
        "trace_ref": f"request_id={request_id}|subgraph_id={SUBGRAPH_ID}|trace_len=1",
    }
    out[PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY] = build_subgraph_commit_summary(
        subgraph_id=SUBGRAPH_ID,
        subgraph_trace=subgraph_trace,
    )
    _strip_committed(out)
    return out


def _strip_committed(payload: dict[str, Any]) -> None:
    """子图不得写入 committed_*（§3 验收 5）。"""
    for k in list(payload.keys()):
        if str(k or "").startswith("committed_"):
            payload.pop(k, None)
