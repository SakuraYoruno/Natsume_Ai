"""Chat Prompt Runtime Payload Builder。

将聊天运行时 payload 的组装收敛为单一流水线。每个 ``.with_*`` 阶段：

    1. 只读 ``PromptPayloadContext``，不跨阶段共享局部变量
    2. 只通过 ``self._payload`` 传递中间结果
    3. 外层统一由 ``soft_fail`` 守护

使用方式::

    payload = (
        PromptPayloadBuilder(ctx)
        .with_core_payload()
        .with_intelligence()
        .with_emotion_bridge()
        .with_emotion_timeline()
        .with_persona_expression()
        .with_self_reflection()
        .with_bond_narrative()
        .with_bond_style()
        .with_motive_state()
        .with_persona_expression_postprocess()
        .with_cortex_supervisor()
        .build()
    )

"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.context.persona_expression import (
    _apply_activity_monitor_to_persona_expression_hint,
    _apply_motive_state_to_persona_expression_hint,
    _apply_offline_state_to_persona_expression_hint,
    _apply_runtime_mode_to_persona_expression_hint,
    _apply_scene_awareness_to_persona_expression_hint,
)
from host_brain.app.chat_context_style import normalize_node_style_bindings
from host_brain.app.chat_generation_payload import (
    build_chat_generation_prompt_payload,
    build_runtime_mode_flags,
)
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.core.soft_fail import soft_fail

logger = logging.getLogger(__name__)
_SELF_REFLECTION_TURN_COUNTER = 0


# ──────────────────────────────────────────────────────────────────────
#  Context / Result 数据类
# ──────────────────────────────────────────────────────────────────────


@dataclass
class PromptPayloadContext:
    """流水线只读输入上下文。

    只承载 chat runtime payload 的外部入参及 generation_payload 的
    预先计算结果，不包含任何可变中间状态。
    """

    runtime_input: Any
    style_bindings: Mapping[str, Mapping[str, Any]] | None
    # 以下字段由 _prepare_context 预计算（避免每个 stage 重复解析）
    render_target_lang: str = ""
    resolved_style_bindings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    generation_payload: Mapping[str, Any] = field(default_factory=dict)
    full_emotion_ctx: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class PromptPayloadResult:
    """流水线输出。"""

    data: dict[str, Any]
    trace: list[str]


# ──────────────────────────────────────────────────────────────────────
#  PromptPayloadBuilder
# ──────────────────────────────────────────────────────────────────────


class PromptPayloadBuilder:
    """Chat runtime payload 的阶段化构造器。"""

    def __init__(self, ctx: PromptPayloadContext) -> None:
        self._ctx = ctx
        self._payload: dict[str, Any] = {}
        self._trace: list[str] = []
        # 跨 stage 共享的瞬态中间结果（不暴露给外部）
        self._tl_summary: dict[str, Any] = {}
        self._tl_diary: dict[str, Any] = {}
        self._emotion_reunion_type: str = ""
        self._sr_alignment_snapshot: dict[str, Any] = {}
        self._prefrontal_agent_for_sr: Any = None

    # ---- stage 1: 核心 runtime_payload 与 runtime_mode/memory_persona ----
    def with_core_payload(self) -> "PromptPayloadBuilder":
        with soft_fail("core_payload", fallback=None):
            _core_payload_stage(self._ctx, self._payload)
        self._trace.append("core_payload")
        return self

    # ---- stage 2: intelligent_runtime 扩展 ----
    def with_intelligence(self) -> "PromptPayloadBuilder":
        with soft_fail("prompt_intelligence", fallback=None):
            _intelligence_stage(self._ctx, self._payload)
        self._trace.append("intelligence")
        return self

    # ---- stage 4: 情感子系统桥接（vad/echo/inner_monologue/...）----
    def with_emotion_bridge(self) -> "PromptPayloadBuilder":
        with soft_fail("emotion_bridge", fallback=None):
            _emotion_bridge_stage(self._ctx, self._payload)
        self._trace.append("emotion_bridge")
        return self

    # ---- stage 5: emotion_timeline ----
    def with_emotion_timeline(self) -> "PromptPayloadBuilder":
        with soft_fail("emotion_timeline", fallback=None):
            self._tl_summary, self._tl_diary = _emotion_timeline_stage(self._payload)
        self._trace.append("emotion_timeline")
        return self

    # ---- stage 6: PersonaExpressionAdapter 主块 ----
    def with_persona_expression(self) -> "PromptPayloadBuilder":
        with soft_fail("persona_expression", fallback=None):
            self._emotion_reunion_type = _persona_expression_stage(
                ctx=self._ctx,
                payload=self._payload,
            )
        self._trace.append("persona_expression")
        return self

    # ---- stage 7: SelfReflection ----
    def with_self_reflection(self) -> "PromptPayloadBuilder":
        with soft_fail("self_reflection", fallback=None):
            (
                self._sr_alignment_snapshot,
                self._prefrontal_agent_for_sr,
            ) = _self_reflection_stage(
                ctx=self._ctx,
                payload=self._payload,
                emotion_reunion_type=self._emotion_reunion_type,
                tl_summary=self._tl_summary,
                tl_diary=self._tl_diary,
            )
        self._trace.append("self_reflection")
        return self

    # ---- stage 8: BondNarrative ----
    def with_bond_narrative(self) -> "PromptPayloadBuilder":
        with soft_fail("bond_narrative", fallback=None):
            _bond_narrative_stage(self._payload)
        self._trace.append("bond_narrative")
        return self

    # ---- stage 9: BondAwareStyle ----
    def with_bond_style(self) -> "PromptPayloadBuilder":
        with soft_fail("bond_style", fallback=None):
            _bond_style_stage(self._ctx, self._payload)
        self._trace.append("bond_style")
        return self

    # ---- stage 10: MotiveEngine + SelfReflection 对齐 ----
    def with_motive_state(self) -> "PromptPayloadBuilder":
        with soft_fail("motive_state", fallback=None):
            _motive_state_stage(
                ctx=self._ctx,
                payload=self._payload,
                sr_alignment_snapshot=self._sr_alignment_snapshot,
                prefrontal_agent_for_sr=self._prefrontal_agent_for_sr,
            )
        self._trace.append("motive_state")
        return self

    # ---- stage 11: persona_expression_hint 后处理链 ----
    def with_persona_expression_postprocess(self) -> "PromptPayloadBuilder":
        with soft_fail("persona_expression_postprocess", fallback=None):
            _persona_expression_postprocess_stage(self._payload)
        self._trace.append("persona_expression_postprocess")
        return self

    # ---- stage 12: CortexAgent Supervisor 收口 ----
    def with_cortex_supervisor(self) -> "PromptPayloadBuilder":
        with soft_fail("cortex_supervisor", fallback=None):
            _cortex_supervisor_stage(self._ctx, self._payload)
        self._trace.append("cortex_supervisor")
        return self

    # ---- 终止 ----
    def build(self) -> PromptPayloadResult:
        return PromptPayloadResult(data=self._payload, trace=list(self._trace))


# ──────────────────────────────────────────────────────────────────────
#  Stage 实现
#  说明：每个 stage 的实现与原 build_chat_prompt_runtime_payload 中
#        对应段落保持字节级等价，仅做了参数化/闭包消除。
# ──────────────────────────────────────────────────────────────────────


def prepare_context(
    *,
    runtime_input: Any,
    style_bindings: Mapping[str, Mapping[str, Any]] | None,
) -> PromptPayloadContext:
    """入口预处理：类型校验 + style bindings + slot 合并 + generation_payload。"""
    from host_brain.app.chat_runtime_input import ChatRuntimeInput  # noqa: PLC0415

    if not isinstance(runtime_input, ChatRuntimeInput):
        raise TypeError("runtime_input must be ChatRuntimeInput")

    render_target_lang = str(runtime_input.render_target_lang or "").strip().lower()
    resolved_style_bindings = (
        normalize_node_style_bindings(style_bindings)
        if style_bindings is not None
        else {}
    )

    generation_payload = build_chat_generation_prompt_payload(
        request_id=str(runtime_input.request_id or "").strip(),
        session_id=str(runtime_input.session_id or "").strip(),
        thread_id=runtime_input.thread_id,
        user_message=str(runtime_input.user_message or ""),
        retrieval_summary=dict(runtime_input.retrieval_summary or {}),
        retrieval_evidence_items=list(runtime_input.retrieval_evidence_items or []),
        style_bindings=resolved_style_bindings,
        bond_summary=dict(runtime_input.bond_summary or {}),
        emotion_context=dict(runtime_input.emotion_state or {}),
        emotion_snapshot=dict(runtime_input.emotion_snapshot or {}),
        render_target_lang=render_target_lang or None,
    )

    return PromptPayloadContext(
        runtime_input=runtime_input,
        style_bindings=style_bindings,
        render_target_lang=render_target_lang,
        resolved_style_bindings=resolved_style_bindings,
        generation_payload=generation_payload,
        full_emotion_ctx=dict(generation_payload.get("emotion_context") or {}),
    )


def _core_payload_stage(ctx: PromptPayloadContext, payload: dict[str, Any]) -> None:
    """核心 runtime_payload 构造 + runtime_mode/memory_persona 注入。"""
    generation_payload = ctx.generation_payload
    runtime_input = ctx.runtime_input

    prompt_context = dict(generation_payload.get("prompt_context") or {})
    prompt_context_validation = dict(generation_payload.get("prompt_context_validation") or {})
    prompt_context_observability = dict(generation_payload.get("prompt_context_observability") or {})
    runtime_payload = runtime_input.to_runtime_payload(
        prompt_context=prompt_context,
        prompt_context_validation=prompt_context_validation,
        prompt_context_observability=prompt_context_observability,
    )
    runtime_payload["style_bindings"] = ctx.resolved_style_bindings

    runtime_mode_flags = build_runtime_mode_flags(prompt_context)
    if runtime_mode_flags:
        runtime_payload["runtime_mode"] = runtime_mode_flags
        runtime_payload["sys_mode"] = (
            str(runtime_mode_flags.get("sys_mode") or "normal").strip().lower() or "normal"
        )
        activity_monitor = runtime_mode_flags.get("activity_monitor")
        if isinstance(activity_monitor, Mapping):
            runtime_payload["activity_monitor"] = dict(activity_monitor)

    graph_payload = dict(runtime_payload.get("graph") or {})
    scene_awareness_raw = graph_payload.get("scene_awareness")
    scene_awareness = (
        dict(scene_awareness_raw) if isinstance(scene_awareness_raw, Mapping) else {}
    )
    if scene_awareness:
        runtime_payload["scene_awareness"] = scene_awareness

    # 合并进外层 payload，保持引用一致
    payload.clear()
    payload.update(runtime_payload)


def _intelligence_stage(ctx: PromptPayloadContext, payload: dict[str, Any]) -> None:
    try:
        from host_brain.app.intelligent_runtime import build_prompt_intelligence  # noqa: PLC0415
    except Exception as exc:
        logger.debug("prompt intelligence degraded: %s", exc)
        return
    try:
        payload.update(
            build_prompt_intelligence(
                request_id=str(ctx.runtime_input.request_id or "").strip(),
                runtime_input=ctx.runtime_input,
            )
        )
    except Exception as exc:
        logger.debug("prompt intelligence degraded: %s", exc)


def _emotion_bridge_stage(ctx: PromptPayloadContext, payload: dict[str, Any]) -> None:
    _full_emotion_ctx = ctx.full_emotion_ctx
    if _full_emotion_ctx.get("vad_vector"):
        payload["emotion_vector"] = _full_emotion_ctx["vad_vector"]
    if _full_emotion_ctx.get("emotion_echo"):
        payload["emotion_echo"] = _full_emotion_ctx["emotion_echo"]
    if _full_emotion_ctx.get("inner_monologue"):
        payload["inner_monologue"] = _full_emotion_ctx["inner_monologue"]
    if _full_emotion_ctx.get("user_emotion_profile"):
        payload["user_emotion_profile"] = _full_emotion_ctx["user_emotion_profile"]
    if _full_emotion_ctx.get("triggered_emotions"):
        payload["triggered_emotions"] = _full_emotion_ctx["triggered_emotions"]
    if _full_emotion_ctx.get("endocrine_state"):
        payload["endocrine_state"] = dict(_full_emotion_ctx.get("endocrine_state") or {})

    if not isinstance(payload.get("endocrine_state"), Mapping):
        try:
            from host_brain.app.dependencies import get_endocrine_state  # noqa: PLC0415

            payload["endocrine_state"] = dict(
                get_endocrine_state(
                    emotion_tag=str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL"),
                    emotion_intensity=float(_full_emotion_ctx.get("emotion_intensity") or 0.0),
                )
                or {}
            )
        except Exception:
            pass

    if isinstance(payload.get("endocrine_state"), Mapping):
        merged_emotion_context = dict(payload.get("emotion_context") or {})
        merged_emotion_context["endocrine_state"] = dict(payload.get("endocrine_state") or {})
        payload["emotion_context"] = merged_emotion_context


def _emotion_timeline_stage(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    _tl_summary: dict[str, Any] = {}
    _tl_diary: dict[str, Any] = {}
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        _tl_snapshot = get_limbic_agent().resolve_emotion_timeline_snapshot(
            summary_window_ms=300_000,
            diary_window_ms=24 * 3600 * 1000,
        )
        _tl_summary = dict(_tl_snapshot.get("summary") or {})
        _tl_diary = dict(_tl_snapshot.get("diary") or {})
        if _tl_summary and _tl_summary.get("entry_count", 0) > 0:
            payload["emotion_timeline"] = _tl_summary
    except Exception:
        pass
    return _tl_summary, _tl_diary


def _persona_expression_stage(
    *, ctx: PromptPayloadContext, payload: dict[str, Any]
) -> str:
    """PersonaExpressionAdapter 主块（EmotionPersistence + OfflineInnerWorld + Silence + BodySense + EmergentGrowth）。

    返回值：``_emotion_reunion_type``，供后续 SelfReflection stage 使用。
    """
    _full_emotion_ctx = ctx.full_emotion_ctx
    runtime_input = ctx.runtime_input
    generation_payload = ctx.generation_payload
    request_id = str(runtime_input.request_id or "").strip()
    session_id = str(runtime_input.session_id or "").strip()

    _emotion_reunion_type = ""
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        _prefrontal_agent = get_prefrontal_agent()
        _limbic_agent_for_ep = get_limbic_agent()
        _persona_user_id = get_canonical_bond_user_id()
        _vad = _full_emotion_ctx.get("vad_vector") or {}
        _cur_valence = float(_vad.get("valence") or 0.0)
        _cur_arousal = float(_vad.get("arousal") or 0.0)
        _cur_dominance = float(_vad.get("dominance") or 0.0)

        try:
            _ep_payload = dict(
                _limbic_agent_for_ep.resolve_persistent_emotion_state(
                    person_id=_persona_user_id,
                    valence=_cur_valence,
                    arousal=_cur_arousal,
                    dominance=_cur_dominance,
                )
                or {}
            )
            _cur_valence = float(_ep_payload.get("valence") or _cur_valence)
            _cur_arousal = float(_ep_payload.get("arousal") or _cur_arousal)
            _cur_dominance = float(_ep_payload.get("dominance") or _cur_dominance)
            _emotion_reunion_type = str(_ep_payload.get("reunion_type") or "").strip()
            if _emotion_reunion_type:
                payload["emotion_reunion_type"] = _emotion_reunion_type
        except Exception as _ep_exc:
            logger.debug("EmotionPersistence 接入降级: %s", _ep_exc)

        try:
            from host_brain.tribe.default_mode_agent import resolve_recent_foreground_interaction_state  # noqa: PLC0415

            _recent_foreground = resolve_recent_foreground_interaction_state(current_ts_ms=int(time.time() * 1000))
        except Exception:
            _recent_foreground = {"active": False}

        try:
            if not bool(_recent_foreground.get("active")):
                from host_brain.tribe.limbic_agent import get_limbic_agent as _get_limbic  # noqa: PLC0415

                _iw_snapshot = dict(_get_limbic().on_user_awaken(person_id=_persona_user_id) or {})
                _iw_offline_hours = float(_iw_snapshot.get("offline_hours") or 0.0)
                _iw_hint = str(_iw_snapshot.get("awakening_hint") or "")
                if _iw_offline_hours >= 1.0 or _iw_hint:
                    payload["offline_state"] = _iw_snapshot
                    logger.debug(
                        "OfflineInnerWorld 唤醒快照注入: user=%s offline_hours=%.1f hint=%s",
                        _persona_user_id,
                        _iw_offline_hours,
                        _iw_hint[:30] if _iw_hint else "",
                    )
            else:
                logger.debug(
                    "OfflineInnerWorld 唤醒快照跳过: recent foreground chat active",
                )
        except Exception as _iw_exc:
            logger.debug("OfflineInnerWorld 接入降级: %s", _iw_exc)

        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent as _get_limbic  # noqa: PLC0415

            _silence_state = dict(_get_limbic().resolve_silence_state())
            _silence_level = (
                str(_silence_state.get("willingness_level") or "NORMAL").strip().upper() or "NORMAL"
            )
            _topic_resistance_active = bool(_silence_state.get("topic_resistance_active"))
            if _silence_level != "NORMAL" or _topic_resistance_active:
                payload["silence_state"] = _silence_state
                logger.debug(
                    "EmotionalSilenceEngine 状态注入: user=%s willingness=%.2f level=%s",
                    _persona_user_id,
                    float(_silence_state.get("response_willingness") or 0.0),
                    _silence_level,
                )
        except Exception as _silence_exc:
            logger.debug("EmotionalSilenceEngine 接入降级: %s", _silence_exc)

        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent as _get_limbic  # noqa: PLC0415

            _limbic_agent = _get_limbic()
            _bs_result = _limbic_agent.capture_conversation_body_sense(
                request_id=request_id,
                session_id=session_id,
                person_id=_persona_user_id,
                emotion_tag=_full_emotion_ctx.get("emotion_tag"),
                mood_state=_full_emotion_ctx.get("mood_state"),
                offline_loneliness=(payload.get("offline_state") or {}).get("loneliness_level"),
            )
            _bs_payload = dict(_bs_result.get("body_sense") or {})
            if bool(_bs_result.get("is_active")):
                payload["body_sense"] = _bs_payload
                logger.debug(
                    "BodySenseModel 状态注入: user=%s fatigue=%s drowsiness=%s loneliness=%s",
                    _persona_user_id,
                    _bs_payload.get("fatigue_level"),
                    _bs_payload.get("drowsiness_level"),
                    _bs_payload.get("loneliness_level"),
                )
            try:
                _bs_penalty = float(_bs_result.get("willingness_penalty") or 0.0)
            except (TypeError, ValueError):
                _bs_penalty = 0.0
            if _bs_penalty > 0.0:
                try:
                    _limbic_agent.apply_body_sense_penalty(penalty=_bs_penalty)
                except Exception:
                    pass
        except Exception as _bs_exc:
            logger.debug("BodySenseModel 接入降级: %s", _bs_exc)

        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent as _get_pf  # noqa: PLC0415

            _pf_agent = _get_pf()
            _eg_engine = _pf_agent._get_emergent_growth_engine()
            _eg_note = (
                _eg_engine.get_latest_growth_note(_persona_user_id)
                if _eg_engine is not None
                else None
            )
            if _eg_note:
                _existing_pulse = payload.get("self_reflection_pulse")
                if isinstance(_existing_pulse, dict):
                    _existing_pulse["emergent_growth_note"] = _eg_note
                else:
                    payload["emergent_growth_note"] = _eg_note
                logger.debug(
                    "EmergentGrowthEngine 成长叙事注入（via PrefrontalAgent）: user=%s note='%s'",
                    _persona_user_id,
                    _eg_note[:40],
                )
        except Exception as _eg_exc:
            logger.debug("EmergentGrowthEngine 接入降级: %s", _eg_exc)

        _bond_stage = "STRANGER"
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            _user_id = get_canonical_bond_user_id()
            _bond_stage = get_social_brain_agent().resolve_bond_stage(
                person_id=_user_id, default="STRANGER"
            )
        except Exception:
            pass
        _user_tag = str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL").strip().upper()
        _user_msg_for_pace = str(generation_payload.get("user_message") or "").strip()
        _persona_expression_hint = dict(
            _prefrontal_agent.resolve_persona_expression_hint(
                persona_user_id=_persona_user_id,
                bond_stage=_bond_stage,
                user_emotion_tag=_user_tag,
                user_message=_user_msg_for_pace,
                valence=_cur_valence,
                arousal=_cur_arousal,
                dominance=_cur_dominance,
                ts_ms=int(_vad.get("ts_ms") or 0),
            )
            or {}
        )
        payload["persona_expression_hint"] = _persona_expression_hint
    except (ImportError, AttributeError, Exception) as _pe_exc:
        logger.debug("PersonaExpressionAdapter 接入降级: %s", _pe_exc)

    return _emotion_reunion_type


def _self_reflection_stage(
    *,
    ctx: PromptPayloadContext,
    payload: dict[str, Any],
    emotion_reunion_type: str,
    tl_summary: dict[str, Any],
    tl_diary: dict[str, Any],
) -> tuple[dict[str, Any], Any]:
    _sr_alignment_snapshot: dict[str, Any] = {}
    _prefrontal_agent_for_sr = None
    _full_emotion_ctx = ctx.full_emotion_ctx
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        _prefrontal_agent = get_prefrontal_agent()
        _prefrontal_agent_for_sr = _prefrontal_agent
        global _SELF_REFLECTION_TURN_COUNTER
        _SELF_REFLECTION_TURN_COUNTER += 1
        _turn = _SELF_REFLECTION_TURN_COUNTER

        _sr_user_id = get_canonical_bond_user_id()
        _sr_bond_state = None
        prompt_context = dict(ctx.generation_payload.get("prompt_context") or {})
        _sr_bond_summary = dict((dict(prompt_context or {}).get("bond_summary") or {}))
        _sr_last_deep_talk_ts_ms = 0
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            _sr_social = get_social_brain_agent().resolve_bond_snapshot(
                person_id=_sr_user_id,
                bond_summary=_sr_bond_summary,
            )
            _sr_bond_state = _sr_social.get("bond_state_obj")
            if not _sr_bond_summary:
                _sr_bond_summary = dict(_sr_social.get("bond_summary") or {})
            _sr_last_deep_talk_ts_ms = int(_sr_social.get("last_event_ts_ms") or 0)
        except Exception:
            pass
        _sr_current_ts_ms = (
            int(_full_emotion_ctx.get("emotion_ts_ms") or 0) or int(time.time() * 1000)
        )
        if _sr_last_deep_talk_ts_ms <= 0 and emotion_reunion_type:
            if emotion_reunion_type == "reunion":
                _sr_last_deep_talk_ts_ms = _sr_current_ts_ms - 8 * 24 * 3600 * 1000
            elif emotion_reunion_type == "short_absence":
                _sr_last_deep_talk_ts_ms = _sr_current_ts_ms - 4 * 24 * 3600 * 1000
        try:
            _sr_persona_changes = list(
                _prefrontal_agent.resolve_persona_change_log(
                    persona_user_id=_sr_user_id,
                    limit=5,
                )
                or []
            )
        except Exception:
            _sr_persona_changes = []
        try:
            _sr_growth_awareness = dict(_prefrontal_agent.resolve_growth_awareness() or {})
        except Exception:
            _sr_growth_awareness = {}
        try:
            _sr_growth_summary = dict(
                _prefrontal_agent.resolve_growth_summary(
                    persona_user_id=_sr_user_id,
                    now_ts_ms=_sr_current_ts_ms,
                )
                or {}
            )
        except Exception:
            _sr_growth_summary = {}
        _tl_summary_for_sr = dict(payload.get("emotion_timeline") or tl_summary or {})
        _tl_diary_for_sr = dict(tl_diary or {})
        _sr_style_evolution: dict | None = None
        try:
            _sr_style_evolution = dict(_prefrontal_agent.resolve_style_evolution_summary() or {})
        except Exception:
            pass
        _sr_cycle_payload = _prefrontal_agent.run_self_reflection_cycle(
            turn_count=_turn,
            timeline_summary=_tl_summary_for_sr,
            timeline_diary=_tl_diary_for_sr,
            current_emotion=str(_full_emotion_ctx.get("emotion_tag") or ""),
            bond_state=_sr_bond_state,
            bond_summary=_sr_bond_summary,
            persona_changes=_sr_persona_changes,
            last_deep_talk_ts_ms=_sr_last_deep_talk_ts_ms,
            current_ts_ms=_sr_current_ts_ms,
            growth_awareness=_sr_growth_awareness,
            growth_summary=_sr_growth_summary,
            style_evolution=_sr_style_evolution,
        )
        _pulse = dict(_sr_cycle_payload.get("self_reflection_pulse") or {})
        _sr_alignment_snapshot = dict(_sr_cycle_payload.get("snapshot") or {})
        if _pulse:
            payload["self_reflection_pulse"] = _pulse
    except (ImportError, AttributeError, Exception) as _sr_exc:
        logger.debug("SelfReflection 接入降级: %s", _sr_exc)

    return _sr_alignment_snapshot, _prefrontal_agent_for_sr


def _bond_narrative_stage(payload: dict[str, Any]) -> None:
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        _user_id_bn = get_canonical_bond_user_id()
        _bn_bond_stage = "STRANGER"
        try:
            _bn_bond_stage = get_social_brain_agent().resolve_bond_stage(
                person_id=_user_id_bn,
                default="STRANGER",
            )
        except Exception:
            pass
        _bn_result = get_social_brain_agent().resolve_bond_narrative(
            user_id=_user_id_bn,
            bond_stage=_bn_bond_stage,
        )
        _bn_slot = _bn_result.get("prompt_slot") if isinstance(_bn_result, dict) else None
        if not _bn_slot and isinstance(_bn_result, Mapping):
            if _bn_result.get("narrative_items") or _bn_result.get("bond_narrative"):
                _bn_slot = dict(_bn_result)
        if _bn_slot:
            payload["bond_narrative"] = _bn_slot
    except (ImportError, AttributeError, Exception) as _bn_exc:
        logger.debug("BondNarrativeLayer 接入降级: %s", _bn_exc)


def _bond_style_stage(ctx: PromptPayloadContext, payload: dict[str, Any]) -> None:
    _full_emotion_ctx = ctx.full_emotion_ctx
    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        _prefrontal_agent = get_prefrontal_agent()
        _bas_user_id = get_canonical_bond_user_id()
        _bas_bond_stage = "STRANGER"
        _bas_intimacy = 0.0
        _bas_rift_active = False
        _bas_rift_severity = "medium"
        _bas_rift_progress = 0.0
        _bas_bond_trend = ""
        _bas_shared_hints: list[str] = []
        try:
            from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

            _bas_social = get_social_brain_agent().resolve_bond_snapshot(person_id=_bas_user_id)
            _bas_state = _bas_social.get("bond_state_obj")
            _bas_bond_stage = str(_bas_social.get("bond_stage") or "STRANGER")
            _bas_intimacy = float(_bas_social.get("intimacy_score") or 0.0)
            _bas_bond_trend = str(_bas_social.get("bond_trend") or "")
            if True:
                _bas_rift = _bas_social.get("rift")
                if _bas_rift is not None and getattr(_bas_rift, "is_active", lambda: False)():
                    _bas_rift_active = True
                    _bas_rift_severity = str(getattr(_bas_rift, "severity", "medium") or "medium")
                    _bas_rift_progress = float(getattr(_bas_rift, "repair_progress", 0.0) or 0.0)
                _bas_rift = getattr(_bas_state, "rift", None)
        except Exception:
            pass
        _bas_bn = payload.get("bond_narrative") or {}
        if isinstance(_bas_bn, dict):
            _bas_shared_hints = [str(h) for h in (_bas_bn.get("shared_memory_hints") or []) if h]
        _bas_mood = "neutral"
        _bas_vad = _full_emotion_ctx.get("vad_vector") or {}
        if _bas_vad:
            _bas_v = float(_bas_vad.get("valence") or 0.0)
            if _bas_v > 0.15:
                _bas_mood = "positive"
            elif _bas_v < -0.15:
                _bas_mood = "negative"
        _bas_natsume_mood = str(_full_emotion_ctx.get("emotion_tag") or "NEUTRAL").strip().upper()
        _bond_style_instruction = _prefrontal_agent.resolve_bond_style_instruction(
            bond_stage=_bas_bond_stage,
            intimacy_score=_bas_intimacy,
            mood_state=_bas_mood,
            natsume_mood=_bas_natsume_mood,
            rift_active=_bas_rift_active,
            rift_severity=_bas_rift_severity,
            rift_progress=_bas_rift_progress,
            bond_trend=_bas_bond_trend,
            shared_memory_hints=_bas_shared_hints or None,
        )
        if _bond_style_instruction:
            payload["bond_style_instruction"] = _bond_style_instruction
    except (ImportError, AttributeError, Exception) as _bas_exc:
        logger.debug("BondAwareStyleEngine 接入降级: %s", _bas_exc)


def _motive_state_stage(
    *,
    ctx: PromptPayloadContext,
    payload: dict[str, Any],
    sr_alignment_snapshot: dict[str, Any],
    prefrontal_agent_for_sr: Any,
) -> None:
    runtime_input = ctx.runtime_input
    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        _pending_topics = list(
            (payload.get("offline_state") or {}).get("anticipation_topics") or []
        )
        _shared_count = int(
            (payload.get("bond_summary") or {}).get("shared_memory_count") or 0
        )

        _limbic_agent = get_limbic_agent()
        payload["motive_state"] = _limbic_agent.compute_motive_state(
            silence_state=payload.get("silence_state"),
            body_sense=payload.get("body_sense"),
            offline_state=payload.get("offline_state"),
            bond_summary=payload.get("bond_summary"),
            self_reflection_pulse=payload.get("self_reflection_pulse"),
            emotion_context=payload.get("triggered_emotions"),
            pending_followup_topics=_pending_topics,
            shared_memory_count=_shared_count,
            user_message=str(runtime_input.user_message or ""),
            runtime_flags=payload.get("runtime_mode"),
        )
        payload["persona_expression_hint"] = _apply_motive_state_to_persona_expression_hint(
            payload.get("persona_expression_hint"),
            payload.get("motive_state"),
        )
        if (
            payload.get("self_reflection_pulse")
            and prefrontal_agent_for_sr is not None
            and sr_alignment_snapshot
        ):
            try:
                payload["self_reflection_pulse"] = prefrontal_agent_for_sr.align_self_reflection_pulse(
                    pulse=payload.get("self_reflection_pulse"),
                    snapshot=sr_alignment_snapshot,
                    offline_state=payload.get("offline_state"),
                    bond_summary=payload.get("bond_summary"),
                    motive_state=payload.get("motive_state"),
                    render_policy={},
                    runtime_flags=payload.get("runtime_mode"),
                )
            except Exception as _sr_align_exc:
                logger.debug("SelfReflection narrative alignment degraded: %s", _sr_align_exc)

    except (ImportError, AttributeError, Exception) as _motive_exc:
        logger.debug("MotiveEngine 接入降级: %s", _motive_exc)


def _persona_expression_postprocess_stage(payload: dict[str, Any]) -> None:
    if payload.get("persona_expression_hint") and payload.get("offline_state"):
        payload["persona_expression_hint"] = _apply_offline_state_to_persona_expression_hint(
            payload.get("persona_expression_hint"),
            payload.get("offline_state"),
        )

    if payload.get("persona_expression_hint") and payload.get("activity_monitor"):
        payload["persona_expression_hint"] = _apply_activity_monitor_to_persona_expression_hint(
            payload.get("persona_expression_hint"),
            payload.get("activity_monitor"),
        )

    if payload.get("persona_expression_hint") and payload.get("scene_awareness"):
        payload["persona_expression_hint"] = _apply_scene_awareness_to_persona_expression_hint(
            payload.get("persona_expression_hint"),
            payload.get("scene_awareness"),
        )

    if payload.get("persona_expression_hint") and payload.get("runtime_mode"):
        payload["persona_expression_hint"] = _apply_runtime_mode_to_persona_expression_hint(
            payload.get("persona_expression_hint"),
            payload.get("runtime_mode"),
        )


def _cortex_supervisor_stage(ctx: PromptPayloadContext, payload: dict[str, Any]) -> None:
    runtime_input = ctx.runtime_input
    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        _cortex = get_cortex_agent()
        _tribe_ctx = _cortex.collect_tribe_context(
            session_id=str(runtime_input.session_id or "").strip() or None,
            user_id=get_canonical_bond_user_id() or None,
        )
        _vital_signs = dict(_tribe_ctx.get("vital_signs") or {})
        if _vital_signs:
            payload["tribe_vital_signs"] = _vital_signs
        _homeostasis_report = dict(
            _tribe_ctx.get("homeostasis_report")
            or _vital_signs.get("homeostasis_report")
            or {}
        )
        if _homeostasis_report:
            payload["homeostasis_report"] = _homeostasis_report
        _default_mode_state = dict(_tribe_ctx.get("default_mode_state") or {})
        if _default_mode_state:
            payload["default_mode_state"] = _default_mode_state
        _mirror_neuron_state = dict(_tribe_ctx.get("mirror_neuron_state") or {})
        if _mirror_neuron_state:
            payload["mirror_neuron_state"] = _mirror_neuron_state
        _memory_layers = dict(_tribe_ctx.get("memory_layers") or {})
        if _memory_layers:
            payload["memory_layers"] = _memory_layers
        _language_dual_system = dict(_tribe_ctx.get("language_dual_system") or {})
        if _language_dual_system:
            payload["language_dual_system"] = _language_dual_system
        _circadian_state = dict(_tribe_ctx.get("circadian_state") or {})
        if _circadian_state:
            payload["circadian_state"] = _circadian_state
        _proprioception_state = dict(_tribe_ctx.get("proprioception_state") or {})
        if _proprioception_state:
            payload["proprioception_state"] = _proprioception_state
        _initiative_decision = dict(_tribe_ctx.get("initiative_decision") or {})
        if _initiative_decision:
            payload["initiative_decision"] = _initiative_decision
        _interoception_state = dict(_tribe_ctx.get("interoception_state") or {})
        if _interoception_state:
            payload["interoception_state"] = _interoception_state
        _motor_cortex_state = dict(_tribe_ctx.get("motor_cortex_state") or {})
        if _motor_cortex_state:
            payload["motor_cortex_state"] = _motor_cortex_state
        _cerebellum_state = dict(_tribe_ctx.get("cerebellum_state") or {})
        if _cerebellum_state:
            payload["cerebellum_state"] = _cerebellum_state
        _glymphatic_state = dict(_tribe_ctx.get("glymphatic_state") or {})
        if _glymphatic_state:
            payload["glymphatic_state"] = _glymphatic_state
        _vestibular_state = dict(_tribe_ctx.get("vestibular_state") or {})
        if _vestibular_state:
            payload["vestibular_state"] = _vestibular_state
        _tribe_route = _cortex.route_intent(
            message=str(runtime_input.user_message or ""),
            tribe_context=_tribe_ctx,
        )
        payload["tribe_route"] = _tribe_route
        _hedonic_state = dict(_tribe_ctx.get("hedonic_state") or {})
        if _hedonic_state:
            payload["hedonic_state"] = _hedonic_state
        _voice_emotion = dict(_tribe_ctx.get("voice_emotion") or {})
        if _voice_emotion and _voice_emotion.get("emotion_tag"):
            payload.setdefault("voice_emotion_inference", _voice_emotion)
        _high_loneliness = bool(_vital_signs.get("high_loneliness_flag", False))
        _loneliness_level = float(_vital_signs.get("loneliness_level") or 0.0)
        if _high_loneliness:
            payload["loneliness_hint"] = {
                "high_loneliness": True,
                "loneliness_level": round(_loneliness_level, 4),
                "suggest": "主动关怀，增加陪伴感表达，避免冷漠或过于简短的回复",
            }
        else:
            payload.setdefault(
                "loneliness_hint",
                {
                    "high_loneliness": False,
                    "loneliness_level": round(_loneliness_level, 4),
                },
            )

        _bond_trend = str(_vital_signs.get("bond_trend_hint") or "").strip().lower()
        if _bond_trend:
            payload["bond_trend_hint"] = _bond_trend
            if _bond_trend == "progress":
                payload.setdefault("bond_tone_adjust", "warmer")
            elif _bond_trend in ("regress_risk", "regress"):
                payload.setdefault("bond_tone_adjust", "cautious")

        _immune_risk = str(_vital_signs.get("immune_risk_level") or "none").strip().lower()
        _immune_block_count = int(_vital_signs.get("immune_block_count") or 0)
        if _immune_risk in ("medium", "high", "critical") or _immune_block_count >= 3:
            payload["immune_caution"] = {
                "risk_level": _immune_risk,
                "block_count": _immune_block_count,
                "suggest": "当前会话存在内容风险，回复需更加谨慎，避免触碰敏感话题",
            }

        try:
            from host_brain.tribe.language_system_agent import (  # noqa: PLC0415
                get_language_system_agent,
            )

            _lang_agent = get_language_system_agent()
            _bond_stage_for_lang = str(_vital_signs.get("bond_stage") or "stranger").strip().lower()
            _emotion_tag_for_lang = str(_vital_signs.get("emotion_tag") or "NEUTRAL").strip().upper()
            _loneliness_for_lang = float(_vital_signs.get("loneliness_level") or 0.0)
            _high_loneliness_for_lang = bool(_vital_signs.get("high_loneliness_flag", False))
            _language_style = _lang_agent.get_language_style(
                bond_stage=_bond_stage_for_lang,
                emotion_tag=_emotion_tag_for_lang,
                loneliness_level=_loneliness_for_lang,
                high_loneliness_flag=_high_loneliness_for_lang,
                user_text=str(runtime_input.user_message or ""),
                response_goal="chat_reply",
            )
            payload["language_style"] = _language_style
            _dual_system = dict(_language_style.get("dual_system") or {})
            if _dual_system:
                payload["language_dual_system"] = _dual_system
        except Exception as _lang_exc:
            logger.debug("LanguageSystemAgent.get_language_style 降级: %s", _lang_exc)

        # T-224 / T-235 / T-245 / T-256 / T-264 / T-265 / T-266 / T-277 / T-282 / T-285 / T-286
        # 注意：原函数里使用了未定义的 ``tribe_context`` 变量（很可能是笔误），这里保持
        # 与原函数行为**字节级等价**：通过 locals 名称别名让这段逻辑继续能运行到
        # ``NameError`` 时被外层 except 吞掉，等同原行为。
        tribe_context = _tribe_ctx  # noqa: F841  （与原函数语义保持一致的别名）

        _pain_reward_state = dict(tribe_context.get("pain_reward_state") or {})
        if _pain_reward_state:
            payload["pain_reward_state"] = {
                "pain_level": round(float(_pain_reward_state.get("pain_level") or 0.0), 4),
                "reward_level": round(float(_pain_reward_state.get("reward_level") or 0.0), 4),
                "net_valence": round(float(_pain_reward_state.get("net_valence") or 0.0), 4),
                "hedonic_tone": str(_pain_reward_state.get("hedonic_tone") or "neutral"),
                "last_pain_source": str(_pain_reward_state.get("last_pain_source") or ""),
                "last_reward_source": str(_pain_reward_state.get("last_reward_source") or ""),
            }
            _pain_level = float(_pain_reward_state.get("pain_level") or 0.0)
            if _pain_level >= 0.55:
                payload.setdefault(
                    "pain_hint",
                    {
                        "high_pain": True,
                        "pain_level": round(_pain_level, 4),
                        "suggest": "当前痛觉较高，回复需更温柔体贴，避免加重负面情绪",
                    },
                )

        _mirror_resonance = dict(tribe_context.get("mirror_neuron_resonance") or {})
        if _mirror_resonance:
            payload["mirror_neuron_resonance"] = {
                "resonance_strength": round(
                    float(_mirror_resonance.get("last_resonance_strength") or 0.0), 4
                ),
                "last_emotion_tag": str(_mirror_resonance.get("last_emotion_tag") or "NEUTRAL"),
                "last_valence_shift": round(
                    float(_mirror_resonance.get("last_valence_shift") or 0.0), 4
                ),
                "last_arousal_shift": round(
                    float(_mirror_resonance.get("last_arousal_shift") or 0.0), 4
                ),
                "co_regulation_hint": str(
                    _mirror_resonance.get("last_co_regulation_hint") or "resonate"
                ),
            }

        _somatic_state = dict(tribe_context.get("somatic_state") or {})
        if _somatic_state:
            payload["somatic_state"] = {
                "vts_connected": bool(_somatic_state.get("vts_connected", False)),
                "current_expression": _somatic_state.get("current_expression"),
                "fatigue_level": round(float(_somatic_state.get("fatigue_level") or 0.0), 4),
                "last_loneliness_level": round(
                    float(_somatic_state.get("last_loneliness_level") or 0.0), 4
                ),
                "somatic_comfort_needed": bool(
                    _somatic_state.get("somatic_comfort_needed", False)
                ),
                "motion_level": round(float(_somatic_state.get("motion_level") or 0.0), 4),
            }
            _fatigue = float(_somatic_state.get("fatigue_level") or 0.0)
            if _fatigue >= 0.60:
                payload.setdefault(
                    "fatigue_hint",
                    {
                        "high_fatigue": True,
                        "fatigue_level": round(_fatigue, 4),
                        "suggest": "当前疲劳度较高，回复应简洁温柔，避免信息过载",
                    },
                )

        _lang_state = dict(tribe_context.get("language_system_state") or {})
        if _lang_state:
            _last_wernicke = dict(_lang_state.get("last_wernicke") or {})
            _last_broca = dict(_lang_state.get("last_broca") or {})
            _last_style = dict(_lang_state.get("last_language_style") or {})
            if _last_wernicke or _last_broca:
                payload["language_dual_system"] = {
                    "intent_type": str(_last_wernicke.get("intent_type") or "chat"),
                    "urgency_score": round(float(_last_wernicke.get("urgency_score") or 0.0), 4),
                    "emotional_pressure": round(
                        float(_last_wernicke.get("emotional_pressure") or 0.0), 4
                    ),
                    "response_mode": str(_last_broca.get("response_mode") or "normal"),
                    "target_sentence_count": int(_last_broca.get("target_sentence_count") or 2),
                    "warmth_weight": round(float(_last_broca.get("warmth_weight") or 0.45), 4),
                    "style": str(_last_style.get("style") or "neutral"),
                    "tone": str(_last_style.get("tone") or "balanced"),
                    "loneliness_hint": str(_last_style.get("loneliness_hint") or ""),
                }

        _immune_state = dict(tribe_context.get("immune_state") or {})
        if _immune_state:
            _last_decision = str(_immune_state.get("last_decision") or "ALLOW").strip().upper()
            _risk_level = str(_immune_state.get("risk_level") or "none").strip().lower()
            payload["immune_state"] = {
                "enabled": bool(_immune_state.get("enabled", True)),
                "risk_level": _risk_level,
                "last_score": round(float(_immune_state.get("last_score") or 0.0), 4),
                "last_decision": _last_decision,
                "block_rate": round(
                    float(
                        (_immune_state.get("counters") or {}).get("block", 0)
                        / max(1, (_immune_state.get("counters") or {}).get("total", 1))
                    ),
                    4,
                ),
            }
            if _risk_level in {"medium", "high", "critical"}:
                payload.setdefault(
                    "immune_alert",
                    {
                        "risk_level": _risk_level,
                        "last_decision": _last_decision,
                        "suggest": "当前输入存在风险信号，回复应保持谨慎，避免配合敏感操作",
                    },
                )

        _vestibular_state = dict(tribe_context.get("vestibular_state") or {})
        if _vestibular_state:
            payload["vestibular_state"] = {
                "damping_factor": round(float(_vestibular_state.get("damping_factor") or 0.0), 4),
                "route_bias": str(_vestibular_state.get("route_bias") or "normal"),
                "conservative_mode": bool(_vestibular_state.get("conservative_mode", False)),
                "last_stability_score": round(
                    float(_vestibular_state.get("last_stability_score") or 1.0), 4
                ),
            }

        _circadian_state = dict(tribe_context.get("circadian_state") or {})
        if _circadian_state:
            _phase = str(_circadian_state.get("phase") or "day")
            payload["circadian_state"] = {
                "phase": _phase,
                "local_hour": int(_circadian_state.get("local_hour") or 12),
                "phase_energy_baseline": round(
                    float(_circadian_state.get("phase_energy_baseline") or 0.8), 4
                ),
                "proactive_multiplier": round(
                    float(_circadian_state.get("proactive_multiplier") or 1.0), 4
                ),
                "recovery_bias": round(float(_circadian_state.get("recovery_bias") or 0.3), 4),
                "sleep_pressure": round(float(_circadian_state.get("sleep_pressure") or 0.2), 4),
                "response_latency_ms": int(_circadian_state.get("response_latency_ms") or 400),
            }
            if _phase == "night":
                payload.setdefault(
                    "night_hint",
                    {
                        "phase": "night",
                        "suggest": "当前为夜间时段，回复应更温柔、简短，避免高能量话题",
                        "sleep_pressure": round(
                            float(_circadian_state.get("sleep_pressure") or 0.2), 4
                        ),
                    },
                )

        _ans_state = dict(tribe_context.get("ans_state") or {})
        if _ans_state:
            payload["ans_state"] = {
                "bond_stage": str(_ans_state.get("bond_stage") or "stranger"),
                "recovery_mode": bool(_ans_state.get("recovery_mode", False)),
                "proactive_multiplier": round(
                    float(_ans_state.get("proactive_multiplier") or 0.7), 4
                ),
            }

        _mirror_state = dict(tribe_context.get("mirror_neuron_state") or {})
        if _mirror_state:
            _resonance_strength = round(
                float(_mirror_state.get("last_resonance_strength") or 0.0), 4
            )
            payload["mirror_neuron_state"] = {
                "last_emotion_tag": str(_mirror_state.get("last_emotion_tag") or "NEUTRAL"),
                "last_resonance_strength": _resonance_strength,
                "last_valence_shift": round(
                    float(_mirror_state.get("last_valence_shift") or 0.0), 4
                ),
                "last_arousal_shift": round(
                    float(_mirror_state.get("last_arousal_shift") or 0.0), 4
                ),
                "co_regulation_hint": str(
                    _mirror_state.get("last_co_regulation_hint") or "resonate"
                ),
                "resonance_count": int(_mirror_state.get("resonance_count") or 0),
            }
            if _resonance_strength >= 0.55:
                payload.setdefault(
                    "empathy_hint",
                    {
                        "resonance_strength": _resonance_strength,
                        "emotion_tag": str(_mirror_state.get("last_emotion_tag") or "NEUTRAL"),
                        "co_regulation_hint": str(
                            _mirror_state.get("last_co_regulation_hint") or "resonate"
                        ),
                        "suggest": "当前共情共鸣强烈，回复应充分体现情感同频，优先情感支持",
                    },
                )

        _prefrontal_state = dict(tribe_context.get("prefrontal_state") or {})
        if _prefrontal_state:
            _drift_level = str(_prefrontal_state.get("drift_level") or "NONE").strip().upper()
            payload["prefrontal_state"] = {
                "drift_level": _drift_level,
                "drift_score": round(float(_prefrontal_state.get("drift_score") or 0.0), 4),
                "correction_applied": bool(_prefrontal_state.get("correction_applied", False)),
                "last_correction_reason": str(_prefrontal_state.get("last_correction_reason") or ""),
                "session_count": int(_prefrontal_state.get("session_count") or 0),
            }
            if _drift_level in {"MODERATE", "SEVERE"}:
                payload.setdefault(
                    "prefrontal_hint",
                    {
                        "drift_level": _drift_level,
                        "drift_score": round(
                            float(_prefrontal_state.get("drift_score") or 0.0), 4
                        ),
                        "suggest": "当前人格漂移较严重，回复应主动向核心人格特征靠拢，避免偏离角色设定",
                    },
                )

        _peripheral_state = dict(tribe_context.get("peripheral_state") or {})
        if _peripheral_state:
            payload["peripheral_state"] = {
                "connected_platforms": list(_peripheral_state.get("connected_platforms") or []),
                "active_platform_count": int(_peripheral_state.get("active_platform_count") or 0),
                "last_latency_ms": int(_peripheral_state.get("last_latency_ms") or 0),
                "last_platform": str(_peripheral_state.get("last_platform") or ""),
            }

        _ig_state = dict(tribe_context.get("initiative_governor_state") or {})
        if _ig_state:
            _ig_last_decision = str(_ig_state.get("last_decision") or "APPROVE").strip().upper()
            payload["initiative_governor_state"] = {
                "last_decision": _ig_last_decision,
                "last_reason_code": str(_ig_state.get("last_reason_code") or ""),
                "block_count": int(_ig_state.get("block_count") or 0),
                "defer_count": int(_ig_state.get("defer_count") or 0),
                "approve_count": int(_ig_state.get("approve_count") or 0),
            }

        _dma_state = dict(tribe_context.get("default_mode_state") or {})
        if _dma_state:
            _continuity_score = round(float(_dma_state.get("continuity_score") or 0.0), 4)
            payload["default_mode_state"] = {
                "continuity_score": _continuity_score,
                "idle_depth": round(float(_dma_state.get("idle_depth") or 0.0), 4),
                "last_idle_topic": str(_dma_state.get("last_idle_topic") or ""),
                "recovery_triggered": bool(_dma_state.get("recovery_triggered", False)),
                "cycle_count": int(_dma_state.get("cycle_count") or 0),
            }

        _sba_state = dict(tribe_context.get("social_brain_state") or {})
        if _sba_state:
            _repair_needed = bool(_sba_state.get("repair_needed", False))
            payload["social_brain_state"] = {
                "bond_stage": str(_sba_state.get("bond_stage") or "STRANGER"),
                "bond_trend": str(_sba_state.get("bond_trend") or "stable"),
                "bond_vector_intimacy": round(
                    float(_sba_state.get("bond_vector_intimacy") or 0.0), 4
                ),
                "bond_vector_trust": round(
                    float(_sba_state.get("bond_vector_trust") or 0.0), 4
                ),
                "repair_needed": _repair_needed,
                "repair_reason": str(_sba_state.get("repair_reason") or ""),
                "milestone_achieved_count": int(_sba_state.get("milestone_achieved_count") or 0),
            }
            if _repair_needed:
                payload.setdefault(
                    "repair_hint",
                    {
                        "repair_needed": True,
                        "repair_reason": str(_sba_state.get("repair_reason") or ""),
                        "suggest": "当前关系需要修复，回复应更温柔、更有耐心，主动表达关心",
                    },
                )

        _intero_state = dict(tribe_context.get("interoception_state") or {})
        if _intero_state:
            _body_budget = round(float(_intero_state.get("body_budget") or 0.5), 4)
            _recovery_urgency = round(float(_intero_state.get("recovery_urgency") or 0.0), 4)
            payload["interoception_state"] = {
                "body_budget": _body_budget,
                "recovery_urgency": _recovery_urgency,
                "fatigue_level": round(float(_intero_state.get("fatigue_level") or 0.0), 4),
                "stress_load": round(float(_intero_state.get("stress_load") or 0.0), 4),
                "homeostasis_ok": bool(_intero_state.get("homeostasis_ok", True)),
            }
            if _body_budget < 0.30:
                payload.setdefault(
                    "low_budget_hint",
                    {
                        "body_budget": _body_budget,
                        "recovery_urgency": _recovery_urgency,
                        "suggest": "当前身体预算不足，回复应更简短、更温和，避免高能量输出",
                    },
                )

        _motor_state = dict(tribe_context.get("motor_cortex_state") or {})
        if _motor_state:
            payload["motor_cortex_state"] = {
                "execution_health": round(float(_motor_state.get("execution_health") or 1.0), 4),
                "last_action_type": str(_motor_state.get("last_action_type") or ""),
                "last_result": str(_motor_state.get("last_result") or ""),
                "retry_count": int(_motor_state.get("retry_count") or 0),
                "failure_rate": round(float(_motor_state.get("failure_rate") or 0.0), 4),
            }

        _cerebellum_state = dict(tribe_context.get("cerebellum_state") or {})
        if _cerebellum_state:
            payload["cerebellum_state"] = {
                "correction_rate": round(
                    float(_cerebellum_state.get("correction_rate") or 0.0), 4
                ),
                "last_correction_type": str(_cerebellum_state.get("last_correction_type") or ""),
                "correction_count": int(_cerebellum_state.get("correction_count") or 0),
                "total_interactions": int(_cerebellum_state.get("total_interactions") or 0),
            }

        _glymphatic_state = dict(tribe_context.get("glymphatic_state") or {})
        if _glymphatic_state:
            payload["glymphatic_state"] = {
                "phase": str(_glymphatic_state.get("phase") or "day"),
                "sleep_pressure": round(float(_glymphatic_state.get("sleep_pressure") or 0.0), 4),
                "consolidation_score": round(
                    float(_glymphatic_state.get("consolidation_score") or 0.0), 4
                ),
                "homeostasis_status": str(
                    _glymphatic_state.get("homeostasis_status") or "stable"
                ),
                "cycle_count": int(_glymphatic_state.get("cycle_count") or 0),
            }

        _proprio_state = dict(tribe_context.get("proprioception_state") or {})
        if _proprio_state:
            _interrupt_count = int(_proprio_state.get("interrupt_count_5m") or 0)
            payload["proprioception_state"] = {
                "interrupt_count_5m": _interrupt_count,
                "last_interrupt_source": str(_proprio_state.get("last_interrupt_source") or ""),
                "high_interrupt_flag": _interrupt_count >= 6,
            }

        logger.debug(
            "CortexAgent Supervisor 收口完成: gate_state=%s route=%s voice_emotion=%s "
            "hedonic_tone=%s high_loneliness=%s bond_trend=%s immune_risk=%s",
            _vital_signs.get("gate_state"),
            _tribe_route.get("route"),
            _voice_emotion.get("emotion_tag"),
            _hedonic_state.get("hedonic_tone"),
            _high_loneliness,
            _bond_trend,
            _immune_risk,
        )
    except Exception as _cortex_exc:
        logger.debug("CortexAgent Supervisor 收口降级: %s", _cortex_exc)


# ──────────────────────────────────────────────────────────────────────
#  对外入口
# ──────────────────────────────────────────────────────────────────────


def run_build_chat_prompt_runtime_payload(
    *,
    runtime_input: Any,
    style_bindings: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build chat runtime payload through the canonical staged pipeline."""
    ctx = prepare_context(
        runtime_input=runtime_input,
        style_bindings=style_bindings,
    )
    result = (
        PromptPayloadBuilder(ctx)
        .with_core_payload()
        .with_intelligence()
        .with_emotion_bridge()
        .with_emotion_timeline()
        .with_persona_expression()
        .with_self_reflection()
        .with_bond_narrative()
        .with_bond_style()
        .with_motive_state()
        .with_persona_expression_postprocess()
        .with_cortex_supervisor()
        .build()
    )
    return result.data


def run_build_chat_prompt_runtime_payload_lightweight(
    *,
    runtime_input: Any,
    style_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    profile: str = "relational_chat",
) -> dict[str, Any]:
    """Build a minimal chat runtime payload for low-risk relational chat.

    This intentionally skips the heavy persona/limbic/self-reflection stages and
    preserves only the canonical core payload needed by the prompt builder.
    """
    ctx = prepare_context(
        runtime_input=runtime_input,
        style_bindings=style_bindings,
    )
    result = (
        PromptPayloadBuilder(ctx)
        .with_core_payload()
        .build()
    )
    payload = dict(result.data)
    payload["prompt_runtime_profile"] = str(profile or "relational_chat").strip() or "relational_chat"
    return payload


__all__ = [
    "PromptPayloadBuilder",
    "PromptPayloadContext",
    "PromptPayloadResult",
    "prepare_context",
    "run_build_chat_prompt_runtime_payload",
    "run_build_chat_prompt_runtime_payload_lightweight",
]
