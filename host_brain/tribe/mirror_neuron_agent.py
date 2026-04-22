from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _emotion_anchor(tag: str) -> tuple[float, float]:
    normalized = str(tag or "NEUTRAL").strip().upper()
    anchors: dict[str, tuple[float, float]] = {
        "JOY": (0.80, 0.60),
        "HAPPY": (0.65, 0.52),
        "EXCITED": (0.70, 0.76),
        "SAD": (-0.66, 0.36),
        "LONELY": (-0.52, 0.34),
        "ANGRY": (-0.68, 0.78),
        "ANXIOUS": (-0.48, 0.70),
        "FEAR": (-0.62, 0.75),
        "CALM": (0.20, 0.25),
        "NEUTRAL": (0.00, 0.32),
    }
    return anchors.get(normalized, anchors["NEUTRAL"])


@dataclass(slots=True)
class MirrorNeuronAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="mirror_neuron_agent",
            family="mirror_neuron",
            execution_side="host",
            owner_domain="brain",
            capabilities=("emotion_resonance", "empathy_shift", "co_regulation"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("EMOTION_INFERENCE_UPDATED", self._on_emotion_inference_updated)
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
        except Exception:
            pass

    def compute_resonance(
        self,
        *,
        user_emotion_tag: str,
        user_emotion_intensity: float,
        bond_vector: Mapping[str, Any] | None = None,
        stress_index: float = 0.0,
        voice_emotion_tag: str = "",
    ) -> dict[str, Any]:
        bond = dict(bond_vector or {})
        trust = _clamp(float(bond.get("trust") or 0.4))
        affinity = _clamp(float(bond.get("affinity") or 0.4))
        safety = _clamp(float(bond.get("safety") or 0.4))
        repair_confidence = _clamp(float(bond.get("repair_confidence") or 0.5))
        intensity = _clamp(float(user_emotion_intensity))
        stress = _clamp(float(stress_index))
        tag = str(user_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        anchor_valence, anchor_arousal = _emotion_anchor(tag)

        bond_factor = _clamp(0.45 + trust * 0.30 + affinity * 0.20 + safety * 0.15, low=0.2, high=1.15)
        stress_penalty = _clamp(1.0 - stress * 0.55, low=0.45, high=1.0)
        repair_boost = _clamp(0.82 + repair_confidence * 0.30, low=0.75, high=1.12)
        voice_sync_bonus = 1.0
        if str(voice_emotion_tag or "").strip().upper() == tag and tag != "NEUTRAL":
            voice_sync_bonus = 1.08

        resonance_strength = _clamp(intensity * bond_factor * stress_penalty * repair_boost * voice_sync_bonus)
        valence_shift = max(-0.35, min(0.35, anchor_valence * resonance_strength * 0.58))
        arousal_shift = max(-0.30, min(0.30, (anchor_arousal - 0.35) * resonance_strength * 0.70))
        co_regulation_hint = (
            "stabilize"
            if stress > 0.65 and tag in {"SAD", "LONELY", "ANXIOUS", "FEAR", "ANGRY"}
            else "resonate"
        )
        return {
            "emotion_tag": tag,
            "input_intensity": round(intensity, 4),
            "resonance_strength": round(resonance_strength, 4),
            "valence_shift": round(valence_shift, 4),
            "arousal_shift": round(arousal_shift, 4),
            "co_regulation_hint": co_regulation_hint,
        }

    def apply_resonance(
        self,
        *,
        user_id: str,
        session_id: str = "",
        emotion_tag: str,
        emotion_intensity: float,
        source: str,
        ts_ms: int | None = None,
    ) -> dict[str, Any]:

        resolved_ts = int(ts_ms or now_ms())
        social = get_social_brain_agent()
        bond_ctx = social.get_bond_context(session_id=str(session_id or "default"), user_id=str(user_id or ""))
        bond_vector = dict(bond_ctx.get("bond_vector") or {})
        vitals = self.state_registry.aggregate_vital_signs()
        stress_index = float(vitals.get("stress_index") or 0.0)
        voice_tag = str(vitals.get("voice_emotion_tag") or "")
        resonance = self.compute_resonance(
            user_emotion_tag=emotion_tag,
            user_emotion_intensity=emotion_intensity,
            bond_vector=bond_vector,
            stress_index=stress_index,
            voice_emotion_tag=voice_tag,
        )
        result = {
            "user_id": str(user_id or "").strip() or None,
            "session_id": str(session_id or "").strip() or None,
            "source": str(source or "runtime").strip() or "runtime",
            "emotion_tag": resonance["emotion_tag"],
            "input_intensity": resonance["input_intensity"],
            "resonance_strength": resonance["resonance_strength"],
            "valence_shift": resonance["valence_shift"],
            "arousal_shift": resonance["arousal_shift"],
            "co_regulation_hint": resonance["co_regulation_hint"],
            "bond_vector": {
                "trust": _clamp(float(bond_vector.get("trust") or 0.4)),
                "affinity": _clamp(float(bond_vector.get("affinity") or 0.4)),
                "safety": _clamp(float(bond_vector.get("safety") or 0.4)),
                "repair_confidence": _clamp(float(bond_vector.get("repair_confidence") or 0.5)),
            },
            "stress_index": round(_clamp(stress_index), 4),
            "ts_ms": resolved_ts,
        }
        prev = self.state_registry.get_state("mirror_neuron_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state["last_resonance"] = result
        prev_state["last_valence_shift"] = result["valence_shift"]
        prev_state["last_arousal_shift"] = result["arousal_shift"]
        prev_state["last_emotion_tag"] = result["emotion_tag"]
        prev_state["last_source"] = result["source"]
        prev_state["last_ts_ms"] = resolved_ts
        prev_state["resonance_count"] = int(prev_state.get("resonance_count") or 0) + 1
        self.state_registry.set_state("mirror_neuron_agent", prev_state, writer="mirror_neuron_agent")

        emit_tribe_event(
            event_type="MIRROR_RESONANCE_UPDATED",
            source_agent="mirror_neuron_agent",
            payload={
                "user_id": result["user_id"],
                "session_id": result["session_id"],
                "emotion_tag": result["emotion_tag"],
                "resonance_strength": result["resonance_strength"],
                "valence_shift": result["valence_shift"],
                "arousal_shift": result["arousal_shift"],
                "co_regulation_hint": result["co_regulation_hint"],
                "ts_ms": resolved_ts,
            },
        )

        # T-217：共情强度达标时，将共情结果回写 LimbicAgent 情绪代谢
        resonance_strength = float(result.get("resonance_strength") or 0.0)
        if resonance_strength >= 0.45:
            try:
                limbic = get_limbic_agent()
                if hasattr(limbic, "drive_emotion_metabolism"):
                    limbic.drive_emotion_metabolism(
                        valence=round(float(result.get("valence_shift") or 0.0), 4),
                        arousal=round(float(result.get("arousal_shift") or 0.0), 4),
                        dominance=0.0,
                        blend_weight=round(resonance_strength * 0.40, 4),
                        event_kind=f"mirror_resonance:{result.get('emotion_tag', 'NEUTRAL')}",
                        source="mirror_neuron_agent",
                    )
            except Exception:
                pass

        # T-259：高共情(>=0.60)时联动 EndocrineAgent 分泌欲素（降低 cortisol，提升 energy_reserve）
        if resonance_strength >= 0.60:
            try:
                _endo = get_endocrine_agent()
                if hasattr(_endo, "_bond_cortisol_delta") and hasattr(_endo, "_bond_lock"):
                    # 高共情时降低 cortisol（欲素效应：社交联结降低压力激素）
                    _oxytocin_effect = round((resonance_strength - 0.60) * 0.20, 4)
                    with _endo._bond_lock:
                        _endo._bond_cortisol_delta = max(
                            -0.06,
                            float(_endo._bond_cortisol_delta) - _oxytocin_effect,
                        )
            except Exception:
                pass
            # T-305：高共情时联动 InteroceptionAgent 降低 recovery_urgency（共情是正向信号，缓解身体压力）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "reduce_recovery_urgency"):
                    _relief = round(min(0.40, (resonance_strength - 0.60) * 0.50), 4)
                    _intero.reduce_recovery_urgency(
                        source=f"mirror_high_resonance:{round(resonance_strength, 3)}",
                        relief=_relief,
                        ts_ms=resolved_ts,
                    )
            except Exception:
                pass
            # T-362：高正向共情（JOY/HAPPY/EXCITED）时联动 PainRewardAgent 记录共情快乐奖励
            # 共情他人的快乐本身就是一种奖励（镜像神经元激活带来的愉悦感）
            _pos_tags = {"JOY", "HAPPY", "EXCITED", "CONTENT", "GRATEFUL"}
            _resonance_tag_pos = str(result.get("emotion_tag") or "NEUTRAL").upper()
            if _resonance_tag_pos in _pos_tags:
                try:
                    _pr = get_pain_reward_agent()
                    if hasattr(_pr, "record_reward"):
                        _empathy_reward = round(min(0.35, (resonance_strength - 0.60) * 0.875), 4)
                        _pr.record_reward(
                            source=f"mirror_empathy_joy:{_resonance_tag_pos.lower()}",
                            intensity=_empathy_reward,
                            metadata={
                                "resonance_strength": round(resonance_strength, 4),
                                "emotion_tag": _resonance_tag_pos,
                            },
                        )
                except Exception:
                    pass
            # T-378：高共情时联动 DefaultModeAgent.tick_idle_cycle()
            # 共情他人的情绪是自我叙事的重要素材（"我感受到了对方的感受"）
            try:
                _dma = get_default_mode_agent()
                if hasattr(_dma, "tick_idle_cycle"):
                    _dma.tick_idle_cycle(
                        source=f"mirror_high_resonance:{str(result.get('emotion_tag') or 'neutral').lower()}",
                        person_id="user_1",
                    )
            except Exception:
                pass

        # T-352：高共情负荷（负向情绪 + 高共情强度）时联动 EndocrineAgent 记录共情压力事件
        # 高强度共情负向情绪（悲伤/愤怒/恐惧）会消耗内分泌储备（共情疲劳）
        _neg_tags = {"SAD", "LONELY", "ANGRY", "ANXIOUS", "FEAR"}
        _resonance_tag = str(result.get("emotion_tag") or "NEUTRAL").upper()
        if resonance_strength >= 0.65 and _resonance_tag in _neg_tags:
            try:
                _endo = get_endocrine_agent()
                if hasattr(_endo, "record_stress_event"):
                    _empathy_stress = round(min(0.35, (resonance_strength - 0.65) * 1.00), 4)
                    _endo.record_stress_event(
                        source=f"mirror_empathy_fatigue:{_resonance_tag.lower()}",
                        intensity=_empathy_stress,
                    )
            except Exception:
                pass

        return result

    # ── 共情回应建议（A 级升级）──────────────────────────────────────────────

    def generate_empathy_response_hint(
        self,
        *,
        resonance_intensity: float = 0.0,
        user_emotion_tag: str = "NEUTRAL",
        bond_stage: str = "stranger",
    ) -> str | None:
        """共情回应建议：共鸣强度高时调用 LLM 生成共情回应策略。

        触发条件：resonance_intensity > 0.6。
        调用：Flash 小模型。
        开关：MIRROR_EMPATHY_HINT_ENABLED（默认 false）。
        输出注入：PrefrontalAgent.plan_response() 的 tone_hint 字段。

        Returns:
            共情回应建议字符串，或 None（未触发/失败）。
        """
        if float(resonance_intensity or 0.0) <= 0.6:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.mirror_neuron_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("mirror_neuron_agent.empathy_response_hint") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("mirror_neuron_agent.empathy_response_hint 模板未配置")

            _user_prompt = _user_tpl.format(
                resonance_intensity=round(float(resonance_intensity), 3),
                user_emotion_tag=str(user_emotion_tag or "NEUTRAL").upper(),
                bond_stage=str(bond_stage or "stranger"),
            )
            _pool_result = complete_llm_with_pool_sync(
                request_id=f"mirror-empathy::{int(time.time() * 1000)}",
                scene_type="MIRROR_EMPATHY",
                cognitive_tier="C",
                timeout_ms=1500,
                messages=[
                    {"role": "system", "content": _system_prompt},
                    {"role": "user", "content": _user_prompt},
                ],
                gen_params={"temperature": 0.2, "max_tokens": 64},
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                response_format="json_object",
            )
            if _pool_result.pool_exhausted:
                raise RuntimeError("mirror empathy llm pool exhausted")
            _raw = _pool_result.content
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _hint = str(_parsed.get("empathy_hint") or "").strip()[:100]
            if _hint:
                _log.debug("MirrorNeuronAgent.generate_empathy_response_hint: %s", _hint)
                # 写入 state_registry 供 PrefrontalAgent 读取
                try:
                    self.state_registry.set_state(
                        "mirror_neuron_agent",
                        {"last_empathy_hint": _hint},
                        writer="mirror_neuron_agent",
                    )
                except Exception:
                    pass
                # 发布 MIRROR_EMPATHY_GENERATED 事件
                try:
                    emit_tribe_event(
                        event_type="MIRROR_EMPATHY_GENERATED",
                        source_agent="mirror_neuron_agent",
                        payload={
                            "empathy_hint": _hint,
                            "resonance_intensity": round(float(resonance_intensity), 3),
                            "user_emotion_tag": str(user_emotion_tag or "NEUTRAL").upper(),
                            "bond_stage": str(bond_stage or "stranger"),
                        },
                    )
                except Exception:
                    pass
            return _hint or None
        except Exception as exc:
            _log.debug("MirrorNeuronAgent.generate_empathy_response_hint 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("mirror_neuron_agent") or {}
        state = dict(snap.get("state") or {})
        last = dict(state.get("last_resonance") or {})
        return {
            "resonance_count": int(state.get("resonance_count") or 0),
            "last_emotion_tag": str(state.get("last_emotion_tag") or "NEUTRAL"),
            "last_resonance_strength": float(last.get("resonance_strength") or 0.0),
            "last_valence_shift": float(state.get("last_valence_shift") or 0.0),
            "last_arousal_shift": float(state.get("last_arousal_shift") or 0.0),
            "last_co_regulation_hint": str(last.get("co_regulation_hint") or "resonate"),
            "last_ts_ms": int(state.get("last_ts_ms") or now_ms()),
        }

    async def _on_emotion_inference_updated(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        await self._absorb_emotion_event(
            payload=payload,
            event=event,
            source="emotion_inference_updated",
            fallback_intensity=0.45,
        )

    async def _on_interaction_recorded(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        await self._absorb_emotion_event(
            payload=payload,
            event=event,
            source="interaction_recorded",
            fallback_intensity=0.5,
        )

    async def _on_emotion_state_changed(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        await self._absorb_emotion_event(
            payload=payload,
            event=event,
            source="emotion_state_changed",
            fallback_intensity=0.4,
        )

    async def _absorb_emotion_event(
        self,
        *,
        payload: dict[str, Any],
        event: Any,
        source: str,
        fallback_intensity: float,
    ) -> None:
        emotion_tag = str(payload.get("emotion_tag") or "").strip().upper()
        if not emotion_tag:
            return
        emotion_intensity = float(
            payload.get("emotion_intensity")
            or payload.get("intensity")
            or fallback_intensity
        )
        user_id = str(payload.get("user_id") or payload.get("person_id") or "user_1").strip() or "user_1"
        session_id = str(payload.get("session_id") or payload.get("conversation_id") or "").strip()
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self.apply_resonance(
            user_id=user_id,
            session_id=session_id,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
            source=source,
            ts_ms=ts_ms,
        )


_MIRROR_NEURON_AGENT: MirrorNeuronAgent | None = None


def get_mirror_neuron_agent() -> MirrorNeuronAgent:
    global _MIRROR_NEURON_AGENT
    if _MIRROR_NEURON_AGENT is None:
        _MIRROR_NEURON_AGENT = MirrorNeuronAgent()
    return _MIRROR_NEURON_AGENT


def set_mirror_neuron_agent(agent: MirrorNeuronAgent | None) -> None:
    global _MIRROR_NEURON_AGENT
    _MIRROR_NEURON_AGENT = agent


__all__ = ["MirrorNeuronAgent", "get_mirror_neuron_agent", "set_mirror_neuron_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: E402, F401
from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
from host_brain.tribe.events import emit_tribe_event  # noqa: E402, F401
