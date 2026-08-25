"""为当前 Surface 对话选择可承接的最近事件与受限记忆上下文。

记忆编排的职责是解释“这句短追问接在什么可见事件之后”，而非代替用户的
显式意图；产物同时驱动事实、禁令和回复计划，保证三者使用同一个绑定决定。
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
import re

from host_brain.surface_runtime.common import (
    _clean_list,
    _coerce_sequence,
    _first_nonempty,
    _safe_mapping,
    _set_agent_input_mapping,
)
from host_brain.surface_runtime.policies import (
    AUTONOMOUS_SELF_QUERY_RE as _AUTONOMOUS_SELF_QUERY_RE,
    morning_greeting_mismatch_candidate as _surface_morning_greeting_mismatch_candidate,
)
from host_brain.surface_runtime.life_projection import (
    surface_life_director_pack as _surface_life_director_pack,
    surface_user_preference_query_owned as _surface_user_preference_query_owned,
)
def _surface_memory_context_orchestrator_pack(
    *,
    agent_input: Any | None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """取得本轮最近可见事件的冻结绑定包。

    该包将短追问与会话中已可见的事件关联，是事实、禁令和计划覆盖共享的输入；
    重入时复用 trace 版本以保持幂等，端口不可用则空降级而非猜测用户所指。
    """

    # 本轮 trace 已有包时直接复用，保证流式重入/恢复不会重新选择绑定对象；
    # 新建包只走端口提供的调试契约，异常则安全降级为空上下文。
    if agent_input is None:
        return {}
    if isinstance(trace_stage_meta, dict):
        existing = _safe_mapping(trace_stage_meta.get("memory_context_orchestrator"))
        if existing:
            return dict(existing)
    try:
        session_id = str(getattr(agent_input, "session_id", "") or "").strip()
        person_id = str(getattr(agent_input, "person_id", "") or "").strip() or "default"
        user_message = str(getattr(agent_input, "user_message", "") or "").strip()
        if not session_id and not user_message:
            return {}
        pack = ports.build_memory_context_debug(
            ledger=ports.get_ledger(),
            session_id=session_id,
            person_id=person_id,
            current_user_message=user_message,
            request_id=str(getattr(agent_input, "request_id", "") or "").strip(),
        )
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["memory_context_orchestrator"] = pack
        return dict(_safe_mapping(pack))
    except Exception:
        return {}


def _surface_memory_context_binding_info(pack: Mapping[str, Any] | None) -> dict[str, Any]:
    """归一化不同编排器版本的绑定结果为受限摘要。

    只保留事件类别、原因、短内容和引用锚点等 Surface 必需字段，并截断可见文本；
    这样下游可解释承接依据，同时不会把调试包的内部结构或长内容送进提示词。
    """

    # 编排器的不同版本可能把绑定写在不同字段；这里归一化成只读摘要，并截断
    # 可见内容，防止诊断包的长文本或内部结构直接扩张到 Surface 提示词。
    pack_map = _safe_mapping(pack)
    binding = _safe_mapping(pack_map.get("current_turn_binding"))
    if not binding:
        binding = _safe_mapping(pack_map.get("binding_decision"))
    selected = _safe_mapping(pack_map.get("selected_context"))
    bound_event = _safe_mapping(
        binding.get("bound_event")
        or binding.get("event")
        or selected.get("bound_event")
        or selected.get("previous_life_proactive_turn")
        or selected.get("previous_visible_assistant_event")
    )
    refs = _safe_mapping(bound_event.get("refs_json"))
    causality = _safe_mapping(bound_event.get("causality_json"))
    content = _first_nonempty(
        binding.get("bound_content"),
        binding.get("content"),
        bound_event.get("content"),
        bound_event.get("content_summary"),
        causality.get("content_summary"),
    )
    if content:
        content = str(content).strip().replace("\r", " ").replace("\n", " ")
        if len(content) > 180:
            content = f"{content[:177]}..."
    event_kind = _first_nonempty(
        binding.get("bound_event_kind"),
        binding.get("event_kind"),
        bound_event.get("event_kind"),
        refs.get("event_kind"),
    )
    binding_kind = _first_nonempty(
        binding.get("binding_kind"),
        binding.get("kind"),
        binding.get("intent_kind"),
        binding.get("query_kind"),
    )
    reason_code = _first_nonempty(
        binding.get("reason_code"),
        binding.get("reason"),
        _safe_mapping(pack_map.get("binding_decision")).get("reason_code"),
    )
    event_id = _first_nonempty(
        binding.get("bound_event_id"),
        binding.get("event_id"),
        bound_event.get("event_id"),
    )
    motive = _first_nonempty(
        binding.get("motive"),
        bound_event.get("motive"),
        causality.get("motive"),
        causality.get("source"),
    )
    reference_anchor = _first_nonempty(
        binding.get("reference_anchor"),
        binding.get("anchor"),
        binding.get("target_label"),
    )
    previous_user_content = _first_nonempty(
        binding.get("bound_previous_user_content"),
        binding.get("previous_user_content"),
    )
    is_followup = bool(
        event_kind
        and (
            str(binding_kind)
            in {"context_question", "continue", "recent_visible_followup", "proactive_followup", "specific_reference_followup"}
            or str(reason_code)
            in {"BOUND_RECENT_VISIBLE_ASSISTANT_EVENT", "BOUND_RECENT_VISIBLE_EVENT", "BOUND_RECENT_VISIBLE_ASSISTANT_REFERENCE"}
        )
    )
    return {
        "binding": binding,
        "bound_event": bound_event,
        "binding_kind": str(binding_kind or ""),
        "reason_code": str(reason_code or ""),
        "event_kind": str(event_kind or ""),
        "event_id": str(event_id or ""),
        "content": str(content or ""),
        "motive": str(motive or ""),
        "reference_anchor": str(reference_anchor or ""),
        "previous_user_content": str(previous_user_content or ""),
        "is_followup": is_followup,
        "is_life_proactive": str(event_kind or "") == "life_proactive_sent",
    }


def _surface_memory_context_should_yield_to_explicit_intent(
    agent_input: Any | None,
    *,
    ports: SurfaceRuntimePorts,
) -> bool:
    """判断最近事件承接是否必须让位于更明确的当前用户意图。"""

    # 明确的自我查询、偏好查询和时间纠正拥有更高的用户意图权：最近事件在此
    # 只能作背景，不能把用户从正在问的新主题强行拉回旧消息。
    text = str(getattr(agent_input, "user_message", "") or "").strip() if agent_input is not None else ""
    if not text:
        return False
    compact_text = re.sub(r"\s+", "", text)
    if _AUTONOMOUS_SELF_QUERY_RE.search(text):
        return True
    if _surface_user_preference_query_owned(agent_input, ports=ports):
        return True
    if _surface_morning_greeting_mismatch_candidate(compact_text):
        try:
            from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415

            environment = _safe_mapping(build_environment_snapshot())
            day_phase = str(environment.get("day_phase") or "").strip()
            return bool(day_phase and day_phase not in {"dawn", "morning"})
        except Exception:
            return False
    return False


def _surface_memory_context_orchestrator_facts(
    *,
    agent_input: Any | None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """将已解析的最近事件绑定转成回复事实，不将候选项当成既定对象。

    只有跨域解析明确为 ``RESOLVED`` 或会话绑定确认是跟进时，才暴露行动目标和
    连续性依据；显式新意图、端口异常或未解析短句都保留普通回复路径。
    """

    # 事实只在没有更明确意图时采用事件绑定；一旦绑定失败或端口异常返回空集，
    # 上游仍可走普通回复链，绝不凭猜测补造“刚才那件事”。
    if _surface_memory_context_should_yield_to_explicit_intent(agent_input, ports=ports):
        return ("记忆编排：当前输入已有明确语义目标，最近事件绑定这轮只降为背景，不接管主话题。",)
    pack = _surface_memory_context_orchestrator_pack(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    if not pack:
        return ()
    try:
        binding_info = _surface_memory_context_binding_info(pack)
        selected = _safe_mapping(pack.get("selected_context"))
        event_resolution = _safe_mapping(
            selected.get("conversation_event_resolution")
            or pack.get("conversation_event_resolution")
        )
        facts: list[str] = [
            "记忆编排：当前用户消息和当前会话最近轮次优先；长期记忆、lesson、数字生命状态只能作为证据或语气影响，不能覆盖当前话题。",
        ]
        # 跨域事件解析已经给出 RESOLVED 才可把 action/目标写进事实；未解析的
        # short follow-up 仍只能依赖当前会话绑定，避免把候选项目当作已选对象。
        if str(event_resolution.get("status") or "") == "RESOLVED":
            action_kind = str(event_resolution.get("action_kind") or "")
            binding_kind = str(event_resolution.get("binding_kind") or "")
            domain = str(event_resolution.get("domain") or "")
            reason_code = str(event_resolution.get("reason_code") or "")
            facts.append(
                f"事件承接：当前用户输入已绑定到最近事件，action_kind={action_kind}，binding_kind={binding_kind}，domain={domain}，reason={reason_code}。"
            )
            target_item = _safe_mapping(event_resolution.get("target_item"))
            if target_item:
                item_rank = str(target_item.get("rank") or "")
                item_title = str(target_item.get("title") or "")
                detail_url = str(target_item.get("detail_url") or "")
                item_bits = []
                if item_rank:
                    item_bits.append(f"第 {item_rank} 项")
                if item_title:
                    item_bits.append(f"《{item_title}》")
                if detail_url:
                    item_bits.append(f"详情页={detail_url}")
                if item_bits:
                    facts.append("事件目标：" + "；".join(item_bits))
            guidance = _safe_mapping(event_resolution.get("answer_guidance"))
            goal = str(guidance.get("goal") or "")
            if goal:
                facts.append(f"事件动作目标：{goal}")
            if action_kind == "fetch_item_detail":
                facts.append("本轮应围绕已绑定作品的详情/内容回答；如果目标项已有标题或详情页，不要反问“哪方面”或“是哪一本”。")
            elif action_kind in {
                "retry_skill_or_work_event",
                "explain_failure",
                "explain_or_continue_skill_result",
                "explain_recent_event_failure",
                "retry_recent_operational_event",
                "cancel_recent_operational_event",
                "push_recent_artifact_or_result",
                "present_recent_artifact_or_observation",
                "report_recent_operational_status",
                "continue_recent_operational_event",
                "adjust_recent_operational_system",
            }:
                facts.append("本轮应围绕最近系统事件继续处理；事件可能来自浏览器、文件/EPUB、后台任务、数字生命、语音、多模态或 skill，不要跳到旧推荐、旧 ASR、旧寒暄或长期语义记忆。")
            elif action_kind == "answer_recent_visible_event":
                facts.append("本轮应解释最近一条可见 assistant 消息的语境；不要把短句追问当成全新话题。")
        # 对最近可见消息的追问必须携带绑定依据，特别是泛指对象没有明确实体时，
        # 要让模型承认语境不完整，而不是编造一个用户“心里所指”的对象。
        if binding_info.get("is_followup"):
            event_kind = str(binding_info.get("event_kind") or "")
            binding_kind = str(binding_info.get("binding_kind") or "")
            reason_code = str(binding_info.get("reason_code") or "")
            content = str(binding_info.get("content") or "")
            motive = str(binding_info.get("motive") or "")
            reference_anchor = str(binding_info.get("reference_anchor") or "")
            previous_user_content = str(binding_info.get("previous_user_content") or "")
            facts.append(
                f"当前承接绑定：用户这句话是在追问最近一条可见 assistant 消息，event_kind={event_kind}，binding_kind={binding_kind}，reason={reason_code}。"
            )
            if content:
                facts.append(f"被追问的上一条可见消息：{content}")
            if previous_user_content:
                facts.append(f"上一条可见消息前的用户话：{previous_user_content}")
            if reference_anchor:
                facts.append(f"用户追问的具体对象词：{reference_anchor}")
                facts.append(
                    f"如果上一条并没有给出“{reference_anchor}”的具体名称或对象，本轮必须自然承认刚才只是顺着上一句接话，不要假装有一个明确对象。"
                )
                if previous_user_content:
                    facts.append(
                        f"本轮承接核心：上一条里的“{reference_anchor}”来自用户前一句“{previous_user_content}”的顺口延展；不是一个已经指定好的具体对象。"
                    )
            if motive:
                facts.append(f"上一条消息的生命/行为动机线索：{motive}")
            if binding_info.get("is_life_proactive"):
                facts.append(
                    "本轮回复目标：先解释上一条主动消息为什么会发出，以及它和当时生命状态/闭环动作的关系；不要把“怎么了”当成新的闲聊入口、试探或旧话题追问。"
                )
        suppressed = list(pack.get("suppressed_items") or [])
        if suppressed:
            reasons = ", ".join(
                reason
                for reason in (str(_safe_mapping(item).get("reason") or "").strip() for item in suppressed[:4])
                if reason
            )
            if reasons:
                facts.append(f"记忆抑制：本轮有低优先级上下文被压制，原因={reasons}。")
        return tuple(_clean_list(facts))
    except Exception:
        return ()


def _surface_memory_context_orchestrator_must_not_do(
    *,
    agent_input: Any | None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """为最近事件承接生成同源的回复禁令，防止重试出现绑定漂移。"""

    # 禁令与上面的事实共用同一 pack。这样计划重试时不会出现“事实承接 A、
    # 禁令却保护 B”的不一致，也不会在显式意图场景施加旧事件的限制。
    if _surface_memory_context_should_yield_to_explicit_intent(agent_input, ports=ports):
        return ()
    pack = _surface_memory_context_orchestrator_pack(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    binding_info = _surface_memory_context_binding_info(pack)
    selected = _safe_mapping(pack.get("selected_context"))
    event_resolution = _safe_mapping(
        selected.get("conversation_event_resolution")
        or pack.get("conversation_event_resolution")
    )
    event_resolved = str(event_resolution.get("status") or "") == "RESOLVED"
    if not binding_info.get("is_followup") and not event_resolved:
        return ()
    rules = [
        "不要忽略 current_turn_binding；用户短句追问必须先承接被绑定的最近可见消息。",
        "不要跳到旧推荐、旧任务、旧 ASR、长期语义记忆或未绑定主题。",
        "不要只评价用户这句“怎么了/继续/然后呢”本身，也不要改写成“你在试探我”这类跑题回应。",
    ]
    if event_resolved:
        action_kind = str(event_resolution.get("action_kind") or "")
        target_item = _safe_mapping(event_resolution.get("target_item"))
        rules.insert(0, "不要忽略 conversation_event_resolution；它是本轮跨领域承接和动作目标的上游解析结果。")
        if target_item.get("title") or target_item.get("detail_url"):
            rules.append("推荐项目标已经明确时，不要反问用户是哪一本、哪方面或要看什么详情。")
        if action_kind in {"fetch_item_detail", "package_item_epub", "record_recommendation_feedback"}:
            rules.append("不要让旧小说偏好、旧推荐、旧测试上下文覆盖当前已绑定的推荐项。")
        if action_kind in {
            "retry_skill_or_work_event",
            "explain_failure",
            "explain_or_continue_skill_result",
            "explain_recent_event_failure",
            "retry_recent_operational_event",
            "cancel_recent_operational_event",
            "push_recent_artifact_or_result",
            "present_recent_artifact_or_observation",
            "report_recent_operational_status",
            "continue_recent_operational_event",
            "adjust_recent_operational_system",
        }:
            rules.append("不要把最近系统事件承接解释成普通闲聊、寒暄或主动消息追问。")
    if binding_info.get("is_life_proactive"):
        rules.append("不要只说“没什么”“突然想喊你”这类空泛解释；必须点明上一条主动消息里的具体触发原因。")
        rules.append("不要把主动消息解释成现实观察；只能按绑定事件、生命状态和可见证据解释为什么发出。")
    if str(binding_info.get("reference_anchor") or ""):
        rules.append("不要把未指定对象说成用户心里已经有明确对象，也不要编成“你心里想的那个”。")
    return tuple(_clean_list(rules))


def _surface_memory_context_orchestrator_must_include(
    *,
    agent_input: Any | None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    # must_include 只要求自然承接已经绑定的可见消息；主动消息需要解释触发
    # 来源，普通跟进则不应把内部 binding 字段或未证实对象暴露给用户。
    if _surface_memory_context_should_yield_to_explicit_intent(agent_input, ports=ports):
        return ()
    pack = _surface_memory_context_orchestrator_pack(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    binding_info = _surface_memory_context_binding_info(pack)
    if not binding_info.get("is_followup"):
        return ()
    content = str(binding_info.get("content") or "")
    reference_anchor = str(binding_info.get("reference_anchor") or "")
    if binding_info.get("is_life_proactive"):
        return _clean_list(
            [
            "承接并解释上一条主动消息的触发原因/状态来源。",
            f"点明上一条主动消息里的具体原因：{content}" if content else "",
            "说明现在已经接到用户回应，而不是继续疑问用户在不在。",
            ]
        )
    rows = ["承接并解释被绑定的上一条可见 assistant 消息。"]
    if reference_anchor:
        rows.append(f"回复里必须明确出现“{reference_anchor}”，并说明它没有具体指哪一个，只是顺着上一句泛指/接话。")
    return tuple(_clean_list(rows))


def _surface_apply_memory_context_reply_plan_override(
    reply_plan: Mapping[str, Any],
    *,
    agent_input: Any | None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """在绑定确定时窄化本轮回复计划，而不修改用户输入或领域状态。

    覆盖仅改变任务模式、动作和必要事实；显式用户意图优先，未解析事件保持原计划，
    避免把候选上下文升级成对外行动或虚构的指代对象。
    """

    # 计划覆盖是受限的：解析到明确事件/跟进后仅替换本轮的任务模式、动作与
    # 必要事实，不修改用户原始输入，也不在有显式意图时抢占回复计划。
    plan = dict(reply_plan or {})
    if _surface_memory_context_should_yield_to_explicit_intent(agent_input, ports=ports):
        return plan
    pack = _surface_memory_context_orchestrator_pack(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    binding_info = _surface_memory_context_binding_info(pack)
    selected = _safe_mapping(pack.get("selected_context"))
    event_resolution = _safe_mapping(
        selected.get("conversation_event_resolution")
        or pack.get("conversation_event_resolution")
    )
    if str(event_resolution.get("status") or "") == "RESOLVED":
        # 只有解析器确定的事件可驱动跨域动作，例如已绑定推荐项详情或最近任务
        # 的继续/取消；否则保持原计划，防止候选事件触发外部行为叙述。
        action_kind = str(event_resolution.get("action_kind") or "")
        target_item = _safe_mapping(event_resolution.get("target_item"))
        if action_kind == "fetch_item_detail":
            plan["task_mode"] = "recommendation_item_detail_followup"
            plan["response_move"] = "answer_bound_recommendation_item_detail"
            plan["interaction_move"] = "answer_bound_recommendation_item_detail"
            title = str(target_item.get("title") or "")
            rank = str(target_item.get("rank") or "")
            plan["response_goal"] = (
                "回答用户指定推荐项的详细内容；目标已经由 conversation_event_resolution 绑定，不能反问是哪一本或哪方面。"
            )
            must_include = list(plan.get("must_include") or [])
            if rank or title:
                must_include.append(f"明确承接已绑定推荐项：第{rank}本《{title}》".strip())
            detail_url = str(target_item.get("detail_url") or "")
            if detail_url:
                must_include.append(f"使用该作品详情页作为证据：{detail_url}")
            plan["must_include"] = _clean_list(must_include)
        elif action_kind in {
            "retry_skill_or_work_event",
            "explain_failure",
            "explain_or_continue_skill_result",
            "explain_recent_event_failure",
            "retry_recent_operational_event",
            "cancel_recent_operational_event",
            "push_recent_artifact_or_result",
            "present_recent_artifact_or_observation",
            "report_recent_operational_status",
            "continue_recent_operational_event",
            "adjust_recent_operational_system",
        }:
            plan["task_mode"] = "recent_operational_event_followup"
            plan["response_move"] = action_kind
            plan["interaction_move"] = action_kind
            plan["response_goal"] = "围绕最近系统事件继续处理或解释，不切换到无关话题；系统事件可以来自浏览器、产物文件、EPUB、后台任务、数字生命、语音、多模态或 skill。"
        elif action_kind == "answer_recent_visible_event":
            plan["task_mode"] = "recent_visible_event_followup"
            plan["response_move"] = "explain_recent_visible_event"
            plan["interaction_move"] = "explain_recent_visible_event"
            plan["response_goal"] = "解释上一条可见消息的语境和动机，不把短句追问当新话题。"
    if binding_info.get("is_followup"):
        content = str(binding_info.get("content") or "")
        if binding_info.get("is_life_proactive"):
            plan["task_mode"] = "proactive_context_followup"
            plan["response_move"] = "explain_recent_proactive_message"
            plan["interaction_move"] = "explain_recent_proactive_message"
            plan["response_goal"] = (
                f"解释上一条主动消息的具体来源：{content}；自然承接用户追问。"
                if content
                else "解释上一条主动消息的来源，并自然承接用户追问。"
            )
            plan["reply_contract"] = "承接上一条主动消息，必须点明具体触发原因，不跑到旧话题。"
        else:
            plan["task_mode"] = "recent_visible_followup"
            plan["response_move"] = "explain_recent_visible_message"
            plan["interaction_move"] = "explain_recent_visible_message"
            plan["response_goal"] = "解释被绑定的上一条可见消息。"
        must_include = list(_coerce_sequence(plan.get("must_include")))
        if content:
            must_include.append(f"点明上一条可见消息里的具体原因：{content}")
        reference_anchor = str(binding_info.get("reference_anchor") or "")
        if reference_anchor:
            must_include.append(f"明确说出“{reference_anchor}”没有具体指哪一个；不要硬编具体对象。")
        plan["must_include"] = _clean_list(must_include)
    return plan


def _surface_apply_autonomous_self_query_reply_plan_override(
    reply_plan: Mapping[str, Any] | None,
    *,
    agent_input: Any | None = None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """把明确的自主状态询问约束到生命导演的真实投影。

    这是提示层计划覆盖，不写入生命状态；回答仅可使用当前议程、项目或成长信号，
    从而避免角色想象被表述成未经证实的现实活动。
    """

    # “你最近在做什么”属于对自主状态的显式查询，必须由 life director 的
    # 真实议程/项目/成长信号回答，不能从最近对话或角色想象推导现实活动。
    plan = dict(reply_plan or {})
    text = str(getattr(agent_input, "user_message", "") or "").strip()
    if not text or not _AUTONOMOUS_SELF_QUERY_RE.search(text):
        return plan

    pack = _surface_life_director_pack(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    adaptive_self = _safe_mapping(pack.get("adaptive_self_pack"))
    agenda = [
        _safe_mapping(item)
        for item in list(adaptive_self.get("active_agenda") or [])
        if isinstance(item, Mapping)
    ]
    projects = [
        _safe_mapping(item)
        for item in list(adaptive_self.get("active_projects") or [])
        if isinstance(item, Mapping)
    ]
    growth = [
        _safe_mapping(item)
        for item in list(adaptive_self.get("recent_growth") or [])
        if isinstance(item, Mapping)
    ]

    plan["task_mode"] = "autonomous_self_status_query"
    plan["primary_topic"] = "autonomous_self_state"
    plan["response_move"] = "answer_autonomous_self_state"
    plan["interaction_move"] = "answer_autonomous_self_state"
    plan["response_goal"] = (
        "回答用户对 Natsume 自主状态、最近变化或刚才在做什么的询问；"
        "必须依据当前自我议程、私有项目或成长信号，不编造现实动作。"
    )
    plan["reply_contract"] = (
        "自然回答她自己的当前状态或最近变化；至少承接一个真实自我议程、私有项目或成长倾向；"
        "不要输出内部字段名，不要说在训练模型，不要编造上课、睡觉、沙发、吃饭、看屏幕等现实动作。"
    )
    must_include = list(_coerce_sequence(plan.get("must_include")))
    if agenda:
        first_agenda = agenda[0]
        agenda_summary = str(first_agenda.get("summary") or first_agenda.get("kind") or "").strip()
        if agenda_summary:
            must_include.append(f"至少承接一个真实当前议程，例如：{agenda_summary}")
    if projects:
        first_project = projects[0]
        project_title = str(first_project.get("title") or first_project.get("project_id") or "").strip()
        project_step = str(first_project.get("next_step") or "").strip()
        if project_title:
            must_include.append(f"可以自然带出当前私有项目：{project_title}")
        if project_step:
            must_include.append(f"如果提到最近在做什么，应优先落到这个具体方向：{project_step}")
    if growth:
        first_growth = growth[0]
        signal_kind = str(first_growth.get("signal_kind") or "").strip()
        if signal_kind:
            must_include.append(f"如果回答最近变化，优先落到真实成长信号：{signal_kind}")
    plan["must_include"] = _clean_list(must_include)
    return plan


def _surface_sync_reply_plan(
    *,
    agent_input: Any,
    trace_stage_meta: dict[str, Any] | None,
    reply_plan: Mapping[str, Any] | None,
) -> None:
    """将本轮最终计划同步到兼容输入字段与 trace，供后续子链一致复用。"""

    # 同一份复制后的计划同步到兼容字段与本轮 trace，避免不同 Surface 子链各自
    # 读取旧计划。写入是本轮编排状态，不会回写持久化用户/生命体数据。
    plan = dict(reply_plan or {})
    if not plan:
        return
    _set_agent_input_mapping(agent_input, "surface_task_control", plan)
    _set_agent_input_mapping(agent_input, "reply_plan", plan)
    _set_agent_input_mapping(agent_input, "render_policy", plan)
    if isinstance(trace_stage_meta, dict):
        trace_stage_meta["surface_selected_reply_plan"] = dict(plan)
        trace_stage_meta["surface_task_control"] = dict(plan)
        trace_stage_meta["reply_plan"] = dict(plan)
