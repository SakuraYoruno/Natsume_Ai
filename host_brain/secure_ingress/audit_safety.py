"""Final secret-material guard shared by primary and emergency audit sinks."""

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
    return bool(_SECRET_RE.search(str(value)))


def assert_audit_event_safe(event: IngressAuditEvent) -> None:
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
