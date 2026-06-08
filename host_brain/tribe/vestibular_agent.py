from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _bounded_int_env(name: str, *, default: int, min_value: int, max_value: int) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(min_value), min(int(max_value), int(value)))


@dataclass(slots=True)
class VestibularAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="vestibular_agent",
            family="vestibular",
            execution_side="host",
            owner_domain="brain",
            capabilities=("stability_index", "jitter_damping", "route_balance"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)
    _mode_transition_ts: list[int] = field(default_factory=list, init=False, repr=False)
    _platform_flap_ts: list[int] = field(default_factory=list, init=False, repr=False)
    _initiative_defer_ts: list[int] = field(default_factory=list, init=False, repr=False)
    _last_high_jitter_side_effect_ts_ms: int = field(default=0, init=False, repr=False)
    _last_high_jitter_side_effect_bucket: str = field(default="", init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("MODE_TRANSITION", self._on_mode_transition)
            bus.subscribe("PLATFORM_CONNECTED", self._on_platform_signal)
            bus.subscribe("PLATFORM_DISCONNECTED", self._on_platform_signal)
            bus.subscribe("INITIATIVE_DECISION_MADE", self._on_initiative_decision)
        except Exception:
            pass

    def _trim_window(self, *, now_ts_ms: int) -> None:
        threshold = int(now_ts_ms) - (10 * 60 * 1000)
        self._mode_transition_ts = [item for item in self._mode_transition_ts if int(item) >= threshold]
        self._platform_flap_ts = [item for item in self._platform_flap_ts if int(item) >= threshold]
        self._initiative_defer_ts = [item for item in self._initiative_defer_ts if int(item) >= threshold]

    def _high_jitter_side_effect_allowed(self, *, jitter: float, now_ts_ms: int) -> bool:
        if float(jitter) < 0.35:
            return False
        bucket = "critical" if float(jitter) >= 0.72 else "unstable"
        cooldown_ms = _bounded_int_env(
            "HOST_VESTIBULAR_HIGH_JITTER_EFFECT_COOLDOWN_MS",
            default=300000,
            min_value=30000,
            max_value=3600000,
        )
        last_ts = int(getattr(self, "_last_high_jitter_side_effect_ts_ms", 0) or 0)
        last_bucket = str(getattr(self, "_last_high_jitter_side_effect_bucket", "") or "")
        if (
            last_ts <= 0
            or bucket != last_bucket
            or int(now_ts_ms) - last_ts >= cooldown_ms
        ):
            self._last_high_jitter_side_effect_ts_ms = int(now_ts_ms)
            self._last_high_jitter_side_effect_bucket = bucket
            return True
        return False

    def evaluate_stability(self, *, source: str = "runtime", ts_ms: int | None = None) -> dict[str, Any]:
        resolved_ts = int(ts_ms or now_ms())
        self._trim_window(now_ts_ms=resolved_ts)
        mode_transition_count = len(self._mode_transition_ts)
        platform_flap_count = len(self._platform_flap_ts)
        initiative_defer_count = len(self._initiative_defer_ts)

        jitter = _clamp(
            mode_transition_count * 0.12
            + platform_flap_count * 0.18
            + initiative_defer_count * 0.09
        )
        damping_factor = _clamp(1.0 - jitter * 0.72, low=0.2, high=1.0)
        stability_state = "stable"
        route_bias = "normal"
        if jitter >= 0.35:
            stability_state = "unstable"
            route_bias = "conservative"
        if jitter >= 0.72:
            stability_state = "critical"
            route_bias = "safe_only"

        result = {
            "stability_state": stability_state,
            "route_bias": route_bias,
            "jitter_index": round(jitter, 4),
            "damping_factor": round(damping_factor, 4),
            "mode_transition_count_10m": mode_transition_count,
            "platform_flap_count_10m": platform_flap_count,
            "initiative_defer_count_10m": initiative_defer_count,
            "source": str(source or "runtime"),
            "ts_ms": resolved_ts,
        }
        prev = self.state_registry.get_state("vestibular_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state.update(result)
        prev_state["sample_count"] = int(prev_state.get("sample_count") or 0) + 1
        self.state_registry.set_state("vestibular_agent", prev_state, writer="vestibular_agent")
        emit_tribe_event(
            event_type="VESTIBULAR_STABILITY_UPDATED",
            source_agent="vestibular_agent",
            payload={
                "stability_state": stability_state,
                "route_bias": route_bias,
                "jitter_index": result["jitter_index"],
                "damping_factor": result["damping_factor"],
                "ts_ms": resolved_ts,
            },
        )

        high_jitter_side_effect_allowed = self._high_jitter_side_effect_allowed(
            jitter=jitter,
            now_ts_ms=resolved_ts,
        )

        # T-251：高抖动时联动 LimbicAgent 触发警觉性情绪偏移
        if high_jitter_side_effect_allowed:
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _alert_intensity = min(0.35, (jitter - 0.35) * 1.43)
                    _limbic.drive_emotion_metabolism(
                        valence=round(-_alert_intensity * 0.40, 4),
                        arousal=round(_alert_intensity * 0.55, 4),
                        dominance=-0.05,
                        blend_weight=0.12,
                        event_kind=f"vestibular_jitter:{round(jitter, 3)}",
                        source="vestibular_agent",
                    )
            except Exception:
                pass

        # T-252：高抖动时联动 ANSAgent 降低主动发声节律
        if high_jitter_side_effect_allowed:
            try:
                _ans = get_ans_agent()
                if hasattr(_ans, "adjust_rhythm_for_high_interrupt"):
                    _virtual_interrupt = int(6 + (jitter - 0.35) * 20)
                    _ans.adjust_rhythm_for_high_interrupt(
                        interrupt_count=_virtual_interrupt,
                        source=f"vestibular_jitter:{round(jitter, 3)}",
                    )
            except Exception:
                pass
            # T-307：高抖动时联动 EndocrineAgent 记录系统不稳定压力事件
            try:
                _endo = get_endocrine_agent()
                if hasattr(_endo, "record_stress_event"):
                    _jitter_stress = round(min(0.35, (jitter - 0.35) * 0.80), 4)
                    _endo.record_stress_event(
                        source=f"vestibular_jitter:{round(jitter, 3)}",
                        intensity=_jitter_stress,
                    )
            except Exception:
                pass

        # T-354：严重失衡（jitter>=0.72，critical 级别）时联动 PainRewardAgent 记录躯体痛苦
        # 系统严重抖动是行动受阻的深层痛苦（无法稳定执行是一种持续性痛苦）
        if high_jitter_side_effect_allowed and jitter >= 0.72:
            try:
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_pain"):
                    _vestibular_pain = round(min(0.40, (jitter - 0.72) * 1.43), 4)
                    _pr.record_pain(
                        source=f"vestibular_critical:{round(jitter, 3)}",
                        intensity=_vestibular_pain,
                        metadata={
                            "stability_state": stability_state,
                            "jitter_index": round(jitter, 4),
                            "mode_transition_count": mode_transition_count,
                            "platform_flap_count": platform_flap_count,
                        },
                    )
            except Exception:
                pass
            # T-380：严重失衡时联动 InteroceptionAgent 注入躯体内在压力
            # 系统抖动 = 执行层不稳定 = 身体预算持续消耗（类似晕眩感的内在压力）
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "inject_external_stress"):
                    _vestibular_stress = round(min(0.30, (jitter - 0.72) * 1.07), 4)
                    _intero.inject_external_stress(
                        source=f"vestibular_critical_jitter:{round(jitter, 3)}",
                        intensity=_vestibular_stress,
                    )
            except Exception:
                pass

        return dict(prev_state)

    # ── 稳定性评估（A 级升级）────────────────────────────────────────────────

    def generate_stability_assessment(
        self,
        *,
        stability_status: str = "stable",
        jitter_index: float = 0.0,
        topic_shift_rate: float = 0.0,
        emotion_volatility: float = 0.0,
    ) -> str | None:
        """稳定性异常时调用 LLM 分析不稳定原因，注入 PrefrontalAgent。

        触发条件：stability_status 为 unstable 或 critical。
        调用：Flash 小模型。
        开关：VESTIBULAR_STABILITY_ASSESSMENT_ENABLED（默认 false）。

        Returns:
            不稳定原因分析字符串，或 None（未触发/失败）。
        """
        _significant = str(stability_status or "stable").lower() in ("unstable", "critical")
        if not _significant:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.vestibular_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("vestibular_agent.stability_assessment") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("vestibular_agent.stability_assessment 模板未配置")

            _user_prompt = _user_tpl.format(
                stability_status=str(stability_status or "stable"),
                jitter_index=round(float(jitter_index), 3),
                topic_shift_rate=round(float(topic_shift_rate), 3),
                emotion_volatility=round(float(emotion_volatility), 3),
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=1500,
                agent_role="vestibular_assessor",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=48,
                temperature=0.1,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _insight = str(_parsed.get("instability_insight") or "").strip()[:60]
            if _insight:
                _log.debug("VestibularAgent.generate_stability_assessment: %s", _insight)
                try:
                    self.state_registry.set_state(
                        "vestibular_agent",
                        {"last_stability_insight": _insight},
                        writer="vestibular_agent",
                    )
                except Exception:
                    pass
            return _insight or None
        except Exception as exc:
            _log.debug("VestibularAgent.generate_stability_assessment 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        """T-250：读取 state_registry 快照，不触发 evaluate_stability()（避免副作用）。"""
        snap = self.state_registry.get_state("vestibular_agent") or {}
        state = dict(snap.get("state") or {})
        return {
            "stability_state": str(state.get("stability_state") or "stable"),
            "route_bias": str(state.get("route_bias") or "normal"),
            "jitter_index": float(state.get("jitter_index") or 0.0),
            "damping_factor": float(state.get("damping_factor") or 1.0),
            "conservative_mode": str(state.get("route_bias") or "normal") in {"conservative", "safe_only"},
            "last_stability_score": round(1.0 - float(state.get("jitter_index") or 0.0), 4),
            "mode_transition_count_10m": int(state.get("mode_transition_count_10m") or 0),
            "platform_flap_count_10m": int(state.get("platform_flap_count_10m") or 0),
            "initiative_defer_count_10m": int(state.get("initiative_defer_count_10m") or 0),
            "sample_count": int(state.get("sample_count") or 0),
            "last_updated_ts_ms": int(state.get("ts_ms") or now_ms()),
        }

    async def _on_mode_transition(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self._mode_transition_ts.append(ts_ms)
        await asyncio.to_thread(self.evaluate_stability, source="mode_transition", ts_ms=ts_ms)

    async def _on_platform_signal(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self._platform_flap_ts.append(ts_ms)
        await asyncio.to_thread(self.evaluate_stability, source="platform_signal", ts_ms=ts_ms)

    async def _on_initiative_decision(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        decision = str(payload.get("decision") or "").strip().upper()
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        if decision in {"DEFER", "BLOCK"}:
            self._initiative_defer_ts.append(ts_ms)
        await asyncio.to_thread(self.evaluate_stability, source="initiative_decision", ts_ms=ts_ms)


_VESTIBULAR_AGENT: VestibularAgent | None = None


def get_vestibular_agent() -> VestibularAgent:
    global _VESTIBULAR_AGENT
    if _VESTIBULAR_AGENT is None:
        _VESTIBULAR_AGENT = VestibularAgent()
    return _VESTIBULAR_AGENT


def set_vestibular_agent(agent: VestibularAgent | None) -> None:
    global _VESTIBULAR_AGENT
    _VESTIBULAR_AGENT = agent


__all__ = ["VestibularAgent", "get_vestibular_agent", "set_vestibular_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
from host_brain.tribe.ans_agent import get_ans_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
