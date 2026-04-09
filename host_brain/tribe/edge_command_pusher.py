"""EdgeCommandPusher：host_brain → edge_client 下行指令推送客户端。

Phase 6 T-124：实现大脑族向躯干族推送下行指令的 HTTP 客户端。
支持两类指令：
  - emotion_state_command：LimbicAgent 推送情绪状态变化，驱动 vts_driver 表情
  - gate_control_command：CortexAgent 推送门控指令，控制 ChannelAgent 消息路由
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any

import requests

from host_brain.core.ids import now_ms

logger = logging.getLogger("Natsume.tribe.edge_command_pusher")


class EdgeCommandPusher:
    """向 edge_client /internal/tribe_command 推送下行指令的 HTTP 客户端。

    使用方式：
        pusher = EdgeCommandPusher.from_env()
        pusher.push_emotion_state(emotion_tag="JOY", intensity=0.8)
        pusher.push_gate_control(gate_open=False, reason="platform_disconnected")
    """

    def __init__(
        self,
        *,
        edge_base_url: str,
        token: str | None = None,
        timeout_ms: int = 2000,
        enabled: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        self.edge_base_url = str(edge_base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout_ms = max(200, int(timeout_ms))
        self.enabled = bool(enabled and bool(self.edge_base_url))
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> "EdgeCommandPusher":
        """从环境变量构建实例。

        环境变量：
          EDGE_COMMAND_BASE_URL       edge_client 的 HTTP 基地址（优先）
          EDGE_RPC_HOST               edge_client 主机（备用，默认 127.0.0.1）
          EDGE_COMMAND_PORT           edge_client 端口（默认 18081）
          HOST_INTERNAL_TRIBE_TOKEN   内部鉴权 token（与上行共用）
          EDGE_COMMAND_PUSHER_ENABLED 是否启用（默认 true）
          EDGE_COMMAND_TIMEOUT_MS     超时毫秒（默认 2000）
        """
        edge_base_url = str(os.environ.get("EDGE_COMMAND_BASE_URL") or "").strip()
        if not edge_base_url:
            rpc_host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
            port = str(os.environ.get("EDGE_COMMAND_PORT") or "18081").strip() or "18081"
            edge_base_url = f"http://{rpc_host}:{port}"
        if "://" not in edge_base_url:
            edge_base_url = f"http://{edge_base_url}"
        token = str(os.environ.get("HOST_INTERNAL_TRIBE_TOKEN") or "").strip()
        enabled_raw = str(os.environ.get("EDGE_COMMAND_PUSHER_ENABLED") or "true").strip().lower()
        enabled = enabled_raw not in {"0", "false", "no", "off"}
        timeout_ms_raw = os.environ.get("EDGE_COMMAND_TIMEOUT_MS")
        try:
            timeout_ms = int(timeout_ms_raw) if timeout_ms_raw else 2000
        except (TypeError, ValueError):
            timeout_ms = 2000
        return cls(
            edge_base_url=edge_base_url,
            token=token or None,
            timeout_ms=timeout_ms,
            enabled=enabled,
        )

    def push_command(
        self,
        *,
        command_type: str,
        target_agent: str,
        payload: dict[str, Any] | None = None,
        source_agent: str = "host_brain",
    ) -> dict[str, Any]:
        """向 edge_client 推送下行指令。

        Args:
            command_type: 指令类型，如 "emotion_state_command" / "gate_control_command"
            target_agent: 目标 Agent ID，如 "edge.somatic" / "edge.channel"
            payload: 指令载荷
            source_agent: 来源 Agent ID（默认 "host_brain"）

        Returns:
            {"ok": bool, "reason": str | None, ...}
        """
        if not self.enabled:
            return {"ok": False, "reason": "PUSHER_DISABLED"}
        if not self.edge_base_url:
            return {"ok": False, "reason": "EDGE_BASE_URL_MISSING"}

        url = f"{self.edge_base_url}/internal/tribe_command"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Internal-Token"] = self.token
        body = {
            "command_type": str(command_type or "").strip() or "UNKNOWN",
            "source_agent": str(source_agent or "host_brain").strip() or "host_brain",
            "target_agent": str(target_agent or "").strip() or "edge.unknown",
            "payload": dict(payload or {}),
            "ts_ms": now_ms(),
            "schema_version": "tribe.command.v1",
        }
        try:
            response = self._session.post(
                url,
                json=body,
                headers=headers,
                timeout=max(0.2, float(self.timeout_ms) / 1000.0),
            )
            if response.status_code >= 400:
                logger.warning(
                    "edge command push rejected: status=%s body=%s",
                    response.status_code, response.text[:200],
                )
                return {"ok": False, "reason": "HTTP_REJECTED", "status_code": response.status_code}
            data = response.json() if response.content else {}
            return {
                "ok": bool(data.get("accepted", True)),
                "command_id": data.get("command_id"),
                "ts_ms": int(data.get("ts_ms") or now_ms()),
                "status_code": response.status_code,
            }
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("edge command push failed: %s", exc)
            return {"ok": False, "reason": "HTTP_ERROR", "detail": str(exc)}

    def push_emotion_state(
        self,
        *,
        emotion_tag: str,
        intensity: float | None = None,
        mood_state: str | None = None,
        session_id: str | None = None,
        source_agent: str = "limbic_agent",
    ) -> dict[str, Any]:
        """推送情绪状态变化指令到 edge.somatic，驱动 vts_driver 表情。

        T-125：LimbicAgent 处理完情绪推断后调用此方法，
        edge_client SomaticAgent 收到后调用 vts_driver.set_expression()。
        """
        return self.push_command(
            command_type="emotion_state_command",
            target_agent="edge.somatic",
            source_agent=source_agent,
            payload={
                "emotion_tag": str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
                "intensity": (
                    max(0.0, min(1.0, float(intensity)))
                    if isinstance(intensity, (int, float))
                    else None
                ),
                "mood_state": str(mood_state).strip().lower() if mood_state else None,
                "session_id": str(session_id or "").strip() or None,
            },
        )

    def push_gate_control(
        self,
        *,
        gate_open: bool,
        reason: str | None = None,
        platform: str | None = None,
        source_agent: str = "cortex_agent",
    ) -> dict[str, Any]:
        """推送门控指令到 edge.channel，控制消息路由开关。

        T-129：CortexAgent 在平台连接/断线时调用此方法，
        edge_client ChannelAgent 收到后更新门控状态。
        """
        return self.push_command(
            command_type="gate_control_command",
            target_agent="edge.channel",
            source_agent=source_agent,
            payload={
                "gate_open": bool(gate_open),
                "reason": str(reason or "").strip() or None,
                "platform": str(platform or "").strip() or None,
            },
        )


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_PUSHER: EdgeCommandPusher | None = None
_PUSHER_LOCK: threading.Lock = threading.Lock()


def get_edge_command_pusher() -> EdgeCommandPusher | None:
    """获取全局 EdgeCommandPusher 单例（未初始化时返回 None）。"""
    return _PUSHER


def init_edge_command_pusher(pusher: EdgeCommandPusher | None = None) -> EdgeCommandPusher:
    """初始化全局 EdgeCommandPusher 单例。

    Args:
        pusher: 可选，若不传则从环境变量自动构建。
    """
    global _PUSHER
    with _PUSHER_LOCK:
        if _PUSHER is None:
            _PUSHER = pusher or EdgeCommandPusher.from_env()
    return _PUSHER


def set_edge_command_pusher(pusher: EdgeCommandPusher | None) -> None:
    """强制替换全局单例（用于测试）。"""
    global _PUSHER
    _PUSHER = pusher


__all__ = [
    "EdgeCommandPusher",
    "get_edge_command_pusher",
    "init_edge_command_pusher",
    "set_edge_command_pusher",
]
