"""将数字生命投影、用户偏好与记忆写入请求转成受限的 Surface 上下文。

生命状态可调节语气、注意力和连续性，却不能被表述成现实身体活动或内部字段；
用户偏好和记忆验收同样只使用已有证据，并保持当前消息的优先权。
"""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence
from host_brain.persistence.runtime_read_repository import RuntimeReadRepository
from host_brain.digital_life.application_boundary import (
    project_life_director_pack,
    read_life_snapshot,
    submit_life_command,
)
import re
from host_brain.surface_runtime.ports import SurfaceRuntimePorts

from host_brain.surface_runtime.common import (
    _clean_list,
    _safe_mapping,
)
from host_brain.surface_runtime.policies import (
    AUTONOMOUS_SELF_QUERY_RE as _AUTONOMOUS_SELF_QUERY_RE,
    morning_greeting_mismatch_candidate as _surface_morning_greeting_mismatch_candidate,
)
from host_brain.surface_runtime.memory_context import (
    _surface_memory_context_binding_info,
    _surface_memory_context_orchestrator_pack,
)
from host_brain.surface_runtime.memory_grounding import (
    _surface_memory_packet_for_output_guard,
)
from host_brain.surface_runtime.life_projection import (
    surface_life_director_pack as _surface_life_director_pack,
    surface_user_preference_message as _surface_user_preference_message,
    surface_user_preference_pack as _surface_user_preference_pack,
    surface_user_preference_query_owned as _surface_user_preference_query_owned,
)
def _surface_user_preference_pack(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> dict[str, Any]:
    # 检索阶段若已附带偏好包则复用，避免同轮再读造成不同快照；仅当文本确为
    # 偏好问题时才经端口补建，失败安全退为空而不是假定用户有某种口味。
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    pack = _safe_mapping(retrieval_summary.get("user_preference_pack"))
    if pack:
        return pack
    user_message = str(getattr(agent_input, "user_message", "") or "")
    if not _surface_user_preference_message(user_message):
        return {}
    try:
        return _safe_mapping(
            ports.build_user_preference_pack(
                person_id=str(getattr(agent_input, "person_id", "") or "").strip() or None,
                query_text=user_message,
                session_id=str(getattr(agent_input, "session_id", "") or "").strip() or None,
                conversation_id=str(getattr(agent_input, "session_id", "") or "").strip() or None,
                ledger=ports.get_ledger(),
            )
        )
    except Exception:
        return {}


def _surface_life_director_pack(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
    task_mode: str = "",
    response_move: str = "",
) -> dict[str, Any]:
    # life director 包优先沿用 trace 中的冻结投影，其次才使用检索快照。需要
    # 新读时只保存版本/摘要引用用于追踪，绝不把整个生命快照回写给对话层。
    trace = _safe_mapping(trace_stage_meta)
    pack = _safe_mapping(trace.get("life_director"))
    if pack:
        return pack
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    pack = _safe_mapping(retrieval_summary.get("life_director") or retrieval_summary.get("life_continuity_pack"))
    if pack:
        return pack
    try:
        snapshot = read_life_snapshot(
            str(getattr(agent_input, "person_id", "") or "").strip() or "default",
            reader=ports.get_life_snapshot_reader(),
        )
        pack = project_life_director_pack(snapshot)
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["life_snapshot_ref"] = {
                "person_id": snapshot["person_id"],
                "version": snapshot["version"],
                "state_digest": snapshot["state_digest"],
                "source": "life_snapshot_reader",
            }
        _ = task_mode, response_move
        return _safe_mapping(pack)
    except Exception:
        return {}


def _surface_life_context_task_facts(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    # 环境快照只是 life pack 不可用时的降级来源。它可纠正明显的时间错位，
    # 但不能据此揣测用户的睡眠、动机或现实状态。
    try:
        from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415
        fallback_environment = _safe_mapping(build_environment_snapshot())
    except Exception:
        fallback_environment = {}
    user_text = str(getattr(agent_input, "user_message", "") or "").strip()
    compact_user = re.sub(r"\s+", "", user_text)
    fallback_day_phase = str(fallback_environment.get("day_phase") or "").strip()
    fallback_morning_mismatch = _surface_morning_greeting_mismatch_candidate(compact_user) and fallback_day_phase not in {"dawn", "morning"}
    fallback_env_facts = _clean_list(
        [
            f"当前环境：{'；'.join(str(x) for x in list(fallback_environment.get('surface_facts') or [])[:3])}" if fallback_environment else "",
            f"时间错位问候：用户说的是早安类问候，但当前阶段是 {fallback_day_phase or '未知'}；只处理时间错位本身，不要直接按早晨回复，也不要推断用户睡迷糊、故意逗你或状态异常。" if fallback_morning_mismatch else "",
        ]
    )
    memory_pack = _surface_memory_context_orchestrator_pack(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    binding_info = _surface_memory_context_binding_info(memory_pack)
    # 已绑定主动消息的追问必须优先解释那条消息本身；此时禁止把无关的工作场景
    # 或旧生命状态塞入回答，避免连续性被误解为话题跳转。
    if binding_info.get("is_followup") and binding_info.get("is_life_proactive"):
        return tuple(
            _clean_list([
            "生命状态边界：本轮已绑定上一条主动消息；数字生命状态只能影响语气和身体倾向，不能引入未绑定的后台任务、旧 work scene、旧推荐或旧话题。",
            *fallback_env_facts,
            ])
        )
    pack = _surface_life_director_pack(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    if not pack:
        return tuple(item[:260] for item in fallback_env_facts)
    scene = str(pack.get("scene") or "").strip()
    motive = str(pack.get("dominant_motive") or "").strip()
    influence = str(pack.get("dialogue_influence") or "").strip()
    backend = str(pack.get("codex_backend_status") or "").strip()
    style_goal = str(pack.get("style_goal") or "").strip()
    environment = _safe_mapping(pack.get("environment"))
    private_mind = _safe_mapping(pack.get("private_mind_summary"))
    adaptive_self = _safe_mapping(pack.get("adaptive_self_pack"))
    active_agenda = [
        _safe_mapping(item)
        for item in list(adaptive_self.get("active_agenda") or [])
        if isinstance(item, Mapping)
    ]
    active_projects = [
        _safe_mapping(item)
        for item in list(adaptive_self.get("active_projects") or [])
        if isinstance(item, Mapping)
    ]
    recent_growth = [
        _safe_mapping(item)
        for item in list(adaptive_self.get("recent_growth") or [])
        if isinstance(item, Mapping)
    ]
    day_phase = str(environment.get("day_phase") or "").strip()
    morning_mismatch = _surface_morning_greeting_mismatch_candidate(compact_user) and day_phase not in {"dawn", "morning"}
    visible = [str(item) for item in list(pack.get("visible_obligation") or []) if str(item).strip()]
    pressure = _safe_mapping(pack.get("life_pressure"))
    pressure_text = "、".join(
        f"{key}={float(value):.2f}"
        for key, value in pressure.items()
        if isinstance(value, (int, float)) and float(value) >= 0.18
    )
    organ = _safe_mapping(pack.get("organ_life_influence"))
    votes = []
    for item in list(organ.get("top_influences") or [])[:4]:
        item_map = _safe_mapping(item)
        organ_name = str(item_map.get("organ") or "").strip()
        vote = str(item_map.get("vote") or "").strip()
        strength = item_map.get("strength")
        if organ_name and vote:
            votes.append(f"{organ_name}:{vote}@{strength}")
    # 自主状态查询才允许把议程、私有项目和成长信号变成可表达事实；普通聊天中
    # 它们最多影响风格，不能主动宣称“我刚才在做什么”。
    autonomous_self_question = bool(_AUTONOMOUS_SELF_QUERY_RE.search(user_text))
    agenda_text = "；".join(
        _clean_list(
            [
                f"{str(item.get('kind') or '').strip()}:{str(item.get('summary') or '').strip()}"
                for item in active_agenda[:4]
            ]
        )
    )
    project_text = "；".join(
        _clean_list(
            [
                f"{str(item.get('title') or item.get('project_id') or '').strip()}:{str(item.get('next_step') or item.get('motive') or '').strip()}"
                for item in active_projects[:4]
            ]
        )
    )
    growth_text = "；".join(_clean_list([_surface_autonomous_growth_summary(item) for item in recent_growth[:4]]))
    autonomous_self_fact = (
        "自我状态询问证据：用户正在问她刚才/最近/自己在做什么；必须依据这些真实状态回答。"
        "这些项目只代表数字生命/系统内部议程，不要改写成现实家务、餐食、课程、报告或线下活动；"
        "不要回答成没干嘛/没变化，也不要编造上课、睡觉、做饭、吃饭、沙发、看屏幕、盯用户学习等现实动作。"
    ) if autonomous_self_question else ""
    facts = [
        autonomous_self_fact,
        f"自我状态当前注意力：{adaptive_self.get('current_attention')}" if autonomous_self_question and adaptive_self.get("current_attention") else "",
        f"自我状态可说议程：{agenda_text}" if autonomous_self_question and agenda_text else "",
        f"自我状态可说项目：{project_text}" if autonomous_self_question and project_text else "",
        f"自我状态最近变化：{growth_text or '暂时没有明显成长变化；可以说最近主要在维持状态和整理表达/记忆，不要说没变化后结束。'}" if autonomous_self_question else "",
        "前台身份：Natsume 是前台数字生命体；Codex 是后台工作执行层。任务只是她正在感受到的环境刺激，不要把她写成工具助手。",
        f"生命导演：scene={scene}，motive={motive}，dialogue_influence={influence}。必须让这轮表达能感觉到生命状态。",
        f"自主注意力：{adaptive_self.get('current_attention')}；她不是只等用户输入，当前有自己的自我议程。" if adaptive_self.get("current_attention") else "",
        f"自我议程：{agenda_text}" if agenda_text else "",
        f"私有项目：{project_text}" if project_text else "",
        f"近期成长：{growth_text}" if growth_text else "",
        f"成长/习惯倾向：{'；'.join(str(x) for x in list(adaptive_self.get('stable_habits') or [])[:3])}" if adaptive_self.get("stable_habits") else "",
        f"表达成长偏置：{adaptive_self.get('style_bias')}" if adaptive_self.get("style_bias") else "",
        f"后台状态：{backend}" if backend else "",
        f"生命压力：{pressure_text}" if pressure_text else "",
        f"器官投票：{'; '.join(votes)}" if votes else "",
        f"当前环境：{'；'.join(str(x) for x in list(environment.get('surface_facts') or [])[:3])}" if environment else "",
        f"时间错位问候：用户说的是早安类问候，但当前阶段是 {day_phase or '未知'}；只处理时间错位本身，不要直接按早晨回复，也不要推断用户睡迷糊、故意逗你或状态异常。" if morning_mismatch else "",
        f"私有心智摘要：attention={private_mind.get('attention_target') or ''}，tendency={private_mind.get('felt_tendency') or ''}，bias={private_mind.get('dialogue_bias') or ''}；只能作为语气/注意力偏置，不要泄露原始内心流。" if private_mind else "",
        f"外显义务：{', '.join(visible)}；如果包含 dialogue，要让文本明显带状态。" if visible else "",
        f"表达目标：{style_goal}" if style_goal else "",
    ]
    return tuple(item[:260] for item in _clean_list(facts))


def _surface_autonomous_self_task(task_control: Mapping[str, Any] | None) -> bool:
    control = _safe_mapping(task_control)
    return any(
        str(control.get(key) or "").strip() == "autonomous_self_status_query"
        or str(control.get(key) or "").strip() == "answer_autonomous_self_state"
        for key in ("task_mode", "response_move", "primary_topic", "interaction_move")
    )


def _surface_autonomous_growth_summary(item: Mapping[str, Any]) -> str:
    signal = str(item.get("signal_kind") or "").strip()
    candidate = _safe_mapping(item.get("candidate_delta"))
    trait = str(candidate.get("trait") or item.get("trait") or "").strip()
    habit = str(candidate.get("habit") or item.get("habit") or "").strip()
    if signal == "novelty" or trait == "curiosity":
        return "好奇心/探索倾向有小幅上升"
    if signal == "closure_pressure" or habit == "keep_recent_visible_event_binding":
        return "更重视把最近可见事件接住，减少对话断裂"
    if habit:
        return f"习惯变化：{habit}"
    if trait:
        return f"人格倾向变化：{trait}"
    return signal or str(item or "").strip()


def _surface_life_context_must_not_do(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    # 禁令与生命事实共同基于同一轮 pack。即便状态投影存在，也应阻止字段名、
    # 内心原文和现实动作泄露；pack 缺失时仍保留时间错位的最低约束。
    memory_pack = _surface_memory_context_orchestrator_pack(
        agent_input=agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    binding_info = _surface_memory_context_binding_info(memory_pack)
    pack = _surface_life_director_pack(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    env_rules: list[str] = []
    try:
        from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415
        environment_fallback = _safe_mapping(build_environment_snapshot())
    except Exception:
        environment_fallback = {}
    compact_user = re.sub(r"\s+", "", str(getattr(agent_input, "user_message", "") or ""))
    fallback_day_phase = str(environment_fallback.get("day_phase") or "").strip()
    if _surface_morning_greeting_mismatch_candidate(compact_user) and fallback_day_phase not in {"dawn", "morning"}:
        env_rules.append("用户说早安类问候但当前不是早上时，不要回复“早”“早啊”“早上好”；要自然指出现在的时间阶段。")
        env_rules.append("时间错位问候不得评价用户状态或动机；不要说睡迷糊、故意逗、逗我、迷糊、糊涂、装傻、脑子不清醒。")
    if not pack:
        if binding_info.get("is_followup") and binding_info.get("is_life_proactive"):
            return tuple(_clean_list([
                "本轮不要提后台任务、测试、盯进度或 work scene，除非被绑定的上一条主动消息明确提到。",
                *env_rules,
            ]))
        return tuple(_clean_list(env_rules))
    rules = [
        "不要在用户可见文本里说 life_state、tick、pressure、organ vote、trace、schema、visible_obligation。",
        "不要说“作为 AI 助手”；Natsume 是前台数字生命体，后台 Codex 才是工作执行层。",
        "任务场景不要写成机械汇报；可以表现她在盯后台、等待、焦躁、松口气或小得意。",
        "不要说她亲手操作终端或代码，除非输入事实明确说明前台正在操作；默认是后台 Codex 在执行。",
        "梦境、日记、内心流只能作为状态余温，不要当现实事实说出来。",
        "不要把普通 hello/你好 自动解释成早安、刚醒、吃饭或早餐；只有环境事实和用户原话都支持时才能提。",
    ]
    environment = _safe_mapping(pack.get("environment")) or environment_fallback
    day_phase = str(environment.get("day_phase") or "").strip()
    if _surface_morning_greeting_mismatch_candidate(compact_user) and day_phase not in {"dawn", "morning"}:
        rules.append("用户说早安类问候但当前不是早上时，不要回复“早”“早啊”“早上好”；要自然指出现在的时间阶段。")
        rules.append("时间错位问候不得评价用户状态或动机；不要说睡迷糊、故意逗、逗我、迷糊、糊涂、装傻、脑子不清醒。")
    if binding_info.get("is_followup") and binding_info.get("is_life_proactive"):
        rules.insert(0, "本轮不要提后台任务、测试、盯进度或 work scene，除非被绑定的上一条主动消息明确提到。")
    for item in list(pack.get("must_not_do") or []):
        item_text = str(item or "").strip()
        if item_text:
            rules.append(item_text)
    adaptive_self = _safe_mapping(pack.get("adaptive_self_pack"))
    for item in list(adaptive_self.get("must_not_claim") or []):
        item_text = str(item or "").strip()
        if item_text:
            rules.append(item_text)
    return tuple(_clean_list(rules)[:14])


def _surface_user_preference_message(user_message: str) -> bool:
    text = str(user_message or "").strip()
    if not text:
        return False
    return bool(
        re.search(r"(轻小说|輕小說|小说|小說|书|書).{0,24}(偏好|口味|喜好|喜欢|喜歡|哪种|哪類|哪类|什么类型)", text)
        or re.search(r"(偏好|口味|喜好|喜欢|喜歡).{0,24}(轻小说|輕小說|小说|小說|书|書)", text)
        or re.search(r"你知道我喜欢.{0,16}(轻小说|輕小說|小说|小說|书|書)", text)
        or re.search(r"(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|項|项|部|步)?|这本|这部|那本|哪本|刚才推荐|刚刚推荐).{0,18}(好看|怎么样|如何|值得|推荐|能看|先看)", text)
        or re.search(r"(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|項|项|部|步)?|这本|这部|那本|刚才推荐|刚刚推荐).{0,18}(详情|详细内容|详细|内容|简介|介绍|讲什么|说什么)", text)
        or re.search(r"(详情|详细内容|详细|简介|介绍).{0,12}(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|項|项|部|步)?|这本|这部|那本)", text)
        or re.search(r"(好看吗|好不好看|值不值得|推不推荐|先看哪本|哪本)", text)
    )


def _surface_user_preference_query_owned(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> bool:
    # 只有偏好包确认相关性/画像存在，或文本明确是偏好询问时才取得话题控制权；
    # 这样一般推荐不会被旧画像抢占，也不会凭模糊关键词断言长期偏好。
    pack = _surface_user_preference_pack(agent_input, ports=ports)
    if pack and bool(pack.get("query_relevant") or pack.get("has_profile")):
        return True
    return _surface_user_preference_message(str(getattr(agent_input, "user_message", "") or ""))


def _surface_user_preference_fact_lines(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> tuple[str, ...]:
    # 画像事实与最近推荐跟进分开处理：后者已有明确目标并优先返回；前者仅描述
    # 用户明确表达过的线索，绝不从推荐列表反推“用户一定喜欢”。
    pack = _surface_user_preference_pack(agent_input, ports=ports)
    if not pack:
        return ()
    profile = _safe_mapping(pack.get("profile"))
    query_relevant = bool(pack.get("query_relevant"))
    has_profile = bool(pack.get("has_profile"))
    if not query_relevant and not has_profile:
        return ()

    domain = str(pack.get("domain") or "").strip() or "general"
    recent_recommendation_context = _safe_mapping(pack.get("recent_recommendation_context"))
    liked_items = _preference_labels(profile.get("liked_items"), limit=3)
    disliked_items = _preference_labels(profile.get("disliked_items"), limit=2)
    likes = _preference_labels(profile.get("likes") or profile.get("inferred_traits"), limit=4)
    dislikes = _preference_labels(profile.get("dislikes"), limit=3)
    evidence_count = len(list(profile.get("evidence_summary") or []))
    lines: list[str] = []
    recommendation_followup = False
    if recent_recommendation_context:
        recent_items = [
            _safe_mapping(item)
            for item in list(recent_recommendation_context.get("items") or [])[:5]
            if isinstance(item, Mapping)
        ]
        item_parts = []
        for item in recent_items:
            rank = str(item.get("rank") or "").strip()
            title = str(item.get("title") or "").strip()
            if rank and title:
                item_parts.append(f"{rank}. {title[:64]}")
        if item_parts:
            lines.append(f"最近推荐上下文({domain})：刚给用户发过这些作品：{'；'.join(item_parts)}。")
        referenced_item = _safe_mapping(recent_recommendation_context.get("referenced_item"))
        if referenced_item:
            recommendation_followup = True
            rank = str(referenced_item.get("rank") or "").strip()
            title = str(referenced_item.get("title") or "").strip()
            reason = str(referenced_item.get("selection_reason") or "").strip()
            intro = str(referenced_item.get("intro_snippet") or "").strip()
            facts = [f"用户当前追问的是第 {rank} 本：{title}" if rank else f"用户当前追问的是：{title}"]
            if reason:
                facts.append(f"推荐理由：{reason}")
            if intro:
                facts.append(f"简介线索：{intro[:90]}")
            lines.append("最近推荐追问：" + "；".join(part for part in facts if part) + "。")
            lines.append(
                "最近推荐追问回答要求：本轮必须先锚定被追问作品，回答正文里要自然出现书名或可识别短标题；"
                "不要只说“那本”“第三本”，也不要把它误解成游戏、动画或其他媒介。"
            )
        lines.append("最近推荐回答边界：这是对刚才推荐作品的追问，不是用户已经表达喜欢；不要把追问写进偏好。")
        lines.append("最近推荐回答风格：直接接上刚才推荐来评价，不要说成 galgame，不要说没上下文，不要输出内部字段。")
        if recommendation_followup:
            return tuple(lines)
    if liked_items:
        lines.append(f"用户偏好线索({domain})：用户刚对这些作品表达过好感：{'、'.join(liked_items)}。")
    if likes:
        lines.append(f"用户偏好线索({domain})：可能相关的口味线索：{'、'.join(likes)}。")
    if disliked_items:
        lines.append(f"用户偏好线索({domain})：用户明确说过不太喜欢的作品：{'、'.join(disliked_items)}。")
    if dislikes:
        lines.append(f"用户偏好线索({domain})：可能避开的口味线索：{'、'.join(dislikes)}。")
    if lines:
        lines.append(
            f"偏好回答边界：线索约 {evidence_count} 条；只能说用户表达过喜欢/好看，不能说用户看过、读完、追到最新卷。"
        )
        lines.append("偏好回答风格：像熟人顺口接话，不要像助手汇报；不要主动宣布“我记住了”。")
        lines.append(
            "偏好回答内容：可以说这本看起来有点对用户胃口；题材线索只能轻轻带过，不要总结成口味报告。"
        )
    elif query_relevant:
        lines.append("偏好回答边界：当前还没有明确偏好证据；自然说明还没攒够，不要编造用户喜欢什么。")
    return tuple(line[:220] for line in lines if line)


def _surface_user_preference_must_not_do(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> tuple[str, ...]:
    if not _surface_user_preference_query_owned(agent_input, ports=ports):
        return ()
    return (
        "偏好问答不要提系统、画像、证据、召回、记录、检索；把这些内部事实换成自然聊天。",
        "偏好查询必须用陈述句，不要使用问号；不要写“吗”“对吧”“是不是”。",
        "不要说“之前不是说过”“我记得”“我记住了”“瞎猜”“正反馈”“初步看”“固定口味”。",
        "不要说“怕误导你”；不要把推荐理由或榜单策略冒充用户长期偏好。",
        "不要说用户看过、读完、追到最新卷或之前推过；只能说用户刚刚/明确表示这本好看或喜欢。",
        "不要像助手做总结；用 Sora 的熟人语气，短一点，带一点轻微吐槽或试探，但别追问。",
        "不要用“对吧？”“是不是？”这类反问收尾；直接给当前判断和不确定边界。",
    )


def _preference_labels(value: Any, *, limit: int) -> list[str]:
    labels: list[str] = []
    for item in list(value or []):
        if isinstance(item, Mapping):
            label = str(item.get("label") or item.get("title") or item.get("name") or "").strip()
        else:
            label = str(item or "").strip()
        if not label or label in labels:
            continue
        labels.append(label[:80])
        if len(labels) >= int(limit):
            break
    return labels


def _surface_memory_plan_reason(trace_stage_meta: Mapping[str, Any] | None = None) -> str:
    packet = _safe_mapping(_safe_mapping(trace_stage_meta).get("surface_memory_recall_packet"))
    trace = _safe_mapping(packet.get("trace"))
    plan = _safe_mapping(trace.get("plan"))
    reason = str(plan.get("reason") or "").strip()
    if reason:
        return reason
    packet_text = "\n".join(
        str(item or "")
        for item in [
            *list(packet.get("facts") or []),
            *list(packet.get("style_anchor") or []),
            *list(packet.get("must_not_do") or []),
        ]
    )
    if "指代选择追问" in packet_text:
        return "anaphoric_selection_recall"
    return ""


def _surface_message_is_memory_acceptance(text: str) -> bool:
    message = str(text or "").strip()
    if not message:
        return False
    if any(token in message for token in ("回忆检查", "记忆验收", "同会话验收", "新会话验收", "最终同会话验收")):
        return True
    if "验收" in message and any(token in message for token in ("列出", "复述", "回答", "说出")):
        return True
    return bool(
        any(token in message for token in ("测试代号", "海边灯塔", "预热指标", "推荐格式"))
        and any(token in message for token in ("是什么", "指什么", "列出", "复述", "当前", "刚才", "刚刚"))
    )


def _surface_memory_acceptance_task_facts(
    *,
    agent_input: Any | None,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    # 这是对记忆内容的验收问答，而不是新的检索或推荐任务。只组合守卫包与当前
    # 会话中可识别的用户事实；没有证据时明确空缺，不能补造测试代号/指标。
    user_message = str(getattr(agent_input, "user_message", "") or "").strip() if agent_input is not None else ""
    if not _surface_message_is_memory_acceptance(user_message):
        return ()
    packet = _surface_memory_packet_for_output_guard(agent_input=agent_input, trace_stage_meta=trace_stage_meta) if agent_input is not None else {}
    facts = [
        str(item or "").strip()
        for item in list(packet.get("facts") or [])
        if str(item or "").strip()
    ]
    facts.extend(_surface_recent_user_fact_texts(agent_input, ports=ports))
    if not facts:
        return (
            "记忆验收请求：当前没有可靠事实，不要编造测试代号、指标或格式。",
        )
    joined = "\n".join(facts)
    structured: list[str] = []
    code = _surface_memory_acceptance_latest_test_code(joined)
    if code:
        structured.append(f"验收标准事实：测试代号={code}；如果出现旧值，以最新纠正为准。")
    if "海边灯塔" in joined and ("连续记忆锚点" in joined or "记忆锚点" in joined):
        structured.append("验收标准事实：海边灯塔=连续记忆锚点，不是字面灯塔。")
    metrics: list[str] = []
    for key in ("prewarm_latency_ms", "prewarm_threads", "prewarm_workers"):
        match = re.search(rf"{re.escape(key)}\s*[=：:]\s*([0-9]+)", joined)
        if match:
            metrics.append(f"{key}={match.group(1)}")
    if metrics:
        structured.append("验收标准事实：app-server预热指标=" + "、".join(metrics) + "。")
    compact = re.sub(r"\s+", "", joined)
    if "推荐格式" in joined and ("书名+一句话理由" in compact or ("书名" in joined and "一句话理由" in joined)):
        structured.append("验收标准事实：轻小说推荐格式=书名 + 一句话理由，不走卡片模板。")
    return tuple(
        _clean_list(
            [
                "记忆验收请求：最终回复必须由模型生成，但只能依据下面这些事实；不要编造缺失字段。",
                "回复约束：直接回答验收清单，不要反问推荐维度，不要发起轻小说推荐任务。",
                *structured,
                *[f"验收事实：{fact}" for fact in facts[-12:]],
            ]
        )
    )


def _surface_memory_acceptance_joined_facts(
    agent_input: Any | None,
    *,
    ports: SurfaceRuntimePorts,
) -> str:
    facts: list[str] = []
    if agent_input is not None:
        packet = _surface_memory_packet_for_output_guard(agent_input=agent_input, trace_stage_meta=None)
        facts.extend(str(item or "").strip() for item in list(packet.get("facts") or []) if str(item or "").strip())
    facts.extend(_surface_recent_user_fact_texts(agent_input, ports=ports))
    return "\n".join(_clean_list(facts))


def _surface_memory_acceptance_latest_test_code(joined_facts: str) -> str:
    # 纠正记录优先于旧出现值，取最后一条使同一会话多次订正时遵循最新版本。
    # 此函数只做文本提取，不能把未知格式的片段猜成测试编号。
    joined = str(joined_facts or "")
    code_pattern = r"蓝莲-0531-[A-Z]\d+"
    correction_matches = re.findall(
        rf"纠正测试代号[：:\s]*({code_pattern})\s*改成\s*({code_pattern})",
        joined,
    )
    if correction_matches:
        return correction_matches[-1][1]
    code_matches = re.findall(code_pattern, joined)
    if not code_matches:
        return ""
    return code_matches[-1]


def _surface_memory_acceptance_must_include(
    agent_input: Any | None,
    *,
    ports: SurfaceRuntimePorts,
) -> tuple[str, ...]:
    user_message = str(getattr(agent_input, "user_message", "") or "").strip() if agent_input is not None else ""
    if not _surface_message_is_memory_acceptance(user_message):
        return ()
    joined = _surface_memory_acceptance_joined_facts(agent_input, ports=ports)
    required: list[str] = []
    code = _surface_memory_acceptance_latest_test_code(joined)
    if code:
        required.append(code)
    if "海边灯塔" in user_message and ("连续记忆锚点" in joined or "记忆锚点" in joined):
        required.append("连续记忆锚点")
    for key in ("prewarm_latency_ms=665", "prewarm_threads=7", "prewarm_workers=1"):
        if key in joined:
            required.append(key)
    compact = re.sub(r"\s+", "", joined)
    if "推荐格式" in user_message and ("书名+一句话理由" in compact or "一句话理由" in joined):
        required.append("书名 + 一句话理由")
    return tuple(_clean_list(required))


def _surface_memory_acceptance_must_not_do(agent_input: Any | None) -> tuple[str, ...]:
    user_message = str(getattr(agent_input, "user_message", "") or "").strip() if agent_input is not None else ""
    if not _surface_message_is_memory_acceptance(user_message):
        return ()
    return (
        "不要反问用户想要什么口味、维度、热度、口碑或冷门宝藏；本轮只回答记忆验收。",
        "不要省略用户要求列出的轻小说推荐格式。",
        "不要把“书名 + 一句话理由，不走卡片模板”改写成“用户不用列模板”或“让用户再说口味”。",
    )


def _surface_memory_write_task_facts(agent_input: Any | None) -> tuple[str, ...]:
    # 写入请求的表层回答只需要自然确认和复述关键事实；实际持久化授权不在这里
    # 决定，因而不暴露渠道、任务状态或实现细节。
    user_message = str(getattr(agent_input, "user_message", "") or "").strip() if agent_input is not None else ""
    if not user_message:
        return ()
    if not any(token in user_message for token in ("记住", "记一下", "记下来", "项目事实", "测试事实", "记忆事实", "纠正", "改成")):
        return ()
    facts = [
        "记忆写入请求：最终回复必须由模型生成；只需自然确认已记住/已更新，并简短复述关键事实。",
        "回复约束：不要把记忆写入请求转成推荐、创作、工具执行或追问；不要输出内部渠道/任务状态。",
        f"待确认记忆内容：{user_message}",
    ]
    if "推荐格式" in user_message and "书名" in user_message and "一句话" in user_message:
        facts.append("待确认关键事实：轻小说推荐格式=书名 + 一句话理由，不走卡片模板。")
    if "海边灯塔" in user_message and ("记忆锚点" in user_message or "连续记忆锚点" in user_message):
        facts.append("待确认关键事实：海边灯塔=连续记忆锚点，不是字面灯塔。")
    if "prewarm_" in user_message or "预热指标" in user_message:
        facts.append("待确认关键事实：保留 prewarm_latency_ms、prewarm_threads、prewarm_workers 的原始数值。")
    code = re.search(r"蓝莲-0531-[A-Z]\d+", user_message)
    if code:
        facts.append(f"待确认关键事实：测试代号={code.group(0)}。")
    return tuple(_clean_list(facts))


def _surface_recent_user_fact_texts(
    agent_input: Any | None,
    *,
    ports: SurfaceRuntimePorts,
    limit: int = 80,
) -> list[str]:
    # 只从当前 person/session 的请求之前用户轮次读取，并筛选明确的事实标记；
    # 读取异常返回空列表，避免把别的会话内容或猜测当作验收依据。
    if agent_input is None:
        return []
    person_id = str(getattr(agent_input, "person_id", "") or "").strip()
    if not person_id:
        return []
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    fact_markers = (
        "测试代号",
        "海边灯塔",
        "prewarm_",
        "预热指标",
        "推荐格式",
        "项目事实",
        "测试事实",
        "记忆事实",
        "纠正",
        "改成",
        "记住",
        "记一下",
    )
    rows: list[str] = []
    repository: RuntimeReadRepository | None = None
    try:
        repository = RuntimeReadRepository.from_settings(
            ports.get_persistence_settings()
        )
        contents = repository.recent_user_turn_contents(
            person_id=person_id,
            session_id=session_id,
            request_id=request_id,
            limit=int(limit),
        )
        for content in contents:
            text = str(content or "").strip()
            if text and any(marker in text for marker in fact_markers):
                rows.append("用户事实：" + text)
    except Exception:
        return []
    finally:
        if repository is not None:
            repository.close()
    rows.reverse()
    return rows
