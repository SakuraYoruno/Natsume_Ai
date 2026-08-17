"""主入口审计仓储失效时使用的耐久追加式后备通道。

它只保存已脱敏的 ``IngressAuditEvent``，不恢复允许决定；``flush`` 加 ``fsync`` 让返回
引用尽可能对应已落盘记录，进程内锁避免同一实例的并发写入交错。
"""

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
    """面向灾难降级的 JSONL 审计写入器，不承担主审计查询或事务权威。"""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, event: IngressAuditEvent) -> str:
        """安全校验后原子追加一条事件并强制刷盘，返回仅用于关联的审计引用。"""
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
