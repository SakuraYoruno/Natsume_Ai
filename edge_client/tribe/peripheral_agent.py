from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor


@dataclass(slots=True)
class PeripheralAgent:
    bridge: Any | None = None
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.peripheral",
            family="peripheral",
            capabilities=("platform_adapter",),
        )
    )
    _gate_state: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _mute_state: dict[str, bool] = field(default_factory=dict, init=False, repr=False)

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def publish_platform_connected(self, *, platform: str, detail: str | None = None) -> None:
        self._publish_event(
            event_type="platform_connected",
            target_agent="cortex_agent",
            payload={
                "platform": str(platform or ""),
                "detail": detail,
            },
        )

    def publish_platform_disconnected(self, *, platform: str, detail: str | None = None) -> None:
        self._publish_event(
            event_type="platform_disconnected",
            target_agent="cortex_agent",
            payload={
                "platform": str(platform or ""),
                "detail": detail,
            },
        )

    # ── T-166：下行指令统一入口 ───────────────────────────────────────────────

    def handle_tribe_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """T-166：处理来自 host_brain 的下行 Tribe 门控指令，统一分发到对应处理方法。

        由 EdgeTribeBridge.receive_command() 调用，根据 command_type 路由到：
        - "gate_open"    → on_gate_open_command()（开启平台输出门控）
        - "gate_close"   → on_gate_close_command()（关闭平台输出门控）
        - "platform_mute"→ on_platform_mute_command()（静音指定平台）

        Args:
            command: {
                "command_type": str,   # 指令类型
                "payload": dict,       # 指令载荷
                "command_id": str,     # 指令 ID（调试用）
                "source_agent": str,   # 来源 Agent
            }

        Returns:
            {"ok": bool, "command_type": str, "command_id": str, "reason": str|None}
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")

        command_type = str(command.get("command_type") or "").strip().lower()
        payload = dict(command.get("payload") or {})
        command_id = str(command.get("command_id") or "").strip()
        source_agent = str(command.get("source_agent") or "host_brain").strip()

        _log.debug(
            "PeripheralAgent.handle_tribe_command: type=%s id=%s source=%s",
            command_type, command_id, source_agent,
        )

        try:
            if command_type == "gate_open":
                result = self.on_gate_open_command(payload)
            elif command_type == "gate_close":
                result = self.on_gate_close_command(payload)
            elif command_type == "platform_mute":
                result = self.on_platform_mute_command(payload)
            else:
                _log.warning(
                    "PeripheralAgent.handle_tribe_command: 未知指令类型 command_type=%s", command_type
                )
                return {
                    "ok": False,
                    "command_type": command_type,
                    "command_id": command_id,
                    "reason": f"UNKNOWN_COMMAND_TYPE:{command_type}",
                }
            return {
                "ok": bool(result.get("ok", True)),
                "command_type": command_type,
                "command_id": command_id,
                "reason": result.get("reason"),
            }
        except Exception as exc:
            _log.warning("PeripheralAgent.handle_tribe_command 异常: %s", exc)
            return {
                "ok": False,
                "command_type": command_type,
                "command_id": command_id,
                "reason": str(exc),
            }

    def on_gate_open_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """T-166：开启平台输出门控（允许向指定平台发送消息）。

        Args:
            payload: {
                "platform": str,       # 平台名称，如 "bilibili"/"vts"/"all"
                "reason": str|None,    # 开启原因（调试用）
            }

        Returns:
            {"ok": bool, "reason": str|None, "platform": str}
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")
        platform = str(payload.get("platform") or "all").strip().lower() or "all"
        reason = str(payload.get("reason") or "").strip() or None
        self._gate_state[platform] = "open"
        _log.info("PeripheralAgent.on_gate_open_command: platform=%s reason=%s", platform, reason)
        # 上报门控状态变更
        self._publish_event(
            event_type="gate_state_changed",
            target_agent="cortex_agent",
            payload={"platform": platform, "gate": "open", "reason": reason},
        )
        return {"ok": True, "reason": None, "platform": platform}

    def on_gate_close_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """T-166：关闭平台输出门控（阻止向指定平台发送消息）。

        Args:
            payload: {
                "platform": str,       # 平台名称，如 "bilibili"/"vts"/"all"
                "reason": str|None,    # 关闭原因（调试用）
            }

        Returns:
            {"ok": bool, "reason": str|None, "platform": str}
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")
        platform = str(payload.get("platform") or "all").strip().lower() or "all"
        reason = str(payload.get("reason") or "").strip() or None
        self._gate_state[platform] = "closed"
        _log.info("PeripheralAgent.on_gate_close_command: platform=%s reason=%s", platform, reason)
        self._publish_event(
            event_type="gate_state_changed",
            target_agent="cortex_agent",
            payload={"platform": platform, "gate": "closed", "reason": reason},
        )
        return {"ok": True, "reason": None, "platform": platform}

    def on_platform_mute_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """T-166：静音指定平台（临时禁止语音/文字输出）。

        Args:
            payload: {
                "platform": str,       # 平台名称
                "mute": bool,          # True=静音，False=取消静音
                "duration_s": int|None # 静音时长（秒），None=永久
            }

        Returns:
            {"ok": bool, "reason": str|None, "platform": str, "mute": bool}
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")
        platform = str(payload.get("platform") or "all").strip().lower() or "all"
        mute = bool(payload.get("mute", True))
        duration_s = payload.get("duration_s")
        self._mute_state[platform] = mute
        _log.info(
            "PeripheralAgent.on_platform_mute_command: platform=%s mute=%s duration_s=%s",
            platform, mute, duration_s,
        )
        self._publish_event(
            event_type="platform_mute_changed",
            target_agent="cortex_agent",
            payload={"platform": platform, "mute": mute, "duration_s": duration_s},
        )
        return {"ok": True, "reason": None, "platform": platform, "mute": mute}

    # ── T-167：主动上报外设状态快照 ───────────────────────────────────────────

    def publish_peripheral_state(
        self,
        *,
        connected_platforms: list[str] | None = None,
        gate_state: dict[str, str] | None = None,
        mute_state: dict[str, bool] | None = None,
        error: str | None = None,
    ) -> None:
        """T-167：主动上报外设状态快照到 host_brain PeripheralAgent。

        由 bootstrap 定时调用或在平台连接/断线时触发，将 edge_client 侧的
        平台连接状态、门控状态等信息上报到 host_brain，供 CortexAgent
        的 collect_tribe_context() 读取。

        Args:
            connected_platforms: 当前已连接的平台列表（可选）。
            gate_state: 各平台门控状态字典（可选），如 {"bilibili": "open"}。
            mute_state: 各平台静音状态字典（可选），如 {"vts": False}。
            error: 错误信息（可选，用于上报异常状态）。
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")

        payload: dict[str, Any] = {
            "connected_platforms": list(connected_platforms or []),
            "gate_state": dict(gate_state or self._gate_state),
            "mute_state": dict(mute_state or self._mute_state),
            "error": str(error or "").strip() or None,
            "source": "edge.peripheral",
        }
        self._publish_event(
            event_type="peripheral_state_updated",
            target_agent="peripheral_agent",
            payload=payload,
        )
        _log.debug(
            "PeripheralAgent.publish_peripheral_state: platforms=%s gate=%s",
            connected_platforms, gate_state,
        )

    def _publish_event(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        target_agent: str | None = None,
    ) -> None:
        bridge = self.bridge
        if bridge is None or not hasattr(bridge, "publish_event"):
            return
        bridge.publish_event(
            event_type=event_type,
            source_agent=self.descriptor.agent_id,
            target_agent=target_agent,
            payload=dict(payload),
        )


__all__ = ["PeripheralAgent"]
