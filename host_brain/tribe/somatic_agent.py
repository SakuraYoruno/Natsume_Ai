from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class SomaticAgent:
    """T-109：躯体感知 Agent 骨架。

    负责接收来自 edge_client 的躯体感知信号（BODY_SENSE_UPDATED），
    以及孤独感信号（LONELINESS_SIGNAL），将躯体状态写入快照，
    供 chat_context_builder 在构建 prompt 时参考身体感知状态。

    Phase 6（edge_client 接入）时将扩展为完整实现。
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="somatic_agent",
            family="somatic",
            execution_side="host",
            owner_domain="brain",
            capabilities=("body_sense", "somatic_state", "physical_feedback"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def get_somatic_state(self) -> dict[str, Any]:
        """返回当前躯体感知状态快照。"""
        try:
            snap = self.state_registry.get_state("somatic_agent") or {}
            return dict(snap.get("state") or {})
        except Exception:
            return {}

    # ── T-109：TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """T-109：向 TribeEventBus 注册事件订阅。

        订阅：
        - BODY_SENSE_UPDATED：躯体感知更新时写入快照（来自 edge_client）
        - LONELINESS_SIGNAL：孤独感信号时记录躯体孤独感状态
        - SOMATIC_STATE_UPDATED：接收 edge_client SomaticAgent 上报的状态快照（T-165）
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BODY_SENSE_UPDATED", self._on_body_sense_updated)
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
            bus.subscribe("SOMATIC_STATE_UPDATED", self._on_somatic_state_updated)  # T-165
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.somatic_agent").warning(
                "SomaticAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_body_sense_updated(self, event: Any) -> None:
        """处理 BODY_SENSE_UPDATED 事件：将躯体感知数据写入 somatic_agent 快照。

        T-109：记录最近一次躯体感知信号的各项指标，
        Phase 6 接入 edge_client 后将携带真实传感器数据。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            # 躯体感知字段（Phase 6 扩展）
            heart_rate = payload.get("heart_rate")
            skin_conductance = payload.get("skin_conductance")
            body_temp = payload.get("body_temp")
            motion_level = max(0.0, min(1.0, float(payload.get("motion_level") or 0.0)))
            fatigue_level = max(0.0, min(1.0, float(payload.get("fatigue_level") or 0.0)))

            state: dict[str, Any] = {
                "last_body_sense_ts_ms": ts_ms,
                "motion_level": round(motion_level, 4),
                "fatigue_level": round(fatigue_level, 4),
            }
            if heart_rate is not None:
                try:
                    state["heart_rate"] = float(heart_rate)
                except (TypeError, ValueError):
                    pass
            if skin_conductance is not None:
                try:
                    state["skin_conductance"] = float(skin_conductance)
                except (TypeError, ValueError):
                    pass
            if body_temp is not None:
                try:
                    state["body_temp"] = float(body_temp)
                except (TypeError, ValueError):
                    pass

            prev = self.state_registry.get_state("somatic_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update(state)
            self.state_registry.set_state("somatic_agent", prev_state, writer="somatic_agent")

            # T-293：高疲劳(>=0.60)时联动 InteroceptionAgent 注入外部压力
            if fatigue_level >= 0.60:
                try:
                    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "inject_external_stress"):
                        _fatigue_stress = round(min(0.50, (fatigue_level - 0.60) * 1.25), 4)
                        _intero.inject_external_stress(
                            source=f"somatic_high_fatigue:{round(fatigue_level, 3)}",
                            intensity=_fatigue_stress,
                            ts_ms=ts_ms,
                        )
                except Exception:
                    pass
                # T-304：高疲劳时联动 EndocrineAgent 记录躯体压力事件（疲劳是内在压力源）
                try:
                    from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
                    _endo = get_endocrine_agent()
                    if hasattr(_endo, "record_stress_event"):
                        _somatic_stress = round(min(0.40, (fatigue_level - 0.60) * 1.00), 4)
                        _endo.record_stress_event(
                            source=f"somatic_high_fatigue:{round(fatigue_level, 3)}",
                            intensity=_somatic_stress,
                        )
                except Exception:
                    pass
                # T-359：高疲劳（>=0.75）时联动 PainRewardAgent 记录躯体痛苦
                # 极度疲劳是持续性躯体痛苦（身体预算严重透支）
                if fatigue_level >= 0.75:
                    try:
                        from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                        _pr = get_pain_reward_agent()
                        if hasattr(_pr, "record_pain"):
                            _fatigue_pain = round(min(0.45, (fatigue_level - 0.75) * 1.80), 4)
                            _pr.record_pain(
                                source=f"somatic_extreme_fatigue:{round(fatigue_level, 3)}",
                                intensity=_fatigue_pain,
                                metadata={
                                    "fatigue_level": round(fatigue_level, 4),
                                    "ts_ms": ts_ms,
                                },
                            )
                    except Exception:
                        pass

            # T-345：低疲劳（< 0.30）时联动 InteroceptionAgent 降低 recovery_urgency
            # 躯体恢复良好是最直接的恢复信号，应主动释放身体预算压力
            if fatigue_level < 0.30:
                try:
                    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "reduce_recovery_urgency"):
                        _relief = round(min(0.35, (0.30 - fatigue_level) * 1.17), 4)
                        _intero.reduce_recovery_urgency(
                            source=f"somatic_low_fatigue:{round(fatigue_level, 3)}",
                            relief=_relief,
                            ts_ms=ts_ms,
                        )
                except Exception:
                    pass
                # T-350：低疲劳时联动 LimbicAgent 触发轻度舒适情绪（躯体恢复带来身体愉悦感）
                try:
                    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _comfort = round(min(0.15, (0.30 - fatigue_level) * 0.50), 4)
                        _limbic.drive_emotion_metabolism(
                            valence=_comfort,
                            arousal=round(-_comfort * 0.40, 4),  # 低疲劳时略微降低唤醒（放松感）
                            dominance=0.03,
                            blend_weight=0.10,
                            event_kind=f"somatic_recovery:fatigue={round(fatigue_level, 3)}",
                            source="somatic_agent",
                        )
                except Exception:
                    pass
                # T-390：低疲劳（fatigue<0.25）时联动 PainRewardAgent 记录躯体舒适奖励
                # 身体充盈、疲劳极低是一种轻度正向奖励（类似运动后的清爽感）
                if fatigue_level < 0.25:
                    try:
                        from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                        _pr = get_pain_reward_agent()
                        if hasattr(_pr, "record_reward"):
                            _somatic_reward = round(min(0.18, (0.25 - fatigue_level) * 0.72), 4)
                            _pr.record_reward(
                                source=f"somatic_low_fatigue:{round(fatigue_level, 3)}",
                                intensity=_somatic_reward,
                                metadata={
                                    "fatigue_level": round(fatigue_level, 4),
                                    "ts_ms": ts_ms,
                                },
                            )
                    except Exception:
                        pass
        except Exception:
            pass

    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：记录躯体孤独感状态。

        T-109：孤独感信号到达时，将孤独感强度写入 somatic_agent 快照，
        供躯体反馈系统（Phase 6）参考（如触觉反馈、体温调节等）。
        """
        try:
            payload = dict(getattr(event, 'payload', None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            ts_ms = int(getattr(event, 'ts_ms', 0) or 0)
            prev = self.state_registry.get_state("somatic_agent") or {}
            prev_state = dict(prev.get("state") or {})
            comfort_needed = loneliness > 0.65
            prev_state.update({
                "last_loneliness_level": round(loneliness, 4),
                "last_loneliness_ts_ms": ts_ms,
                # 高孤独感时标记需要躯体安抚反馈
                "somatic_comfort_needed": comfort_needed,
            })
            self.state_registry.set_state("somatic_agent", prev_state, writer="somatic_agent")

            # T-226：高孤独感时通知 InteroceptionAgent 提高 recovery_urgency
            if comfort_needed:
                try:
                    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                    intero = get_interoception_agent()
                    if hasattr(intero, "inject_external_stress"):
                        intero.inject_external_stress(
                            source="somatic_loneliness",
                            intensity=round(loneliness * 0.6, 4),
                            ts_ms=ts_ms,
                        )
                except Exception:
                    pass
        except Exception:
            pass

    # ── T-165：接收 edge_client 上报的躯体状态快照 ────────────────────────────

    async def _on_somatic_state_updated(self, event: Any) -> None:
        """处理 SOMATIC_STATE_UPDATED 事件：接收 edge_client SomaticAgent 上报的状态快照。

        T-165：edge_client SomaticAgent.publish_somatic_state() 发布此事件，
        host_brain SomaticAgent 接收后将 VTS 连接状态、当前表情等写入快照，
        供 CortexAgent.collect_tribe_context() 读取。
        """
        try:
            payload = dict(getattr(event, 'payload', None) or {})
            ts_ms = int(getattr(event, 'ts_ms', 0) or 0)
            prev = self.state_registry.get_state("somatic_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "vts_connected": bool(payload.get("vts_connected", False)),
                "current_expression": str(payload.get("current_expression") or "").strip() or None,
                "last_emotion_tag": str(payload.get("last_emotion_tag") or "").strip().upper() or None,
                "last_intensity": (
                    round(max(0.0, min(1.0, float(payload["last_intensity"]))), 4)
                    if isinstance(payload.get("last_intensity"), (int, float))
                    else None
                ),
                "edge_somatic_error": str(payload.get("error") or "").strip() or None,
                "last_edge_somatic_report_ts_ms": ts_ms,
            })
            self.state_registry.set_state("somatic_agent", prev_state, writer="somatic_agent")
        except Exception:
            pass

    # ── 躯体感受叙事（A 级升级）──────────────────────────────────────────────

    def generate_somatic_narrative(
        self,
        *,
        somatic_pressure: float = 0.0,
        user_expression: str = "NEUTRAL",
        face_confidence: float = 0.0,
    ) -> str | None:
        """将躯体信号转化为自然语言身体感受叙事，注入 Prompt Phase XIII 情绪层。

        触发条件：somatic_pressure > 0.4 或 face_confidence > 0.5（面捕有效）。
        调用：Flash 小模型。
        开关：SOMATIC_NARRATIVE_ENABLED（默认 false）。

        Returns:
            躯体感受描述字符串，或 None（未触发/失败）。
        """
        _significant = (
            float(somatic_pressure or 0.0) > 0.4
            or (float(face_confidence or 0.0) > 0.5 and str(user_expression or "NEUTRAL").upper() != "NEUTRAL")
        )
        if not _significant:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.somatic_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("somatic_agent.somatic_narrative") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("somatic_agent.somatic_narrative 模板未配置")

            _user_prompt = _user_tpl.format(
                somatic_pressure=round(float(somatic_pressure), 3),
                user_expression=str(user_expression or "NEUTRAL").upper(),
                face_confidence=round(float(face_confidence), 3),
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=1500,
                agent_role="somatic_narrator",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=32,
                temperature=0.2,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _hint = str(_parsed.get("somatic_hint") or "").strip()[:40]
            if _hint:
                _log.debug("SomaticAgent.generate_somatic_narrative: %s", _hint)
                try:
                    self.state_registry.set_state(
                        "somatic_agent",
                        {"last_somatic_hint": _hint},
                        writer="somatic_agent",
                    )
                except Exception:
                    pass
            return _hint or None
        except Exception as exc:
            _log.debug("SomaticAgent.generate_somatic_narrative 失败（降级）: %s", exc)
            return None

    # ── T-170：运行时状态导出 ─────────────────────────────────────────────────

    def export_runtime_state(self) -> dict[str, Any]:
        """T-170：导出 SomaticAgent 运行时状态，供 CortexAgent.collect_tribe_context() 读取。

        整合 state_registry 快照中的躯体感知数据，返回结构化状态字典，
        包含 VTS 连接状态、当前表情、疲劳度、孤独感等关键字段。

        Returns:
            {
                "vts_connected": bool,
                "current_expression": str | None,
                "last_emotion_tag": str | None,
                "last_intensity": float | None,
                "motion_level": float,
                "fatigue_level": float,
                "last_loneliness_level": float,
                "somatic_comfort_needed": bool,
                "heart_rate": float | None,
                "edge_somatic_error": str | None,
                "last_body_sense_ts_ms": int,
                "last_edge_somatic_report_ts_ms": int,
            }
        """
        try:
            state = self.get_somatic_state()
            return {
                "vts_connected": bool(state.get("vts_connected", False)),
                "current_expression": state.get("current_expression"),
                "last_emotion_tag": state.get("last_emotion_tag"),
                "last_intensity": state.get("last_intensity"),
                "motion_level": float(state.get("motion_level") or 0.0),
                "fatigue_level": float(state.get("fatigue_level") or 0.0),
                "last_loneliness_level": float(state.get("last_loneliness_level") or 0.0),
                "somatic_comfort_needed": bool(state.get("somatic_comfort_needed", False)),
                "heart_rate": state.get("heart_rate"),
                "skin_conductance": state.get("skin_conductance"),
                "body_temp": state.get("body_temp"),
                "edge_somatic_error": state.get("edge_somatic_error"),
                "last_body_sense_ts_ms": int(state.get("last_body_sense_ts_ms") or 0),
                "last_edge_somatic_report_ts_ms": int(
                    state.get("last_edge_somatic_report_ts_ms") or 0
                ),
            }
        except Exception:
            return {
                "vts_connected": False,
                "current_expression": None,
                "last_emotion_tag": None,
                "last_intensity": None,
                "motion_level": 0.0,
                "fatigue_level": 0.0,
                "last_loneliness_level": 0.0,
                "somatic_comfort_needed": False,
                "heart_rate": None,
                "skin_conductance": None,
                "body_temp": None,
                "edge_somatic_error": None,
                "last_body_sense_ts_ms": 0,
                "last_edge_somatic_report_ts_ms": 0,
            }


_SOMATIC_AGENT: SomaticAgent | None = None


def get_somatic_agent() -> SomaticAgent:
    global _SOMATIC_AGENT
    if _SOMATIC_AGENT is None:
        _SOMATIC_AGENT = SomaticAgent()
    return _SOMATIC_AGENT


def set_somatic_agent(agent: SomaticAgent | None) -> None:
    global _SOMATIC_AGENT
    _SOMATIC_AGENT = agent


__all__ = ["SomaticAgent", "get_somatic_agent", "set_somatic_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
