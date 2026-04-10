from __future__ import annotations

import logging
import os
from typing import Any

import requests

from host_brain.core.ids import now_ms

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
        if not self.enabled:
            return {"ok": False, "reason": "BRIDGE_DISABLED"}
        if not self.host_base_url:
            return {"ok": False, "reason": "HOST_BASE_URL_MISSING"}

        url = f"{self.host_base_url}/internal/tribe_event"
        headers = {"Content-Type": "application/json"}
        if self.token:
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
            return {
                "ok": bool(data.get("accepted", False)),
                "event_id": data.get("event_id"),
                "ts_ms": int(data.get("ts_ms") or now_ms()),
                "status_code": response.status_code,
            }
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("edge tribe event publish failed: %s", exc)
            return {"ok": False, "reason": "HTTP_ERROR", "detail": str(exc)}

    # ── T-168：下行指令接收与分发 ─────────────────────────────────────────────

    def receive_command(
        self,
        *,
        command_type: str,
        source_agent: str,
        target_agent: str | None = None,
        payload: dict[str, Any] | None = None,
        command_id: str | None = None,
        schema_version: str = "tribe.command.v1",
        somatic_agent: Any | None = None,
        peripheral_agent: Any | None = None,
        channel_agent: Any | None = None,
    ) -> dict[str, Any]:
        """T-168：接收来自 host_brain 的下行 Tribe 指令并分发到对应 Agent。

        由 edge_client /internal/tribe_command 端点调用，根据 command_type
        和 target_agent 将指令路由到 SomaticAgent 或 PeripheralAgent。

        路由规则：
        - command_type in ("EMOTION_STATE_CHANGED", "EXPRESSION_SET", "VTS_HOTKEY")
          → SomaticAgent.handle_tribe_command()
        - command_type in ("GATE_CONTROL", "GATE_CONTROL_COMMAND")
          → ChannelAgent.on_gate_control_command()
        - command_type in ("GATE_OPEN", "GATE_CLOSE", "PLATFORM_MUTE")
          → PeripheralAgent.handle_tribe_command()
        - target_agent 精确匹配优先于 command_type 路由

        Args:
            command_type: 指令类型（大写），如 "EMOTION_STATE_CHANGED"/"GATE_CONTROL"。
            source_agent: 来源 Agent，如 "limbic_agent"/"cortex_agent"。
            target_agent: 目标 Agent（可选），如 "edge.somatic"/"edge.peripheral"。
            payload: 指令载荷（可选）。
            command_id: 指令 ID（可选，用于调试追踪）。
            schema_version: 协议版本，默认 "tribe.command.v1"。
            somatic_agent: SomaticAgent 实例（可选，由调用方注入）。
            peripheral_agent: PeripheralAgent 实例（可选，由调用方注入）。
            channel_agent: ChannelAgent 实例（可选，由调用方注入）。

        Returns:
            {"ok": bool, "dispatched": bool, "command_type": str,
             "command_id": str, "reason": str|None}
        """
        resolved_type = str(command_type or "").strip().upper()
        resolved_target = str(target_agent or "").strip().lower()
        resolved_payload = dict(payload or {})
        resolved_id = str(command_id or "").strip()

        # 构造统一指令包，传递给 Agent 的 handle_tribe_command()
        # command_type 转为小写 snake_case 供 Agent 内部路由
        _type_map: dict[str, str] = {
            "EMOTION_STATE_CHANGED": "emotion_state",
            "EMOTION_STATE_COMMAND": "emotion_state",
            "EXPRESSION_SET": "expression_set",
            "VTS_HOTKEY": "vts_hotkey",
            "GATE_CONTROL": "gate_open",   # 兼容旧格式，payload 中含 gate 字段
            "GATE_CONTROL_COMMAND": "gate_control",
            "GATE_OPEN": "gate_open",
            "GATE_CLOSE": "gate_close",
            "PLATFORM_MUTE": "platform_mute",
            "PLATFORM_MUTE_COMMAND": "platform_mute",
        }
        agent_command_type = _type_map.get(resolved_type, resolved_type.lower())

        command_pkg: dict[str, Any] = {
            "command_type": agent_command_type,
            "payload": resolved_payload,
            "command_id": resolved_id,
            "source_agent": str(source_agent or "host_brain").strip(),
            "schema_version": str(schema_version or "tribe.command.v1"),
        }

        # ── 路由决策 ──────────────────────────────────────────────────────
        # 1. target_agent 精确匹配
        _somatic_targets = {"edge.somatic", "somatic_agent", "somatic"}
        _peripheral_targets = {"edge.peripheral", "peripheral_agent", "peripheral"}
        _channel_targets = {"edge.channel", "channel_agent", "channel"}
        _somatic_types = {"emotion_state", "expression_set", "vts_hotkey"}
        _peripheral_types = {"gate_open", "gate_close", "platform_mute"}
        _channel_types = {"gate_control"}

        use_somatic = (
            resolved_target in _somatic_targets
            or (not resolved_target and agent_command_type in _somatic_types)
        )
        use_peripheral = (
            resolved_target in _peripheral_targets
            or (not resolved_target and agent_command_type in _peripheral_types)
        )
        use_channel = (
            resolved_target in _channel_targets
            or (not resolved_target and agent_command_type in _channel_types)
        )

        # GATE_CONTROL 旧格式：根据 payload.gate 字段决定 open/close
        if resolved_type in {"GATE_CONTROL", "GATE_CONTROL_COMMAND"} and "gate" in resolved_payload:
            gate_val = str(resolved_payload.get("gate") or "open").strip().lower()
            command_pkg["command_type"] = "gate_close" if gate_val == "closed" else "gate_open"

        try:
            if use_somatic and somatic_agent is not None:
                result = somatic_agent.handle_tribe_command(command_pkg)
                return {
                    "ok": bool(result.get("ok", True)),
                    "dispatched": True,
                    "command_type": resolved_type,
                    "command_id": resolved_id,
                    "reason": result.get("reason"),
                    "routed_to": "somatic_agent",
                }
            elif use_channel and channel_agent is not None:
                gate_open = bool(resolved_payload.get("gate_open", True))
                if "gate" in resolved_payload:
                    gate_open = str(resolved_payload.get("gate") or "").strip().lower() != "closed"
                result = channel_agent.on_gate_control_command(
                    gate_open=gate_open,
                    reason=resolved_payload.get("reason"),
                    platform=resolved_payload.get("platform"),
                    ts_ms=int(resolved_payload.get("ts_ms") or 0),
                )
                return {
                    "ok": bool(result.get("accepted", True)),
                    "dispatched": True,
                    "command_type": resolved_type,
                    "command_id": resolved_id,
                    "reason": result.get("reason"),
                    "routed_to": "channel_agent",
                }
            elif use_peripheral and peripheral_agent is not None:
                if command_pkg.get("command_type") == "gate_control":
                    gate_open = bool(resolved_payload.get("gate_open", True))
                    command_pkg["command_type"] = "gate_open" if gate_open else "gate_close"
                result = peripheral_agent.handle_tribe_command(command_pkg)
                return {
                    "ok": bool(result.get("ok", True)),
                    "dispatched": True,
                    "command_type": resolved_type,
                    "command_id": resolved_id,
                    "reason": result.get("reason"),
                    "routed_to": "peripheral_agent",
                }
            else:
                logger.info(
                    "EdgeTribeBridge.receive_command: 无匹配 Agent，command_type=%s target=%s",
                    resolved_type, resolved_target,
                )
                return {
                    "ok": True,
                    "dispatched": False,
                    "command_type": resolved_type,
                    "command_id": resolved_id,
                    "reason": "NO_MATCHING_AGENT",
                    "routed_to": None,
                }
        except Exception as exc:
            logger.warning("EdgeTribeBridge.receive_command 分发异常: %s", exc)
            return {
                "ok": False,
                "dispatched": False,
                "command_type": resolved_type,
                "command_id": resolved_id,
                "reason": str(exc),
                "routed_to": None,
            }


__all__ = ["EdgeTribeBridge"]
