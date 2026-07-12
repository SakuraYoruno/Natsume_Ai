"""Secret-free public traces for canonical model attempts."""

from __future__ import annotations

from typing import Any

from host_brain.model_runtime.contracts import ModelAttemptResult


def model_attempt_result_trace(result: ModelAttemptResult) -> dict[str, Any]:
    route = result.route_decision
    final = result.final_target
    return {
        "request_id": result.request_id,
        "success": bool(result.success),
        "cached": bool(result.cached),
        "route_decision_id": route.decision_id,
        "route_reason": route.reason,
        "route_max_attempts": route.max_attempts,
        "attempt_ids": [item.attempt_id for item in result.attempts],
        "attempts": [
            {
                "attempt_id": item.attempt_id,
                "route_decision_id": item.route_decision_id,
                "attempt_index": item.attempt_index,
                "status": item.status.value,
                "retry_reason": item.retry_reason,
                "adapter_id": item.adapter_id,
                "provider": item.provider,
                "model_id": item.model_id,
                "prompt_id": item.prompt_id,
                "prompt_version": item.prompt_version,
                "prompt_digest": item.prompt_digest,
                "latency_ms": item.latency_ms,
                "error_code": item.error_code,
            }
            for item in result.attempts
        ],
        "final_adapter_id": final.adapter_id if final is not None else "",
        "final_provider": final.provider if final is not None else "",
        "final_model": final.model_id if final is not None else "",
        "error_code": result.error_code,
    }


__all__ = ["model_attempt_result_trace"]
