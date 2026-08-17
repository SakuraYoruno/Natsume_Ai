"""受治理 TaskGraph 节点执行所用的纯记录辅助函数。

这些函数只规范化、派生或校验记录值，不访问存储也不推进状态，使首次执行和
恢复路径能够用同一规则重建审查上下文、幂等标识和 worker 回执摘要。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import (
    _nested_mapping,
    _record_evidence_refs,
    _text,
    _version,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_WORKER_COMMIT_INVALID,
    GovernedRuntimeError,
)
from .heartbeat import DurableExecutionHeartbeatError
from .review_runtime import TaskNodeReviewContext


def build_review_context(
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    work_order: Any,
    node: Mapping[str, Any],
    attempt_id: str | None,
    reason: str,
    node_status_version: int | None = None,
    additional_evidence_refs: Sequence[str] = (),
) -> TaskNodeReviewContext:
    """从冻结准入、工单和节点记录构建可审计的人工审查上下文。"""
    node_payload = _nested_mapping(node, "node")
    side_effects = tuple(
        _text(item)
        for item in tuple(getattr(work_order, "allowed_actions", ()) or ())
        if _text(item)
    )
    input_refs = tuple(
        _text(item)
        for item in tuple(getattr(work_order, "input_refs", ()) or ())
        if _text(item)
    )
    # 合并输入、节点证据和补充证据并去重，审查者可追溯结论但不会得到重复引用。
    evidence_refs = tuple(
        dict.fromkeys(
            (
                *input_refs,
                *_record_evidence_refs(node),
                *(
                    _text(item)
                    for item in additional_evidence_refs
                    if _text(item)
                ),
            )
        )
    )
    review_policy = getattr(work_order, "review_policy", {})
    review_policy = dict(review_policy) if isinstance(review_policy, Mapping) else {}
    root_work_order_id = _text(
        getattr(work_order, "root_work_order_id", None)
        or getattr(work_order, "work_order_id", None)
        or admitted.work_order_id
    )
    return TaskNodeReviewContext(
        work_order_id=admitted.work_order_id,
        root_work_order_id=root_work_order_id,
        graph_id=admitted.graph_id,
        graph_revision=admitted.graph_revision,
        node_id=admitted.node_id,
        node_status_version=(
            _version(node)
            if node_status_version is None
            else int(node_status_version)
        ),
        attempt_id=attempt_id,
        requester=command.principal_id,
        reason=reason,
        risk_level=max(
            1,
            min(
                5,
                int(
                    node_payload.get("risk_level")
                    or getattr(work_order, "risk_level", 1)
                    or 1
                ),
            ),
        ),
        side_effects=side_effects,
        evidence_refs=evidence_refs,
        diff_ref=_text(review_policy.get("diff_ref")) or None,
    )


def _retry_attempt_available(
    node: Mapping[str, Any],
    *,
    attempt_no: int,
) -> bool:
    """判断下一次 Attempt 是否仍在持久化重试策略允许的范围内。"""
    payload = node.get("node")
    retry_policy = payload.get("retry_policy") if isinstance(payload, Mapping) else None
    max_attempts = retry_policy.get("max_attempts") if isinstance(retry_policy, Mapping) else None
    if (
        isinstance(attempt_no, bool)
        or not isinstance(attempt_no, int)
        or attempt_no < 1
        or isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts < 1
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable TaskNode has an invalid retry attempt policy",
        )
    return attempt_no < max_attempts


def _retry_usage_id(
    *,
    budget_id: str,
    attempt_id: str,
    node_only: bool = False,
) -> str:
    """生成重试预算扣费的确定性标识，避免同一 Attempt 被重复计费。"""
    material = f"{_text(budget_id)}\n{_text(attempt_id)}\nretry"
    if node_only:
        material += "\nnode-only"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"budget-usage://{digest}"


def _payload_digest(value: Mapping[str, Any]) -> str:
    """以规范有限 JSON 摘要绑定 worker COMMIT 的实际内容。"""
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GovernedRuntimeError(
            GOVERNED_WORKER_COMMIT_INVALID,
            "worker COMMIT is not canonical finite JSON",
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _is_terminal_commit(event: Mapping[str, Any]) -> bool:
    """仅把带 ``done`` 标记的 COMMIT 视为 worker 流的终结性提交。"""
    return _text(event.get("phase")).upper() == "COMMIT" and bool(event.get("done"))


def _heartbeat_failure_phase(exc: BaseException) -> str:
    """提取心跳失败阶段，以便恢复策略区分续租失败和生命周期失败。"""
    if isinstance(exc, DurableExecutionHeartbeatError):
        return _text(exc.phase) or "renewal"
    return _text(getattr(exc, "_heartbeat_phase", None)) or "lifecycle"


def _precharged_tool_call_count(worker_route: str) -> int:
    """仅对真正穿越工具或委派边界的路由预扣工具调用额度。

    未识别的兼容路由延续历史上的一次预扣；规范 chat 属于模型工作，不能误报为
    工具调用，否则预算和审计都会失真。
    """

    kind = str(worker_route or "").strip().partition(":")[0].lower()
    return 0 if kind == "chat" else 1


def _ref_token(value: Any) -> str:
    """将任意引用压缩为可安全嵌入派生标识的保守令牌。"""
    text = _text(value)
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in text).strip("-") or "unknown"


__all__ = [
    "build_review_context",
    "_retry_attempt_available",
    "_retry_usage_id",
    "_payload_digest",
    "_is_terminal_commit",
    "_heartbeat_failure_phase",
    "_precharged_tool_call_count",
    "_ref_token",
]
