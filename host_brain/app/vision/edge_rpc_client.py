"""EdgeRpcClient：通过 Edge RPC 网关执行截图和自动化操作。

替代 ClawdbotClient，将截图/操作请求发送到 Edge 端的
  POST /v1/edge/screenshot
  POST /v1/edge/automate
两个接口，复用现有的 HMAC 鉴权机制。

使用方式：
    client = EdgeRpcClient(
        edge_base_url="http://192.168.1.100:17800",
        hmac_key="your-shared-key",
    )
    # 在 agent_routes.py 中替换 ClawdbotClient
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import logging
import time
import uuid
from typing import Any

from host_brain.app.vision.agent_loop import ClawdbotClient
from host_brain.app.vision.clawdbot_protocol import (
    ActionPlan,
    ActionReport,
    ActionStatus,
)
from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.edge_rpc_client")


class EdgeRpcClient(ClawdbotClient):
    """通过 Edge RPC 网关执行截图和自动化操作的客户端。

    继承 ClawdbotClient 接口，可直接替换 agent_loop 中的 ClawdbotClient。

    Args:
        edge_base_url: Edge RPC 网关地址，如 "http://192.168.1.100:17800"
        hmac_key: HMAC 签名密钥（来自 EdgeConfig.hmac_shared_key）
        timeout_sec: HTTP 请求超时秒数
        monitor_index: 截图目标显示器索引（1=主显示器）
    """

    def __init__(
        self,
        edge_base_url: str = "http://localhost:17800",
        hmac_key: str | None = None,
        timeout_sec: float = 30.0,
        monitor_index: int = 1,
    ) -> None:
        super().__init__(base_url=edge_base_url, timeout_sec=timeout_sec)
        self._edge_base_url = edge_base_url.rstrip("/")
        self._hmac_key = hmac_key
        self._monitor_index = monitor_index

    async def capture_screenshot(self, session_id: str) -> dict[str, Any]:
        """通过 Edge RPC 截图，返回 {image_b64, image_mime, width, height}。"""
        try:
            import httpx
            envelope = self._build_envelope(
                request_id=new_request_id(),
                payload={"monitor_index": self._monitor_index, "session_id": session_id},
            )
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(
                    f"{self._edge_base_url}/v1/edge/screenshot",
                    json=envelope,
                )
                resp.raise_for_status()
                data = resp.json()

            if not data.get("success"):
                error = data.get("detail") or data.get("reason_code") or "截图失败"
                _logger.warning("EdgeRpcClient: 截图失败: %s", error)
                return {"error": error, "image_b64": "", "image_mime": "image/png"}

            event = data.get("event") or {}
            # 兼容 Edge 端返回 image_b64 或 image_base64 两种字段名
            image_base64 = event.get("image_base64") or event.get("image_b64", "")
            return {
                "image_base64": image_base64,
                "image_mime": event.get("image_mime", "image/png"),
                "width": event.get("width", 0),
                "height": event.get("height", 0),
                "latency_ms": event.get("latency_ms", 0),
            }
        except Exception as exc:
            _logger.warning("EdgeRpcClient: 截图请求异常: %s", exc)
            return {"error": str(exc), "image_b64": "", "image_mime": "image/png"}

    async def execute_plan(self, plan: ActionPlan) -> ActionReport:
        """将 ActionPlan 转换为 Edge 自动化操作并执行。"""
        try:
            import httpx
            # 将 ActionPlan 的 actions 转换为 DesktopAction 格式
            desktop_actions = _convert_plan_to_desktop_actions(plan)
            envelope = self._build_envelope(
                request_id=plan.request_id,
                payload={
                    "session_id": plan.session_id,
                    "actions": desktop_actions,
                    "stop_on_error": plan.stop_on_error,
                },
            )
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(
                    f"{self._edge_base_url}/v1/edge/automate",
                    json=envelope,
                )
                resp.raise_for_status()
                data = resp.json()

            event = data.get("event") or {}
            success = bool(data.get("success"))

            from host_brain.app.vision.clawdbot_protocol import ActionResult
            results = []
            for r in event.get("results") or []:
                results.append(ActionResult(
                    action_index=r.get("action_index", 0),
                    action_type=r.get("action_type", ""),
                    status=ActionStatus.SUCCESS if r.get("success") else ActionStatus.FAILED,
                    latency_ms=r.get("latency_ms", 0),
                    detail=r.get("detail", ""),
                ))

            return ActionReport(
                request_id=plan.request_id,
                session_id=plan.session_id,
                status=ActionStatus.SUCCESS if success else ActionStatus.FAILED,
                goal_achieved=success and event.get("failed_count", 0) == 0,
                results=results,
                total_actions=event.get("total_actions", len(plan.actions)),
                success_count=event.get("success_count", 0),
                failed_count=event.get("failed_count", 0),
                total_latency_ms=event.get("total_latency_ms", 0),
                error=None if success else (data.get("detail") or "执行失败"),
            )
        except Exception as exc:
            _logger.warning("EdgeRpcClient: 执行操作异常: %s", exc)
            return ActionReport(
                request_id=plan.request_id,
                session_id=plan.session_id,
                status=ActionStatus.FAILED,
                goal_achieved=False,
                total_actions=len(plan.actions),
                failed_count=len(plan.actions),
                error=str(exc),
            )

    def _build_envelope(self, *, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """构建 Edge RPC 信封（与 edge_client.rpc.auth 协议保持一致）。"""
        import os as _os
        _clock_offset = int(_os.environ.get("HOST_EDGE_CLOCK_OFFSET_MS", "0") or "0")
        ts_ms = int(time.time() * 1000) + _clock_offset
        nonce = str(uuid.uuid4())
        signature = "insecure"
        if self._hmac_key:
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            message = f"{request_id}\n{ts_ms}\n{nonce}\n{canonical}"
            signature = _hmac.new(
                self._hmac_key.encode("utf-8"),
                message.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        envelope: dict[str, Any] = {
            "request_id": request_id,
            "ts_ms": ts_ms,
            "nonce": nonce,
            "signature": signature,
            "payload": payload,
        }
        return envelope


# ---------------------------------------------------------------------------
# ActionPlan → DesktopAction 转换
# ---------------------------------------------------------------------------

def _convert_plan_to_desktop_actions(plan: ActionPlan) -> list[dict[str, Any]]:
    """将 ActionPlan 的 actions 转换为 Edge DesktopAction 格式。

    ActionPlan 使用 clawdbot_protocol 的 ActionType，
    DesktopAction 使用 desktop_controller 的 DesktopActionType。
    两者字段基本对应，做简单映射即可。
    """
    from host_brain.app.vision.clawdbot_protocol import ActionType

    _TYPE_MAP = {
        ActionType.CLICK: "click",
        ActionType.RIGHT_CLICK: "right_click",
        ActionType.DOUBLE_CLICK: "double_click",
        ActionType.TYPE: "type",
        ActionType.KEY: "key",
        ActionType.SCROLL: "scroll",
        ActionType.DRAG: "drag",
        ActionType.WAIT: "wait",
        ActionType.SCREENSHOT: "screenshot",
        ActionType.DONE: "wait",  # DONE 映射为 wait 0ms（占位）
    }

    result = []
    for action in plan.actions:
        desktop_type = _TYPE_MAP.get(action.type, "wait")
        item: dict[str, Any] = {
            "type": desktop_type,
            "wait_after_ms": 150,
        }
        if action.x is not None:
            item["x"] = action.x
        if action.y is not None:
            item["y"] = action.y
        if action.x2 is not None:
            item["x2"] = action.x2
        if action.y2 is not None:
            item["y2"] = action.y2
        if action.text:
            item["text"] = action.text
        if action.scroll_amount is not None:
            item["amount"] = action.scroll_amount
        result.append(item)
    return result
