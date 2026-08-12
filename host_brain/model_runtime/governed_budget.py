"""Task-bound model budget guards shared by Chat and Agent execution.

The durable Task budget remains authoritative.  This module only projects its
already-consumed ``remaining`` snapshot into a non-expandable model guard and,
for multi-call Agent loops, keeps an in-process remainder between provider
requests.  The Task runtime still persists the final usage and decides whether
the graph budget was exceeded.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
import math
import re
import time
from typing import Any

from host_brain.model_runtime.contracts import ModelBudgetGuard


GOVERNED_MODEL_BUDGET_INVALID = "GOVERNED_MODEL_BUDGET_INVALID"
GOVERNED_MODEL_BUDGET_EXHAUSTED = "GOVERNED_MODEL_BUDGET_EXHAUSTED"
GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID = (
    "GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID"
)

_MEASUREMENT_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)


class GovernedModelBudgetError(RuntimeError):
    """Fail-closed model budget projection or accounting error."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_MODEL_BUDGET_INVALID)
        self.detail = str(detail or "governed model budget is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_governed_model_budget_guard(
    *,
    budget: Mapping[str, Any],
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    require_model_cost: bool = True,
) -> ModelBudgetGuard:
    """Project one exact ACTIVE Task budget snapshot into a model guard."""

    # 这里只投影已处于 ACTIVE 的持久预算快照，不能从缺失/终态预算“恢复”额度；
    # guard 是对单次模型调用的不可扩张上限，最终结算权仍在 Task runtime。
    if not isinstance(require_model_cost, bool):
        _invalid("require_model_cost must be a boolean")

    raw = dict(budget or {})
    budget_id = _required_text(raw.get("budget_id"), field_name="budget_id")
    status = str(raw.get("status") or "").strip().upper()
    if status != "ACTIVE":
        _invalid("Task budget must be ACTIVE before a model call")
    remaining_raw = raw.get("remaining")
    if not isinstance(remaining_raw, Mapping):
        _invalid("Task budget remaining projection is required")
    remaining = {
        field_name: _non_negative_int(
            remaining_raw.get(field_name),
            field_name=f"remaining.{field_name}",
        )
        for field_name in (
            "token_count",
            "time_ms",
            "retry_count",
            "model_cost_micros",
        )
    }
    # 将剩余 retry 数转换成“本次调用最多 provider 尝试数”，并预留每次尝试的输出
    # token 上限，避免一次重试循环吞掉之后节点所需的全部额度。
    provider_attempts = remaining["retry_count"] + 1
    max_output_tokens = remaining["token_count"] // provider_attempts
    if (
        remaining["token_count"] <= 0
        or remaining["time_ms"] <= 0
        or remaining["model_cost_micros"] <= 0
        or max_output_tokens <= 0
    ):
        # 任一受治理维度耗尽都 fail-closed；不得只因仍有 token 就忽略时间或费用预算。
        raise GovernedModelBudgetError(
            GOVERNED_MODEL_BUDGET_EXHAUSTED,
            "Task budget cannot admit another model call",
        )
    try:
        return ModelBudgetGuard(
            budget_id=budget_id,
            graph_id=_required_text(graph_id, field_name="graph_id"),
            graph_revision=_positive_int(
                graph_revision,
                field_name="graph_revision",
            ),
            node_id=_required_text(node_id, field_name="node_id"),
            attempt_id=_required_text(attempt_id, field_name="attempt_id"),
            deadline_ms=remaining["time_ms"],
            max_provider_attempts=provider_attempts,
            max_token_count=remaining["token_count"],
            max_output_tokens=max_output_tokens,
            max_model_cost_micros=remaining["model_cost_micros"],
            required_usage_dimensions=(
                (
                    "token_count",
                    "completion_tokens",
                    "model_cost_micros",
                )
                if require_model_cost
                else ("token_count", "completion_tokens")
            ),
        )
    except (TypeError, ValueError) as exc:
        raise GovernedModelBudgetError(
            GOVERNED_MODEL_BUDGET_INVALID,
            "Task budget could not form a model guard",
        ) from exc


@dataclass(slots=True)
class GovernedAgentModelBudget:
    """Consume one Task model allowance across every request in an Agent loop."""

    base_guard: ModelBudgetGuard
    monotonic: Callable[[], float] = time.monotonic
    _started_at: float = field(init=False, repr=False)
    _token_count: int = field(default=0, init=False, repr=False)
    _retry_count: int = field(default=0, init=False, repr=False)
    _model_cost_micros: int = field(default=0, init=False, repr=False)
    _measurement_refs: set[str] = field(
        default_factory=set,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        # 计时使用单调时钟，避免系统校时使多调用 Agent 在预算上出现倒退或虚增。
        if not isinstance(self.base_guard, ModelBudgetGuard):
            raise TypeError("base_guard must be a ModelBudgetGuard")
        if not callable(self.monotonic):
            raise TypeError("monotonic must be callable")
        self._started_at = float(self.monotonic())

    def next_guard(self) -> ModelBudgetGuard:
        """Return the strictly smaller allowance for the next model request."""

        # 每次调用从同一冻结基线减去已测量消耗；返回 guard 永远不会比上一次更宽松，
        # 因此循环式 Agent 不能通过重复申请获得新额度。
        remaining_time = self.base_guard.deadline_ms - self.elapsed_ms
        remaining_tokens = self.base_guard.max_token_count - self._token_count
        remaining_cost = (
            self.base_guard.max_model_cost_micros
            - self._model_cost_micros
        )
        retry_limit = self.base_guard.max_provider_attempts - 1
        remaining_retries = retry_limit - self._retry_count
        if (
            remaining_time <= 0
            or remaining_tokens <= 0
            or remaining_cost <= 0
            or remaining_retries < 0
        ):
            # 任何维度耗尽立即阻止下一次 provider 调用，避免“部分还有余额”掩盖
            # 费用、重试或时限已经越界的事实。
            raise GovernedModelBudgetError(
                GOVERNED_MODEL_BUDGET_EXHAUSTED,
                "Agent exhausted its Task-bound model allowance",
            )
        max_output_tokens = min(
            self.base_guard.max_output_tokens,
            remaining_tokens,
        )
        if max_output_tokens <= 0:
            raise GovernedModelBudgetError(
                GOVERNED_MODEL_BUDGET_EXHAUSTED,
                "Agent has no output-token allowance for another model request",
            )
        return replace(
            self.base_guard,
            deadline_ms=remaining_time,
            max_provider_attempts=remaining_retries + 1,
            max_token_count=remaining_tokens,
            max_output_tokens=max_output_tokens,
            max_model_cost_micros=remaining_cost,
        )

    def consume_trace(self, trace: Mapping[str, Any]) -> None:
        """Consume one canonical ModelAttempt trace exactly once."""

        # 只接受新鲜、规范版本的 provider 测量；缓存命中或缺失 measurement 无法证明
        # 本轮实际消耗，因而不能用于推进受治理额度。
        payload = dict(trace or {})
        measurement = payload.get("budget_measurement")
        if payload.get("cached") is True or not isinstance(
            measurement,
            Mapping,
        ):
            self._measurement_invalid(
                "guarded Agent model result must be fresh and measured"
            )
        measured = dict(measurement)
        if measured.get("schema_version") != (
            "Natsume.model_runtime.budget_measurement.v1"
        ):
            self._measurement_invalid("Agent model measurement schema is invalid")
        measurement_ref = str(
            measured.get("measurement_ref") or ""
        ).strip()
        if not _MEASUREMENT_REF.fullmatch(measurement_ref):
            self._measurement_invalid("Agent model measurement_ref is invalid")
        if measurement_ref in self._measurement_refs:
            # measurement_ref 是一次供应商计量的幂等键，重复消费会把同一调用双重扣减，
            # 所以在写入累计值前 fail-closed。
            self._measurement_invalid(
                "Agent model measurement was consumed more than once"
            )
        attempt_count = _measurement_int(measured, "attempt_count")
        retry_count = _measurement_int(measured, "retry_count")
        token_count = _measurement_int(measured, "token_count")
        model_cost_micros = _measurement_int(
            measured,
            "model_cost_micros",
        )
        if attempt_count < 1 or retry_count != attempt_count - 1:
            self._measurement_invalid(
                "Agent model attempt and retry counts are inconsistent"
            )

        # 先在局部变量计算整组累计量并做全维度比较，全部通过后才更新内部状态，
        # 避免失败 trace 留下部分扣减而使后续恢复不可解释。
        next_tokens = self._token_count + token_count
        next_retries = self._retry_count + retry_count
        next_cost = self._model_cost_micros + model_cost_micros
        if (
            next_tokens > self.base_guard.max_token_count
            or next_retries > self.base_guard.max_provider_attempts - 1
            or next_cost > self.base_guard.max_model_cost_micros
            or self.elapsed_ms > self.base_guard.deadline_ms
        ):
            # 测量超出最初绑定的 guard 视为预算耗尽，而不是自动扩大限制或截断记录；
            # 这保留了对异常 provider 报告的审计证据。
            raise GovernedModelBudgetError(
                GOVERNED_MODEL_BUDGET_EXHAUSTED,
                "Agent model measurements exceeded the Task-bound allowance",
            )
        self._measurement_refs.add(measurement_ref)
        self._token_count = next_tokens
        self._retry_count = next_retries
        self._model_cost_micros = next_cost

    @property
    def elapsed_ms(self) -> int:
        return max(
            0,
            int(
                math.ceil(
                    (float(self.monotonic()) - self._started_at) * 1000
                )
            ),
        )

    @property
    def consumed(self) -> dict[str, int]:
        return {
            "token_count": self._token_count,
            "retry_count": self._retry_count,
            "model_cost_micros": self._model_cost_micros,
        }

    @staticmethod
    def _measurement_invalid(detail: str) -> None:
        raise GovernedModelBudgetError(
            GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID,
            detail,
        )


def require_guard_matches_work_order(
    guard: ModelBudgetGuard,
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
) -> None:
    """Reject a guard copied from another immutable TaskGraph node."""

    # 同一预算 ID 也不能跨图、版本或节点复用；把 guard 与不可变 WorkOrder 绑定，
    # 防止调用方将较宽松节点的额度挪给另一条执行路径。
    if not isinstance(guard, ModelBudgetGuard):
        raise TypeError("guard must be a ModelBudgetGuard")
    if (
        guard.graph_id != str(graph_id or "").strip()
        or guard.graph_revision != int(graph_revision)
        or guard.node_id != str(node_id or "").strip()
    ):
        raise GovernedModelBudgetError(
            GOVERNED_MODEL_BUDGET_INVALID,
            "model budget guard differs from the WorkOrder TaskGraph binding",
        )


def _measurement_int(value: Mapping[str, Any], field_name: str) -> int:
    raw = value.get(field_name)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise GovernedModelBudgetError(
            GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID,
            f"Agent model measurement {field_name} is invalid",
        )
    return int(raw)


def _non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _invalid(f"{field_name} must be a non-negative integer")
    return int(value)


def _positive_int(value: Any, *, field_name: str) -> int:
    parsed = _non_negative_int(value, field_name=field_name)
    if parsed < 1:
        _invalid(f"{field_name} must be positive")
    return parsed


def _required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        _invalid(f"{field_name} is required")
    return text


def _invalid(detail: str) -> None:
    raise GovernedModelBudgetError(GOVERNED_MODEL_BUDGET_INVALID, detail)


__all__ = [
    "GOVERNED_MODEL_BUDGET_EXHAUSTED",
    "GOVERNED_MODEL_BUDGET_INVALID",
    "GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID",
    "GovernedAgentModelBudget",
    "GovernedModelBudgetError",
    "build_governed_model_budget_guard",
    "require_guard_matches_work_order",
]
