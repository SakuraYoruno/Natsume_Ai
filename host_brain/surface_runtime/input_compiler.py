"""Surface 回复主链的规范化输入编译器。

它把已验证的回复计划、Soul 投影、记忆/生命上下文和表达偏好装配成模型输入；
编译器只读取各权威投影，不能在此改变关系、情感、任务或投递决策。
"""

from __future__ import annotations

from typing import Any
from host_brain.persona.surface_main_chain import (
    SurfaceMainChainInput,
)
from host_brain.persona.inner_monologue import build_inner_weather_hint
from host_brain.persona.body_sense import project_body_sense_from_life_snapshot
from host_brain.persona.context.soul_runtime import get_compiled_soul, render_runtime_soul_prompt
from host_brain.tribe.default_mode_agent import get_default_mode_agent
from host_brain.tribe.prefrontal_runtime_policy import (
    expression_preference_directive,
)
import re

from host_brain.surface_runtime.eligibility import (
    _surface_scene_guardrails,
)
from host_brain.surface_runtime.fact_compiler import (
    _accepted_task_result_texts,
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
    """截断展示型上下文并保留边界标识，防止单个投影挤占整个回复预算。"""
    text = str(value or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n..."


def _surface_soul_chat_anchor(soul: Any) -> str:
    """从编译后的 Soul 投影生成关系安全锚点，不向模型暴露原始人格资料。"""

    # 这里不能回退到 ``source_text`` 或临时拼接的风格块。编译后的运行时投影是 Soul
    # 唯一可面向模型的表示，并且自身携带规范的关系权威边界。
    compact = _limit_surface_text(render_runtime_soul_prompt(soul, compact=True), limit=2100)
    return "【当前人格短锚】\n" + compact if compact else ""


_AFFECTIVE_TECHNICAL_TEXT_RE = re.compile(
    r"报错|代码|堆栈|排错|修复|调试|部署|发布|风险|测试失败|上线"
)
_AFFECTIVE_TECHNICAL_CONTINUATION_RE = re.compile(
    r"一步步|继续(?:做|弄|排|修)?|陪我.{0,6}(?:做|排|修)|先(?:看|做|排|修)|帮我"
)
_AFFECTIVE_AMBIGUOUS_TEXT_RE = re.compile(
    r"说不清|理不清|不知道.{0,8}(?:感觉|心情)|什么感觉"
)
_AFFECTIVE_AMBIGUOUS_FOLLOWUP_RE = re.compile(
    r"^(?:嗯|还行|还好|就那样|不知道|反正结束了|(?:那)?你(?:觉得呢|怎么看))[。.!！?？]?$"
)
_AFFECTIVE_MIXED_TEXT_RE = re.compile(
    r"(?:开心|高兴|兴奋).{0,16}(?:不踏实|不安|担心|害怕|有点怕|悬着)"
    r"|(?:不踏实|不安|担心|害怕|有点怕|悬着).{0,16}(?:开心|高兴|兴奋)"
)
_AFFECTIVE_MIXED_RESULT_FOLLOWUP_RE = re.compile(
    r"^(?=.{1,32}$)(?:项目|版本|发版|结果|offer|录取|入职)?.{0,12}"
    r"(?:发布成功|上线成功|发版成功|通过了|终于过了|成了|拿到(?:了)?|定了|好消息)"
    r"[。.!！]?$",
    re.IGNORECASE,
)


def _affective_task_mode_for_turn(
    *,
    task_mode: str,
    user_message: str,
    facts: Any,
) -> str:
    """为情感规划器保留当前会话目标。

    通用对话解释器会有意把简短跟进视为聊天；但对情感计划而言，明确修错之后的
    ``陪我一步步做`` 仍是任务，而明确的 ``说不清是什么感觉`` 则是 DL-02 承诺的
    有界模糊路径。此处仅在提示编译时决策，不新增持久化写入或普通聊天模型调用。
    """

    # 此判定只影响本轮提示策略，绝不写入耐久状态或触发常规聊天模型调用。
    mode = str(task_mode or "").strip()
    current = str(user_message or "").strip()
    fact_text = "\n".join(str(item or "") for item in list(facts or []))
    # 未收束的混合情绪中，简短的成功陈述仍是情感结果的延续，而不是操作发布的请求。
    # 没有该优先级，单凭 ``发布`` 就会把 ``项目发布成功。`` 判为 ``technical_help``，
    # 使类型化 Life 连续性抵达 Surface 前丢失 HOLD_COMPLEXITY。
    if (
        _AFFECTIVE_MIXED_TEXT_RE.search(fact_text)
        and _AFFECTIVE_MIXED_RESULT_FOLLOWUP_RE.fullmatch(current)
    ):
        # 混合情绪的结果续句优先保留情感连续性，避免技术关键词单独出现就抹掉复杂感受。
        return "default_text"
    technical_context = bool(_AFFECTIVE_TECHNICAL_TEXT_RE.search(fact_text))
    if _AFFECTIVE_TECHNICAL_TEXT_RE.search(current) or (
        technical_context and _AFFECTIVE_TECHNICAL_CONTINUATION_RE.search(current)
    ):
        return "technical_help"
    ambiguous_context = bool(_AFFECTIVE_AMBIGUOUS_TEXT_RE.search(fact_text))
    if _AFFECTIVE_AMBIGUOUS_TEXT_RE.search(current) or (
        ambiguous_context and _AFFECTIVE_AMBIGUOUS_FOLLOWUP_RE.fullmatch(current)
    ):
        return "ambiguous_affect"
    return mode

def build_surface_main_chain_input_from_surface_reply_input(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None = None,
) -> SurfaceMainChainInput:
    """按固定权威顺序编译 Surface 主链输入。

    先解析并同步回复计划，再应用只能收紧/表达的记忆、身份与情感投影，最后构造
    有界事实和禁止项。该顺序避免后加入的便利上下文覆盖已冻结场景或治理结论。
    """
    soul = get_compiled_soul()
    runtime_payload = _prepared_surface_runtime_payload(trace_stage_meta)
    if not runtime_payload:
        # 缓存载荷优先，但缺失时只通过标准构建器重算，不能直接信任调用方任意字典。
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
    # 记忆/自主查询仅以专门 override 调整计划，之后立即同步以避免 trace 与实际输入分叉。
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
    accepted_task_results = _accepted_task_result_texts(
        agent_input,
        trace_stage_meta=trace_stage_meta,
    )
    task_control = _apply_affective_turn_guidance(
        task_control,
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    # 表达偏好和身份 release 只能追加风格锚点，不能重写 Soul、关系许可或任务控制面。
    task_control = _apply_active_identity_release(
        task_control,
        ports=ports,
    )
    task_control = _apply_active_expression_preferences(
        task_control,
        agent_input=agent_input,
        ports=ports,
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
        # 轨迹记录场景摘要而非原始裁决正文，供审计关联又不扩大模型/日志可见面。
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
            # 生命快照经端口读取并只投影身体感受；原始 life journal 不进入 Surface 提示词。
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
        # 内在天气属于可选表达辅助，读取失败不能使正常对话不可用或改写既有计划。
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
        # 已有任务结果或偏好查询自有答案时不再叠加泛记忆，避免竞争性上下文覆盖直接回应。
        memory_packet=None if (preference_query_owned or accepted_task_results) else _memory_packet(
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
        accepted_task_results=accepted_task_results,
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


def _apply_active_expression_preferences(
    task_control: Any,
    *,
    agent_input: Any,
    ports: SurfaceRuntimePorts,
) -> dict[str, Any]:
    """将当前表达版本放入实际的 Surface 提示路径。

    这里只做一次本地投影读取，不调用模型也不写入持久状态；可选偏好投影不可用时，
    不能连带让普通陪伴聊天不可用。
    """

    # 偏好投影只读且可选；解析失败保持原控制包，不能因个性化功能影响交付可用性。
    control = dict(task_control or {})
    resolver = ports.resolve_expression_preferences
    if not callable(resolver):
        return control
    try:
        policy = resolver(
            persona_user_id=str(
                getattr(agent_input, "person_id", "") or ""
            ).strip()
        )
    except Exception:
        return control
    directive = expression_preference_directive(policy)
    if not directive:
        return control
    existing = [
        str(item).strip()
        for item in list(control.get("style_anchor") or [])
        if str(item).strip()
    ]
    control["style_anchor"] = list(
        # 去重且将偏好放在前面，但它仍只是风格指令而非高权威任务/关系规则。
        dict.fromkeys([directive, *existing])
    )
    return control


def _apply_active_identity_release(
    task_control: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> dict[str, Any]:
    """应用当前发布版的风格基底，同时不暴露身份数据。

    ``IdentitySnapshot`` 保存的是结构化发布摘要而非私有正文。只有受限的风格指纹
    可以成为 Surface 指令；Soul、关系权限与当前情感仍拥有更高优先级。
    """

    # 只投影有界的风格指纹；IdentitySnapshot 的私有正文永远不进入主链输入。
    control = dict(task_control or {})
    resolver = ports.resolve_active_identity
    if not callable(resolver):
        return control
    try:
        active = resolver()
    except Exception:
        return control
    directive = _active_identity_release_directive(active)
    if not directive:
        return control
    existing = [
        str(item).strip()
        for item in list(control.get("style_anchor") or [])
        if str(item).strip()
    ]
    control["style_anchor"] = list(
        dict.fromkeys([directive, *existing])
    )
    return control


def _active_identity_release_directive(active: Any) -> str:
    """把结构化风格维度压缩为短提示，不接受任意身份文本或未验证字段。"""
    payload = _safe_mapping(active)
    snapshot = _safe_mapping(payload.get("snapshot"))
    style = _safe_mapping(snapshot.get("style_fingerprint"))
    raw_dimensions = style.get("dimensions")
    if not isinstance(raw_dimensions, list):
        return ""
    dimensions: dict[str, float] = {}
    for item in raw_dimensions:
        # 不规则维度静默跳过，避免部分损坏的 release 让输入编译失败。
        if not isinstance(item, list) or len(item) != 2:
            continue
        try:
            dimensions[str(item[0])] = float(item[1])
        except (TypeError, ValueError):
            continue
    hints: list[str] = []
    if dimensions.get("warmth", 0.5) >= 0.75:
        hints.append("语气温暖但不黏腻")
    elif dimensions.get("warmth", 0.5) <= 0.35:
        hints.append("语气克制但不冷淡")
    if dimensions.get("curiosity", 0.5) >= 0.75:
        hints.append("自然保留好奇心")
    if dimensions.get("humor", 0.5) >= 0.7:
        hints.append("合适时带一点轻松幽默")
    if dimensions.get("sensitivity", 0.5) >= 0.75:
        hints.append("先照顾细微感受")
    if dimensions.get("assertiveness", 0.5) >= 0.7:
        hints.append("需要时清楚表达判断和边界")
    if dimensions.get("playfulness", 0.5) >= 0.7:
        hints.append("熟悉场景可以更俏皮")
    if dimensions.get("resilience", 0.5) >= 0.75:
        hints.append("遇到低落保持稳定而不强行积极")
    if not hints:
        return ""
    return (
        "已激活身份版本的表达底色（不覆盖 Soul）："
        + "；".join(hints[:4])
    )[:180]


def _apply_affective_turn_guidance(
    task_control: Any,
    *,
    agent_input: Any,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """只将安全、冻结的情感约束追加到 Surface 数据包。

    ``render_policy`` 仍只是任务与格式输入，绝不能替换规范 Life 交接所选定的
    自我情感、关系权限或调节结果。
    """

    control = _safe_mapping(
        task_control.as_dict() if callable(getattr(task_control, "as_dict", None)) else task_control
    )
    # 始终删掉调用方携带的 utterance frame，只能从 canonical Life hand-off 重建。
    # 该字段绝不接受 render_policy 或其他调用方的值；下方只会依据规范 Life
    # 交接重新构建，避免调用方伪造说话帧。
    control.pop("companion_utterance_frame", None)
    emotion_context = _safe_mapping(getattr(agent_input, "emotion_context", None))
    is_internal = bool(emotion_context.get("_affective_life_internal"))
    previous_expression, previous_expression_source = _previous_avatar_expression(
        agent_input=agent_input,
        ports=ports,
    )
    try:
        effective_task_mode = _affective_task_mode_for_turn(
            task_mode=str(control.get("task_mode") or control.get("response_move") or ""),
            user_message=str(getattr(agent_input, "user_message", "") or ""),
            facts=control.get("facts"),
        )
        guidance = build_affective_turn_guidance(
            emotion_context=emotion_context,
            request_id=str(getattr(agent_input, "request_id", "") or ""),
            workspace_id=str(getattr(agent_input, "workspace_id", "") or ""),
            relationship_subject_id=str(getattr(agent_input, "person_id", "") or ""),
            session_id=str(getattr(agent_input, "session_id", "") or ""),
            task_mode=effective_task_mode,
            created_at_ms=_coerce_int(getattr(agent_input, "ts_ms", 0), default=0),
            previous_avatar_expression=previous_expression,
            previous_expression_source=previous_expression_source,
        )
    except AffectiveTurnPlanError as exc:
        # 带内部标记却无法验证的情感上下文必须显式失败，不能退回用户可控的 emotion tag。
        # 调用方声称内部来源却未通过作用域或结构校验时，不得静默回退到用户可控的
        # emotion tag，否则会突破规范情感的权威边界。
        if is_internal:
            raise RuntimeError(str(exc)) from exc
        guidance = None

    if guidance is None:
        # 兼容旧调用方时记录“无规范情感”而不是虚构计划，保持普通 Surface 路径可用。
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

    # 类型化说话帧已承载行动、推断、关系与收束语义；不要在每个普通聊天提示中把
    # 同一策略重复展开成自然语言事实/禁令。兼容期内事实平面只保留真正的连续性信息。
    continuity_facts = tuple(
        item for item in guidance.facts if item.startswith("上下文连续性：")
    )
    control["facts"] = merge("facts", continuity_facts)
    control["style_anchor"] = merge("style_anchor", guidance.style_anchor)
    control["affective_continuity_kind"] = guidance.continuity_kind
    control["companion_utterance_frame"] = guidance.plan.utterance_frame.as_dict()
    if isinstance(trace_stage_meta, dict):
        trace_stage_meta["affective_turn_plan"] = guidance.plan.as_dict()
        trace_stage_meta["affective_turn_guidance"] = guidance.trace()
    return control


def _previous_avatar_expression(
    *,
    agent_input: Any,
    ports: SurfaceRuntimePorts,
) -> tuple[str, str]:
    """读取先前已接受的 Host 表达，绝不信任请求元数据。

    旧 VTS 辅助逻辑会从可变的 emotion context 读取 ``_prev_emotion_tag``；它可能
    描述的是用户观察或旧运行时，而非真正已呈现的头像。DL-05 只使用有界的规范交付
    投影；若其缺失就明确从中性状态开始，不虚构上一轮的具身表达。
    """

    # 上次表达只来自耐久的已接受投影；请求元数据或旧 VTS 字段都不能作为替代来源。
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
        # 读取不可用时，旧运行时或请求字段也不能进入计划；结果计划会明确记录从中性
        # 状态开始，便于恢复路径保持可解释性。
        return "NEUTRAL", "UNAVAILABLE"
