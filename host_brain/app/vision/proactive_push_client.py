"""ProactivePushClient：经受治理 Edge transport 推送主动对话消息。"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from host_brain.core.ids import new_request_id
from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof

_logger = logging.getLogger("Natsume.proactive_push_client")


def _run_edge_push_sync(call_factory: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(call_factory())

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result_box["result"] = asyncio.run(call_factory())
        except BaseException as exc:  # pragma: no cover - defensive thread bridge
            error_box["error"] = exc

    thread = threading.Thread(target=_runner, name="proactive-edge-rpc-push", daemon=True)
    thread.start()
    thread.join()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("result")


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

    def push(
        self,
        message: dict[str, Any],
        *,
        preview_proof: ActionPreviewExecutionProof | None = None,
    ) -> dict[str, Any]:
        """同步推送主动对话消息到 Edge。

        Args:
            message: proactive_talk skill 生成的消息字典，包含
                     type / content / time_period / event_id / ts_ms 等字段。

        Returns:
            dict: Edge 响应体，包含 success / status / detail 等字段。
                  推送失败时返回 {"success": False, "error": str}。
        """
        try:
            from host_brain.channels.edge_transport import submit_proactive_push  # noqa: PLC0415

            request_id = (
                preview_proof.action_scope
                if preview_proof is not None
                else new_request_id()
            )
            if preview_proof is not None and (
                preview_proof.action_kind != "proactive.ui_push"
                or preview_proof.action_args != dict(message or {})
                or not request_id
            ):
                return {
                    "success": False,
                    "status": "REJECTED",
                    "reason_code": "ACTION_PREVIEW_DRIFT",
                    "detail": "proactive payload or request scope differs from Preview proof",
                }

            async def _call() -> Any:
                return await submit_proactive_push(
                    message=message,
                    request_id=request_id,
                    timeout_ms=max(1000, int(float(self._timeout_sec) * 1000)),
                    base_url=self._edge_base_url,
                    shared_key=self._hmac_key,
                    action_preview_service=(
                        preview_proof.service if preview_proof is not None else None
                    ),
                    action_preview_event_id=(
                        preview_proof.event_id if preview_proof is not None else None
                    ),
                    action_preview_boundary=(
                        preview_proof.boundary_dict()
                        if preview_proof is not None
                        else None
                    ),
                )

            result = _run_edge_push_sync(_call)
            data = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
            log_fn = _logger.info if bool(data.get("success")) else _logger.warning
            log_fn(
                "ProactivePushClient: push finished event_id=%s success=%s status=%s reason=%s",
                message.get("event_id", ""),
                data.get("success"),
                data.get("status", ""),
                data.get("reason_code"),
            )
            return dict(data)
        except Exception as exc:
            _logger.warning("ProactivePushClient: 推送失败: %s", exc)
            return {"success": False, "error": str(exc)}


def build_proactive_push_client() -> ProactivePushClient:
    """从环境变量构建 ProactivePushClient（供 http_hooks.py 调用）。"""
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip()
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    hmac_key = (
        str(
            os.environ.get("HOST_EDGE_RPC_SHARED_KEY")
            or os.environ.get("EDGE_HMAC_SHARED_KEY")
            or ""
        ).strip()
        or None
    )
    return ProactivePushClient(
        edge_base_url=f"http://{host}:{port}",
        hmac_key=hmac_key,
    )
