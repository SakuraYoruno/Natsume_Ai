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

    def publish_message_received(
        self,
        *,
        platform: str,
        msg_type: str,
        sender: str = "",
    ) -> None:
        """Phase XV：收到社交消息时发布感知事件，触发 VTS「看到消息」动作。

        Args:
            platform: 来源平台（如 "wechat"/"qq"）。
            msg_type: 消息类型（如 "text"/"image"/"file"）。
            sender: 发送者昵称或 ID（显示用）。
        """
        self._publish_event(
            event_type="social_message_received",
            target_agent="peripheral_agent",
            payload={
                "platform": str(platform or ""),
                "msg_type": str(msg_type or "text"),
                "sender": str(sender or ""),
                "vts_hint": "look_at_screen",  # VTS 动作提示：眼神移向屏幕方向
                "source": "edge.peripheral",
            },
        )

    def publish_voice_received(
        self,
        *,
        platform: str,
        transcript: str,
        sender: str = "",
    ) -> None:
        """Phase XV：收到语音消息（ASR 识别后）时发布感知事件，触发 VTS「听到声音」动作。

        Args:
            platform: 来源平台（如 "wechat"）。
            transcript: ASR 识别出的文字内容。
            sender: 发送者昵称或 ID（显示用）。
        """
        self._publish_event(
            event_type="social_voice_received",
            target_agent="peripheral_agent",
            payload={
                "platform": str(platform or ""),
                "transcript": str(transcript or ""),
                "sender": str(sender or ""),
                "vts_hint": "listen_attentively",  # VTS 动作提示：侧耳倾听
                "source": "edge.peripheral",
            },
        )

    # ── T-166：下行指令统一入口 ───────────────────────────────────────────────

    # ── T-198：Motor 执行结果回流 ─────────────────────────────────────────────

    def report_motor_execution_result(
        self,
        *,
        plan_id: str,
        action: str,
        ok: bool,
        latency_ms: int,
        reason: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        """T-198：将 Edge 侧 motor_action_command 执行结果回传 Host。

        Edge 侧执行完 motor_action_command 后调用此方法，通过 EdgeTribeBridge
        向 Host 的 MotorCortexAgent 发布 MOTOR_EXECUTION_FEEDBACK 事件，
        打通 MotorCortex → Edge → 反馈回流 的完整执行闭环。

        Args:
            plan_id: Host 侧生成的计划 ID（来自 motor_action_command.plan_id）。
            action: 执行的动作名称。
            ok: Edge 侧实际执行是否成功。
            latency_ms: Edge 侧执行耗时（毫秒）。
            reason: 失败原因（可选）。
            detail: 详细信息（可选）。

        Returns:
            {"ok": bool, "plan_id": str, "published": bool}
        """
        import logging  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")

        resolved_plan_id = str(plan_id or "").strip()
        resolved_action = str(action or "").strip()
        resolved_latency = max(0, int(latency_ms or 0))

        # 回流记录实际 Edge 尝试的结果，而非 Host 计划的预期；plan_id 将两者关联，
        # 便于 Host 对失败、超时或重复命令做对账，不能凭计划存在推断已执行。
        payload: dict[str, Any] = {
            "plan_id": resolved_plan_id,
            "action": resolved_action,
            "ok": bool(ok),
            "latency_ms": resolved_latency,
            "reason": str(reason or "").strip() or None,
            "detail": str(detail or "").strip() or None,
            "source": "edge.peripheral",
        }
        self._publish_event(
            event_type="motor_execution_feedback",
            target_agent="motor_cortex_agent",
            payload=payload,
        )
        _log.info(
            "PeripheralAgent.report_motor_execution_result: plan_id=%s ok=%s latency_ms=%d",
            resolved_plan_id, ok, resolved_latency,
        )
        return {"ok": bool(ok), "plan_id": resolved_plan_id, "published": True}

    def on_motor_action_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """T-198：执行来自 MotorCortexAgent 的动作指令，并自动回传执行结果。

        Args:
            payload: {
                "plan_id": str,    # Host 侧计划 ID
                "action": str,     # 动作名称
                "args": dict,      # 动作参数
                "urgency": float,  # 紧迫度（0~1）
            }

        Returns:
            {"ok": bool, "plan_id": str, "action": str, "latency_ms": int}
        """
        import logging  # noqa: PLC0415
        import time  # noqa: PLC0415
        _log = logging.getLogger("Natsume.edge.tribe.peripheral_agent")

        plan_id = str(payload.get("plan_id") or "").strip()
        action = str(payload.get("action") or "").strip()
        args = dict(payload.get("args") or {})
        urgency = float(payload.get("urgency") or 0.5)

        started_ms = int(time.time() * 1000)
        ok = False
        reason: str | None = None
        try:
            _log.info(
                "PeripheralAgent.on_motor_action_command: plan_id=%s action=%s urgency=%.2f",
                plan_id, action, urgency,
            )
            # 实际执行逻辑：通过 bridge 向平台发送动作指令
            # Agent 仅委托已注入的本地执行桥，不自行选择新设备或补发命令；桥接抛错
            # 后仍须在 finally 回流失败收据，避免 Host 永远等待无终态的计划。
            bridge = self.bridge
            if bridge is not None and hasattr(bridge, "execute_motor_action"):
                bridge.execute_motor_action(action=action, args=args, urgency=urgency)
            ok = True
        except Exception as exc:
            reason = str(exc)[:200]
            _log.warning(
                "PeripheralAgent.on_motor_action_command 执行失败: plan_id=%s action=%s error=%s",
                plan_id, action, exc,
            )
        finally:
            latency_ms = int(time.time() * 1000) - started_ms
            # T-198：自动回传执行结果
            self.report_motor_execution_result(
                plan_id=plan_id,
                action=action,
                ok=ok,
                latency_ms=latency_ms,
                reason=reason,
            )
        return {"ok": ok, "plan_id": plan_id, "action": action, "latency_ms": latency_ms}

    def handle_tribe_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """T-166：处理来自 host_brain 的下行 Tribe 门控指令，统一分发到对应处理方法。

        由 EdgeTribeBridge.receive_command() 调用，根据 command_type 路由到：
        - "gate_open"         → on_gate_open_command()（开启平台输出门控）
        - "gate_close"        → on_gate_close_command()（关闭平台输出门控）
        - "platform_mute"     → on_platform_mute_command()（静音指定平台）
        - "motor_action_command" → on_motor_action_command()（T-198：执行 Motor 动作指令）

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
            # 未知命令显式拒绝而非猜测默认动作；下行命令是权限边界，宽松路由会把
            # 新字段或拼写错误意外扩展为可执行的外围控制。
            if command_type == "gate_open":
                result = self.on_gate_open_command(payload)
            elif command_type == "gate_close":
                result = self.on_gate_close_command(payload)
            elif command_type == "platform_mute":
                result = self.on_platform_mute_command(payload)
            elif command_type == "motor_action_command":
                result = self.on_motor_action_command(payload)
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
        # 上报是观察通道而非本地命令成功条件。桥接不可用时保留本地状态，不能将
        # “没有发送反馈”表示成“平台/设备已恢复”。
        if bridge is None or not hasattr(bridge, "publish_event"):
            return
        bridge.publish_event(
            event_type=event_type,
            source_agent=self.descriptor.agent_id,
            target_agent=target_agent,
            payload=dict(payload),
        )


__all__ = ["PeripheralAgent"]
