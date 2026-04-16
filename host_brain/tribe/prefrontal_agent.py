from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

_logger = logging.getLogger("Natsume.prefrontal_agent")


@dataclass(slots=True)
class PrefrontalAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="prefrontal_agent",
            family="prefrontal",
            capabilities=("self_reflection", "drift_guard", "evolution", "autobiographical_gate"),
        )
    )
    _drift_detector: Any | None = field(default=None, init=False, repr=False)
    _growth_feedback_collector: Any | None = field(default=None, init=False, repr=False)
    _persona_growth_tracker: Any | None = field(default=None, init=False, repr=False)
    _evolution_orchestrator: Any | None = field(default=None, init=False, repr=False)
    _bond_style_engine: Any | None = field(default=None, init=False, repr=False)
    _self_reflection: Any | None = field(default=None, init=False, repr=False)
    _emergent_growth_engine: Any | None = field(default=None, init=False, repr=False)
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def resolve_self_reflection_context(
        self,
        *,
        persona_user_id: str,
        timeline_summary: Mapping[str, Any] | None,
        timeline_diary: Mapping[str, Any] | None,
        bond_state_obj: Any,
        bond_summary: Mapping[str, Any] | None,
        now_ms: int,
        trigger_reason: str = "runtime_snapshot",
    ) -> dict[str, Any]:
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
        from host_brain.persona.self_reflection import build_self_reflection_pulse  # noqa: PLC0415

        persona_engine = PersonaEngine.get_instance()
        snapshot_obj = persona_engine.get_self_model_snapshot(
            str(persona_user_id or "user_1"),
            timeline_summary=dict(timeline_summary or {}),
            timeline_diary=dict(timeline_diary or {}),
            bond_state=bond_state_obj,
            bond_summary=dict(bond_summary or {}),
            last_deep_talk_ts_ms=int(getattr(bond_state_obj, "last_event_ts_ms", 0) or 0),
            current_ts_ms=int(now_ms),
        )
        if hasattr(snapshot_obj, "as_dict"):
            snapshot = dict(snapshot_obj.as_dict() or {})
        elif isinstance(snapshot_obj, Mapping):
            snapshot = dict(snapshot_obj)
        else:
            snapshot = {}
        pulse = dict(
            build_self_reflection_pulse(
                snapshot,
                trigger_reason=str(trigger_reason or "runtime_snapshot"),
            )
            or {}
        ) if snapshot else {}
        return {
            "self_model_snapshot": snapshot,
            "self_reflection_pulse": pulse,
        }

    def resolve_style_evolution_summary(self) -> dict[str, Any]:
        from host_brain.persona.drift_detector import get_drift_detector  # noqa: PLC0415

        summary = get_drift_detector().get_style_evolution_summary()
        if isinstance(summary, Mapping):
            return dict(summary)
        return {}

    def resolve_persona_traits(self, *, persona_user_id: str) -> Any:
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
        from host_brain.persona.personality_traits import PersonalityTraits  # noqa: PLC0415

        try:
            return PersonaEngine.get_instance().get_traits(str(persona_user_id or "user_1"))
        except Exception:
            return PersonalityTraits.from_baseline()

    def resolve_persona_expression_hint(
        self,
        *,
        persona_user_id: str,
        bond_stage: str,
        user_emotion_tag: str,
        user_message: str,
        valence: float,
        arousal: float,
        dominance: float,
        ts_ms: int = 0,
    ) -> dict[str, Any]:
        from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415
        from host_brain.persona.expression_adapter import PersonaExpressionAdapter  # noqa: PLC0415

        adapter = PersonaExpressionAdapter()
        traits = self.resolve_persona_traits(persona_user_id=persona_user_id)
        emotion_vector = EmotionVector(
            valence=float(valence),
            arousal=float(arousal),
            dominance=float(dominance),
            ts_ms=int(ts_ms),
        )
        hint = adapter.adapt(
            traits,
            emotion_vector,
            str(bond_stage or "STRANGER"),
            user_emotion_tag=str(user_emotion_tag or "NEUTRAL"),
            user_message=str(user_message or ""),
        )
        follow_up_tendency = getattr(
            hint,
            "follow_up_tendency",
            getattr(hint, "追问倾向", 0.0),
        )
        return {
            "style_hint": str(getattr(hint, "style_hint", "") or ""),
            "tone": str(getattr(hint, "tone", "") or ""),
            "echo_weight_modifier": float(getattr(hint, "echo_weight_modifier", 0.0) or 0.0),
            "follow_up_tendency": float(follow_up_tendency or 0.0),
            "pace_hint": str(getattr(hint, "pace_hint", "") or ""),
        }

    def resolve_bond_style_instruction(
        self,
        *,
        bond_stage: str,
        intimacy_score: float,
        mood_state: str,
        natsume_mood: str,
        rift_active: bool,
        rift_severity: str,
        rift_progress: float,
        bond_trend: str,
        texture_hint: str = "",
        shared_memory_hints: list[str] | None = None,
    ) -> str:
        payload = self.resolve_bond_style_payload(
            bond_stage=bond_stage,
            intimacy_score=intimacy_score,
            mood_state=mood_state,
            natsume_mood=natsume_mood,
            rift_active=rift_active,
            rift_severity=rift_severity,
            rift_progress=rift_progress,
            bond_trend=bond_trend,
            texture_hint=texture_hint,
            shared_memory_hints=shared_memory_hints,
        )
        return str(payload.get("prompt_injection") or "")

    def resolve_bond_style_payload(
        self,
        *,
        bond_stage: str,
        intimacy_score: float,
        mood_state: str,
        natsume_mood: str,
        rift_active: bool,
        rift_severity: str,
        rift_progress: float,
        bond_trend: str,
        texture_hint: str = "",
        shared_memory_hints: list[str] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.bond_aware_style import BondAwareStyleEngine  # noqa: PLC0415

        if self._bond_style_engine is None:
            self._bond_style_engine = BondAwareStyleEngine()
        result = self._bond_style_engine.generate(
            bond_stage=str(bond_stage or "STRANGER"),
            intimacy_score=float(intimacy_score or 0.0),
            mood_state=str(mood_state or "neutral"),
            natsume_mood=str(natsume_mood or "NEUTRAL"),
            rift_active=bool(rift_active),
            rift_severity=str(rift_severity or "medium"),
            rift_progress=float(rift_progress or 0.0),
            bond_trend=str(bond_trend or ""),
            texture_hint=str(texture_hint or ""),
            shared_memory_hints=list(shared_memory_hints or []) or None,
        )
        as_dict_payload = {}
        if hasattr(result, "as_dict"):
            maybe = result.as_dict()
            if isinstance(maybe, Mapping):
                as_dict_payload = dict(maybe)
        elif isinstance(result, Mapping):
            as_dict_payload = dict(result)
        return {
            "as_dict": as_dict_payload,
            "prompt_injection": str(getattr(result, "prompt_injection", "") or ""),
            "forbidden_patterns": list(getattr(result, "forbidden_patterns", []) or []),
            "intimacy_level": float(
                getattr(
                    result,
                    "intimacy_level",
                    as_dict_payload.get("intimacy_level", 0.0),
                )
                or 0.0
            ),
            "bond_stage": str(as_dict_payload.get("bond_stage") or bond_stage or "STRANGER"),
        }

    def run_self_reflection_cycle(
        self,
        *,
        turn_count: int,
        timeline_summary: Mapping[str, Any] | None,
        timeline_diary: Mapping[str, Any] | None,
        current_emotion: str,
        bond_state: Any | None,
        bond_summary: Mapping[str, Any] | None,
        persona_changes: list[Any] | None,
        last_deep_talk_ts_ms: int,
        current_ts_ms: int,
        growth_awareness: Mapping[str, Any] | None,
        growth_summary: Mapping[str, Any] | None,
        style_evolution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.self_reflection import SelfReflection  # noqa: PLC0415

        if self._self_reflection is None:
            self._self_reflection = SelfReflection()
        reflection = self._self_reflection
        pulse = reflection.maybe_generate(
            turn_count=int(turn_count),
            timeline_summary=dict(timeline_summary or {}),
            timeline_diary=dict(timeline_diary or {}),
            current_emotion=str(current_emotion or ""),
            bond_state=bond_state,
            bond_summary=dict(bond_summary or {}),
            persona_changes=list(persona_changes or []),
            last_deep_talk_ts_ms=int(last_deep_talk_ts_ms),
            current_ts_ms=int(current_ts_ms),
            growth_awareness=dict(growth_awareness or {}),
            growth_summary=dict(growth_summary or {}),
            style_evolution=dict(style_evolution or {}),
        )
        snapshot: dict[str, Any] = {}
        if isinstance(pulse, Mapping):
            try:
                snapshot_obj = reflection._model.build_snapshot(  # type: ignore[attr-defined]
                    timeline_summary=dict(timeline_summary or {}),
                    timeline_diary=dict(timeline_diary or {}),
                    bond_state=bond_state,
                    bond_summary=dict(bond_summary or {}),
                    persona_changes=list(persona_changes or []),
                    last_deep_talk_ts_ms=int(last_deep_talk_ts_ms),
                    current_ts_ms=int(current_ts_ms),
                )
                snapshot = dict(snapshot_obj or {})
                snapshot = dict(reflection._validate_consistency(snapshot, bond_state) or {})  # type: ignore[attr-defined]
            except Exception:
                snapshot = {}
        return {
            "self_reflection_pulse": dict(pulse or {}) if isinstance(pulse, Mapping) else {},
            "snapshot": snapshot,
        }

    def align_self_reflection_pulse(
        self,
        *,
        pulse: Mapping[str, Any] | None,
        snapshot: Mapping[str, Any] | None = None,
        offline_state: Mapping[str, Any] | None = None,
        bond_summary: Mapping[str, Any] | None = None,
        motive_state: Mapping[str, Any] | None = None,
        response_policy: Mapping[str, Any] | None = None,
        runtime_flags: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.self_reflection import align_self_reflection_pulse  # noqa: PLC0415

        aligned = align_self_reflection_pulse(
            dict(pulse or {}),
            snapshot=dict(snapshot or {}),
            offline_state=dict(offline_state or {}),
            bond_summary=dict(bond_summary or {}),
            motive_state=dict(motive_state or {}),
            response_policy=dict(response_policy or {}),
            runtime_flags=dict(runtime_flags or {}),
        )
        return dict(aligned or {})

    def plan_response(
        self,
        *,
        user_message: str,
        motive_state: Mapping[str, Any] | None = None,
        recall_summary: str = "",
        bond_stage: str = "STRANGER",
        bond_intimacy: float = 0.0,
        immune_risk_level: str = "none",
        drift_alert_level: str = "NONE",
        emotion_tag: str = "NEUTRAL",
        valence: float = 0.0,
        arousal: float = 0.0,
        session_id: str = "",
        request_id: str = "",
        timeout_ms: int = 500,
    ) -> dict[str, Any]:
        """Phase D: 前额叶意图规划——用专用小模型实时决策"我要做什么"。

        这是 LLM 从"总控制器"降级为"语言生成器"的关键一步。
        PrefrontalAgent 基于器官族完整状态产出意图计划，
        chat_agent 退化为纯粹的语言生成器，只负责把 task 转成流畅自然语言。

        Args:
            user_message: 用户消息原文
            motive_state: LimbicAgent.compute_motive_state() 输出
            recall_summary: HippocampusAgent 记忆检索摘要
            bond_stage: 当前关系阶段
            bond_intimacy: 亲密度分数 0~1
            immune_risk_level: ImmuneAgent 风险等级
            drift_alert_level: 人格漂移告警等级
            emotion_tag: 当前情绪标签
            valence: 情感效价 -1~1
            arousal: 情感唤醒度 0~1
            session_id: 会话 ID
            request_id: 请求 ID
            timeout_ms: LLM 调用超时（毫秒），默认 500ms

        Returns:
            dict: {
                "action": "speak" | "skill" | "delegate" | "wait",
                "target": str,          # skill/agent 名称，speak 时为空
                "task": str,            # 任务描述，注入 chat_agent 作为语言化目标
                "tone_hint": str,       # 语气提示（warm/direct/gentle/playful）
                "urgency": float,       # 紧迫度 0~1
                "source": str,          # "prefrontal_llm" | "prefrontal_rule"
                "latency_ms": int,
            }
        """
        import time as _time  # noqa: PLC0415
        _t0 = _time.monotonic()

        # ── 规则快速路径（<1ms，无 LLM 调用）────────────────────────────
        # 1. 免疫高风险：强制 speak，不执行任何 skill
        if immune_risk_level in {"high", "critical"}:
            return {
                "action": "speak",
                "target": "",
                "task": "以温和方式回应，不执行任何外部操作",
                "tone_hint": "gentle",
                "urgency": 0.3,
                "source": "prefrontal_rule",
                "latency_ms": int((_time.monotonic() - _t0) * 1000),
            }
        # 2. 严重漂移：强制 speak，避免执行复杂任务加重漂移
        if drift_alert_level in {"HIGH", "CRITICAL"}:
            return {
                "action": "speak",
                "target": "",
                "task": "以真实自我回应，不执行复杂任务，优先情感连接",
                "tone_hint": "warm",
                "urgency": 0.4,
                "source": "prefrontal_rule",
                "latency_ms": int((_time.monotonic() - _t0) * 1000),
            }
        # 3. 极度退缩（withdraw > 0.85）：等待或极简回应
        _withdraw = float((motive_state or {}).get("withdraw", 0.0))
        if _withdraw > 0.85:
            return {
                "action": "speak",
                "target": "",
                "task": "简短回应，表达当前状态，不强迫自己展开话题",
                "tone_hint": "gentle",
                "urgency": 0.2,
                "source": "prefrontal_rule",
                "latency_ms": int((_time.monotonic() - _t0) * 1000),
            }

        # ── LLM 推理路径（小模型，<500ms）────────────────────────────────
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            # 从 executive.yaml 加载 prefrontal_plan prompt 模板
            _tpl = resolve_prompt_template_json("prefrontal_agent.plan_response.prompt") or {}
            _system_tpl = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")

            if not _system_tpl or not _user_tpl:
                raise ValueError("prefrontal_plan prompt 模板未配置")

            # 构建 user prompt
            _motive = dict(motive_state or {})
            _care = round(float(_motive.get("care", 0.0)), 3)
            _continuity = round(float(_motive.get("continuity", 0.0)), 3)
            _user_prompt = _user_tpl.format(
                user_message=str(user_message or "")[:200],
                emotion_tag=str(emotion_tag or "NEUTRAL"),
                valence=round(float(valence), 3),
                arousal=round(float(arousal), 3),
                bond_stage=str(bond_stage or "STRANGER"),
                bond_intimacy=round(float(bond_intimacy), 3),
                motive_care=_care,
                motive_continuity=_continuity,
                motive_withdraw=round(_withdraw, 3),
                recall_summary=str(recall_summary or "")[:300],
                immune_risk=str(immune_risk_level or "none"),
                drift_alert=str(drift_alert_level or "NONE"),
            )

            # 调用专用小模型（Flash，低延迟）
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=int(timeout_ms),
                agent_role="prefrontal_planner",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_tpl,
                user_prompt=_user_prompt,
                max_tokens=128,
                temperature=0.1,
            )
            _raw_text = str(_raw or "").strip()

            # 解析 JSON 输出
            import json as _json  # noqa: PLC0415
            import re as _re  # noqa: PLC0415
            _json_match = _re.search(r"\{.*\}", _raw_text, _re.DOTALL)
            if _json_match:
                _plan = _json.loads(_json_match.group())
            else:
                _plan = {}

            _action = str(_plan.get("action") or "speak").strip().lower()
            if _action not in {"speak", "skill", "delegate", "wait"}:
                _action = "speak"

            _result = {
                "action": _action,
                "target": str(_plan.get("target") or "").strip(),
                "task": str(_plan.get("task") or "").strip(),
                "tone_hint": str(_plan.get("tone_hint") or "balanced").strip(),
                "urgency": max(0.0, min(1.0, float(_plan.get("urgency") or 0.5))),
                "source": "prefrontal_llm",
                "latency_ms": int((_time.monotonic() - _t0) * 1000),
            }

            # 写入 state_registry 供可观测性
            self.state_registry.write(
                namespace="prefrontal_agent",
                state={"last_plan_response": {
                    **_result,
                    "session_id": session_id,
                    "request_id": request_id,
                    "ts_ms": now_ms(),
                }},
                writer="prefrontal_agent",
                merge=True,
                reason="plan_response",
            )
            _logger.info(
                "PrefrontalAgent.plan_response: action=%s tone=%s urgency=%.2f latency=%dms",
                _result["action"], _result["tone_hint"], _result["urgency"], _result["latency_ms"],
            )
            return _result

        except Exception as exc:
            _logger.warning(
                "PrefrontalAgent.plan_response LLM 调用失败（降级到规则）: %s", exc,
            )
            # 降级：基于 motive_state 做简单规则决策
            _care = float((motive_state or {}).get("care", 0.5))
            _tone = "warm" if _care > 0.6 else "balanced"
            return {
                "action": "speak",
                "target": "",
                "task": "",
                "tone_hint": _tone,
                "urgency": 0.5,
                "source": "prefrontal_rule_fallback",
                "latency_ms": int((_time.monotonic() - _t0) * 1000),
            }

    def run_evolution_cycle(self, *, system_intent: dict[str, Any], logger: Any | None = None) -> dict[str, Any]:
        runtime_logger = logger or _logger
        try:
            from host_brain.app.emotion_context import _global_emotion_timeline  # noqa: PLC0415
            from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator  # noqa: PLC0415
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            orchestrator = EvolutionOrchestrator()
            args = dict((system_intent or {}).get("args") or {})
            user_id = str(args.get("user_id") or "user_1")
            date = str(args.get("date") or _dt.date.today().isoformat())

            timeline_summary: dict[str, Any] = {}
            timeline_diary: dict[str, Any] = {}
            try:
                timeline_summary = _global_emotion_timeline.rebuild_summary(window_ms=24 * 3600 * 1000)
                timeline_diary = _global_emotion_timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
            except Exception:
                pass

            bond_state = None
            bond_summary: dict[str, Any] = {}
            try:
                social_snapshot = get_social_brain_agent().resolve_bond_snapshot(person_id=user_id)
                bond_state = social_snapshot.get("bond_state_obj")
                bond_summary = dict(social_snapshot.get("bond_summary") or {})
                if not bond_summary:
                    bond_summary = get_social_brain_agent().resolve_scheduler_bond_summary(
                        session_id="default",
                        user_id=user_id,
                    )
            except Exception:
                pass

            result = orchestrator.run_evolution_cycle(
                user_id=user_id,
                date=date,
                timeline_summary=timeline_summary,
                timeline_diary=timeline_diary,
                bond_state=bond_state,
                bond_summary=bond_summary,
            )
            runtime_logger.info(
                "evolution cycle finished: date=%s traits_changed=%s errors=%d",
                date,
                result.traits_changed,
                len(result.errors),
            )
            return {
                "status": "OK",
                "cycle_id": result.cycle_id,
                "traits_changed": result.traits_changed,
                "growth_awareness": result.growth_awareness,
                "errors": result.errors,
            }
        except Exception as exc:
            runtime_logger.warning("evolution cycle failed (degraded): %s", exc)
            return {
                "status": "DEGRADED",
                "reason_code": "EVOLUTION_CYCLE_FAILED",
                "detail": str(exc),
            }

    def resolve_persona_change_log(self, *, persona_user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

        entries = PersonaEngine.get_instance().get_change_log(
            str(persona_user_id or "user_1"),
            limit=max(1, int(limit)),
        )
        if not isinstance(entries, list):
            return []
        return [dict(item) if isinstance(item, Mapping) else {"value": item} for item in entries]

    def resolve_growth_awareness(self) -> dict[str, Any]:
        orchestrator = self._get_evolution_orchestrator()
        payload = orchestrator.get_last_growth_awareness()
        if isinstance(payload, Mapping):
            return dict(payload)
        return {}

    def resolve_growth_summary(self, *, persona_user_id: str, now_ts_ms: int) -> dict[str, Any]:
        orchestrator = self._get_evolution_orchestrator()
        payload = orchestrator.get_growth_summary(
            str(persona_user_id or "user_1"),
            now_ts_ms=int(now_ts_ms),
        )
        if isinstance(payload, Mapping):
            return dict(payload)
        return {}

    def run_persona_drift_cycle(
        self,
        *,
        llm_output_text: str,
        user_input_text: str,
    ) -> dict[str, Any]:
        from host_brain.app.dependencies import get_hard_state  # noqa: PLC0415
        from host_brain.persona.drift_detector import PersonaDriftDetector  # noqa: PLC0415

        if self._drift_detector is None:
            self._drift_detector = PersonaDriftDetector()
        detector = self._drift_detector
        result = detector.on_turn(
            llm_output_text=str(llm_output_text or ""),
            user_input_text=str(user_input_text or ""),
        )
        if result is None:
            return {}
        payload = {
            "alert_level": str(getattr(result, "alert_level", "") or "").strip().upper(),
            "drift_dims": list(getattr(result, "drift_dims", []) or []),
            "correction_hint": str(getattr(result, "correction_hint", "") or ""),
        }
        if payload["alert_level"] in {"MODERATE", "SEVERE"}:
            try:
                hard_state = get_hard_state()
                if hard_state is not None:
                    hard_state.set("drift_correction_hint", payload["correction_hint"])
            except Exception:
                pass
        return payload

    def run_growth_feedback_cycle(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> dict[str, Any]:
        from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415
        from host_brain.persona.growth_feedback_collector import GrowthFeedbackCollector  # noqa: PLC0415

        if self._growth_feedback_collector is None:
            self._growth_feedback_collector = GrowthFeedbackCollector()
        collector = self._growth_feedback_collector
        tracker = self._get_persona_growth_tracker()
        if tracker is None:
            return {
                "user_id": resolve_bond_user_id(session_id=str(session_id or "default")),
                "feedback_count": 0,
                "applied": False,
                "triggered_rules": [],
            }
        user_id = resolve_bond_user_id(session_id=str(session_id or "default"))
        feedbacks = list(
            GrowthFeedbackCollector.extract_engagement_signals(str(user_message or ""))
            or []
        )
        for feedback in feedbacks:
            collector.add_feedback(user_id, feedback)

        growth_result = None
        if feedbacks:
            growth_result = tracker.on_interaction_cycle(
                user_id=user_id,
                feedback_collector=collector,
                user_message=str(user_message or ""),
                assistant_reply=str(assistant_reply or ""),
            )
        return {
            "user_id": user_id,
            "feedback_count": len(feedbacks),
            "applied": bool(getattr(growth_result, "applied", False)),
            "triggered_rules": list(getattr(growth_result, "triggered_rules", []) or []),
        }

    def evaluate_autobiographical_memory_gate(
        self,
        *,
        persona_user_id: str,
        narrative: str,
        source_agent: str = "default_mode_agent",
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415
        from host_brain.tribe.tribe_state import get_global_registry  # noqa: PLC0415

        user_id = str(persona_user_id or "user_1").strip() or "user_1"
        narrative_text = str(narrative or "").strip()
        ctx = dict(context or {})
        trace_id = f"autobio_gate_{now_ms()}"
        registry = get_global_registry()
        vitals = registry.aggregate_vital_signs()
        gate_state = str(vitals.get("gate_state") or "open").strip().lower()
        immune_risk = str(vitals.get("immune_risk_level") or "none").strip().lower()
        stress_index = float(vitals.get("stress_index") or 0.0)
        bond_ctx = get_social_brain_agent().get_bond_context(
            session_id=str(ctx.get("session_id") or "default"),
            user_id=user_id,
        )
        bond_vector = dict(bond_ctx.get("bond_vector") or {})
        trust = float(bond_vector.get("trust") or 0.4)
        repair_confidence = float(bond_vector.get("repair_confidence") or 0.5)

        decision = "ALLOW"
        reason_code = "PASS"
        if gate_state == "pause":
            decision, reason_code = "BLOCK", "ENDOCRINE_GATE_PAUSE"
        elif immune_risk in {"high", "critical"}:
            decision, reason_code = "BLOCK", "IMMUNE_RISK_HIGH"
        elif stress_index > 0.78:
            decision, reason_code = "DEFER", "HIGH_STRESS"
        elif len(narrative_text) > 80 and trust < 0.32:
            decision, reason_code = "DEFER", "LOW_TRUST_LONG_NARRATIVE"
        elif repair_confidence < 0.25 and len(narrative_text) > 40:
            decision, reason_code = "DEFER", "REPAIR_CONFIDENCE_LOW"
        elif not narrative_text:
            decision, reason_code = "BLOCK", "EMPTY_NARRATIVE"

        payload = {
            "trace_id": trace_id,
            "user_id": user_id,
            "source_agent": str(source_agent or "default_mode_agent"),
            "decision": decision,
            "reason_code": reason_code,
            "narrative_chars": len(narrative_text),
            "gate_state": gate_state,
            "immune_risk_level": immune_risk,
            "stress_index": round(stress_index, 4),
            "bond_vector": {
                "trust": round(trust, 4),
                "repair_confidence": round(repair_confidence, 4),
            },
            "ts_ms": now_ms(),
        }
        self.state_registry.write(
            namespace="prefrontal_agent",
            state={"last_autobiographical_gate": payload},
            writer="prefrontal_agent",
            merge=True,
            reason="autobiographical_gate_decision",
            decision_trace_id=trace_id,
        )
        try:
            emit_tribe_event(
                event_type="AUTOBIOGRAPHICAL_GATE_DECISION",
                source_agent="prefrontal_agent",
                payload=payload,
            )
        except Exception:
            pass
        return payload

    def commit_autobiographical_memory(
        self,
        *,
        persona_user_id: str,
        narrative: str,
        source_agent: str = "default_mode_agent",
        context: Mapping[str, Any] | None = None,
        request_id: str = "",
    ) -> dict[str, Any]:
        from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

        gate = self.evaluate_autobiographical_memory_gate(
            persona_user_id=persona_user_id,
            narrative=narrative,
            source_agent=source_agent,
            context=context,
        )
        if str(gate.get("decision") or "").upper() != "ALLOW":
            return {"status": "BLOCKED", "gate": gate}
        write_result = get_hippocampus_agent().write_autobiographical_memory(
            person_id=str(persona_user_id or "user_1"),
            narrative=str(narrative or ""),
            gate_result=gate,
            request_id=request_id,
        )
        return {"status": "OK", "gate": gate, "write_result": write_result}

    def apply_stage_promotion_growth(
        self,
        *,
        user_id: str,
        from_stage: str,
        to_stage: str,
        ts_ms: int,
    ) -> dict[str, Any]:
        tracker = self._get_persona_growth_tracker()
        if tracker is None:
            return {"applied": False, "reason": "tracker_unavailable"}
        try:
            result = tracker.on_stage_promoted(
                user_id=str(user_id or "user_1"),
                from_stage=str(from_stage or "STRANGER"),
                to_stage=str(to_stage or "STRANGER"),
                ts_ms=int(ts_ms),
            )
            return {
                "applied": bool(getattr(result, "applied", False)),
                "reason": str(getattr(result, "reason", "") or ""),
            }
        except Exception as exc:
            return {"applied": False, "reason": str(exc)}

    def apply_milestone_growth(
        self,
        *,
        user_id: str,
        milestone_key: str,
        ts_ms: int,
    ) -> dict[str, Any]:
        tracker = self._get_persona_growth_tracker()
        if tracker is None:
            return {"applied": False, "reason": "tracker_unavailable"}
        try:
            result = tracker.on_milestone(
                user_id=str(user_id or "user_1"),
                milestone_key=str(milestone_key or ""),
                ts_ms=int(ts_ms),
            )
            return {
                "applied": bool(getattr(result, "applied", False)),
                "reason": str(getattr(result, "reason", "") or ""),
            }
        except Exception as exc:
            return {"applied": False, "reason": str(exc)}

    def _get_persona_growth_tracker(self) -> Any | None:
        from host_brain.persona.persona_growth import PersonaGrowthTracker  # noqa: PLC0415

        if self._persona_growth_tracker is None:
            try:
                self._persona_growth_tracker = PersonaGrowthTracker()
            except Exception:
                return None
        return self._persona_growth_tracker

    def run_emergent_growth_weekly(
        self,
        *,
        user_id: str,
        lookback_days: int,
        episodic_store: Any,
        llm_fn: Any | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.interaction_pattern_analyzer import InteractionPatternAnalyzer  # noqa: PLC0415
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

        persona_engine = PersonaEngine.get_instance()
        analyzer = InteractionPatternAnalyzer(llm_fn=llm_fn)
        pattern_result = analyzer.analyze(
            user_id=str(user_id or "user_1"),
            episodic_store=episodic_store,
            current_traits=persona_engine.get_traits(str(user_id or "user_1")),
            lookback_days=max(1, int(lookback_days or 7)),
        )
        growth_result = self._get_emergent_growth_engine().apply(
            user_id=str(user_id or "user_1"),
            pattern_result=pattern_result,
        )
        return {
            "status": "OK" if bool(getattr(growth_result, "applied", False)) else "SKIPPED",
            "user_id": str(user_id or "user_1"),
            "lookback_days": max(1, int(lookback_days or 7)),
            "pattern_result": dict(pattern_result.as_dict() if hasattr(pattern_result, "as_dict") else {}),
            "growth_result": dict(growth_result.as_dict() if hasattr(growth_result, "as_dict") else {}),
        }

    def resolve_emergent_growth_status(self, *, user_id: str, recent_limit: int = 5) -> dict[str, Any]:
        engine = self._get_emergent_growth_engine()
        recent = max(1, int(recent_limit or 5))
        growth_log = [record.as_dict() for record in engine.get_growth_log(str(user_id or "user_1"))[-recent:]]
        return {
            "status": "OK",
            "user_id": str(user_id or "user_1"),
            "growth_summary": engine.get_growth_summary(str(user_id or "user_1")),
            "recent_growth_log": growth_log,
        }

    def _get_evolution_orchestrator(self) -> Any:
        from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator  # noqa: PLC0415

        if self._evolution_orchestrator is None:
            self._evolution_orchestrator = EvolutionOrchestrator()
        return self._evolution_orchestrator

    def _get_emergent_growth_engine(self) -> Any:
        from host_brain.persona.emergent_growth_engine import EmergentGrowthEngine  # noqa: PLC0415
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

        if self._emergent_growth_engine is None:
            self._emergent_growth_engine = EmergentGrowthEngine(
                persona_engine=PersonaEngine.get_instance(),
            )
        return self._emergent_growth_engine

    # ── TribeEventBus 订阅注册（Phase 4）────────────────────────────────────

    def export_runtime_state(self) -> dict[str, Any]:
        """T-274：导出 PrefrontalAgent 当前运行时状态快照。

        读取 state_registry 中的 prefrontal_agent 命名空间快照，
        不触发任何副作用（不 emit 事件，不写 state_registry）。

        Returns:
            dict: 包含 alert_level/drift_dims/correction_hint/session_count 等关键字段。
        """
        snap = self.state_registry.get_state("prefrontal_agent") or {}
        state = dict(snap.get("state") or {})
        last_drift = dict(state.get("last_drift_result") or {})
        return {
            "alert_level": str(last_drift.get("alert_level") or "NONE").strip().upper() or "NONE",
            "drift_dims": list(last_drift.get("drift_dims") or []),
            "correction_hint": str(last_drift.get("correction_hint") or ""),
            "drift_score": round(float(last_drift.get("drift_score") or 0.0), 4),
            "correction_applied": bool(last_drift.get("correction_applied", False)),
            "last_correction_reason": str(last_drift.get("correction_reason") or ""),
            "session_count": int(state.get("session_count") or 0),
            "evolution_cycle_count": int(state.get("evolution_cycle_count") or 0),
            "last_drift_ts_ms": int(last_drift.get("ts_ms") or 0),
        }

    def register_subscriptions(self) -> None:
        """向 TribeEventBus 注册事件订阅 handler。

        在 http_hooks.py lifespan 中调用，确保 Bus 的 dispatch_loop 启动后
        PrefrontalAgent 的 handler 能被正确触发。

        T-34：新增订阅 BOND_STAGE_CHANGED，
        关系阶段跃迁时触发 apply_stage_promotion_growth，将关系进展转化为人格成长动力。
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("EVOLUTION_CYCLE_TRIGGERED", self._on_evolution_cycle_triggered)
            bus.subscribe("INTERACTION_RECORDED", self._on_interaction_recorded)
            # T-34：订阅关系阶段跃迁事件，触发人格成长
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed_growth)
            # T-47：订阅孤独感信号，影响决策策略（降低主动性阈值）
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
            # T-71：订阅免疫拦截事件，触发安全审查成长周期
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block_growth)
        except Exception as exc:
            _logger.warning("PrefrontalAgent 订阅注册失败（降级）: %s", exc)

    async def _on_evolution_cycle_triggered(self, event: Any) -> None:
        """处理 EVOLUTION_CYCLE_TRIGGERED 事件：触发人格演化周期。

        由 ANSAgent（inner_world_skill）或 LimbicAgent 发布，
        PrefrontalAgent 订阅后异步执行 evolution_cycle。
        """
        try:
            payload = dict(event.payload or {})
            user_id = str(payload.get("user_id") or "user_1").strip() or "user_1"
            reason = str(payload.get("reason") or "event_triggered").strip()
            _logger.info(
                "PrefrontalAgent 收到 EVOLUTION_CYCLE_TRIGGERED: user_id=%s reason=%s",
                user_id, reason,
            )
            # 异步执行 evolution_cycle（避免阻塞 dispatch_loop）
            import asyncio  # noqa: PLC0415
            asyncio.create_task(
                self._run_evolution_cycle_async(user_id=user_id, reason=reason),
                name=f"evolution_cycle_{user_id}",
            )
        except Exception as exc:
            _logger.warning("PrefrontalAgent._on_evolution_cycle_triggered 异常: %s", exc)

    async def _run_evolution_cycle_async(self, *, user_id: str, reason: str) -> None:
        """在独立 task 中异步执行 evolution_cycle，避免阻塞事件分发循环。"""
        try:
            import asyncio  # noqa: PLC0415
            result = await asyncio.to_thread(
                self.run_evolution_cycle,
                system_intent={
                    "skill_name": "evolution_cycle",
                    "args": {"user_id": user_id},
                    "proposer_node_id": "tribe_event_bus",
                },
            )
            _logger.info(
                "PrefrontalAgent evolution_cycle 完成: user_id=%s reason=%s status=%s",
                user_id, reason, result.get("status"),
            )
        except Exception as exc:
            _logger.warning(
                "PrefrontalAgent._run_evolution_cycle_async 异常: user_id=%s exc=%s",
                user_id, exc,
            )

    async def _on_interaction_recorded(self, event: Any) -> None:
        """处理 INTERACTION_RECORDED 事件：触发人格漂移检测。

        T-26：通过 assistant_reply 内容检测 humor/task 成功信号，
        发布 TASK_SUCCESS / HUMOR_LANDED 事件，供 PainRewardAgent 接收。
        """
        try:
            payload = dict(event.payload or {})
            user_message = str(payload.get("user_message") or "").strip()
            assistant_reply = str(payload.get("assistant_reply") or "").strip()
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            if user_message and assistant_reply:
                drift_result = self.run_growth_feedback_cycle(
                    session_id=session_id,
                    user_message=user_message,
                    assistant_reply=assistant_reply,
                )

                # T-219：漂移检测结果联动 LimbicAgent 情绪调整
                drift_alert = str(drift_result.get("drift_alert_level") or "").strip().upper()
                if drift_alert in {"MODERATE", "SEVERE"}:
                    # FIX-P1：将漂移状态同步到 GovernorRulesCache（L3 → L2 单向通道）
                    # PrefrontalAgent 检测到漂移时，收紧 Governor 的 scope_policy_gate
                    try:
                        from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
                        _scope_override = f"drift_restricted:{drift_alert.lower()}" if drift_alert == "SEVERE" else None
                        get_governor_rules_cache().update_prefrontal_state(
                            drift_detected=True,
                            scope_override=_scope_override,
                        )
                        _logger.info(
                            "PrefrontalAgent 漂移状态已同步到 GovernorRulesCache: alert_level=%s scope_override=%s",
                            drift_alert, _scope_override,
                        )
                    except Exception as _gov_exc:
                        _logger.debug("PrefrontalAgent GovernorRulesCache 更新跳过: %s", _gov_exc)

                    try:
                        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                        limbic = get_limbic_agent()
                        if hasattr(limbic, "drive_emotion_metabolism"):
                            # 漂移时情绪向不安/焦虑偏移
                            severity = 0.15 if drift_alert == "MODERATE" else 0.28
                            limbic.drive_emotion_metabolism(
                                valence=-severity,
                                arousal=severity * 0.6,
                                dominance=-0.05,
                                blend_weight=0.25,
                                event_kind=f"persona_drift:{drift_alert.lower()}",
                                source="prefrontal_agent",
                            )
                    except Exception:
                        pass
                    # T-279：高漂移时联动 EndocrineAgent 记录压力事件（人格漂移是内在压力源）
                    try:
                        from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
                        _endo = get_endocrine_agent()
                        if hasattr(_endo, "record_stress_event"):
                            _drift_intensity = 0.20 if drift_alert == "MODERATE" else 0.35
                            _endo.record_stress_event(
                                source=f"persona_drift:{drift_alert.lower()}",
                                intensity=_drift_intensity,
                            )
                    except Exception:
                        pass
                    # T-336：高漂移时联动 PainRewardAgent 记录深层痛苦（价值漂移是自我认同的痛苦）
                    try:
                        from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                        _pr = get_pain_reward_agent()
                        if hasattr(_pr, "record_pain"):
                            _pain_intensity = 0.22 if drift_alert == "MODERATE" else 0.38
                            _pr.record_pain(
                                source=f"persona_drift:{drift_alert.lower()}",
                                intensity=_pain_intensity,
                                metadata={
                                    "drift_alert": drift_alert,
                                    "session_id": session_id,
                                    "drift_dims": list(drift_result.get("drift_dims") or []),
                                },
                            )
                    except Exception:
                        pass
                    # T-374：严重漂移（SEVERE）时联动 InteroceptionAgent 注入深层内在压力
                    # 人格/价值观漂移是最深层的内在压力源（自我认同危机消耗大量认知资源）
                    if drift_alert == "SEVERE":
                        try:
                            from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                            _intero = get_interoception_agent()
                            if hasattr(_intero, "inject_external_stress"):
                                _drift_intero = round(min(0.40, len(
                                    list(drift_result.get("drift_dims") or [])
                                ) * 0.10 + 0.20), 4)
                                _intero.inject_external_stress(
                                    source=f"prefrontal_severe_drift:{session_id[:16]}",
                                    intensity=_drift_intero,
                                )
                        except Exception:
                            pass
                else:
                    # FIX-P1：漂移恢复正常时，重置 GovernorRulesCache 的漂移状态
                    # 避免历史漂移状态永久收紧 Governor 策略
                    if drift_alert in {"NONE", "LOW", ""}:
                        try:
                            from host_brain.integrations.governor_rules_cache import get_governor_rules_cache  # noqa: PLC0415
                            get_governor_rules_cache().update_prefrontal_state(
                                drift_detected=False,
                                scope_override=None,
                            )
                        except Exception:
                            pass

            # T-26：检测 humor/task 成功信号并发布对应事件
            if assistant_reply:
                try:
                    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415

                    _reply_lower = assistant_reply.lower()
                    # 幽默成功信号：回复中包含笑声/幽默标记词
                    _HUMOR_SIGNALS = ["哈哈", "哈哈哈", "lol", "haha", "😄", "😂", "🤣", "笑死", "好笑", "哈哈哈哈"]
                    _humor_hit = any(sig in _reply_lower or sig in assistant_reply for sig in _HUMOR_SIGNALS)
                    if _humor_hit:
                        emit_tribe_event(
                            event_type="HUMOR_LANDED",
                            source_agent="prefrontal_agent",
                            payload={
                                "session_id": session_id,
                                "intensity": 0.3,
                                "reason": "humor_signal_in_reply",
                                "reply_preview": assistant_reply[:64],
                            },
                        )
                        _logger.debug(
                            "PrefrontalAgent 发布 HUMOR_LANDED: session_id=%s", session_id,
                        )

                    # 任务成功信号：用户消息包含任务请求词 + 回复包含完成标记词
                    _TASK_REQUEST_SIGNALS = ["帮我", "帮忙", "请", "能不能", "可以", "写", "生成", "翻译", "总结", "分析"]
                    _TASK_DONE_SIGNALS = ["好的", "完成", "已经", "这是", "如下", "以下", "done", "here", "sure"]
                    _task_request = any(sig in user_message for sig in _TASK_REQUEST_SIGNALS)
                    _task_done = any(sig in _reply_lower or sig in assistant_reply for sig in _TASK_DONE_SIGNALS)
                    if _task_request and _task_done and not _humor_hit:
                        emit_tribe_event(
                            event_type="TASK_SUCCESS",
                            source_agent="prefrontal_agent",
                            payload={
                                "session_id": session_id,
                                "intensity": 0.35,
                                "reason": "task_completion_signal",
                                "user_message_preview": user_message[:64],
                            },
                        )
                        _logger.debug(
                            "PrefrontalAgent 发布 TASK_SUCCESS: session_id=%s", session_id,
                        )
                except Exception as inner_exc:
                    _logger.debug("PrefrontalAgent humor/task 事件发布跳过: %s", inner_exc)
        except Exception as exc:
            _logger.warning("PrefrontalAgent._on_interaction_recorded 异常: %s", exc)


    async def _on_bond_stage_changed_growth(self, event: Any) -> None:
        """处理 BOND_STAGE_CHANGED 事件：关系阶段跃迁触发人格成长。

        T-34：当 bond stage 发生升级跃迁时，调用 apply_stage_promotion_growth()，
        将关系进展转化为人格成长动力（如开放度提升、信任感增强等）。
        降级跃迁不触发成长（但不会回滚已有成长）。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            from_stage = str(payload.get("from_stage") or "").strip().upper()
            to_stage = str(payload.get("to_stage") or "").strip().upper()
            user_id = str(payload.get("user_id") or "user_1").strip() or "user_1"
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            _STAGE_ORDER = [
                "STRANGER", "ACQUAINTANCE", "FRIEND",
                "CLOSE_FRIEND", "INTIMATE", "SOULMATE",
            ]
            from_idx = _STAGE_ORDER.index(from_stage) if from_stage in _STAGE_ORDER else -1
            to_idx = _STAGE_ORDER.index(to_stage) if to_stage in _STAGE_ORDER else -1

            # 只处理升级跃迁
            if from_idx >= 0 and to_idx > from_idx:
                import asyncio  # noqa: PLC0415
                result = await asyncio.to_thread(
                    self.apply_stage_promotion_growth,
                    user_id=user_id,
                    from_stage=from_stage,
                    to_stage=to_stage,
                    ts_ms=ts_ms,
                )
                _logger.info(
                    "PrefrontalAgent 关系阶段跃迁人格成长: %s→%s user_id=%s applied=%s",
                    from_stage, to_stage, user_id, result.get("applied"),
                )
        except Exception as exc:
            _logger.warning("PrefrontalAgent._on_bond_stage_changed_growth 异常: %s", exc)


    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：孤独感影响决策策略。

        T-47：当孤独感较高时（>0.6），降低主动发起话题的阈值，
        触发 EVOLUTION_CYCLE_TRIGGERED 以加速人格成长（孤独感是成长催化剂）。
        孤独感极高时（>0.8）还会触发 milestone 成长节点。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            user_id = str(payload.get("user_id") or "user_1").strip() or "user_1"
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            if loneliness > 0.6:
                _logger.info(
                    "PrefrontalAgent 孤独感触发决策调整: loneliness=%.2f user_id=%s",
                    loneliness, user_id,
                )
                # 孤独感 > 0.6：触发演化周期（孤独感是人格成长的催化剂）
                try:
                    from host_brain.tribe.events import emit_tribe_event  # noqa: PLC0415
                    emit_tribe_event(
                        event_type="EVOLUTION_CYCLE_TRIGGERED",
                        source_agent="prefrontal_agent",
                        payload={
                            "user_id": user_id,
                            "reason": "loneliness_signal",
                            "loneliness": loneliness,
                            "ts_ms": ts_ms,
                        },
                    )
                except Exception:
                    pass

            if loneliness > 0.8:
                # 孤独感极高：触发 milestone 成长节点（深度孤独体验）
                import asyncio  # noqa: PLC0415
                asyncio.create_task(
                    asyncio.to_thread(
                        self.apply_milestone_growth,
                        user_id=user_id,
                        milestone_key="deep_loneliness_experience",
                        ts_ms=ts_ms,
                    ),
                    name=f"loneliness_milestone_{user_id}",
                )
        except Exception as exc:
            _logger.warning("PrefrontalAgent._on_loneliness_signal 异常: %s", exc)

    async def _on_immune_block_growth(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：拦截时触发安全审查成长周期。

        T-71：ImmuneAgent 拦截有害内容时发布此事件，
        PrefrontalAgent 订阅后触发 apply_safety_review_growth()，
        将安全拦截转化为人格成长动力（提升边界感/自我保护意识）。
        仅在 block_rate > 0.1 或 risk_level 为 high/critical 时触发，
        避免低频拦截过度影响人格演化。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "").strip().lower()
            score = float(payload.get("score") or 0.0)
            session_id = str(payload.get("session_id") or "default").strip() or "default"
            # 仅高风险拦截才触发成长周期
            if risk_level not in ("high", "critical") and score < 0.7:
                return
            user_id = str(payload.get("user_id") or "user_1").strip() or "user_1"
            _logger.info(
                "PrefrontalAgent IMMUNE_BLOCK 触发安全审查成长: risk_level=%s score=%.3f session_id=%s",
                risk_level, score, session_id,
            )
            import asyncio  # noqa: PLC0415
            asyncio.create_task(
                self._run_evolution_cycle_async(
                    user_id=user_id,
                    reason=f"immune_block_risk_{risk_level}",
                ),
                name=f"immune_block_growth_{session_id}",
            )
        except Exception as exc:
            _logger.warning("PrefrontalAgent._on_immune_block_growth 异常: %s", exc)


_PREFRONTAL_AGENT: PrefrontalAgent | None = None


def get_prefrontal_agent() -> PrefrontalAgent:
    global _PREFRONTAL_AGENT
    if _PREFRONTAL_AGENT is None:
        _PREFRONTAL_AGENT = PrefrontalAgent()
    return _PREFRONTAL_AGENT


def set_prefrontal_agent(agent: PrefrontalAgent | None) -> None:
    global _PREFRONTAL_AGENT
    _PREFRONTAL_AGENT = agent


__all__ = ["PrefrontalAgent", "get_prefrontal_agent", "set_prefrontal_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
