"""Canonical, privacy-bounded helpers for model-attempt read models.

``model_attempt_log`` is an append-only lifecycle journal: one logical attempt
normally has a ``started`` row followed by exactly one terminal row.  Operator
statistics and health projections must never count those journal transitions
as two calls.  This module owns that normalization so every consumer applies
the same terminal-only rule.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


MODEL_ATTEMPT_TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "timed_out", "rejected", "cancelled"}
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _safe_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if normalized >= 0 else None


def latest_terminal_model_attempts(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return one latest terminal journal row per logical attempt."""

    # started 与 terminal 是同一逻辑尝试的生命周期记录；只选择最终行可避免运营指标
    # 把一次模型调用统计两遍，也能在重复终态写入时以稳定排序收敛到最新事实。
    selected: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        attempt_id = str(row.get("attempt_id") or "").strip()
        status = str(row.get("status") or "").strip().lower()
        if not attempt_id or status not in MODEL_ATTEMPT_TERMINAL_STATUSES:
            continue
        rank = (
            _safe_non_negative_int(row.get("ended_at_ms")) or 0,
            _safe_non_negative_int(row.get("ts_ms")) or 0,
            _safe_non_negative_int(row.get("id")) or 0,
        )
        current = selected.get(attempt_id)
        if current is None or rank >= current[0]:
            # 排名同时使用结束时间、记录时间与单调行 ID，处理时钟精度相同或重放写入，
            # 但不会依赖数据库返回顺序这种不稳定的偶然属性。
            selected[attempt_id] = (rank, row)
    return [
        item[1]
        for item in sorted(
            selected.values(),
            key=lambda item: (
                item[0],
                str(item[1].get("attempt_id") or ""),
            ),
        )
    ]


def model_attempt_node_id(row: Mapping[str, Any]) -> str | None:
    """Read the explicit governed model-node identity, never infer by model."""

    # provider/model 名称可以因路由降级或版本迁移变化；只有显式 model_node_id 才能
    # 将一次尝试安全归属到冻结的图节点、预算和治理决策。
    direct = str(row.get("model_node_id") or "").strip()
    if direct:
        return direct
    metadata = _mapping(row.get("metadata"))
    value = str(metadata.get("model_node_id") or "").strip()
    return value or None


def model_attempt_token_counts(
    row: Mapping[str, Any],
) -> tuple[int | None, int | None, int | None]:
    """Return provider-reported prompt/completion/total token measurements.

    A missing dimension remains ``None``: zero is a valid provider measurement
    and must not also mean "unmeasured".  Total tokens are derived only when
    both prompt and completion dimensions are explicitly measured.  We do not
    estimate tokens in this canonical attempt path; the legacy pool owns its
    explicit estimator in ``llm_token_usage_log``.
    """

    metadata = _mapping(row.get("metadata"))
    measurements = _mapping(metadata.get("provider_budget_measurements"))

    def _first_measurement(*values: Any) -> int | None:
        for value in values:
            normalized = _safe_non_negative_int(value)
            if normalized is not None:
                return normalized
        return None

    prompt = _first_measurement(
        row.get("prompt_tokens"),
        measurements.get("prompt_tokens"),
    )
    completion = _first_measurement(
        row.get("completion_tokens"),
        measurements.get("completion_tokens"),
    )
    total = _first_measurement(
        row.get("total_tokens"),
        measurements.get("token_count"),
    )
    if total is None and prompt is not None and completion is not None:
        # 仅在两个维度均由 provider 实测时才推导 total；缺测必须保持 None，不能用
        # 零或估算值填充，否则预算/成本视图会把“未知”误读为“免费”。
        total = prompt + completion
    return prompt, completion, total


__all__ = [
    "MODEL_ATTEMPT_TERMINAL_STATUSES",
    "latest_terminal_model_attempts",
    "model_attempt_node_id",
    "model_attempt_token_counts",
]
