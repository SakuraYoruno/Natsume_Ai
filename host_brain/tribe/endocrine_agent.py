from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.endocrine import derive_endocrine_state
from host_brain.tribe.base_agent import TribeAgentDescriptor


@dataclass(slots=True)
class EndocrineAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="endocrine_agent",
            family="endocrine",
            capabilities=("hormone_baseline", "gate_state", "homeostasis"),
        )
    )
    _last_state: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    # T-28：bond 事件产生的激素增量缓冲（在下次 resolve_endocrine_state 时消费）
    _bond_cortisol_delta: float = field(default=0.0, init=False, repr=False)
    _bond_oxytocin_delta: float = field(default=0.0, init=False, repr=False)
    _bond_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def resolve_endocrine_state(
        self,
        *,
        hard_state: Mapping[str, Any] | Any,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        state = derive_endocrine_state(
            hard_state=hard_state,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )
        payload = dict(state.as_dict())

        # ── T-17：接入 PainRewardAgent 压力信号 ──────────────────────────
        # 将痛觉/奖励系统的净压力变化量叠加到 cortisol_level，
        # 使痛觉事件（被拒绝/被攻击/话题受挫等）能够影响内分泌状态。
        # 叠加量上限 ±0.15，避免单次事件过度影响皮质醇。
        try:

            _pain_agent = get_pain_reward_agent()
            _stress_signal = _pain_agent.get_stress_signal()
            _net_stress_delta = float(_stress_signal.get("net_stress_delta") or 0.0)
            if abs(_net_stress_delta) > 0.001:
                _clamped_delta = max(-0.15, min(0.15, _net_stress_delta))
                _cur_cortisol = _safe_float(payload.get("cortisol_level"), 0.0)
                _new_cortisol = max(0.0, min(1.0, _cur_cortisol + _clamped_delta))
                payload["cortisol_level"] = round(_new_cortisol, 4)
                payload["_pain_stress_delta"] = round(_clamped_delta, 4)
        except Exception:
            pass

        # ── T-28：消费 bond 事件产生的激素增量缓冲 ───────────────────────
        # BOND_PROGRESS → 催产素增加（oxytocin_boost）+ 皮质醇轻微降低
        # BOND_REGRESS  → 皮质醇增加（cortisol_boost）+ 催产素轻微降低
        try:
            with self._bond_lock:
                _cortisol_delta = self._bond_cortisol_delta
                _oxytocin_delta = self._bond_oxytocin_delta
                # 消费后清零
                self._bond_cortisol_delta = 0.0
                self._bond_oxytocin_delta = 0.0

            if abs(_cortisol_delta) > 0.001:
                _cur_cortisol = _safe_float(payload.get("cortisol_level"), 0.0)
                _new_cortisol = max(0.0, min(1.0, _cur_cortisol + _cortisol_delta))
                payload["cortisol_level"] = round(_new_cortisol, 4)
                payload["_bond_cortisol_delta"] = round(_cortisol_delta, 4)

            if abs(_oxytocin_delta) > 0.001:
                # oxytocin_level 可能不在 payload 中，先读取再叠加
                _cur_oxytocin = _safe_float(payload.get("oxytocin_level"), 0.5)
                _new_oxytocin = max(0.0, min(1.0, _cur_oxytocin + _oxytocin_delta))
                payload["oxytocin_level"] = round(_new_oxytocin, 4)
                payload["_bond_oxytocin_delta"] = round(_oxytocin_delta, 4)
        except Exception:
            pass

        self._last_state = dict(payload)

        # T-246：高皮质醇时联动 LimbicAgent 触发压力性情绪偏移（焦虑/低唤醒）
        _cortisol = float(payload.get("cortisol_level") or 0.0)
        if _cortisol >= 0.65:
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _stress_intensity = max(0.01, (_cortisol - 0.65) * 1.43)
                    _limbic.drive_emotion_metabolism(
                        valence=round(-_stress_intensity * 0.45, 4),
                        arousal=round(_stress_intensity * 0.30, 4),
                        dominance=-0.08,
                        blend_weight=0.15,
                        event_kind=f"high_cortisol:{round(_cortisol, 3)}",
                        source="endocrine_agent",
                    )
            except Exception:
                pass
            # T-320：高皮质醇时联动 InteroceptionAgent 注入内分泌压力（cortisol 升高消耗身体预算）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "inject_external_stress"):
                    _endo_stress = round(min(0.40, (_cortisol - 0.65) * 1.33), 4)
                    _intero.inject_external_stress(
                        source=f"high_cortisol:{round(_cortisol, 3)}",
                        intensity=_endo_stress,
                    )
            except Exception:
                pass
            # T-360：极高皮质醇（>=0.80）时联动 PainRewardAgent 记录持续压力痛苦
            # 持续高 cortisol 是深层躯体痛苦（慢性压力的内在体验）
            if _cortisol >= 0.80:
                try:
                    _pr = get_pain_reward_agent()
                    if hasattr(_pr, "record_pain"):
                        _cortisol_pain = round(min(0.40, (_cortisol - 0.80) * 2.00), 4)
                        _pr.record_pain(
                            source=f"endocrine_high_cortisol:{round(_cortisol, 3)}",
                            intensity=_cortisol_pain,
                            metadata={
                                "cortisol_level": round(_cortisol, 4),
                            },
                        )
                except Exception:
                    pass

        return payload

    def record_stress_event(
        self,
        *,
        source: str,
        intensity: float = 0.20,
    ) -> None:
        """T-238：外部族注入压力事件，叠加到 cortisol 缓冲区。

        由 ProprioceptionAgent（高打断）、LanguageSystemAgent（高情绪压力）等外部族调用，
        将压力信号写入 _bond_cortisol_delta 缓冲，下次 resolve_endocrine_state() 时消费。

        Args:
            source: 压力来源标识（用于日志追踪）
            intensity: 压力强度 0.0 ~ 1.0，叠加到 cortisol 的增量上限 0.10
        """
        clamped = max(0.0, min(1.0, float(intensity)))
        cortisol_boost = round(min(0.10, clamped * 0.12), 4)
        with self._bond_lock:
            self._bond_cortisol_delta += cortisol_boost
        import logging  # noqa: PLC0415
        logging.getLogger("Natsume.tribe.endocrine_agent").debug(
            "EndocrineAgent.record_stress_event: source=%s intensity=%.3f cortisol+%.4f",
            source, clamped, cortisol_boost,
        )

    def resolve_vital_signs(self, *, endocrine_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        state = dict(endocrine_state or self._last_state or {})
        cortisol = _safe_float(state.get("cortisol_level"), 0.0)
        energy = _safe_float(state.get("energy_reserve"), 0.0)
        gate_state = str(state.get("gate_state") or "open").strip().lower() or "open"
        return {
            "gate_state": gate_state,
            "stress_index": round(cortisol, 4),
            "energy_reserve": round(energy, 4),
            "homeostasis_ok": bool(cortisol < 0.62 and energy > 0.22 and gate_state == "open"),
        }

    # ── 激素状态身体感受提示（A 级升级）─────────────────────────────────────

    def generate_hormonal_state_hint(
        self,
        *,
        cortisol: float = 0.0,
        oxytocin: float = 0.0,
        emotion_tag: str = "NEUTRAL",
    ) -> str | None:
        """将激素状态转化为自然语言身体感受提示，注入 Prompt Phase XIII 情绪层。

        触发条件：cortisol > 0.6 或 oxytocin > 0.6（激素状态显著）。
        调用：Flash 小模型。
        开关：ENDOCRINE_HORMONAL_HINT_ENABLED（默认 false）。

        Returns:
            身体感受描述字符串，或 None（未触发/失败）。
        """
        _significant = float(cortisol or 0.0) > 0.6 or float(oxytocin or 0.0) > 0.6
        if not _significant:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.endocrine_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.tribe.model_invocation import complete_tribe_json  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("endocrine_agent.hormonal_state_hint") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("endocrine_agent.hormonal_state_hint 模板未配置")

            _user_prompt = _user_tpl.format(
                cortisol=round(float(cortisol), 3),
                oxytocin=round(float(oxytocin), 3),
                emotion_tag=str(emotion_tag or "NEUTRAL").upper(),
            )
            _parsed = complete_tribe_json(
                semantic_id="endocrine_hormonal_hint",
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
            )
            _hint = str(_parsed.get("hormonal_hint") or "").strip()[:40]
            if _hint:
                _log.debug("EndocrineAgent.generate_hormonal_state_hint: %s", _hint)
                try:
                    self.state_registry.set_state(
                        "endocrine_agent",
                        {"last_hormonal_hint": _hint},
                        writer="endocrine_agent",
                    )
                except Exception:
                    pass
            return _hint or None
        except Exception as exc:
            _log.debug("EndocrineAgent.generate_hormonal_state_hint 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        last_state = dict(self._last_state or {})
        return {
            "state": last_state,
            "vitals": self.resolve_vital_signs(endocrine_state=last_state),
        }

    # ── T-28：TribeEventBus 订阅注册 ─────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        T-28：订阅 BOND_PROGRESS / BOND_REGRESS 事件，
        关系变化影响催产素（oxytocin）和皮质醇（cortisol）。

        订阅事件：
          - BOND_PROGRESS：关系进展 → 催产素增加 + 皮质醇轻微降低
          - BOND_REGRESS：关系退步 → 皮质醇增加 + 催产素轻微降低
        FIX-4.1：订阅 ENDOCRINE_STATE_UPDATED，将激素状态同步到 GovernorRulesCache，
        使 Governor 在下次裁决时感知当前能量/压力水平。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BOND_PROGRESS", self._on_bond_progress)
            bus.subscribe("BOND_REGRESS", self._on_bond_regress)
            # T-57：订阅孤独感信号，孤独感触发 cortisol 轻微上升
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
            # T-69：订阅任务成功事件，任务成功触发 cortisol 轻微下降
            bus.subscribe("TASK_SUCCESS", self._on_task_success_endocrine)
            # T-86：订阅免疫拦截事件，拦截时 cortisol 轻微上升
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block_endocrine)
            # FIX-4.1：订阅内分泌状态更新事件，同步到 GovernorRulesCache（L3 → L2 通道）
            bus.subscribe("ENDOCRINE_STATE_UPDATED", self._on_endocrine_state_updated)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.endocrine_agent").warning(
                "EndocrineAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_endocrine_state_updated(self, event: Any) -> None:
        """FIX-4.1：处理 ENDOCRINE_STATE_UPDATED 事件，将激素状态同步到 GovernorRulesCache。

        当 EndocrineAgent 完成一次内分泌状态计算后，通过 GovernorRulesCache 通知 Governor，
        使其在下次裁决时感知当前能量/压力水平（如高压力时收紧策略）。
        这是 L3（认知层）→ L2（基础设施层）的单向通道。

        事件 payload 格式：
            energy_level: float  - 能量水平 0.0 ~ 1.0（来自 energy_reserve）
            stress_level: float  - 压力水平 0.0 ~ 1.0（来自 cortisol_level）
        若 payload 为空，则从 _last_state 中读取当前状态。
        """
        try:
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.endocrine_agent")
            payload = dict(getattr(event, "payload", None) or {})

            # 优先从事件 payload 读取，降级到 _last_state
            if "energy_level" in payload or "stress_level" in payload:
                energy_level = max(0.0, min(1.0, float(payload.get("energy_level") or 1.0)))
                stress_level = max(0.0, min(1.0, float(payload.get("stress_level") or 0.0)))
            else:
                # 从 _last_state 中读取（cortisol_level → stress，energy_reserve → energy）
                last = dict(self._last_state or {})
                energy_level = max(0.0, min(1.0, float(last.get("energy_reserve") or 1.0)))
                stress_level = max(0.0, min(1.0, float(last.get("cortisol_level") or 0.0)))

            # 同步到 GovernorRulesCache（L3 → L2 单向通道）
            from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
            cache = get_governor_rules_cache()
            cache.update_endocrine_state(
                energy_level=energy_level,
                stress_level=stress_level,
            )
            _log.debug(
                "EndocrineAgent → GovernorRulesCache: energy=%.3f stress=%.3f",
                energy_level, stress_level,
            )
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.endocrine_agent").warning(
                "EndocrineAgent._on_endocrine_state_updated 异常（降级）: %s", exc
            )

    async def _on_bond_progress(self, event: Any) -> None:
        """处理 BOND_PROGRESS 事件：关系进展 → 催产素增加 + 皮质醇轻微降低。

        催产素增量：intensity * 0.15（上限 0.12）
        皮质醇减量：intensity * 0.05（上限 0.05）
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            reason = str(payload.get("reason") or "bond_progress")
            # 催产素增加（正值）
            oxytocin_boost = round(min(0.12, intensity * 0.15), 4)
            # 皮质醇降低（负值）
            cortisol_relief = round(-min(0.05, intensity * 0.05), 4)
            with self._bond_lock:
                self._bond_oxytocin_delta += oxytocin_boost
                self._bond_cortisol_delta += cortisol_relief
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent BOND_PROGRESS: oxytocin+%.3f cortisol%.3f reason=%s",
                oxytocin_boost, cortisol_relief, reason,
            )
        except Exception:
            pass

    async def _on_bond_regress(self, event: Any) -> None:
        """处理 BOND_REGRESS 事件：关系退步 → 皮质醇增加 + 催产素轻微降低。

        皮质醇增量：intensity * 0.10（上限 0.08）
        催产素减量：intensity * 0.08（上限 0.06）
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.4)))
            reason = str(payload.get("reason") or "bond_regress")
            # 皮质醇增加（正值）
            cortisol_boost = round(min(0.08, intensity * 0.10), 4)
            # 催产素降低（负值）
            oxytocin_drop = round(-min(0.06, intensity * 0.08), 4)
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_boost
                self._bond_oxytocin_delta += oxytocin_drop
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent BOND_REGRESS: cortisol+%.3f oxytocin%.3f reason=%s",
                cortisol_boost, oxytocin_drop, reason,
            )
        except Exception:
            pass


    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感触发 cortisol 轻微上升。

        T-57：孤独感 > 0.5 时将 cortisol 增量写入缓冲，
        在下次 resolve_endocrine_state() 时消费，模拟孤独感带来的轻微压力反应。
        cortisol 增量上限 0.06，避免单次事件过度影响内分泌状态。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            if loneliness <= 0.5:
                return
            cortisol_boost = round(min(0.06, (loneliness - 0.5) * 0.12), 4)
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_boost
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent LONELINESS_SIGNAL: cortisol+%.3f loneliness=%.2f",
                cortisol_boost, loneliness,
            )
        except Exception:
            pass

    async def _on_task_success_endocrine(self, event: Any) -> None:
        """处理 TASK_SUCCESS 事件：任务成功触发 cortisol 轻微下降。

        T-69：任务成功时将 cortisol 减量写入缓冲，
        在下次 resolve_endocrine_state() 时消费，模拟成就感带来的压力缓解。
        cortisol 减量上限 0.05，避免单次事件过度影响内分泌状态。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.35)))
            cortisol_relief = round(-min(0.05, intensity * 0.07), 4)  # 负值表示减少
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_relief
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent TASK_SUCCESS: cortisol%.3f intensity=%.2f",
                cortisol_relief, intensity,
            )
        except Exception:
            pass

    async def _on_immune_block_endocrine(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：免疫拦截时 cortisol 轻微上升。

        T-86：被拦截意味着输出受到抑制，模拟轻微的挫败感/压力反应。
        cortisol 增量上限 0.04，避免单次拦截过度影响内分泌状态。
        仅在 risk_level 为 medium/high/critical 时触发。
        """
        try:
            import logging  # noqa: PLC0415
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "low").strip().lower()
            # low 级别不触发压力反应
            if risk_level == "low":
                return
            # medium → +0.02，high → +0.03，critical → +0.04
            risk_boost_map = {"medium": 0.02, "high": 0.03, "critical": 0.04}
            cortisol_boost = risk_boost_map.get(risk_level, 0.02)
            with self._bond_lock:
                self._bond_cortisol_delta += cortisol_boost
            logging.getLogger("Natsume.tribe.endocrine_agent").debug(
                "EndocrineAgent IMMUNE_BLOCK: cortisol+%.3f risk_level=%s",
                cortisol_boost, risk_level,
            )
        except Exception:
            pass


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


_ENDOCRINE_AGENT: EndocrineAgent | None = None


def get_endocrine_agent() -> EndocrineAgent:
    global _ENDOCRINE_AGENT
    if _ENDOCRINE_AGENT is None:
        _ENDOCRINE_AGENT = EndocrineAgent()
    return _ENDOCRINE_AGENT


def set_endocrine_agent(agent: EndocrineAgent | None) -> None:
    global _ENDOCRINE_AGENT
    _ENDOCRINE_AGENT = agent


__all__ = [
    "EndocrineAgent",
    "get_endocrine_agent",
    "set_endocrine_agent",
]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
