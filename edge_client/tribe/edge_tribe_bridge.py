from __future__ import annotations

import logging
import os
from typing import Any

import requests

from natsume_contracts.edge.v1 import now_ms

logger = logging.getLogger("Natsume.edge.tribe_bridge")


class EdgeTribeBridge:
    """Edge -> Host 内部 Tribe 事件桥。"""

    def __init__(
        self,
        *,
        host_base_url: str,
        token: str | None = None,
        timeout_ms: int = 2500,
        enabled: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        self.host_base_url = str(host_base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout_ms = max(200, int(timeout_ms))
        self.enabled = bool(enabled and bool(self.host_base_url))
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "EdgeTribeBridge":
        host_base_url = str(os.environ.get("EDGE_TRIBE_HOST_BASE_URL") or "").strip()
        if not host_base_url:
            host_base_url = str(os.environ.get("EDGE_EVENT_REPORT_HOST_URL") or "").strip()
        if not host_base_url:
            rpc_host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
            host_base_url = f"http://{rpc_host}:18080"
        if "://" not in host_base_url:
            host_base_url = f"http://{host_base_url}"
        token = str(os.environ.get("HOST_INTERNAL_TRIBE_TOKEN") or "").strip()
        timeout_ms_raw = os.environ.get("EDGE_TRIBE_BRIDGE_TIMEOUT_MS")
        enabled_raw = str(os.environ.get("EDGE_TRIBE_BRIDGE_ENABLED") or "true").strip().lower()
        enabled = enabled_raw not in {"0", "false", "no", "off"}
        try:
            timeout_ms = int(timeout_ms_raw) if timeout_ms_raw else 2500
        except (TypeError, ValueError):
            timeout_ms = 2500
        return cls(
            host_base_url=host_base_url,
            token=token or None,
            timeout_ms=timeout_ms,
            enabled=enabled,
        )

    def publish_event(
        self,
        *,
        event_type: str,
        source_agent: str,
        target_agent: str | None = None,
        payload: dict[str, Any] | None = None,
        schema_version: str = "tribe.event.v1",
    ) -> dict[str, Any]:
        # 关闭桥接是显式降级，不应改为本地缓存后假装已送达；返回原因让调用链选择
        # 自己的恢复策略，同时避免 Edge 事件通道悄然获得持久重放职责。
        if not self.enabled:
            return {"ok": False, "reason": "BRIDGE_DISABLED"}
        if not self.host_base_url:
            return {"ok": False, "reason": "HOST_BASE_URL_MISSING"}

        url = f"{self.host_base_url}/internal/tribe_event"
        headers = {"Content-Type": "application/json"}
        if self.token:
            # 内部令牌只放在请求头，不进入事件体、日志或失败回显，避免感知/具身
            # payload 被保存或转发时携带跨边界的认证材料。
            headers["X-Internal-Token"] = self.token
        body = {
            "event_type": str(event_type or "").strip() or "UNKNOWN",
            "source_agent": str(source_agent or "").strip() or "edge.unknown",
            "target_agent": (str(target_agent).strip() or None) if target_agent is not None else None,
            "payload": dict(payload or {}),
            "schema_version": str(schema_version or "").strip() or "tribe.event.v1",
        }
        try:
            response = self._session.post(
                url,
                json=body,
                headers=headers,
                timeout=max(0.2, float(self.timeout_ms) / 1000.0),
            )
            if response.status_code >= 400:
                logger.warning("edge tribe event publish rejected: status=%s body=%s", response.status_code, response.text[:200])
                return {"ok": False, "reason": "HTTP_REJECTED", "status_code": response.status_code}
            data = response.json() if response.content else {}
            # Host 的 accepted 是唯一的远端接纳证据；HTTP 成功但业务未接纳不能
            # 升级为成功，以免上游错误清除待处理事件。
            return {
                "ok": bool(data.get("accepted", False)),
                "event_id": data.get("event_id"),
                "ts_ms": int(data.get("ts_ms") or now_ms()),
                "status_code": response.status_code,
            }
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("edge tribe event publish failed: %s", exc)
            return {"ok": False, "reason": "HTTP_ERROR", "detail": str(exc)}



__all__ = ["EdgeTribeBridge"]
