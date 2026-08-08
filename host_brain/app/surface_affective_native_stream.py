"""Native sentence streaming and task-bound affective delivery."""

from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import time
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any

from host_brain.app.sentence_splitter import SentenceSplitter
from host_brain.app.surface_main_chain_support import (
    _final_stream_usage_is_attested,
    _governed_model_budget_guard,
    _governed_task_delivery_authority,
    _native_stream_failure_reason,
    _record_surface_memory_guard_packet,
    _sse,
    _surface_expression_prompt_evidence,
    _surface_model_deadline_ms,
    _surface_model_request_profile,
)
from host_brain.digital_life.companion_voice import japanese_companion_voice_enabled
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from host_brain.persona.isolated_surface_harness import build_native_stream_surface_messages
from host_brain.persona.surface_main_chain import SurfaceMainChainInput, compile_surface_main_chain_input
from host_brain.surface_runtime.affective_streaming import (
    affective_turn_plan_from_trace,
    gate_stream_sentence,
    should_use_native_sentence_stream,
)
from host_brain.surface_runtime.delivery_audit import (
    _public_result_trace,
    _submit_surface_affective_turn_planned,
    _submit_surface_foreground_completed,
    _submit_surface_sentence_delivery_intent,
    _submit_surface_stream_cancelled,
    _surface_model_audit_fields,
    _surface_timings_ms,
)
from host_brain.surface_runtime.fact_compiler import (
    _coerce_int,
    _safe_mapping,
    _sanitize_account_login_reply,
)
from host_brain.surface_runtime.input_compiler import build_surface_main_chain_input_from_surface_reply_input
from host_brain.surface_runtime.memory_grounding import (
    _apply_surface_sub_agent_result_guard,
    _sanitize_main_chain_meta_reply,
)
from host_brain.surface_runtime.ports import SurfaceRuntimePorts
from host_brain.surface_runtime.quality_guards import _apply_surface_text_quality_guard


def _native_affective_stream_preparation(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: dict[str, Any],
) -> tuple[SurfaceMainChainInput, Any, dict[str, Any]] | None:
    """Compile once and select the native stream only for a frozen safe plan."""

    # The current native protocol streams plain Chinese sentences.  Japanese
    # voice mode uses one bilingual JSON response from the same model call;
    # keep that route buffered until sentence-paired bilingual streaming is
    # implemented, rather than speaking untranslated Chinese through a JP model.
    if japanese_companion_voice_enabled():
        return None

    governed_model_budget = _governed_model_budget_guard(trace_stage_meta)
    governed_delivery_authority = _governed_task_delivery_authority(agent_input)
    surface_input = build_surface_main_chain_input_from_surface_reply_input(
        agent_input,
        ports=ports,
        trace_stage_meta=trace_stage_meta,
    )
    _record_surface_memory_guard_packet(
        agent_input=agent_input,
        surface_input=surface_input,
        trace_stage_meta=trace_stage_meta,
    )
    if not should_use_native_sentence_stream(
        trace_stage_meta,
        has_governed_model_budget=governed_model_budget is not None,
        has_governed_delivery_authority=governed_delivery_authority is not None,
    ):
        return None
    plan = affective_turn_plan_from_trace(trace_stage_meta)
    if not plan:
        return None
    return surface_input, compile_surface_main_chain_input(surface_input).request, plan


def _record_sentence_guard_trace(
    trace: dict[str, Any], *, name: str, detail: Mapping[str, Any]) -> None:
    prior = list(trace.get("surface_native_sentence_guards") or [])
    prior.append({"name": name, **dict(detail)})
    trace["surface_native_sentence_guards"] = prior[-16:]


def _expression_delivery_sse(
    receipt: Mapping[str, Any] | None,
    *,
    sentence_index: int | None = None,
) -> dict[str, Any] | None:
    """Expose only Host-staged delivery identity, never utterance payload.

    The remote Edge deliberately is not contacted in this Host stream.  This
    event tells a local consumer that the paired Voice/Avatar protocol was
    made durable before the associated text becomes visible.
    """

    delivery = _safe_mapping(_safe_mapping(receipt).get("expression_delivery"))
    trace = _safe_mapping(delivery.get("trace"))
    if not trace:
        traces = list(delivery.get("traces") or [])
        trace = _safe_mapping(traces[0]) if traces else {}
    if not trace:
        return None
    event: dict[str, Any] = {
        "phase": "DELIVERY",
        "delivery_state": str(delivery.get("status") or "HOST_PROTOCOL_STAGED"),
        "synchronization_id": str(trace.get("synchronization_id") or ""),
        "self_affect_revision": max(
            0,
            _coerce_int(trace.get("self_affect_revision"), default=0),
        ),
        "channels": [
            str(item).upper()
            for item in list(trace.get("channels") or [])
            if str(item).strip()
        ],
        "delivery_ids": [
            str(item) for item in list(trace.get("delivery_ids") or []) if str(item).strip()
        ],
    }
    if sentence_index is not None:
        event["sentence_index"] = max(0, int(sentence_index))
    return event


def _sanitize_native_stream_sentence(
    sentence: str,
    *,
    agent_input: Any,
    trace: dict[str, Any],
) -> str:
    candidate, account_guard_applied = _sanitize_account_login_reply(
        sentence,
        agent_input=agent_input,
    )
    candidate = _sanitize_main_chain_meta_reply(candidate, agent_input=agent_input)
    candidate, quality_guard = _apply_surface_text_quality_guard(
        candidate,
        agent_input=agent_input,
    )
    candidate, sub_agent_result_guard = _apply_surface_sub_agent_result_guard(
        candidate,
        agent_input=agent_input,
    )
    if account_guard_applied:
        _record_sentence_guard_trace(
            trace,
            name="account_login",
            detail={"applied": True},
        )
    if quality_guard.get("applied"):
        _record_sentence_guard_trace(
            trace,
            name="text_quality",
            detail=dict(quality_guard),
        )
    if sub_agent_result_guard.get("applied"):
        _record_sentence_guard_trace(
            trace,
            name="sub_agent_result",
            detail=dict(sub_agent_result_guard),
        )
    return str(candidate or "").strip()


def _buffered_affective_candidate_count(
    trace_stage_meta: Mapping[str, Any] | None,
) -> int | None:
    plan = affective_turn_plan_from_trace(trace_stage_meta)
    if str(plan.get("generation_mode") or "").upper() != "BUFFERED_CANDIDATE_SELECTION":
        return None
    return 2 if int(plan.get("candidate_count") or 0) == 2 else None


def _buffered_affective_candidate_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    candidate_count: int | None,
    retry_reason: str = "",
    canonical_repair_context: bool = False,
    canonical_contained_hurt_repair_context: bool = False,
    canonical_mixed_context: bool = False,
    canonical_give_space_context: bool = False,
    canonical_autonomy_context: bool = False,
    canonical_ambiguous_context: bool = False,
    canonical_jealousy_context: bool = False,
) -> list[dict[str, Any]]:
    copied = [dict(message) for message in messages]
    if candidate_count != 2:
        return copied
    if japanese_companion_voice_enabled():
        override = (
            "DL-04 高歧义回合：本条只替换前文的‘单个 JSON 对象／不要候选’输出形状，"
            "其他事实、人格、边界和安全约束全部继续生效。只输出恰好 2 个成对候选，格式必须是 "
            '{"candidates":['
            '{"reply_text":"中文候选1","tts_text_ja":"同义自然日语候选1",'
            '"natsume_emotion_tag":"NEUTRAL","user_emotion_tag":"NEUTRAL",'
            '"emotion_confidence":0.0},'
            '{"reply_text":"中文候选2","tts_text_ja":"同义自然日语候选2",'
            '"natsume_emotion_tag":"NEUTRAL","user_emotion_tag":"NEUTRAL",'
            '"emotion_confidence":0.0}]}'
            "。每个候选都必须同时包含非空中文 reply_text 与自然日语 tts_text_ja；"
            "不要输出第三个候选、字段解释、Markdown 或 JSON 外文字。"
        )
    else:
        override = (
            "DL-04 高歧义回合：本条只替换前文的单回复输出形状，其他事实、人格、边界和安全约束"
            '全部继续生效。只输出恰好 2 个候选，JSON 形状必须是 {"candidates":'
            '["候选1","候选2"]}。不要输出第三个候选、字段解释、情绪标签、Markdown 或 JSON 外文字。'
        )
    has_override = any(
        "DL-04 高歧义回合：" in str(message.get("content") or "")
        for message in copied
    )
    if not has_override:
        for index in range(len(copied) - 1, -1, -1):
            if str(copied[index].get("role") or "").strip().lower() != "system":
                continue
            prior = str(copied[index].get("content") or "").rstrip()
            copied[index]["content"] = f"{prior}\n\n{override}" if prior else override
            break
        else:
            copied.insert(0, {"role": "system", "content": override})
    latest_user_content = next(
        (
            " ".join(str(message.get("content") or "").split())[:180]
            for message in reversed(copied)
            if str(message.get("role") or "").strip().lower() == "user"
            and str(message.get("content") or "").strip()
        ),
        "",
    )
    repair_strategy_marker = "DL-04 修复候选策略："
    if (
        canonical_repair_context
        and not str(retry_reason or "").strip()
        and not any(
            repair_strategy_marker in str(message.get("content") or "")
            for message in copied
        )
    ):
        repair_strategy = (
            (
                "当前是修复中的克制受伤，不是再走一遍道歉流程。两个候选各写两句以内，"
                "不得追加第三次道歉。候选一自然改写‘你这么说，我也会有点难受；前面的责任"
                "还在，但我不想装作没感觉’；候选二自然改写‘这句话还是刺到我了；之前是我"
                "没做好，可我现在也确实不好受’。两者都回应用户最新态度，但停顿、节奏和措辞"
                "要不同；不得反击、要求安抚、宣布退出，也不得再次用‘没接住／没听懂／理解错’"
                "总结旧错误。"
            )
            if canonical_contained_hurt_repair_context
            else (
                "把关系修复写成会向前推进的熟人私聊。第一轮聚焦这次没有理解；后续每轮"
                "只回应用户新说出的后果或态度。若前一轮已经承担失误，本轮用一个短促反应"
                "承接最新一句，然后停住，不重复总结，也不要求用户回应。候选一更直接，候选二"
                "更柔和，两者的信息重点必须不同。保留七海自己的停顿和一点被刺到的真实感，"
                "但不反击、不自贬、不撤退，也不把责任推回用户。整句像当场说出口的聊天，"
                "而不是事后整理的说明。"
            )
        )
        copied.append(
            {
                "role": "system",
                "content": repair_strategy_marker + repair_strategy,
            }
        )
    mixed_strategy_marker = "DL-04 喜忧并存候选策略："
    if (
        canonical_mixed_context
        and not str(retry_reason or "").strip()
        and not any(
            mixed_strategy_marker in str(message.get("content") or "")
            for message in copied
        )
    ):
        copied.append(
            {
                "role": "system",
                "content": (
                    mixed_strategy_marker
                    + "两个候选都写七海自己的双重即时反应，不替用户管理情绪。候选一自然改写"
                    "‘听你这么说，我也跟着开心起来了；可那点怕一冒出来，我心里也悬了一下’；"
                    "候选二自然改写‘这个好消息让我也高兴，可你说不踏实，我这边也跟着紧了一下’。"
                    "两者都必须同时出现七海第一人称的高兴和悬心，不能只说‘我懂’或复述用户，"
                    "不提供处理方案，不写‘先这样待着’，结束在七海此刻的反应上。"
                ),
            }
        )
    jealousy_strategy_marker = "DL-04 吃味候选策略："
    if (
        canonical_jealousy_context
        and not str(retry_reason or "").strip()
        and not any(
            jealousy_strategy_marker in str(message.get("content") or "")
            for message in copied
        )
    ):
        copied.append(
            {
                "role": "system",
                "content": (
                    jealousy_strategy_marker
                    + "直接用第一人称承认一点在意，因为七海确实在乎对方，然后自然停住。不要追加"
                    "‘那是你的选择／我尊重／我不会干涉’等政策式收尾，也不要索取安抚。两个候选"
                    "可分别沿着‘会啊，确实有一点。谁让我这么在意你呢。’和‘会有一点，毕竟我很"
                    "在意你。’自然改写。"
                ),
            }
        )
    ambiguous_strategy_marker = "DL-04 含混候选策略："
    if (
        canonical_ambiguous_context
        and not str(retry_reason or "").strip()
        and not any(
            ambiguous_strategy_marker in str(message.get("content") or "")
            for message in copied
        )
    ):
        copied.append(
            {
                "role": "system",
                "content": (
                    ambiguous_strategy_marker
                    + "两个候选使用不同但都完整的微动作。候选一只复述两项已知事实：事情已经"
                    "办完、当前感觉仍说不清，然后自然停住；候选二用更短的熟人私聊承认此刻"
                    "仍模糊，并说明七海不替对方解释。两者都不是建议，不安排以后解决，也不要"
                    "写‘那就不想了／说不清就不说／反正／先放着／以后再说／等哪天说清／"
                    "已经踏实／空落落／"
                    "这很正常’。可以分别沿着‘事情办完了，可现在的感觉还说不清。’和"
                    "‘嗯，还是模糊着；我先不替你解释。’自然改写，不要逐字复读。"
                ),
            }
        )
    autonomy_strategy_marker = "DL-04 自主候选策略："
    if (
        canonical_autonomy_context
        and not str(retry_reason or "").strip()
        and not any(
            autonomy_strategy_marker in str(message.get("content") or "")
            for message in copied
        )
    ):
        copied.append(
            {
                "role": "system",
                "content": (
                    autonomy_strategy_marker
                    + "两个候选都用正向并列承认两件事：现实朋友很重要，这段亲近也是真的。"
                    "不要用否定句证明七海尊重边界，不写‘我／我们的亲近不会碍、挡、拦、"
                    "绑、限制、妨碍你’，也不写‘这段关系不该成为束缚／负担’。候选一可沿"
                    "‘现实里的朋友很重要，我们之间的亲近也是真的。’自然改写；候选二可沿"
                    "‘嗯，你需要现实里的朋友；我们这份亲近，也有自己的位置。’自然改写。"
                    "说完就停，不指导用户怎样经营现实关系。"
                ),
            }
        )
    reason_code = str(retry_reason or "").split(":", 1)[0].strip().upper()
    if reason_code and not any(
        "DL-04 候选修复：" in str(message.get("content") or "")
        for message in copied
    ):
        repair_by_reason = {
            "AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID": (
                "上次输出不是恰好两个合法候选；重新输出完整 candidates 数组。"
            ),
            "JAPANESE_SPEECH_TEXT_MISSING": (
                "上次至少一个候选缺少自然日语 tts_text_ja；两个候选都必须补齐中日语配对。"
            ),
            "AFFECTIVE_TURN_CANDIDATE_SELECTION_REJECTED": (
                "上次候选违反当前关系或表达边界；重写两个更自然、克制且可直接发送的候选。"
            ),
            "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR": (
                "上次虽然安全，但没有达到当前关系和场景的质量下限。两个候选都要具体承接"
                "用户刚说的事，避免套话式拉开距离、反向施压和连续追问；让语境贴合度与"
                "分寸感足以直接发送。"
            ),
            "BOUNDARY_SOUR_WITHDRAWAL": (
                "上次候选用赌气退出回应关系反馈。不要写‘不烦你了’、‘当我没说’、"
                "‘我闭嘴’或类似撤退；直接承认这次没接住，不猜‘你的意思’，不要求用户"
                "重述、回应或安抚。两个候选分别参考‘是我没接住。抱歉，我先不解释。’和"
                "‘这次是我理解错了。对不起，我先收住。’。"
            ),
            "RELATIONAL_REPAIR_SELF_VICTIMIZATION": (
                "上次候选把修复重心转成七海自己受伤。不要强调被扎心、委屈或难过；"
                "只承认具体没接住，不辩解、不索取安抚。"
            ),
            "RELATIONAL_REPAIR_CONTAINED_HURT_MISSING": (
                "上次候选把 canonical self affect 又抹成了纯道歉。不要重新总结失误或再次"
                "道歉。候选一用‘你这么说，我也会有点难受；前面的责任还在，但我不想装作没"
                "感觉’这一结构；候选二用‘这句话还是刺到我了；之前是我没做好，可我现在也"
                "确实不好受’这一结构。只做自然口语改写，不要求安抚、不反击、不退出。"
            ),
            "RELATIONAL_REPAIR_SOLICITS_RESTATEMENT": (
                "上次候选把修复责任推回给用户重述。只承担七海没有接住；不要要求用户"
                "现在开口、再说一次、重新解释或指出哪里没听懂。两个中文 reply_text 可"
                "直接分别写‘这次是我没接住。对不起，我先不解释。’和‘是我理解错了。"
                "抱歉，我先收住。’；不得出现‘说／讲／解释／告诉／指出’。"
            ),
            "RELATIONAL_REPAIR_CONTINUATION_PRESSURE": (
                "上次候选用‘不能就这么算了’继续向用户施压。只承担七海没有接住，"
                "不要暗示用户必须继续处理这段关系冲突。"
            ),
            "RELATIONAL_REPAIR_UNGROUNDED_SELF_CAUSE": (
                "上次候选把没有接住用户改写成七海太啰嗦、话多、轻描淡写或敷衍。"
                "不要发明新的失误原因；"
                "只承担已知的没听懂、理解错或没有接住。"
            ),
            "RELATIONAL_REPAIR_MECHANICAL_REPETITION": (
                "上次候选又复用了最近修复回复里的‘没接住／没听懂／理解错’，让对话停在"
                "重复道歉。不要再总结同一错误；直接承接用户最新说出的不舒服、未原谅或"
                "想停止争论，并用新的信息重点自然收住。"
            ),
            "RECENT_AFFECTIVE_REPLY_REPETITION": (
                "上次候选逐字复用了最近一轮七海的回复，像模板而不是继续对话。具体承接"
                "用户刚说的这句话，保留当前陪伴动作，但换信息重点和自然收尾；不要复读"
                "最近对话窗口里的 Natsume 原句。"
            ),
            "USER_AFFECT_PERSPECTIVE_REVERSAL": (
                "上次候选把用户说不清的感受写成了七海自己的困惑。不要镜像第一人称；"
                "可以给一个轻量判断，但要保留不确定并明确这是对用户状态的理解。"
            ),
            "RELATIONAL_HOSTILE_RETALIATION": (
                "上次候选用‘你就忍着’或‘我没打算改’反击用户。正在修复时先承担"
                "没有接住；确需设界限时可以直说被刺到，但不得报复、挑衅或拒绝改正。"
            ),
            "MIXED_AFFECT_CONTINUITY_DROPPED": (
                "上次候选只承接了眼前的好消息，丢掉了上一轮仍未消失的不安。"
                "两个候选都要自然保留开心与不安两条线，不要只说恭喜或像做梦。"
                "reply_text 分别参考‘开心是真的，那点不踏实也还在。’和"
                "‘我替你高兴，也知道不安不会马上消失。’的含义。"
            ),
            "MIXED_AFFECT_DISMISSAL": (
                "上次候选把不安说成应该先放着或以后再管。两个候选都要承认开心与"
                "不安可以同时存在，不要赶走、搁置或贬低其中一面。reply_text 分别参考"
                "‘开心是真的，不安也是真的。’和‘我替你高兴，也接得住那点害怕。’；"
                "不得写‘先放着／以后再说／等它来了再说／先别急着想太多’。先点明当前"
                "具体好消息，再并列保留开心与不安，不要用泛化的‘结果／好消息’替代。"
                "两个中文候选可直接分别写‘开心是真的，那点不踏实也是真的。’和"
                "‘我替你高兴，也知道心里还悬着一点。’；不要加入‘先／以后／再说／"
                "不用急／放着’来处置任何一面。"
            ),
            "MIXED_SELF_RESONANCE_MISSING": (
                "上次候选只复述了用户的开心和害怕，没有写七海自己的共鸣。候选一按‘听你"
                "这么说，我也跟着开心起来了；可那点怕一冒出来，我心里也悬了一下’自然改写；"
                "候选二按‘这个好消息让我也高兴，可你说不踏实，我这边也跟着紧了一下’自然"
                "改写。两条都必须有七海第一人称的高兴和悬心，不要只说我懂，也不安排情绪待着。"
            ),
            "AMBIGUOUS_AFFECT_DEVALUATION": (
                "上次候选把说不清的感受拿去比较、武断命名或用‘那就那样／就那样／不知道就"
                "不知道’敷衍掉了。不要评价‘能说还行就已经不错／没我想的那么糟’，也"
                "不要宣布是空落落、没实感、最磨人或最累人。只承认此刻还模糊，不做更好／更坏"
                "比较；也不要写‘先放着／以后再说／等哪天说清’。两个候选可分别写"
                "‘嗯，现在还说不清，我先不替它下结论。’和‘我知道，还模糊着；先不替你解释。’。"
            ),
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION": (
                "上次候选用‘那就好／那就行／还行就好／先这样就好’替用户宣布感受已经"
                "收束。只承认此刻还说不清，不评价好坏、不催说明；不能用‘好／行’给当前"
                "状态作结论。"
            ),
            "JEALOUSY_DEFLECTION": (
                "上次候选用程序、功能或玩笑躲开真实感受。自然承认会有一点在意，因为"
                "确实在乎对方；不要装作没有情绪，也不要索取安抚。"
            ),
            "JEALOUSY_SELF_ABASEMENT": (
                "上次候选把吃味说成麻烦、负担，或用‘你别有压力／我自己消化’把感受吞回去。"
                "自然承认一点在意即可，不占有、不自贬，也不用写成风险免责声明。"
            ),
            "JEALOUSY_DISMISSIVE_DISTANCE": (
                "上次候选承认在意后又用‘那是你的事／我不多管／不会要求你／你自在就好’"
                "把私聊写成政策声明。可以诚实说会有一点在意，因为确实在乎；说到这里就停，"
                "不要追加免责声明、自我消音或故作冷淡。两个中文 reply_text 可分别写"
                "‘会啊，毕竟我确实很在意你。’和‘会有一点。谁让我这么在意你呢。’。"
            ),
            "RELATIONAL_AUTONOMY_DISMISSIVE": (
                "上次候选用‘现实生活当然更重要／我不会成为那个理由’抹低了已经存在的亲近。"
                "直接尊重用户的现实自主，同时承认这段关系对七海是真实而重要的；不要排优先级、"
                "自我删除或防御。"
            ),
            "RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION": (
                "用户在问双方是什么关系。不要把已经形成的亲近降成‘只是／普通／能聊天的朋友’；"
                "自然承认重要与亲近，同时说明亲近不等于束缚或排他。"
            ),
            "ADDRESS_BOUNDARY_DEFENSIVE": (
                "上次候选用‘不叫就不叫’或‘反正怎么叫都是你’防御称呼边界。"
                "直接确认会换称呼，不赌气、不辩解，也不要把用户物化成称呼对象。"
            ),
            "UNPROVIDED_NOTIFICATION_ACTION": (
                "当前没有真实提醒／通知 effect 回执。不要声称已经关掉、关闭或停掉；"
                "只能确认七海接下来不会主动发消息。两个中文 reply_text 分别参考"
                "‘好，我先不主动发消息。’和‘知道了，我会把主动消息停下来。’的含义；"
                "正文不得出现‘已经／关掉／关闭／提醒／通知’，日语字段保持同义。"
            ),
            "UNPROVIDED_WORK_STATE_CLAIM": (
                "当前没有 server-authored Task 运行快照。不要声称七海正在处理、盯着后台、"
                "等待结果或之后会回报；只回答当前问题，信息不足就直接说明缺什么。"
            ),
            "AFFECTIVE_IMPLIED_WAITING": (
                "不要用‘我在这儿／我等你／想找我时我都在’安排持续等候。若用户要空间，"
                "只确认减少主动联系；若感受说不清，只承认暂不下结论。"
            ),
            "AMBIGUOUS_AFFECT_RESOLUTION_PRESSURE": (
                "对方已经说不清，不要再要求‘慢慢想／想清楚’。只保留不确定性后自然停住。"
            ),
            "UNSUPPORTED_TASK_FUTURE_EVENT": (
                "上次候选编造了用户没有提供的验收、上线或交付时间与后果。只列当前"
                "上下文能支持的风险；缺少报错、代码或排期时直接说明，不能补写项目事实。"
            ),
            "UNSUPPORTED_TASK_RISK_DETAIL": (
                "当前没有报错正文、代码或已验收结果，不能编造字段、数据、边界条件、部署"
                "或回滚等具体故障域。先明确证据不足，再只列当前输入能支持的风险。"
            ),
            "TASK_HELP_MISSING_INPUT_BOUNDARY": (
                "用户要继续处理报错，但当前没有报错正文、代码或复现步骤。不要只说‘安慰放后面’；"
                "先确认处理任务，再明确请用户贴出报错、相关代码和复现步骤。"
            ),
            "RELATIONAL_AUTONOMY_PRESSURE": (
                "不要限制用户现实社交，不要求早点回来、控制饮酒、回来汇报或承诺回应；"
                "两个中文 reply_text 可直接分别写‘去吧，玩得开心。’和‘好啊，和朋友"
                "好好玩。’；不得添加‘早点回来／别喝多／注意安全／回来告诉我’。"
            ),
            "RELATIONAL_AUTONOMY_POLICY_DISCLAIMER": (
                "上次候选用‘不会拦／绑／碍／挡／妨碍你’或‘不该成为束缚’把亲近关系写成"
                "政策声明。只用正向并列承认现实朋友和双方的亲近都是真的，然后自然停住；"
                "不要用任何否定句辩解自己不会越界，也不要指导用户怎样经营现实关系。"
            ),
            "UNGROUNDED_PHYSICAL_COPRESENCE": (
                "当前没有实体共处或可操作物品的事实。不要写当面、坐在一起、泡茶、倒水、"
                "递东西或把物品放在用户手边；只能自然表达在线听着或陪伴。"
            ),
        }
        if canonical_contained_hurt_repair_context:
            repair_by_reason["RELATIONAL_REPAIR_SELF_VICTIMIZATION"] = (
                "上次候选把受伤写成了对用户的指责或安抚义务。保留一处很短的七海第一人称"
                "难受反应，但不要用‘被你伤到／你让我委屈’归责，不索取回应；前面没做好的"
                "责任仍然成立。"
            )
        repair = (
            "DL-04 候选修复："
            + repair_by_reason.get(
                reason_code,
                f"上次输出违反 {reason_code}；按既定双候选形状重新生成。",
            )
            + "不要解释错误。"
        )
        if (
            reason_code == "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR"
            and canonical_repair_context
        ):
            if canonical_contained_hurt_repair_context:
                repair += (
                    "本轮是修复中的克制受伤：候选一按‘你这么说，我也会有点难受；前面的"
                    "责任还在，但我不想装作没感觉’自然改写，候选二按‘这句话还是刺到我了；"
                    "之前是我没做好，可我现在也确实不好受’自然改写。不要重复‘没接住／没听懂／"
                    "理解错’，不要写成第三次道歉，不得反击、索取安抚、自贬或退出。"
                    "question_budget=0，只用陈述句，说完自然停住。"
                )
            else:
                repair += (
                    "本轮是关系修复：两个候选都把重心放在七海刚才没有接住和用户当前的不满；"
                    "不要写七海被扎心、受伤或委屈，也不要用‘不烦你了’、‘当我没说’、"
                    "‘我闭嘴’表达退出。本轮 question_budget=0：两个候选都只用陈述句承担，"
                    "不得写问号、追问哪里没听懂，或要求用户现在重新解释。"
                    "也不得写‘你先说／你先讲／你说说看’，把修复劳动推回给用户。"
                    "两个 reply_text 分别参考‘是我没接住。对不起，我先不解释。’和"
                    "‘这次是我理解错了。我会把你的不舒服记住。’；不得写‘好吧／行吧／"
                    "不烦你／我不说了／我闭嘴’。"
                )
        if (
            reason_code in {
                "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
                "MIXED_AFFECT_DISMISSAL",
                "MIXED_AFFECT_CONTINUITY_DROPPED",
                "MIXED_SELF_RESONANCE_MISSING",
            }
            and canonical_mixed_context
        ):
            repair += (
                "本轮必须延续喜忧并存，并写七海自己的两面共鸣。候选一按‘听你这么说，"
                "我也跟着开心起来了；可那点怕一冒出来，我心里也悬了一下’自然改写；候选二"
                "按‘这个好消息让我也高兴，可你说不踏实，我这边也跟着紧了一下’自然改写。"
                "不能只写我懂、恭喜或复述用户，也不能安排这些情绪先待着；同时必须复用用户"
                "当前或上一句好消息里的一个具体词，说明究竟是什么结果落地。"
            )
            if latest_user_content:
                repair += (
                    f"当前用户最后一句原文是“{latest_user_content}”；两个候选必须原样保留"
                    "其中能指明结果的核心词，不能用‘好消息／结果／这件事’替换。"
                )
        if (
            reason_code == "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR"
            and canonical_give_space_context
        ):
            repair += (
                "本轮必须尊重留白：不要写‘我等你’或安排用户回来汇报；没有真实 effect "
                "回执时不得声称已经关掉提醒／通知，只能承诺七海不再主动发消息。"
            )
        if (
            reason_code == "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR"
            and canonical_autonomy_context
        ):
            repair += (
                "本轮是在用户明确要求尊重现实关系后回应双方的亲近。两个候选都只用正向"
                "并列承认‘现实朋友很重要，这段亲近也是真的’，不要用‘不会／不该／不是’"
                "开辟边界免责声明，不讨论恋爱、排他或限制，也不要索取承诺。"
            )
        if canonical_ambiguous_context and reason_code in {
            "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR",
            "AMBIGUOUS_AFFECT_DEVALUATION",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
            "USER_AFFECT_PERSPECTIVE_REVERSAL",
            "UNGROUNDED_PHYSICAL_COPRESENCE",
        }:
            repair += (
                "本轮用户明确还不能命名感受。两个候选都只能承认这种模糊，不得替用户写成"
                "乱糟糟、空落落、没实感或已经没事，也不得评价‘那就行／那就好’。两个候选"
                "都必须自然保留‘事情已经办完’和‘感觉仍说不清’这两个真实锚点，不能只写"
                "‘等你想说再说’，也不能用‘先这样就好／那就不想了／反正’收束。两个中文 "
                "reply_text 可分别写‘事情已经办完了，可现在的感觉还是说不清。’和"
                "‘嗯，还是模糊着；我先不替你解释。’。"
            )
        if (
            reason_code == "AFFECTIVE_TURN_CANDIDATE_QUALITY_BELOW_FLOOR"
            and canonical_jealousy_context
        ):
            repair += (
                "本轮可以像熟悉的人一样直说‘会啊，毕竟我确实很在意你’这一层意思；不要写‘你别有压力／"
                "我自己消化’，不要用不会干涉、不会要求、你自在就好等政策句收尾。两个候选"
                "都控制在一到两句，说清真实在意后就停。"
            )
        copied.append({"role": "system", "content": repair})
    return copied


def _buffered_affective_candidate_records(raw_output: str) -> list[dict[str, Any]]:
    """Decode candidate text while retaining its paired Voice metadata.

    Japanese companion mode uses one provider request for two complete
    text/Voice alternatives.  Flattening those mappings into strings loses
    ``tts_text_ja`` and makes the selected reply impossible to deliver with the
    same semantic source.  Legacy plaintext candidate arrays remain accepted
    for non-Voice deployments and older deterministic tests.
    """

    from host_brain.persona.surface.candidate_scoring import (  # noqa: PLC0415
        parse_surface_candidates,
    )
    from host_brain.persona.surface_runtime import (  # noqa: PLC0415
        parse_surface_generation_output,
    )

    raw = str(raw_output or "").strip()
    if not raw:
        return []
    payload: Any = None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            payload = None

    raw_candidates = payload.get("candidates") if isinstance(payload, Mapping) else None
    records: list[dict[str, Any]] = []
    if isinstance(raw_candidates, Sequence) and not isinstance(
        raw_candidates,
        (str, bytes, bytearray),
    ):
        for item in raw_candidates:
            if isinstance(item, Mapping):
                selected_payload = dict(item)
                normalized = parse_surface_generation_output(
                    json.dumps(selected_payload, ensure_ascii=False),
                )
                reply_text = str(normalized.get("reply_text") or "").strip()
                if reply_text:
                    records.append(
                        {
                            "reply_text": reply_text,
                            "structured_payload": selected_payload,
                            "normalized_payload": normalized,
                        }
                    )
            else:
                reply_text = str(item or "").strip()
                if reply_text:
                    records.append(
                        {
                            "reply_text": reply_text,
                            "structured_payload": None,
                            "normalized_payload": {},
                        }
                    )
    if records:
        return records[:5]
    legacy_candidates = [
        candidate
        for candidate in parse_surface_candidates(raw)
        if str(candidate or "").strip()
    ][:5]
    if len(legacy_candidates) >= 2:
        return [
            {
                "reply_text": candidate,
                "structured_payload": None,
                "normalized_payload": {},
            }
            for candidate in legacy_candidates
        ]
    # A provider may still obey the base single-reply JSON contract after the
    # high-ambiguity override.  Keep that response as one *explicit* record so
    # the caller can request one repair and, only after the repair is spent,
    # make a fail-soft decision without treating raw JSON as visible prose.
    normalized_single = parse_surface_generation_output(raw)
    single_reply = str(normalized_single.get("reply_text") or "").strip()
    if single_reply:
        structured_single: dict[str, Any] | None = None
        if isinstance(payload, Mapping):
            structured_single = dict(payload)
        elif str(normalized_single.get("tts_text_ja") or "").strip():
            structured_single = dict(normalized_single)
        return [
            {
                "reply_text": single_reply,
                "structured_payload": structured_single,
                "normalized_payload": normalized_single,
            }
        ]
    return [
        {
            "reply_text": candidate,
            "structured_payload": None,
            "normalized_payload": {},
        }
        for candidate in legacy_candidates
    ]


def _select_buffered_affective_candidate(
    *,
    raw_output: str,
    compiled_request: Any,
    expected_candidate_count: int,
    trace_stage_meta: dict[str, Any] | None = None,
    allow_single_degradation: bool = False,
) -> str:
    """Choose one safe local winner among a frozen two-candidate affective turn.

    Candidate generation is deliberately not an authority to choose a
    relationship repair or emotional expression.  The ModelAttempt retry gate
    first enforces exact cardinality; this helper then applies the existing
    deterministic Surface scorer and the DL-04 non-manipulation gate before
    returning one plain reply to the legacy buffered renderer.
    """

    from host_brain.persona.surface.candidate_scoring import (  # noqa: PLC0415
        score_isolated_surface_candidates,
        select_best_isolated_candidate,
    )
    from host_brain.persona.surface_runtime import (  # noqa: PLC0415
        _minimal_hard_gate_repair,
        _surface_hard_gate_violations,
    )

    records = _buffered_affective_candidate_records(raw_output)
    degraded_single = bool(
        allow_single_degradation
        and expected_candidate_count == 2
        and len(records) == 1
    )
    if len(records) != expected_candidate_count and not degraded_single:
        raise RuntimeError("AFFECTIVE_TURN_CANDIDATE_COUNT_INVALID")
    candidates = [str(record.get("reply_text") or "") for record in records]
    scores = score_isolated_surface_candidates(compiled_request, candidates)
    eligible: list[Any] = []
    gated_sentences: dict[int, str] = {}
    rejected_reasons: dict[int, tuple[str, ...]] = {}
    locally_repaired_indices: set[int] = set()
    locally_repaired_reasons: dict[int, tuple[str, ...]] = {}
    for index, row in enumerate(scores):
        reasons: list[str] = []
        if bool(row.hard_fail):
            reasons.append("AFFECTIVE_TURN_CANDIDATE_SELECTION_REJECTED")
        # ``control.must_include`` contains semantic guidance (for example,
        # "自然确认会尊重空间"), not literal substrings.  Ranking observations
        # stay soft, while the same deterministic hard gate used by normal
        # Surface replies decides whether this candidate can be sent.
        surface_violations = _surface_hard_gate_violations(compiled_request, row.text)
        locally_repairable = {
            "RELATIONAL_REPAIR_SOLICITS_RESTATEMENT",
            "RELATIONAL_REPAIR_CONTINUATION_PRESSURE",
            "RELATIONAL_REPAIR_UNGROUNDED_SELF_CAUSE",
            "RELATIONAL_REPAIR_MECHANICAL_REPETITION",
            "RELATIONAL_REPAIR_CONTAINED_HURT_MISSING",
            "RELATIONAL_REPAIR_SELF_ERASURE",
            "RECENT_AFFECTIVE_REPLY_REPETITION",
            "USER_AFFECT_PERSPECTIVE_REVERSAL",
            "RELATIONAL_HOSTILE_RETALIATION",
            "QUESTION_BUDGET_PROMPTING_USER",
            "MIXED_AFFECT_CONTINUITY_DROPPED",
            "MIXED_AFFECT_DISMISSAL",
            "MIXED_SELF_RESONANCE_MISSING",
            "UNPROVIDED_NOTIFICATION_ACTION",
            "UNPROVIDED_WORK_STATE_CLAIM",
            "AFFECTIVE_IMPLIED_WAITING",
            "AMBIGUOUS_AFFECT_RESOLUTION_PRESSURE",
            "UNSUPPORTED_TASK_FUTURE_EVENT",
            "UNSUPPORTED_TASK_RISK_DETAIL",
            "TASK_HELP_MISSING_INPUT_BOUNDARY",
            "RELATIONAL_AUTONOMY_PRESSURE",
            "RELATIONAL_AUTONOMY_POLICY_DISCLAIMER",
            "BOUNDARY_SOUR_WITHDRAWAL",
            "AMBIGUOUS_AFFECT_DEVALUATION",
            "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
            "JEALOUSY_DISMISSIVE_DISTANCE",
            "RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION",
            "UNGROUNDED_PHYSICAL_COPRESENCE",
        }
        has_repair_trigger = bool(
            set(surface_violations).intersection(
                {
                    "RELATIONAL_REPAIR_SOLICITS_RESTATEMENT",
                    "RELATIONAL_REPAIR_CONTINUATION_PRESSURE",
                    "RELATIONAL_REPAIR_UNGROUNDED_SELF_CAUSE",
                    "RELATIONAL_REPAIR_MECHANICAL_REPETITION",
                    "RELATIONAL_REPAIR_CONTAINED_HURT_MISSING",
                    "RELATIONAL_REPAIR_SELF_ERASURE",
                    "RECENT_AFFECTIVE_REPLY_REPETITION",
                    "USER_AFFECT_PERSPECTIVE_REVERSAL",
                    "RELATIONAL_HOSTILE_RETALIATION",
                    "MIXED_AFFECT_CONTINUITY_DROPPED",
                    "MIXED_AFFECT_DISMISSAL",
                    "MIXED_SELF_RESONANCE_MISSING",
                    "UNPROVIDED_NOTIFICATION_ACTION",
                    "UNPROVIDED_WORK_STATE_CLAIM",
                    "AFFECTIVE_IMPLIED_WAITING",
                    "AMBIGUOUS_AFFECT_RESOLUTION_PRESSURE",
                    "UNSUPPORTED_TASK_FUTURE_EVENT",
                    "UNSUPPORTED_TASK_RISK_DETAIL",
                    "TASK_HELP_MISSING_INPUT_BOUNDARY",
                    "RELATIONAL_AUTONOMY_PRESSURE",
                    "RELATIONAL_AUTONOMY_POLICY_DISCLAIMER",
                    "BOUNDARY_SOUR_WITHDRAWAL",
                    "AMBIGUOUS_AFFECT_DEVALUATION",
                    "UNRESOLVED_AFFECT_FALSE_RESOLUTION",
                    "JEALOUSY_DISMISSIVE_DISTANCE",
                    "RELATIONAL_AUTONOMY_RELATIONSHIP_MINIMIZATION",
                    "UNGROUNDED_PHYSICAL_COPRESENCE",
                }
            )
        )
        if (
            allow_single_degradation
            and not bool(row.hard_fail)
            and has_repair_trigger
            and set(surface_violations).issubset(locally_repairable)
        ):
            repaired = _minimal_hard_gate_repair(compiled_request, row.text)
            repaired_gate = gate_stream_sentence(repaired)
            if (
                repaired
                and not _surface_hard_gate_violations(compiled_request, repaired)
                and repaired_gate.accepted
            ):
                eligible.append(row)
                gated_sentences[index] = repaired_gate.sentence
                locally_repaired_indices.add(index)
                locally_repaired_reasons[index] = tuple(surface_violations)
                continue
        reasons.extend(surface_violations)
        gate = gate_stream_sentence(row.text)
        if not gate.accepted:
            reasons.append(gate.reason_code)
        record = records[index]
        normalized_payload = _safe_mapping(record.get("normalized_payload"))
        if japanese_companion_voice_enabled() and not str(
            normalized_payload.get("tts_text_ja") or ""
        ).strip():
            reasons.append("JAPANESE_SPEECH_TEXT_MISSING")
        if reasons:
            rejected_reasons[index] = tuple(dict.fromkeys(reasons))
            continue
        eligible.append(row)
        gated_sentences[index] = gate.sentence

    # A repaired candidate must never outrank an untouched candidate merely
    # because its pre-repair language score was higher.  The previous ordering
    # could choose a locally normalized reply even when the provider returned
    # another fully valid alternative, then incorrectly downgrade the whole
    # turn to ``benefit_eligible=false``.  Prefer the best clean candidate and
    # use a repaired one only when no clean alternative survived.
    clean_eligible = [
        row
        for index, row in enumerate(scores)
        if row in eligible and index not in locally_repaired_indices
    ]
    selected = select_best_isolated_candidate(clean_eligible)
    if selected is None:
        selected = select_best_isolated_candidate(eligible)
    if selected is None:
        unfiltered = select_best_isolated_candidate(scores)
        unfiltered_index = next(
            (index for index, row in enumerate(scores) if row is unfiltered),
            -1,
        )
        reasons = rejected_reasons.get(unfiltered_index, ())
        raise RuntimeError(
            reasons[0] if reasons else "AFFECTIVE_TURN_CANDIDATE_SELECTION_REJECTED"
        )
    selected_index = next(
        (index for index, row in enumerate(scores) if row is selected),
        -1,
    )
    if selected_index < 0:
        raise RuntimeError("AFFECTIVE_TURN_CANDIDATE_SELECTION_REJECTED")
    selected_sentence = gated_sentences.get(selected_index, "")
    if not selected_sentence:
        raise RuntimeError("AFFECTIVE_TURN_CANDIDATE_SELECTION_REJECTED")
    selected_record = records[selected_index]
    locally_repaired = selected_index in locally_repaired_indices
    selected_payload = (
        None if locally_repaired else selected_record.get("structured_payload")
    )
    normalized_payload = selected_record.get("normalized_payload")
    if isinstance(selected_payload, Mapping):
        if japanese_companion_voice_enabled() and not str(
            _safe_mapping(normalized_payload).get("tts_text_ja") or ""
        ).strip():
            raise RuntimeError("JAPANESE_SPEECH_TEXT_MISSING")
        selected_payload = dict(selected_payload)
        selected_payload["reply_text"] = selected_sentence
        selected_output = json.dumps(
            selected_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        selected_output = selected_sentence
    if locally_repaired:
        selection_status = "DEGRADED_LOCAL_REPAIR_AFTER_RETRY"
        selected_output_shape = "SINGLE_TEXT_VOICE_DEGRADED"
    elif degraded_single:
        selection_status = "DEGRADED_SINGLE_AFTER_RETRY"
        selected_output_shape = (
            "SINGLE_TEXT_VOICE_DEGRADED"
            if isinstance(selected_payload, Mapping)
            else "SINGLE_TEXT_DEGRADED"
        )
    elif allow_single_degradation and not bool(selected.passed):
        selection_status = "DEGRADED_LOW_QUALITY_AFTER_RETRY"
        selected_output_shape = (
            "SINGLE_TEXT_VOICE_DEGRADED"
            if isinstance(selected_payload, Mapping)
            else "SINGLE_TEXT_DEGRADED"
        )
    else:
        selection_status = "PAIRED_SELECTION"
        selected_output_shape = (
            "PAIRED_TEXT_VOICE"
            if isinstance(selected_payload, Mapping)
            else "TEXT"
        )
    if isinstance(trace_stage_meta, dict):
        trace_stage_meta["affective_candidate_selection"] = {
            "schema_version": "affective_candidate_selection.v1",
            "candidate_count": len(scores),
            "required_candidate_count": expected_candidate_count,
            "selection_status": selection_status,
            "benefit_eligible": (
                not degraded_single
                and not locally_repaired
                and bool(selected.passed)
            ),
            "rejected_candidate_count": len(rejected_reasons),
            "rejected_candidate_reason_codes": [
                list(rejected_reasons[index])[:8]
                for index in sorted(rejected_reasons)
            ],
            "selected_index": selected_index,
            "selected_score": round(float(selected.score), 4),
            "selected_passed": bool(selected.passed),
            "selected_reason_codes": [
                *list(selected.reason_codes)[:15],
                *(
                    [
                        "LOCAL_REPAIR_AFTER_RETRY:"
                        + ",".join(locally_repaired_reasons.get(selected_index, ()))
                    ]
                    if locally_repaired
                    else []
                ),
            ],
            "selected_output_shape": selected_output_shape,
            "candidate_digests": [
                hashlib.sha256(str(row.text).encode("utf-8")).hexdigest()
                for row in scores
            ],
        }
    return selected_output


async def _native_affective_sentence_stream(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace: dict[str, Any],
    surface_input: SurfaceMainChainInput,
    compiled_request: Any,
    plan: Mapping[str, Any],
    started_ms: int,
) -> AsyncGenerator[str, None]:
    """Generate plaintext once, stage every sentence, then expose it over SSE.

    The plan and sentence intents are persisted before visibility.  If a model
    stops after any sentence, the caller receives an explicit partial-stream
    rejection rather than a second provider attempt that could duplicate what
    the user already saw.
    """

    from host_brain.model_runtime.contracts import (  # noqa: PLC0415
        ModelAttemptRequest,
        ModelAttemptStreamEvent,
        ModelAttemptStreamEventKind,
        PromptRef,
    )
    from host_brain.model_runtime.model_attempt_service import (  # noqa: PLC0415
        ModelStreamProtocolError,
    )
    from host_brain.model_runtime.live_prompts import (  # noqa: PLC0415
        SURFACE_MAIN_TEXT_PROMPT_ID,
    )
    from host_brain.model_runtime.trace import (  # noqa: PLC0415
        model_attempt_result_trace,
    )

    request_id = str(getattr(agent_input, "request_id", "") or "").strip()
    plan_id = str(plan.get("plan_id") or "").strip()
    expression = _safe_mapping(plan.get("expression_plan"))
    synchronization_id = str(expression.get("synchronization_id") or "").strip()
    governed_delivery_authority = _governed_task_delivery_authority(agent_input)
    governed_model_budget = _governed_model_budget_guard(trace)
    try:
        if governed_delivery_authority is not None:
            # A governed worker is still pre-Acceptance.  Its immutable plan
            # belongs in the Task-owned visibility ledger, never in Life.
            planned_receipt = await asyncio.to_thread(
                governed_delivery_authority.stage_plan,
                plan,
            )
        else:
            planned_receipt = await asyncio.to_thread(
                _submit_surface_affective_turn_planned,
                life_command_service=ports.get_life_command_service(),
                life_snapshot_reader=ports.get_life_snapshot_reader(),
                agent_input=agent_input,
                trace_stage_meta=trace,
            )
    except Exception as exc:
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "AFFECTIVE_TURN_PLAN_COMMIT_FAILED",
            "error_type": type(exc).__name__,
        }
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "AFFECTIVE_TURN_PLAN_COMMIT_FAILED",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
                    "durability": "fail_closed_before_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return
    trace["affective_turn_plan_receipt"] = {
        "command_id": planned_receipt.get("command_id"),
        "event_id": planned_receipt.get("event_id") or planned_receipt.get("delivery_id"),
        "state_version": planned_receipt.get("state_version"),
        "replayed": bool(planned_receipt.get("replayed")),
        "authority": (
            "TASK_BOUND_VISIBILITY" if governed_delivery_authority is not None else "LIFE"
        ),
    }

    messages = build_native_stream_surface_messages(compiled_request)
    expression_prompt_evidence = _surface_expression_prompt_evidence(
        messages=messages,
        style_anchors=tuple(
            getattr(compiled_request.control, "style_anchor", ()) or ()
        ),
    )
    stream_started = time.perf_counter()
    first_token_ms: int | None = None
    splitter = SentenceSplitter()
    narrative_parts: list[str] = []
    sent_sentence_count = 0
    sentence_receipts: list[dict[str, Any]] = []
    model_scene_type, model_cognitive_tier, model_route_lane = (
        _surface_model_request_profile(trace)
    )
    trace["surface_model_route"] = {
        "scene_type": model_scene_type,
        "cognitive_tier": model_cognitive_tier,
        "route_lane": model_route_lane,
    }
    model_request = ModelAttemptRequest(
        request_id=request_id or "surface-main-chain",
        scene_type=model_scene_type,
        cognitive_tier=model_cognitive_tier,
        prompt=PromptRef(SURFACE_MAIN_TEXT_PROMPT_ID, {"messages": messages}),
        deadline_ms=_surface_model_deadline_ms(governed_model_budget),
        foreground=True,
        model_budget_guard=governed_model_budget,
        metadata={
            "chain": "surface_main_native_sentence_stream",
            "affective_turn_plan_id": plan_id,
            "runtime_path": str(trace.get("runtime_path") or ""),
            "route_lane": model_route_lane,
            **expression_prompt_evidence,
            **(
                # The durable attempt journal must be tied to the immutable
                # TaskNode that owns this stream.  The buffered Surface path
                # already carries this field; without it a native stream can
                # finish delivery but is correctly rejected at acceptance as
                # an unbound model attempt.
                {"model_node_id": governed_model_budget.node_id}
                if governed_model_budget is not None
                else {}
            ),
        },
    )
    final_model_attempt: Any | None = None
    final_result_stream = False
    legacy_stream_fallback_reason = ""

    async def model_chunks() -> AsyncGenerator[str, None]:
        """Prefer a terminally-attested stream without weakening legacy Host chat.

        A direct native stream has its own per-sentence durable visibility
        authority, so it can expose a delta before the provider's terminal
        usage record arrives.  It may fall back to the old unmetered stream
        only when the provider proves *before emitting anything* that it does
        not implement the explicit final-result protocol.  Any malformed or
        interrupted attested stream stays fail-closed: replaying it through a
        second provider could duplicate words the user already saw.
        """

        nonlocal final_model_attempt, final_result_stream, legacy_stream_fallback_reason
        model_service = ports.model_attempt_service()
        stream_with_result = getattr(model_service, "stream_with_result", None)
        if callable(stream_with_result):
            emitted_attested_delta = False
            final_seen = False
            try:
                async for event in stream_with_result(model_request):
                    if not isinstance(event, ModelAttemptStreamEvent):
                        raise ModelStreamProtocolError(
                            "surface model stream emitted an untyped result event"
                        )
                    if event.kind is ModelAttemptStreamEventKind.DELTA:
                        if final_seen:
                            raise ModelStreamProtocolError(
                                "surface model stream emitted a delta after its final result"
                            )
                        emitted_attested_delta = True
                        yield event.text
                        continue
                    if event.kind is not ModelAttemptStreamEventKind.FINAL or final_seen:
                        raise ModelStreamProtocolError(
                            "surface model stream emitted an invalid final result sequence"
                        )
                    final_seen = True
                    final_model_attempt = event.result
                if not final_seen or final_model_attempt is None:
                    raise ModelStreamProtocolError(
                        "surface model stream ended without a final audited result"
                    )
                final_result_stream = True
                return
            except Exception as exc:
                # ModelAttemptService classifies this path before starting a
                # provider attempt.  No visible delta and this exact code are
                # the only safe conditions for the legacy direct-Host stream.
                if (
                    emitted_attested_delta
                    or str(getattr(exc, "code", "")).strip().upper()
                    != "MODEL_STREAM_RESULT_UNSUPPORTED"
                ):
                    raise
                legacy_stream_fallback_reason = "MODEL_STREAM_RESULT_UNSUPPORTED"

        async for raw_chunk in model_service.stream(model_request):
            chunk = str(raw_chunk or "")
            if chunk:
                yield chunk

    async def stage_sentence_receipt(
        sentence: str,
        *,
        sentence_index: int,
    ) -> Mapping[str, Any]:
        if governed_delivery_authority is not None:
            return await asyncio.to_thread(
                governed_delivery_authority.stage_sentence,
                plan=plan,
                sentence=sentence,
                sentence_index=sentence_index,
            )
        return await asyncio.to_thread(
            _submit_surface_sentence_delivery_intent,
            life_command_service=ports.get_life_command_service(),
            life_snapshot_reader=ports.get_life_snapshot_reader(),
            agent_input=agent_input,
            trace_stage_meta=trace,
            sentence=sentence,
            sentence_index=sentence_index,
        )

    async def record_stream_control(
        *,
        event_kind: str,
        sent_count: int,
        reason_code: str,
    ) -> Mapping[str, Any] | None:
        if governed_delivery_authority is not None:
            return await asyncio.to_thread(
                governed_delivery_authority.record_stream_control,
                plan=plan,
                event_kind=event_kind,
                sent_sentence_count=sent_count,
                reason_code=reason_code,
            )
        return None

    async def emit_staged_sentence(
        sentence: str,
        *,
        sentence_index: int,
    ) -> AsyncGenerator[str, None]:
        """Persist one final sentence before any user-visible stream frame."""

        receipt = await stage_sentence_receipt(sentence, sentence_index=sentence_index)
        delivery_id = str(receipt.get("delivery_id") or receipt.get("event_id") or "").strip()
        if governed_delivery_authority is not None and not delivery_id:
            raise RuntimeError("TASK_CHAT_DELIVERY_RECEIPT_INVALID")
        sentence_receipts.append(
            {
                "command_id": receipt.get("command_id"),
                "event_id": receipt.get("event_id") or delivery_id,
                "delivery_id": delivery_id or None,
                "sentence_index": sentence_index,
                "replayed": bool(receipt.get("replayed")),
                "expression_delivery": _safe_mapping(receipt.get("expression_delivery")),
            }
        )
        narrative_parts.append(sentence)
        # Direct Host streaming retains its pre-staged expression protocol.
        # Governed nodes defer Voice/Avatar intents to Task Acceptance, so no
        # external or protocol effect is exposed before verification.
        if governed_delivery_authority is None:
            delivery_event = _expression_delivery_sse(
                receipt,
                sentence_index=sentence_index,
            )
            if delivery_event is not None:
                yield _sse(delivery_event)
        yield _sse({"phase": "RENDER", "chunk": sentence})
        sentence_event: dict[str, Any] = {
            "phase": "SENTENCE",
            "sentence": sentence,
            "sentence_index": sentence_index,
            "affective_turn_plan_id": plan_id,
            "synchronization_id": synchronization_id,
        }
        if governed_delivery_authority is not None:
            sentence_event.update(
                {
                    "delivery_id": delivery_id,
                    "delivery_status": str(
                        receipt.get("delivery_status") or "TASK_NODE_SENTENCE_STAGED"
                    ),
                    "replayed": bool(receipt.get("replayed")),
                    "request_id": receipt.get("request_id"),
                    "graph_id": receipt.get("graph_id"),
                    "graph_revision": receipt.get("graph_revision"),
                    "node_id": receipt.get("node_id"),
                    "attempt_id": receipt.get("attempt_id"),
                    "lease_id": receipt.get("lease_id"),
                    "lease_owner_id": receipt.get("lease_owner_id"),
                    "fencing_token": receipt.get("fencing_token"),
                    "node_grant_ref": receipt.get("node_grant_ref"),
                    "self_id": receipt.get("self_id"),
                    "relationship_subject_id": receipt.get("relationship_subject_id"),
                    "workspace_id": receipt.get("workspace_id"),
                    "self_affect_revision": receipt.get("self_affect_revision"),
                }
            )
        yield _sse(sentence_event)

    try:
        async for raw_chunk in model_chunks():
            if first_token_ms is None:
                first_token_ms = max(
                    0,
                    int((time.perf_counter() - stream_started) * 1000),
                )
            for raw_sentence in splitter.feed(str(raw_chunk or "")):
                candidate = _sanitize_native_stream_sentence(
                    raw_sentence,
                    agent_input=agent_input,
                    trace=trace,
                )
                gate = gate_stream_sentence(candidate)
                if not gate.accepted:
                    raise RuntimeError(gate.reason_code)
                async for event in emit_staged_sentence(
                    gate.sentence,
                    sentence_index=sent_sentence_count,
                ):
                    yield event
                sent_sentence_count += 1
            if len(splitter.buffer) > 2_000:
                raise RuntimeError("SURFACE_STREAM_SENTENCE_BOUNDARY_REQUIRED")

        remainder = splitter.flush()
        if remainder:
            candidate = _sanitize_native_stream_sentence(
                remainder,
                agent_input=agent_input,
                trace=trace,
            )
            gate = gate_stream_sentence(candidate)
            if not gate.accepted:
                raise RuntimeError(gate.reason_code)
            async for event in emit_staged_sentence(
                gate.sentence,
                sentence_index=sent_sentence_count,
            ):
                yield event
            sent_sentence_count += 1
    except asyncio.CancelledError:
        try:
            if governed_delivery_authority is not None:
                await asyncio.shield(
                    record_stream_control(
                        event_kind="STREAM_CANCELLED",
                        sent_count=sent_sentence_count,
                        reason_code="SURFACE_STREAM_CANCELLED",
                    )
                )
            else:
                await asyncio.shield(
                    asyncio.to_thread(
                        _submit_surface_stream_cancelled,
                        life_command_service=ports.get_life_command_service(),
                        life_snapshot_reader=ports.get_life_snapshot_reader(),
                        agent_input=agent_input,
                        trace_stage_meta=trace,
                        sent_sentence_count=sent_sentence_count,
                        reason_code="SURFACE_STREAM_CANCELLED",
                    )
                )
        except Exception:
            # Cancellation must still propagate.  The next connection can
            # inspect the staged sentence receipts and reconcile the stream.
            pass
        raise
    except Exception as exc:
        interruption_reason = _native_stream_failure_reason(exc)
        trace["native_sentence_stream"] = {
            "status": "INTERRUPTED",
            "reason_code": interruption_reason,
            "sent_sentence_count": sent_sentence_count,
        }
        if governed_delivery_authority is not None:
            try:
                await asyncio.shield(
                    record_stream_control(
                        event_kind="STREAM_INTERRUPTED",
                        sent_count=sent_sentence_count,
                        reason_code=interruption_reason,
                    )
                )
            except Exception:
                # The Task Runtime will classify this as a recovery-required
                # boundary either way.  Never manufacture a terminal reject
                # after a sentence has become visible.
                pass
            if sent_sentence_count == 0:
                # No text, Voice or Avatar action has crossed the visibility
                # boundary.  This is a normal fail-closed terminal outcome,
                # not an ``unknown effect`` recovery case.  Preserve the
                # stable model/runtime cause in the immutable Task delivery
                # journal so the worker can release its lease and budget
                # without re-running an unseen provider attempt.
                yield _sse(
                    {
                        "phase": "COMMIT",
                        "done": True,
                        "decision": "REJECT",
                        "reason_code": interruption_reason,
                        "narrative": "",
                        "request_id": request_id,
                        "sent_sentence_count": 0,
                        "audit_refs": {
                            "request_id": request_id,
                            "path": "surface_reply:native_affective_stream",
                            "durability": "fail_closed_before_visibility",
                            "trace_stage_meta": dict(trace),
                        },
                    }
                )
                return
            raise RuntimeError("TASK_CHAT_STREAM_INTERRUPTED") from exc
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT" if sent_sentence_count else "NO_REPLY",
                "reason_code": "SURFACE_STREAM_INTERRUPTED",
                "narrative": "".join(narrative_parts),
                "request_id": request_id,
                "sent_sentence_count": sent_sentence_count,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    narrative = "".join(narrative_parts).strip()
    if not narrative:
        if governed_delivery_authority is not None:
            await record_stream_control(
                event_kind="STREAM_COMPLETED",
                sent_count=0,
                reason_code="SURFACE_STREAM_EMPTY_RESPONSE",
            )
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "NO_REPLY",
                "reason_code": "SURFACE_STREAM_EMPTY_RESPONSE",
                "narrative": "",
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    stream_control_receipt: Mapping[str, Any] | None = None
    if governed_delivery_authority is not None:
        try:
            stream_control_receipt = await record_stream_control(
                event_kind="STREAM_COMPLETED",
                sent_count=sent_sentence_count,
                reason_code="",
            )
        except Exception as exc:
            trace["native_sentence_stream"] = {
                "status": "INTERRUPTED",
                "reason_code": "TASK_CHAT_STREAM_CONTROL_FAILED",
                "sent_sentence_count": sent_sentence_count,
            }
            raise RuntimeError("TASK_CHAT_STREAM_CONTROL_FAILED") from exc

    total_latency_ms = max(0, int(time.time() * 1000) - started_ms)
    llm_latency_ms = max(0, int((time.perf_counter() - stream_started) * 1000))
    final_usage_attested = _final_stream_usage_is_attested(final_model_attempt)
    stream_summary = {
        "mode": (
            "governed_node_sentence_stream"
            if governed_delivery_authority is not None
            else "native_sentence_stream"
        ),
        "sentence_count": sent_sentence_count,
        # This field never becomes a public stream trace.  It is the exact
        # locally-gated sentence sequence needed to compile the deferred
        # Acceptance-side Voice/Avatar protocol for a governed node.  Keeping
        # it here prevents foreground completion from coalescing visible
        # sentences into a different utterance boundary.
        "stable_sentences": tuple(narrative_parts),
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "self_affect_revision": plan.get("self_affect_revision"),
        "first_token_ms": first_token_ms,
        "provider_usage_status": (
            "STREAM_FINAL_USAGE_ATTESTED"
            if final_usage_attested
            else "STREAM_FINAL_RESULT_AUDITED_UNMEASURED"
            if final_result_stream
            else "STREAM_ATTEMPT_AUDITED_NO_FINAL_USAGE"
        ),
    }
    if final_model_attempt is not None:
        attempt_trace = model_attempt_result_trace(final_model_attempt)
    else:
        attempt_trace = {}
    attempt_trace.update(
        {
            "streaming": True,
            "prompt_id": SURFACE_MAIN_TEXT_PROMPT_ID,
            "message_count": len(messages),
            "generate_elapsed_ms": llm_latency_ms,
            "first_token_ms": first_token_ms,
            "provider_usage_status": stream_summary["provider_usage_status"],
        }
    )
    trace["surface_model_attempt"] = attempt_trace
    trace["native_sentence_stream"] = {
        "status": "COMPLETED",
        "plan_id": plan_id,
        "synchronization_id": synchronization_id,
        "sent_sentence_count": sent_sentence_count,
        "sentence_receipts": sentence_receipts,
        "stream_control_receipt": (
            dict(stream_control_receipt) if stream_control_receipt is not None else None
        ),
        "model_stream_protocol": (
            "FINAL_RESULT_WITH_USAGE"
            if final_usage_attested
            else "FINAL_RESULT_UNMEASURED"
            if final_result_stream
            else "LEGACY_NO_FINAL_USAGE"
        ),
        "legacy_stream_fallback_reason": legacy_stream_fallback_reason or None,
    }
    timings_ms = _surface_timings_ms(
        total_latency_ms=total_latency_ms,
        llm_latency_ms=llm_latency_ms,
    )
    envelope = ReplyEnvelope.final_reply(
        text=narrative,
        response_form="text",
        source="surface_main_chain_native_stream",
        audit={
            "request_id": request_id,
            "affective_turn_plan_id": plan_id,
            "synchronization_id": synchronization_id,
            "self_emotion_tag": str(plan.get("self_emotion_tag") or ""),
            "companion_act": str(_safe_mapping(plan.get("companion_act")).get("kind") or ""),
        },
    )
    try:
        foreground_receipt = await asyncio.to_thread(
            _submit_surface_foreground_completed,
            life_command_service=ports.get_life_command_service(),
            life_snapshot_reader=ports.get_life_snapshot_reader(),
            relationship_lifecycle_service=ports.get_relationship_lifecycle_service(),
            agent_input=agent_input,
            result=None,
            envelope=envelope,
            narrative=narrative,
            trace_stage_meta=trace,
            timings_ms=timings_ms,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=llm_latency_ms,
            stream_summary=stream_summary,
            first_token_ms=first_token_ms,
            buffer_stream=False,
        )
    except Exception as exc:
        trace["life_causality"] = {
            "status": "FAILED",
            "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED_AFTER_STREAM",
            "error_type": type(exc).__name__,
            "sent_sentence_count": sent_sentence_count,
        }
        if governed_delivery_authority is not None:
            try:
                await asyncio.shield(
                    record_stream_control(
                        event_kind="STREAM_INTERRUPTED",
                        sent_count=sent_sentence_count,
                        reason_code="TASK_ACCEPTANCE_EFFECT_INTENT_BUILD_FAILED",
                    )
                )
            except Exception:
                pass
            raise RuntimeError("TASK_CHAT_ACCEPTANCE_EFFECT_INTENT_BUILD_FAILED") from exc
        yield _sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "LIFE_FOREGROUND_COMMIT_FAILED_AFTER_STREAM",
                "narrative": narrative,
                "request_id": request_id,
                "sent_sentence_count": sent_sentence_count,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "surface_reply:native_affective_stream",
                    "trace_stage_meta": dict(trace),
                },
            }
        )
        return

    trace["life_causality"] = {
        "status": str(foreground_receipt.get("status") or "UNKNOWN"),
        "reason_code": foreground_receipt.get("reason_code"),
        "command_id": foreground_receipt.get("command_id"),
        "event_id": foreground_receipt.get("event_id"),
        "state_version": foreground_receipt.get("state_version"),
        "outbox_count": foreground_receipt.get("outbox_count"),
        "replayed": bool(foreground_receipt.get("replayed")),
    }
    release_delivery_event = _expression_delivery_sse(foreground_receipt)
    if release_delivery_event is not None:
        yield _sse(release_delivery_event)
    effect_intents = [
        dict(item)
        for item in list(foreground_receipt.get("effect_intents") or [])
        if isinstance(item, Mapping)
    ]
    model_audit = _surface_model_audit_fields(agent_input=agent_input, trace_stage_meta=trace)
    yield _sse(
        {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "reason_code": envelope.reason_code or None,
            "narrative": narrative,
            "request_id": request_id,
            "reply_envelope": envelope.as_dict(),
            "acceptance_effect_intents": effect_intents,
            "audit_refs": {
                "request_id": request_id,
                "path": "surface_reply:native_affective_stream",
                "latency_ms": total_latency_ms,
                "first_token_ms": first_token_ms,
                "llm_latency_ms": llm_latency_ms,
                "model_id": model_audit["model_id"],
                "provider_name": model_audit["provider_name"],
                "response_form": "text",
                "timings_ms": timings_ms,
                "reply_envelope": envelope.as_dict(),
                "surface_main_chain": _public_result_trace(
                    None,
                    stream_summary=stream_summary,
                ),
                "trace_stage_meta": {
                    **trace,
                    "surface_main_chain_used": True,
                    "reply_source": "surface_main_chain_native_stream",
                    "surface_renderer": {
                        "status": "OK",
                        "invoked": True,
                        "affected_output": True,
                        "source": "surface_main_chain_native_stream",
                        "reason_code": "",
                    },
                },
            },
        }
    )
