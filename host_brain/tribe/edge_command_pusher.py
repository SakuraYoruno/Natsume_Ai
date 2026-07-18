"""EdgeCommandPusher：host_brain → edge_client 下行指令推送客户端。

Phase 6 T-124：实现大脑族向躯干族推送下行指令的 HTTP 客户端。
支持两类指令：
  - emotion_state_command：LimbicAgent 推送情绪状态变化，驱动 vts_driver 表情
  - gate_control_command：CortexAgent 推送门控指令，控制 ChannelAgent 消息路由
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import requests

from host_brain.core.ids import now_ms
from host_brain.runtime.action_preview_proof import ActionPreviewExecutionProof

logger = logging.getLogger("Natsume.tribe.edge_command_pusher")


def _normalize_base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    if not netloc:
        return ""
    return f"{scheme}://{netloc}".rstrip("/")


def _resolve_edge_command_base_url() -> str:
    for env_name in ("EDGE_COMMAND_BASE_URL", "HOST_EDGE_RPC_BASE_URL", "EDGE_RPC_BASE_URL"):
        candidate = _normalize_base_url(os.environ.get(env_name) or "")
        if candidate:
            return candidate

    rpc_host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = str(
        os.environ.get("EDGE_COMMAND_PORT")
        or os.environ.get("EDGE_RPC_PORT")
        or "17800"
    ).strip()
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = 17800
    return f"http://{rpc_host}:{port}"


def build_edge_command_preview_args(
    *,
    command_type: str,
    target_agent: str,
    payload: dict[str, Any] | None = None,
    source_agent: str = "host_brain",
) -> dict[str, Any]:
    """Return the exact semantic command that Preview authority must bind."""

    return {
        "command_type": str(command_type or "").strip() or "UNKNOWN",
        "source_agent": str(source_agent or "host_brain").strip() or "host_brain",
        "target_agent": str(target_agent or "").strip() or "edge.unknown",
        "payload": dict(payload or {}),
    }


def edge_command_preview_scope(action_args: dict[str, Any]) -> str:
    material = json.dumps(
        dict(action_args or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "tribe-command://sha256/" + hashlib.sha256(material).hexdigest()


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
        failure_cooldown_ms: int = 15000,
        max_failure_cooldown_ms: int = 300000,
        enabled: bool = True,
        session: requests.Session | None = None,
        enforce_action_preview: bool = True,
    ) -> None:
        self.edge_base_url = str(edge_base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout_ms = max(200, int(timeout_ms))
        self.failure_cooldown_ms = max(0, int(failure_cooldown_ms))
        self.max_failure_cooldown_ms = max(self.failure_cooldown_ms, int(max_failure_cooldown_ms))
        self.enabled = bool(enabled and bool(self.edge_base_url))
        self._session = session or requests.Session()
        # Kept as a compatibility argument only.  No constructor, factory or
        # singleton injection is allowed to downgrade the terminal Preview
        # gate; ``False`` therefore has no effect.
        _ = enforce_action_preview
        self._enforce_action_preview = True
        self._state_lock = threading.Lock()
        self._cooldown_until_ms = 0
        self._consecutive_failures = 0
        self._last_failure_log_ms = 0

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
        edge_base_url = _resolve_edge_command_base_url()
        token = str(os.environ.get("HOST_INTERNAL_TRIBE_TOKEN") or "").strip()
        enabled_raw = str(os.environ.get("EDGE_COMMAND_PUSHER_ENABLED") or "true").strip().lower()
        enabled = enabled_raw not in {"0", "false", "no", "off"}
        timeout_ms_raw = os.environ.get("EDGE_COMMAND_TIMEOUT_MS")
        failure_cooldown_ms_raw = os.environ.get("EDGE_COMMAND_FAILURE_COOLDOWN_MS")
        max_failure_cooldown_ms_raw = os.environ.get("EDGE_COMMAND_MAX_FAILURE_COOLDOWN_MS")
        try:
            timeout_ms = int(timeout_ms_raw) if timeout_ms_raw else 2000
        except (TypeError, ValueError):
            timeout_ms = 2000
        try:
            failure_cooldown_ms = int(failure_cooldown_ms_raw) if failure_cooldown_ms_raw else 15000
        except (TypeError, ValueError):
            failure_cooldown_ms = 15000
        try:
            max_failure_cooldown_ms = (
                int(max_failure_cooldown_ms_raw) if max_failure_cooldown_ms_raw else 300000
            )
        except (TypeError, ValueError):
            max_failure_cooldown_ms = 300000
        return cls(
            edge_base_url=edge_base_url,
            token=token or None,
            timeout_ms=timeout_ms,
            failure_cooldown_ms=failure_cooldown_ms,
            max_failure_cooldown_ms=max_failure_cooldown_ms,
            enabled=enabled,
            enforce_action_preview=True,
        )

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _current_cooldown_remaining_ms(self) -> int:
        with self._state_lock:
            return max(0, int(self._cooldown_until_ms) - self._now_ms())

    def _record_success(self) -> None:
        with self._state_lock:
            self._consecutive_failures = 0
            self._cooldown_until_ms = 0

    def _resolve_failure_backoff_ms(self, *, failures: int) -> int:
        if self.failure_cooldown_ms <= 0:
            return 0
        attempt = max(1, int(failures))
        cooldown_ms = int(self.failure_cooldown_ms) * (2 ** max(0, attempt - 1))
        return min(max(int(self.failure_cooldown_ms), cooldown_ms), int(self.max_failure_cooldown_ms))

    def _record_failure(self, *, reason: str, detail: str | None = None) -> None:
        if self.failure_cooldown_ms <= 0:
            return
        now = self._now_ms()
        cooldown_ms = int(self.failure_cooldown_ms)
        with self._state_lock:
            self._consecutive_failures += 1
            cooldown_ms = self._resolve_failure_backoff_ms(failures=self._consecutive_failures)
            self._cooldown_until_ms = max(
                int(self._cooldown_until_ms),
                now + cooldown_ms,
            )
            should_log = (now - int(self._last_failure_log_ms)) >= 5000
            if should_log:
                self._last_failure_log_ms = now
        if should_log:
            logger.warning(
                "edge command pusher entering cooldown: cooldown_ms=%s failures=%s reason=%s detail=%s",
                cooldown_ms,
                self._consecutive_failures,
                reason,
                (detail or "")[:200],
            )

    def push_command(
        self,
        *,
        command_type: str,
        target_agent: str,
        payload: dict[str, Any] | None = None,
        source_agent: str = "host_brain",
        action_preview_proof: ActionPreviewExecutionProof | None = None,
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
        action_args = build_edge_command_preview_args(
            command_type=command_type,
            target_agent=target_agent,
            source_agent=source_agent,
            payload=payload,
        )
        if action_preview_proof is None:
            return {
                "ok": False,
                "reason": "ACTION_PREVIEW_TASK_AUTHORITY_REQUIRED",
                "detail": (
                    "legacy EdgeCommandPusher has no immutable TaskGraph/node "
                    "authority and is disabled before network I/O"
                ),
            }
        expected_scope = edge_command_preview_scope(action_args)
        if (
            not isinstance(action_preview_proof, ActionPreviewExecutionProof)
            or action_preview_proof.action_kind != "edge.tribe_command"
            or action_preview_proof.action_scope != expected_scope
            or action_preview_proof.action_args != action_args
        ):
            return {
                "ok": False,
                "reason": "ACTION_PREVIEW_DRIFT",
                "detail": "Edge command differs from its exact Preview proof",
            }
        try:
            action_preview_proof.service.require_execution(
                event_id=action_preview_proof.event_id,
                **action_preview_proof.boundary_dict(),
            )
        except Exception as exc:
            return {
                "ok": False,
                "reason": str(
                    getattr(exc, "reason_code", None)
                    or "ACTION_PREVIEW_INVALID"
                ),
                "detail": str(getattr(exc, "detail", None) or exc)[:300],
            }
        cooldown_remaining_ms = self._current_cooldown_remaining_ms()
        if cooldown_remaining_ms > 0:
            return {
                "ok": False,
                "reason": "COOLDOWN_ACTIVE",
                "cooldown_remaining_ms": cooldown_remaining_ms,
            }

        url = f"{self.edge_base_url}/internal/tribe_command"
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Internal-Token"] = self.token
        body = {
            **action_args,
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
                self._record_failure(
                    reason="HTTP_REJECTED",
                    detail=f"status={response.status_code} body={response.text[:200]}",
                )
                logger.warning(
                    "edge command push rejected: status=%s body=%s",
                    response.status_code, response.text[:200],
                )
                return {"ok": False, "reason": "HTTP_REJECTED", "status_code": response.status_code}
            data = response.json() if response.content else {}
            accepted = bool(data.get("accepted", True))
            # 优先读取 edge 回包的 dispatched 字段；未提供时与 accepted 保持一致
            dispatched = bool(data["dispatched"]) if "dispatched" in data else accepted
            if accepted and dispatched:
                self._record_success()
            return {
                "ok": bool(accepted and dispatched),
                "accepted": accepted,
                "dispatched": dispatched,
                "command_id": data.get("command_id"),
                "ts_ms": int(data.get("ts_ms") or now_ms()),
                "status_code": response.status_code,
            }
        except Exception as exc:  # pragma: no cover - network path
            self._record_failure(reason="HTTP_ERROR", detail=str(exc))
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
        action_preview_proof: ActionPreviewExecutionProof | None = None,
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
            action_preview_proof=action_preview_proof,
        )

    def push_gate_control(
        self,
        *,
        gate_open: bool,
        reason: str | None = None,
        platform: str | None = None,
        source_agent: str = "cortex_agent",
        action_preview_proof: ActionPreviewExecutionProof | None = None,
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
            action_preview_proof=action_preview_proof,
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
    if pusher is not None and type(pusher) is not EdgeCommandPusher:
        raise TypeError("EdgeCommandPusher injection must use the canonical gated type")
    with _PUSHER_LOCK:
        if _PUSHER is None:
            _PUSHER = pusher or EdgeCommandPusher.from_env()
    return _PUSHER


def set_edge_command_pusher(pusher: EdgeCommandPusher | None) -> None:
    """强制替换全局单例（用于测试）。"""
    global _PUSHER
    if pusher is not None and type(pusher) is not EdgeCommandPusher:
        raise TypeError("EdgeCommandPusher injection must use the canonical gated type")
    _PUSHER = pusher


__all__ = [
    "EdgeCommandPusher",
    "build_edge_command_preview_args",
    "edge_command_preview_scope",
    "get_edge_command_pusher",
    "init_edge_command_pusher",
    "set_edge_command_pusher",
]
