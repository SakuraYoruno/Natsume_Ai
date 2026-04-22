from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

_logger = logging.getLogger("Natsume.thalamus_agent")

_INTENT_SCHEMA_VERSION = "Natsume.intent.v1"
_INTENT_CLASSIFICATION_TEMPLATE_ID = "thalamus_agent.intent_classification_prompts"
_ROUTE_ALLOWLIST = {"chat", "delegate", "skill", "plan", "task_progress", "recent_turn"}
_CANONICAL_ACTIONS = {
    "read_only_status",
    "narrative_only",
    "memory_recall",
    "noop",
    "exec_intent",
    "work_code",
    "work_translate",
    "task_progress",
    "recent_turn",
}
_DIRECT_SUMMARIZE_MARKERS = (
    "summarize",
    "summary",
    "recap",
    "回顾",
    "总结",
    "摘要",
    "整理",
)
_DIRECT_SUMMARIZE_CONTEXT_MARKERS = (
    "session",
    "会话",
    "这段会话",
    "当前会话",
    "对话",
    "聊天",
    "今天聊",
    "current session",
)
_DIRECT_EMOTION_MARKERS = (
    "emotion",
    "feeling",
    "mood",
    "情绪",
    "情感",
    "心情",
    "感受",
)
_DIRECT_EMOTION_QUERY_MARKERS = (
    "query",
    "read",
    "snapshot",
    "分析",
    "查询",
    "查一下",
    "查下",
    "看看",
    "读取",
)
_DIRECT_BOND_MARKERS = (
    "bond",
    "relationship",
    "亲密",
    "好感",
    "关系",
    "关系阶段",
)
_DIRECT_BOND_QUERY_MARKERS = (
    "score",
    "stage",
    "状态",
    "阶段",
    "程度",
    "查询",
    "看看",
)
_DIRECT_MEMORY_RECALL_MARKERS = (
    "recall",
    "do you remember",
    "记得",
    "回忆",
    "之前说过",
    "上次提到",
)
_SELF_HEAL_MARKERS = (
    "self heal",
    "heal yourself",
    "自愈",
    "自我修复",
    "修复健康检查",
    "修一下健康检查",
    "健康检查失败",
    "readiness failed",
    "health check failed",
    "/health",
    "/ready",
    "probe失败",
    "修复host",
)
_SELF_EVOLVE_MARKERS = (
    "self evolve",
    "evolve yourself",
    "自我进化",
    "系统进化",
    "能力进化",
    "持续进化",
    "进化一下",
)
_HUB_SKILL_LIST_MARKERS = (
    "列出技能",
    "技能列表",
    "看看技能",
    "有什么技能",
    "有哪些技能",
    "list skills",
    "show skills",
    "show me skills",
    "available skills",
)
_STEP_LEAD_IN_RE = re.compile(
    r"^\s*(?:请|请你|帮我|麻烦|蓝蓝|夏目蓝|natsume(?: blue)?|先|首先|first)\s*",
    re.IGNORECASE,
)
_HUB_SEARCH_PATTERNS = (
    re.compile(r"(?:搜索技能|查找技能|搜技能)\s*[:：]?\s*(?P<query>.+)$", re.IGNORECASE),
    re.compile(r"(?:search|find)\s+(?:skills?|skill)\s*(?:for|about)?\s*(?P<query>.+)$", re.IGNORECASE),
)
_EXPLICIT_MULTI_STEP_SPLITTERS = (
    ("再", re.compile(r"\s*(?:，|,|；|;)?\s*再\s*", re.IGNORECASE)),
    ("然后", re.compile(r"\s*(?:，|,|；|;)?\s*然后\s*", re.IGNORECASE)),
    ("接着", re.compile(r"\s*(?:，|,|；|;)?\s*接着\s*", re.IGNORECASE)),
    ("之后", re.compile(r"\s*(?:，|,|；|;)?\s*之后\s*", re.IGNORECASE)),
    ("and then", re.compile(r"\s*(?:,|;)?\s*and then\s*", re.IGNORECASE)),
    ("then", re.compile(r"\s*(?:,|;)?\s*then\s*", re.IGNORECASE)),
    ("next", re.compile(r"\s*(?:,|;)?\s*next\s*", re.IGNORECASE)),
)
_TASK_TRANSLATE_RE = re.compile(
    r"(翻译|译成|翻成|translate|translation|中译英|英译中|中译日|日译中)",
    re.IGNORECASE,
)
_TASK_MEMORY_RE = re.compile(
    r"(记住|记一下|记下来|写入记忆|保存.*记忆|记到记忆|remember this|save this memory|偏好是|我的偏好)",
    re.IGNORECASE,
)
_TASK_CODE_RE = re.compile(
    r"(报错|错误|异常|修复|修一个|改代码|写代码|单测|测试|diff|patch|traceback|stack trace|compile|build|修.*bug|bug.*修"
    r"|代码|编程|写.*函数|函数|脚本|调试|实现|开发|code|coding|programming|function|script|program"
    r"|python|javascript|typescript|java|golang|rust|c\+\+|debug|bug|class|algorithm|implement|develop)",
    re.IGNORECASE,
)
_TASK_VISION_UI_RE = re.compile(
    r"(screenshot|screen shot|截图|屏幕|界面|画面|看一下屏幕|看看这个界面|desktop|ui|window|interface|capture)",
    re.IGNORECASE,
)
_TASK_RECENT_TURN_RE = re.compile(
    r"(我刚刚|上一句|上一条|上句说|你刚刚|what did i just say|what did you just say)",
    re.IGNORECASE,
)
_TASK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|进展|任务状态|执行状态|结果呢|有结果吗|完成了\??|做好了吗|好了没|搞定了吗|到哪了|还要多久|eta|done\??|status|progress|update)",
    re.IGNORECASE,
)
_TASK_INTENT_PLANNING_RE = re.compile(
    r"(三步建议|几步建议|下一步|先做什么|该怎么做|应该怎么做|怎么安排|帮我整理.*建议"
    r"|next steps?|what should i do (?:first|next)|three[- ]step (?:advice|plan)|action plan|plan this out)",
    re.IGNORECASE,
)
_TASK_OPEN_APP_VERB_RE = re.compile(r"(打开|启动|运行|开一下|帮我开|替我开|launch|open)", re.IGNORECASE)
_TASK_OPEN_APP_HINT_RE = re.compile(
    r"(本地|应用|软件|程序|app|酷狗|kugou|qq音乐|网易云|spotify|浏览器|微信|qq|音乐)",
    re.IGNORECASE,
)
_PHATIC_GREETING_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_PHATIC_PRESENCE_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_PHATIC_ACK_RE = re.compile(r"^(嗯|嗯嗯|哦|噢|好的|好呀|好啊|可以|可以啊|可以呀|收到|对|对啊|是的|行)[!！。…~]*$")
_PHATIC_COMFORT_RE = re.compile(
    r"(抱抱我|抱抱|安慰我|哄哄我|陪陪我|想被抱抱|想要抱抱|我好累|好累啊|有点累|有点困|有点难过|有点委屈|想哭|心情不好|不太开心|不开心|累死了)",
    re.IGNORECASE,
)
_PHATIC_FASTPATH_MAX_CHARS = 24
_PHATIC_COMFORT_BLOCKING_TOKENS: tuple[str, ...] = (
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
_COMPANION_OPEN_CHAT_MARKERS: tuple[str, ...] = (
    "认真聊",
    "聊一会儿",
    "聊聊最近",
    "想和你聊",
    "想跟你聊",
    "先听我说",
    "陪我说下去",
    "陪我理顺",
    "接住我",
    "不是为了完成什么任务",
    "不是为了让你",
    "不是要你列清单",
    "不是要你帮我做任务",
    "不是要你帮我",
    "只是想和你聊",
    "慢慢陪我",
    "认真聊聊",
    "talk for a while",
    "hear me out",
    "listen to me first",
    "stay with me",
)
_TASK_EXEC_ACTION_MARKERS: tuple[str, ...] = (
    "请",
    "麻烦",
    "帮我",
    "帮忙",
    "需要你",
    "执行",
    "处理",
    "安排",
    "规划",
    "调用",
    "查询",
    "查一个",
    "查下",
    "检索",
    "搜索",
    "总结",
    "归纳",
    "翻译",
    "改写",
    "写一个",
    "生成",
    "实现",
    "改进",
    "优化",
    "修复",
    "排查",
    "测试",
    "验证",
    "启动",
    "停止",
    "重启",
    "run",
    "execute",
    "build",
    "fix",
    "test",
    "deploy",
    "search",
)
_TASK_ENTERTAINMENT_RE = re.compile(
    r"(动漫|漫画|番剧|动画|电影|电视剧|剧集|综艺|音乐|歌曲|歌手|专辑|小说|读书|游戏|爱好|兴趣|趣味)",
    re.IGNORECASE,
)


def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _normalize_confidence(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0.0:
        return 0.0
    if numeric > 1.0:
        return 1.0
    return round(numeric, 4)


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = _normalize_match_text(text)
    return any(marker in normalized for marker in markers)


def _normalize_phatic_match_text(value: Any) -> str:
    return re.sub(r"[\s，。！？,.!?~～…]+", "", str(value or "").strip().lower())


def _detect_short_companion_chat_reason(text: str) -> str | None:
    cleaned = _strip_step_lead_in(text)
    if not cleaned:
        return None

    compact = _normalize_phatic_match_text(cleaned)
    if not compact or len(compact) > _PHATIC_FASTPATH_MAX_CHARS:
        return None

    if _PHATIC_GREETING_RE.match(cleaned):
        return "THALAMUS_RULE_PHATIC_GREETING"
    if _PHATIC_PRESENCE_RE.match(cleaned):
        return "THALAMUS_RULE_PHATIC_PRESENCE"
    if _PHATIC_ACK_RE.match(cleaned):
        return "THALAMUS_RULE_PHATIC_ACK"
    if _PHATIC_COMFORT_RE.search(compact):
        if any(token in compact for token in _PHATIC_COMFORT_BLOCKING_TOKENS):
            return None
        return "THALAMUS_RULE_PHATIC_COMFORT"
    return None


def _looks_like_companion_open_chat(text: str) -> bool:
    normalized = _normalize_match_text(text)
    if not normalized:
        return False
    has_companion_marker = any(marker in normalized for marker in _COMPANION_OPEN_CHAT_MARKERS)
    if not has_companion_marker:
        return False
    if any(marker in normalized for marker in _TASK_EXEC_ACTION_MARKERS):
        calming_negations = (
            "不是要你帮我",
            "不是要你",
            "不是为了完成什么任务",
            "先听我说",
            "只是想和你聊",
            "接住我",
        )
        if not any(marker in normalized for marker in calming_negations):
            return False
    return True


def _strip_step_lead_in(text: str) -> str:
    stripped = _normalize_text(text)
    while True:
        next_value = _STEP_LEAD_IN_RE.sub("", stripped).strip(" \t,，;；:：")
        if next_value == stripped:
            return stripped
        stripped = next_value


def _extract_hub_skill_search_query(text: str) -> str:
    cleaned = _strip_step_lead_in(text)
    for pattern in _HUB_SEARCH_PATTERNS:
        match = pattern.search(cleaned)
        if match is None:
            continue
        query = str(match.group("query") or "").strip(" \t,，;；:：。.!！？?")
        if query:
            return query
    return ""


def _infer_codex_runtime_mode(text: str) -> str | None:
    normalized = _normalize_match_text(text)
    if any(marker in normalized for marker in _SELF_EVOLVE_MARKERS):
        return "self_evolve"
    if any(marker in normalized for marker in _SELF_HEAL_MARKERS):
        return "self_heal"
    return None


def _looks_like_lightweight_codex_request(text: str) -> bool:
    normalized = _normalize_match_text(text)
    if not normalized:
        return False
    heavyweight_markers = (
        "fix", "debug", "bug", "failing test", "test", "traceback", "stack trace",
        "workspace", "repo", "repository", "project", "file", "files", "patch", "diff",
        "修复", "报错", "错误", "异常", "测试", "仓库", "项目", "文件", "路径", "重构", "集成",
    )
    if any(marker in normalized for marker in heavyweight_markers):
        return False
    lightweight_markers = (
        "写一个", "写个", "写一段", "函数", "示例代码", "代码示例", "python 函数",
        "code snippet", "example code", "write a function", "write a python function",
        "implement a function", "generate a function",
    )
    return any(marker in normalized for marker in lightweight_markers)


def _looks_like_open_app_request(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return bool(_TASK_OPEN_APP_VERB_RE.search(normalized) and _TASK_OPEN_APP_HINT_RE.search(normalized))


def _has_explicit_route_seed(payload: Mapping[str, Any] | None) -> bool:
    data = dict(payload or {})
    route = _normalize_text(data.get("route"), lower=True)
    if route in _ROUTE_ALLOWLIST:
        return True
    if _normalize_text(data.get("target_id")):
        return True
    planned_steps = data.get("planned_steps")
    if not isinstance(planned_steps, list):
        planned_steps = data.get("steps")
    if isinstance(planned_steps, list) and len(planned_steps) > 0:
        return True
    action = _normalize_text(data.get("action"), lower=True)
    if action in {"work_code", "work_translate", "task_progress", "recent_turn"}:
        return True
    if action and action not in {"exec_intent", "narrative_only", "noop", "read_only_status"}:
        return True
    return False


def _build_step_payload(*, route: str, target_id: str, task_text: str, step_index: int, route_reason: str) -> dict[str, Any]:
    description = _normalize_text(task_text) or target_id or f"step-{step_index}"
    task_description = description
    if target_id == "hub_skill_search":
        query = _extract_hub_skill_search_query(task_text)
        if query:
            task_description = query
            description = f"search skill {query}"
    return {
        "step_index": step_index,
        "route": route,
        "target_id": target_id,
        "description": description,
        "task_description": task_description,
        "route_reason": route_reason,
        "allow_parallel": False,
        "risk_level": 1 if route == "skill" else 2,
    }


def _infer_supported_step_route(text: str) -> tuple[str, str, str] | None:
    cleaned = _strip_step_lead_in(text)
    if not cleaned:
        return None
    if _looks_like_companion_open_chat(cleaned):
        return None

    search_query = _extract_hub_skill_search_query(cleaned)
    if search_query:
        return ("skill", "hub_skill_search", "THALAMUS_RULE_HUB_SKILL_SEARCH")
    if _contains_any(cleaned, _HUB_SKILL_LIST_MARKERS):
        return ("skill", "hub_skill_list", "THALAMUS_RULE_HUB_SKILL_LIST")
    if _contains_any(cleaned, _DIRECT_SUMMARIZE_MARKERS) and _contains_any(cleaned, _DIRECT_SUMMARIZE_CONTEXT_MARKERS):
        return ("skill", "session_summary", "THALAMUS_RULE_SESSION_SUMMARY")
    if _contains_any(cleaned, _DIRECT_EMOTION_MARKERS) and not _looks_like_companion_open_chat(cleaned):
        return ("skill", "emotion_query", "THALAMUS_RULE_EMOTION_QUERY")
    if _contains_any(cleaned, _DIRECT_BOND_MARKERS):
        return ("skill", "bond_query", "THALAMUS_RULE_BOND_QUERY")
    if _contains_any(cleaned, _DIRECT_MEMORY_RECALL_MARKERS):
        return ("skill", "memory_recall", "THALAMUS_RULE_MEMORY_RECALL")

    routed = classify_task_intent_by_rules(cleaned)
    if isinstance(routed, dict):
        route = _normalize_text(routed.get("route"), lower=True)
        target_id = _normalize_text(routed.get("target_id"))
        reason_code = _normalize_text(routed.get("reason_code")) or "THALAMUS_RULE_SEGMENT"
        if route in {"skill", "delegate"} and target_id:
            return (route, target_id, reason_code)
    return None


def _split_explicit_step_segments(text: str) -> list[str]:
    cleaned = _strip_step_lead_in(text)
    if not cleaned:
        return []

    candidate_splits: list[list[str]] = []
    for marker, pattern in _EXPLICIT_MULTI_STEP_SPLITTERS:
        if marker not in _normalize_match_text(cleaned):
            continue
        parts = [part.strip(" \t,，;；:：") for part in pattern.split(cleaned) if part.strip(" \t,，;；:：")]
        if len(parts) > 1:
            candidate_splits.append(parts)

    if "再" in cleaned:
        parts = [part.strip(" \t,，;；:：") for part in cleaned.split("再") if part.strip(" \t,，;；:：")]
        if len(parts) > 1:
            candidate_splits.append(parts)

    for parts in candidate_splits:
        if all(_infer_supported_step_route(part) is not None for part in parts):
            return parts
    return []


def _build_direct_route_seed(text: str) -> dict[str, Any] | None:
    cleaned = _strip_step_lead_in(text)
    if not cleaned:
        return None

    if _looks_like_open_app_request(cleaned):
        return {
            "route": "skill",
            "target_id": "open_app",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_OPEN_APP",
            "confidence": 0.97,
            "route_source": "thalamus.rule",
            "args": {
                "query": cleaned,
                "text": cleaned,
                "user_message": cleaned,
            },
        }

    if _looks_like_companion_open_chat(cleaned):
        return {
            "route": "chat",
            "target_id": "",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_CONTEXT_CHAT",
            "confidence": 0.9,
            "route_source": "thalamus.rule",
            "args": {
                "text": cleaned,
            },
        }

    codex_runtime_mode = _infer_codex_runtime_mode(cleaned)
    if codex_runtime_mode:
        reason_code = (
            "THALAMUS_RULE_CODEX_SELF_EVOLVE"
            if codex_runtime_mode == "self_evolve"
            else "THALAMUS_RULE_CODEX_SELF_HEAL"
        )
        return {
            "route": "skill",
            "target_id": "codex_codegen",
            "task": cleaned,
            "reason_code": reason_code,
            "confidence": 0.98,
            "route_source": "thalamus.rule",
            "args": {
                "task": cleaned,
                "problem_statement": cleaned,
                "run_mode": codex_runtime_mode,
                "workspace_strategy": "runtime_repo",
                "include_dependency_probes": True,
                "text": cleaned,
                "user_message": cleaned,
            },
        }

    phatic_reason = _detect_short_companion_chat_reason(cleaned)
    if phatic_reason:
        return {
            "route": "chat",
            "target_id": "",
            "task": cleaned,
            "reason_code": phatic_reason,
            "confidence": 0.99,
            "route_source": "thalamus.rule",
            "args": {
                "text": cleaned,
            },
        }

    if _TASK_INTENT_PLANNING_RE.search(cleaned):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": cleaned,
            "reason_code": "RULE_FORCE_INTENT_AGENT",
            "confidence": 0.94,
            "route_source": "rule",
        }

    step_segments = _split_explicit_step_segments(cleaned)
    if len(step_segments) > 1:
        planned_steps: list[dict[str, Any]] = []
        for index, segment in enumerate(step_segments, start=1):
            inferred = _infer_supported_step_route(segment)
            if inferred is None:
                planned_steps = []
                break
            route, target_id, reason_code = inferred
            planned_steps.append(
                _build_step_payload(
                    route=route,
                    target_id=target_id,
                    task_text=segment,
                    step_index=index,
                    route_reason=reason_code,
                )
            )
        if len(planned_steps) > 1:
            return {
                "route": "plan",
                "target_id": "skill_dag",
                "task": cleaned,
                "reason_code": "THALAMUS_RULE_EXPLICIT_PLAN",
                "confidence": 0.99,
                "route_source": "thalamus.rule",
                "planned_steps": planned_steps,
            }

    search_query = _extract_hub_skill_search_query(cleaned)
    if search_query:
        return {
            "route": "skill",
            "target_id": "hub_skill_search",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_HUB_SKILL_SEARCH",
            "confidence": 0.99,
            "route_source": "thalamus.rule",
            "args": {
                "query": search_query,
                "text": cleaned,
                "user_message": cleaned,
            },
        }

    if _contains_any(cleaned, _HUB_SKILL_LIST_MARKERS):
        return {
            "route": "skill",
            "target_id": "hub_skill_list",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_HUB_SKILL_LIST",
            "confidence": 0.99,
            "route_source": "thalamus.rule",
            "args": {
                "text": cleaned,
                "user_message": cleaned,
            },
        }

    if _contains_any(cleaned, _DIRECT_SUMMARIZE_MARKERS) and _contains_any(cleaned, _DIRECT_SUMMARIZE_CONTEXT_MARKERS):
        return {
            "route": "skill",
            "target_id": "session_summary",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_SESSION_SUMMARY",
            "confidence": 0.96,
            "route_source": "thalamus.rule",
        }

    if (
        _contains_any(cleaned, _DIRECT_EMOTION_MARKERS)
        and _contains_any(cleaned, _DIRECT_EMOTION_QUERY_MARKERS)
        and not _looks_like_companion_open_chat(cleaned)
    ):
        return {
            "route": "skill",
            "target_id": "emotion_query",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_EMOTION_QUERY",
            "confidence": 0.95,
            "route_source": "thalamus.rule",
        }

    if _contains_any(cleaned, _DIRECT_BOND_MARKERS) and _contains_any(cleaned, _DIRECT_BOND_QUERY_MARKERS):
        return {
            "route": "skill",
            "target_id": "bond_query",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_BOND_QUERY",
            "confidence": 0.95,
            "route_source": "thalamus.rule",
        }

    if _contains_any(cleaned, _DIRECT_MEMORY_RECALL_MARKERS):
        return {
            "route": "skill",
            "target_id": "memory_recall",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_MEMORY_RECALL",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
        }
    return None


def classify_task_intent_by_rules(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    direct_seed = _build_direct_route_seed(normalized)
    if isinstance(direct_seed, dict):
        return direct_seed

    compact = re.sub(r"\s+", "", normalized).lower()

    if _TASK_PROGRESS_QUERY_RE.search(compact):
        return {
            "route": "task_progress",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_TASK_PROGRESS",
            "confidence": 0.98,
            "route_source": "rule",
        }

    if _TASK_RECENT_TURN_RE.search(normalized):
        return {
            "route": "recent_turn",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_RECENT_TURN",
            "confidence": 0.95,
            "route_source": "rule",
        }

    if _TASK_VISION_UI_RE.search(normalized):
        return {
            "route": "delegate",
            "target_id": "vision_agent",
            "task": normalized,
            "reason_code": "RULE_VISION_UI",
            "confidence": 0.92,
            "route_source": "rule",
        }

    if _TASK_TRANSLATE_RE.search(normalized):
        return {
            "route": "delegate",
            "target_id": "translate_agent",
            "task": normalized,
            "reason_code": "RULE_FORCE_TRANSLATE_AGENT",
            "confidence": 0.95,
            "route_source": "rule",
        }

    if _TASK_INTENT_PLANNING_RE.search(normalized):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": normalized,
            "reason_code": "RULE_FORCE_INTENT_AGENT",
            "confidence": 0.94,
            "route_source": "rule",
        }

    if _TASK_MEMORY_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "memory_store",
            "task": normalized,
            "reason_code": "RULE_FORCE_MEMORY_STORE",
            "confidence": 0.95,
            "route_source": "rule",
        }

    if _TASK_CODE_RE.search(normalized) and not _TASK_ENTERTAINMENT_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "codex_codegen",
            "task": normalized,
            "reason_code": "RULE_FORCE_CODEX_CODEGEN",
            "confidence": 0.93,
            "route_source": "rule",
        }

    if normalized.startswith(("/", "!", "run ", "exec ")):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": normalized,
            "reason_code": "RULE_ACTION_PREFIX",
            "confidence": 0.90,
            "route_source": "rule",
        }

    compact_lower = normalized.lower().replace(" ", "")
    if any(marker in compact_lower for marker in _TASK_EXEC_ACTION_MARKERS):
        return None
    return None


def _build_multimodal_route_seed(
    text: str,
    *,
    attachments: Sequence[Mapping[str, Any]] | None = None,
    multimodal_observations: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    attachment_items = [
        dict(item)
        for item in list(attachments or [])
        if isinstance(item, Mapping) and _normalize_text(item.get("type"))
    ]
    observation_payload = dict(multimodal_observations or {})
    if not attachment_items and not observation_payload:
        return None
    task_text = normalized or "帮我看看这个附件。"
    return {
        "route": "chat",
        "target_id": "",
        "task": task_text,
        "reason_code": "THALAMUS_RULE_MULTIMODAL_CHAT",
        "confidence": 0.97,
        "route_source": "thalamus.rule",
        "multimodal_input": True,
        "args": {
            "text": task_text,
        },
    }


async def classify_task_intent_by_llm(
    *,
    request_id: str,
    session_id: str,
    text: str,
    tribe_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        import os  # noqa: PLC0415

        if str(os.environ.get("HOST_CORTEX_INTENT_LLM_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
            return None

        from collections.abc import Mapping as _Mapping  # noqa: PLC0415

        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            resolve_executive_router_llm_runtime,
            resolve_host_llm_runtime_async,
        )
        from host_brain.app.input_service_gateway import extract_first_json_object  # noqa: PLC0415
        from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged  # noqa: PLC0415
        from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415

        llm_runtime = await resolve_host_llm_runtime_async(rollout_key=session_id, scene_type="EXEC_INTENT")
        router_runtime = resolve_executive_router_llm_runtime(base_runtime=llm_runtime)
        if not bool(router_runtime.get("available")):
            return None

        provider_name = str(router_runtime.get("provider_name") or "").strip()
        model_id = str(router_runtime.get("model_id") or "").strip()
        api_key = str(router_runtime.get("llm_api_key") or "").strip()
        if not provider_name or not model_id or not api_key:
            return None

        ctx = dict(tribe_context or {})
        vital_signs = dict(ctx.get("vital_signs") or {})
        bond_stage = str(vital_signs.get("bond_stage") or ctx.get("bond_stage") or "stranger").strip()
        emotion_label = str(vital_signs.get("emotion_label") or ctx.get("emotion_label") or "neutral").strip()
        tribe_summary = f"bond_stage={bond_stage}, emotion={emotion_label}"

        raw_templates = _resolve_intent_classification_prompt_templates()
        templates = dict(raw_templates) if isinstance(raw_templates, _Mapping) else {}
        system_prompt = str(templates.get("system_prompt") or "").strip()
        user_prompt_template = str(templates.get("user_prompt_template") or "").strip()
        if not system_prompt or not user_prompt_template:
            return None
        user_prompt = user_prompt_template.format(tribe_summary=tribe_summary, text=str(text or "").strip())

        timeout_ms = int(os.environ.get("HOST_CORTEX_INTENT_LLM_TIMEOUT_MS", "4000"))
        base_url_override = str(router_runtime.get("base_url_override") or "").strip() or None
        agent_gen_params = dict(router_runtime.get("agent_gen_params") or {"temperature": 0.1, "top_p": 0.8})

        raw_chunks: list[str] = []
        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            gen_params=agent_gen_params,
            base_url_override=base_url_override,
        ):
            raw_chunks.append(str(chunk or ""))
            if sum(len(item) for item in raw_chunks) >= 4000:
                break

        raw_text = "".join(raw_chunks).strip()
        parsed = extract_first_json_object(raw_text)
        if not isinstance(parsed, dict):
            _logger.warning("[%s] thalamus_intent_llm_parse_failed output=%.180s", request_id, raw_text)
            return None

        route = str(parsed.get("route") or "").strip().lower()
        if route not in {"chat", "delegate", "skill", "task_progress", "recent_turn"}:
            return None

        target_id = str(parsed.get("target_id") or "").strip()
        task = str(parsed.get("task") or text or "").strip()
        reason = str(parsed.get("reason") or "LLM_INTENT_ROUTE").strip() or "LLM_INTENT_ROUTE"
        try:
            confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0.7)))
        except (TypeError, ValueError):
            confidence = 0.7

        return {
            "route": route,
            "target_id": target_id,
            "task": task,
            "reason_code": reason,
            "confidence": confidence,
            "route_source": "llm",
        }
    except Exception as exc:
        _logger.warning("thalamus_intent_llm_failed request_id=%s error=%s", request_id, exc)
        return None


async def classify_task_intent(
    *,
    request_id: str,
    session_id: str,
    text: str,
    tribe_context: Mapping[str, Any] | None = None,
    attachments: Sequence[Mapping[str, Any]] | None = None,
    multimodal_observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    import os as _os  # noqa: PLC0415

    normalized = _normalize_text(text)
    multimodal_seed = _build_multimodal_route_seed(
        normalized,
        attachments=attachments,
        multimodal_observations=multimodal_observations,
    )
    if multimodal_seed is not None:
        return multimodal_seed

    prefrontal_enabled = _os.environ.get("PREFRONTAL_PLAN_ENABLED", "false").lower() == "true"
    if prefrontal_enabled:
        ctx = dict(tribe_context or {})
        prefrontal_plan = dict(ctx.get("prefrontal_plan") or {})
        if prefrontal_plan.get("action"):
            rule_result = classify_task_intent_by_rules(normalized)
            if rule_result is not None:
                return rule_result
            _logger.debug(
                "[%s] thalamus classify_task_intent: prefrontal_plan exists, skip LLM fallback action=%s",
                request_id,
                prefrontal_plan.get("action"),
            )
            return {
                "route": "chat",
                "target_id": "",
                "task": normalized,
                "reason_code": "PREFRONTAL_PLAN_BYPASS",
                "confidence": 0.95,
                "route_source": "prefrontal",
                "prefrontal_action": prefrontal_plan.get("action"),
            }

    rule_result = classify_task_intent_by_rules(normalized)
    if rule_result is not None:
        return rule_result

    llm_result = await classify_task_intent_by_llm(
        request_id=request_id,
        session_id=session_id,
        text=normalized,
        tribe_context=tribe_context,
    )
    if llm_result is not None:
        return llm_result

    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": "DEFAULT_CHAT",
        "confidence": 0.7,
        "route_source": "default",
    }


def _resolve_target_id(payload: Mapping[str, Any]) -> str:
    candidate = _normalize_text(payload.get("target_id"))
    if candidate:
        return candidate
    action = _normalize_text(payload.get("action"), lower=True)
    if action == "work_code":
        return "codex_codegen"
    if action == "work_translate":
        return "translate_agent"
    if action.endswith("_agent"):
        return action
    if action and action not in _CANONICAL_ACTIONS:
        return action
    return ""


def _resolve_route(payload: Mapping[str, Any], *, target_id: str) -> str:
    route = _normalize_text(payload.get("route"), lower=True)
    if route in _ROUTE_ALLOWLIST:
        return route

    planned_steps = payload.get("planned_steps")
    if not isinstance(planned_steps, list):
        planned_steps = payload.get("steps")
    if isinstance(planned_steps, list) and len(planned_steps) > 1:
        return "plan"

    action = _normalize_text(payload.get("action"), lower=True)
    if action in {"task_progress", "recent_turn"}:
        return action
    if action == "work_code":
        return "skill"
    if action == "work_translate":
        return "delegate"
    if action == "narrative_only":
        return "chat"
    if target_id.endswith("_agent"):
        return "delegate"
    if target_id:
        return "skill"
    return "chat"


def _resolve_action(*, route: str, target_id: str, fallback: Any) -> str:
    explicit = _normalize_text(fallback)
    if explicit:
        return explicit
    normalized_route = _normalize_text(route, lower=True)
    normalized_target = _normalize_text(target_id, lower=True)
    if normalized_route == "chat":
        return "narrative_only"
    if normalized_route == "skill" and normalized_target:
        return normalized_target
    if normalized_route == "delegate" and normalized_target == "translate_agent":
        return "work_translate"
    return "exec_intent"


def _build_default_args(*, route: str, target_id: str, text: str) -> dict[str, Any]:
    normalized_route = _normalize_text(route, lower=True)
    normalized_target = _normalize_text(target_id, lower=True)
    normalized_text = _normalize_text(text)
    if normalized_route == "chat":
        return {"text": normalized_text}
    if normalized_route == "skill":
        args = {
            "query": normalized_text,
            "text": normalized_text,
            "user_message": normalized_text,
        }
        if normalized_target == "memory_store":
            args["content"] = normalized_text
        return args
    return {
        "text": normalized_text,
        "user_message": normalized_text,
    }


def _backfill_canonical_args(
    *,
    route: str,
    target_id: str,
    text: str,
    args_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(args_payload or {})
    fallback_text = _normalize_text(
        merged.get("content")
        or merged.get("query")
        or merged.get("text")
        or merged.get("user_message")
        or text
    )
    normalized_route = _normalize_text(route, lower=True)
    normalized_target = _normalize_text(target_id, lower=True)

    if normalized_route == "skill":
        if not _normalize_text(merged.get("query")):
            merged["query"] = fallback_text
        if not _normalize_text(merged.get("text")):
            merged["text"] = fallback_text
        if not _normalize_text(merged.get("user_message")):
            merged["user_message"] = fallback_text
        if normalized_target == "memory_store" and not _normalize_text(merged.get("content")):
            merged["content"] = fallback_text
        if normalized_target == "codex_codegen":
            if not _normalize_text(merged.get("task")):
                merged["task"] = _normalize_text(merged.get("problem_statement")) or fallback_text
            if not _normalize_text(merged.get("problem_statement")):
                merged["problem_statement"] = _normalize_text(merged.get("task")) or fallback_text
            explicit_workspace = any(
                _normalize_text(merged.get(key))
                for key in ("workspace", "file_path", "path", "project_path", "repo_alias", "git_url")
            )
            explicit_workspace = explicit_workspace or bool(
                _normalize_text(merged.get("workspace_strategy"), lower=True)
                or merged.get("allow_temp_workspace")
            )
            if not _normalize_text(merged.get("run_mode"), lower=True):
                if _looks_like_lightweight_codex_request(_normalize_text(merged.get("task")) or fallback_text):
                    merged["run_mode"] = "inspect"
                else:
                    merged["run_mode"] = "worker"
            resolved_run_mode = _normalize_text(merged.get("run_mode"), lower=True)
            if not explicit_workspace:
                if resolved_run_mode == "inspect":
                    merged["allow_temp_workspace"] = True
                elif not _normalize_text(merged.get("workspace_strategy"), lower=True):
                    merged["workspace_strategy"] = "runtime_repo"
        return merged

    if normalized_route == "chat":
        if not _normalize_text(merged.get("text")):
            merged["text"] = fallback_text
        return merged

    return {
        "text": _normalize_text(merged.get("text")) or fallback_text,
    }


def _allocate_seq(*, session_id: str, existing_seq: Any) -> int:
    try:
        seq = int(existing_seq)
        if seq >= 0:
            return seq
    except (TypeError, ValueError):
        pass

    try:
        from host_brain.app.intent_seq import allocate_intent_seq  # noqa: PLC0415

        return int(allocate_intent_seq(session_id=session_id))
    except Exception:
        return 0


def _merge_preserving_truthy(primary: Mapping[str, Any] | None, secondary: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(primary or {})
    for key, value in dict(secondary or {}).items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged


def _deep_merge_json(base_value: Any, overlay_value: Any) -> Any:
    if isinstance(base_value, dict) and isinstance(overlay_value, dict):
        merged = {str(key): value for key, value in base_value.items()}
        for key, value in overlay_value.items():
            key_text = str(key)
            if key_text in merged:
                merged[key_text] = _deep_merge_json(merged[key_text], value)
            else:
                merged[key_text] = value
        return merged
    return overlay_value if overlay_value is not None else base_value


def _load_template_json_parts(template_id: str) -> dict[str, Any] | None:
    try:
        from host_brain.app.prompt_template_defs import get_prompt_template_definition  # noqa: PLC0415

        definition = get_prompt_template_definition(template_id)
    except Exception:
        return None

    default_raw = str(definition.default_content or "").strip()
    default_value = json.loads(default_raw) if default_raw else None
    override_value = None

    try:
        from host_brain.app.dependencies import get_prompt_template_store  # noqa: PLC0415

        template = get_prompt_template_store().get_template(template_id)
        if bool(template.get("has_override")) and bool(template.get("enabled")):
            override_raw = str(template.get("override_content") or "").strip()
            if override_raw:
                override_value = json.loads(override_raw)
    except Exception as exc:
        _logger.debug("thalamus prompt template override unavailable for %s: %s", template_id, exc)

    return {
        "template_id": template_id,
        "default": default_value,
        "override": override_value,
    }


def _resolve_intent_classification_prompt_templates() -> Any:
    primary = _load_template_json_parts(_INTENT_CLASSIFICATION_TEMPLATE_ID)
    if primary is None:
        return None
    resolved = primary.get("default")
    if primary.get("override") is not None:
        resolved = _deep_merge_json(resolved, primary.get("override"))
    return resolved


@dataclass(slots=True)
class ThalamusAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="thalamus_agent",
            family="thalamus",
            capabilities=("intent_compile", "schema_normalize", "handoff_pack"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def export_runtime_state(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("thalamus_agent") or {}
        return dict(snap.get("state") or {})

    async def classify_and_compile_intent_draft(
        self,
        *,
        request_id: str,
        session_id: str,
        user_message: str,
        person_id: str | None = None,
        raw_route_decision: Mapping[str, Any] | None = None,
        existing_intent_draft: Mapping[str, Any] | None = None,
        source: str = "thalamus_agent",
        input_source: str | None = None,
        ts_ms: int | None = None,
        target: str = "host",
        tribe_context: Mapping[str, Any] | None = None,
        attachments: Sequence[Mapping[str, Any]] | None = None,
        multimodal_observations: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        route_seed = dict(raw_route_decision or {})
        incoming = dict(existing_intent_draft or {})

        if not _has_explicit_route_seed(route_seed) and not _has_explicit_route_seed(incoming):
            route_seed = dict(
                await classify_task_intent(
                    request_id=request_id,
                    session_id=session_id,
                    text=user_message,
                    tribe_context=tribe_context,
                    attachments=attachments,
                    multimodal_observations=multimodal_observations,
                )
                or {}
            )

        return self.compile_intent_draft(
            request_id=request_id,
            session_id=session_id,
            user_message=user_message,
            person_id=person_id,
            raw_route_decision=route_seed,
            existing_intent_draft=incoming,
            source=source,
            input_source=input_source,
            ts_ms=ts_ms,
            target=target,
        )

    def compile_intent_draft(
        self,
        *,
        request_id: str,
        session_id: str,
        user_message: str,
        person_id: str | None = None,
        raw_route_decision: Mapping[str, Any] | None = None,
        existing_intent_draft: Mapping[str, Any] | None = None,
        source: str = "thalamus_agent",
        input_source: str | None = None,
        ts_ms: int | None = None,
        target: str = "host",
    ) -> dict[str, Any]:
        route_seed = dict(raw_route_decision or {})
        incoming = dict(existing_intent_draft or {})
        draft = _merge_preserving_truthy(route_seed, incoming)

        target_id = _resolve_target_id(draft)
        route = _resolve_route(draft, target_id=target_id)
        task = _normalize_text(draft.get("task")) or _normalize_text(user_message)
        route_reason = (
            _normalize_text(draft.get("route_reason"))
            or _normalize_text(draft.get("reason_code"))
            or "TRIBE_INTENT"
        )
        route_source = _normalize_text(draft.get("route_source")) or "rule"
        route_confidence = _normalize_confidence(draft.get("route_confidence"))
        if route_confidence is None:
            route_confidence = _normalize_confidence(draft.get("confidence"))
        action = _resolve_action(route=route, target_id=target_id, fallback=draft.get("action"))
        llm_route_used = bool(draft.get("llm_route_used")) or route_source == "llm"
        args_payload = dict(draft.get("args") or {}) if isinstance(draft.get("args"), dict) else {}
        if not args_payload:
            args_payload = _build_default_args(route=route, target_id=target_id, text=task or user_message)
        args_payload = _backfill_canonical_args(
            route=route,
            target_id=target_id,
            text=task or user_message,
            args_payload=args_payload,
        )
        if (
            _normalize_text(route, lower=True) == "skill"
            and _normalize_text(target_id, lower=True) == "codex_codegen"
            and _normalize_text(args_payload.get("run_mode"), lower=True) in {"self_heal", "self_evolve"}
        ):
            try:
                from host_brain.skills.codex_task_builder import build_codex_runtime_task_args  # noqa: PLC0415

                args_payload = build_codex_runtime_task_args(
                    request_id=request_id,
                    user_message=user_message,
                    run_mode=_normalize_text(args_payload.get("run_mode"), lower=True),
                    existing_args=args_payload,
                    include_dependency_probes=bool(args_payload.get("include_dependency_probes", True)),
                )
            except Exception as exc:
                _logger.debug("thalamus codex runtime task build degraded: %s", exc)

        compiled = dict(draft)
        compiled.update(
            {
                "schema_version": str(draft.get("schema_version") or _INTENT_SCHEMA_VERSION).strip() or _INTENT_SCHEMA_VERSION,
                "id": _normalize_text(draft.get("id")) or f"intent::{request_id}",
                "ts_ms": int(draft.get("ts_ms") or ts_ms or now_ms()),
                "session_id": _normalize_text(draft.get("session_id")) or _normalize_text(session_id),
                "seq": _allocate_seq(session_id=_normalize_text(session_id), existing_seq=draft.get("seq")),
                "person_id": _normalize_text(draft.get("person_id")) or _normalize_text(person_id) or None,
                "type": _normalize_text(draft.get("type")) or "query",
                "action": action,
                "route": route,
                "target_id": target_id,
                "task": task,
                "route_reason": route_reason,
                "reason_code": route_reason,
                "route_source": route_source,
                "route_confidence": route_confidence,
                "confidence": route_confidence,
                "llm_route_used": llm_route_used,
                "target": _normalize_text(draft.get("target")) or _normalize_text(target) or "host",
                "args": args_payload,
                "nonce": _normalize_text(draft.get("nonce")) or f"nonce::{request_id}",
                "source": _normalize_text(draft.get("source")) or _normalize_text(source) or "thalamus_agent",
            }
        )

        if input_source is not None:
            compiled["input_source"] = _normalize_text(draft.get("input_source")) or _normalize_text(input_source) or None
        elif "input_source" in draft:
            compiled["input_source"] = _normalize_text(draft.get("input_source")) or None

        try:
            from host_brain.tribe.language_system_agent import get_language_system_agent  # noqa: PLC0415

            language_understanding = get_language_system_agent().wernicke_understand(
                user_text=user_message,
                emotion_tag="NEUTRAL",
                bond_stage="stranger",
            )
        except Exception as exc:
            _logger.debug("thalamus language understanding degraded: %s", exc)
            language_understanding = {}

        extension_keys = sorted(
            key
            for key in compiled.keys()
            if key
            not in {
                "schema_version",
                "id",
                "ts_ms",
                "session_id",
                "seq",
                "person_id",
                "type",
                "action",
                "route",
                "target_id",
                "task",
                "route_reason",
                "reason_code",
                "route_source",
                "route_confidence",
                "confidence",
                "llm_route_used",
                "target",
                "args",
                "nonce",
                "source",
                "input_source",
            }
        )
        compiled["_thalamus_meta"] = {
            "compiled_by": "thalamus_agent",
            "family": "thalamus",
            "language_intent_type": _normalize_text(language_understanding.get("intent_type")) or None,
            "language_confidence": _normalize_confidence(language_understanding.get("confidence")),
            "extension_keys": extension_keys,
            "route_seed_source": _normalize_text(route_seed.get("route_source")) or None,
            "ts_ms": int(now_ms()),
        }

        try:
            self.state_registry.write(
                namespace="thalamus_agent",
                state={
                    "last_intent_draft": compiled,
                    "last_route": route,
                    "last_target_id": target_id,
                    "last_action": action,
                    "last_extension_keys": extension_keys,
                    "last_updated_ts_ms": int(now_ms()),
                },
                writer="thalamus_agent",
                merge=True,
                reason="compile_intent_draft",
            )
        except Exception:
            pass

        emit_tribe_event(
            event_type="INTENT_DRAFT_COMPILED",
            source_agent="thalamus_agent",
            target_agent="executive_router",
            payload={
                "request_id": _normalize_text(request_id),
                "session_id": _normalize_text(session_id),
                "route": route,
                "target_id": target_id,
                "action": action,
                "extension_keys": extension_keys,
            },
        )
        return compiled


_THALAMUS_AGENT: ThalamusAgent | None = None


def get_thalamus_agent() -> ThalamusAgent:
    global _THALAMUS_AGENT
    if _THALAMUS_AGENT is None:
        _THALAMUS_AGENT = ThalamusAgent()
    return _THALAMUS_AGENT


def set_thalamus_agent(agent: ThalamusAgent | None) -> None:
    global _THALAMUS_AGENT
    _THALAMUS_AGENT = agent


__all__ = [
    "ThalamusAgent",
    "classify_task_intent",
    "classify_task_intent_by_llm",
    "classify_task_intent_by_rules",
    "get_thalamus_agent",
    "set_thalamus_agent",
]
