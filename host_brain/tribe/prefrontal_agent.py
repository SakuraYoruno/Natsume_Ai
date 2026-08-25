from __future__ import annotations

import datetime as _dt
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.tribe.model_invocation import (
    complete_tribe_compiled,
    complete_tribe_compiled_sync,
)
from host_brain.core.ids import now_ms
from host_brain.model_runtime.live_prompts import (
    PREFRONTAL_PLAN_PROMPT_ID,
    PREFRONTAL_SELF_REFLECTION_PROMPT_ID,
)
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.prefrontal_event_handlers import PrefrontalEventHandlersMixin
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.prefrontal_runtime_policy import (
    background_tasks_enabled as _background_tasks_enabled,
    expression_preference_hints as _expression_preference_hints,
    resolve_prefrontal_bond_user_id as _resolve_prefrontal_bond_user_id,
    resolve_prefrontal_persona_id as _resolve_prefrontal_persona_id,
    resolve_prefrontal_persona_workspace_id as _resolve_prefrontal_persona_workspace_id,
)
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

_logger = logging.getLogger("Natsume.prefrontal_agent")


@dataclass(slots=True)
class PrefrontalAgent(PrefrontalEventHandlersMixin):
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
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
    _last_evolution_event_ts_by_key: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _evolution_event_inflight_keys: set[str] = field(default_factory=set, init=False, repr=False)
    _last_evolution_cycle_ts_by_user: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _evolution_cycle_inflight_users: set[str] = field(default_factory=set, init=False, repr=False)
    _created_monotonic: float = field(default_factory=time.monotonic, init=False, repr=False)
    _last_loneliness_event_emit_ts_by_user: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _last_loneliness_signal_log_ts_by_user: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _last_loneliness_milestone_ts_by_user: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def _uses_canonical_affective_life(self) -> bool:
        """Whether this agent must reject legacy affect/Bond paths.

        Missing or malformed composition is fail-closed. A bare agent must
        never silently regain the retired Limbic, Endocrine, Pain or Bond
        writer graph merely because its canonical ports were not injected.
        """

        ports = self.application_ports
        if not isinstance(ports, TribeApplicationPorts):
            return True
        return bool(ports.uses_canonical_affective_life())

    def uses_canonical_affective_life(self) -> bool:
        """Expose the composition boundary without exposing mutable ports.

        Compatibility Skill adapters need this preflight so they can stop
        before resolving a model port or handing raw episodic data to a legacy
        analyzer.  It is deliberately a read-only capability flag: it does
        not create a second affect/relationship authority.
        """

        return self._uses_canonical_affective_life()

    @staticmethod
    def _retired_canonical_life_result(
        *,
        operation: str,
        decision: str | None = None,
    ) -> dict[str, Any]:
        """Return a side-effect-free result for a retired legacy writer.

        Canonical companion growth is driven by durable, scope-bound
        aftereffects and the profile lifecycle.  The old Prefrontal
        autobiographical path derives decisions from global organs and Bond,
        so it must not even inspect those sources after canonical cutover.
        """

        result: dict[str, Any] = {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_PREFRONTAL_GROWTH_RETIRED",
            "operation": str(operation),
            "applied": False,
            "retired": True,
        }
        if decision is not None:
            result["decision"] = str(decision).strip().upper()
        return result

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
        render_target_lang: str = "zh",
        use_llm: bool = True,
    ) -> dict[str, Any]:
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415
        from host_brain.persona.self_reflection import build_self_reflection_pulse  # noqa: PLC0415

        persona_engine = PersonaEngine.get_instance()
        persona_user_id = _resolve_prefrontal_persona_id(persona_user_id)
        snapshot_obj = persona_engine.get_self_model_snapshot(
            persona_user_id,
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
        llm_enabled = bool(use_llm)
        model_attempts: list[dict[str, Any]] = []
        if llm_enabled and not _background_tasks_enabled():
            return {
                "self_model_snapshot": snapshot,
                "self_reflection_pulse": {},
                "reason_code": "SELF_REFLECTION_BACKGROUND_TASKS_DISABLED",
            }
        if snapshot:
            llm_fn = None
            if llm_enabled:
                def _complete_reflection(prompt: str) -> str:
                    completion = complete_tribe_compiled_sync(
                        attempt_service=self.application_ports.require("model_attempt_service")(),
                        prompt_id=PREFRONTAL_SELF_REFLECTION_PROMPT_ID,
                        messages=({"role": "user", "content": str(prompt or "")},),
                        request_id=(
                            f"self_reflection_context::{int(now_ms)}::"
                            f"{len(model_attempts) + 1}"
                        ),
                        scene_type="SELF_REFLECTION",
                        cognitive_tier="B",
                        deadline_ms=4_000,
                        foreground=False,
                        metadata={"caller": "prefrontal_self_reflection"},
                    )
                    model_attempts.append(dict(completion.trace))
                    if not completion.success:
                        raise RuntimeError(
                            completion.result.error_code or "SELF_REFLECTION_MODEL_ATTEMPT_FAILED"
                        )
                    return completion.content

                llm_fn = _complete_reflection
            try:
                pulse = dict(
                    build_self_reflection_pulse(
                        snapshot,
                        trigger_reason=str(trigger_reason or "runtime_snapshot"),
                        llm_fn=llm_fn,
                        strict_llm=bool(llm_fn),
                        render_target_lang=str(render_target_lang or "zh"),
                    )
                    or {}
                )
            except TypeError:
                pulse = dict(
                    build_self_reflection_pulse(
                        snapshot,
                        trigger_reason=str(trigger_reason or "runtime_snapshot"),
                    )
                    or {}
                )
        else:
            pulse = {}
        return {
            "self_model_snapshot": snapshot,
            "self_reflection_pulse": pulse,
            "model_attempts": model_attempts,
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

        resolved_user_id = _resolve_prefrontal_persona_id(
            persona_user_id
        )
        if self.application_ports is not None:
            provider = getattr(
                self.application_ports,
                "evolution_activation_service",
                None,
            )
            if callable(provider):
                payload = provider().resolve_persona_traits(
                    workspace_id=(
                        _resolve_prefrontal_persona_workspace_id()
                    ),
                    subject_id=resolved_user_id,
                )
                if payload is not None:
                    return PersonalityTraits.from_dict(dict(payload))
        try:
            return PersonaEngine.get_instance().get_traits(
                resolved_user_id
            )
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
        render_target_lang: str = "zh",
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
        try:
            hint = adapter.adapt(
                traits,
                emotion_vector,
                str(bond_stage or "FRIEND"),
                user_emotion_tag=str(user_emotion_tag or "NEUTRAL"),
                user_message=str(user_message or ""),
                render_target_lang=str(render_target_lang or "zh"),
            )
        except TypeError:
            hint = adapter.adapt(
                traits,
                emotion_vector,
                str(bond_stage or "FRIEND"),
                user_emotion_tag=str(user_emotion_tag or "NEUTRAL"),
                user_message=str(user_message or ""),
            )
        follow_up_tendency = getattr(
            hint,
            "follow_up_tendency",
            getattr(hint, "追问倾向", 0.0),
        )
        style_hint = str(getattr(hint, "style_hint", "") or "")
        active_preferences: dict[str, Any] | None = None
        if self.application_ports is not None:
            provider = getattr(
                self.application_ports,
                "evolution_activation_service",
                None,
            )
            if callable(provider):
                active_preferences = (
                    provider().resolve_expression_preferences(
                        workspace_id=(
                            _resolve_prefrontal_persona_workspace_id()
                        ),
                        subject_id=_resolve_prefrontal_persona_id(
                            persona_user_id
                        ),
                    )
                )
        preference_hints = _expression_preference_hints(
            active_preferences
        )
        if preference_hints:
            style_hint = (
                f"{style_hint}；长期偏好："
                + "、".join(preference_hints)
            ).strip("；")
        return {
            "style_hint": style_hint,
            "tone": str(getattr(hint, "tone", "") or ""),
            "echo_weight_modifier": float(getattr(hint, "echo_weight_modifier", 0.0) or 0.0),
            "follow_up_tendency": float(follow_up_tendency or 0.0),
            "pace_hint": str(getattr(hint, "pace_hint", "") or ""),
            "preference_policy_active": bool(active_preferences),
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
        render_target_lang: str = "zh",
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
            render_target_lang=render_target_lang,
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
        render_target_lang: str = "zh",
    ) -> dict[str, Any]:
        from host_brain.persona.bond_aware_style import BondAwareStyleEngine  # noqa: PLC0415

        if self._bond_style_engine is None:
            self._bond_style_engine = BondAwareStyleEngine()
        result = self._bond_style_engine.generate(
            bond_stage=str(bond_stage or "FRIEND"),
            intimacy_score=float(intimacy_score or 0.0),
            mood_state=str(mood_state or "neutral"),
            natsume_mood=str(natsume_mood or "NEUTRAL"),
            rift_active=bool(rift_active),
            rift_severity=str(rift_severity or "medium"),
            rift_progress=float(rift_progress or 0.0),
            bond_trend=str(bond_trend or ""),
            texture_hint=str(texture_hint or ""),
            shared_memory_hints=list(shared_memory_hints or []) or None,
            render_target_lang=str(render_target_lang or "zh"),
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
            "bond_stage": str(as_dict_payload.get("bond_stage") or bond_stage or "FRIEND"),
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
        render_target_lang: str = "zh",
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
            render_target_lang=str(render_target_lang or "zh"),
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
        render_policy: Mapping[str, Any] | None = None,
        runtime_flags: Mapping[str, Any] | None = None,
        render_target_lang: str = "zh",
    ) -> dict[str, Any]:
        from host_brain.persona.self_reflection import align_self_reflection_pulse  # noqa: PLC0415

        aligned = align_self_reflection_pulse(
            dict(pulse or {}),
            snapshot=dict(snapshot or {}),
            offline_state=dict(offline_state or {}),
            bond_summary=dict(bond_summary or {}),
            motive_state=dict(motive_state or {}),
            render_policy=dict(render_policy or {}),
            runtime_flags=dict(runtime_flags or {}),
            render_target_lang=str(render_target_lang or "zh"),
        )
        return dict(aligned or {})

    async def plan_response(
        self,
        *,
        user_message: str,
        motive_state: Mapping[str, Any] | None = None,
        recall_summary: str = "",
        bond_stage: str = "FRIEND",
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
        surface_reply 退化为纯粹的语言生成器，只负责把 task 转成流畅自然语言。

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
                "task": str,            # 任务描述，注入 surface_reply 作为语言化目标
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

        # ── Canonical model-attempt path ─────────────────────────────────
        _model_attempt_trace: dict[str, Any] = {}
        try:
            from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json  # noqa: PLC0415

            # 从 executive.yaml 加载 prefrontal_plan prompt 模板
            _tpl = resolve_prompt_contract_json("prefrontal_agent.plan_response.prompt") or {}
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
                bond_stage=str(bond_stage or "FRIEND"),
                bond_intimacy=round(float(bond_intimacy), 3),
                motive_care=_care,
                motive_continuity=_continuity,
                motive_withdraw=round(_withdraw, 3),
                recall_summary=str(recall_summary or "")[:300],
                immune_risk=str(immune_risk_level or "none"),
                drift_alert=str(drift_alert_level or "NONE"),
            )

            _completion = await complete_tribe_compiled(
                attempt_service=self.application_ports.require("model_attempt_service")(),
                prompt_id=PREFRONTAL_PLAN_PROMPT_ID,
                messages=(
                    {"role": "system", "content": _system_tpl},
                    {"role": "user", "content": _user_prompt},
                ),
                request_id=str(request_id or f"prefrontal::{now_ms()}"),
                scene_type="PREFRONTAL_PLAN",
                cognitive_tier="B",
                deadline_ms=int(timeout_ms),
                foreground=True,
                metadata={"caller": "prefrontal_plan_response"},
            )
            _model_attempt_trace = dict(_completion.trace)
            if not _completion.success:
                raise RuntimeError(
                    _completion.result.error_code or "PREFRONTAL_MODEL_ATTEMPT_FAILED"
                )
            _raw_text = _completion.content.strip()

            _plan = json.loads(_raw_text)

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
                "model_attempt": _model_attempt_trace,
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
                "model_attempt": _model_attempt_trace,
            }

    def run_evolution_cycle(self, *, system_intent: dict[str, Any], logger: Any | None = None) -> dict[str, Any]:
        runtime_logger = logger or _logger
        user_id = "natsume_ai"
        try:
            args = dict((system_intent or {}).get("args") or {})
            user_id = _resolve_prefrontal_persona_id(args.get("user_id"))
            date = str(args.get("date") or _dt.date.today().isoformat())

            if self._uses_canonical_affective_life():
                # A generic scheduler/EventBus intent identifies Natsume but
                # not the exact workspace + relationship subject whose
                # durable aftereffects could justify a Persona proposal.
                # Do not run the legacy in-memory orchestrator with empty
                # relationship inputs merely to manufacture background
                # "growth".  A future canonical route must carry the exact
                # aggregate and its receipt refs explicitly.
                runtime_logger.info(
                    "canonical evolution deferred: exact relationship scope required "
                    "user_id=%s date=%s",
                    user_id,
                    date,
                )
                return {
                    "status": "DEFERRED",
                    "reason_code": "CANONICAL_RELATIONSHIP_SCOPE_REQUIRED",
                    "proposal_only": True,
                    "proposals": [],
                    "growth_awareness": {},
                    "errors": [],
                    "relationship_input_source": "CANONICAL_RELATIONSHIP_SCOPE_REQUIRED",
                }

            orchestrator = self._get_evolution_orchestrator()

            timeline_summary: dict[str, Any] = {}
            timeline_diary: dict[str, Any] = {}
            ports = self.application_ports
            try:
                if not isinstance(ports, TribeApplicationPorts):
                    raise RuntimeError("TRIBE_APPLICATION_PORTS_NOT_CONFIGURED")
                timeline = ports.require("emotion_timeline")()
                timeline_summary = timeline.rebuild_summary(window_ms=24 * 3600 * 1000)
                timeline_diary = timeline.build_daily_diary(window_ms=24 * 3600 * 1000)
            except Exception as exc:
                self._record_evolution_failure(
                    user_id=user_id,
                    operation="READ_EMOTION_TIMELINE",
                    exc=exc,
                )

            # A background evolution intent identifies Natsume's persona, not
            # one exact relationship scope.  Canonical Life relationship
            # heads are scoped by workspace + relationship subject, so this
            # route must not substitute a global/legacy Bond score merely to
            # make a proposal look more personal.  Until a future caller
            # carries that exact scope, canonical runs deliberately withhold
            # relationship input.  This prevents Bond stage/score from
            # creating persona-growth candidates or relationship milestones.
            bond_state = None
            bond_summary: dict[str, Any] = {}
            relationship_input_source = "LEGACY_BOND_COMPATIBILITY"
            try:
                social_snapshot = get_social_brain_agent().resolve_bond_snapshot(person_id=user_id)
                bond_state = social_snapshot.get("bond_state_obj")
                bond_summary = dict(social_snapshot.get("bond_summary") or {})
                if not bond_summary:
                    bond_summary = get_social_brain_agent().resolve_scheduler_bond_summary(
                        session_id="default",
                        user_id=user_id,
                    )
            except Exception as exc:
                self._record_evolution_failure(
                    user_id=user_id,
                    operation="READ_BOND_SNAPSHOT",
                    exc=exc,
                )

            result = orchestrator.run_evolution_cycle(
                user_id=user_id,
                date=date,
                timeline_summary=timeline_summary,
                timeline_diary=timeline_diary,
                bond_state=bond_state,
                bond_summary=bond_summary,
            )
            proposals: list[dict[str, Any]] = []
            for growth_result in list(
                getattr(result, "growth_results", None) or []
            ):
                proposal = self._stage_persona_growth_candidate(
                    user_id=user_id,
                    source_kind=str(
                        growth_result.get("type")
                        or "evolution_cycle"
                    ),
                    growth_result=growth_result,
                )
                if proposal.get("status") != "NO_CANDIDATE":
                    proposals.append(proposal)
            runtime_logger.info(
                "evolution cycle finished: date=%s "
                "traits_changed=%s proposals=%d errors=%d",
                date,
                result.traits_changed,
                len(proposals),
                len(result.errors),
            )
            return {
                "status": "OK",
                "cycle_id": result.cycle_id,
                "traits_changed": result.traits_changed,
                "proposal_only": True,
                "proposals": proposals,
                "growth_awareness": result.growth_awareness,
                "errors": result.errors,
                "relationship_input_source": relationship_input_source,
            }
        except Exception as exc:
            failure_ref = self._record_evolution_failure(
                user_id=user_id,
                operation="RUN_EVOLUTION_CYCLE",
                exc=exc,
            )
            runtime_logger.warning("evolution cycle failed (degraded): %s", exc)
            return {
                "status": "DEGRADED",
                "reason_code": "EVOLUTION_CYCLE_FAILED",
                "detail": str(exc),
                "failure_ref": failure_ref,
            }

    def resolve_persona_change_log(self, *, persona_user_id: str, limit: int = 5) -> list[dict[str, Any]]:
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

        entries = PersonaEngine.get_instance().get_change_log(
            _resolve_prefrontal_persona_id(persona_user_id),
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
            _resolve_prefrontal_persona_id(persona_user_id),
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
        # Persona drift 仅产生显式结果/后续 Feedback proposal。HardState 是
        # Host app-local frozen snapshot，Prefrontal 不得把 correction hint 写入其中。
        return payload

    def _stage_persona_growth_candidate(
        self,
        *,
        user_id: str,
        source_kind: str,
        growth_result: Any,
    ) -> dict[str, Any]:
        if isinstance(growth_result, Mapping):
            payload = dict(growth_result)
            guard_result = dict(
                payload.get("guard_result") or {}
            )
            candidate_delta = dict(
                payload.get("candidate_delta")
                or guard_result.get("candidate_delta")
                or {}
            )
            reason = str(
                payload.get("reason")
                or payload.get("skip_reason")
                or ""
            )
        else:
            guard_result = dict(
                getattr(growth_result, "guard_result", None) or {}
            )
            candidate_delta = dict(
                getattr(growth_result, "candidate_delta", None)
                or guard_result.get("candidate_delta")
                or {}
            )
            reason = str(
                getattr(growth_result, "reason", "")
                or getattr(growth_result, "skip_reason", "")
                or ""
            )
        if not candidate_delta:
            return {
                "status": "NO_CANDIDATE",
                "reason_code": (
                    reason or "PERSONA_GROWTH_CANDIDATE_EMPTY"
                ),
            }
        resolved_user_id = _resolve_prefrontal_persona_id(user_id)
        service = self.application_ports.require(
            "evolution_activation_service"
        )()
        current = self.resolve_persona_traits(
            persona_user_id=resolved_user_id
        )
        return dict(
            service.stage_persona_feedback(
                workspace_id=(
                    _resolve_prefrontal_persona_workspace_id()
                ),
                subject_id=resolved_user_id,
                source_kind=str(source_kind or "persona_growth"),
                current_traits=current.as_dict(),
                candidate_delta=candidate_delta,
                confidence=1.0,
            )
        )

    def _record_evolution_failure(
        self,
        *,
        user_id: str,
        operation: str,
        exc: Exception,
    ) -> str:
        provider = getattr(
            self.application_ports,
            "evolution_activation_service",
            None,
        )
        if not callable(provider):
            return ""
        try:
            return str(
                provider().record_pipeline_failure(
                    workspace_id=(
                        _resolve_prefrontal_persona_workspace_id()
                    ),
                    subject_kind="FEEDBACK_POLICY",
                    subject_id=_resolve_prefrontal_persona_id(
                        user_id
                    ),
                    operation=operation,
                    exc=exc,
                )
                or ""
            )
        except Exception:
            _logger.exception(
                "演化失败证据写入失败: user_id=%s operation=%s",
                user_id,
                operation,
            )
            return ""

    def run_growth_feedback_cycle(
        self,
        *,
        session_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> dict[str, Any]:
        if self._uses_canonical_affective_life():
            # Raw interaction text plus a legacy session→Bond identity is not
            # admissible feedback evidence for Persona evolution.  Canonical
            # low-risk adaptation is derived from durable, relationship-scoped
            # aftereffects; this compatibility hook must not invent a second
            # learning authority while that input is absent.
            return {
                "status": "RETIRED",
                "reason_code": "LEGACY_SESSION_FEEDBACK_GROWTH_RETIRED",
                "feedback_source": "CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED",
                "user_id": "",
                "feedback_count": 0,
                "applied": False,
                "proposal": {
                    "status": "NO_CANDIDATE",
                    "reason_code": "LEGACY_SESSION_FEEDBACK_GROWTH_RETIRED",
                },
                "triggered_rules": [],
            }

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
                # PersonaGrowthTracker consumes a bounded interaction
                # spectrum, not raw conversation bodies.  Keeping only the
                # count preserves proposal-only authority and fixes the old
                # user_message/assistant_reply signature drift.
                interaction_stats={"response_feedback": len(feedbacks)},
                feedback_collector=collector,
            )
        proposal = (
            self._stage_persona_growth_candidate(
                user_id=user_id,
                source_kind="response_feedback",
                growth_result=growth_result,
            )
            if growth_result is not None
            else {"status": "NO_CANDIDATE"}
        )
        return {
            "user_id": user_id,
            "feedback_count": len(feedbacks),
            "applied": False,
            "proposal": proposal,
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

        if self._uses_canonical_affective_life():
            # This legacy gate reads global endocrine/immune state and Bond,
            # then writes a process-local trace. Neither is admissible
            # relationship evidence after the canonical Life cutover.
            return self._retired_canonical_life_result(
                operation="evaluate_autobiographical_memory_gate",
                decision="BLOCK",
            )

        user_id = _resolve_prefrontal_persona_id(persona_user_id)
        narrative_text = str(narrative or "").strip()
        ctx = dict(context or {})
        trace_id = f"autobio_gate_{now_ms()}"
        registry = self.state_registry
        vitals = registry.aggregate_vital_signs()
        gate_state = str(vitals.get("gate_state") or "open").strip().lower()
        immune_risk = str(vitals.get("immune_risk_level") or "none").strip().lower()
        stress_index = float(vitals.get("stress_index") or 0.0)
        bond_ctx = get_social_brain_agent().get_bond_context(
            session_id=str(ctx.get("session_id") or "default"),
            user_id=_resolve_prefrontal_bond_user_id(ctx.get("user_id"), session_id=str(ctx.get("session_id") or "default")),
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

        if self._uses_canonical_affective_life():
            return {
                **self._retired_canonical_life_result(
                    operation="commit_autobiographical_memory",
                    decision="BLOCK",
                ),
                "gate": self._retired_canonical_life_result(
                    operation="evaluate_autobiographical_memory_gate",
                    decision="BLOCK",
                ),
            }

        gate = self.evaluate_autobiographical_memory_gate(
            persona_user_id=persona_user_id,
            narrative=narrative,
            source_agent=source_agent,
            context=context,
        )
        if str(gate.get("decision") or "").upper() != "ALLOW":
            return {"status": "BLOCKED", "gate": gate}
        write_result = get_hippocampus_agent().write_autobiographical_memory(
            person_id=_resolve_prefrontal_persona_id(persona_user_id),
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
        if self._uses_canonical_affective_life():
            # A legacy Bond stage is neither an exact canonical relationship
            # scope nor admissible evidence for Persona evolution.  Keep the
            # public compatibility method harmless in case an old event bus
            # subscriber or adapter invokes it after the production cutover.
            return {
                "applied": False,
                "reason": "LEGACY_BOND_STAGE_GROWTH_RETIRED",
                "proposal": {
                    "status": "NO_CANDIDATE",
                    "reason_code": "LEGACY_BOND_STAGE_GROWTH_RETIRED",
                },
            }
        tracker = self._get_persona_growth_tracker()
        if tracker is None:
            return {"applied": False, "reason": "tracker_unavailable"}
        try:
            result = tracker.on_stage_promoted(
                user_id=_resolve_prefrontal_persona_id(user_id),
                from_stage=str(from_stage or "FRIEND"),
                to_stage=str(to_stage or "FRIEND"),
                ts_ms=int(ts_ms),
            )
            proposal = self._stage_persona_growth_candidate(
                user_id=user_id,
                source_kind="bond_stage_promotion",
                growth_result=result,
            )
            return {
                "applied": False,
                "reason": str(getattr(result, "reason", "") or ""),
                "proposal": proposal,
            }
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="STAGE_PROMOTION_FEEDBACK",
                exc=exc,
            )
            _logger.exception(
                "阶段成长 proposal 持久化失败: user_id=%s",
                user_id,
            )
            raise RuntimeError(
                "PREFRONTAL_STAGE_GROWTH_PROPOSAL_FAILED"
            ) from exc

    def apply_milestone_growth(
        self,
        *,
        user_id: str,
        milestone_key: str,
        ts_ms: int,
    ) -> dict[str, Any]:
        if self._uses_canonical_affective_life():
            # Legacy milestones are derived from Bond/EventBus state and do
            # not carry canonical relationship scope or aftereffect refs.
            # Keep this public compatibility entry harmless after cutover.
            return {
                "applied": False,
                "reason": "LEGACY_UNSCOPED_MILESTONE_GROWTH_RETIRED",
                "proposal": {
                    "status": "NO_CANDIDATE",
                    "reason_code": "LEGACY_UNSCOPED_MILESTONE_GROWTH_RETIRED",
                },
            }
        tracker = self._get_persona_growth_tracker()
        if tracker is None:
            return {"applied": False, "reason": "tracker_unavailable"}
        try:
            result = tracker.on_milestone(
                user_id=_resolve_prefrontal_persona_id(user_id),
                milestone_key=str(milestone_key or ""),
                ts_ms=int(ts_ms),
            )
            proposal = self._stage_persona_growth_candidate(
                user_id=user_id,
                source_kind="relationship_milestone",
                growth_result=result,
            )
            return {
                "applied": False,
                "reason": str(getattr(result, "reason", "") or ""),
                "proposal": proposal,
            }
        except Exception as exc:
            self._record_evolution_failure(
                user_id=user_id,
                operation="MILESTONE_FEEDBACK",
                exc=exc,
            )
            _logger.exception(
                "里程碑成长 proposal 持久化失败: user_id=%s",
                user_id,
            )
            raise RuntimeError(
                "PREFRONTAL_MILESTONE_GROWTH_PROPOSAL_FAILED"
            ) from exc

    def _get_persona_growth_tracker(self) -> Any | None:
        from host_brain.persona.persona_growth import PersonaGrowthTracker  # noqa: PLC0415

        if self._persona_growth_tracker is None:
            try:
                self._persona_growth_tracker = PersonaGrowthTracker()
            except Exception as exc:
                self._record_evolution_failure(
                    user_id="natsume_ai",
                    operation="INITIALIZE_PERSONA_GROWTH_TRACKER",
                    exc=exc,
                )
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
        resolved_user_id = _resolve_prefrontal_persona_id(user_id)
        resolved_lookback_days = max(1, int(lookback_days or 7))

        if self._uses_canonical_affective_life():
            # This legacy weekly path consumes raw episodic text and carries
            # neither an exact workspace/relationship subject nor durable
            # accepted-response aftereffects.  Canonical companion growth may
            # only use the scoped aftereffect pipeline; stop before importing
            # the analyzer, constructing a model port, or staging a Persona
            # proposal.
            return {
                "status": "DEFERRED",
                "reason_code": "CANONICAL_RELATIONAL_AFTEREFFECT_REQUIRED",
                "user_id": resolved_user_id,
                "lookback_days": resolved_lookback_days,
                "pattern_result": {},
                "growth_result": {},
                "proposal": {
                    "status": "NO_CANDIDATE",
                    "reason_code": "LEGACY_EMERGENT_GROWTH_RETIRED",
                },
            }

        from host_brain.persona.interaction_pattern_analyzer import InteractionPatternAnalyzer  # noqa: PLC0415
        from host_brain.persona.persona_engine import PersonaEngine  # noqa: PLC0415

        if llm_fn is None:
            return {
                "status": "SKIPPED",
                "reason_code": "EMERGENT_GROWTH_LLM_REQUIRED",
                "user_id": resolved_user_id,
                "lookback_days": resolved_lookback_days,
                "pattern_result": {},
                "growth_result": {},
            }

        persona_engine = PersonaEngine.get_instance()
        analyzer = InteractionPatternAnalyzer(llm_fn=llm_fn, strict_llm=True)
        pattern_result = analyzer.analyze(
            user_id=resolved_user_id,
            episodic_store=episodic_store,
            current_traits=persona_engine.get_traits(resolved_user_id),
            lookback_days=resolved_lookback_days,
        )
        if str(getattr(pattern_result, "analysis_method", "") or "").strip().lower() != "llm":
            return {
                "status": "SKIPPED",
                "reason_code": "EMERGENT_GROWTH_LLM_REQUIRED",
                "user_id": resolved_user_id,
                "lookback_days": resolved_lookback_days,
                "pattern_result": dict(pattern_result.as_dict() if hasattr(pattern_result, "as_dict") else {}),
                "growth_result": {},
            }
        growth_result = self._get_emergent_growth_engine().apply(
            user_id=resolved_user_id,
            pattern_result=pattern_result,
        )
        proposal = self._stage_persona_growth_candidate(
            user_id=resolved_user_id,
            source_kind="emergent_weekly",
            growth_result=growth_result,
        )
        return {
            "status": (
                "PROPOSED"
                if proposal.get("status") == "STAGED"
                else "SKIPPED"
            ),
            "user_id": resolved_user_id,
            "lookback_days": resolved_lookback_days,
            "pattern_result": dict(pattern_result.as_dict() if hasattr(pattern_result, "as_dict") else {}),
            "growth_result": dict(growth_result.as_dict() if hasattr(growth_result, "as_dict") else {}),
            "proposal": proposal,
        }

    def resolve_emergent_growth_status(self, *, user_id: str, recent_limit: int = 5) -> dict[str, Any]:
        recent = max(1, int(recent_limit or 5))
        resolved_user_id = _resolve_prefrontal_persona_id(user_id)
        if self._uses_canonical_affective_life():
            return {
                "status": "RETIRED",
                "reason_code": "LEGACY_EMERGENT_GROWTH_RETIRED",
                "user_id": resolved_user_id,
                "recent_limit": recent,
                "growth_summary": {},
                "recent_growth_log": [],
            }
        engine = self._get_emergent_growth_engine()
        growth_log = [record.as_dict() for record in engine.get_growth_log(resolved_user_id)[-recent:]]
        return {
            "status": "OK",
            "user_id": resolved_user_id,
            "growth_summary": engine.get_growth_summary(resolved_user_id),
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

def get_prefrontal_agent() -> PrefrontalAgent:
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        require_active_tribe_agent,
    )

    return require_active_tribe_agent(
        "prefrontal_agent",
        expected_type=PrefrontalAgent,
    )


__all__ = ["PrefrontalAgent", "get_prefrontal_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
from host_brain.tribe.events import emit_tribe_event  # noqa: E402, F401
from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: E402, F401
from host_brain.tribe.tribe_state import get_global_registry  # noqa: E402, F401
