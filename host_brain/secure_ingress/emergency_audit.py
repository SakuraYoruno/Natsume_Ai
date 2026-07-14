"""Durable append-only fallback when the primary ingress audit store fails."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
import uuid

from .models import IngressAuditEvent
from .audit_safety import assert_audit_event_safe


class FileEmergencyAuditSink:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: IngressAuditEvent) -> str:
        if not isinstance(event, IngressAuditEvent):
            raise TypeError("emergency audit requires IngressAuditEvent")
        assert_audit_event_safe(event)
        audit_ref = f"secure-ingress-emergency-audit://{uuid.uuid4().hex}"
        payload = {
            "audit_ref": audit_ref,
            "audit_status": "EMERGENCY_DURABLE",
            "event": asdict(event),
            "schema_version": "natsume.secure-ingress.emergency-audit.v1",
        }
        line = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        return audit_ref


__all__ = ["FileEmergencyAuditSink"]
