"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from host_brain.persona.surface_main_chain import (
    SurfaceMainChain,
    SurfaceMainChainInput,
    SurfaceMainChainResult,
    compile_surface_main_chain_input,
)
from host_brain.persona.inner_monologue import build_inner_weather_hint, InnerMonologueDigest
from host_brain.persona.body_sense import project_body_sense_from_life_snapshot
from host_brain.persona.context.soul_runtime import get_compiled_soul, render_runtime_soul_prompt
from host_brain.tribe.default_mode_agent import get_default_mode_agent
import re
import time

from host_brain.surface_runtime.eligibility import (
    _surface_scene_guardrails,
)
from host_brain.surface_runtime.fact_compiler import (
    _build_digest_from_inner_monologue,
    _necessary_facts,
    _surface_boundary_must_not_do,
)
from host_brain.surface_runtime.common import _coerce_int, _safe_mapping
from host_brain.surface_runtime.life_context import (
    _surface_autonomous_self_task,
)
from host_brain.surface_runtime.life_projection import (
    surface_user_preference_query_owned as _surface_user_preference_query_owned,
)
from host_brain.surface_runtime.memory_context import (
    _surface_apply_autonomous_self_query_reply_plan_override,
    _surface_apply_memory_context_reply_plan_override,
    _surface_sync_reply_plan,
)
from host_brain.surface_runtime.quality_guards import (
    _affect_packet,
    _influence_packet,
    _memory_packet,
)
from host_brain.surface_runtime.reply_context import (
    _build_surface_runtime_payload,
    _merge_surface_trace,
    _prepared_surface_runtime_payload,
    _resolve_surface_reply_plan,
    _surface_task_control_from_reply_plan,
)
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
from host_brain.digital_life.application_boundary import read_life_snapshot
from host_brain.digital_life.affective_turn_planner import (
    AffectiveTurnPlanError,
    build_affective_turn_guidance,
)


def _limit_surface_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n..."


def _surface_soul_chat_anchor(soul: Any) -> str:
    """Build the relationship-safe Soul anchor for live Surface replies."""

    # Do not fall back to ``source_text`` or an ad-hoc style block here.  The
    # compiled runtime projection is the only model-facing representation of
    # Soul; it carries the canonical relationship authority boundary itself.
    compact = _limit_surface_text(render_runtime_soul_prompt(soul, compact=True), limit=2100)
    return "【当前人格短锚】\n" + compact if compact else ""


_TEXT_RESPONSE_FORMS = {"", "text", "plain_text", "chat_text", "zh_text"}


_AUDIO_INPUT_SOURCES = {"asr", "audio", "voice", "speech", "mic", "microphone", "voice_chat"}


_DETERMINISTIC_RESULT_AGENT_IDS = {
    "system_info",
    "system_resource_snapshot",
    "process_resource_top",
    "resource_pressure_analysis",
    "resource_manager_status",
    "resource_release",
    "process_list",
    "network_status",
    "open_app",
    "plan_execution",
    "recent_turn_direct",
    "task_progress_direct",
}


_TECHNICAL_TASK_MODES = {
    "advice_continue",
    "complaint",
    "help_takeover",
    "next_step",
    "small_failure",
    "technical_help",
    "triage_first_step",
    "write_takeover",
}


_ACCOUNT_LOGIN_TASK_RE = re.compile(
    r"(登录|账号|账户|积分|轻币|购买|买|epub|电子书|lightnovel|轻小说)",
    re.IGNORECASE,
)


_ACCOUNT_CREDENTIAL_REQUEST_RE = re.compile(
    r"((账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password)"
    r".{0,16}(丢|发|给|贴|提供|传|告诉|send|paste|provide)"
    r"|(丢|发|给|贴|提供|传|告诉|send|paste|provide).{0,16}"
    r"(账号信息|账户信息|账号密码|账户密码|密码|验证码|cookie|cookies|credentials?|password))",
    re.IGNORECASE,
)


_SURFACE_GREETING_RE = re.compile(
    r"^(你好|你好啊|你好呀|您好|嗨|哈喽|哈啰|hello|hi|hey)[。.!！?？~～]*$",
    re.IGNORECASE,
)


_MORNING_GREETING_START_RE = re.compile(r"^(?:早安|早上好|早晨好|上午好|早啊|早呀|早哦|早)(?:[。.!！?？~～]|$)")


_MORNING_GREETING_MENTION_RE = re.compile(r"(?:早安|早上好|早晨好|上午好|早啊|早呀|早哦)")


_TEMPORAL_GREETING_CORRECTION_RE = re.compile(
    r"(?:现在|当前|都|几点|什么时候|下午|晚上|夜|晚了|不是早上|还早上|说早|怎么还|怎么又)"
)


_AUTONOMOUS_SELF_QUERY_RE = re.compile(
    r"(你(?:刚才|刚刚|最近|现在|这会儿|平时)?(?:在)?(?:干嘛|做什么|忙什么|想什么|惦记什么)"
    r"|你(?:最近|这阵子|现在)?(?:有什?么变化|变了什?么|学到什?么|在成长什?么)"
    r"|你(?:自己|自个儿).{0,12}(?:项目|事情|议程|想做|在做)"
    r"|你是不是.{0,10}(?:只是在等我|一直等我|没有自己的事))",
    re.IGNORECASE,
)


def _surface_morning_greeting_mismatch_candidate(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if _MORNING_GREETING_START_RE.search(compact):
        return True
    return bool(_MORNING_GREETING_MENTION_RE.search(compact) and _TEMPORAL_GREETING_CORRECTION_RE.search(compact))


_MAIN_CHAIN_META_CHAT_RE = re.compile(
    r"(主链|链路|耗时|延迟|fallback|Codex|模型|路由|surface|thalamus|后台任务)",
    re.IGNORECASE,
)


_BAD_TEASE_PREFIX_RE = re.compile(
    r"^\s*(?:哈？|啊？|嘛，?)?[^。！？!?]{0,40}(?:乱七八糟|又在琢磨|又在研究)[^。！？!?]*[。！？!?]\s*",
    re.IGNORECASE,
)


_PERCEPTION_EVIDENCE_REQUIRED_RE = re.compile(
    r"(这几本|这几张|这几份|这张|哪张|图里|图上|图片|照片|截图|画面|看起来|"
    r"image|photo|screenshot|cover)",
    re.IGNORECASE,
)


_PERCEPTION_PRODUCTION_TASK_RE = re.compile(
    r"(调用|推荐|抓|获取|找|搜索|采集|每日榜单|最新资源|轻小说|epub|封装|购买)",
    re.IGNORECASE,
)


_CURRENT_COVER_CLAIM_RE = re.compile(r"(这|那|哪).{0,8}封面|封面(上|里|中|有|是|看起来)", re.IGNORECASE)


_MEMORY_ASSERTION_RE = re.compile(
    r"(我记得|记得|我记到|我想起来|刚才|刚刚|昨天|前天|上次|之前|"
    r"我选|我会选|更推荐|最好看|哪本好看|推的是|推荐了|那几本|这几本)",
    re.IGNORECASE,
)


_QUOTED_TITLE_RE = re.compile(r"《([^》]{1,120})》")


_MEMORY_RECALL_FAILURE_CODES = {
    "SURFACE_MEMORY_RECALL_VALIDATION_FAILED",
    "SURFACE_MEMORY_RECALL_COVERAGE_FAILED",
    "SURFACE_MEMORY_RECALL_FAILED",
}


_MEMORY_RECALL_USER_STATE_GUESS_TERMS = (
    "终于来问",
    "终于想起来",
    "终于想起",
    "总算来问",
    "总算想起来",
    "才想起来",
    "老哥终于",
    "你终于",
    "昨天是不是没看",
    "你是不是没看",
    "你是不是忘了",
)


_MEMORY_RECALL_USER_STATE_GUESS_RE = re.compile(
    r"(?:^|[，,。.!！?？；;、\s])"
    r"(?:嘿嘿|哈哈|哈|嗯|哼|欸|诶)?[，,、\s]*"
    r"(?:老哥|哥哥|对方)?(?:你)?"
    r"(?:(?:终于|总算|才).{0,14}(?:来问|问我|问这个|想起来|想起|舍得问)"
    r"|(?:昨天|刚刚|刚才|之前|上次).{0,12}(?:没看|忘了|想起来)"
    r"|(?:你|老哥|哥哥|对方).{0,8}(?:是不是|不会是).{0,8}(?:没看|忘了))"
    r"[^，,。.!！?？；;、~～]*[，,。.!！?？；;、~～\s]*",
    re.IGNORECASE,
)


_MEMORY_RECALL_SELF_READING_DRIFT_RE = re.compile(
    r"((?:最近|现在|这阵子|这两天|刚好).{0,14}(?:在看|看了?一本|读了?一本|追|翻的是)"
    r"|一本讲[^，,。.!！?？；;、]{1,40})",
    re.IGNORECASE,
)


_MEMORY_RECALL_LOW_SIGNAL_PREFIX_RE = re.compile(
    r"^\s*(?:哈[？?]?|哈哈|嘿嘿|嗯|欸|诶|呃|啊|老哥|哥哥|对方|老哥问得正好|问得正好|难得难得|(?:老哥|哥哥|对方)?你问这个啊)"
    r"[^，,。.!！?？；;、~～—-]{0,12}[，,。.!！?？；;、~～—\-\s]*",
    re.IGNORECASE,
)


_MEMORY_GUARD_EMPTY_REWRITE_QUERY_RE = re.compile(
    r"(记得|记不记得|还记得|记忆|我之前|之前说|以前说|上次说|我说过|"
    r"偏好|喜好|喜欢什么|不喜欢什么|我的.{0,20}(有哪些|是什么|是啥))",
    re.IGNORECASE,
)


def build_surface_main_chain_input_from_surface_reply_input(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainInput:
    soul = get_compiled_soul()
    runtime_payload = _prepared_surface_runtime_payload(trace_stage_meta)
    if not runtime_payload:
        runtime_payload = _build_surface_runtime_payload(
            agent_input,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
            merge_trace=False,
        )
    reply_plan = _resolve_surface_reply_plan(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
        runtime_payload=runtime_payload,
    )
    _merge_surface_trace(trace_stage_meta, runtime_payload)
    reply_plan = _surface_apply_memory_context_reply_plan_override(
        reply_plan,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    reply_plan = _surface_apply_autonomous_self_query_reply_plan_override(
        reply_plan,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    _surface_sync_reply_plan(
        agent_input=agent_input,
        trace_stage_meta=trace_stage_meta,
        reply_plan=reply_plan,
    )
    task_control = _surface_task_control_from_reply_plan(
        agent_input=agent_input,
        reply_plan=reply_plan,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    task_control = _apply_affective_turn_guidance(
        task_control,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    _surface_sync_reply_plan(
        agent_input=agent_input,
        trace_stage_meta=trace_stage_meta,
        reply_plan=task_control,
    )
    surface_intent_id = str(
        _safe_mapping(getattr(agent_input, "intent_draft", None)).get("id") or ""
    ).strip()
    frozen_intent = _safe_mapping(getattr(agent_input, "intent_draft", None))
    frozen_scene_decision = getattr(agent_input, "scene_decision_v2", None)
    if frozen_scene_decision is None:
        frozen_scene_decision = frozen_intent.get("scene_decision_v2")
    frozen_scene_identity = getattr(agent_input, "scene_identity_v2", None)
    if frozen_scene_identity is None:
        frozen_scene_identity = frozen_intent.get("scene_identity_v2")
    scene_payload, scene_guardrails = _surface_scene_guardrails(
        scene_decision_v2=frozen_scene_decision,
        scene_identity_v2=frozen_scene_identity,
        user_goal_ref=f"intent://{surface_intent_id}" if surface_intent_id else "",
    )
    if isinstance(trace_stage_meta, dict):
        trace_stage_meta["scene_decision"] = {
            "schema_version": scene_payload.get("schema_version"),
            "decision_id": scene_payload.get("decision_id"),
            "scene_type": scene_payload.get("scene_type"),
            "revision": scene_payload.get("revision"),
            "decision_digest": scene_payload.get("decision_digest"),
            "proactive_policy": scene_payload.get("proactive_policy"),
            "expression_mode": scene_payload.get("expression_mode"),
            "source_refs": list(scene_payload.get("source_refs") or []),
            "user_goal_preserved": scene_payload.get("user_goal_preserved"),
            "validation_status": scene_payload.get("validation_status"),
        }
    affect_packet = _affect_packet(
        agent_input,
        task_control=task_control,
        trace_stage_meta=trace_stage_meta,
        runtime_payload=runtime_payload,
    )
    preference_query_owned = _surface_user_preference_query_owned(agent_input, ports=ports)
    autonomous_self_owned = _surface_autonomous_self_task(task_control)

    # --- Inner-state leakage (life-feel renovation A5) ---
    # Bridge DefaultModeAgent runtime state into surface so the LLM
    # can feel the character's inner weather without seeing raw thoughts.
    _inner_weather = ""
    _body_state_hint = ""
    _care_signal = 0.0
    _continuity_signal = 0.0
    _tension_hint = ""
    _liveliness = 0.5
    _active_loops = 0
    try:
        agent = get_default_mode_agent()
        state = agent.export_runtime_state()
        inner_monologue = state.get("inner_monologue") or {}

        # Build InnerMonologueDigest for inner-weather generation.
        _digest = _build_digest_from_inner_monologue(inner_monologue)
        relationship_subject_id = str(
            getattr(agent_input, "person_id", "") or ""
        ).strip()
        if relationship_subject_id:
            life_snapshot = read_life_snapshot(
                relationship_subject_id,
                reader=ports.get_life_snapshot_reader(),
            )
            body_sense = project_body_sense_from_life_snapshot(life_snapshot)
            if isinstance(trace_stage_meta, dict):
                trace_stage_meta["body_sense_projection"] = {
                    "authority": "CANONICAL_LIFE_JOURNAL",
                    "relationship_subject_id": relationship_subject_id,
                    "life_version": int(life_snapshot.get("version") or 0),
                    "life_state_digest": str(life_snapshot.get("state_digest") or ""),
                }
        else:
            body_sense = None
        _inner_weather = build_inner_weather_hint(
            _digest,
            fatigue=float(getattr(body_sense, "fatigue", 0.0)),
            drowsiness=float(getattr(body_sense, "drowsiness", 0.0)),
            loneliness=float(getattr(body_sense, "loneliness", 0.0)),
        )
        _body_state_hint = str(getattr(body_sense, "body_sense_hint", "") or "")

        # Map drive signals.
        _care_signal = float(inner_monologue.get("care_drive") or 0.0)
        _continuity_signal = float(inner_monologue.get("continuity_drive") or 0.0)
        _liveliness = float(inner_monologue.get("liveliness_score") or 0.5)
        _active_loops = int(inner_monologue.get("open_loop_count") or 0)

        # Extract tension hint from self-directive.
        unresolved = list(state.get("unresolved_tensions") or [])
        if unresolved:
            _tension_hint = unresolved[0]
    except Exception:
        pass

    surface_must_not_do = tuple(
        _surface_boundary_must_not_do(
            agent_input=agent_input,
            task_control=task_control,
            ports=ports,
            trace_stage_meta=trace_stage_meta,
        )
    ) + scene_guardrails

    return SurfaceMainChainInput(
        soul_md=_surface_soul_chat_anchor(soul),
        user_message=str(getattr(agent_input, "user_message", "") or "").strip(),
        case_id=str(getattr(agent_input, "request_id", "") or "").strip(),
        task_control=task_control,
        memory_packet=None if preference_query_owned else _memory_packet(
            agent_input,
            trace_stage_meta=trace_stage_meta,
            runtime_payload=runtime_payload,
        ),
        affect_packet=affect_packet,
        influence_packet=_influence_packet(
            agent_input,
            trace_stage_meta=trace_stage_meta,
            runtime_payload=runtime_payload,
        ),
        necessary_facts=_necessary_facts(
            agent_input,
            ports=ports,
            task_control=task_control,
            trace_stage_meta=trace_stage_meta,
        ),
        must_not_do=surface_must_not_do,
        style_anchor=(
            "像熟人私聊：先接住当前话，再轻轻顶嘴或顺手打趣；不要写成客服回执、流程确认或任务记录。",
            "寒暄也要像真的在聊天：不要把寒暄抽象成“问候/招呼”这类标签词；直接回对方这句话，比如顺手回一句下午好或轻轻吐槽。",
            "用户重复同一句时，优先理解为调侃、测试或故意逗你；不要机械复读上一轮纠正。",
        ),
        max_facts=14 if autonomous_self_owned else 12 if preference_query_owned else 8,
        max_must_not_do=24,
        # --- Inner-state leakage fields (life-feel renovation A5) ---
        inner_weather=_inner_weather,
        body_state_hint=_body_state_hint,
        care_signal=_care_signal,
        continuity_signal=_continuity_signal,
        tension_hint=_tension_hint,
        liveliness=_liveliness,
        active_loops=_active_loops,
    )


def _apply_affective_turn_guidance(
    task_control: Any,
    *,
    agent_input: Any,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Append only safe, frozen affect constraints to the Surface packet.

    ``render_policy`` remains a task/format input; it never gets to replace
    the self-affect, relationship permission or regulation selected by the
    canonical Life hand-off.
    """

    control = _safe_mapping(
        task_control.as_dict() if callable(getattr(task_control, "as_dict", None)) else task_control
    )
    emotion_context = _safe_mapping(getattr(agent_input, "emotion_context", None))
    is_internal = bool(emotion_context.get("_affective_life_internal"))
    previous_expression, previous_expression_source = _previous_avatar_expression(
        agent_input=agent_input,
        ports=ports,
    )
    try:
        guidance = build_affective_turn_guidance(
            emotion_context=emotion_context,
            request_id=str(getattr(agent_input, "request_id", "") or ""),
            workspace_id=str(getattr(agent_input, "workspace_id", "") or ""),
            relationship_subject_id=str(getattr(agent_input, "person_id", "") or ""),
            session_id=str(getattr(agent_input, "session_id", "") or ""),
            task_mode=str(control.get("task_mode") or control.get("response_move") or ""),
            created_at_ms=_coerce_int(getattr(agent_input, "ts_ms", 0), default=0),
            previous_avatar_expression=previous_expression,
            previous_expression_source=previous_expression_source,
        )
    except AffectiveTurnPlanError as exc:
        # A caller that presents an internal marker but fails scope or schema
        # validation must not silently fall back to a user-supplied emotion tag.
        if is_internal:
            raise RuntimeError(str(exc)) from exc
        guidance = None

    if guidance is None:
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["affective_turn_plan"] = {
                "status": "COMPATIBILITY_NO_CANONICAL_AFFECT",
            }
        return control

    def merge(name: str, values: tuple[str, ...]) -> list[str]:
        existing = [
            str(item).strip()
            for item in list(control.get(name) or [])
            if str(item).strip()
        ]
        return list(dict.fromkeys([*existing, *values]))

    control["facts"] = merge("facts", guidance.facts)
    control["must_not_do"] = merge("must_not_do", guidance.must_not_do)
    control["style_anchor"] = merge("style_anchor", guidance.style_anchor)
    if isinstance(trace_stage_meta, dict):
        trace_stage_meta["affective_turn_plan"] = guidance.plan.as_dict()
        trace_stage_meta["affective_turn_guidance"] = guidance.trace()
    return control


def _previous_avatar_expression(
    *,
    agent_input: Any,
    ports: SurfaceRuntimePorts,
) -> tuple[str, str]:
    """Read a prior accepted Host expression without trusting request metadata.

    The old VTS helper read ``_prev_emotion_tag`` from a mutable emotion
    context.  That field may describe a user observation or a legacy runtime,
    not the avatar that was actually staged.  DL-05 uses only the bounded
    canonical delivery projection; when it is absent we explicitly start from
    neutral rather than inventing a previous embodiment.
    """

    workspace_id = str(getattr(agent_input, "workspace_id", "") or "").strip()
    person_id = str(getattr(agent_input, "person_id", "") or "").strip()
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    if not workspace_id or not person_id or not session_id:
        return "NEUTRAL", "UNAVAILABLE"
    try:
        reader = ports.get_life_snapshot_reader()
        lookup = getattr(reader, "last_affective_expression_delivery_head", None)
        if not callable(lookup):
            return "NEUTRAL", "UNAVAILABLE"
        row = lookup(
            person_id,
            workspace_id=workspace_id,
            relationship_subject_id=person_id,
            session_id=session_id,
        )
        if not isinstance(row, dict):
            return "NEUTRAL", "FALLBACK_NEUTRAL"
        expression = str(row.get("avatar_expression") or "").strip().upper()
        if not expression:
            return "NEUTRAL", "FALLBACK_NEUTRAL"
        return expression[:64], "DURABLE_LAST_ACCEPTED"
    except Exception:
        # Read unavailability must not let a legacy runtime or request field
        # enter the plan.  The resulting plan records that it started neutral.
        return "NEUTRAL", "UNAVAILABLE"
