from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
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

_DEFAULT_MAX_LOCAL_STEPS = 10
_DEFAULT_MAX_RETRY_COUNT = 2
_DEFAULT_LOCAL_LOOP_TIMEOUT_MS = 60_000


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _resolve_failure_text(key: str, **kwargs: Any) -> str:
    templates = _load_template_mapping("intent_agent_entry_handler.failure_texts")
    return _mapping_text(templates, key, **kwargs)


def _int_positive(value: Any, default: int) -> int:
    try:
        number = int(value)
        return number if number > 0 else default
    except (TypeError, ValueError):
        return default


def _int_non_neg(value: Any, default: int) -> int:
    try:
        number = int(value)
        return number if number >= 0 else default
    except (TypeError, ValueError):
        return default


def intent_agent_entry_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    invoke = ctx.get("invoke_model_node")
    if not callable(invoke):
        return _emit_failure(
            payload,
            request_id=request_id,
            failed_node=SUBGRAPH_NODE_IDS[0],
            error_code="INVOKE_MODEL_UNAVAILABLE",
            safe_fallback_reason=_resolve_failure_text(
                "invoke_model_unavailable",
            ),
        )

    binding = ctx.get("node_binding") or {}
    max_local_steps = _int_positive(binding.get(LOCAL_LOOP_CONFIG_MAX_STEPS), _DEFAULT_MAX_LOCAL_STEPS)
    max_retry_count = _int_non_neg(binding.get(LOCAL_LOOP_CONFIG_MAX_RETRY), _DEFAULT_MAX_RETRY_COUNT)
    local_loop_timeout_ms = _int_positive(
        binding.get(LOCAL_LOOP_CONFIG_TIMEOUT_MS),
        _DEFAULT_LOCAL_LOOP_TIMEOUT_MS,
    )

    out = dict(payload)
    subgraph_trace: list[dict[str, Any]] = []
    start_ms = int(time.time() * 1000)
    step_index = -1

    for step_index in range(max_local_steps):
        if step_index >= len(SUBGRAPH_NODE_IDS):
            break

        elapsed_ms = int(time.time() * 1000) - start_ms
        if elapsed_ms >= local_loop_timeout_ms:
            _append_step(
                subgraph_trace,
                step_index,
                LOCAL_LOOP_VIRTUAL_NODE_ID,
                STATUS_FAILED,
                "LOCAL_LOOP_TIMEOUT",
                "local loop timeout",
            )
            return _emit_local_loop_failure(
                out,
                request_id,
                LOCAL_LOOP_FAILURE_TIMEOUT,
                subgraph_trace,
                safe_fallback_reason=_resolve_failure_text(
                    "local_loop_timeout",
                    elapsed_ms=elapsed_ms,
                    timeout_ms=local_loop_timeout_ms,
                ),
            )

        node_id = SUBGRAPH_NODE_IDS[step_index]
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
                _append_step(subgraph_trace, step_index, node_id, STATUS_FAILED, "SUBGRAPH_NODE_ERROR", str(exc))
                if attempt >= max_retry_count:
                    return _emit_local_loop_failure(
                        out,
                        request_id,
                        LOCAL_LOOP_FAILURE_INTERNAL_ERROR,
                        subgraph_trace,
                        failed_node=node_id,
                        safe_fallback_reason=_resolve_failure_text(
                            "node_exception",
                            node_id=node_id,
                            max_retry_count=max_retry_count,
                            error=str(exc),
                        ),
                    )

    if step_index + 1 < len(SUBGRAPH_NODE_IDS):
        _append_step(
            subgraph_trace,
            step_index,
            LOCAL_LOOP_VIRTUAL_NODE_ID,
            STATUS_FAILED,
            "LOCAL_LOOP_MAX_STEPS",
            "max_local_steps reached",
        )
        return _emit_local_loop_failure(
            out,
            request_id,
            LOCAL_LOOP_FAILURE_MAX_STEPS,
            subgraph_trace,
            safe_fallback_reason=_resolve_failure_text(
                "max_steps_reached",
                max_local_steps=max_local_steps,
            ),
        )

    out["subgraph_trace"] = subgraph_trace
    out[PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY] = build_subgraph_commit_summary(
        subgraph_id=SUBGRAPH_ID,
        subgraph_trace=subgraph_trace,
    )
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
    subgraph_trace.append(
        {
            "step_index": step_index,
            "node_id": node_id,
            "ts_ms": int(time.time() * 1000),
            "status": status,
            "reason_code": reason_code,
            "detail": detail,
        }
    )


def _emit_local_loop_failure(
    out: dict[str, Any],
    request_id: str,
    error_code: str,
    subgraph_trace: list[dict[str, Any]],
    *,
    failed_node: str = LOCAL_LOOP_VIRTUAL_NODE_ID,
    safe_fallback_reason: str,
) -> dict[str, Any]:
    trace_ref = f"request_id={request_id}|subgraph_id={SUBGRAPH_ID}|trace_len={len(subgraph_trace)}"
    out[PAYLOAD_KEY_SUBGRAPH_FAILURE] = {
        "failed_node": failed_node,
        "error_code": error_code,
        "safe_fallback_reason": safe_fallback_reason,
        "trace_ref": trace_ref,
    }
    out["subgraph_trace"] = subgraph_trace
    out[PAYLOAD_KEY_SUBGRAPH_COMMIT_SUMMARY] = build_subgraph_commit_summary(
        subgraph_id=SUBGRAPH_ID,
        subgraph_trace=subgraph_trace,
    )
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
    out = dict(payload)
    subgraph_trace = [
        {
            "step_index": 0,
            "node_id": failed_node,
            "ts_ms": int(time.time() * 1000),
            "status": STATUS_FAILED,
            "reason_code": error_code,
            "detail": safe_fallback_reason,
        }
    ]
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
    for key in list(payload.keys()):
        if str(key or "").startswith("committed_"):
            payload.pop(key, None)
