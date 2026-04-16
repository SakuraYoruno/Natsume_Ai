from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class CerebellumAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="cerebellum_agent",
            family="cerebellum",
            execution_side="host",
            owner_domain="brain",
            capabilities=("error_correction", "latency_compensation", "retry_strategy"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("MOTOR_ACTION_DISPATCHED", self._on_motor_action_dispatched)
            bus.subscribe("PLATFORM_DISCONNECTED", self._on_platform_disconnected)
        except Exception:
            pass

    def evaluate_correction(
        self,
        *,
        execution_feedback: Mapping[str, Any],
        source: str = "runtime",
        ts_ms: int | None = None,
    ) -> dict[str, Any]:
        feedback = dict(execution_feedback or {})
        resolved_ts = int(ts_ms or now_ms())
        accepted = bool(feedback.get("accepted", feedback.get("ok", False)))
        dispatched = bool(feedback.get("dispatched", feedback.get("ok", False)))
        latency_ms = max(0, int(feedback.get("latency_ms") or 0))
        reason = str(feedback.get("reason") or "").strip().upper()

        strategy = "NO_ADJUST"
        urgency = 0.15
        backoff_ms = 0
        if not accepted or not dispatched:
            strategy = "RETRY_ONCE"
            urgency = 0.74
            backoff_ms = 120
        elif latency_ms > 1200:
            strategy = "DEGRADE_OUTPUT"
            urgency = 0.52
            backoff_ms = 80
        elif latency_ms > 700:
            strategy = "LATENCY_COMPENSATE"
            urgency = 0.38
            backoff_ms = 40
        if reason in {"PUSHER_UNAVAILABLE", "HTTP_REJECTED", "PUSH_FAILED"}:
            strategy = "SAFE_FALLBACK"
            urgency = 0.81
            backoff_ms = 180

        decision = {
            "plan_id": str(feedback.get("plan_id") or "").strip() or None,
            "strategy": strategy,
            "urgency": round(_clamp(float(urgency)), 4),
            "backoff_ms": int(backoff_ms),
            "accepted": accepted,
            "dispatched": dispatched,
            "latency_ms": latency_ms,
            "reason": reason or None,
            "source": str(source or "runtime"),
            "ts_ms": resolved_ts,
        }
        prev = self.state_registry.get_state("cerebellum_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state["last_correction"] = decision
        prev_state["correction_count"] = int(prev_state.get("correction_count") or 0) + 1
        self.state_registry.set_state("cerebellum_agent", prev_state, writer="cerebellum_agent")
        emit_tribe_event(
            event_type="CEREBELLUM_CORRECTION_DECIDED",
            source_agent="cerebellum_agent",
            payload=decision,
        )

        # T-288：高纠偏策略时联动 EndocrineAgent 记录压力事件（执行失败是内在压力源）
        if strategy in {"RETRY_ONCE", "SAFE_FALLBACK"}:
            try:
                _endo = get_endocrine_agent()
                if hasattr(_endo, "record_stress_event"):
                    _stress_intensity = 0.18 if strategy == "RETRY_ONCE" else 0.28
                    _endo.record_stress_event(
                        source=f"cerebellum_{strategy.lower()}:{str(reason or 'unknown')[:24]}",
                        intensity=_stress_intensity,
                    )
            except Exception:
                pass

        # T-335：高纠偏策略时联动 PainRewardAgent 记录认知痛苦（持续纠偏是认知负担）
        if strategy in {"RETRY_ONCE", "SAFE_FALLBACK"}:
            try:
                from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_pain"):
                    _pain_intensity = 0.20 if strategy == "RETRY_ONCE" else 0.32
                    _pr.record_pain(
                        source=f"cerebellum_{strategy.lower()}:{str(reason or 'unknown')[:24]}",
                        intensity=_pain_intensity,
                        metadata={
                            "strategy": strategy,
                            "urgency": round(urgency, 4),
                            "reason": str(reason or ""),
                            "plan_id": str(decision.get("plan_id") or ""),
                        },
                    )
            except Exception:
                pass

        # T-341：低纠偏（NO_ADJUST）时联动 PainRewardAgent 记录技能奖励（执行顺畅是正向强化）
        if strategy == "NO_ADJUST":
            try:
                from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_reward"):
                    _pr.record_reward(
                        source="cerebellum_no_adjust",
                        intensity=0.18,
                        metadata={
                            "strategy": "NO_ADJUST",
                            "latency_ms": latency_ms,
                            "plan_id": str(decision.get("plan_id") or ""),
                        },
                    )
            except Exception:
                pass
            # T-375：纠偏成功（NO_ADJUST）时联动 InteroceptionAgent 降低 recovery_urgency
            # 执行顺畅 = 认知资源充足，应主动释放身体预算压力
            try:
                from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                _intero = get_interoception_agent()
                if hasattr(_intero, "reduce_recovery_urgency"):
                    _intero.reduce_recovery_urgency(
                        source="cerebellum_no_adjust_smooth",
                        relief=0.15,
                    )
            except Exception:
                pass
            # T-379：纠偏成功（NO_ADJUST）时联动 DefaultModeAgent.tick_idle_cycle()
            # 执行顺畅是"我能做到"的自我能力叙事，应强化连续性
            try:
                from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
                _dma = get_default_mode_agent()
                if hasattr(_dma, "tick_idle_cycle"):
                    _dma.tick_idle_cycle(
                        source="cerebellum_no_adjust_smooth",
                        person_id="user_1",
                    )
            except Exception:
                pass

        # T-342：持续高纠偏时联动 InteroceptionAgent 注入躯体压力（持续纠偏消耗身体预算）
        if strategy in {"RETRY_ONCE", "SAFE_FALLBACK"}:
            try:
                _prev_snap = self.state_registry.get_state("cerebellum_agent") or {}
                _prev_st = dict(_prev_snap.get("state") or {})
                _correction_count = int(_prev_st.get("correction_count") or 0)
                # 连续纠偏次数 >= 3 时才注入躯体压力（避免单次纠偏过度触发）
                if _correction_count >= 3:
                    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "inject_external_stress"):
                        _somatic_stress = round(min(0.35, urgency * 0.38), 4)
                        _intero.inject_external_stress(
                            source=f"cerebellum_persistent_correction:count={_correction_count}",
                            intensity=_somatic_stress,
                        )
            except Exception:
                pass

        return decision

    # ── 纠偏叙事与失败模式分析（A 级升级）───────────────────────────────────

    def generate_correction_narrative(
        self,
        *,
        strategy: str = "RETRY_ONCE",
        target_id: str = "",
        error_text: str = "",
        urgency: float = 0.5,
    ) -> str | None:
        """执行纠偏时调用 LLM 生成面向用户的自然语言解释。

        触发条件：evaluate_correction() 返回 RETRY/DEGRADE/SAFE_FALLBACK。
        调用：Flash 小模型。
        开关：CEREBELLUM_CORRECTION_NARRATIVE_ENABLED（默认 false）。
        输出注入：ChatAgent 的 extra_context_prompt。

        Returns:
            纠偏叙事字符串，或 None（未触发/失败）。
        """
        if str(strategy or "").upper() == "NO_ADJUST":
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.cerebellum_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("cerebellum_agent.correction_narrative") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("cerebellum_agent.correction_narrative 模板未配置")

            _user_prompt = _user_tpl.format(
                strategy=str(strategy or "RETRY_ONCE"),
                target_id=str(target_id or "")[:50],
                error_text=str(error_text or "")[:100],
                urgency=round(float(urgency), 3),
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=2000,
                agent_role="cerebellum_narrator",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=64,
                temperature=0.2,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _narrative = str(_parsed.get("correction_narrative") or "").strip()[:80]
            if _narrative:
                _log.debug("CerebellumAgent.generate_correction_narrative: %s", _narrative)
                try:
                    self.state_registry.set_state(
                        "cerebellum_agent",
                        {"last_correction_narrative": _narrative},
                        writer="cerebellum_agent",
                    )
                except Exception:
                    pass
            return _narrative or None
        except Exception as exc:
            _log.debug("CerebellumAgent.generate_correction_narrative 失败（降级）: %s", exc)
            return None

    def analyze_failure_pattern(
        self,
        *,
        consecutive_failures: int = 0,
        failure_history: list[dict] | None = None,
        target_id: str = "",
    ) -> str | None:
        """连续失败时调用 LLM 分析失败模式，产出 failure_insight 写入语义记忆。

        触发条件：consecutive_failures > 2。
        调用：Flash 小模型。
        开关：CEREBELLUM_CORRECTION_NARRATIVE_ENABLED（复用同一开关）。

        Returns:
            失败模式洞察字符串，或 None（未触发/失败）。
        """
        if int(consecutive_failures or 0) <= 2:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.cerebellum_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("cerebellum_agent.failure_pattern") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("cerebellum_agent.failure_pattern 模板未配置")

            _history_text = ""
            for _item in (failure_history or [])[-5:]:
                _history_text += f"- {_item.get('strategy','?')} / {_item.get('error','?')[:40]}\n"

            _user_prompt = _user_tpl.format(
                consecutive_failures=int(consecutive_failures),
                target_id=str(target_id or "")[:50],
                failure_history=_history_text.strip()[:200],
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=3000,
                agent_role="cerebellum_analyst",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=96,
                temperature=0.1,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _insight = str(_parsed.get("failure_insight") or "").strip()[:120]
            if _insight:
                _log.info("CerebellumAgent.analyze_failure_pattern: %s", _insight[:60])
                # 写入 HippocampusAgent 语义记忆
                try:
                    _hippo = get_hippocampus_agent()
                    if hasattr(_hippo, "write_semantic_memory"):
                        _hippo.write_semantic_memory(
                            content=_insight,
                            source="cerebellum_failure_analysis",
                            tags=["failure_pattern", str(target_id or "")[:20]],
                        )
                except Exception:
                    pass
                try:
                    self.state_registry.set_state(
                        "cerebellum_agent",
                        {"last_failure_insight": _insight},
                        writer="cerebellum_agent",
                    )
                except Exception:
                    pass
            return _insight or None
        except Exception as exc:
            _log.debug("CerebellumAgent.analyze_failure_pattern 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("cerebellum_agent") or {}
        state = dict(snap.get("state") or {})
        decision = dict(state.get("last_correction") or {})
        return {
            "last_strategy": str(decision.get("strategy") or "NO_ADJUST"),
            "last_urgency": float(decision.get("urgency") or 0.0),
            "last_backoff_ms": int(decision.get("backoff_ms") or 0),
            "last_latency_ms": int(decision.get("latency_ms") or 0),
            "correction_count": int(state.get("correction_count") or 0),
            "last_updated_ts_ms": int(decision.get("ts_ms") or 0),
        }

    async def _on_motor_action_dispatched(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        decision = await asyncio.to_thread(
            self.evaluate_correction,
            execution_feedback=payload,
            source="motor_action_dispatched",
            ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )
        # T-205：若策略为 RETRY_ONCE，延迟 backoff_ms 后发布 MOTOR_RETRY_REQUESTED
        if decision.get("strategy") == "RETRY_ONCE" and int(decision.get("backoff_ms") or 0) >= 0:
            import asyncio  # noqa: PLC0415
            import logging  # noqa: PLC0415
            _log = logging.getLogger("Natsume.tribe.cerebellum_agent")
            plan_id = str(decision.get("plan_id") or "").strip()
            backoff_ms = int(decision.get("backoff_ms") or 0)
            urgency = float(decision.get("urgency") or 0.5)
            _log.info(
                "CerebellumAgent RETRY_ONCE: plan_id=%s backoff_ms=%d urgency=%.2f",
                plan_id, backoff_ms, urgency,
            )
            if backoff_ms > 0:
                await asyncio.sleep(backoff_ms / 1000.0)
            emit_tribe_event(
                event_type="MOTOR_RETRY_REQUESTED",
                source_agent="cerebellum_agent",
                payload={
                    "plan_id": plan_id,
                    "backoff_ms": backoff_ms,
                    "urgency": urgency,
                    "strategy": "RETRY_ONCE",
                    "ts_ms": now_ms(),
                },
            )
            # T-268：RETRY_ONCE 时联动 LimbicAgent 触发轻度焦虑情绪偏移
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _anxiety_intensity = min(0.25, urgency * 0.28)
                    _limbic.drive_emotion_metabolism(
                        valence=round(-_anxiety_intensity * 0.50, 4),
                        arousal=round(_anxiety_intensity * 0.40, 4),
                        dominance=-0.05,
                        blend_weight=0.10,
                        event_kind=f"cerebellum_retry:{plan_id[:16] if plan_id else 'unknown'}",
                        source="cerebellum_agent",
                    )
            except Exception:
                pass

    async def _on_platform_disconnected(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        await asyncio.to_thread(
            self.evaluate_correction,
            execution_feedback={
                "accepted": False,
                "dispatched": False,
                "latency_ms": 0,
                "reason": f"PLATFORM_DISCONNECTED:{str(payload.get('platform') or '').strip().lower()}",
            },
            source="platform_disconnected",
            ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )


_CEREBELLUM_AGENT: CerebellumAgent | None = None


def get_cerebellum_agent() -> CerebellumAgent:
    global _CEREBELLUM_AGENT
    if _CEREBELLUM_AGENT is None:
        _CEREBELLUM_AGENT = CerebellumAgent()
    return _CEREBELLUM_AGENT


def set_cerebellum_agent(agent: CerebellumAgent | None) -> None:
    global _CEREBELLUM_AGENT
    _CEREBELLUM_AGENT = agent


__all__ = ["CerebellumAgent", "get_cerebellum_agent", "set_cerebellum_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
