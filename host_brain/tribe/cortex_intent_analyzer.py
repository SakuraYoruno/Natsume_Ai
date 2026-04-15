"""CortexIntentAnalyzer：TRIBE 族上下文聚合模块。

负责从各族 Agent 收集运行时上下文，供 chat_context_builder 使用。
原属 CortexAgent.collect_tribe_context()，拆分后由 CortexAgent 委托调用。
"""

from __future__ import annotations

from typing import Any

from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


class CortexIntentAnalyzer:
    """Supervisor 上下文聚合器：统一收集各族 Agent 运行时上下文。

    T-11：CortexAgent 作为 Supervisor，通过本类聚合：
    - LimbicAgent：情绪上下文、沉默意愿、body_sense 快照
    - HippocampusAgent：记忆召回摘要（仅元数据，不触发新召回）
    - SocialBrainAgent：Bond 阶段、关系状态
    - EndocrineAgent：内分泌门控状态（vital_signs）
    - TribeStateRegistry：aggregate_vital_signs() 统一生命体征
    - PainRewardAgent：完整享乐状态快照（T-20）
    """

    def __init__(self, state_registry: TribeStateRegistry | None = None) -> None:
        self.state_registry: TribeStateRegistry = state_registry or get_global_registry()

    def collect_tribe_context(
        self,
        *,
        request_body: Any | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """从各族 Agent 收集运行时上下文，供 chat_context_builder 使用。

        返回字段：
            vital_signs (dict): 统一生命体征聚合
            emotion_context (dict): 情绪上下文快照
            bond_context (dict): Bond/关系上下文
            endocrine_gate (dict): 内分泌门控状态
            silence_willingness (float): 沉默意愿
            voice_emotion (dict | None): 最近语音情绪推断
            hedonic_state (dict): 完整享乐状态（T-20）
            tribe_context_source (str): 数据来源标记
        """
        result: dict[str, Any] = {
            "vital_signs": {},
            "emotion_context": {},
            "bond_context": {},
            "bond_vector": {},
            "endocrine_gate": {},
            "silence_willingness": 0.5,
            "voice_emotion": None,
            "hedonic_state": {},
            "immune_state": {},
            "loneliness_level": 0.0,
            "somatic_state": {},
            "peripheral_state": {},
            "circadian_state": {},
            "proprioception_state": {},
            "initiative_decision": {},
            "default_mode_state": {},
            "mirror_neuron_state": {},
            "memory_layers": {},
            "language_dual_system": {},
            "homeostasis_report": {},
            "interoception_state": {},
            "motor_cortex_state": {},
            "cerebellum_state": {},
            "glymphatic_state": {},
            "vestibular_state": {},
            "tribe_context_source": "cortex_intent_analyzer",
        }

        # ── 1. 统一生命体征聚合（TribeStateRegistry）────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            vital_signs = get_limbic_agent().state_registry.aggregate_vital_signs()
            result["vital_signs"] = vital_signs
            result["homeostasis_report"] = dict(vital_signs.get("homeostasis_report") or {})
            result["silence_willingness"] = float(vital_signs.get("silence_willingness") or 0.5)
            if vital_signs.get("voice_emotion_tag"):
                result["voice_emotion"] = {
                    "emotion_tag": vital_signs["voice_emotion_tag"],
                    "emotion_intensity": vital_signs.get("voice_emotion_intensity"),
                }
        except Exception:
            pass

        # ── 2. 情绪上下文（LimbicAgent）──────────────────────────────────────
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

            limbic = get_limbic_agent()
            if request_body is not None:
                emotion_ctx = limbic.get_emotion_context(request_body=request_body)
            else:
                last_ctx = limbic.state_registry.get_state("last_emotion_context")
                emotion_ctx = dict((last_ctx or {}).get("state") or {})
            result["emotion_context"] = emotion_ctx
        except Exception:
            pass

        # ── 3. Bond/关系上下文（SocialBrainAgent）────────────────────────────
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            social = get_social_brain_agent()
            bond_ctx = social.get_bond_context(
                session_id=str(session_id or ""),
                user_id=str(user_id or ""),
            )
            result["bond_context"] = bond_ctx
            result["bond_vector"] = dict(bond_ctx.get("bond_vector") or {})
        except Exception:
            pass

        # ── 4. 内分泌门控（EndocrineAgent）───────────────────────────────────
        try:
            from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415

            endocrine = get_endocrine_agent()
            vitals = endocrine.resolve_vital_signs()
            result["endocrine_gate"] = vitals
        except Exception:
            pass

        # ── 5. 享乐状态（PainRewardAgent）T-20 ───────────────────────────────
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            pain_agent = get_pain_reward_agent()
            result["hedonic_state"] = pain_agent.get_hedonic_state()
        except Exception:
            pass

        # ── 6. 免疫状态（ImmuneAgent）T-38 ───────────────────────────────────
        try:
            from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415

            result["immune_state"] = get_immune_agent().export_runtime_state()
        except Exception:
            pass

        # ── 7. 孤独感强度（PainRewardAgent）T-76 ─────────────────────────────
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415

            result["loneliness_level"] = get_pain_reward_agent().get_loneliness_level()
        except Exception:
            pass

        # ── 8. 躯体状态（SomaticAgent）───────────────────────────────────────
        try:
            from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415

            result["somatic_state"] = get_somatic_agent().export_runtime_state()
        except Exception:
            pass

        # ── 9. 外周状态（PeripheralAgent）────────────────────────────────────
        try:
            from host_brain.tribe.peripheral_agent import get_peripheral_agent  # noqa: PLC0415

            result["peripheral_state"] = get_peripheral_agent().export_runtime_state()
        except Exception:
            pass

        # ── 10. bond_trend_hint（SocialBrainAgent 快照）T-54 ─────────────────
        try:
            from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415

            _sba_snap = get_global_registry().get_state("social_brain_agent") or {}
            _sba_state = dict(_sba_snap.get("state") or {})
            _hint = str(_sba_state.get("bond_trend_hint") or "").strip().lower()
            if _hint:
                result["bond_trend_hint"] = _hint
                result["bond_trend_hint_ts_ms"] = int(_sba_state.get("bond_trend_hint_ts_ms") or 0)
        except Exception:
            pass

        # ── 11. Circadian / Proprioception / InitiativeGovernor──────────────
        try:
            from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
            from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
            from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415

            result["circadian_state"] = get_circadian_agent().export_runtime_state()
            result["proprioception_state"] = get_proprioception_agent().export_runtime_state()
            result["initiative_decision"] = get_initiative_governor_agent().get_last_decision()
        except Exception:
            pass

        # ── 12. DefaultMode / MirrorNeuron（内在生活 + 共情共振）──────────────
        try:
            from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
            from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415

            result["default_mode_state"] = get_default_mode_agent().export_runtime_state()
            result["mirror_neuron_state"] = get_mirror_neuron_agent().export_runtime_state()
        except Exception:
            pass

        # ── 13. Memory Layers（三分层记忆快照）────────────────────────────────
        try:
            from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

            result["memory_layers"] = get_hippocampus_agent().resolve_memory_layer_snapshot()
        except Exception:
            pass

        # ── 14. Language Dual System（Wernicke/Broca）──────────────────────
        try:
            from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415

            language_agent = get_language_system_agent()
            latest_state_row = language_agent.state_registry.get_state("language_system_agent") or {}
            latest_state = dict(latest_state_row.get("state") or {})
            dual_snapshot = dict(latest_state.get("last_dual_system") or {})

            if not dual_snapshot:
                user_text = ""
                if isinstance(request_body, dict):
                    user_text = str(
                        request_body.get("user_message")
                        or request_body.get("message")
                        or request_body.get("text")
                        or ""
                    ).strip()
                elif request_body is not None:
                    user_text = str(
                        getattr(request_body, "user_message", None)
                        or getattr(request_body, "message", None)
                        or getattr(request_body, "text", None)
                        or ""
                    ).strip()
                if user_text:
                    vital_signs = dict(result.get("vital_signs") or {})
                    dual_snapshot = language_agent.resolve_dual_system(
                        user_text=user_text,
                        bond_stage=str(vital_signs.get("bond_stage") or "stranger"),
                        emotion_tag=str(vital_signs.get("emotion_tag") or "NEUTRAL"),
                        loneliness_level=float(vital_signs.get("loneliness_level") or 0.0),
                        high_loneliness_flag=bool(vital_signs.get("high_loneliness_flag", False)),
                        response_goal="chat_reply",
                    )
            result["language_dual_system"] = dual_snapshot
        except Exception:
            pass

        # ── 15. Homeostasis fallback（当 vital_signs 未携带报告时）───────────
        if not result.get("homeostasis_report"):
            try:
                result["homeostasis_report"] = self.state_registry.get_homeostasis_report()
            except Exception:
                pass

        # ── 16. 扩充族（Interoception/Motor/Cerebellum/Glymphatic/Vestibular）──
        try:
            from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
            from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: PLC0415
            from host_brain.tribe.cerebellum_agent import get_cerebellum_agent  # noqa: PLC0415
            from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
            from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415

            result["interoception_state"] = get_interoception_agent().export_runtime_state()
            result["motor_cortex_state"] = get_motor_cortex_agent().export_runtime_state()
            result["cerebellum_state"] = get_cerebellum_agent().export_runtime_state()
            result["glymphatic_state"] = get_glymphatic_agent().export_runtime_state()
            result["vestibular_state"] = get_vestibular_agent().export_runtime_state()
        except Exception:
            pass

        # ── 17. T-223：PainReward 完整快照 + MirrorNeuron 共情快照 ──────────
        try:
            from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
            result["pain_reward_state"] = get_pain_reward_agent().get_hedonic_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415
            result["mirror_neuron_resonance"] = get_mirror_neuron_agent().export_runtime_state()
        except Exception:
            pass

        # ── 18. T-236：SomaticAgent 躯体感知快照 + LanguageSystemAgent 双系统快照 ──
        try:
            from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: PLC0415
            result["somatic_state"] = get_somatic_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415
            _lang_agent = get_language_system_agent()
            _lang_snap = _lang_agent.state_registry.get_state("language_system_agent") or {}
            _lang_state = dict(_lang_snap.get("state") or {})
            result["language_system_state"] = {
                "last_wernicke": dict(_lang_state.get("last_wernicke") or {}),
                "last_broca": dict(_lang_state.get("last_broca") or {}),
                "last_language_style": dict(_lang_state.get("last_language_style") or {}),
            }
        except Exception:
            pass

        # ── 19. T-244：ImmuneAgent 风险快照 + VestibularAgent 路由偏置快照 ──
        try:
            from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
            result["immune_state"] = get_immune_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.vestibular_agent import get_vestibular_agent  # noqa: PLC0415
            result["vestibular_state"] = get_vestibular_agent().export_runtime_state()
        except Exception:
            pass

        # ── 20. T-255：CircadianAgent 日节律 + ANSAgent 节律状态 + MirrorNeuronAgent 共情快照 ──
        try:
            from host_brain.tribe.circadian_agent import get_circadian_agent  # noqa: PLC0415
            result["circadian_state"] = get_circadian_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415
            result["ans_state"] = get_ans_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.mirror_neuron_agent import get_mirror_neuron_agent  # noqa: PLC0415
            result["mirror_neuron_state"] = get_mirror_neuron_agent().export_runtime_state()
        except Exception:
            pass

        # ── 21. T-262：PrefrontalAgent 漂移快照 + PeripheralAgent 外设感知快照 ──
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
            result["prefrontal_state"] = get_prefrontal_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.peripheral_agent import get_peripheral_agent  # noqa: PLC0415
            result["peripheral_state"] = get_peripheral_agent().export_runtime_state()
        except Exception:
            pass

        # ── 22. T-263：HippocampusAgent 记忆快照 + InitiativeGovernorAgent 门控快照 ──
        try:
            from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415
            result["hippocampus_state"] = get_hippocampus_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.initiative_governor_agent import get_initiative_governor_agent  # noqa: PLC0415
            result["initiative_governor_state"] = get_initiative_governor_agent().export_runtime_state()
        except Exception:
            pass

        # ── 23. T-276：DefaultModeAgent 连续性快照 + SocialBrainAgent 关系快照 ──
        try:
            from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
            result["default_mode_state"] = get_default_mode_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
            result["social_brain_state"] = get_social_brain_agent().export_runtime_state()
        except Exception:
            pass
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
            result["prefrontal_state"] = get_prefrontal_agent().export_runtime_state()
        except Exception:
            pass

        # ── 24. T-282：InteroceptionAgent 身体预算快照 ──────────────────────
        try:
            from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
            result["interoception_state"] = get_interoception_agent().export_runtime_state()
        except Exception:
            pass

        return result


__all__ = ["CortexIntentAnalyzer"]
