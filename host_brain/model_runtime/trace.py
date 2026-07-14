"""Secret-free public traces for canonical model attempts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from host_brain.model_runtime.contracts import ModelAttemptResult


MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION = "Natsume.model_runtime.budget_measurement.v1"


def model_attempt_result_trace(result: ModelAttemptResult) -> dict[str, Any]:
    route = result.route_decision
    final = result.final_target
    trace = {
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
    trace["budget_measurement"] = _budget_measurement(result)
    return trace


def _budget_measurement(result: ModelAttemptResult) -> dict[str, Any]:
    attempts = tuple(result.attempts)
    identity = {
        "request_id": result.request_id,
        "route_decision_id": result.route_decision.decision_id,
        "attempt_ids": [item.attempt_id for item in attempts],
        "cached": bool(result.cached),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    measured: dict[str, Any] = {
        "schema_version": MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION,
        "measurement_ref": f"model-budget://{digest}",
        "attempt_count": len(attempts),
        "retry_count": max(0, len(attempts) - 1),
    }
    if result.cached:
        # A cache hit makes no provider attempt in this execution.
        measured["token_count"] = 0
        measured["model_cost_micros"] = 0
        return measured
    provider_rows = [
        dict(item.metadata.get("provider_budget_measurements") or {})
        for item in attempts
    ]
    if attempts and all("token_count" in row for row in provider_rows):
        measured["token_count"] = sum(int(row["token_count"]) for row in provider_rows)
    if attempts and all("model_cost_micros" in row for row in provider_rows):
        measured["model_cost_micros"] = sum(
            int(row["model_cost_micros"]) for row in provider_rows
        )
    return measured


__all__ = ["MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION", "model_attempt_result_trace"]
