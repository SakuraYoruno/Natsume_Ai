"""Secret-free public traces for canonical model attempts."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from host_brain.model_runtime.contracts import ModelAttemptResult


MODEL_BUDGET_MEASUREMENT_SCHEMA_VERSION = "Natsume.model_runtime.budget_measurement.v1"


def model_attempt_result_trace(result: ModelAttemptResult) -> dict[str, Any]:
    # 对外 trace 只保留路由、版本、尝试和错误等可审计元数据，刻意排除提示词正文、
    # 模型响应与凭据；它既可供结算也不会把敏感上下文扩散到任务日志。
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
    # 用独立的规范计量载荷把“发生过哪些尝试”与“应扣减多少”关联，供受治理预算
    # 进行幂等消费，而不让调用方自行汇总费用。
    trace["budget_measurement"] = _budget_measurement(result)
    return trace


def _budget_measurement(result: ModelAttemptResult) -> dict[str, Any]:
    # measurement_ref 由请求、路由和尝试身份构成确定性摘要；同一结果重放得到同一键，
    # 预算层可据此拒绝双重扣减，且摘要不暴露 provider 返回内容。
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
        # 缓存命中在本次执行没有 provider 消耗，显式写零值以区分“零消耗”与
        # “测量缺失”；后者仍需由受治理调用链 fail-closed。
        measured["token_count"] = 0
        measured["model_cost_micros"] = 0
        return measured
    # 只在每个尝试均提供该维度时才汇总，禁止以部分测量推导较低费用，从而把缺失值
    # 伪装成预算余量。
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
