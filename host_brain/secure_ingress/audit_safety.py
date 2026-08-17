"""主审计与紧急审计共享的最终秘密材料防线。

入口模型本身只应包含摘要；此检查在写入前再次扫描序列化事件，作为对未来字段扩展或
错误日志拼接的纵深防御，而不是将正则识别当作唯一脱敏机制。
"""

from __future__ import annotations

from dataclasses import asdict
import json
import re

from .models import IngressAuditEvent


_SECRET_RE = re.compile(
    r"(?:-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----|"
    r"\bBearer\s+[A-Za-z0-9._~+/-]{4,}|"
    r"\bsk-(?:live-|test-|proj-)?[A-Za-z0-9_-]{8,}|"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)\s*[:=])",
    re.IGNORECASE,
)


def contains_secret_material(value: object) -> bool:
    """检测常见私钥、Bearer、API 密钥与密码形态；仅用于阻止持久化。"""
    return bool(_SECRET_RE.search(str(value)))


def assert_audit_event_safe(event: IngressAuditEvent) -> None:
    """在入库前断言审计事件不含可用秘密；类型错误同样拒绝而非尝试序列化。"""
    if not isinstance(event, IngressAuditEvent):
        raise TypeError("audit sink requires IngressAuditEvent")
    rendered = json.dumps(
        asdict(event),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if contains_secret_material(rendered):
        raise ValueError("SECURE_INGRESS_AUDIT_SECRET_MATERIAL_FORBIDDEN")


__all__ = ["assert_audit_event_safe", "contains_secret_material"]
