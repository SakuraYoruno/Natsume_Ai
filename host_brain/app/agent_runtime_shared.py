"""agent_runtime_shared.py — app 外壳之间的中性共享运行时 helper。

这一层先承接旧统一入口中已经被多个入口直接复用的兼容能力，
把旧调度入口 / `chat_agent.py` 对历史统一入口的硬耦合先切断，
后续再继续把真实实现逐步下沉到 tribe / observability / airlock 等稳定归属。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from functools import partial
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Callable, Sequence

from host_brain.app.sub_agent_governor import chat_lite_governor
from host_brain.app.sub_agent_protocol import ALL_AGENT_IDS
from host_brain.app.response_form_contract import derive_render_profile_for_response_form
from host_brain.bond.user_identity import resolve_person_id
from host_brain.codex_runtime.skill_ids import is_codex_runtime_skill

# ---------------------------------------------------------------------------
# 纯工具函数从 core/runtime_helpers.py 导入（向后兼容重新导出）
# ---------------------------------------------------------------------------
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
from host_brain.core.soft_fail import soft_fail  # OPT-3 Batch B：业务路径统一降级

_logger = logging.getLogger("Natsume.agent_runtime_shared")


def _resolve_post_process_executor_workers() -> int:
    raw = str(os.environ.get("HOST_POST_PROCESS_EXECUTOR_WORKERS") or "4").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 4
    return max(1, min(value, 16))


_POST_PROCESS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_resolve_post_process_executor_workers(),
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
_PHATIC_FASTPATH_BLOCKING_TOKENS = (
    "翻译",
    "代码",
    "搜索",
    "总结",
    "记住",
    "技能",
    "计划",
    "步骤",
    "文件",
    "脚本",
    "函数",
    "报错",
    "bug",
    "执行",
    "处理",
    "列出",
    "分析",
    "修复",
)
_PHATIC_FASTPATH_MAX_CHARS = 24
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
    "贪吃蛇", "codex", "codex_worker", "agent", "work", "tool", "翻译", "实现思路",
)

_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [

    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
    ("ANGRY", "生气|气死|烦|腹立|むかつく"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
    ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
]



def _normalize_retrieval_summary_for_audit(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(summary or {})
    surface_memory_trace = _surface_memory_trace_for_audit(raw)
    route_rows = list(raw.get("route_rows") or []) if isinstance(raw.get("route_rows"), list) else []
    route_count = raw.get("route_count")
    if route_count in (None, ""):
        route_count = len(route_rows)
    candidate_count = int(raw.get("candidate_count") or 0)
    topk_count = int(raw.get("topk_count") or 0)
    reason_code = str(raw.get("reason_code") or "").strip() or None
    error_text = str(raw.get("error") or "").strip() or None
    explicit_status = str(raw.get("status") or "").strip().upper()

    if explicit_status in {"OK", "SKIPPED", "DEGRADED"}:
        status = explicit_status
    elif reason_code or error_text or bool(raw.get("degraded", False)):
        status = "DEGRADED"
    elif candidate_count > 0 or int(route_count) > 0 or topk_count > 0 or route_rows:
        status = "OK"
    else:
        status = "SKIPPED"

    degraded = status == "DEGRADED" or bool(raw.get("degraded", False))

    raw["status"] = status
    raw["route_count"] = int(route_count)
    raw["candidate_count"] = candidate_count
    raw["topk_count"] = topk_count
    raw["degraded"] = degraded
    raw["reason_code"] = reason_code
    raw["error"] = error_text
    if surface_memory_trace:
        raw["surface_memory_trace"] = surface_memory_trace
    return raw


def _surface_memory_trace_for_audit(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    packet = _coerce_trace_mapping(dict(summary or {}).get("surface_memory_recall_packet"))
    trace = _coerce_trace_mapping(packet.get("trace"))
    if not trace:
        return {}

    out: dict[str, Any] = {}
    plan = _coerce_trace_mapping(trace.get("plan"))
    if plan:
        out["plan"] = {
            "reason": str(plan.get("reason") or ""),
            "task_mode": str(plan.get("task_mode") or ""),
            "recall_policy": str(plan.get("recall_policy") or ""),
            "max_facts": _safe_int(plan.get("max_facts"), default=0),
            "target_store_ids": [str(item) for item in list(plan.get("target_store_ids") or [])[:8]],
        }
    bool_trace_keys = {
        "skipped",
        "short_term_explicit_memory",
        "answer_ready",
        "coverage_failed_resolved_by_deep_review",
        "coverage_failed_resolved_by_generic_evidence_review",
    }
    for key in ("skipped", "route_count", "short_term_explicit_memory", "answer_ready", "coverage_failed_resolved_by_deep_review", "coverage_failed_resolved_by_generic_evidence_review"):
        if key in trace:
            out[key] = (
                bool(trace.get(key))
                if key in bool_trace_keys
                else _safe_int(trace.get(key), default=0)
            )

    decision = _coerce_trace_mapping(trace.get("recall_decision"))
    if decision:
        out["recall_decision"] = {
            "schema": str(decision.get("schema") or ""),
            "decision": str(decision.get("decision") or ""),
            "invoked": bool(decision.get("invoked")),
            "skip_reason": str(decision.get("skip_reason") or ""),
            "recall_policy": str(decision.get("recall_policy") or ""),
            "reason": str(decision.get("reason") or ""),
            "task_mode": str(decision.get("task_mode") or ""),
            "query_kind": str(decision.get("query_kind") or ""),
            "temporal_scope": str(decision.get("temporal_scope") or ""),
            "recall_contract_id": str(decision.get("recall_contract_id") or ""),
            "need_type": str(decision.get("need_type") or ""),
            "primary_memory_layer": str(decision.get("primary_memory_layer") or ""),
            "expected_memory_layers": [str(item) for item in list(decision.get("expected_memory_layers") or [])[:8]],
            "target_store_ids": [str(item) for item in list(decision.get("target_store_ids") or [])[:8]],
            "target_domains": [str(item) for item in list(decision.get("target_domains") or [])[:8]],
            "validation_requirements": [str(item) for item in list(decision.get("validation_requirements") or [])[:8]],
            "answer_obligations": [str(item) for item in list(decision.get("answer_obligations") or [])[:8]],
            "evidence_depth": str(decision.get("evidence_depth") or ""),
            "min_collection_items": _safe_int(decision.get("min_collection_items"), default=0),
            "time_lookback_days": decision.get("time_lookback_days"),
            "max_facts": _safe_int(decision.get("max_facts"), default=0),
            "selection_basis": [str(item) for item in list(decision.get("selection_basis") or [])[:12]],
        }

    need = _coerce_trace_mapping(trace.get("recall_need"))
    if need:
        out["recall_need"] = {
            "schema": str(need.get("schema") or ""),
            "query_kind": str(need.get("query_kind") or ""),
            "temporal_scope": str(need.get("temporal_scope") or ""),
            "recall_contract_id": str(need.get("recall_contract_id") or ""),
            "need_type": str(need.get("need_type") or ""),
            "primary_memory_layer": str(need.get("primary_memory_layer") or ""),
            "expected_memory_layers": [str(item) for item in list(need.get("expected_memory_layers") or [])[:8]],
            "target_domains": [str(item) for item in list(need.get("target_domains") or [])[:8]],
            "min_collection_items": _safe_int(need.get("min_collection_items"), default=0),
            "evidence_depth": str(need.get("evidence_depth") or ""),
        }
    coverage = _coerce_trace_mapping(trace.get("recall_coverage"))
    if coverage:
        dimensions = _coerce_trace_mapping(coverage.get("dimensions"))
        out["recall_coverage"] = {
            "score": _safe_float(coverage.get("score"), default=0.0),
            "passed": bool(coverage.get("passed")),
            "missing": [str(item) for item in list(coverage.get("missing") or [])[:8]],
            "dimensions": {str(key): _safe_float(value, default=0.0) for key, value in dimensions.items()},
            "required_count": _safe_int(coverage.get("required_count"), default=0),
            "passed_required_count": _safe_int(coverage.get("passed_required_count"), default=0),
            "selected_item_count": _safe_int(coverage.get("selected_item_count"), default=0),
            "min_collection_items": _safe_int(coverage.get("min_collection_items"), default=0),
            "evidence_ref_count": _safe_int(coverage.get("evidence_ref_count"), default=0),
            "source_ref_count": _safe_int(coverage.get("source_ref_count"), default=0),
        }
        detail = _coerce_trace_mapping(coverage.get("detail_evidence"))
        if detail:
            out["recall_coverage"]["detail_evidence"] = {
                "required": bool(detail.get("required")),
                "ready_for_deep_review": bool(detail.get("ready_for_deep_review")),
                "basis": str(detail.get("basis") or ""),
                "body_item_count": _safe_int(detail.get("body_item_count"), default=0),
                "anchor_item_count": _safe_int(detail.get("anchor_item_count"), default=0),
                "structured_item_count": _safe_int(detail.get("structured_item_count"), default=0),
            }
        out["coverage_score"] = out["recall_coverage"]["score"]

    readiness = _coerce_trace_mapping(trace.get("recall_answer_readiness"))
    if readiness:
        detail = _coerce_trace_mapping(readiness.get("detail_evidence"))
        out["recall_answer_readiness"] = {
            "schema": str(readiness.get("schema") or ""),
            "status": str(readiness.get("status") or ""),
            "ready_to_answer": bool(readiness.get("ready_to_answer")),
            "response_mode": str(readiness.get("response_mode") or ""),
            "surface_action": str(readiness.get("surface_action") or ""),
            "reasons": [str(item) for item in list(readiness.get("reasons") or [])[:8]],
            "need_type": str(readiness.get("need_type") or ""),
            "recall_contract_id": str(readiness.get("recall_contract_id") or ""),
            "query_kind": str(readiness.get("query_kind") or ""),
            "temporal_scope": str(readiness.get("temporal_scope") or ""),
            "evidence_depth": str(readiness.get("evidence_depth") or ""),
            "coverage_score": _safe_float(readiness.get("coverage_score"), default=0.0),
            "coverage_passed": bool(readiness.get("coverage_passed")),
            "validation_passed": bool(readiness.get("validation_passed")),
            "validation_failed": bool(readiness.get("validation_failed")),
            "coverage_failed": bool(readiness.get("coverage_failed")),
            "missing": [str(item) for item in list(readiness.get("missing") or [])[:8]],
            "selected_fact_count": _safe_int(readiness.get("selected_fact_count"), default=0),
            "selected_user_fact_count": _safe_int(readiness.get("selected_user_fact_count"), default=0),
            "selected_bond_count": _safe_int(readiness.get("selected_bond_count"), default=0),
            "selected_item_count": _safe_int(readiness.get("selected_item_count"), default=0),
            "min_collection_items": _safe_int(readiness.get("min_collection_items"), default=0),
            "required_count": _safe_int(readiness.get("required_count"), default=0),
            "passed_required_count": _safe_int(readiness.get("passed_required_count"), default=0),
        }
        if detail:
            out["recall_answer_readiness"]["detail_evidence"] = {
                "required": bool(detail.get("required")),
                "ready_for_deep_review": bool(detail.get("ready_for_deep_review")),
                "basis": str(detail.get("basis") or ""),
                "body_item_count": _safe_int(detail.get("body_item_count"), default=0),
                "anchor_item_count": _safe_int(detail.get("anchor_item_count"), default=0),
                "structured_item_count": _safe_int(detail.get("structured_item_count"), default=0),
            }

    deep = _coerce_trace_mapping(trace.get("lightnovel_deep_review"))
    if deep:
        selected = _coerce_trace_mapping(deep.get("selected"))
        safe_deep = {
            "status": str(deep.get("status") or ""),
            "reason_code": str(deep.get("reason_code") or ""),
            "item_count": _safe_int(deep.get("item_count"), default=0),
            "latency_ms": _safe_int(deep.get("latency_ms"), default=0),
            "cached": bool(deep.get("cached")),
            "source": str(deep.get("source") or ""),
            "recommendation_signature": str(deep.get("recommendation_signature") or ""),
            "source_episode_id": str(deep.get("source_episode_id") or ""),
            "persisted_episode_id": str(deep.get("persisted_episode_id") or ""),
            "persist_status": str(deep.get("persist_status") or ""),
            "evidence_ref_count": _safe_int(deep.get("evidence_ref_count"), default=0),
        }
        if selected:
            safe_deep["selected"] = {
                "index": _safe_int(selected.get("index"), default=0),
                "title": str(selected.get("title") or "")[:120],
            }
        out["lightnovel_deep_review"] = safe_deep

    generic = _coerce_trace_mapping(trace.get("generic_evidence_review"))
    if generic:
        out["generic_evidence_review"] = {
            "status": str(generic.get("status") or ""),
            "reason_code": str(generic.get("reason_code") or ""),
            "item_count": _safe_int(generic.get("item_count"), default=0),
            "latency_ms": _safe_int(generic.get("latency_ms"), default=0),
            "cached": bool(generic.get("cached")),
            "source": str(generic.get("source") or ""),
            "persisted_episode_id": str(generic.get("persisted_episode_id") or ""),
            "persist_status": str(generic.get("persist_status") or ""),
            "evidence_ref_count": _safe_int(generic.get("evidence_ref_count"), default=0),
        }
    return out


def _surface_memory_debug_trace_for_audit(
    *,
    retrieval_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: Sequence[Mapping[str, Any]] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
    delegated_agents: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    summary = dict(retrieval_summary or {})
    packet = _coerce_trace_mapping(summary.get("surface_memory_recall_packet"))
    trace_stage = _coerce_trace_mapping(trace_stage_meta)
    out: dict[str, Any] = {}

    history = _coerce_trace_mapping(trace_stage.get("conversation_history"))
    if history:
        out["history_window"] = {
            "turns_injected": _safe_int(history.get("turns_injected"), default=0),
            "fetched_turns": _safe_int(history.get("fetched_turns"), default=0),
            "older_turn_count": _safe_int(history.get("older_turn_count"), default=0),
            "summary_present": bool(history.get("summary_present")),
            "oldest_ts_ms": history.get("oldest_ts_ms"),
            "newest_ts_ms": history.get("newest_ts_ms"),
        }

    if packet:
        rows = [
            _surface_memory_debug_row(row)
            for row in list(packet.get("rows") or [])[:8]
            if isinstance(row, Mapping)
        ]
        episode_rows = [row for row in rows if row.get("store_id") == "life_episode"]
        out["episode_recall_packet"] = {
            "candidate_count": _safe_int(packet.get("candidate_count"), default=0),
            "route_ids": [str(item) for item in list(packet.get("route_ids") or [])[:8]],
            "source_refs": [str(item) for item in list(packet.get("source_refs") or [])[:8]],
            "evidence_refs": [str(item) for item in list(packet.get("evidence_refs") or [])[:8]],
            "fact_count": len(list(packet.get("facts") or [])),
            "life_episode_row_count": len(episode_rows),
            "rows": rows,
        }

    evidence_items = [
        item
        for item in list(retrieval_evidence_items or [])
        if isinstance(item, Mapping)
    ]
    if evidence_items:
        out["evidence_pack"] = {
            "item_count": len(evidence_items),
            "items": [
                {
                    "store_id": str(item.get("store_id") or ""),
                    "evidence_ref": str(item.get("evidence_ref") or ""),
                    "source_ref": str(item.get("source_ref") or ""),
                    "score": item.get("score"),
                    "snippet": str(item.get("snippet") or "")[:220],
                }
                for item in evidence_items[:8]
            ],
        }
    elif packet:
        evidence_refs = [str(item) for item in list(packet.get("evidence_refs") or [])[:8] if str(item or "").strip()]
        source_refs = [str(item) for item in list(packet.get("source_refs") or [])[:8] if str(item or "").strip()]
        if evidence_refs or source_refs:
            out["evidence_pack"] = {
                "item_count": len(evidence_refs or source_refs),
                "items": [
                    {"evidence_ref": ref}
                    for ref in evidence_refs[:8]
                ] or [
                    {"source_ref": ref}
                    for ref in source_refs[:8]
                ],
            }

    sub_agent_rows: list[dict[str, Any]] = []
    for item in list(delegated_agents or [])[:8]:
        if not isinstance(item, Mapping):
            continue
        audit_refs = _coerce_trace_mapping(item.get("audit_refs"))
        agent_context = _coerce_trace_mapping(audit_refs.get("agent_context"))
        agent_context_trace = _coerce_trace_mapping(agent_context.get("trace"))
        sub_agent_rows.append(
            {
                "agent_id": str(item.get("agent_id") or ""),
                "success": bool(item.get("success")),
                "latency_ms": _safe_int(item.get("latency_ms"), default=0),
                "context_profile": str(agent_context_trace.get("context_profile") or ""),
                "conversation_turns": _safe_int(agent_context.get("conversation_turns"), default=0),
                "memory_fact_count": _safe_int(agent_context.get("memory_fact_count"), default=0),
                "evidence_item_count": _safe_int(agent_context.get("evidence_item_count"), default=0),
            }
        )
    if sub_agent_rows:
        out["sub_agent_envelope"] = {
            "agent_count": len(sub_agent_rows),
            "agents": sub_agent_rows,
        }
    return {
        key: value
        for key, value in out.items()
        if value not in (None, "", [], {})
    }


def _surface_memory_debug_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "store_id": str(row.get("store_id") or ""),
        "source_ref": str(row.get("source_ref") or ""),
        "evidence_ref": str(row.get("evidence_ref") or ""),
        "score": row.get("score"),
        "fact": str(row.get("fact") or "")[:260],
    }


def _safe_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return round(float(default), 6)


# _coerce_trace_mapping / _coerce_trace_score / _coerce_trace_bool /
# _summarize_motive_state_for_trace / _summarize_reply_plan_for_trace /
# _summarize_emotion_context_for_trace / _summarize_persona_resident_memory_for_trace /
# _normalize_trace_stage_meta / _resolve_envelope_confidence /
# _build_trace_output_digest / _detect_trace_fallback_used
# 已迁移到 host_brain/core/runtime_helpers.py，此处通过顶部 import 重新导出。


def _build_trace_agent_envelope(
    *,
    request_id: str,
    node_id: str,
    parent_node_id: str | None,
    node_type: str,
    status: str,
    reason_code: str | None,
    output_data: Any,
    fallback_used: bool,
    degrade_code: str | None,
    ts_ms: int,
) -> dict[str, Any]:
    status_text = str(status or "OK").strip().upper() or "OK"
    reason_code_text = str(reason_code or "").strip() or None
    degrade_code_text = str(degrade_code or "").strip() or None
    reason_text = reason_code_text or ""
    output_digest = _build_trace_output_digest(output_data)

    fallback_marked = bool(fallback_used) or _detect_trace_fallback_used(
        reason_code=reason_code_text,
        degrade_code=degrade_code_text,
        output_data=output_data,
    )
    if degrade_code_text is None and status_text in {"DEGRADED", "FAILED", "ERROR"}:
        degrade_code_text = reason_code_text

    try:
        from host_brain.app.schemas.agent_envelope import AgentEnvelope  # noqa: PLC0415

        envelope_model = AgentEnvelope(
            agent_id=node_id,
            agent_family=str(node_type or "system").strip().lower() or "system",
            status=status_text,
            confidence=_resolve_envelope_confidence(status_text),
            reason=reason_text,
            reason_code=reason_code_text,
            request_id=str(request_id or "").strip(),
            node_id=node_id,
            parent_node_id=(str(parent_node_id).strip() if parent_node_id else None),
            output_digest=output_digest,
            fallback_used=fallback_marked,
            degrade_code=degrade_code_text,
            ts_ms=int(ts_ms or 0),
        )
        envelope = envelope_model.as_trace_dict()
        envelope["request_id"] = envelope_model.request_id
        envelope["reason"] = envelope_model.reason
        return envelope
    except Exception:
        return {
            "agent_id": node_id,
            "agent_family": str(node_type or "system").strip().lower() or "system",
            "node_id": node_id,
            "parent_node_id": (str(parent_node_id).strip() if parent_node_id else None),
            "request_id": str(request_id or "").strip(),
            "status": status_text,
            "confidence": _resolve_envelope_confidence(status_text),
            "reason": reason_text,
            "reason_code": reason_code_text,
            "fallback_used": fallback_marked,
            "degrade_code": degrade_code_text,
            "output_digest": output_digest,
            "schema_version": "1.0",
            "ts_ms": int(ts_ms or 0),
        }



def _build_trace_stage_node(
    *,
    node_id: str,
    node_type: str,
    display_name: str,
    ts_ms: int,
    status: str = "OK",
    side: str = "host",
    request_id: str = "",
    parent_node_id: str | None = None,
    reason_code: str | None = None,
    fallback_used: bool = False,
    degrade_code: str | None = None,
    latency_ms: int | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    input_data: Any = None,
    output_data: Any = None,
    error: str | None = None,
    invoked: bool | None = None,
    skipped_reason: str | None = None,
    affected_output: bool | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "OK").strip().upper() or "OK"
    node: dict[str, Any] = {
        "node_id": node_id,
        "node_type": node_type,
        "display_name": display_name,
        "side": side,
        "status": status,
        "ts_ms": ts_ms,
        "invoked": bool(invoked) if invoked is not None else normalized_status != "SKIPPED",
        "affected_output": bool(affected_output) if affected_output is not None else normalized_status not in {"SKIPPED", "FAILED"},
    }
    if skipped_reason:
        node["skipped_reason"] = skipped_reason
    elif normalized_status == "SKIPPED":
        node["skipped_reason"] = reason_code or "not_invoked"
    if source:
        node["source"] = source
    if reason_code:
        node["reason_code"] = reason_code
    if latency_ms is not None:
        node["latency_ms"] = latency_ms
    if provider:
        node["provider"] = provider
    if model_id:
        node["model_id"] = model_id
    if input_data not in (None, {}, []):
        node["input_data"] = input_data
    if output_data not in (None, {}, []):
        node["output_data"] = output_data
    if error:
        node["error"] = error
    node["agent_envelope"] = _build_trace_agent_envelope(
        request_id=request_id,
        node_id=node_id,
        parent_node_id=parent_node_id,
        node_type=node_type,
        status=status,
        reason_code=reason_code,
        output_data=output_data,
        fallback_used=fallback_used,
        degrade_code=degrade_code,
        ts_ms=ts_ms,
    )
    return node


def _load_request_aux_llm_trace_nodes(
    *,
    ledger: Any,
    request_id: str,
) -> list[dict[str, Any]]:
    try:
        rows = list(ledger.fetch_by_request_id("llm_node_call_log", request_id) or [])
    except Exception:
        return []

    rows.sort(
        key=lambda row: (
            int(row.get("ts_ms") or 0),
            str(row.get("node_id") or "").strip(),
        )
    )

    nodes: list[dict[str, Any]] = []
    for row in rows:
        node_id = str(row.get("node_id") or "").strip()
        if not node_id or node_id == "cortex_orchestrator_llm_stream":
            continue
        provider = str(row.get("provider") or "").strip().lower() or None
        model_id = str(row.get("model_id") or "").strip() or None
        status = str(row.get("status") or "OK").strip().upper() or "OK"
        reason_code = str(row.get("reason_code") or "").strip() or None
        latency_ms = row.get("latency_ms")
        try:
            latency_value = int(latency_ms) if latency_ms is not None else None
        except (TypeError, ValueError):
            latency_value = None
        display_name = "LLM Pool"
        if node_id.startswith("llm_pool:"):
            scene_name = node_id.split(":", 1)[1].strip().upper() or "CHAT"
            display_name = f"LLM Pool ({scene_name})"
        output_digest = str(row.get("output_digest") or "").strip()
        output_data: dict[str, Any] | None = None
        if output_digest:
            try:
                attempts_payload = json.loads(output_digest)
            except Exception:
                attempts_payload = None
            output_data = {"attempts": attempts_payload} if isinstance(attempts_payload, list) else {"output_digest": output_digest[:400]}
        input_digest = str(row.get("input_digest") or "").strip()
        input_data = {"input_digest": input_digest[:300]} if input_digest else None
        nodes.append(
            _build_trace_stage_node(
                node_id=node_id,
                node_type="llm",
                display_name=display_name,
                ts_ms=int(row.get("ts_ms") or 0),
                status=status,
                reason_code=reason_code,
                latency_ms=latency_value,
                provider=provider,
                model_id=model_id,
                input_data=input_data,
                output_data=output_data,
            )
        )
    return nodes


def _trace_node_sort_key(node: Mapping[str, Any]) -> tuple[int, int, str]:
    node_id = str(node.get("node_id") or "").strip()
    ts_ms = int(node.get("ts_ms") or 0)
    priority = {
        "airlock_ingress": 0,
        "governor": 1,
        "emotion_context": 10,
        "persona_resident_memory": 11,
        "motive_state": 12,
        "conversation_interpretation": 13,
        "topic_ledger": 14,
        "tactic_ledger": 15,
        "affect_state": 16,
        "relationship_state": 17,
        "drive_state": 18,
        "reply_plan": 19,
        "cortex_orchestrator": 20,
        "query_planner": 30,
        "agentic_rag": 31,
        "recall_fusion": 32,
        "executive_router": 40,
        "chat_agent": 50,
        "assistant_followup_surface_render": 51,
        "image_social_surface_render": 51,
        "image_social_surface_repair_render": 51,
        "identity_surface_render": 51,
        "phatic_surface_render": 51,
        "surface_render": 51,
        "surface_renderer": 51,
        "prompt_builder": 60,
        "llm_stream": 70,
        "pii_filter": 80,
        "airlock_egress": 90,
    }.get(node_id)
    if priority is None:
        priority = 55
    return (priority, ts_ms, node_id)



async def _persist_post_process_audit(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    narrative: str,
    emotion_context: Mapping[str, Any] | None = None,
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    reply_plan: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    multimodal_observations: Mapping[str, Any] | None = None,
    attachment_summary: str | None = None,
    reply_channel: str | None = None,
    reply_target: str | None = None,
) -> None:
    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    ledger = get_ledger()
    _now = int(time.time() * 1000)

    if gov_decision:
        ledger.log_governor(
            {
                "request_id": request_id,
                "intent_id": request_id,
                "session_id": session_id,
                "decision": gov_decision.get("decision", "APPROVE"),
                "reason_code": gov_decision.get("reason_code"),
                "rule_trace": json.dumps(gov_decision.get("rule_trace", []), ensure_ascii=False, default=str),
                "latency_ms": gov_decision.get("latency_ms", 0),
                "ts_ms": ts_ms,
            }
        )

    try:
        runtime_task_registry = get_runtime_task_registry()
        runtime_task = runtime_task_registry.get_by_request_id(request_id)
        if runtime_task is not None and str(runtime_task.status).upper() in {"RUNNING", "RECOVERING"}:
            runtime_task_status_templates = _load_runtime_texts_section(
                "runtime_task_status",
                render_target_lang=render_target_lang,
            )
            has_failure = bool(runtime_task.error or runtime_task.result_code)
            if has_failure:
                runtime_task_registry.mark_failed(
                    runtime_task.task_id,
                    detail=_mapping_text(
                    runtime_task_status_templates,
                        "failed_detail",
                    ),
                    error=str(runtime_task.error or "").strip(),
                    result_code=str(runtime_task.result_code or "").strip() or None,
                    result_text=narrative,
                )
            else:
                runtime_task_registry.mark_completed(
                    runtime_task.task_id,
                    detail=_mapping_text(
                    runtime_task_status_templates,
                        "completed_detail",
                    ),
                    result_text=narrative,
                )
    except Exception as exc:
        _logger.debug("[%s] runtime task finalize failed: %s", request_id, exc)

    ledger.log_llm_node_call(
        {
            "request_id": request_id,
            "node_id": "cortex_orchestrator_llm_stream",
            "provider": provider_name,
            "model_id": model_id,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "LLM_EMPTY_OUTPUT",
            "latency_ms": llm_latency_ms,
            "input_digest": (llm_input_digest or "")[:500] or None,
            "output_digest": (narrative or "")[:400] or None,
            "ts_ms": _now,
        }
    )

    _trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    _normalized_retrieval_summary = _normalize_retrieval_summary_for_audit(retrieval_summary)
    _evidence_items = list(retrieval_evidence_items or [])
    _rs = _normalized_retrieval_summary
    _surface_memory_debug_trace = _surface_memory_debug_trace_for_audit(
        retrieval_summary=_rs,
        retrieval_evidence_items=_evidence_items,
        trace_stage_meta=_trace_stage_meta,
        delegated_agents=delegated_agents,
    )
    if _surface_memory_debug_trace:
        _rs["surface_memory_debug_trace"] = _surface_memory_debug_trace
    if _evidence_items or _rs.get("status") == "OK":
        _query_plan = _rs.get("query_plan") or {}
        _topk_snippets = [
            {
                "evidence_ref": str(item.get("evidence_ref") or ""),
                "store_id": str(item.get("store_id") or ""),
                "snippet": str(item.get("snippet") or "")[:200],
                "score": item.get("score"),
            }
            for item in _evidence_items[:8]
        ]
        ledger.log_retrieval_event(
            {
                "request_id": request_id,
                "intent_id": request_id,
                "op_kind": "PIPELINE",
                "status": _rs.get("status", "OK"),
                "reason_code": _rs.get("reason_code"),
                "stage": "cortex_orchestrator._async_post_process",
                "payload": {
                    "candidate_count": _rs.get("candidate_count", 0),
                    "route_count": _rs.get("route_count", 0),
                    "topk_count": _rs.get("topk_count", 0),
                    "degraded": bool(_rs.get("degraded", False)),
                    "query_plan": _query_plan,
                    "topk": _topk_snippets,
                    "agentic_meta": _rs.get("agentic_meta"),
                    "embedding_model_id": _rs.get("embedding_model_id"),
                    "embedding_provider": _rs.get("embedding_provider"),
                    "effective_embedding_provider": _rs.get("effective_embedding_provider"),
                    "fallback_embedding_used": bool(_rs.get("fallback_embedding_used", False)),
                    "fallback_embedding_reason_code": _rs.get("fallback_embedding_reason_code"),
                    "surface_memory_trace": _rs.get("surface_memory_trace") or {},
                    "surface_memory_debug_trace": _rs.get("surface_memory_debug_trace") or {},
                },
                "detail": (
                    f"candidate={_rs.get('candidate_count', 0)} "
                    f"topk={len(_topk_snippets)} "
                    f"intent={_query_plan.get('intent', 'unknown')}"
                ),
                "ts_ms": _now,
            }
        )

    _retrieval_status = _normalized_retrieval_summary.get("status", "SKIPPED")
    _retrieval_count = _normalized_retrieval_summary.get("candidate_count", 0)
    _retrieval_reason = str(_normalized_retrieval_summary.get("reason_code") or "").strip() or None
    _retrieval_output = {
        "candidate_count": _retrieval_count,
        "status": _retrieval_status,
        "reason_code": _retrieval_reason,
        "error": _normalized_retrieval_summary.get("error"),
        "route_count": _normalized_retrieval_summary.get("route_count"),
        "topk_count": _normalized_retrieval_summary.get("topk_count"),
        "degraded": _normalized_retrieval_summary.get("degraded"),
        "target_stores": _normalized_retrieval_summary.get("target_stores"),
        "query_plan": _normalized_retrieval_summary.get("query_plan"),
        "agentic_meta": _normalized_retrieval_summary.get("agentic_meta"),
        "embedding_model_id": _normalized_retrieval_summary.get("embedding_model_id"),
        "embedding_provider": _normalized_retrieval_summary.get("embedding_provider"),
        "effective_embedding_provider": _normalized_retrieval_summary.get("effective_embedding_provider"),
        "fallback_embedding_used": bool(_normalized_retrieval_summary.get("fallback_embedding_used", False)),
        "fallback_embedding_reason_code": _normalized_retrieval_summary.get("fallback_embedding_reason_code"),
        "surface_memory_trace": _normalized_retrieval_summary.get("surface_memory_trace") or {},
        "surface_memory_debug_trace": _normalized_retrieval_summary.get("surface_memory_debug_trace") or {},
    }
    _query_plan_output = _coerce_trace_mapping(_normalized_retrieval_summary.get("query_plan"))
    _agentic_rag_output = _coerce_trace_mapping(_normalized_retrieval_summary.get("agentic_meta"))
    _emotion_trace = _summarize_emotion_context_for_trace(emotion_context)
    _persona_memory_trace = _summarize_persona_resident_memory_for_trace(
        bond_summary=bond_summary,
        retrieval_summary=_normalized_retrieval_summary,
        retrieval_evidence_items=_evidence_items,
    )
    _motive_trace = _summarize_motive_state_for_trace(motive_state)
    _conversation_interpretation_trace = _summarize_conversation_interpretation_for_trace(
        _trace_stage_meta.get("conversation_interpretation")
    )
    _topic_ledger_trace = _summarize_topic_ledger_for_trace(_trace_stage_meta.get("topic_ledger"))
    _tactic_ledger_trace = _summarize_tactic_ledger_for_trace(_trace_stage_meta.get("tactic_ledger"))
    _affect_state_trace = _summarize_affect_state_for_trace(_trace_stage_meta.get("affect_state"))
    _relationship_state_trace = _summarize_relationship_state_for_trace(_trace_stage_meta.get("relationship_state"))
    _drive_state_trace = _summarize_drive_state_for_trace(_trace_stage_meta.get("drive_state"))
    _reply_plan_trace = _summarize_reply_plan_for_trace(_trace_stage_meta.get("reply_plan"))
    _conversation_history_trace = _coerce_trace_mapping(_trace_stage_meta.get("conversation_history"))
    _prompt_builder_output: dict[str, Any] = {}
    if _motive_trace:
        _prompt_builder_output["motive_state"] = _motive_trace
    if _conversation_interpretation_trace:
        _prompt_builder_output["conversation_interpretation"] = _conversation_interpretation_trace
    if _topic_ledger_trace:
        _prompt_builder_output["topic_ledger"] = _topic_ledger_trace
    if _tactic_ledger_trace:
        _prompt_builder_output["tactic_ledger"] = _tactic_ledger_trace
    if _affect_state_trace:
        _prompt_builder_output["affect_state"] = _affect_state_trace
    if _relationship_state_trace:
        _prompt_builder_output["relationship_state"] = _relationship_state_trace
    if _drive_state_trace:
        _prompt_builder_output["drive_state"] = _drive_state_trace
    if _reply_plan_trace:
        _prompt_builder_output["reply_plan"] = _reply_plan_trace
    if _conversation_history_trace:
        _prompt_builder_output["conversation_history"] = _conversation_history_trace
    _prompt_builder_status = "OK" if (llm_input_digest or _prompt_builder_output) else "SKIPPED"
    _prompt_builder_node = _build_trace_stage_node(
        node_id="prompt_builder",
        node_type="system",
        display_name="Prompt Builder",
        status=_prompt_builder_status,
        ts_ms=ts_ms,
        output_data=_prompt_builder_output,
    )

    _dispatch_used = bool(_trace_stage_meta.get("executive_router_used"))
    _chat_used = bool(_trace_stage_meta.get("chat_agent_used"))
    _orchestrator_used = bool(_trace_stage_meta.get("cortex_orchestrator_used"))
    _path = str(
        _trace_stage_meta.get("dispatch_route")
        or ("delegate" if delegated_agents else "chat_direct")
    ).strip() or ("delegate" if delegated_agents else "chat_direct")
    _dispatch_reason = str(_trace_stage_meta.get("dispatch_reason") or "").strip() or None
    _dispatch_target = str(_trace_stage_meta.get("dispatch_target") or "").strip() or None
    _chat_mode = str(
        _trace_stage_meta.get("chat_mode")
        or ("sub_agent" if delegated_agents else "direct")
    ).strip() or ("sub_agent" if delegated_agents else "direct")
    _chat_sub_agent_id = str(_trace_stage_meta.get("chat_sub_agent_id") or "").strip() or None
    _dispatch_output = {
        "route": _path,
        "reason": _dispatch_reason,
        "target": _dispatch_target,
    }
    _chat_output = {
        "mode": _chat_mode,
        "sub_agent_id": _chat_sub_agent_id,
        "delegated_agent_count": len(list(delegated_agents or [])),
    }
    _cortex_output = {
        "entrypoint": "cortex_orchestrator",
        "path": _path,
        "retrieval_status": _retrieval_status,
        "retrieval_candidate_count": _retrieval_count,
        "delegated_agent_count": len(list(delegated_agents or [])),
        "orchestrator_used": _orchestrator_used,
        "executive_router_used": bool(_trace_stage_meta.get("executive_router_used")),
        "trace_stage_meta": _trace_stage_meta,
    }

    _trace_nodes_v2: list[dict[str, Any]] = [
        _build_trace_stage_node(
            node_id="airlock_ingress",
            node_type="governor",
            display_name="Airlock Ingress",
            ts_ms=ts_ms,
            status="OK",
        ),
        _build_trace_stage_node(
            node_id="governor",
            node_type="governor",
            display_name="Governor",
            ts_ms=ts_ms,
            status=gov_decision.get("decision", "APPROVE") if gov_decision else "OK",
            latency_ms=gov_decision.get("latency_ms", 0) if gov_decision else 0,
            reason_code=gov_decision.get("reason_code") if gov_decision else None,
            output_data={"rule_trace": gov_decision.get("rule_trace")} if gov_decision else None,
        ),
        _build_trace_stage_node(
            node_id="emotion_context",
            node_type="system",
            display_name="Emotion Context",
            ts_ms=ts_ms,
            status="OK" if _emotion_trace else "SKIPPED",
            output_data=_emotion_trace,
        ),
        _build_trace_stage_node(
            node_id="persona_resident_memory",
            node_type="memory",
            display_name="Persona Resident Memory",
            ts_ms=ts_ms,
            status="OK" if _persona_memory_trace else "SKIPPED",
            output_data=_persona_memory_trace,
        ),
        _build_trace_stage_node(
            node_id="motive_state",
            node_type="system",
            display_name="Motive State",
            ts_ms=ts_ms,
            status="OK" if _motive_trace else "SKIPPED",
            output_data=_motive_trace,
        ),
        _build_trace_stage_node(
            node_id="conversation_interpretation",
            node_type="system",
            display_name="Conversation Interpretation",
            ts_ms=ts_ms,
            status="OK" if _conversation_interpretation_trace else "SKIPPED",
            output_data=_conversation_interpretation_trace,
        ),
        _build_trace_stage_node(
            node_id="topic_ledger",
            node_type="system",
            display_name="Topic Ledger",
            ts_ms=ts_ms,
            status="OK" if _topic_ledger_trace else "SKIPPED",
            output_data=_topic_ledger_trace,
        ),
        _build_trace_stage_node(
            node_id="tactic_ledger",
            node_type="system",
            display_name="Tactic Ledger",
            ts_ms=ts_ms,
            status="OK" if _tactic_ledger_trace else "SKIPPED",
            output_data=_tactic_ledger_trace,
        ),
        _build_trace_stage_node(
            node_id="affect_state",
            node_type="system",
            display_name="Affect State",
            ts_ms=ts_ms,
            status="OK" if _affect_state_trace else "SKIPPED",
            output_data=_affect_state_trace,
        ),
        _build_trace_stage_node(
            node_id="relationship_state",
            node_type="system",
            display_name="Relationship State",
            ts_ms=ts_ms,
            status="OK" if _relationship_state_trace else "SKIPPED",
            output_data=_relationship_state_trace,
        ),
        _build_trace_stage_node(
            node_id="drive_state",
            node_type="system",
            display_name="Drive State",
            ts_ms=ts_ms,
            status="OK" if _drive_state_trace else "SKIPPED",
            output_data=_drive_state_trace,
        ),
        _build_trace_stage_node(
            node_id="reply_plan",
            node_type="system",
            display_name="Reply Plan",
            ts_ms=ts_ms,
            status="OK" if _reply_plan_trace else "SKIPPED",
            output_data=_reply_plan_trace,
        ),
        _build_trace_stage_node(
            node_id="cortex_orchestrator",
            node_type="system",
            display_name="Cortex Orchestrator",
            ts_ms=ts_ms,
            status="OK" if narrative.strip() else "DEGRADED",
            output_data=_cortex_output,
        ),
    ]
    if _retrieval_status != "SKIPPED" and _query_plan_output:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="query_planner",
                node_type="retrieval",
                display_name="Query Planner",
                ts_ms=ts_ms,
                status=_retrieval_status,
                reason_code=_retrieval_reason,
                output_data=_query_plan_output,
            )
        )
    if _retrieval_status != "SKIPPED" and _agentic_rag_output:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="agentic_rag",
                node_type="retrieval",
                display_name="Agentic RAG",
                ts_ms=ts_ms,
                status=_retrieval_status,
                reason_code=_retrieval_reason,
                output_data=_agentic_rag_output,
            )
        )
    if _retrieval_status != "SKIPPED" or _retrieval_count or _retrieval_output.get("degraded"):
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="recall_fusion",
                node_type="recall",
                display_name="Recall Fusion",
                ts_ms=ts_ms,
                status=_retrieval_status,
                reason_code=_retrieval_reason,
                output_data=_retrieval_output,
            )
        )
    if _dispatch_used:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="executive_router",
                node_type="agent",
                display_name="Executive Router",
                ts_ms=ts_ms,
                status="OK",
                reason_code=_dispatch_reason,
                output_data=_dispatch_output,
            )
        )
    for da in (delegated_agents or []):
        _agent_id = str(da.get("agent_id") or "?")
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id=f"sub_agent:{_agent_id}",
                node_type="agent",
                display_name=f"Sub Agent: {_agent_id}",
                ts_ms=ts_ms,
                status="OK" if da.get("success") else "FAILED",
                latency_ms=da.get("latency_ms", 0),
                error=da.get("error"),
                output_data={
                    "agent_id": _agent_id,
                    "audit_refs": da.get("audit_refs"),
                },
            )
        )
    if _chat_used:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="chat_agent",
                node_type="agent",
                display_name="Chat Agent",
                ts_ms=ts_ms,
                status="OK" if narrative.strip() else "DEGRADED",
                output_data=_chat_output,
            )
        )
    for _surface_key, _surface_name in (
        ("assistant_followup_surface_render", "Assistant Followup Surface"),
        ("image_social_surface_render", "Image Social Surface"),
        ("image_social_surface_repair_render", "Image Social Surface Repair"),
        ("identity_surface_render", "Identity Surface"),
        ("phatic_surface_render", "Phatic Surface"),
        ("surface_render", "Surface Renderer"),
        ("surface_renderer", "Surface Renderer"),
    ):
        _surface_meta = _coerce_trace_mapping(_trace_stage_meta.get(_surface_key))
        if not _surface_meta:
            continue
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id=_surface_key,
                node_type="surface",
                display_name=_surface_name,
                ts_ms=ts_ms,
                status=str(_surface_meta.get("status") or "OK").strip().upper() or "OK",
                reason_code=str(_surface_meta.get("reason_code") or "").strip() or None,
                latency_ms=int(_surface_meta.get("latency_ms") or 0) if _surface_meta.get("latency_ms") is not None else None,
                provider=str(_surface_meta.get("provider_name") or "").strip() or None,
                model_id=str(_surface_meta.get("model_id") or "").strip() or None,
                output_data=_surface_meta,
                invoked=bool(_surface_meta.get("invoked")),
                affected_output=bool(_surface_meta.get("affected_output")),
                source=str(_surface_meta.get("source") or "").strip() or None,
            )
        )
    _trace_nodes_v2.extend(
        _load_request_aux_llm_trace_nodes(
            ledger=ledger,
            request_id=request_id,
        )
    )
    _trace_nodes_v2.append(_prompt_builder_node)
    _trace_nodes_v2.append(
        _build_trace_stage_node(
            node_id="llm_stream",
            node_type="llm",
            display_name="LLM Stream",
            ts_ms=_now,
            status="OK" if narrative.strip() else "DEGRADED",
            latency_ms=llm_latency_ms,
            provider=provider_name,
            model_id=model_id,
            input_data={
                "first_token_ms": first_token_ms,
                "buffer_stream": buffer_stream,
                "input_digest": (llm_input_digest or "")[:300] or None,
            },
            output_data={
                "final_response": (narrative or "")[:800] or None,
            },
        )
    )
    _trace_nodes_v2.append(
        _build_trace_stage_node(
            node_id="airlock_egress",
            node_type="governor",
            display_name="Airlock Egress",
            ts_ms=_now,
            status="OK",
        )
    )

    trace_nodes = list(_trace_nodes_v2)
    _pii_detail: dict[str, Any] = {}
    if pii_ingress_stats:
        _pii_detail["ingress"] = pii_ingress_stats
    if pii_egress_stats:
        _pii_detail["egress"] = pii_egress_stats
    if _pii_detail:
        trace_nodes.append(
            {
                "node_id": "pii_filter",
                "node_type": "governor",
                "display_name": "PII 过滤",
                "side": "host",
                "status": "OK",
                "output_data": _pii_detail,
                "ts_ms": _now,
            }
        )
    trace_nodes = sorted(trace_nodes, key=_trace_node_sort_key)

    _prev_node_id: str | None = None
    for _node in trace_nodes:
        _node_id = str(_node.get("node_id") or "").strip()
        _node_type = str(_node.get("node_type") or "system").strip().lower() or "system"
        _node_status = str(_node.get("status") or "OK").strip().upper() or "OK"
        _node_reason = str(_node.get("reason_code") or "").strip() or None
        _node_output = _node.get("output_data")
        _node_ts = int(_node.get("ts_ms") or ts_ms)
        _node["agent_envelope"] = _build_trace_agent_envelope(
            request_id=request_id,
            node_id=_node_id,
            parent_node_id=_prev_node_id,
            node_type=_node_type,
            status=_node_status,
            reason_code=_node_reason,
            output_data=_node_output,
            fallback_used=False,
            degrade_code=None,
            ts_ms=_node_ts,
        )
        if _node_id:
            _prev_node_id = _node_id

    trace_edges = [
        {"source": trace_nodes[i]["node_id"], "target": trace_nodes[i + 1]["node_id"]}
        for i in range(len(trace_nodes) - 1)
    ]

    ledger.log_graph_run_trace(
        {
            "request_id": request_id,
            "session_id": session_id,
            "total_latency_ms": total_latency_ms,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None,
            "node_count": len(trace_nodes),
            "nodes_json": trace_nodes,
            "edges_json": trace_edges,
            "ts_ms": _now,
        }
    )



# _airlock_sanitize_output / _build_reply_preview 已迁移到 core/runtime_helpers.py


class _SubAgentGatewayAdapter:
    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        api_key: str,
        timeout_ms: int,
        base_url_override: str | None,
    ) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: PLC0415
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        from host_brain.app.prompt_builder import extract_langchain_content_text  # noqa: PLC0415

        self._chat_openai_cls = ChatOpenAI
        self._human_message = HumanMessage
        self._system_message = SystemMessage
        self._ai_message = AIMessage
        self._extract_text = extract_langchain_content_text
        self.provider_name = str(provider_name or "").strip().lower()
        self._model_id = str(model_id or "").strip() or "default"
        self._api_key = str(api_key or "").strip()
        self._base_url_override = str(base_url_override or "").strip() or None
        self._timeout_sec = max(1.0, float(timeout_ms) / 1000.0)
        self._temperature = 0.3
        try:
            from host_brain.integrations.codex_cli_provider import codex_cli_lm_available  # noqa: PLC0415

            self._use_codex_cli = self.provider_name == "codex" and not self._api_key and codex_cli_lm_available()
        except Exception:
            self._use_codex_cli = False
        self._fallback_model_ids = self._resolve_fallback_model_ids(
            provider_name=self.provider_name,
            primary_model_id=self._model_id,
        )
        self._llm = None if self._use_codex_cli else self._build_llm(model_id=self._model_id, api_key=self._api_key)

    @staticmethod
    def _resolve_fallback_model_ids(*, provider_name: str, primary_model_id: str) -> list[str]:
        if str(provider_name or "").strip().lower() != "gemini":
            return []
        raw = str(os.environ.get("HOST_SUBAGENT_FALLBACK_MODEL_IDS") or "").strip()
        if not raw:
            raw = "gemini-3.1-pro-preview,gemini-2.5-flash-lite"
        primary = str(primary_model_id or "").strip()
        rows: list[str] = []
        for chunk in raw.split(","):
            model_id = str(chunk or "").strip()
            if not model_id:
                continue
            if primary and model_id == primary:
                continue
            if model_id in rows:
                continue
            rows.append(model_id)
        return rows

    def _build_llm(self, *, model_id: str, api_key: str) -> Any:
        return self._chat_openai_cls(
            model=str(model_id or "").strip() or "default",
            api_key=str(api_key or "").strip() or None,
            base_url=self._base_url_override,
            timeout=self._timeout_sec,
            max_retries=1,
            temperature=self._temperature,
        )

    def _to_lc_messages(self, messages: list[dict[str, Any]]) -> list[Any]:
        lc_messages: list[Any] = []
        for row in list(messages or []):
            if not isinstance(row, Mapping):
                continue
            role = str(row.get("role") or "user").strip().lower()
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                lc_messages.append(self._system_message(content=content))
            elif role == "assistant":
                lc_messages.append(self._ai_message(content=content))
            else:
                lc_messages.append(self._human_message(content=content))
        return lc_messages

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if self._use_codex_cli:
            from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: PLC0415

            content = invoke_llm_text(
                provider_name=self.provider_name,
                model_id=self._model_id,
                api_key="",
                timeout_ms=int(self._timeout_sec * 1000),
                messages=messages,
                gen_params={"temperature": self._temperature},
                feature_key="subagent_gateway",
            )
            return {"content": str(content or "").strip()}
        lc_messages = self._to_lc_messages(messages)
        try:
            response = self._llm.invoke(lc_messages)
            return {"content": self._extract_text(getattr(response, "content", ""))}
        except Exception as primary_exc:
            last_exc: Exception = primary_exc
            _logger.warning(
                "sub-agent gateway invoke failed provider=%s model=%s err=%s",
                self.provider_name,
                self._model_id,
                primary_exc,
            )
            for fallback_model_id in list(self._fallback_model_ids or []):
                retry_api_key = self._api_key
                if self.provider_name == "gemini":
                    try:
                        from host_brain.app.graph_config_builder import resolve_gemini_model_route  # noqa: PLC0415

                        retry_route = resolve_gemini_model_route(
                            role="subagent_fallback",
                            preferred_api_key=None,
                            route_key=f"{fallback_model_id}:{int(time.time() * 1000)}",
                        )
                        retry_api_key = str(retry_route.get("api_key") or retry_api_key or "").strip() or retry_api_key
                    except Exception:
                        pass
                try:
                    retry_llm = self._build_llm(model_id=fallback_model_id, api_key=retry_api_key)
                    response = retry_llm.invoke(lc_messages)
                    return {"content": self._extract_text(getattr(response, "content", ""))}
                except Exception as fallback_exc:
                    last_exc = fallback_exc
                    _logger.warning(
                        "sub-agent gateway fallback failed provider=%s model=%s err=%s",
                        self.provider_name,
                        fallback_model_id,
                        fallback_exc,
                    )
            raise last_exc



def _build_subagent_gateway_from_runtime(
    llm_runtime: Mapping[str, Any],
    adapter_class: type[Any] | None = None,
) -> Any | None:
    try:
        if not bool(llm_runtime.get("available")):
            return None
        provider_name = str(llm_runtime.get("provider_name") or "").strip()
        model_id = str(llm_runtime.get("model_id") or "").strip()
        api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        timeout_ms = int(llm_runtime.get("timeout_ms") or 30_000)
        base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None
        if not provider_name or not model_id:
            return None
        _adapter_class = adapter_class or _SubAgentGatewayAdapter
        return _adapter_class(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            base_url_override=base_url_override,
        )
    except Exception as exc:
        _logger.debug("sub-agent gateway adapter unavailable: %s", exc)
        return None



def _detect_recent_turn_query_role(
    user_message: str,
    user_query_re: Any | None = None,
    assistant_query_re: Any | None = None,
    generic_query_re: Any | None = None,
) -> str | None:
    normalized = str(user_message or "").strip()
    if not normalized:
        return None
    _user_query_re = user_query_re or _RECENT_TURN_USER_QUERY_RE
    _assistant_query_re = assistant_query_re or _RECENT_TURN_ASSISTANT_QUERY_RE
    _generic_query_re = generic_query_re or _RECENT_TURN_GENERIC_QUERY_RE
    if _user_query_re.search(normalized):
        return "user"
    if _assistant_query_re.search(normalized):
        return "assistant"
    if _generic_query_re.search(normalized):
        return "both"
    return None


def _is_short_assistant_reply_followup(user_message: str) -> bool:
    try:
        from host_brain.app.context.interaction_thread import detect_assistant_reply_followup_kind  # noqa: PLC0415

        return bool(detect_assistant_reply_followup_kind(str(user_message or "").strip()))
    except Exception:
        return False



def _load_continuity_turns(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    render_target_lang: str,
    fetch_limit: int = 24,
) -> list[dict[str, str]]:
    try:
        from host_brain.app.dependencies import get_conversation_history  # noqa: PLC0415

        window = get_conversation_history().fetch_window_for_messages(
            session_id=session_id,
            max_turns=fetch_limit,
            exclude_request_id=request_id,
            render_target_lang="ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh",
        )
        return [{"role": turn.role, "content": turn.content} for turn in window.recent_turns]
    except Exception as exc:
        _logger.debug("[%s] continuity lookup failed: %s", request_id, exc)
        return []


def _extract_contextual_memory_value(turns: Sequence[Mapping[str, Any]]) -> str:
    fallback_quoted = ""
    for turn in reversed(list(turns or [])):
        row = dict(turn or {})
        role = str(row.get("role") or "").strip().lower()
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        if "别名" in content:
            continue
        if "偏好" in content and ("系统测试" in content or "测试报告" in content or "可复现" in content):
            continue
        if "报告" in content and ("可复现" in content or "主观" in content or "体验" in content):
            continue
        if _CONTEXTUAL_MEMORY_RECALL_RE.search(content) or _CONTEXTUAL_MEMORY_TRANSFORM_RE.search(content):
            continue
        quoted = _CONTEXTUAL_MEMORY_QUOTED_VALUE_RE.search(content)
        if quoted:
            candidate = str(quoted.group(1) or "").strip()
            if 1 <= len(candidate) <= 32:
                if any(marker in content for marker in ("口令", "暗号", "关键词", "锚点", "记住")):
                    return candidate
                fallback_quoted = fallback_quoted or candidate
        if role and role != "user":
            continue
        if not _CONTEXTUAL_MEMORY_WRITE_RE.search(content):
            continue
        tail = _CONTEXTUAL_MEMORY_WRITE_RE.split(content, maxsplit=1)[-1]
        tail = tail.strip(" \t\r\n，,。.!！?？:：；;、")
        tail = re.sub(r"(只需要|请|不用|不要).*$", "", tail).strip(" \t\r\n，,。.!！?？:：；;、")
        if 1 <= len(tail) <= 32:
            return tail
    return fallback_quoted


def _extract_contextual_alias_target(user_message: str, turns: Sequence[Mapping[str, Any]]) -> str:
    if "小橙灯" not in str(user_message or ""):
        return ""
    for turn in reversed(list(turns or [])):
        content = str(dict(turn or {}).get("content") or "").strip()
        if "小橙灯" in content and "橙灯码" in content:
            return "橙灯码"
    return ""


def _short_contextual_memory_sentence(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    for suffix in ("还亮着。", "在这里。", "收到。"):
        if len(token + suffix) <= 12:
            return token + suffix
    return token


def _maybe_build_contextual_memory_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _ = person_id, render_target_lang
    normalized = str(user_message or "").strip()
    if not normalized:
        return None
    strict_answer = any(marker in normalized for marker in ("只回答", "只答", "本身"))
    wants_transform = bool(_CONTEXTUAL_MEMORY_TRANSFORM_RE.search(normalized))
    wants_recall = bool(_CONTEXTUAL_MEMORY_RECALL_RE.search(normalized))
    if not wants_transform and not wants_recall:
        return None
    if wants_transform or not strict_answer:
        return None
    _load_turns = load_continuity_turns or _load_continuity_turns
    turns = _load_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=40,
    )
    value = _extract_contextual_memory_value(turns)
    alias_target = _extract_contextual_alias_target(normalized, turns)
    if alias_target:
        value = alias_target
    if not value:
        return None
    if wants_transform:
        return _short_contextual_memory_sentence(value)
    return value


def _extract_contextual_style_preferences(turns: Sequence[Mapping[str, Any]]) -> list[str]:
    joined = "\n".join(
        str(dict(turn or {}).get("content") or "").strip()
        for turn in list(turns or [])[-40:]
        if str(dict(turn or {}).get("role") or "").strip().lower() == "user"
    )
    preferences: list[str] = []
    candidates = (
        ("偏青色", ("偏青色", "偏青", "青色")),
        ("偏冷色", ("偏冷色", "冷色")),
        ("少废话", ("少废话", "少废话", "简短", "别废话")),
        ("保留一点温度", ("有一点温度", "带点温度", "温度")),
        ("别太客服腔", ("客服腔", "像客服", "太客服")),
    )
    for label, markers in candidates:
        if any(marker in joined for marker in markers) and label not in preferences:
            preferences.append(label)
    return preferences


def _extract_contextual_report_preference(turns: Sequence[Mapping[str, Any]]) -> str:
    joined = "\n".join(
        str(dict(turn or {}).get("content") or "").strip()
        for turn in list(turns or [])[-40:]
        if str(dict(turn or {}).get("role") or "").strip().lower() == "user"
    )
    has_repro = "可复现" in joined or "复现" in joined
    has_report = "报告" in joined
    has_subjective = "主观" in joined or "体验备注" in joined or "体感" in joined
    if has_repro and has_subjective:
        return "可复现报告为主，同时保留一点主观体验备注。"
    if has_repro and has_report:
        return "更相信可复现报告，不只看体感。"
    return ""


def _contextual_session_summary_sentence(turns: Sequence[Mapping[str, Any]]) -> str:
    preferences = _extract_contextual_style_preferences(turns)
    style = "冷光余温，偏青色、有余温" if "偏青色" in preferences else "冷光余温，偏冷色、少废话、有温度"
    anchor = _extract_contextual_memory_value(turns) or "橙灯码"
    alias = "，小橙灯也指它" if any("小橙灯" in str(dict(turn or {}).get("content") or "") for turn in turns or []) else ""
    report = _extract_contextual_report_preference(turns) or "可复现报告为主，保留主观体验备注。"
    return f"表达风格：{style}；锚点：{anchor}{alias}；测试报告：{report}"


def _maybe_build_contextual_session_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _ = request_id, session_id, person_id, user_message, render_target_lang, load_continuity_turns
    return None


def _maybe_build_contextual_style_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _ = request_id, session_id, person_id, user_message, render_target_lang, load_continuity_turns
    return None



def _maybe_build_recent_turn_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    detect_recent_turn_query_role: Callable[[str], str | None] | None = None,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    if not _parse_bool_env("HOST_RECENT_TURN_DIRECT_ANSWER_ENABLED", default=True):
        return None
    _detect_query_role = detect_recent_turn_query_role or _detect_recent_turn_query_role
    _load_turns = load_continuity_turns or _load_continuity_turns
    query_role = _detect_query_role(user_message)
    if query_role is None:
        return None

    turns = _load_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=24,
    )

    if not turns:
        return None
    current_query = str(user_message or "").strip()
    filtered_turns: list[dict[str, str]] = []
    for turn in turns:
        role = str(turn.get("role") or "").strip().lower()
        content = str(turn.get("content") or "").strip()
        if not role or not content:
            continue
        if role == "user" and content == current_query:
            continue
        filtered_turns.append({"role": role, "content": content})
    turns = filtered_turns
    if not turns:
        if query_role == "both":
            return "我这边还没拿到上一轮内容。"
        return None
    low_info_only_recent_turn = False
    if query_role == "both":
        semantic_turns: list[dict[str, str]] = []
        for turn in turns:
            role = str(turn.get("role") or "").strip().lower()
            content = str(turn.get("content") or "").strip()
            if role == "user" and (
                _CONTINUATION_QUERY_RE.match(content)
                or _GREETING_FASTPATH_RE.match(content)
                or _PRESENCE_FASTPATH_RE.match(content)
                or _ACK_FASTPATH_RE.match(content)
            ):
                continue
            if role == "assistant" and content in {"接着来。", "在。", "我这里只看到文字。"}:
                continue
            semantic_turns.append(turn)
        if semantic_turns:
            turns = semantic_turns
        else:
            low_info_only_recent_turn = True

    def _finalize_recent_turn_text(candidate: str, *, fallback: str) -> str:
        normalized = str(candidate or "").strip()
        if not normalized:
            return fallback
        if any(
            token in normalized
            for token in ("已确认事实", "请直接告诉用户", "请直接回答", "不要再说看不到", "不要再说没看到")
        ):
            return fallback
        return normalized

    latest_user = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "user"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )
    latest_user = _build_reply_preview(latest_user, limit=160)
    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )
    latest_assistant = _build_reply_preview(latest_assistant, limit=220)

    templates = _load_runtime_texts_section(
        "recent_turn_answer",
        render_target_lang=render_target_lang,
    )
    if query_role == "both" and low_info_only_recent_turn:
        fallback = "刚才只是短接了一句，还没有新的实质内容。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "low_info_only_template",
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "user" and latest_user:
        fallback = f"你上一句说的是：「{latest_user}」。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "user_template",
                latest_user=latest_user,
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "assistant" and latest_assistant:
        fallback = f"我上一句回的是：「{latest_assistant}」。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "assistant_template",
                latest_assistant=latest_assistant,
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "both":
        parts: list[str] = []
        if latest_user:
            parts.append(
                _mapping_text(
                    templates,
                    "both_user_item_template",
                    latest_user=latest_user,
                    default=f"你刚才说：「{latest_user}」",
                )
            )
        if latest_assistant:
            parts.append(
                _mapping_text(
                    templates,
                    "both_assistant_item_template",
                    latest_assistant=latest_assistant,
                    default=f"我回：「{latest_assistant}」",
                )
            )
        if parts:
            fallback = "；".join(parts) + "。"
            return _finalize_recent_turn_text(
                _mapping_text(
                    templates,
                    "both_wrapper_template",
                    facts="；".join(parts),
                    default=fallback,
                ),
                fallback=fallback,
            )
    return None



def _resolve_persona_session_emotion(emotion_context: Mapping[str, Any]) -> tuple[float, float]:
    """为人格 session_end 提取可用的 valence / arousal。"""
    payload = dict(emotion_context or {})
    vad_payload = payload.get("vad") if isinstance(payload.get("vad"), Mapping) else {}

    raw_valence = payload.get("valence")
    if raw_valence is None:
        raw_valence = vad_payload.get("valence")

    raw_arousal = payload.get("arousal")
    if raw_arousal is None:
        raw_arousal = vad_payload.get("arousal")

    def _coerce(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    if raw_valence is not None or raw_arousal is not None:
        valence = max(-1.0, min(1.0, _coerce(raw_valence, 0.0)))
        arousal = max(0.0, min(1.0, _coerce(raw_arousal, 0.3)))
        return valence, arousal

    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
    intensity = max(0.0, min(1.0, _coerce(payload.get("emotion_intensity"), 0.5)))
    fallback_vad = {
        "HAPPY": (0.7, 0.65),
        "LOVING": (0.8, 0.45),
        "EXCITED": (0.75, 0.85),
        "SURPRISED": (0.2, 0.8),
        "SAD": (-0.6, 0.25),
        "ANGRY": (-0.7, 0.8),
        "NEUTRAL": (0.0, 0.3),
    }
    base_valence, base_arousal = fallback_vad.get(emotion_tag, (0.0, 0.3))
    valence = max(-1.0, min(1.0, float(base_valence) * max(0.4, intensity)))
    arousal = max(0.0, min(1.0, max(float(base_arousal), intensity)))
    return valence, arousal


async def _run_persona_session_end_hooks(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
) -> None:
    """统一触发人格 session_end/conversation_end 收口。"""
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        persona_user_id = get_canonical_bond_user_id()
        valence, arousal = _resolve_persona_session_emotion(emotion_context)
        pending_topics = []
        topic_preview = _build_reply_preview(user_message, limit=64)
        if topic_preview:
            pending_topics = [topic_preview]
        await asyncio.to_thread(
            get_limbic_agent().on_session_end,
            person_id=persona_user_id,
            valence=valence,
            arousal=arousal,
            pending_topics=pending_topics,
        )
    except Exception as exc:
        _logger.debug("[%s] persona inner_world session_end failed: %s", request_id, exc)

    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        await asyncio.to_thread(
            get_limbic_agent().on_conversation_ended,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
        )
    except Exception as exc:
        _logger.debug("[%s] persona body_sense conversation_end failed: %s", request_id, exc)


def _write_surface_main_chain_memory_sync(
    *,
    episodic_store: Any,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    emotion_context: Mapping[str, Any],
    ts_ms: int,
    reply_plan: Mapping[str, Any] | None,
) -> dict[str, Any]:
    from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415
    from host_brain.persona.surface_memory_recall import write_surface_turn_memory  # noqa: PLC0415

    payload_emotion = dict(emotion_context or {})
    resolved_person_id = resolve_bond_user_id(person_id or None, session_id=session_id or None)
    memory_emotion_tag = str(
        payload_emotion.get("surface_user_emotion_tag")
        or payload_emotion.get("user_emotion_tag")
        or payload_emotion.get("emotion_tag")
        or "NEUTRAL"
    ).strip().upper() or "NEUTRAL"
    valence_context = {**payload_emotion, "emotion_tag": memory_emotion_tag}
    valence, arousal = _resolve_persona_session_emotion(valence_context)
    plan = dict(reply_plan or {}) if isinstance(reply_plan, Mapping) else {}
    intent_id = str(
        plan.get("intent_id")
        or plan.get("task_mode")
        or plan.get("primary_topic")
        or plan.get("response_move")
        or request_id
    ).strip() or request_id
    result = write_surface_turn_memory(
        episodic_store=episodic_store,
        request_id=request_id,
        session_id=session_id,
        person_id=resolved_person_id,
        user_message=user_message,
        assistant_reply=assistant_reply,
        ts_ms=ts_ms,
        intent_id=intent_id,
        evidence_refs=[f"surface://reply/{request_id}"],
        emotion_tag=memory_emotion_tag,
        emotion_valence=valence,
        emotion_arousal=arousal,
    )
    payload = result.as_dict() if hasattr(result, "as_dict") else dict(result or {})
    payload["source"] = "surface_main_chain"
    payload["intent_id"] = intent_id
    payload["person_id"] = resolved_person_id
    return payload


def _run_surface_persona_session_end_sync(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
) -> dict[str, Any]:
    from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

    payload_emotion = dict(emotion_context or {})
    user_emotion_tag = str(
        payload_emotion.get("surface_user_emotion_tag")
        or payload_emotion.get("user_emotion_tag")
        or payload_emotion.get("emotion_tag")
        or "NEUTRAL"
    ).strip().upper() or "NEUTRAL"
    valence, arousal = _resolve_persona_session_emotion({**payload_emotion, "emotion_tag": user_emotion_tag})
    topic_preview = _build_reply_preview(user_message, limit=64)
    _ = person_id
    persona_user_id = get_canonical_bond_user_id()
    pending_topics = [topic_preview] if topic_preview else []
    state = get_limbic_agent().on_session_end(
        person_id=persona_user_id,
        valence=valence,
        arousal=arousal,
        pending_topics=pending_topics,
    )
    return {
        "status": "OK",
        "request_id": request_id,
        "session_id": session_id,
        "person_id": persona_user_id,
        "emotion_tag": user_emotion_tag,
        "valence": round(float(valence), 4),
        "arousal": round(float(arousal), 4),
        "pending_topics": pending_topics,
        "inner_world": {
            "continuity_score": dict(state or {}).get("continuity_score"),
            "anticipation_topics": list(dict(state or {}).get("anticipation_topics") or [])[:5],
            "inner_stream_size": dict(state or {}).get("inner_stream_size"),
        },
    }



def _infer_emotion_tag_for_tts(
    narrative: str,
    current_tag: str,
    emotion_infer_keywords: list[tuple[str, str]] | None = None,
) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案推断 emotion_tag。"""
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    _emotion_infer_keywords = emotion_infer_keywords or _EMOTION_INFER_KEYWORDS
    for tag, pattern in _emotion_infer_keywords:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


# _parse_bool_env / _sse / _airlock_sanitize_input 已迁移到 core/runtime_helpers.py


def _looks_like_active_task_context(turns: list[dict[str, str]]) -> bool:
    if not turns:
        return False
    recent_turns = list(turns[-8:])
    joined = "\n".join(str(turn.get("content") or "") for turn in recent_turns).lower()
    if any(keyword.lower() in joined for keyword in _TASK_CONTEXT_KEYWORDS):
        return True
    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(recent_turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
        ),
        "",
    )
    if len(latest_assistant) >= 40 and any(
        phrase in latest_assistant
        for phrase in ("从哪里开始", "具体", "下一步", "准备好", "继续", "展开", "思路", "方案")
    ):
        return True
    return False


def _extract_delegate_visible_text(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    with soft_fail("agent_runtime_shared.normalize_narrative_payload", fallback=normalized) as _box:
        from host_brain.app.intelligent_runtime import _normalize_narrative_payload  # noqa: PLC0415

        _box[0] = _normalize_narrative_payload(normalized)
    normalized = _box[0]
    return str(normalized or "").strip()


def _render_delegated_result_naturally(
    *,
    agent_id: str,
    result_text: str,
    reply_template: str | None,
    render_target_lang: str = "zh",
    extract_delegate_visible_text: Callable[[str], str] | None = None,
) -> str:
    _extract_visible = extract_delegate_visible_text or _extract_delegate_visible_text
    visible = _extract_visible(result_text)
    templates = _load_runtime_texts_section(
        "delegated_result",
        render_target_lang=render_target_lang,
    )
    empty_visible_templates = _mapping_section(templates, "empty_visible_result")
    if not visible or _DELEGATE_INTERNAL_MARKER_RE.search(visible):
        if is_codex_runtime_skill(agent_id):
            return _mapping_text(empty_visible_templates, "codex_worker")
        return _mapping_text(empty_visible_templates, "default")

    if reply_template and "{agent_result}" in reply_template:
        rendered = reply_template.replace("{agent_result}", visible).strip()
        if rendered:
            return rendered
    if is_codex_runtime_skill(agent_id):
        return _mapping_text(
            templates,
            "codex_worker_prefix_template",
            agent_result=visible,
        ).strip()
    return visible


def _resolve_retrieval_timeout_seconds() -> float:
    raw = str(os.environ.get("HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS") or "").strip()
    try:
        timeout_ms = int(raw) if raw else 20_000
    except ValueError:
        timeout_ms = 20_000
    timeout_ms = max(500, min(timeout_ms, 60_000))
    return timeout_ms / 1000.0


def _resolve_selection_followup_retrieval_timeout_seconds() -> float:
    raw = str(os.environ.get("HOST_SELECTION_FOLLOWUP_RETRIEVAL_TIMEOUT_MS") or "").strip()
    try:
        timeout_ms = int(raw) if raw else 35_000
    except ValueError:
        timeout_ms = 35_000
    timeout_ms = max(1_000, min(timeout_ms, 90_000))
    return timeout_ms / 1000.0


def _needs_selection_followup_retrieval_budget(message: str) -> bool:
    text = re.sub(r"\s+", "", str(message or ""))
    if not text:
        return False
    if not re.search(r"(哪本|哪部|哪个|哪個|喜欢哪|喜歡哪|好看|先看|选哪|選哪|挑哪|推荐哪|推薦哪)", text):
        return False
    return bool(re.search(r"(这几本|這幾本|这三本|這三本|这两本|這兩本|这本书|這本書|书|書|轻小说|輕小說|小说|小說)", text))


def _is_low_risk_short_chat_route_reason(route_reason: str) -> bool:
    reason = str(route_reason or "").strip()
    if not reason:
        return False
    return bool(
        reason.startswith("RELATIONAL_CHAT:")
        or reason.startswith("THALAMUS_RULE_PHATIC")
        or reason
        in {
            "THALAMUS_RULE_ASSISTANT_REPLY_FOLLOWUP",
            "THALAMUS_RULE_CONNECTIVITY_CHECK",
        }
    )


def _maybe_build_task_progress_fastpath(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str = "zh",
    task_progress_query_re: Any | None = None,
    task_failure_query_re: Any | None = None,
    task_step_index_re: Any | None = None,
) -> str | None:
    normalized = str(user_message or "").strip()
    _task_progress_query_re = task_progress_query_re or _TASK_PROGRESS_QUERY_RE
    _task_failure_query_re = task_failure_query_re or _TASK_FAILURE_QUERY_RE
    _task_step_index_re = task_step_index_re or _TASK_STEP_INDEX_RE
    if not normalized or not (_task_progress_query_re.search(normalized) or _task_failure_query_re.search(normalized)):
        return None

    with soft_fail("agent_runtime_shared.task_progress_summarize", fallback=None) as _box:
        from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

        _box[0] = get_runtime_task_registry().summarize_for_chat(
            person_id=person_id,
            session_id=session_id,
            exclude_request_id=request_id,
        )
    summary = _box[0]
    templates = _load_runtime_texts_section(
        "task_progress",
        render_target_lang=render_target_lang,
    )
    default_labels = _mapping_section(templates, "defaults")
    if not summary:
        return _mapping_text(templates, "no_summary")

    child_tasks = [dict(item or {}) for item in list(summary.get("child_tasks") or []) if isinstance(item, dict)]
    step_match = _task_step_index_re.search(normalized)
    ask_failure = bool(_task_failure_query_re.search(normalized))


    selected_child = None
    if step_match:
        wanted_step = int(step_match.group(1))
        for child in child_tasks:
            meta = dict(child.get("meta") or {}) if isinstance(child.get("meta"), dict) else {}
            if int(meta.get("step_index") or 0) == wanted_step:
                selected_child = child
                break
    elif ask_failure:
        for child in child_tasks:
            if str(child.get("status") or "").strip().upper() in {"FAILED", "RECOVERING"}:
                selected_child = child
                break

    if selected_child is not None:
        selected_child_templates = _mapping_section(templates, "selected_child")
        meta = dict(selected_child.get("meta") or {}) if isinstance(selected_child.get("meta"), dict) else {}
        step_index = int(meta.get("step_index") or 0)
        default_step_label = _mapping_text(default_labels, "step_label")
        target_id = str(
            selected_child.get("target_id")
            or meta.get("target_id")
            or default_step_label
        ).strip() or default_step_label
        step_desc = str(meta.get("description") or selected_child.get("detail") or "").strip()
        child_status = str(selected_child.get("status") or "UNKNOWN").strip().upper()
        child_error = str(selected_child.get("error") or "").strip()
        recovery_text = str(selected_child.get("recovery_result_text") or "").strip()
        if child_status in {"FAILED", "RECOVERING"}:
            parts = [
                _mapping_text(
                    selected_child_templates,
                    "failed_intro_template",
                    step_index=step_index,
                    target_id=target_id,
                ),
                _mapping_text(selected_child_templates, "failed_status"),
            ]
            if step_desc:
                parts.append(
                    _mapping_text(
                        selected_child_templates,
                        "failed_detail_template",
                        step_desc=step_desc,
                    )
                )
            if child_error:
                parts.append(
                    _mapping_text(
                        selected_child_templates,
                        "failed_error_template",
                        error_text=child_error,
                    )
                )
            if recovery_text:
                parts.append(
                    _mapping_text(
                        selected_child_templates,
                        "failed_recovery_template",
                        recovery_text=recovery_text,
                    )
                )
            return "".join(parts)
        parts = [
            _mapping_text(
                selected_child_templates,
                "completed_intro_template",
                step_index=step_index,
                target_id=target_id,
            ),
            _mapping_text(selected_child_templates, "completed_status"),
        ]
        if step_desc:
            parts.append(
                _mapping_text(
                    selected_child_templates,
                    "completed_detail_template",
                    step_desc=step_desc,
                )
            )
        return "".join(parts)

    status = str(summary.get("status") or "UNKNOWN").strip().upper()
    if ask_failure and status in {"FAILED", "RECOVERING"}:
        failed_summary_templates = _mapping_section(templates, "failed_summary")
        default_task_label = _mapping_text(default_labels, "task_label")
        target_id = str(summary.get("target_id") or default_task_label).strip() or default_task_label
        error_text = str(summary.get("error") or "").strip()
        recovery_text = str(summary.get("recovery_result_text") or "").strip()
        parts = [
            _mapping_text(
                failed_summary_templates,
                "intro_template",
                target_id=target_id,
            )
        ]
        if error_text:
            parts.append(
                _mapping_text(
                    failed_summary_templates,
                    "error_template",
                    error_text=error_text,
                )
            )
        if recovery_text:
            parts.append(
                _mapping_text(
                    failed_summary_templates,
                    "recovery_template",
                    recovery_text=recovery_text,
                )
            )
        return "".join(parts)
    default_task_label = _mapping_text(default_labels, "task_label")
    target_id = str(summary.get("target_id") or default_task_label).strip() or default_task_label
    progress_message = str(summary.get("progress_message") or summary.get("detail") or "").strip()
    recovery_result_text = str(summary.get("recovery_result_text") or "").strip()
    if status in {"RUNNING", "RECOVERING"}:
        running_summary_templates = _mapping_section(templates, "running_summary")
        parts = [
            _mapping_text(
                running_summary_templates,
                "intro_template",
                target_id=target_id,
            )
        ]
        if progress_message:
            parts.append(
                _mapping_text(
                    running_summary_templates,
                    "progress_template",
                    progress_message=progress_message,
                )
            )
        if recovery_result_text:
            parts.append(
                _mapping_text(
                    running_summary_templates,
                    "recovery_template",
                    recovery_text=recovery_result_text,
                )
            )
        return "".join(parts)
    finished_summary_templates = _mapping_section(templates, "finished_summary")
    parts = [
        _mapping_text(
            finished_summary_templates,
            "intro_template",
            target_id=target_id,
            status_text=status.lower(),
        )
    ]
    if progress_message:
        parts.append(
            _mapping_text(
                finished_summary_templates,
                "progress_template",
                progress_message=progress_message,
            )
        )
    if recovery_result_text:
        parts.append(
            _mapping_text(
                finished_summary_templates,
                "recovery_template",
                recovery_text=recovery_result_text,
            )
        )
    return "".join(parts)


def _maybe_build_previous_meaning_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    # Clarification direct rendering is retired; let the chat main chain use
    # InteractionThreadState + ReplyPlan instead of a side-door template answer.
    return None
    normalized = str(user_message or "").strip()
    if not normalized or not _CLARIFICATION_DIRECT_QUERY_RE.match(normalized):
        return None

    turns = _load_continuity_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=24,
    )
    if not turns:
        return None

    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )
    if not latest_assistant:
        return None

    templates = _load_runtime_texts_section(
        "clarification_direct_answer",
        render_target_lang=render_target_lang,
    )
    return _mapping_text(
        templates,
        "assistant_meaning_template",
        latest_assistant=latest_assistant,
    )


def _should_buffer_stream_output(
    *,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
) -> bool:
    if not _parse_bool_env("HOST_STREAM_REFLECTION_BUFFER_ENABLED", default=True):
        return False
    try:
        from host_brain.app.dependencies import get_reflection_chain  # noqa: PLC0415

        reflection = get_reflection_chain()
        return bool(
            reflection.should_reflect(
                emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""),
                intent=str((bond_summary or {}).get("intent") or ""),
                bond_stage=str((bond_summary or {}).get("stage") or "FRIEND"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception as _sr_exc:
        _logger.debug(
            "agent_runtime_shared.should_reflect soft fail: %s", _sr_exc, exc_info=True
        )
        from host_brain.core.soft_fail import _emit_metric  # noqa: PLC0415

        _emit_metric(
            stage="agent_runtime_shared.should_reflect",
            exc_type=type(_sr_exc).__name__,
        )
        return False


def _extract_delegate_from_llm_output(full_text: str) -> tuple[str | None, str | None, str | None]:
    m = _DELEGATE_RE.search(full_text)
    if m:
        agent_id = m.group(1).strip()
        task = (m.group(2) or "").strip()
        before = full_text[: m.start()].strip()
        after = full_text[m.end():].strip()
        template = f"{before}\n{{agent_result}}\n{after}".strip()
        if agent_id in ALL_AGENT_IDS:
            return agent_id, task, template

    try:
        json_text = full_text.strip()
        if json_text.startswith("```"):
            lines = json_text.split("\n")
            json_lines: list[str] = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.strip() == "```" and in_block:
                    break
                if in_block:
                    json_lines.append(line)
            json_text = "\n".join(json_lines)

        parsed = json.loads(json_text)
        if isinstance(parsed, dict):
            delegate = parsed.get("delegate")
            if isinstance(delegate, dict):
                agent_id = str(delegate.get("agent") or "").strip()
                task = str(delegate.get("task") or "").strip()
                template = str(parsed.get("reply_template") or "").strip()
                if agent_id in ALL_AGENT_IDS and task:
                    return agent_id, task, template or None
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return None, None, None


def _extract_visible_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    return str(narrative.get("text_zh") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text


def _build_retrieval_bond_summary(summary_raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(summary_raw)
    bond_stage = str(raw.get("bond_stage") or raw.get("stage") or "").strip()
    if not bond_stage:
        return {}

    bond_summary: dict[str, Any] = {
        "stage": bond_stage,
        "trend": str(raw.get("trend") or "").strip(),
        "capability_hint": str(raw.get("capability_hint") or "").strip(),
    }
    bond_score = _coerce_trace_score(raw.get("bond_score"))
    if bond_score is not None:
        bond_summary["score"] = bond_score
        bond_summary["bond_score"] = bond_score
    try:
        interaction_count = int(raw.get("interaction_count") or 0)
    except (TypeError, ValueError):
        interaction_count = 0
    if interaction_count > 0:
        bond_summary["interaction_count"] = interaction_count
    return bond_summary


async def _prefetch_retrieval_persona_inputs(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
    mood_state: str | None,
    governor_decision: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    bond_summary: dict[str, Any] = {}
    bond_evidence: dict[str, Any] | None = None
    motive_state: dict[str, Any] = {}

    try:
        from host_brain.app.state_arbiter_queries import _resolve_state_arbiter_bond_fact  # noqa: PLC0415

        bond_summary_raw, bond_evidence = await _resolve_state_arbiter_bond_fact(
            session_id=session_id,
            request_id=request_id,
        )
        bond_summary = _build_retrieval_bond_summary(bond_summary_raw)
    except Exception as exc:
        _logger.debug("[%s] retrieval persona bond prefetch failed: %s", request_id, exc)

    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        retrieval_emotion_context = dict(emotion_context or {})
        if mood_state and not str(retrieval_emotion_context.get("mood_state") or "").strip():
            retrieval_emotion_context["mood_state"] = mood_state
        runtime_flags = {"source": "cortex_orchestrator_retrieval"}
        resolved_sys_mode = str(
            (governor_decision or {}).get("sys_mode")
            or (governor_decision or {}).get("mood_state")
            or ""
        ).strip().lower()
        if resolved_sys_mode:
            runtime_flags["sys_mode"] = resolved_sys_mode
        motive_state_raw = await asyncio.to_thread(
            get_limbic_agent().compute_runtime_motive_state,
            persona_user_id=person_id,
            current_topic=user_message,
            bond_summary=bond_summary,
            emotion_context=retrieval_emotion_context,
            pending_topics=[user_message] if user_message else [],
            runtime_flags=runtime_flags,
            now_ms=None,
        )
        motive_state = dict(motive_state_raw) if isinstance(motive_state_raw, Mapping) else {}
    except Exception as exc:
        _logger.debug("[%s] retrieval persona motive prefetch failed: %s", request_id, exc)

    return bond_summary, bond_evidence, motive_state


async def _cortex_orchestrator_sse_generator_impl(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
    sse_fn: Callable[[dict[str, Any]], str] | None = None,
    airlock_sanitize_input_fn: Callable[[str], tuple[str, bool]] | None = None,
    airlock_sanitize_output_fn: Callable[[str], str] | None = None,
    maybe_build_task_progress_fastpath_fn: Callable[..., str | None] | None = None,
    maybe_build_recent_turn_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_contextual_memory_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_contextual_style_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_contextual_session_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_previous_meaning_answer_fn: Callable[..., str | None] | None = None,
    async_post_process_fn: Callable[..., Any] | None = None,
    resolve_retrieval_timeout_seconds_fn: Callable[[], float] | None = None,
    prefetch_retrieval_persona_inputs_fn: Callable[..., Any] | None = None,
    chat_lite_governor_fn: Callable[..., dict[str, Any]] | None = None,
    resolve_person_id_fn: Callable[..., str] | None = None,
) -> AsyncGenerator[str, None]:
    orchestrator_started_ms = int(time.time() * 1000)
    _emit_sse = sse_fn or _sse
    _sanitize_input = airlock_sanitize_input_fn or _airlock_sanitize_input
    _sanitize_output = airlock_sanitize_output_fn or _airlock_sanitize_output
    _task_progress_fastpath = maybe_build_task_progress_fastpath_fn or _maybe_build_task_progress_fastpath
    _recent_turn_answer = maybe_build_recent_turn_answer_fn or _maybe_build_recent_turn_answer
    _contextual_memory_answer = maybe_build_contextual_memory_answer_fn or _maybe_build_contextual_memory_answer
    _contextual_style_answer = maybe_build_contextual_style_answer_fn or _maybe_build_contextual_style_answer
    _contextual_session_answer = maybe_build_contextual_session_answer_fn or _maybe_build_contextual_session_answer
    _previous_meaning_answer = maybe_build_previous_meaning_answer_fn or _maybe_build_previous_meaning_answer
    _post_process = async_post_process_fn or _async_post_process
    _resolve_timeout = resolve_retrieval_timeout_seconds_fn or _resolve_retrieval_timeout_seconds
    _prefetch_persona_inputs = prefetch_retrieval_persona_inputs_fn or _prefetch_retrieval_persona_inputs
    _chat_governor = chat_lite_governor_fn or chat_lite_governor
    _resolve_person = resolve_person_id_fn or resolve_person_id

    user_message = str(request_body.message or "").strip()
    thread_id = getattr(request_body, "thread_id", None)
    person_id = _resolve_person(
        getattr(request_body, "person_id", None),
        session_id=session_id,
        platform=getattr(request_body, "reply_channel", None),
        conversation_id=getattr(request_body, "reply_target", None),
    )

    sanitized_message, airlock_passed = _sanitize_input(user_message)
    if not airlock_passed:
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "AIRLOCK_EMPTY_MESSAGE",
                "request_id": request_id,
            }
        )
        return

    _pii_ingress_stats: dict[str, Any] = {}
    with soft_fail("agent_runtime_shared.pii_scrub_ingress", fallback=None):
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415

        if _check_channel("INGRESS"):
            _pii_result = pii_scrub_text(sanitized_message, context="ingress")
            sanitized_message = _pii_result.scrubbed_text
            if _pii_result.has_pii:
                _pii_ingress_stats = {
                    "detections": _pii_result.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_result.detections}),
                }

    yield _emit_sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    gov = _chat_governor(session_id=session_id, request_id=request_id)
    if gov["decision"] != "APPROVE":
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": gov.get("reason_code", "REJECTED"),
                "request_id": request_id,
            }
        )
        return

    bond_summary: dict[str, Any] = {}

    with soft_fail("agent_runtime_shared.cortex_conversation_started", fallback=None):
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        get_cortex_agent().emit_conversation_started(
            user_id=str(person_id or ""),
            session_id=str(session_id or ""),
            request_id=str(request_id or ""),
        )

    render_target_lang = _resolve_text_render_target_lang(
        request_body=request_body,
        sanitized_message=sanitized_message,
    )

    task_progress_answer = (
        _task_progress_fastpath(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            render_target_lang=render_target_lang,
        )
        if _parse_bool_env("HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED", default=False)
        else None
    )
    if task_progress_answer:
        narrative = _sanitize_output(str(task_progress_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:task_progress_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "TASK_PROGRESS_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "chat_agent_used": False,
                    "cortex_orchestrator_mode": "task_progress_direct",
                },
            )
        )
        return

    recent_turn_answer = (
        _recent_turn_answer(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            render_target_lang=render_target_lang,
        )
        if _parse_bool_env("HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED", default=False)
        else None
    )
    if recent_turn_answer:
        narrative = _sanitize_output(str(recent_turn_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:recent_turn_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "RECENT_TURN_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "chat_agent_used": False,
                    "cortex_orchestrator_mode": "recent_turn_direct",
                },
            )
        )
        return

    contextual_session_answer = _contextual_session_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
    )
    if contextual_session_answer:
        narrative = _sanitize_output(str(contextual_session_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:contextual_session_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CONTEXTUAL_SESSION_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "chat_agent_used": False,
                    "cortex_orchestrator_mode": "contextual_session_direct",
                    "reply_source": "contextual_session_direct",
                },
            )
        )
        return

    contextual_style_answer = _contextual_style_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
    )
    if contextual_style_answer:
        narrative = _sanitize_output(str(contextual_style_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:contextual_style_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CONTEXTUAL_STYLE_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "chat_agent_used": False,
                    "cortex_orchestrator_mode": "contextual_style_direct",
                    "reply_source": "contextual_style_direct",
                },
            )
        )
        return

    contextual_memory_answer = _contextual_memory_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang=render_target_lang,
    )
    if contextual_memory_answer:
        narrative = _sanitize_output(str(contextual_memory_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:contextual_memory_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CONTEXTUAL_MEMORY_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "chat_agent_used": False,
                    "cortex_orchestrator_mode": "contextual_memory_direct",
                    "reply_source": "contextual_memory_direct",
                },
            )
        )
        return

    clarification_direct_answer = (
        _previous_meaning_answer(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            render_target_lang=render_target_lang,
        )
        if _parse_bool_env("HOST_CLARIFICATION_DIRECT_ENABLED", default=False)
        else None
    )
    if clarification_direct_answer:
        narrative = _sanitize_output(str(clarification_direct_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "cortex_orchestrator:clarification_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CLARIFICATION_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "cortex_orchestrator_used": True,
                    "executive_router_used": False,
                    "chat_agent_used": False,
                    "cortex_orchestrator_mode": "clarification_direct",
                },
            )
        )
        return

    has_multimodal_input = bool(getattr(request_body, "attachments", None)) or bool(
        getattr(request_body, "multimodal_observations", None)
    )
    assistant_followup_fastlane = (not has_multimodal_input) and _is_short_assistant_reply_followup(sanitized_message)
    route_reason = str(
        (intent_draft or {}).get("route_reason")
        or (intent_draft or {}).get("reason_code")
        or ""
    ).strip()
    route_hint = str((intent_draft or {}).get("route") or "").strip().lower()
    relational_fastlane = False
    relational_topic = ""
    phatic_fastlane = False
    direct_status_fastlane = route_hint in {"task_progress", "recent_turn"}
    multimodal_fastlane = False
    if has_multimodal_input:
        _multimodal_observations = dict(getattr(request_body, "multimodal_observations", None) or {})
        _primary_modality = str(_multimodal_observations.get("primary_modality") or "").strip().lower()
        multimodal_fastlane = bool(
            route_reason == "THALAMUS_RULE_MULTIMODAL_CHAT"
            or _primary_modality in {"image", "emoji", "sticker"}
            or sanitized_message in {"[图片消息]", "[表情消息]", "[贴纸消息]", "[动画表情]"}
        )
    if not has_multimodal_input:
        try:
            from host_brain.app.relational_chat_detector import detect_relational_chat_topic  # noqa: PLC0415

            relational_topic = str(detect_relational_chat_topic(sanitized_message) or "").strip()
        except Exception as relational_detector_exc:
            _logger.debug("[%s] cortex_orchestrator relational detector degraded: %s", request_id, relational_detector_exc)
        relational_fastlane = bool(
            _is_low_risk_short_chat_route_reason(route_reason)
            or relational_topic
            or assistant_followup_fastlane
        )
        phatic_fastlane = bool(
            route_reason.startswith("THALAMUS_RULE_PHATIC")
            or route_reason == "THALAMUS_RULE_CONNECTIVITY_CHECK"
        )

    try:
        from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

        runtime_task_status_templates = _load_runtime_texts_section(
            "runtime_task_status",
            render_target_lang=render_target_lang,
        )
        runtime_task_registry = get_runtime_task_registry()
        if runtime_task_registry.get_by_request_id(request_id) is None:
            runtime_task_registry.create_task(
                task_kind="chat_request",
                target_id="cortex_orchestrator",
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                user_message=sanitized_message,
                detail=_mapping_text(
                    runtime_task_status_templates,
                    "pre_dispatch_detail",
                ),
                progress_message=_mapping_text(
                    runtime_task_status_templates,
                    "pre_dispatch_progress",
                ),
                meta={"stage": "cortex_pre_dispatch"},
            )
    except Exception as task_exc:
        _logger.debug("[%s] cortex_orchestrator runtime task init failed: %s", request_id, task_exc)

    retrieval_task: asyncio.Task | None = None
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {"status": "SKIPPED"}
    bond_evidence: dict[str, Any] | None = None
    retrieval_motive_state: dict[str, Any] = {}
    prefetch_persona_inputs_ms = 0
    retrieval_wait_ms = 0
    prompt_filter_ms = 0
    llm_runtime_resolve_ms = 0
    _hard_state_summary = gov.get("hard_state_summary") if isinstance(gov.get("hard_state_summary"), Mapping) else {}
    retrieval_mood_state = (
        str(
            emotion_snapshot.get("mood_state")
            or emotion_context.get("mood_state")
            or gov.get("mood_state")
            or _hard_state_summary.get("mood_state")
            or ""
        ).strip().lower()
        or None
    )

    prefetch_started_ms = int(time.time() * 1000)
    try:
        if relational_fastlane or direct_status_fastlane:
            bond_summary, bond_evidence, retrieval_motive_state = await asyncio.wait_for(
                _prefetch_persona_inputs(
                    request_id=request_id,
                    session_id=session_id,
                    person_id=person_id,
                    user_message=sanitized_message,
                    emotion_context=emotion_context,
                    mood_state=retrieval_mood_state,
                    governor_decision=gov,
                ),
                timeout=0.35,
            )
        else:
            bond_summary, bond_evidence, retrieval_motive_state = await _prefetch_persona_inputs(
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                user_message=sanitized_message,
                emotion_context=emotion_context,
                mood_state=retrieval_mood_state,
                governor_decision=gov,
            )
    except asyncio.TimeoutError:
        bond_summary, bond_evidence, retrieval_motive_state = {}, None, {}
        _logger.info(
            "[%s] cortex_orchestrator relational prefetch skipped timeout_ms=%s topic=%s route_reason=%s",
            request_id,
            350,
            relational_topic or "",
            route_reason or "",
        )
    prefetch_persona_inputs_ms = max(0, int(time.time() * 1000) - prefetch_started_ms)

    try:
        from host_brain.app.chat_retrieval_helpers import _run_retrieval  # noqa: PLC0415

        if assistant_followup_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": "ASSISTANT_REPLY_FOLLOWUP_RETRIEVAL_SKIPPED",
            }
        elif phatic_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": "PHATIC_RETRIEVAL_SKIPPED",
            }
        elif multimodal_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": "MULTIMODAL_FASTLANE_RETRIEVAL_SKIPPED",
            }
        elif direct_status_fastlane:
            retrieval_summary = {
                "status": "SKIPPED",
                "reason_code": f"{route_hint.upper()}_DIRECT_RETRIEVAL_SKIPPED",
            }
        else:
            retrieval_task = asyncio.create_task(
                _run_retrieval(
                    request_id=request_id,
                    message=sanitized_message,
                    person_id=person_id,
                    session_id=session_id,
                    ts_ms=ts_ms,
                    emotion_tag=str(emotion_context.get("emotion_tag") or "") or None,
                    render_target_lang=render_target_lang,
                    mood_state=retrieval_mood_state,
                    bond_summary=bond_summary or None,
                    motive_state=retrieval_motive_state or None,
                    intent_draft=intent_draft,
                )
            )
    except Exception as retrieval_exc:
        _logger.debug("[%s] cortex_orchestrator retrieval init failed: %s", request_id, retrieval_exc)

    if retrieval_task is not None:
        try:
            retrieval_wait_started_ms = int(time.time() * 1000)
            retrieval_timeout_s = 0.45 if relational_fastlane else _resolve_timeout()
            if not relational_fastlane and _needs_selection_followup_retrieval_budget(sanitized_message):
                retrieval_timeout_s = max(retrieval_timeout_s, _resolve_selection_followup_retrieval_timeout_seconds())
            retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                retrieval_task,
                timeout=retrieval_timeout_s,
            )
            retrieval_wait_ms = max(0, int(time.time() * 1000) - retrieval_wait_started_ms)
        except asyncio.TimeoutError:
            retrieval_wait_ms = max(0, int(time.time() * 1000) - retrieval_wait_started_ms)
            retrieval_summary = (
                {"status": "SKIPPED", "reason_code": "RELATIONAL_CHAT_RETRIEVAL_SKIPPED"}
                if relational_fastlane
                else {"status": "DEGRADED", "reason_code": "RETRIEVAL_TIMEOUT"}
            )
            _logger.info(
                "[%s] cortex_orchestrator retrieval skipped timeout_ms=%s topic=%s route_reason=%s",
                request_id,
                int(retrieval_timeout_s * 1000),
                relational_topic or "",
                route_reason or "",
            )
        except Exception as retrieval_exc:
            retrieval_summary = {"status": "DEGRADED", "error": str(retrieval_exc)[:100]}
            retrieval_wait_ms = max(0, int(time.time() * 1000) - retrieval_wait_started_ms)
            _logger.debug("[%s] cortex_orchestrator retrieval failed/timeout: %s", request_id, retrieval_exc)

    if bond_evidence is not None:
        retrieval_evidence_items.append(bond_evidence)
    try:
        from host_brain.app.chat_retrieval_helpers import filter_retrieval_evidence_items_for_prompt  # noqa: PLC0415

        prompt_filter_started_ms = int(time.time() * 1000)
        filtered_evidence_items, prompt_filter_meta = filter_retrieval_evidence_items_for_prompt(
            user_message=sanitized_message,
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            session_id=session_id,
            person_id=person_id,
            ledger=_get_ledger_safe(),
        )
        prompt_filter_ms = max(0, int(time.time() * 1000) - prompt_filter_started_ms)
        if prompt_filter_meta.get("applied"):
            retrieval_evidence_items = filtered_evidence_items
            retrieval_summary = dict(retrieval_summary or {})
            retrieval_summary["prompt_filter"] = dict(prompt_filter_meta)
    except Exception as prompt_filter_exc:
        _logger.debug("[%s] cortex_orchestrator prompt retrieval filter failed: %s", request_id, prompt_filter_exc)

    # 人格聊天链不再使用 slot 投影。
    # 运行时只保留五路召回原始结果，由 prompt 直接消费检索证据；
    # 最近上下文则继续由 continuity 链单独管理。

    from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async  # noqa: PLC0415

    llm_runtime_started_ms = int(time.time() * 1000)
    llm_runtime = await resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="CHAT",
    )
    llm_runtime_resolve_ms = max(0, int(time.time() * 1000) - llm_runtime_started_ms)

    if llm_runtime is not None and not llm_runtime.get("available"):
        fallback_narrative = _load_runtime_text_value(
            "llm_unavailable_fallback",
            render_target_lang=render_target_lang,
        )
        total_latency_ms = int(time.time() * 1000) - ts_ms
        try:
            asyncio.create_task(
                _post_process(
                    request_id=request_id,
                    person_id=person_id,
                    session_id=session_id,
                    user_message=sanitized_message,
                    narrative=fallback_narrative,
                    emotion_context=emotion_context,
                    bond_summary=bond_summary,
                    ts_ms=ts_ms,
                    render_target_lang=render_target_lang,
                    total_latency_ms=total_latency_ms,
                    llm_latency_ms=0,
                    first_token_ms=None,
                    provider_name=str(llm_runtime.get("provider_name") or "fallback"),
                    model_id=str(llm_runtime.get("model_id") or "llm-unavailable-fallback"),
                    buffer_stream=False,
                    delegated_agents=[],
                    retrieval_summary=(retrieval_summary or {"status": "SKIPPED", "reason_code": "LLM_UNAVAILABLE"}),
                    gov_decision=governor_result,
                    pii_ingress_stats=_pii_ingress_stats,
                    pii_egress_stats={},
                    retrieval_evidence_items=list(retrieval_evidence_items or []),
                    trace_stage_meta={
                        "cortex_orchestrator_used": True,
                        "executive_router_used": False,
                        "chat_agent_used": False,
                        "cortex_orchestrator_mode": "llm_unavailable",
                    },
                )
            )
        except Exception as post_process_exc:
            _logger.debug("[%s] cortex_orchestrator llm unavailable post_process failed: %s", request_id, post_process_exc)
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "LLM_UNAVAILABLE",
                "request_id": request_id,
                "narrative": fallback_narrative,
            }
        )
        return

    from host_brain.app.executive_router import executive_router_sse_generator  # noqa: PLC0415

    _logger.info(
        "[%s] cortex_orchestrator stage_summary prefetch_ms=%s retrieval_wait_ms=%s prompt_filter_ms=%s "
        "llm_runtime_resolve_ms=%s request_to_executive_ms=%s",
        request_id,
        prefetch_persona_inputs_ms,
        retrieval_wait_ms,
        prompt_filter_ms,
        llm_runtime_resolve_ms,
        max(0, int(time.time() * 1000) - orchestrator_started_ms),
    )

    async for event in executive_router_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=gov,
        intent_id=intent_id,
        metrics=metrics,
        person_id=person_id,
        sanitized_message=sanitized_message,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=_pii_ingress_stats,
    ):
        yield event


async def _async_post_process(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    render_target_lang: str,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    reply_plan: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    multimodal_observations: dict[str, Any] | None = None,
    attachment_summary: str = "",
    reply_channel: str | None = None,
    reply_target: str | None = None,
) -> None:
    normalized_trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    voice_outputs_enabled = bool(normalized_trace_stage_meta.get("voice_outputs_enabled"))
    suppress_final_reply = bool(normalized_trace_stage_meta.get("suppress_final_reply"))
    skip_heavy_persona_postprocess = bool(
        normalized_trace_stage_meta.get("skip_heavy_persona_postprocess")
    )
    skip_default_mode_on_conversation_end = bool(
        normalized_trace_stage_meta.get("skip_default_mode_on_conversation_end")
    )
    surface_main_chain_used = bool(normalized_trace_stage_meta.get("surface_main_chain_used"))

    try:
        from host_brain.tribe.default_mode_agent import mark_recent_foreground_interaction  # noqa: PLC0415

        mark_recent_foreground_interaction(ts_ms=int(time.time() * 1000))
    except Exception:
        pass

    if surface_main_chain_used:
        try:
            from host_brain.app.surface_emotion_bond_flow import apply_surface_emotion_bond_flow  # noqa: PLC0415

            surface_bond_result = apply_surface_emotion_bond_flow(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                assistant_reply=narrative,
                emotion_context=emotion_context,
                bond_summary=bond_summary,
                reply_plan=reply_plan,
                retrieval_evidence_items=retrieval_evidence_items,
                ts_ms=ts_ms,
            )
            normalized_trace_stage_meta = {
                **normalized_trace_stage_meta,
                "surface_emotion_bond_flow": surface_bond_result,
            }
        except Exception as exc:
            _logger.debug("[%s] surface emotion-bond flow failed: %s", request_id, exc)
            normalized_trace_stage_meta = {
                **normalized_trace_stage_meta,
                "surface_emotion_bond_flow": {
                    "status": "DEGRADED",
                    "reason_code": "SURFACE_EMOTION_BOND_FLOW_FAILED",
                },
            }

    try:
        await _persist_post_process_audit(
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            ts_ms=ts_ms,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=llm_latency_ms,
            first_token_ms=first_token_ms,
            provider_name=provider_name,
            model_id=model_id,
            buffer_stream=buffer_stream,
            delegated_agents=delegated_agents,
            retrieval_summary=retrieval_summary,
            gov_decision=gov_decision,
            pii_ingress_stats=pii_ingress_stats,
            pii_egress_stats=pii_egress_stats,
            llm_input_digest=llm_input_digest,
            retrieval_evidence_items=retrieval_evidence_items,
            motive_state=motive_state,
            reply_plan=reply_plan,
            trace_stage_meta=normalized_trace_stage_meta,
        )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process audit persist failed: %s", request_id, exc)

    try:
        from host_brain.conversation.writer import record_assistant_turn  # noqa: PLC0415

        if suppress_final_reply:
            _logger.debug("[%s] cortex_orchestrator skipped assistant turn: suppressed final reply", request_id)
        else:
            fallback_used = _resolve_persisted_fallback_flag(
                trace_stage_meta=normalized_trace_stage_meta,
                gov_decision=gov_decision,
                narrative=narrative,
                provider_name=provider_name,
                model_id=model_id,
            )
            record_assistant_turn(
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                content=narrative,
                ts_ms=int(time.time() * 1000),
                lang=render_target_lang,
                reply_plan=reply_plan,
                metadata={
                    "provider_name": provider_name,
                    "model_id": model_id,
                    "fallback": fallback_used,
                    "message_kind": "final_reply",
                    "reply_source": str(normalized_trace_stage_meta.get("reply_source") or ("fallback" if fallback_used else "full_model")).strip(),
                    "degraded": bool(fallback_used),
                    "reply_envelope": dict(normalized_trace_stage_meta.get("reply_envelope") or {}),
                },
            )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process conversation_turn failed: %s", request_id, exc)

    try:
        from host_brain.airlock.quarantine import classify_ingress  # noqa: PLC0415

        await _run_in_post_process_executor(classify_ingress, narrative)
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process airlock classify failed: %s", request_id, exc)

    try:
        if not suppress_final_reply:
            from host_brain.app.observability_persist import persist_life_chat_episode  # noqa: PLC0415

            life_user = user_message
            life_narrative = narrative
            with soft_fail("agent_runtime_shared.pii_scrub_life_memory_write", fallback=None):
                from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled  # noqa: PLC0415

                if _check_channel("MEMORY"):
                    life_user = pii_scrub_if_enabled(user_message, context="life_memory_write_user")
                    life_narrative = pii_scrub_if_enabled(narrative, context="life_memory_write_ai")
            life_memory_write = await _run_in_post_process_executor(
                persist_life_chat_episode,
                request_id=request_id,
                intent_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=life_user,
                assistant_reply=life_narrative,
                ts_ms=ts_ms,
                attachments=list(attachments or []),
                multimodal_observations=dict(multimodal_observations or {}),
                attachment_summary=str(attachment_summary or "").strip(),
                metadata={
                    "trace_stage_meta": dict(normalized_trace_stage_meta or {}),
                    "reply_source": str(normalized_trace_stage_meta.get("reply_source") or ""),
                    "reply_channel": str(reply_channel or "").strip() or None,
                    "reply_target": str(reply_target or "").strip() or None,
                },
            )
            normalized_trace_stage_meta = {
                **normalized_trace_stage_meta,
                "life_memory_write": life_memory_write,
            }
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process life memory failed: %s", request_id, exc)

    try:
        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

        store = None if suppress_final_reply else get_episodic_store()
        if store is not None:
            mem_user = user_message
            mem_narrative = narrative
            with soft_fail("agent_runtime_shared.pii_scrub_memory_write", fallback=None):
                from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled  # noqa: PLC0415

                if _check_channel("MEMORY"):
                    mem_user = pii_scrub_if_enabled(user_message, context="memory_write_user")
                    mem_narrative = pii_scrub_if_enabled(narrative, context="memory_write_ai")

            if surface_main_chain_used:
                surface_memory_write = await _run_in_post_process_executor(
                    _write_surface_main_chain_memory_sync,
                    episodic_store=store,
                    request_id=request_id,
                    person_id=person_id,
                    session_id=session_id,
                    user_message=mem_user,
                    assistant_reply=mem_narrative,
                    emotion_context=emotion_context,
                    ts_ms=ts_ms,
                    reply_plan=reply_plan,
                )
                normalized_trace_stage_meta = {
                    **normalized_trace_stage_meta,
                    "surface_memory_write": surface_memory_write,
                }
            else:
                from host_brain.app.observability_persist import persist_chat_episodic_memory  # noqa: PLC0415

                await _run_in_post_process_executor(
                    persist_chat_episodic_memory,
                    request_id=request_id,
                    intent_id=request_id,
                    person_id=person_id,
                    session_id=session_id,
                    user_message=mem_user,
                    assistant_reply=mem_narrative,
                    emotion_context=emotion_context,
                    ts_ms=ts_ms,
                    episodic_store=store,
                    attachments=list(attachments or []),
                    multimodal_observations=dict(multimodal_observations or {}),
                    attachment_summary=str(attachment_summary or "").strip(),
                    metadata={
                        "trace_stage_meta": dict(normalized_trace_stage_meta or {}),
                        "reply_source": str(normalized_trace_stage_meta.get("reply_source") or ""),
                        "reply_channel": str(reply_channel or "").strip() or None,
                        "reply_target": str(reply_target or "").strip() or None,
                    },
                )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process episodic failed: %s", request_id, exc)

    if skip_heavy_persona_postprocess and surface_main_chain_used:
        try:
            surface_session_end = await _run_in_post_process_executor(
                _run_surface_persona_session_end_sync,
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                emotion_context=emotion_context,
            )
            normalized_trace_stage_meta = {
                **normalized_trace_stage_meta,
                "surface_persona_session_end": surface_session_end,
            }
        except Exception as exc:
            _logger.debug("[%s] surface persona session_end failed: %s", request_id, exc)

    if skip_heavy_persona_postprocess:
        _logger.debug(
            "[%s] cortex_orchestrator post_process skip persona session_end",
            request_id,
        )
    else:
        try:
            await _run_persona_session_end_hooks(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                emotion_context=emotion_context,
            )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process persona session_end failed: %s", request_id, exc)

    if surface_main_chain_used:
        _logger.debug("[%s] cortex_orchestrator skipped legacy bond update: surface flow handled it", request_id)
    else:
        try:
            from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

            await get_cortex_agent().post_process_bond_update(
                request_id=request_id,
                session_id=session_id,
                user_message=user_message,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
            )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process bond failed: %s", request_id, exc)

    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent as _get_cortex  # noqa: PLC0415

        ec = dict(emotion_context or {})
        event_user_id = str(person_id or "")
        if surface_main_chain_used:
            from host_brain.bond.user_identity import resolve_bond_user_id  # noqa: PLC0415

            event_user_id = resolve_bond_user_id(event_user_id or None, session_id=session_id or None)
        _get_cortex().emit_conversation_ended(
            user_id=event_user_id,
            session_id=str(session_id or ""),
            request_id=str(request_id or ""),
            emotion_tag=str(ec.get("emotion_tag") or "NEUTRAL").strip().upper(),
            emotion_intensity=float(ec.get("emotion_intensity") or 0.5),
            skip_default_mode_inner_activity=skip_default_mode_on_conversation_end,
            summary={
                "user_message": str(user_message or ""),
                "user_message_preview": str(user_message or "")[:64],
                "assistant_reply": str(narrative or "")[:512],
                "ts_ms": int(ts_ms),
            },
        )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process conversation_ended tribe event failed: %s", request_id, exc)

    try:
        from host_brain.app.vts_emotion_driver import _drive_stream_vts_emotion  # noqa: PLC0415

        await _drive_stream_vts_emotion(
            request_id=request_id,
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process vts failed: %s", request_id, exc)

    try:
        tts_enabled = str(os.environ.get("HOST_STREAM_TTS_ENABLED") or "true").strip().lower()
        tts_sentence_stream = str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        skip_bulk_tts = tts_sentence_stream in ("1", "true", "yes", "on")
        if tts_enabled in ("1", "true", "yes", "on") and narrative.strip() and not skip_bulk_tts:
            from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
            from host_brain.app.intelligent_runtime import resolve_tts_route  # noqa: PLC0415

            base_etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
            tts_route = resolve_tts_route(
                text=narrative,
                emotion_context={**dict(emotion_context), "emotion_tag": base_etag},
                bond_summary=bond_summary or None,
            )
            etag = str(tts_route.get("emotion_tag") or _infer_emotion_tag_for_tts(narrative, base_etag)).strip()
            intensity_raw = emotion_context.get("emotion_intensity")
            intensity: float | None = None
            if intensity_raw is not None:
                try:
                    intensity = max(0.0, min(1.0, float(intensity_raw)))
                except (TypeError, ValueError):
                    pass
            tts_result = await post_speak(
                text=narrative,
                request_id=request_id,
                session_id=session_id,
                speed=float(tts_route.get("speed") or 1.0),
                pitch=float(tts_route.get("pitch") or 0.0),
                emotion_tag=etag,
                emotion_intensity=intensity,
                style_note=str(tts_route.get("style_note") or ""),
                lang_hint=str(tts_route.get("lang_hint") or render_target_lang),
            )

            try:
                from host_brain.app.dependencies import get_ledger as _get_tts_ledger  # noqa: PLC0415

                _get_tts_ledger().log_voice_event(
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "event_type": "TTS_SPEAK",
                        "direction": "EGRESS",
                        "status": str(getattr(tts_result, "status", "OK")),
                        "latency_ms": int(getattr(tts_result, "latency_ms", 0)),
                        "payload": {
                            "text_len": len(narrative),
                            "emotion_tag": etag,
                            "speed": float(tts_route.get("speed") or 1.0),
                            "pitch": float(tts_route.get("pitch") or 0.0),
                            "lang_hint": str(tts_route.get("lang_hint") or render_target_lang),
                        },
                        "ts_ms": int(time.time() * 1000),
                    }
                )
            except Exception:
                pass
    except Exception as exc:
        _logger.debug("[%s] cortex_orchestrator post_process tts failed: %s", request_id, exc)

    if skip_heavy_persona_postprocess:
        _logger.debug(
            "[%s] cortex_orchestrator post_process skip drift/growth",
            request_id,
        )
    else:
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

            drift_result = await _run_in_post_process_executor(
                get_prefrontal_agent().run_persona_drift_cycle,
                llm_output_text=narrative,
                user_input_text=user_message,
            )
            drift_level = str(dict(drift_result or {}).get("alert_level") or "").strip().upper()
            if drift_level in ("MODERATE", "SEVERE"):
                _logger.info(
                    "[%s] 人格漂移检测: level=%s dims=%s",
                    request_id,
                    drift_level,
                    dict(drift_result or {}).get("drift_dims"),
                )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process drift_detector failed: %s", request_id, exc)

        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

            growth_payload = await _run_in_post_process_executor(
                get_prefrontal_agent().run_growth_feedback_cycle,
                session_id=session_id,
                user_message=user_message,
                assistant_reply=narrative,
            )
            if bool(dict(growth_payload or {}).get("applied")):
                _logger.info(
                    "[%s] 人格成长: user=%s rules=%s",
                    request_id,
                    dict(growth_payload or {}).get("user_id"),
                    dict(growth_payload or {}).get("triggered_rules"),
                )
        except Exception as exc:
            _logger.debug("[%s] cortex_orchestrator post_process growth_feedback failed: %s", request_id, exc)


__all__ = [
    "_format_template_text",
    "_load_template_mapping",
    "_load_runtime_texts",
    "_mapping_section",
    "_mapping_text",
    "_parse_bool_env",
    "_sse",
    "_airlock_sanitize_input",
    "_airlock_sanitize_output",
    "_build_reply_preview",
    "_load_continuity_turns",
    "_detect_recent_turn_query_role",
    "_looks_like_active_task_context",
    "_extract_delegate_visible_text",
    "_render_delegated_result_naturally",
    "_resolve_retrieval_timeout_seconds",
    "_normalize_retrieval_summary_for_audit",
    "_surface_memory_debug_trace_for_audit",
    "_coerce_trace_mapping",
    "_coerce_trace_score",
    "_coerce_trace_bool",
    "_summarize_motive_state_for_trace",
    "_summarize_reply_plan_for_trace",
    "_summarize_emotion_context_for_trace",
    "_summarize_persona_resident_memory_for_trace",
    "_normalize_trace_stage_meta",
    "_resolve_envelope_confidence",
    "_build_trace_output_digest",
    "_detect_trace_fallback_used",
    "_build_trace_agent_envelope",
    "_build_trace_stage_node",
    "_persist_post_process_audit",
    "_build_subagent_gateway_from_runtime",
    "_cortex_orchestrator_sse_generator_impl",
    "_maybe_build_task_progress_fastpath",
    "_maybe_build_contextual_memory_answer",
    "_maybe_build_contextual_session_answer",
    "_maybe_build_contextual_style_answer",
    "_maybe_build_recent_turn_answer",
    "_maybe_build_previous_meaning_answer",
    "_should_buffer_stream_output",
    "_extract_delegate_from_llm_output",
    "_extract_visible_text",
    "_build_retrieval_bond_summary",
    "_prefetch_retrieval_persona_inputs",
    "_resolve_persona_session_emotion",
    "_run_persona_session_end_hooks",
    "_infer_emotion_tag_for_tts",
    "_resolve_persisted_fallback_flag",
    "_async_post_process",
]


def _resolve_persisted_fallback_flag(
    *,
    trace_stage_meta: Mapping[str, Any] | None,
    gov_decision: Mapping[str, Any] | None,
    narrative: str,
    provider_name: str,
    model_id: str,
) -> bool:
    normalized_trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    explicit_reply_fallback = _coerce_trace_bool(normalized_trace_stage_meta.get("reply_used_fallback"))
    if explicit_reply_fallback is not None:
        return bool(explicit_reply_fallback)

    explicit_trace_fallback = _coerce_trace_bool(normalized_trace_stage_meta.get("fallback_used"))
    if explicit_trace_fallback is not None:
        return bool(explicit_trace_fallback)

    return _detect_trace_fallback_used(
        reason_code=str((gov_decision or {}).get("reason_code") or ""),
        degrade_code=str(normalized_trace_stage_meta.get("degrade_code") or ""),
        output_data={"narrative": narrative, "provider_name": provider_name, "model_id": model_id},
    )
