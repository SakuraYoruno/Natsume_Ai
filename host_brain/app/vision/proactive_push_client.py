"""ProactivePushClient：Host → Edge 主动对话消息推送客户端。

复用 EdgeRpcClient._build_envelope 的 HMAC 签名机制，
向 Edge 的 POST /v1/edge/proactive_push 接口推送主动对话消息。
"""

from __future__ import annotations

import logging
from typing import Any

from host_brain.app.vision.edge_rpc_client import EdgeRpcClient
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.proactive_push_client")


class ProactivePushClient:
    """同步 HTTP 客户端，将主动对话消息推送到 Edge。

    Args:
        edge_base_url: Edge RPC 网关地址，如 "http://127.0.0.1:17800"
        hmac_key: HMAC 签名密钥（来自 EdgeConfig.hmac_shared_key）
        timeout_sec: HTTP 请求超时秒数
    """

    def __init__(
        self,
        edge_base_url: str = "http://127.0.0.1:17800",
        hmac_key: str | None = None,
        timeout_sec: float = 5.0,
    ) -> None:
        self._edge_base_url = edge_base_url.rstrip("/")
        self._hmac_key = hmac_key
        self._timeout_sec = timeout_sec
        # 复用 EdgeRpcClient 的信封构建逻辑
        self._rpc = EdgeRpcClient(
            edge_base_url=edge_base_url,
            hmac_key=hmac_key,
            timeout_sec=timeout_sec,
        )

    def push(self, message: dict[str, Any]) -> dict[str, Any]:
        """同步推送主动对话消息到 Edge。

        Args:
            message: proactive_talk skill 生成的消息字典，包含
                     type / content / time_period / event_id / ts_ms 等字段。

        Returns:
            dict: Edge 响应体，包含 success / status / detail 等字段。
                  推送失败时返回 {"success": False, "error": str}。
        """
        try:
            import httpx
            request_id = new_request_id()
            envelope = self._rpc._build_envelope(
                request_id=request_id,
                payload=message,
            )
            resp = httpx.post(
                f"{self._edge_base_url}/v1/edge/proactive_push",
                json=envelope,
                timeout=self._timeout_sec,
            )
            resp.raise_for_status()
            data = resp.json()
            _logger.info(
                "ProactivePushClient: 推送成功 event_id=%s status=%s",
                message.get("event_id", ""),
                data.get("status", ""),
            )
            return dict(data)
        except Exception as exc:
            _logger.warning("ProactivePushClient: 推送失败: %s", exc)
            return {"success": False, "error": str(exc)}


def build_proactive_push_client() -> ProactivePushClient:
    """从环境变量构建 ProactivePushClient（供 http_hooks.py 调用）。"""
    import os
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    hmac_key = str(os.environ.get("EDGE_HMAC_SHARED_KEY") or "").strip() or None
    return ProactivePushClient(
        edge_base_url=f"http://{host}:{port}",
        hmac_key=hmac_key,
    )
