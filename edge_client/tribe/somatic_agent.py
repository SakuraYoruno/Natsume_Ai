from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from edge_client.tribe.base_agent import EdgeTribeAgentDescriptor

_log = logging.getLogger("Natsume.edge.tribe.somatic_agent")


@dataclass(slots=True)
class SomaticAgent:
    bridge: Any | None = None
    vts_driver: Any | None = None  # T-126/T-127：VTubeStudioDriver 实例，由 bootstrap 注入
    descriptor: EdgeTribeAgentDescriptor = field(
        default_factory=lambda: EdgeTribeAgentDescriptor(
            agent_id="edge.somatic",
            family="somatic",
            capabilities=("body_sense", "emotion_inference"),
        )
    )

    def set_bridge(self, bridge: Any | None) -> None:
        self.bridge = bridge

    def set_vts_driver(self, vts_driver: Any | None) -> None:
        """注入 VTubeStudioDriver，用于接收下行情绪指令后驱动表情。"""
        self.vts_driver = vts_driver

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def on_emotion_state_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """处理来自 LimbicAgent 的下行情绪状态指令，驱动 vts_driver 表情。

        T-126：Phase 6 下行链路核心方法。
        host_brain LimbicAgent 处理完 EMOTION_INFERENCE_UPDATED 后，
        通过 EdgeCommandPusher → /internal/tribe_command → 此方法，
        最终调用 vts_driver.set_expression() 驱动 VTubeStudio 表情。

        Args:
            payload: {
                "emotion_tag": str,       # 情绪标签，如 "JOY"/"SAD"/"NEUTRAL"
                "intensity": float|None,  # 情绪强度 0.0~1.0
                "mood_state": str|None,   # 心情状态，如 "upbeat"/"steady"
                "session_id": str|None,   # 会话 ID（调试用）
            }

        Returns:
            {"ok": bool, "reason": str|None, "emotion_tag": str}
        """
        try:
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            intensity = payload.get("intensity")
            intensity_f: float | None = (
                max(0.0, min(1.0, float(intensity)))
                if isinstance(intensity, (int, float))
                else None
            )
            session_id = str(payload.get("session_id") or "").strip() or None

            driver = self.vts_driver
            if driver is None:
                _log.debug(
                    "SomaticAgent.on_emotion_state_command: vts_driver 未注入，跳过表情驱动"
                    " emotion_tag=%s session=%s", emotion_tag, session_id,
                )
                return {"ok": False, "reason": "VTS_DRIVER_UNAVAILABLE", "emotion_tag": emotion_tag}

            # 调用 vts_driver.set_expression()，intensity 可选
            if intensity_f is not None:
                result = driver.set_expression(emotion_tag, intensity=intensity_f)
            else:
                result = driver.set_expression(emotion_tag)

            ok = bool(getattr(result, "success", True) if hasattr(result, "success") else True)
            _log.info(
                "SomaticAgent 下行情绪指令已执行: emotion_tag=%s intensity=%s ok=%s session=%s",
                emotion_tag, intensity_f, ok, session_id,
            )
            return {"ok": ok, "reason": None, "emotion_tag": emotion_tag}
        except Exception as exc:
            _log.warning("SomaticAgent.on_emotion_state_command 异常: %s", exc)
            return {"ok": False, "reason": str(exc), "emotion_tag": str(payload.get("emotion_tag") or "NEUTRAL")}

    def publish_body_sense_updated(
        self,
        *,
        request_id: str,
        session_id: str,
        seq: int | None,
        ts_ms: int,
        resource_metrics: dict[str, Any],
        upstream_status: str,
        upstream_reason_code: str | None,
    ) -> None:
        self._publish_event(
            event_type="body_sense_updated",
            target_agent="limbic_agent",
            payload={
                "request_id": str(request_id or ""),
                "session_id": str(session_id or ""),
                "seq": seq,
                "ts_ms": int(ts_ms),
                "resource_metrics": dict(resource_metrics or {}),
                "upstream_status": str(upstream_status or ""),
                "upstream_reason_code": upstream_reason_code,
            },
        )

    def publish_emotion_inference_updated(
        self,
        *,
        request_id: str,
        session_id: str,
        seq: int | None,
        emotion_tag: str,
        emotion_intensity: float | None,
        mood_state: str | None,
        emotion_source: str | None,
        emotion_terms: list[str],
        emotion_modality: str | None = None,
        emotion_confidence: float | None = None,
        emotion_inference_degraded: bool | None = None,
        acoustic_emotion_available: bool | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "request_id": str(request_id or ""),
            "session_id": str(session_id or ""),
            "seq": seq,
            "emotion_tag": str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
            "emotion_intensity": (
                max(0.0, min(1.0, float(emotion_intensity)))
                if isinstance(emotion_intensity, (int, float))
                else None
            ),
            "mood_state": str(mood_state).strip().lower() if mood_state else None,
            "emotion_source": str(emotion_source).strip() if emotion_source else None,
            "emotion_terms": list(emotion_terms or []),
            "emotion_modality": str(emotion_modality or "").strip() or None,
            "emotion_confidence": (
                max(0.0, min(1.0, float(emotion_confidence)))
                if isinstance(emotion_confidence, (int, float))
                else None
            ),
            "emotion_inference_degraded": bool(emotion_inference_degraded)
            if emotion_inference_degraded is not None
            else None,
            "acoustic_emotion_available": bool(acoustic_emotion_available)
            if acoustic_emotion_available is not None
            else None,
        }
        self._publish_event(
            event_type="emotion_inference_updated",
            target_agent="limbic_agent",
            payload=payload,
        )

    # ── T-164：下行指令统一入口 ───────────────────────────────────────────────

    def handle_tribe_command(self, command: dict[str, Any]) -> dict[str, Any]:
        """T-164：处理来自 host_brain 的下行 Tribe 指令，统一分发到对应处理方法。

        由 EdgeTribeBridge.receive_command() 调用，根据 command_type 路由到：
        - "emotion_state"  → on_emotion_state_command()（情绪表情驱动）
        - "expression_set" → on_expression_set_command()（直接设置表情参数）
        - "vts_hotkey"     → on_vts_hotkey_command()（触发 VTS 热键）

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
        command_type = str(command.get("command_type") or "").strip().lower()
        payload = dict(command.get("payload") or {})
        command_id = str(command.get("command_id") or "").strip()
        source_agent = str(command.get("source_agent") or "host_brain").strip()

        _log.debug(
            "SomaticAgent.handle_tribe_command: type=%s id=%s source=%s",
            command_type, command_id, source_agent,
        )

        try:
            if command_type == "emotion_state":
                result = self.on_emotion_state_command(payload)
            elif command_type == "expression_set":
                result = self.on_expression_set_command(payload)
            elif command_type == "vts_hotkey":
                result = self.on_vts_hotkey_command(payload)
            else:
                _log.warning(
                    "SomaticAgent.handle_tribe_command: 未知指令类型 command_type=%s", command_type
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
            _log.warning("SomaticAgent.handle_tribe_command 异常: %s", exc)
            return {
                "ok": False,
                "command_type": command_type,
                "command_id": command_id,
                "reason": str(exc),
            }

    def on_expression_set_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """T-164：直接设置 VTS 表情参数（精细控制）。

        Args:
            payload: {
                "parameter_id": str,   # VTS 参数 ID
                "value": float,        # 参数值
                "weight": float|None,  # 混合权重（可选）
            }

        Returns:
            {"ok": bool, "reason": str|None}
        """
        try:
            driver = self.vts_driver
            if driver is None:
                return {"ok": False, "reason": "VTS_DRIVER_UNAVAILABLE"}
            parameter_id = str(payload.get("parameter_id") or "").strip()
            value = float(payload.get("value") or 0.0)
            weight = payload.get("weight")
            if not parameter_id:
                return {"ok": False, "reason": "MISSING_PARAMETER_ID"}
            if weight is not None and hasattr(driver, "set_parameter"):
                driver.set_parameter(parameter_id, value=value, weight=float(weight))
            elif hasattr(driver, "set_parameter"):
                driver.set_parameter(parameter_id, value=value)
            _log.debug(
                "SomaticAgent.on_expression_set_command: param=%s value=%.3f", parameter_id, value
            )
            return {"ok": True, "reason": None}
        except Exception as exc:
            _log.warning("SomaticAgent.on_expression_set_command 异常: %s", exc)
            return {"ok": False, "reason": str(exc)}

    def on_vts_hotkey_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """T-164：触发 VTubeStudio 热键。

        Args:
            payload: {
                "hotkey_id": str,   # 热键 ID
            }

        Returns:
            {"ok": bool, "reason": str|None}
        """
        try:
            driver = self.vts_driver
            if driver is None:
                return {"ok": False, "reason": "VTS_DRIVER_UNAVAILABLE"}
            hotkey_id = str(payload.get("hotkey_id") or "").strip()
            if not hotkey_id:
                return {"ok": False, "reason": "MISSING_HOTKEY_ID"}
            if hasattr(driver, "trigger_hotkey"):
                driver.trigger_hotkey(hotkey_id)
            _log.debug("SomaticAgent.on_vts_hotkey_command: hotkey_id=%s", hotkey_id)
            return {"ok": True, "reason": None}
        except Exception as exc:
            _log.warning("SomaticAgent.on_vts_hotkey_command 异常: %s", exc)
            return {"ok": False, "reason": str(exc)}

    # ── T-165：主动上报躯体状态快照 ───────────────────────────────────────────

    def publish_somatic_state(
        self,
        *,
        vts_connected: bool = False,
        current_expression: str | None = None,
        last_emotion_tag: str | None = None,
        last_intensity: float | None = None,
        error: str | None = None,
    ) -> None:
        """T-165：主动上报躯体状态快照到 host_brain SomaticAgent。

        由 bootstrap 定时调用或在状态变更时触发，将 edge_client 侧的
        VTS 连接状态、当前表情等信息上报到 host_brain，供 CortexAgent
        的 collect_tribe_context() 读取。

        Args:
            vts_connected: VTubeStudio 是否已连接。
            current_expression: 当前激活的表情名称（可选）。
            last_emotion_tag: 最近一次情绪标签（可选）。
            last_intensity: 最近一次情绪强度（可选）。
            error: 错误信息（可选，用于上报异常状态）。
        """
        payload: dict[str, Any] = {
            "vts_connected": bool(vts_connected),
            "current_expression": str(current_expression or "").strip() or None,
            "last_emotion_tag": str(last_emotion_tag or "").strip().upper() or None,
            "last_intensity": (
                round(max(0.0, min(1.0, float(last_intensity))), 4)
                if isinstance(last_intensity, (int, float))
                else None
            ),
            "error": str(error or "").strip() or None,
            "source": "edge.somatic",
        }
        self._publish_event(
            event_type="somatic_state_updated",
            target_agent="somatic_agent",
            payload=payload,
        )
        _log.debug(
            "SomaticAgent.publish_somatic_state: vts_connected=%s expression=%s tag=%s",
            vts_connected, current_expression, last_emotion_tag,
        )

    # ── 面部捕捉感知：读取用户表情并上报 ────────────────────────────────────

    def poll_user_face_capture(self) -> dict[str, Any]:
        """读取 VTS 面部捕捉参数，推断用户当前表情，并通过 TribeEventBus 上报。

        由 bootstrap 定时调用（建议 5s interval），将用户表情感知结果
        发布为 emotion_inference_updated 事件，供 host_brain LimbicAgent
        和 MirrorNeuronAgent 感知用户情绪状态。

        Returns:
            {
                "ok": bool,
                "expression_tag": str,   # 推断的用户表情标签
                "confidence": float,
                "skipped": bool,         # True 表示 VTS 未连接，跳过
            }
        """
        driver = self.vts_driver
        if driver is None:
            return {"ok": False, "expression_tag": "NEUTRAL", "confidence": 0.0, "skipped": True,
                    "reason": "VTS_DRIVER_UNAVAILABLE"}

        try:
            # 调用 vts_driver 推断用户表情
            infer_result = driver.infer_user_expression()
            expression_tag = str(infer_result.get("expression_tag") or "NEUTRAL").strip().upper()
            confidence = float(infer_result.get("confidence") or 0.0)

            # 置信度过低时不上报（避免噪声）
            if confidence < 0.35:
                _log.debug(
                    "SomaticAgent.poll_user_face_capture: 置信度过低，跳过上报 "
                    "tag=%s confidence=%.3f", expression_tag, confidence,
                )
                return {
                    "ok": True,
                    "expression_tag": expression_tag,
                    "confidence": confidence,
                    "skipped": True,
                    "reason": "LOW_CONFIDENCE",
                }

            # 将用户表情标签映射为情绪维度（供 MirrorNeuronAgent 使用）
            _tag_to_emotion: dict[str, tuple[str, float]] = {
                "SMILE":     ("JOY",      0.6),
                "FROWN":     ("SAD",      0.5),
                "SURPRISED": ("SURPRISE", 0.7),
                "BLINK":     ("NEUTRAL",  0.3),
                "NEUTRAL":   ("NEUTRAL",  0.4),
            }
            emotion_tag, emotion_intensity = _tag_to_emotion.get(
                expression_tag, ("NEUTRAL", 0.4)
            )

            # 上报为 emotion_inference_updated 事件（source 标记为 face_capture）
            from host_brain.core.ids import now_ms  # noqa: PLC0415
            self.publish_emotion_inference_updated(
                request_id=f"face_capture_{now_ms()}",
                session_id="face_capture",
                seq=None,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
                mood_state=None,
                emotion_source="face_capture",
                emotion_terms=[expression_tag.lower()],
                emotion_modality="face_capture",
                emotion_confidence=confidence,
                emotion_inference_degraded=False,
                acoustic_emotion_available=False,
            )

            _log.debug(
                "SomaticAgent.poll_user_face_capture: 用户表情已上报 "
                "expression=%s emotion=%s confidence=%.3f",
                expression_tag, emotion_tag, confidence,
            )
            return {
                "ok": True,
                "expression_tag": expression_tag,
                "emotion_tag": emotion_tag,
                "confidence": confidence,
                "skipped": False,
            }

        except Exception as exc:
            _log.debug("SomaticAgent.poll_user_face_capture 异常: %s", exc)
            return {"ok": False, "expression_tag": "NEUTRAL", "confidence": 0.0,
                    "skipped": True, "reason": str(exc)}

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


__all__ = ["SomaticAgent"]
