"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from collections.abc import Mapping
from host_brain.core.runtime_helpers import (  # noqa: F401
    _format_template_text,
    _load_template_mapping,
    _mapping_section,
    _mapping_text,
    _parse_bool_env,
    _sse,
    _airlock_sanitize_input,
    _airlock_sanitize_output,
    _build_reply_preview,
    _coerce_trace_mapping,
    _coerce_trace_score,
    _coerce_trace_bool,
    _normalize_trace_stage_meta,
    _resolve_envelope_confidence,
    _build_trace_output_digest,
    _detect_trace_fallback_used,
    _summarize_motive_state_for_trace,
    _summarize_conversation_interpretation_for_trace,
    _summarize_reply_plan_for_trace,
    _summarize_tactic_ledger_for_trace,
    _summarize_topic_ledger_for_trace,
    _summarize_affect_state_for_trace,
    _summarize_relationship_state_for_trace,
    _summarize_drive_state_for_trace,
    _summarize_emotion_context_for_trace,
    _summarize_persona_resident_memory_for_trace,
)
import asyncio
import concurrent.futures
from host_brain.persona.response_form_contract import derive_render_profile_for_response_form
import logging
from functools import partial
import re
from host_brain.core.orchestration_settings import get_orchestration_settings
_logger = logging.getLogger("Natsume.agent_runtime_shared")


_POST_PROCESS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=get_orchestration_settings().post_process_executor_workers,
    thread_name_prefix="postprocess",
)


async def _run_in_post_process_executor(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _POST_PROCESS_EXECUTOR,
        partial(func, *args, **kwargs),
    )


def _load_runtime_texts() -> dict[str, Any]:
    return _load_template_mapping("cortex_orchestrator.runtime_texts")


def _normalize_runtime_lang(render_target_lang: str | None) -> str:
    return "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"


def _load_runtime_texts_section(section_name: str, *, render_target_lang: str | None = None) -> dict[str, Any]:
    raw_section = _mapping_section(_load_runtime_texts(), section_name)
    lang_key = _normalize_runtime_lang(render_target_lang)
    localized_section = _mapping_section(raw_section, lang_key)
    return localized_section or raw_section


def _load_runtime_text_value(
    key: str,
    *,
    render_target_lang: str | None = None,
    default: str = "",
    **kwargs: Any,
) -> str:
    runtime_texts = _load_runtime_texts()
    raw_value = runtime_texts.get(key)
    lang_key = _normalize_runtime_lang(render_target_lang)
    if isinstance(raw_value, Mapping):
        localized = raw_value.get(lang_key)
        if isinstance(localized, str):
            return _format_template_text(localized, **kwargs)
    return _mapping_text(runtime_texts, key, default=default, **kwargs)


def _resolve_text_render_target_lang(
    *,
    request_body: Any,
    sanitized_message: str,
) -> str:
    """Resolve language for the active output profile.

    Platform is not the language boundary: text and the current voice model render
    as Chinese, media uses a structured action envelope. Gateway/QQ is only a
    transport layer.
    """
    _ = sanitized_message
    profile = derive_render_profile_for_response_form(str(getattr(request_body, "response_form", None) or ""))
    if profile == "ja_voice":
        return "ja"
    if profile == "media_action":
        return "media"
    return "zh"


_RECENT_TURN_USER_QUERY_RE = re.compile(r"(我刚才|我刚刚|我上一句|我上句|我前一句|我前面).*?(说了什么|发了什么|是什么)")


_RECENT_TURN_ASSISTANT_QUERY_RE = re.compile(r"(你刚才|你刚刚|你上一句|你上句|你前一句|你前面).*?(说了什么|回了什么|回复了什么|是什么)")


_RECENT_TURN_GENERIC_QUERY_RE = re.compile(
    r"(上一句|上句|前一句|刚才说的什么|刚刚说的什么|刚才(?:说|聊)到哪|刚刚(?:说|聊)到哪)"
    r".*?(是什么|是什么来着|说了什么|了)?"
)


_CONTEXTUAL_MEMORY_RECALL_RE = re.compile(
    r"(刚才|刚刚|上一轮|上轮|前面|让你记|叫你记|要你记|那个|临时).{0,40}(口令|暗号|关键词|锚点|记住).{0,40}(是什么|什么|啥|本身|内容|还记得|记得吗)"
    r"|(?:口令|暗号|关键词|锚点).{0,24}(是什么|什么|啥|本身|内容|还记得|记得吗)"
    r"|小橙灯.{0,12}(指什么|是什么)",
    re.IGNORECASE,
)


_CONTEXTUAL_MEMORY_TRANSFORM_RE = re.compile(
    r"(?:这个|那个|它|刚才那个|刚刚那个).{0,16}(口令|暗号|关键词|锚点).{0,32}(变成|改成|写成|造句|短句|包含)"
    r"|(?:口令|暗号|关键词|锚点).{0,24}(变成|改成|写成|造句|短句|包含)",
    re.IGNORECASE,
)


_CONTEXTUAL_MEMORY_QUOTED_VALUE_RE = re.compile(r"[「『“\"]([^」』”\"]{1,64})[」』”\"]")


_CONTEXTUAL_MEMORY_WRITE_RE = re.compile(r"(记住|记一下|记下来|记着|remember this|save this)", re.IGNORECASE)


_CONTEXTUAL_MEMORY_WRITE_ACK_RE = re.compile(
    r"(记住|记一下|记下来|记着).{0,32}(口令|暗号|关键词|锚点).{0,64}[「『“\"]([^」』”\"]{1,32})[」』”\"]",
    re.IGNORECASE,
)


_CONTEXTUAL_MEMORY_ALIAS_WRITE_RE = re.compile(
    r"(锚点).{0,24}(别名|小橙灯).{0,32}(橙灯码)|小橙灯.{0,24}(指|也指).{0,24}橙灯码",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_NAME_RE = re.compile(
    r"(给这个表达风格起.{0,12}(名字|名称)|起一个临时名字|临时名字|风格.{0,12}(名字|名称))",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_REPHRASE_RE = re.compile(
    r"(客服腔|更像你自己|刚才那个名字|这个表达风格|这个风格).{0,40}(客服腔|更像你自己|风格|名字|表达)"
    r"|(?:不喜欢|别|不要).{0,12}客服腔",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_COMFORT_RE = re.compile(
    r"(按这个风格|按刚才那个表达风格|按刚才那个风格|用这个风格|照这个风格|不用重复背景).{0,40}(安慰|回一句|说一句|我.*累|有点累)"
    r"|(?:我.*累|有点累).{0,40}(按这个风格|按刚才那个表达风格|按刚才那个风格|回一句)",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_SUMMARY_RE = re.compile(
    r"(总结|概括|列要点|整理).{0,24}(刚才|明确要求|表达偏好|表达设定|这个风格|偏好)",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_UPDATE_DIFF_RE = re.compile(
    r"(新版|旧版|更新).{0,24}(表达偏好|表达风格|哪里不同|不同)",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_SEED_RE = re.compile(
    r"(表达偏好|表达设定|表达风格).{0,96}(偏冷|冷色|偏青|青色|少废话|温度|余温|客服腔)"
    r"|(?:偏冷色|冷色|少废话|客服腔).{0,96}(表达|风格|确认)",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_UPDATE_ACK_RE = re.compile(
    r"(更新一下|之后按这个新版理解|按这个新版理解|新版理解).{0,96}(偏青|青色|余温|不是完全冷)",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_APPLY_UPDATED_RE = re.compile(
    r"(按新版风格|按我们新版表达风格|按这些约定).{0,48}(回一句|换成|说法|今天先收工|有点烦|不想解释)",
    re.IGNORECASE,
)


_CONTEXTUAL_STYLE_SELF_CORRECT_RE = re.compile(
    r"(客服腔).{0,48}(自我修正|怎么修正|应该怎么|一句话)",
    re.IGNORECASE,
)


_CONTEXTUAL_REPORT_PREFERENCE_WRITE_RE = re.compile(
    r"(记住|记一下|偏好).{0,48}(系统测试|测试报告).{0,64}(可复现|报告|体感)"
    r"|(?:再修正|修正|更新).{0,24}报告.{0,64}(可复现|主观|体验|备注)",
    re.IGNORECASE,
)


_CONTEXTUAL_REPORT_PREFERENCE_RECALL_RE = re.compile(
    r"(系统测试|测试报告).{0,64}(更相信|核心偏好|要求|修正|不要漏掉|是什么|什么)",
    re.IGNORECASE,
)


_CONTEXTUAL_SESSION_SUMMARY_RE = re.compile(
    r"(三条关键约定|关键约定|哪几件事|列三条|合并成一个短列表).{0,64}(表达风格|锚点|测试报告|可复现|小橙灯)?",
    re.IGNORECASE,
)


_CONTEXTUAL_STABILIZE_RE = re.compile(
    r"(场子).{0,24}(稳住|稳定|怎样)|(?:上一轮|上轮).{0,32}场子",
    re.IGNORECASE,
)


_CONTEXTUAL_WRAP_UP_RE = re.compile(r"(今晚|今天).{0,32}(收尾|收工|不开新坑|新坑|先收)", re.IGNORECASE)


_CONTEXTUAL_LONG_DIALOGUE_METRICS_RE = re.compile(
    r"(长对话|连续对话|连续性).{0,48}(指标|最该看|哪三类|测连续性)",
    re.IGNORECASE,
)


_CONTEXTUAL_WORKBENCH_COLOR_RE = re.compile(r"(工作台).{0,24}(颜色)|颜色.{0,24}工作台", re.IGNORECASE)


_CONTEXTUAL_EMOTIONAL_STABLE_RE = re.compile(
    r"(稳定地在旁边|不想被哄得很热闹|不想.*热闹|有人稳定)",
    re.IGNORECASE,
)


_CONTEXTUAL_COMFORT_PREFERENCE_RE = re.compile(
    r"(如果我说累|我说累).{0,48}(不要讲大道理|不讲大道理|缓下来|不黏糊)",
    re.IGNORECASE,
)


_CONTEXTUAL_FINAL_META_RE = re.compile(
    r"(评价).{0,40}(连续性测试|长对话).{0,40}(实际意义|意义|问题|别自夸)",
    re.IGNORECASE,
)


_DELEGATE_RE = re.compile(r"\[DELEGATE:(\w+?)(?::(.+?))?\]", re.DOTALL)


_DELEGATE_INTERNAL_MARKER_RE = re.compile(r"draft_only|intent_draft|ReAct|LLM gateway|agent_id=", re.IGNORECASE)


_CONTINUATION_QUERY_RE = re.compile(r"^(然后|然后呢|继续|接着|接着呢|后面呢|再然后呢|展开说说|具体一点|继续说|接着来|那后面呢|それで|それでね|それから|続き|続けて)[!！。…~？?]*$")


_GREETING_FASTPATH_RE = re.compile(
    r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好|おはよう|こんにちは|こんばんは|やあ|hello|hi|hey)[!！。…~]*$",
    re.IGNORECASE,
)


_PRESENCE_FASTPATH_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛|いる|いるの|いるかな|まだいる|まだいるの?)[?？!！。…~]*$")


_ACK_FASTPATH_RE = re.compile(r"^(嗯|嗯嗯|哦|噢|好的|好呀|好啊|可以|可以啊|可以呀|收到|对|对啊|是的|行|うん|はい|そう|そうか|わかった|分かった|了解|なるほど)[!！。…~]*$")


_COMFORT_FASTPATH_RE = re.compile(
    r"(抱抱我|抱抱|安慰我|哄哄我|陪陪我|想被抱抱|想要抱抱|我好累|好累啊|有点累|有点困|有点难过|有点委屈|想哭|心情不好|不太开心|不开心|累死了|难受|做不好|しんどい|つらい|疲れた|苦しい|慰めて|そばにいて|付き合って)"
)


_REUNION_FASTPATH_RE = re.compile(r"(好久不见|很久不见|有想我吗|想我吗|会想我吗|想过我吗|会えなかった|思い出した|思い出してた|会いたかった)")


_RIFT_REPAIR_FASTPATH_RE = re.compile(r"(在意之前.*争执|那次争执|之前那次争执|还在意.*争执|之前那次吵架|那次吵架|闹得有点不开心|上次.*不开心|気まず|けんか|引っかかっている|引っかかってる)")


_PLAYFUL_FASTPATH_RE = re.compile(r"(更会逗我开心|逗我开心|会逗我|会哄我开心|更会哄我|更会逗我|偷偷觉得我.*可爱|觉得我.*可爱|有点可爱|可愛いと思って|可愛いって|からかって|笑わせて)")


_DEEP_COMPANIONSHIP_FASTPATH_RE = re.compile(
    r"(认真聊聊最近的状态|认真聊聊最近|慢慢陪我说下去|慢慢陪我聊|陪我说下去|慢慢聊聊最近|想认真聊聊|想和你认真聊聊|认真说一说最近|真面目に.*話|近頃のことを話|急がせずに|ゆっくり話|ちゃんと聞いて|付き合ってくれる)"
)


_TIME_REALITY_CHECK_FASTPATH_RE = re.compile(
    r"(现在不是(晚上|中午|白天|早上)吗|都这么晚了|说什么早上好|说什么中午好|不是中午吗|不是晚上吗|不是早上吗|已经到晚上了|都半夜了|もう夜|もうこんな時間|朝じゃない|昼じゃない|夜なのに)"
)


_LOCAL_MEDIA_CONTROL_FASTPATH_RE = re.compile(
    r"(播放.*(收藏|列表|歌|歌曲|音乐)|收藏里.*(音乐|歌)|列表播放|直接播放就行|你直接播放就行|直接列表播放|播放我的收藏|点开收藏.*播放|酷狗.*播放|qq音乐.*播放|网易云.*播放|spotify.*播放"
    r"|再生.*(お気に入り|リスト|音楽)|お気に入り.*再生|リスト再生|そのまま再生して|直接再生して)",
    re.IGNORECASE,
)


_LOCAL_MEDIA_CONTEXT_HINT_RE = re.compile(
    r"(酷狗|kugou|qq音乐|网易云|spotify|收藏|列表|歌|歌曲|音乐|お気に入り|リスト|音楽|再生)",
    re.IGNORECASE,
)


_TASK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|做到哪|到哪了|还在.*(执行|跑|做)|上个任务.*(怎么样|进度)|刚才.*任务.*(怎么样|进度)|修好了吗|处理到哪"
    r"|(?:这次|当前|聊天)?主链.{0,16}(效果|表现|耗时|延迟|怎么样|如何))"
)


_TASK_FAILURE_QUERY_RE = re.compile(r"(哪一步.*失败|哪个步骤.*失败|为什么失败|失败原因|哪一步没成功|哪个步骤没成功)")


_TASK_STEP_INDEX_RE = re.compile(r"第\s*(\d+)\s*步")


_CLARIFICATION_DIRECT_QUERY_RE = re.compile(
    r"^(?:"
    r"什么意思|啥意思|什么意思啊|什么意思呀|啥意思啊|啥意思呀|"
    r"这句什么意思|这句话什么意思|这个什么意思|这是什么意思|"
    r"是什么意思|意思是啥|啥意思呢|什么意思呢|"
    r"what does this mean|what does that mean|"
    r"どういう意味|何の意味"
    r")[\s\?？!！。,.]*$",
    re.IGNORECASE,
)


_TASK_CONTEXT_KEYWORDS = (
    "思路", "方案", "步骤", "实现", "代码", "函数", "模块", "接口", "逻辑", "架构", "设计",
    "分析", "整理", "计划", "从哪里开始", "下一步", "接下来", "具体", "展开", "继续写",
    "贪吃蛇", "codex", "agent_worker", "agent", "work", "tool", "翻译", "实现思路",
)


_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [

    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
    ("ANGRY", "生气|气死|烦|腹立|むかつく"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
    ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
]
