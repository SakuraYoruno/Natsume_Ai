from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class PeripheralAgent:
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
    """T-110：外周神经系统 Agent 骨架。

    负责接收情绪状态变化（EMOTION_STATE_CHANGED）和免疫拦截（IMMUNE_BLOCK）信号，
    将外周神经系统状态写入快照，供 edge_client 侧的输出通道（语音/表情/动作）参考。

    Phase 6（edge_client 接入）时将扩展为完整实现，驱动实际的外设输出。
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="peripheral_agent",
            family="peripheral",
            execution_side="host",
            owner_domain="brain",
            capabilities=("peripheral_output", "emotion_expression", "motor_signal"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def _legacy_expression_authority_retired(self) -> bool:
        """Whether an accepted ExpressionPlan owns emotional embodiment.

        Platform health and security suppression remain valid peripheral facts.
        What is retired is the old event-to-expression mapper, which could
        turn an unbound emotion event into voice/avatar state outside the
        canonical affect revision and delivery receipt.
        """

        ports = getattr(self, "application_ports", None)
        return bool(
            isinstance(ports, TribeApplicationPorts)
            and ports.uses_canonical_affective_life()
        )

    def get_peripheral_state(self) -> dict[str, Any]:
        """返回当前外周神经系统状态快照。"""
        try:
            snap = self.state_registry.get_state("peripheral_agent") or {}
            return dict(snap.get("state") or {})
        except Exception:
            return {}

    # ── T-110：TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """T-110：向 TribeEventBus 注册事件订阅。

        订阅：
        - EMOTION_STATE_CHANGED：情绪变化时更新外周表达状态（表情/语调建议）
        - IMMUNE_BLOCK：免疫拦截时触发外周抑制信号（降低输出活跃度）
        - PLATFORM_CONNECTED / PLATFORM_DISCONNECTED：平台连通性变更
        - PERIPHERAL_STATE_UPDATED：接收 edge_client 外设状态快照
        - GATE_STATE_CHANGED / PLATFORM_MUTE_CHANGED：接收 edge 门控与静音状态变更
        """
        try:
            from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            if not self._legacy_expression_authority_retired():
                bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block)
            bus.subscribe("PLATFORM_CONNECTED", self._on_platform_connected)
            bus.subscribe("PLATFORM_DISCONNECTED", self._on_platform_disconnected)
            bus.subscribe("PERIPHERAL_STATE_UPDATED", self._on_peripheral_state_updated)
            bus.subscribe("GATE_STATE_CHANGED", self._on_gate_state_changed)
            bus.subscribe("PLATFORM_MUTE_CHANGED", self._on_platform_mute_changed)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.peripheral_agent").warning(
                "PeripheralAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_emotion_state_changed(self, event: Any) -> None:
        """处理 EMOTION_STATE_CHANGED 事件：更新外周表达状态。

        T-110：情绪变化时，根据 emotion_tag 生成外周表达建议
        （表情强度、语调建议、动作幅度），供 edge_client 驱动外设输出。
        """
        if self._legacy_expression_authority_retired():
            return
        try:
            payload = dict(getattr(event, "payload", None) or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.5)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 情绪 → 外周表达映射（Phase 6 扩展为完整驱动指令）
            _expression_map: dict[str, dict[str, Any]] = {
                "JOY":     {"facial_expr": "smile",   "voice_tone": "bright",  "motion_scale": 0.8},
                "HAPPY":   {"facial_expr": "smile",   "voice_tone": "warm",    "motion_scale": 0.6},
                "EXCITED": {"facial_expr": "bright",  "voice_tone": "energetic","motion_scale": 1.0},
                "SAD":     {"facial_expr": "soft",    "voice_tone": "gentle",  "motion_scale": 0.2},
                "LONELY":  {"facial_expr": "soft",    "voice_tone": "caring",  "motion_scale": 0.3},
                "ANXIOUS": {"facial_expr": "neutral", "voice_tone": "calm",    "motion_scale": 0.4},
                "ANGRY":   {"facial_expr": "tense",   "voice_tone": "measured","motion_scale": 0.5},
                "NEUTRAL": {"facial_expr": "neutral", "voice_tone": "balanced","motion_scale": 0.5},
            }
            expr = _expression_map.get(emotion_tag, _expression_map["NEUTRAL"])

            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "current_emotion_tag": emotion_tag,
                "current_intensity": round(intensity, 4),
                "facial_expr": expr["facial_expr"],
                "voice_tone": expr["voice_tone"],
                "motion_scale": round(float(expr["motion_scale"]) * intensity, 4),
                "last_emotion_ts_ms": ts_ms,
                # 免疫抑制标记（IMMUNE_BLOCK 时置 True）
                "suppressed": prev_state.get("suppressed", False),
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
            # T-388：高强度正向情绪（JOY/HAPPY/EXCITED，intensity>=0.65）时联动 LimbicAgent
            # 外周接收到高强度正向情绪信号，应反馈到情绪代谢系统形成正向回路
            _pos_emotion_tags = {"JOY", "HAPPY", "EXCITED", "CONTENT", "GRATEFUL"}
            if emotion_tag in _pos_emotion_tags and intensity >= 0.65:
                try:
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _periph_valence = round(min(0.15, (intensity - 0.65) * 0.43), 4)
                        _limbic.drive_emotion_metabolism(
                            valence=_periph_valence,
                            arousal=round(_periph_valence * 0.50, 4),
                            dominance=0.03,
                            blend_weight=0.08,
                            event_kind=f"peripheral_positive_emotion:{emotion_tag.lower()}",
                            source="peripheral_agent",
                        )
                except Exception:
                    pass
        except Exception:
            pass

    async def _on_immune_block(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：触发外周抑制信号。

        T-110：免疫拦截时，将外周输出置为抑制状态，
        降低表情/动作活跃度，避免在有害内容被拦截时仍表现出积极外周反应。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "low").strip().lower()
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 仅中等及以上风险触发外周抑制
            if risk_level not in ("medium", "high", "critical"):
                return

            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "suppressed": True,
                "suppressed_risk_level": risk_level,
                "suppressed_ts_ms": ts_ms,
                "facial_expr": "neutral",
                "voice_tone": "measured",
                "motion_scale": 0.1,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
            # T-387：免疫拦截（medium/high/critical）时联动 InteroceptionAgent 注入压力
            # 被迫抑制外周输出是一种行动受阻的内在压力（想表达却被拦截）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "inject_external_stress"):
                    _immune_stress = {"medium": 0.12, "high": 0.22, "critical": 0.32}.get(risk_level, 0.12)
                    _intero.inject_external_stress(
                        source=f"peripheral_immune_block:{risk_level}",
                        intensity=_immune_stress,
                        ts_ms=ts_ms,
                    )
            except Exception:
                pass
        except Exception:
            pass

    async def _on_platform_connected(self, event: Any) -> None:
        """处理 PLATFORM_CONNECTED 事件：记录平台连通状态。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "").strip().lower()
            detail = str(payload.get("detail") or "").strip() or None
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            if not platform:
                return
            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            connected = list(prev_state.get("connected_platforms") or [])
            if platform not in connected:
                connected.append(platform)
            prev_state.update({
                "connected_platforms": sorted({item for item in connected if isinstance(item, str)}),
                "last_platform_event": "connected",
                "last_connected_platform": platform,
                "last_connected_detail": detail,
                "last_connected_ts_ms": ts_ms,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")

            # T-294/T-297：外设连接成功时联动 InteroceptionAgent 降低 recovery_urgency（连接恢复减轻压力）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "reduce_recovery_urgency"):
                    _intero.reduce_recovery_urgency(
                        source=f"peripheral_connected:{platform}",
                        relief=0.30,
                        ts_ms=ts_ms,
                    )
            except Exception:
                pass

            # T-389：平台连接恢复时联动 PainRewardAgent 记录通道恢复奖励
            # 外设重新连通意味着表达/行动通道恢复，是轻度正向奖励
            try:
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_reward"):
                    _pr.record_reward(
                        source=f"peripheral_connected:{platform}",
                        intensity=0.12,
                        metadata={
                            "platform": platform,
                            "detail": detail,
                            "ts_ms": ts_ms,
                        },
                    )
            except Exception:
                pass
        except Exception:
            pass

    async def _on_platform_disconnected(self, event: Any) -> None:
        """处理 PLATFORM_DISCONNECTED 事件：记录平台断开状态。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "").strip().lower()
            detail = str(payload.get("detail") or "").strip() or None
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            if not platform:
                return
            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            connected = [item for item in list(prev_state.get("connected_platforms") or []) if item != platform]
            prev_state.update({
                "connected_platforms": sorted({item for item in connected if isinstance(item, str)}),
                "last_platform_event": "disconnected",
                "last_disconnected_platform": platform,
                "last_disconnected_detail": detail,
                "last_disconnected_ts_ms": ts_ms,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
            # T-386：平台断开时联动 InteroceptionAgent 注入压力（失去连接=行动通道受阻）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "inject_external_stress"):
                    _intero.inject_external_stress(
                        source=f"peripheral_disconnected:{platform}",
                        intensity=0.20,
                        ts_ms=ts_ms,
                    )
            except Exception:
                pass
        except Exception:
            pass

    async def _on_peripheral_state_updated(self, event: Any) -> None:
        """处理 PERIPHERAL_STATE_UPDATED：接收 edge_client 外设状态快照。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            connected_platforms = payload.get("connected_platforms")
            if isinstance(connected_platforms, list):
                prev_state["connected_platforms"] = [
                    str(item).strip().lower()
                    for item in connected_platforms
                    if str(item).strip()
                ]
            gate_state = payload.get("gate_state")
            if isinstance(gate_state, dict):
                prev_state["gate_state"] = {
                    str(key).strip().lower(): str(value).strip().lower()
                    for key, value in gate_state.items()
                    if str(key).strip()
                }
            mute_state = payload.get("mute_state")
            if isinstance(mute_state, dict):
                prev_state["mute_state"] = {
                    str(key).strip().lower(): bool(value)
                    for key, value in mute_state.items()
                    if str(key).strip()
                }
            prev_state.update({
                "edge_peripheral_error": str(payload.get("error") or "").strip() or None,
                "last_edge_peripheral_report_ts_ms": ts_ms,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")

            # T-269：外设报告错误时联动 InteroceptionAgent 提高 recovery_urgency
            _error = str(payload.get("error") or "").strip()
            _latency_ms = int(payload.get("latency_ms") or 0)
            if _error or _latency_ms >= 1500:
                try:
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "inject_external_stress"):
                        _stress = 0.25 if _error else min(0.20, (_latency_ms - 1500) / 5000.0)
                        _intero.inject_external_stress(
                            source=f"peripheral_error:{_error[:32] if _error else f'latency_{_latency_ms}ms'}",
                            intensity=round(_stress, 4),
                        )
                except Exception:
                    pass
                # T-355：外设高延迟（>=2000ms）或错误时联动 PainRewardAgent 记录行动受阻痛苦
                # 外周高延迟/错误是行动受阻的直接体验（想说话却说不出来）
                if _error or _latency_ms >= 2000:
                    try:
                        _pr = get_pain_reward_agent()
                        if hasattr(_pr, "record_pain"):
                            _pain = 0.20 if _error else round(min(0.25, (_latency_ms - 2000) / 8000.0), 4)
                            _pr.record_pain(
                                source=f"peripheral_blocked:{_error[:24] if _error else f'latency_{_latency_ms}ms'}",
                                intensity=_pain,
                                metadata={
                                    "error": _error or None,
                                    "latency_ms": _latency_ms,
                                    "ts_ms": ts_ms,
                                },
                            )
                    except Exception:
                        pass
        except Exception:
            pass

    async def _on_gate_state_changed(self, event: Any) -> None:
        """处理 GATE_STATE_CHANGED：同步平台门控状态。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "all").strip().lower() or "all"
            gate = str(payload.get("gate") or "").strip().lower()
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            if gate not in {"open", "closed", "pause"}:
                return
            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            gate_state = dict(prev_state.get("gate_state") or {})
            gate_state[platform] = gate
            prev_state.update({
                "gate_state": gate_state,
                "last_gate_change_ts_ms": ts_ms,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
        except Exception:
            pass

    async def _on_platform_mute_changed(self, event: Any) -> None:
        """处理 PLATFORM_MUTE_CHANGED：同步平台静音状态。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            platform = str(payload.get("platform") or "all").strip().lower() or "all"
            mute = bool(payload.get("mute", True))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            mute_state = dict(prev_state.get("mute_state") or {})
            mute_state[platform] = mute
            prev_state.update({
                "mute_state": mute_state,
                "last_mute_change_ts_ms": ts_ms,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
        except Exception:
            pass

    # ── 跨平台情境感知（A 级升级）───────────────────────────────────────────

    def analyze_peripheral_context(self) -> str | None:
        """跨平台情境感知：调用 LLM 分析当前平台连接状态的语义含义。

        触发条件：有活跃平台连接。
        调用：Flash 小模型。
        开关：PERIPHERAL_CONTEXT_ANALYSIS_ENABLED（默认 false）。
        输出注入：PrefrontalAgent.plan_response() 的 context 字段。

        Returns:
            情境感知描述字符串，或 None（未触发/失败）。
        """
        pass  # 无灰度开关，直接执行

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.peripheral_agent")
        try:
            state = self.get_peripheral_state()
            _active_platforms = list(state.get("connected_platforms") or [])
            if not _active_platforms:
                return None

            from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json  # noqa: PLC0415
            from host_brain.tribe.model_invocation import complete_tribe_json  # noqa: PLC0415

            _tpl = resolve_prompt_contract_json("peripheral_agent.context_analysis") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("peripheral_agent.context_analysis 模板未配置")

            _gate_state = dict(state.get("gate_state") or {})
            _mute_state = dict(state.get("mute_state") or {})
            _user_prompt = _user_tpl.format(
                active_platforms=str(_active_platforms),
                connection_status="connected" if _active_platforms else "disconnected",
                gate_status=str(_gate_state.get("state") or "open"),
                mute_status=str(_mute_state.get("muted") or False),
            )
            _parsed = complete_tribe_json(
                attempt_service=self.application_ports.require("model_attempt_service")(),
                semantic_id="peripheral_context_analysis",
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
            )
            _hint = str(_parsed.get("context_hint") or "").strip()[:60]
            if _hint:
                _log.debug("PeripheralAgent.analyze_peripheral_context: %s", _hint)
                try:
                    self.state_registry.set_state(
                        "peripheral_agent",
                        {"last_context_hint": _hint},
                        writer="peripheral_agent",
                    )
                except Exception:
                    pass
            return _hint or None
        except Exception as exc:
            _log.debug("PeripheralAgent.analyze_peripheral_context 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        """导出 Peripheral 运行时状态，供 CortexAgent 收口与监控使用。"""
        try:
            state = self.get_peripheral_state()
            return {
                "connected_platforms": list(state.get("connected_platforms") or []),
                "gate_state": dict(state.get("gate_state") or {}),
                "mute_state": dict(state.get("mute_state") or {}),
                "suppressed": bool(state.get("suppressed", False)),
                "suppressed_risk_level": state.get("suppressed_risk_level"),
                "current_emotion_tag": state.get("current_emotion_tag"),
                "current_intensity": _to_float(state.get("current_intensity"), 0.0),
                "voice_tone": state.get("voice_tone"),
                "motion_scale": _to_float(state.get("motion_scale"), 0.0),
                "last_platform_event": state.get("last_platform_event"),
                "last_connected_ts_ms": int(state.get("last_connected_ts_ms") or 0),
                "last_disconnected_ts_ms": int(state.get("last_disconnected_ts_ms") or 0),
                "last_edge_peripheral_report_ts_ms": int(state.get("last_edge_peripheral_report_ts_ms") or 0),
                "edge_peripheral_error": state.get("edge_peripheral_error"),
            }
        except Exception:
            return {
                "connected_platforms": [],
                "gate_state": {},
                "mute_state": {},
                "suppressed": False,
                "suppressed_risk_level": None,
                "current_emotion_tag": None,
                "current_intensity": 0.0,
                "voice_tone": None,
                "motion_scale": 0.0,
                "last_platform_event": None,
                "last_connected_ts_ms": 0,
                "last_disconnected_ts_ms": 0,
                "last_edge_peripheral_report_ts_ms": 0,
                "edge_peripheral_error": None,
            }


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def get_peripheral_agent() -> PeripheralAgent:
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        require_active_tribe_agent,
    )

    return require_active_tribe_agent(
        "peripheral_agent",
        expected_type=PeripheralAgent,
    )


__all__ = ["PeripheralAgent", "get_peripheral_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
