"""AIRLOCK 出口净化与报告强制执行模块。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import AIRLOCK_BLOCKED_EGRESS
from host_brain.core.ids import now_ms


DEFAULT_REMOVED_FIELDS = frozenset(
    {
        "hard_state",
        "governor_state",
        "governor_snapshot",
        "rule_trace",
        "tokens",
        "snapshot_refs",
        "constitution_full_text",
        "constitution_text",
        "internal_policies",
    }
)

DEFAULT_REDACTED_FIELDS = frozenset(
    {
        "api_key",
        "apikey",
        "secret",
        "token",
        "password",
        "session_id",
        "nonce",
    }
)


# 这是出口载荷与证明的不可变配对：报告的摘要必须能唯一对应净化后的载荷，
# 防止下游把一份旧报告复用于不同的内容。
@dataclass(frozen=True)
class EgressSanitization:
    """出站传输的载荷净化结果。"""

    sanitized_payload: dict[str, Any]
    sanitization_report_json: dict[str, Any]


# 闸门只给出放行决定和审计信息；它本身不拥有网络发送或其他副作用权限。
@dataclass(frozen=True)
class EgressGateResult:
    """AIRLOCK 出口门控返回的决策结果。"""

    allowed: bool
    reason_code: str | None
    payload: dict[str, Any]
    sanitization_report_json: dict[str, Any] | None
    detail: str


def build_sanitization_report(
    payload: dict[str, Any],
    *,
    remove_fields: set[str] | None = None,
    redact_fields: set[str] | None = None,
) -> EgressSanitization:
    """对载荷进行净化并生成结构化报告。"""

    # 策略按字段名大小写无关匹配：内部状态直接移除，仍需保留结构的秘密改为占位符。
    remove_keys = {x.strip().lower() for x in (remove_fields or DEFAULT_REMOVED_FIELDS) if str(x).strip()}
    redact_keys = {x.strip().lower() for x in (redact_fields or DEFAULT_REDACTED_FIELDS) if str(x).strip()}
    removed_paths: list[str] = []
    redaction_paths: list[str] = []

    sanitized = _sanitize_node(
        value=payload,
        path="",
        remove_keys=remove_keys,
        redact_keys=redact_keys,
        removed_paths=removed_paths,
        redaction_paths=redaction_paths,
    )
    assert isinstance(sanitized, dict)

    report = {
        "removed_fields": _dedupe(removed_paths),
        "redactions": _dedupe(redaction_paths),
        "payload_digest": _digest_json(sanitized),
    }
    return EgressSanitization(sanitized_payload=sanitized, sanitization_report_json=report)


def gate_egress_payload(
    payload: dict[str, Any],
    *,
    sanitization_report_json: dict[str, Any] | None,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
) -> EgressGateResult:
    """强制要求出站载荷携带有效的净化报告。"""

    # 先对当前载荷重新计算摘要，拒绝缺失、畸形或与此载荷不闭合的净化证明。
    payload_digest = _digest_json(payload)
    report_valid, detail = _validate_report(sanitization_report_json, payload_digest=payload_digest)
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    if not report_valid:
        # 拒绝记录仅保存路径、摘要和原因，避免审计账本反而扩散待保护原文。
        if ledger is not None:
            ledger.log_airlock(
                {
                    "request_id": request_id,
                    "direction": "EGRESS",
                    "reason_code": AIRLOCK_BLOCKED_EGRESS,
                    "removed_fields": [],
                    "redactions": [],
                    "payload_digest": payload_digest,
                    "quarantine_digest": None,
                    "ingress_classification": None,
                    "blocked": 1,
                    "detail": detail,  # 拒绝原因
                    "ts_ms": now_value,
                }
            )
        return EgressGateResult(
            allowed=False,
            reason_code=AIRLOCK_BLOCKED_EGRESS,
            payload=payload,
            sanitization_report_json=sanitization_report_json,
            detail=detail,
        )

    report = sanitization_report_json or {}
    if ledger is not None:
        ledger.log_airlock(
            {
                "request_id": request_id,
                "direction": "EGRESS",
                "reason_code": None,
                "removed_fields": report.get("removed_fields") or [],
                "redactions": report.get("redactions") or [],
                "payload_digest": report.get("payload_digest"),
                "quarantine_digest": None,
                "ingress_classification": None,
                "blocked": 0,
                    "detail": "出口已放行",
                "ts_ms": now_value,
            }
        )

    return EgressGateResult(
        allowed=True,
        reason_code=None,
        payload=payload,
        sanitization_report_json=report,
        detail="出口已放行",
    )


def sanitize_and_gate_egress(
    payload: dict[str, Any],
    *,
    request_id: str | None = None,
    ledger: SQLiteLedger | None = None,
    ts_ms: int | None = None,
    remove_fields: set[str] | None = None,
    redact_fields: set[str] | None = None,
) -> EgressGateResult:
    """出站网关使用的便捷辅助函数。"""

    # 该组合入口固定“先净化、再验证”的顺序，调用方无法因漏传报告而绕过闸门。
    result = build_sanitization_report(
        payload=payload,
        remove_fields=remove_fields,
        redact_fields=redact_fields,
    )
    return gate_egress_payload(
        payload=result.sanitized_payload,
        sanitization_report_json=result.sanitization_report_json,
        request_id=request_id,
        ledger=ledger,
        ts_ms=ts_ms,
    )


def _validate_report(report: dict[str, Any] | None, *, payload_digest: str) -> tuple[bool, str]:
    # 摘要不一致通常表示报告陈旧或被替换；宁可阻断也不能按未知净化状态出站。
    if not isinstance(report, dict):
        return False, "缺少 sanitization_report_json"

    removed_fields = report.get("removed_fields")
    redactions = report.get("redactions")
    report_digest = report.get("payload_digest")

    if not isinstance(removed_fields, list):
        return False, "净化报告缺少 removed_fields 列表"
    if not isinstance(redactions, list):
        return False, "净化报告缺少 redactions 列表"
    if not isinstance(report_digest, str) or not report_digest:
        return False, "净化报告缺少 payload_digest"
    if report_digest != payload_digest:
        return False, "净化报告 payload_digest 不匹配"

    return True, "验证通过"


def _sanitize_node(
    *,
    value: Any,
    path: str,
    remove_keys: set[str],
    redact_keys: set[str],
    removed_paths: list[str],
    redaction_paths: list[str],
) -> Any:
    # 递归时只收集字段路径，不把敏感值写入报告，保留最小化的可解释审计线索。
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            key_name = str(key)
            key_lower = key_name.lower()
            child_path = f"{path}.{key_name}" if path else key_name
            if key_lower in remove_keys:
                removed_paths.append(child_path)
                continue
            if key_lower in redact_keys:
                redaction_paths.append(child_path)
                out[key_name] = "[REDACTED]"
                continue
            out[key_name] = _sanitize_node(
                value=child,
                path=child_path,
                remove_keys=remove_keys,
                redact_keys=redact_keys,
                removed_paths=removed_paths,
                redaction_paths=redaction_paths,
            )
        return out

    if isinstance(value, list):
        out_list: list[Any] = []
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            out_list.append(
                _sanitize_node(
                    value=item,
                    path=child_path,
                    remove_keys=remove_keys,
                    redact_keys=redact_keys,
                    removed_paths=removed_paths,
                    redaction_paths=redaction_paths,
                )
            )
        return out_list

    return value


def _digest_json(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
