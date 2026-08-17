"""受治理 Operation 对账效果提案的纯校验器。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any


_EFFECT_TYPE = "task.operation_reconciliation"
_TARGET_ID = "operator_task_operation_reconcile"
_CORE_FIELDS = frozenset(
    {
        "evidence_ref",
        "evidence_digest",
        "reservation_id",
        "expected_version",
        "action",
        "reconciler_actor_id",
    }
)
_RECOVERY_CASE_FIELDS = frozenset(
    {
        "recovery_case_ref",
        "recovery_case_subject_sha256",
    }
)
_EVIDENCE_REF = re.compile(
    r"^operation-reconcile-evidence://sha256/[0-9a-f]{64}$"
)
_RECOVERY_CASE_REF = re.compile(
    r"^operation-recovery-hold://sha256/[0-9a-f]{64}$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def validate_operation_reconciliation_proposal(
    *,
    raw_effects: Any,
    frozen_intent: Mapping[str, Any],
    frozen_args: Mapping[str, Any],
    work_order_metadata: Mapping[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """仅返回与冻结输入逐字一致、且不含授权能力的对账 payload。

    Operator worker 只能复述受批准的对账意图，不能选择 reservation、动作或
    证据。任何字段漂移都失败关闭，避免在未知操作结果恢复期间扩张权限。
    """

    if not isinstance(raw_effects, Sequence) or isinstance(
        raw_effects,
        (str, bytes, bytearray),
    ):
        raise ValueError(
            "Operation reconciliation Operator must propose one effect"
        )
    if len(raw_effects) != 1:
        raise ValueError(
            "Operation reconciliation Operator must propose exactly one effect"
        )
    descriptor = _mapping(
        raw_effects[0],
        "Operation reconciliation effect descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise ValueError("Operation reconciliation descriptor fields drifted")
    if (
        descriptor.get("effect_type") != _EFFECT_TYPE
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise ValueError(
            "Operation reconciliation proposed an unsupported effect"
        )
    payload = _mapping(
        descriptor.get("payload"),
        "Operation reconciliation effect payload",
    )
    intent = _mapping(frozen_intent, "intent_draft")
    args = _mapping(frozen_args, "intent_draft.args")
    metadata = _mapping(work_order_metadata, "work_order.metadata")
    # 恢复案件绑定必须成对出现：只给 case 引用或只给主体摘要都无法证明该
    # 签名决策针对当前持有案件，而不是另一个同 reservation 的历史案件。
    present_case_fields = _RECOVERY_CASE_FIELDS.intersection(payload)
    if present_case_fields not in (frozenset(), _RECOVERY_CASE_FIELDS):
        raise ValueError(
            "Operation reconciliation recovery case fields are incomplete"
        )
    request_fields = set(_CORE_FIELDS | present_case_fields)
    required_keys = {
        "operator_command_id",
        "request_id",
        *request_fields,
    }
    # 全部业务字段只从冻结 intent 参数重建；不要用 worker payload 作为期望值，
    # 否则“比较相等”会退化为让执行器自行授予对账动作。
    expected = {
        "operator_command_id": "task_operation.reconcile",
        "request_id": str(request_id or "").strip(),
        **{field: args.get(field) for field in request_fields},
    }
    action = str(expected.get("action") or "").strip().upper()
    expected_version = expected.get("expected_version")
    evidence_ref = str(expected.get("evidence_ref") or "")
    if (
        set(payload) != required_keys
        or payload != expected
        or not expected["request_id"]
        or not str(expected.get("reservation_id") or "").strip()
        or type(expected_version) is not int
        or expected_version < 0
        or action not in {"COMMIT", "RELEASE", "REACQUIRE"}
        or not str(expected.get("reconciler_actor_id") or "").strip()
        or not _EVIDENCE_REF.fullmatch(evidence_ref)
        or str(expected.get("evidence_digest") or "")
        != "sha256:" + evidence_ref.rsplit("/", 1)[-1]
        or str(intent.get("action") or "").strip()
        != "operator_task_operation_reconciliation"
        or str(intent.get("target") or "").strip().lower() != "task_runtime"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip() != _TARGET_ID
        or str(metadata.get("operator_command_id") or "").strip()
        != "task_operation.reconcile"
    ):
        raise ValueError(
            "Operation reconciliation effect differs from frozen intent"
        )
    if present_case_fields:
        case_ref = str(expected.get("recovery_case_ref") or "")
        case_digest = str(
            expected.get("recovery_case_subject_sha256") or ""
        )
        if (
            not _RECOVERY_CASE_REF.fullmatch(case_ref)
            or not _SHA256.fullmatch(case_digest)
            or case_digest
            != "sha256:" + case_ref.rsplit("/", 1)[-1]
        ):
            raise ValueError(
                "Operation reconciliation recovery case binding is invalid"
            )
    return payload


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    """验证并深拷贝规范 JSON Mapping，切断调用方后续变异。"""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be canonical JSON") from exc


__all__ = ["validate_operation_reconciliation_proposal"]
