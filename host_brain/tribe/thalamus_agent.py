from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from host_brain.app.prompt_builder import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)
from host_brain.app.relational_chat_detector import (
    build_relational_chat_reason_code,
    detect_relational_chat_topic,
)
from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

_logger = logging.getLogger("Natsume.thalamus_agent")

_INTENT_SCHEMA_VERSION = "Natsume.intent.v1"
_INTENT_CLASSIFICATION_TEMPLATE_ID = "thalamus_agent.intent_classification_prompts"
THALAMUS_SCENE_TYPE_SOURCE = "thalamus_agent"
_ROUTE_ALLOWLIST = {"chat", "delegate", "skill", "plan", "task_progress", "recent_turn"}
_LLM_ROUTE_MIN_CONFIDENCE_DEFAULT = 0.72
_LLM_CHATLIKE_ROUTES: frozenset[str] = frozenset({"chat", "task_progress", "recent_turn"})
_LLM_DELEGATE_TARGET_ALLOWLIST: frozenset[str] = frozenset({
    "emotion_agent",
    "intent_agent",
    "retrieval_agent",
    "translate_agent",
    "vision_agent",
})
_LLM_SKILL_TARGET_ALLOWLIST: frozenset[str] = frozenset({
    "bond_query",
    "browser_navigate",
    "browser_scrape",
    "browser_screenshot",
    "codex_dream_compile",
    "codex_inspect",
    "codex_organ_scan",
    "codex_persona_drift_audit",
    "codex_research",
    "codex_self_evolve",
    "codex_self_heal",
    "codex_skill_forge",
    "codex_sql_audit",
    "codex_translate_project",
    "codex_worker",
    "emotion_query",
    "frontend_component_value_audit",
    "frontend_route_audit",
    "hub_skill_list",
    "hub_skill_search",
    "file_list",
    "file_read",
    "memory_consolidate",
    "memory_preference_extract",
    "memory_recall",
    "memory_store",
    "network_status",
    "open_app",
    "process_list",
    "repo_health_scan",
    "session_summary",
    "system_info",
    "test_failure_triage",
})
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
_MEMORY_PREFERENCE_EXTRACT_RE = re.compile(
    r"((提取|抽取|识别|分析|整理).{0,12}(偏好|喜好|习惯|交互风格|禁忌)"
    r"|(?:extract|mine|identify).{0,12}(?:preference|preferences|habit|style))",
    re.IGNORECASE,
)
_MEMORY_CONSOLIDATE_RE = re.compile(
    r"((记忆管家|整理.{0,12}记忆|沉淀.{0,12}记忆|记忆候选|长期记忆候选|整理成记忆)"
    r"|(?:consolidate|summari[sz]e).{0,12}(?:memory|memories|memory candidates?))",
    re.IGNORECASE,
)
_REPO_HEALTH_SCAN_RE = re.compile(
    r"((仓库|repo|repository|项目).{0,12}(医生|体检|健康|扫描|自检|状态检查|health)"
    r"|(?:repo|repository)\s+(?:doctor|health|scan|check))",
    re.IGNORECASE,
)
_TEST_FAILURE_TRIAGE_RE = re.compile(
    r"((分析|归因|triage).{0,16}(测试|单测|pytest|unittest).{0,16}(失败|报错|日志|输出)"
    r"|(测试|单测|pytest|unittest).{0,16}(失败日志|失败输出|报错日志|报错输出|归因|triage)"
    r"|(?:triage|analy[sz]e).{0,16}(?:test|pytest).{0,12}(?:failure|error|log))",
    re.IGNORECASE,
)
_FRONTEND_ROUTE_AUDIT_RE = re.compile(
    r"((前端|页面|ui|UI|monitor).{0,18}(路由|导航|断链|覆盖|巡检|审计|检查)"
    r"|(路由|导航).{0,18}(断链|覆盖|巡检|审计|检查)"
    r"|(?:frontend|route|navigation).{0,18}(?:audit|coverage|broken|check))",
    re.IGNORECASE,
)
_FRONTEND_COMPONENT_VALUE_AUDIT_RE = re.compile(
    r"((前端|页面|ui|UI|组件|卡片|布局).{0,18}(低价值|无意义|重复|整合|组件|卡片|布局|优化|巡检|审计)"
    r"|(?:frontend|component|card|layout|ux).{0,18}(?:audit|duplicate|consolidat|low-value|optimi[sz]e))",
    re.IGNORECASE,
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
_TASK_CODE_NEGATION_RE = re.compile(
    r"(不要|不用|不需要|先别|别|无需).{0,6}(改代码|写代码|代码|实现|开发)|"
    r"(no code changes|do not (?:write|change|modify) code|don't (?:write|change|modify) code)",
    re.IGNORECASE,
)
_TASK_VISION_UI_RE = re.compile(
    r"(screenshot|screen shot|截图|屏幕|当前界面|这个界面|看一下屏幕|看看这个界面|desktop|window|interface|capture)",
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
_TASK_PLAN_REQUEST_RE = re.compile(
    r"((给我|帮我|请|请你|麻烦|制定|出|写|整理|规划).{0,12}(方案|路线图|计划|plan|roadmap)"
    r"|有没有.{0,12}(前端|后端|系统|仓库|项目|主链|路由|意图识别|优化).{0,12}(方案|路线图|计划|plan|roadmap))",
    re.IGNORECASE,
)
_TASK_CONTEXT_CONTINUATION_RE = re.compile(
    r"^\s*(开始吧|开始|继续|继续吧|接着|接着做|继续做|接下来呢|接下来|然后呢|后面呢|开工|go ahead|start|continue|proceed)\s*[。.!！?？]*\s*$",
    re.IGNORECASE,
)
_TASK_OPEN_APP_VERB_RE = re.compile(r"(打开|启动|运行|开一下|帮我开|替我开|launch|open)", re.IGNORECASE)
_TASK_OPEN_APP_HINT_RE = re.compile(
    r"(本地|应用|软件|程序|app|设置|系统设置|酷狗|kugou|qq音乐|网易云|spotify|浏览器|微信|qq|音乐)",
    re.IGNORECASE,
)
_TASK_URL_RE = re.compile(r"https?://[^\s\]）)>,，。；;!?！？\"']+", re.IGNORECASE)
_TASK_BROWSER_NAVIGATE_RE = re.compile(
    r"(打开|访问|浏览|查看|看看|进入|open|visit|browse|navigate)",
    re.IGNORECASE,
)
_TASK_BROWSER_SCRAPE_RE = re.compile(
    r"(抓取|读取|提取|网页文本|页面文本|页面内容|scrape|extract|read)",
    re.IGNORECASE,
)
_TASK_SYSTEM_INFO_RE = re.compile(
    r"((系统|本机|机器|主机|电脑|host|machine).{0,8}(状态|信息|资源|配置|运行情况|概况|负载)"
    r"|(?:system|host|machine)\s+(?:info|status|overview|health))",
    re.IGNORECASE,
)
_TASK_PROCESS_LIST_RE = re.compile(
    r"((列出|查看|看看|读取|当前|运行中).{0,8}(进程|process)"
    r"|(?:process\s+list|running\s+processes|list\s+processes))",
    re.IGNORECASE,
)
_TASK_NETWORK_STATUS_RE = re.compile(
    r"((检查|查看|看看|检测|测试).{0,8}(网络|联网|连通|连通性)"
    r"|网络.{0,4}(状态|连通|检测)"
    r"|(?:network|connectivity).{0,8}(?:status|check|test))",
    re.IGNORECASE,
)
_PHATIC_GREETING_RE = re.compile(
    r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好|hello|hi|hey|yo)[!！。…~]*$",
    re.IGNORECASE,
)
_PHATIC_PRESENCE_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_PHATIC_ACK_RE = re.compile(r"^(嗯|嗯嗯|哦|噢|好的|好呀|好啊|可以|可以啊|可以呀|收到|对|对啊|是的|行)[!！。…~]*$")
_PHATIC_PUNCT_RE = re.compile(r"^[?？!！。…~、,.，]{1,4}$")
_PHATIC_COMFORT_RE = re.compile(
    r"(抱抱我|抱抱|安慰我|哄哄我|陪陪我|想被抱抱|想要抱抱|我好累|好累啊|有点累|有点困|有点难过|有点委屈|想哭|心情不好|不太开心|不开心|累死了)",
    re.IGNORECASE,
)
_PHATIC_SOCIAL_FOLLOWUP_RE = re.compile(
    r"(我不委屈|不委屈|我没委屈|我不难过|我没难过|才没有|哪有|(?:没有|没|不是|不).{0,4}(?:闹别扭|委屈|难过|生气|不高兴|撒娇|伤心|emo|哭)|怎么了啊|怎么啦啊|怎么了呀|怎么啦呀|怎么了嘛|怎么啦嘛|没什么是怎么了|没什么啦|没什么呀|没什么啊|没事啊|没事啦)"
)
_PHATIC_CONNECTIVITY_EXACT: frozenset[str] = frozenset(
    {
        "测试",
        "测试一下",
        "测试下",
        "test",
        "ping",
    }
)
_PHATIC_CONNECTIVITY_RE = re.compile(
    r"(测试(?:一下|下)?(?:现在)?(?:能不能|能|会不会|会|可以)?(?:回|回复|收到)"
    r"|(?:你)?(?:能不能|能|会不会|会|可以)(?:回|回复)(?:我)?"
    r"|(?:你)?(?:能不能|能|可以)?收到(?:吗|么)?"
    r"|有反应(?:吗|么)?|回我一下|回复一下|在线(?:吗|么)?)",
    re.IGNORECASE,
)
_PHATIC_CONNECTIVITY_BLOCKING_RE = re.compile(
    r"(单测|测试用例|用例|pytest|unittest|跑.*测试|测试.*(?:接口|函数|代码|脚本|服务|模块|功能|api|API)"
    r"|(?:接口|函数|代码|脚本|服务|模块|功能|api|API).{0,8}测试"
    r"|报错|错误|异常|失败|修复|排查|验证|仓库|文件|host|edge|gateway|onebot|build|compile)",
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
_GENERIC_ACTION_INTENT_MARKERS: tuple[str, ...] = (
    "继续执行",
    "执行",
    "帮我做",
    "帮忙做",
    "设置",
    "部署",
    "创建",
    "新建",
    "提交",
    "保存",
    "安装",
    "配置",
    "启动",
    "停止",
    "重启",
    "调用",
    "查询",
    "查一下",
    "查下",
    "检索",
    "搜索",
    "总结",
    "归纳",
    "整理",
    "润色",
    "改写",
    "生成",
    "安排",
    "规划",
    "run",
    "execute",
    "setup",
    "deploy",
    "build",
    "submit",
    "save",
    "create",
    "install",
    "configure",
    "search",
    "summarize",
    "rewrite",
    "polish",
    "plan",
)
_TASK_ENTERTAINMENT_RE = re.compile(
    r"(动漫|漫画|番剧|动画|电影|电视剧|剧集|综艺|音乐|歌曲|歌手|专辑|这首歌|歌|小说|读书|游戏|爱好|兴趣|趣味)",
    re.IGNORECASE,
)
_WORKSPACE_DOMAIN_RE = re.compile(
    r"(仓库|代码库|项目|文件|目录|路径|主链|断链|闭环|器官族|前端|页面|组件|布局|卡片|路由|意图识别|thalamus"
    r"|gateway|asr|vts|soul\.md|\.md\b|repo|repository|workspace|project|file|frontend|browser|playwright"
    r"|headless|route|routing|intent)",
    re.IGNORECASE,
)
_WORKSPACE_WRITE_RE = re.compile(
    r"(开始|修复|优化|整理|清理|删除|移除|接入|重塑|重构|实现|修改|改造|创建|新建|提交|继续整理|继续.*提交"
    r"|fix|optimi[sz]e|clean|remove|delete|refactor|implement|modify|commit)",
    re.IGNORECASE,
)
_WORKSPACE_INSPECT_RE = re.compile(
    r"(查看|看|验证|检查|分析|评估|梳理|审查|报告|完整|完成情况|接入情况"
    r"|inspect|verify|check|analy[sz]e|review|audit|status)",
    re.IGNORECASE,
)
_WORKSPACE_ACTIVE_VERIFY_RE = re.compile(
    r"(无头浏览器|浏览器|playwright|前端效果|整个前端|跑测试|测试|pytest|单测|headless|browser|test)",
    re.IGNORECASE,
)
_WORKSPACE_GIT_ACTION_RE = re.compile(
    r"(整理提交|提交代码|提交仓库|提交到仓库|git\s+commit|commit)",
    re.IGNORECASE,
)
_SIMPLE_TECH_ADVICE_RE = re.compile(
    r"(报错|错误|异常|失败|缺包|导入|import|ModuleNotFoundError|No module named|端口|port|日志|启动|连接不上|连不上"
    r"|没响应|无响应|timeout|http://|https://|bolt://|neo4j|chroma|chromadb|OPENAI_API_KEY|环境变量)",
    re.IGNORECASE,
)
_SIMPLE_TECH_QUESTION_RE = re.compile(r"(怎么|如何|怎么办|该|排查|查|先看|看什么|先试|试什么|\?|？)", re.IGNORECASE)
_SIMPLE_TECH_ACTION_BLOCKERS: tuple[str, ...] = (
    "开始处理",
    "开始做",
    "继续做",
    "直接改",
    "帮我改",
    "修改",
    "修复",
    "实现",
    "重构",
    "删除",
    "清理",
    "整理仓库",
    "提交",
    "跑测试",
    "执行",
    "接入",
    "迁移",
    "写代码",
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


def _looks_like_simple_technical_advice(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    compact_lower = normalized.lower().replace(" ", "")
    if any(marker in compact_lower for marker in _SIMPLE_TECH_ACTION_BLOCKERS):
        return False
    return bool(_SIMPLE_TECH_ADVICE_RE.search(normalized) and _SIMPLE_TECH_QUESTION_RE.search(normalized))


def _looks_like_generic_action_intent(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    compact = normalized.lower().replace(" ", "")
    return any(marker.lower().replace(" ", "") in compact for marker in _GENERIC_ACTION_INTENT_MARKERS)


def _build_simple_technical_chat_seed(text: str, *, route_source: str) -> dict[str, Any]:
    normalized = _normalize_text(text)
    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": "THALAMUS_RULE_SIMPLE_TECH_CHAT",
        "confidence": 0.98,
        "route_source": route_source,
        "args": {
            "text": normalized,
        },
    }


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    normalized = _normalize_match_text(text)
    return any(marker in normalized for marker in markers)


def _normalize_phatic_match_text(value: Any) -> str:
    return re.sub(r"[\s，。！？,.!?~～…]+", "", str(value or "").strip().lower())


def _detect_short_companion_chat_reason(text: str) -> str | None:
    cleaned = _strip_step_lead_in(text)
    if not cleaned:
        return None

    relational_reason = build_relational_chat_reason_code(cleaned)
    if relational_reason:
        return relational_reason

    if _PHATIC_PUNCT_RE.match(cleaned):
        return "THALAMUS_RULE_PHATIC_PUNCT"

    compact = _normalize_phatic_match_text(cleaned)
    if not compact or len(compact) > _PHATIC_FASTPATH_MAX_CHARS:
        return None

    if (
        compact in _PHATIC_CONNECTIVITY_EXACT or _PHATIC_CONNECTIVITY_RE.search(compact)
    ) and not _PHATIC_CONNECTIVITY_BLOCKING_RE.search(cleaned):
        return "THALAMUS_RULE_CONNECTIVITY_CHECK"

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
    if _PHATIC_SOCIAL_FOLLOWUP_RE.search(cleaned):
        return "THALAMUS_RULE_PHATIC_SOCIAL_FOLLOWUP"
    try:
        from host_brain.app.context.interaction_thread import detect_assistant_reply_followup_kind  # noqa: PLC0415

        if detect_assistant_reply_followup_kind(cleaned):
            return "THALAMUS_RULE_ASSISTANT_REPLY_FOLLOWUP"
    except Exception:
        pass
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


def _build_codex_workspace_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    has_git_action = bool(_WORKSPACE_GIT_ACTION_RE.search(normalized))
    if not _WORKSPACE_DOMAIN_RE.search(normalized) and not has_git_action:
        return None
    has_write = bool(_WORKSPACE_WRITE_RE.search(normalized))
    has_inspect = bool(_WORKSPACE_INSPECT_RE.search(normalized))
    has_active_verify = bool(_WORKSPACE_ACTIVE_VERIFY_RE.search(normalized))
    if not (has_write or has_inspect or has_active_verify or has_git_action):
        return None

    target_id = "codex_worker" if has_write or has_active_verify or has_git_action else "codex_inspect"
    run_mode = "worker" if target_id == "codex_worker" else "inspect"
    return {
        "route": "skill",
        "target_id": target_id,
        "task": normalized,
        "reason_code": f"THALAMUS_RULE_CODEX_WORKSPACE_{run_mode.upper()}",
        "confidence": 0.94 if target_id == "codex_worker" else 0.91,
        "route_source": "thalamus.rule",
        "args": {
            "task": normalized,
            "problem_statement": normalized,
            "run_mode": run_mode,
            "workspace_strategy": "runtime_repo",
            "text": normalized,
            "user_message": normalized,
        },
    }


def _build_system_observation_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    if _TASK_PROCESS_LIST_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "process_list",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_PROCESS_LIST",
            "confidence": 0.95,
            "route_source": "thalamus.rule",
            "args": {
                "limit": 20,
                "sort_by": "pid",
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _TASK_NETWORK_STATUS_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "network_status",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_NETWORK_STATUS",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "host": "8.8.8.8",
                "port": 53,
                "timeout_ms": 1500,
                "get_public_ip": False,
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _TASK_SYSTEM_INFO_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "system_info",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_SYSTEM_INFO",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "include_cpu": True,
                "include_memory": True,
                "include_disk": True,
                "include_network": False,
                "include_gpu": False,
                "text": normalized,
                "user_message": normalized,
            },
        }
    return None


def _extract_first_url(text: str) -> str:
    match = _TASK_URL_RE.search(_normalize_text(text))
    return match.group(0).rstrip(".,;:!?，。；：！？") if match else ""


def _build_browser_observation_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    url = _extract_first_url(normalized)
    if not url:
        return None

    if _TASK_BROWSER_SCRAPE_RE.search(normalized):
        return {
            "route": "plan",
            "target_id": "skill_dag",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_BROWSER_SCRAPE_PLAN",
            "confidence": 0.95,
            "route_source": "thalamus.rule",
            "planned_steps": [
                {
                    "step_index": 1,
                    "route": "skill",
                    "target_id": "browser_navigate",
                    "description": f"open {url}",
                    "task_description": normalized,
                    "route_reason": "THALAMUS_RULE_BROWSER_NAVIGATE",
                    "allow_parallel": False,
                    "risk_level": 2,
                    "args": {
                        "url": url,
                        "wait_until": "load",
                        "timeout_ms": 10000,
                    },
                },
                {
                    "step_index": 2,
                    "route": "skill",
                    "target_id": "browser_scrape",
                    "description": "scrape page text",
                    "task_description": normalized,
                    "route_reason": "THALAMUS_RULE_BROWSER_SCRAPE",
                    "allow_parallel": False,
                    "risk_level": 2,
                    "args": {
                        "selector": "body",
                        "extract": "text",
                        "limit": 20,
                    },
                },
            ],
        }
    if _TASK_BROWSER_NAVIGATE_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "browser_navigate",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_BROWSER_NAVIGATE",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "url": url,
                "wait_until": "load",
                "timeout_ms": 10000,
                "text": normalized,
                "user_message": normalized,
            },
        }
    return None


def _build_memory_steward_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _MEMORY_PREFERENCE_EXTRACT_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "memory_preference_extract",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_MEMORY_PREFERENCE_EXTRACT",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "text": normalized,
                "max_candidates": 8,
                "source": "thalamus",
                "user_message": normalized,
            },
        }
    if _MEMORY_CONSOLIDATE_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "memory_consolidate",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_MEMORY_CONSOLIDATE",
            "confidence": 0.93,
            "route_source": "thalamus.rule",
            "args": {
                "text": normalized,
                "max_candidates": 10,
                "include_preferences": True,
                "source": "thalamus",
                "user_message": normalized,
            },
        }
    return None


def _build_repo_doctor_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _TEST_FAILURE_TRIAGE_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "test_failure_triage",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_TEST_FAILURE_TRIAGE",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "output": normalized,
                "text": normalized,
                "max_failures": 12,
                "user_message": normalized,
            },
        }
    if _REPO_HEALTH_SCAN_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "repo_health_scan",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_REPO_HEALTH_SCAN",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "include_git": True,
                "include_tests": True,
                "include_python": True,
                "run_tests": False,
                "text": normalized,
                "user_message": normalized,
            },
        }
    return None


def _build_frontend_audit_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _FRONTEND_ROUTE_AUDIT_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "frontend_route_audit",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_FRONTEND_ROUTE_AUDIT",
            "confidence": 0.94,
            "route_source": "thalamus.rule",
            "args": {
                "include_static": False,
                "max_files": 800,
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _FRONTEND_COMPONENT_VALUE_AUDIT_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "frontend_component_value_audit",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_FRONTEND_COMPONENT_VALUE_AUDIT",
            "confidence": 0.93,
            "route_source": "thalamus.rule",
            "args": {
                "min_repeated_label_count": 5,
                "max_card_surfaces_per_file": 35,
                "max_files": 800,
                "text": normalized,
                "user_message": normalized,
            },
        }
    return None


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
    memory_steward_seed = _build_memory_steward_route_seed(cleaned)
    if memory_steward_seed is not None:
        return (
            "skill",
            _normalize_text(memory_steward_seed.get("target_id")),
            _normalize_text(memory_steward_seed.get("reason_code")) or "THALAMUS_RULE_MEMORY_STEWARD",
        )
    repo_doctor_seed = _build_repo_doctor_route_seed(cleaned)
    if repo_doctor_seed is not None:
        return (
            "skill",
            _normalize_text(repo_doctor_seed.get("target_id")),
            _normalize_text(repo_doctor_seed.get("reason_code")) or "THALAMUS_RULE_REPO_DOCTOR",
        )
    frontend_audit_seed = _build_frontend_audit_route_seed(cleaned)
    if frontend_audit_seed is not None:
        return (
            "skill",
            _normalize_text(frontend_audit_seed.get("target_id")),
            _normalize_text(frontend_audit_seed.get("reason_code")) or "THALAMUS_RULE_FRONTEND_AUDIT",
        )
    system_seed = _build_system_observation_route_seed(cleaned)
    if system_seed is not None:
        return (
            "skill",
            _normalize_text(system_seed.get("target_id")),
            _normalize_text(system_seed.get("reason_code")) or "THALAMUS_RULE_SYSTEM_OBSERVATION",
        )

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

    browser_seed = _build_browser_observation_route_seed(cleaned)
    if browser_seed is not None:
        return browser_seed

    memory_steward_seed = _build_memory_steward_route_seed(cleaned)
    if memory_steward_seed is not None:
        return memory_steward_seed

    repo_doctor_seed = _build_repo_doctor_route_seed(cleaned)
    if repo_doctor_seed is not None:
        return repo_doctor_seed

    frontend_audit_seed = _build_frontend_audit_route_seed(cleaned)
    if frontend_audit_seed is not None:
        return frontend_audit_seed

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
        codex_target = "codex_self_evolve" if codex_runtime_mode == "self_evolve" else "codex_self_heal"
        return {
            "route": "skill",
            "target_id": codex_target,
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

    if _looks_like_simple_technical_advice(cleaned):
        return _build_simple_technical_chat_seed(cleaned, route_source="thalamus.rule")

    system_seed = _build_system_observation_route_seed(cleaned)
    if system_seed is not None:
        return system_seed

    if _TASK_CONTEXT_CONTINUATION_RE.match(cleaned):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": cleaned,
            "reason_code": "RULE_CONTEXT_CONTINUATION",
            "confidence": 0.88,
            "route_source": "thalamus.rule",
            "args": {
                "text": cleaned,
                "user_message": cleaned,
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

    if _TASK_PLAN_REQUEST_RE.search(cleaned):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": cleaned,
            "reason_code": "RULE_ACTION_INTENT",
            "confidence": 0.86,
            "route_source": "rule",
        }

    workspace_seed = _build_codex_workspace_route_seed(cleaned)
    if workspace_seed is not None:
        return workspace_seed

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

    if detect_relational_chat_topic(normalized):
        return {
            "route": "chat",
            "target_id": "",
            "task": normalized,
            "reason_code": build_relational_chat_reason_code(normalized) or "RELATIONAL_CHAT",
            "confidence": 0.98,
            "route_source": "rule",
            "args": {
                "text": normalized,
            },
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

    if _TASK_PLAN_REQUEST_RE.search(normalized):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": normalized,
            "reason_code": "RULE_ACTION_INTENT",
            "confidence": 0.86,
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

    if (
        _TASK_CODE_RE.search(normalized)
        and not _TASK_ENTERTAINMENT_RE.search(normalized)
        and not _TASK_CODE_NEGATION_RE.search(normalized)
    ):
        codex_target = "codex_inspect" if _looks_like_lightweight_codex_request(normalized) else "codex_worker"
        return {
            "route": "skill",
            "target_id": codex_target,
            "task": normalized,
            "reason_code": f"RULE_FORCE_{codex_target}",
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

    if _TASK_ENTERTAINMENT_RE.search(normalized):
        return None

    if _looks_like_generic_action_intent(normalized):
        return {
            "route": "delegate",
            "target_id": "intent_agent",
            "task": normalized,
            "reason_code": "RULE_ACTION_INTENT",
            "confidence": 0.86,
            "route_source": "rule",
        }

    compact_lower = normalized.lower().replace(" ", "")
    if any(marker in compact_lower for marker in _TASK_EXEC_ACTION_MARKERS):
        return None
    return None


_WORK_CODE_TARGETS: frozenset[str] = frozenset({
    "codex_worker",
    "codex_inspect",
    "code_agent",
    "workbench_code_assist",
})
_WORK_TRANSLATE_TARGETS: frozenset[str] = frozenset({
    "translate_agent",
    "workbench_translate",
})
_VISION_UI_TARGETS: frozenset[str] = frozenset({
    "vision_agent",
    "vision_ui",
    "multimodal_input",
})


def _decision_text(value: Any) -> str:
    return str(value or "").strip().lower()


def resolve_scene_type_from_intent_decision(
    decision: Mapping[str, Any] | None,
    *,
    fallback_text: str | None = None,
) -> str:
    """Map the canonical Thalamus intent decision to the runtime scene type."""
    draft = dict(decision or {}) if isinstance(decision, Mapping) else {}
    route = _decision_text(draft.get("route"))
    target_id = _decision_text(draft.get("target_id") or draft.get("target"))
    action = _decision_text(draft.get("action"))
    reason = _decision_text(draft.get("route_reason") or draft.get("reason_code"))
    args = draft.get("args") if isinstance(draft.get("args"), Mapping) else {}
    task_text = str(
        draft.get("task")
        or (args or {}).get("text")
        or (args or {}).get("query")
        or fallback_text
        or ""
    ).strip()

    if (
        target_id in _VISION_UI_TARGETS
        or action in _VISION_UI_TARGETS
        or "vision" in reason
        or _TASK_VISION_UI_RE.search(task_text)
    ):
        return SCENE_VISION_UI
    if (
        target_id in _WORK_TRANSLATE_TARGETS
        or action == "work_translate"
        or "translate" in reason
        or _TASK_TRANSLATE_RE.search(task_text)
    ):
        return SCENE_WORK_TRANSLATE
    if (
        target_id in _WORK_CODE_TARGETS
        or target_id.startswith("codex_")
        or action in {"work_code", "codex_worker", "codex_inspect"}
        or action.startswith("codex_")
        or "codex" in reason
    ):
        return SCENE_WORK_CODE
    if route in {"delegate", "skill", "plan", "task_progress", "recent_turn"}:
        return SCENE_EXEC_INTENT
    return SCENE_CHAT


def classify_scene_type_by_rules(text: str) -> str:
    """Classify a scene using Thalamus rules only, without app-layer pre-routing."""
    decision = classify_task_intent_by_rules(text)
    return resolve_scene_type_from_intent_decision(decision, fallback_text=text)


def classify_task_intent_fastpath(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    relational_reason = build_relational_chat_reason_code(normalized)
    if relational_reason:
        return {
            "route": "chat",
            "target_id": "",
            "task": normalized,
            "reason_code": relational_reason,
            "confidence": 0.99,
            "route_source": "rule.fastpath",
            "args": {
                "text": normalized,
            },
        }

    phatic_reason = _detect_short_companion_chat_reason(normalized)
    if phatic_reason:
        bio_ops_reflex = None
        try:
            from host_brain.bio_ops.capabilities import classify_reflex_stimulus  # noqa: PLC0415

            candidate = classify_reflex_stimulus(normalized)
            if bool(candidate.get("matched")):
                bio_ops_reflex = {
                    **candidate,
                    "path": "bio_ops_reflex",
                    "forbidden_actions": ["codex_worker", "long_reasoning", "skill_execution"],
                }
        except Exception:
            bio_ops_reflex = None
        return {
            "route": "chat",
            "target_id": "",
            "task": normalized,
            "reason_code": phatic_reason,
            "confidence": 0.99,
            "route_source": "rule.fastpath",
            "bio_ops_reflex": bio_ops_reflex,
            "args": {
                "text": normalized,
            },
        }

    if _looks_like_simple_technical_advice(normalized):
        return _build_simple_technical_chat_seed(normalized, route_source="rule.fastpath")

    compact = re.sub(r"\s+", "", normalized).lower()
    if _TASK_PROGRESS_QUERY_RE.search(compact):
        return {
            "route": "task_progress",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_TASK_PROGRESS",
            "confidence": 0.98,
            "route_source": "rule.fastpath",
        }

    if _TASK_RECENT_TURN_RE.search(normalized):
        bio_ops_reflex = None
        try:
            from host_brain.bio_ops.capabilities import classify_reflex_stimulus  # noqa: PLC0415

            candidate = classify_reflex_stimulus(normalized)
            if bool(candidate.get("matched")):
                bio_ops_reflex = {
                    **candidate,
                    "path": "bio_ops_reflex",
                    "forbidden_actions": ["codex_worker", "long_reasoning", "skill_execution"],
                }
        except Exception:
            bio_ops_reflex = None
        return {
            "route": "recent_turn",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_RECENT_TURN",
            "confidence": 0.95,
            "route_source": "rule.fastpath",
            "bio_ops_reflex": bio_ops_reflex,
        }
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


def _build_proactive_reply_route_seed(
    text: str,
    *,
    tribe_context: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    ctx = dict(tribe_context or {})
    raw_proactive = ctx.get("proactive_interaction") or ctx.get("proactive_context") or {}
    proactive_context = dict(raw_proactive) if isinstance(raw_proactive, Mapping) else {}
    if not proactive_context:
        return None
    in_reply = bool(proactive_context.get("in_reply_to_proactive"))
    outbound_message_id = _normalize_text(
        proactive_context.get("outbound_message_id")
        or proactive_context.get("message_id")
        or proactive_context.get("event_id")
    )
    if not in_reply and not outbound_message_id:
        return None

    normalized = _normalize_text(text) or "收到主动消息后的回复"
    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION",
        "confidence": 0.99,
        "route_source": "thalamus.rule",
        "proactive_context": {
            "in_reply_to_proactive": True,
            "outbound_message_id": outbound_message_id,
            "delivery_receipt_id": _normalize_text(proactive_context.get("delivery_receipt_id")),
            "delivery_status": _normalize_text(proactive_context.get("delivery_status")),
            "channel": _normalize_text(proactive_context.get("channel")) or "safe_outbox",
        },
        "args": {
            "text": normalized,
            "user_message": normalized,
            "proactive_reply": True,
            "outbound_message_id": outbound_message_id,
        },
    }


def _llm_intent_confidence_threshold() -> float:
    try:
        import os  # noqa: PLC0415

        configured = _normalize_confidence(os.environ.get("HOST_CORTEX_INTENT_LLM_MIN_CONFIDENCE"))
    except Exception:
        configured = None
    return configured if configured is not None else _LLM_ROUTE_MIN_CONFIDENCE_DEFAULT


def _normalize_llm_intent_route_candidate(
    candidate: Mapping[str, Any] | None,
    *,
    fallback_text: str,
) -> dict[str, Any] | None:
    if not isinstance(candidate, Mapping):
        return None

    route = _normalize_text(candidate.get("route"), lower=True)
    if route not in _ROUTE_ALLOWLIST:
        return None

    confidence = _normalize_confidence(candidate.get("confidence"))
    if confidence is None:
        confidence = 0.7
    if confidence < _llm_intent_confidence_threshold():
        return None

    raw_target_id = _normalize_text(candidate.get("target_id") or candidate.get("target"), lower=True)
    target_id = raw_target_id
    if route in _LLM_CHATLIKE_ROUTES:
        target_id = ""
    elif route == "plan":
        target_id = raw_target_id or "skill_dag"
        if target_id != "skill_dag":
            return None
    elif route == "delegate":
        if target_id not in _LLM_DELEGATE_TARGET_ALLOWLIST:
            return None
    elif route == "skill":
        if target_id not in _LLM_SKILL_TARGET_ALLOWLIST:
            return None
    else:
        return None

    task = _normalize_text(candidate.get("task")) or _normalize_text(fallback_text)
    reason = (
        _normalize_text(candidate.get("reason_code"))
        or _normalize_text(candidate.get("reason"))
        or "LLM_INTENT_ROUTE"
    )
    normalized: dict[str, Any] = {
        "route": route,
        "target_id": target_id,
        "task": task,
        "reason_code": reason,
        "confidence": confidence,
        "route_source": "llm",
    }
    if route == "plan":
        planned_steps = candidate.get("planned_steps")
        if not isinstance(planned_steps, list):
            planned_steps = candidate.get("steps")
        if isinstance(planned_steps, list):
            normalized["planned_steps"] = [dict(step) for step in planned_steps if isinstance(step, Mapping)]
    return normalized


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
        from host_brain.integrations.llm_helpers import acomplete_llm_with_pool  # noqa: PLC0415

        llm_runtime = await resolve_host_llm_runtime_async(rollout_key=session_id, scene_type="EXEC_INTENT")
        router_runtime = resolve_executive_router_llm_runtime(base_runtime=llm_runtime)
        if not bool(router_runtime.get("available")):
            return None

        provider_name = str(router_runtime.get("provider_name") or "").strip()
        model_id = str(router_runtime.get("model_id") or "").strip()
        api_key = str(router_runtime.get("llm_api_key") or "").strip()
        if not provider_name or not model_id:
            return None

        ctx = dict(tribe_context or {})
        vital_signs = dict(ctx.get("vital_signs") or {})
        bond_stage = str(vital_signs.get("bond_stage") or ctx.get("bond_stage") or "friend").strip()
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

        result = await acomplete_llm_with_pool(
            request_id=request_id,
            scene_type="EXEC_INTENT",
            cognitive_tier="C",
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            gen_params=agent_gen_params,
            response_format="json_object",
            base_url_override=base_url_override,
        )
        if result.pool_exhausted:
            return None
        raw_text = str(result.content or "").strip()
        parsed = extract_first_json_object(raw_text)
        if not isinstance(parsed, dict):
            _logger.warning("[%s] thalamus_intent_llm_parse_failed output=%.180s", request_id, raw_text)
            return None

        return _normalize_llm_intent_route_candidate(parsed, fallback_text=text)
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
    proactive_seed = _build_proactive_reply_route_seed(
        normalized,
        tribe_context=tribe_context,
    )
    if proactive_seed is not None:
        return proactive_seed

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

    fastpath_result = classify_task_intent_fastpath(normalized)
    if fastpath_result is not None:
        return fastpath_result

    rule_result = classify_task_intent_by_rules(normalized)
    if rule_result is not None:
        if str(rule_result.get("route") or "").strip().lower() in {"task_progress", "recent_turn"}:
            return rule_result
        if str(rule_result.get("reason_code") or "").strip().upper().startswith("THALAMUS_RULE_SHORT_COMPANION_CHAT"):
            return rule_result
        if _should_prefer_rule_seed(rule_result):
            return rule_result

    raw_llm_result = await classify_task_intent_by_llm(
        request_id=request_id,
        session_id=session_id,
        text=normalized,
        tribe_context=tribe_context,
    )
    llm_result = _normalize_llm_intent_route_candidate(raw_llm_result, fallback_text=normalized)
    if llm_result is not None:
        if _should_prefer_rule_seed(rule_result):
            merged = dict(rule_result or {})
            merged["llm_route_used"] = True
            merged["llm_route_candidate"] = dict(llm_result)
            return merged
        return llm_result

    if rule_result is not None:
        return rule_result

    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": "DEFAULT_CHAT",
        "confidence": 0.7,
        "route_source": "default",
    }


def _should_prefer_rule_seed(rule_result: Mapping[str, Any] | None) -> bool:
    if not isinstance(rule_result, Mapping):
        return False
    reason_code = _normalize_text(rule_result.get("reason_code"))
    route_source = _normalize_text(rule_result.get("route_source"))
    if route_source.startswith("thalamus.rule"):
        return True
    if route_source.startswith("rule"):
        return True
    if reason_code.startswith("THALAMUS_RULE_"):
        return True
    if reason_code.startswith("RULE_FORCE_"):
        return True
    return False


def _resolve_target_id(payload: Mapping[str, Any]) -> str:
    candidate = _normalize_text(payload.get("target_id"))
    if candidate:
        return candidate
    action = _normalize_text(payload.get("action"), lower=True)
    if action == "work_code":
        return "codex_worker"
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
        if normalized_target in {
            "codex_inspect",
            "codex_worker",
            "codex_self_heal",
            "codex_self_evolve",
            "codex_sql_audit",
            "codex_translate_project",
            "codex_skill_forge",
            "codex_research",
            "codex_organ_scan",
            "codex_persona_drift_audit",
            "codex_dream_compile",
        }:
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
                if normalized_target == "codex_self_heal":
                    merged["run_mode"] = "self_heal"
                elif normalized_target == "codex_self_evolve":
                    merged["run_mode"] = "self_evolve"
                elif normalized_target == "codex_inspect":
                    merged["run_mode"] = "inspect"
                elif normalized_target == "codex_research":
                    merged["run_mode"] = "research"
                elif normalized_target == "codex_translate_project":
                    merged["run_mode"] = "translate"
                elif normalized_target == "codex_sql_audit":
                    merged["run_mode"] = "audit"
                elif _looks_like_lightweight_codex_request(_normalize_text(merged.get("task")) or fallback_text):
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
        from host_brain.app.prompt_specs import resolve_prompt_spec_json  # noqa: PLC0415

        default_value = resolve_prompt_spec_json(template_id)
    except Exception:
        return None

    return {
        "template_id": template_id,
        "default": default_value,
        "override": None,
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
                bond_stage="friend",
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
    "THALAMUS_SCENE_TYPE_SOURCE",
    "ThalamusAgent",
    "classify_scene_type_by_rules",
    "classify_task_intent",
    "classify_task_intent_by_llm",
    "classify_task_intent_by_rules",
    "get_thalamus_agent",
    "resolve_scene_type_from_intent_decision",
    "set_thalamus_agent",
]
