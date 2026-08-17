"""AIRLOCK 入口隔离分类与草稿降级处理。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import AIRLOCK_BLOCKED_INGRESS
from host_brain.core.ids import now_ms


CLASS_FACT = "FACT"
CLASS_DRAFT_CODE = "DRAFT_CODE"
CLASS_DRAFT_INSTRUCTION = "DRAFT_INSTRUCTION"
CLASS_UNKNOWN = "UNKNOWN"

CLASSIFICATION_SET = {
    CLASS_FACT,
    CLASS_DRAFT_CODE,
    CLASS_DRAFT_INSTRUCTION,
    CLASS_UNKNOWN,
}

_CODE_HINT = re.compile(
    r"```|^\s*(def\s+\w+|class\s+\w+|import\s+\w+|from\s+\w+\s+import|function\s+\w+|SELECT\s+|INSERT\s+|UPDATE\s+|DELETE\s+)",
    re.IGNORECASE | re.MULTILINE,
)
_INSTRUCTION_HINT = re.compile(
    r"\b(run|execute|delete|drop|sudo|rm\s+-rf|powershell|cmd|pip\s+install|npm\s+install|curl|wget)\b",
    re.IGNORECASE,
)


# 不论分类为何，入站内容都只作为草稿保存；分类结果绝不等同于执行授权。
@dataclass(frozen=True)
class IngressQuarantineResult:
    """入口内容隔离检查结果。"""

    classification: str
    draft_only: bool
    allow_execute: bool
    reason_code: str
    quarantine_digest: str
    payload_digest: str
    quarantined_payload: dict[str, Any]


def quarantine_ingress(
    inbound_payload: Any,
    *,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> IngressQuarantineResult:
    """对入站内容分类、摘要并强制封装为仅草稿模式。

    无论启发式给出何种类别，返回值的 ``allow_execute`` 都固定为假；若后续要执行，必须
    经过独立的意图、治理和授权链路，不能借隔离结果升级权限。
    """

    # 先标准化再摘要，使审计能关联同一输入，而不必在账本中重复复制外部内容。
    normalized_text = _normalize_text(inbound_payload)
    payload_digest = _digest_json({"payload": normalized_text})
    classification = classify_ingress(normalized_text)
    # 包装层显式写死仅草稿标记，防止后续适配器把分类标签误当成信任或权限。
    quarantined_payload = {
        "content": normalized_text,
        "classification": classification,
        "draft_only": True,
    }
    quarantine_digest = _digest_json(quarantined_payload)
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    if ledger is not None:
        ledger.log_airlock(
            {
                "request_id": request_id,
                "direction": "INGRESS",
                "reason_code": AIRLOCK_BLOCKED_INGRESS,
                "removed_fields": [],
                "redactions": [],
                "payload_digest": payload_digest,
                "quarantine_digest": quarantine_digest,
                "ingress_classification": classification,
                "blocked": 1,
                "detail": "入站内容已隔离为仅草稿模式",
                "ts_ms": now_value,
            }
        )

    return IngressQuarantineResult(
        classification=classification,
        draft_only=True,
        allow_execute=False,
        reason_code=AIRLOCK_BLOCKED_INGRESS,
        quarantine_digest=quarantine_digest,
        payload_digest=payload_digest,
        quarantined_payload=quarantined_payload,
    )


def classify_ingress(content: str) -> str:
    """将入站文本分到固定 AIRLOCK 类别，仅用于风险分流而非真实性判断。"""

    # 本地、确定性的启发式只用于风险分流；未知或过短内容保持保守分类。
    text = (content or "").strip()
    if not text:
        return CLASS_UNKNOWN
    if _CODE_HINT.search(text):
        return CLASS_DRAFT_CODE
    if _INSTRUCTION_HINT.search(text):
        return CLASS_DRAFT_INSTRUCTION
    if len(text) < 4:
        return CLASS_UNKNOWN
    return CLASS_FACT


def is_valid_classification(value: str) -> bool:
    """验证分类标签是否属于审计可解释的固定闭集。"""
    # 边界适配器用固定枚举拒绝任意自造标签，确保审计语义可解释。
    return value in CLASSIFICATION_SET


def _normalize_text(value: Any) -> str:
    """将外部载荷转为稳定文本以供摘要和分类，不保留对象身份或可执行语义。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _digest_json(payload: Any) -> str:
    """以规范 JSON 计算可关联摘要，避免在账本中重复保存入站原文。"""
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
