from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from host_brain.model_runtime.prompt_builder_utils import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)
from host_brain.persona.relational_chat import (
    build_relational_chat_reason_code,
    detect_relational_chat_topic,
)
from host_brain.core.ids import now_ms
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry

_logger = logging.getLogger("Natsume.thalamus_agent")

_INTENT_SCHEMA_VERSION = "Natsume.intent.v1"
THALAMUS_SCENE_TYPE_SOURCE = "thalamus_agent"
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
_PENTEST_EXEC_MARKER_RE = re.compile(
    r"("
    r"授权.{0,20}(渗透测试|安全扫描|漏洞扫描|漏洞评估|安全评估|端口扫描|资产侦察|子域名枚举|配置审计)"
    r"|(渗透测试|安全扫描|漏洞扫描|漏洞评估|安全评估|端口扫描|资产侦察|子域名枚举|配置审计)"
    r"|(?:pentest|penetration\s+test|authorized\s+(?:security\s+)?scan|security\s+scan"
    r"|vulnerability\s+(?:scan|assessment)|recon(?:naissance)?|port\s+scan|subdomain\s+enumeration"
    r"|config(?:uration)?\s+audit)"
    r")",
    re.IGNORECASE,
)
_PENTEST_STRUCTURED_SCOPE_RE = re.compile(
    r"("
    r"\btarget\s*[:=]\s*\S+.{0,160}\bscope\s*[:=]\s*\S+"
    r"|\bscope\s*[:=]\s*\S+.{0,160}\btarget\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_PENTEST_OPERATION_HINT_RE = re.compile(
    r"("
    r"read[-_\s]?only|no\s+exploit|without\s+exploit|forbid(?:den)?\s+exploit"
    r"|pentest|penetration\s+test|security\s+scan|vulnerability|vuln|recon(?:naissance)?|scan|audit|exploit"
    r"|授权|只读|禁止|安全扫描|漏洞扫描|漏洞评估|安全评估|端口扫描|资产侦察|配置审计|侦察|审计"
    r")",
    re.IGNORECASE,
)
_PENTEST_SCOPE_HINT_RE = re.compile(
    r"("
    r"\b(?:target|scope|host|domain|url)\s*[:=]"
    r"|(?:目标|范围|授权范围|授权域|靶标)\s*[:：=]"
    r"|https?://[^\s，。；,;]+"
    r"|\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b"
    r"|\b(?:localhost|127\.0\.0\.1)\b"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b"
    r"|(?:靶场|测试环境|本地环境|内网环境|ctf|lab|dvwa|bwapp|metasploitable)"
    r")",
    re.IGNORECASE,
)
_PENTEST_ADVICE_ONLY_RE = re.compile(
    r"(是什么|什么是|解释|原理|概念|科普|讲讲|学习|教程|怎么做|如何做|怎么防|如何防|如何修复"
    r"|what\s+is|explain|how\s+to|mitigat|defen[cs]e)",
    re.IGNORECASE,
)
_PENTEST_TAKEOVER_RE = re.compile(
    r"("
    r"端(?:了|掉)|拿下|打(?:下|穿|通)|攻(?:下|克|破|陷)|清(?:场|剿|空)|横(?:向|扫|移)"
    r"|全(?:域|网)|一(?:锅端|网打尽)|彻底|完全|所有(?:节点|主机|机器|服务器)"
    r"|domain\s+(?:admin|takeover|compromise)|take\s*over|full\s+(?:compromise|takeover)"
    r"|getshell|get\s+shell|(?:全部|所有).{0,6}(?:拿下|打掉|攻破|干掉)"
    r"|拿下.{0,6}(?:域控|域|内网|所有|全部)"
    r"|(?:域控|内网|域|全(?:部|网)).{0,6}(?:拿下|端掉|打掉|攻破)"
    r"|compromise\s+(?:the\s+)?(?:domain|entire|all\s+(?:hosts|machines|nodes))"
    r")",
    re.IGNORECASE,
)

# ── 多意图分类：每种渗透目标有独立正则 → goal_type 映射 ──
_PENTEST_INTENT_SHELL_RE = re.compile(
    r"("
    r"拿\s*shell|弹\s*shell|get\s*shell|webshell|反弹|reverse\s*shell"
    r"|(?:拿|弹|搞|要).{0,4}(?:shell|权限|webshell)"
    r"|(?:弄|打|搞|开).{0,4}(?:一个shell|个shell)"
    r"|shell\s*(?:access|权限)"
    r")",
    re.IGNORECASE,
)
_PENTEST_INTENT_ROOT_RE = re.compile(
    r"("
    r"提权|root|system|administrator|特权|(?:提到|升到|搞到|拿到).{0,4}(?:root|system|管理员|最高权限)"
    r"|privilege\s+escalat|privesc|(?:get|gain)\s+(?:root|system|admin)"
    r")",
    re.IGNORECASE,
)
_PENTEST_INTENT_FLAG_RE = re.compile(
    r"("
    r"flag|ctf|夺旗|读\s*(?:flag|标志)|找\s*(?:flag|标志)|(?:拿|读|找).{0,3}flag"
    r"|capture\s+(?:the\s+)?flag"
    r")",
    re.IGNORECASE,
)
_PENTEST_INTENT_CHAIN_RE = re.compile(
    r"("
    r"攻击链|打穿|深度|(?:尽可能|尽量|能走多远走多远).{0,6}(?:深|远|多)"
    r"|attack\s+chain|deep\s+exploit|maximum\s+depth"
    r"|(?:挖|找).{0,4}(?:链|path|深度)"
    r")",
    re.IGNORECASE,
)
_PENTEST_INTENT_SERVICE_RE = re.compile(
    r"("
    r"(?:拿下|打掉|攻破|搞定|搞掉).{0,6}(?:服务|应用|网站|app|api|数据库|容器)"
    r"|(?:审计|渗透|测试|检查).{0,6}(?:服务|应用|网站|app|api|数据库|容器)"
    r"|compromise\s+(?:the\s+)?(?:service|application|website|api|database)"
    r"|service\s+compromise|(?:特定|指定|某个|这个|那个).{0,4}(?:服务|应用)"
    r")",
    re.IGNORECASE,
)
_PENTEST_INTENT_CRITICAL_RE = re.compile(
    r"("
    r"(?:挖|找|搞|弄).{0,4}(?:高危|严重|critical|high).{0,4}(?:漏洞|洞|vuln|finding)"
    r"|(?:找出|发现|确认).{0,4}(?:高危|严重|critical|high)"
    r"|n\s*_?\s*critical|N_CRITICAL"
    r")",
    re.IGNORECASE,
)
_PENTEST_INTENT_DOMAIN_ADMIN_RE = re.compile(
    r"("
    r"域管|domain\s+admin|域(?:管理|控)员|da(?:权限|账号|账户)?"
    r"|(?:拿|搞|升|提).{0,4}(?:域管|domain\s+admin|da)"
    r"|kerberos.*(?:admin|域管)|dcsync"
    r")",
    re.IGNORECASE,
)

_PENTEST_PROGRESS_QUERY_RE = re.compile(
    r"("
    # 明确战况/攻击进度查询（正向匹配）
    r"打(?:到哪|到哪里|得怎么样|得如何|完了没|到哪了|到哪里了)"
    r"|(?:现在|目前|当前).{0,4}(?:进度|战况|打得)"
    r"|(?:进度|战况|战报).{0,2}(?:如何|怎么样|怎样|报告|汇报)"
    r"|(?:汇报|报告).{0,2}(?:进度|战况)"
    r"|(?:到|打到).{0,2}(?:哪一步|第几步|哪了|哪里了)"
    r"|(?:how.*(?:going|progress)|what.*(?:progress|next)|update.*progress)"
    r")",
    re.IGNORECASE,
)
# 否定词：排除非渗透上下文的查询（情绪状态、系统状态等）
_PENTEST_PROGRESS_NEGATIVE_RE = re.compile(
    r"(情绪|心情|感情|心理|系统状态|服务状态|健康状态)",
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
_CODEX_PRODUCTION_BACKGROUND_RE = re.compile(
    r"(codex|后台|异步|生产型|生产技能|深度|长期|每日|daily|background|production)",
    re.IGNORECASE,
)
_CODEX_WORKBENCH_BRIEF_RE = re.compile(
    r"((工作|任务|今日|每日).{0,10}(简报|brief|摘要)|(?:workbench|daily)\s+brief|brief\s+me)",
    re.IGNORECASE,
)
_CODEX_REPO_SENTINEL_RE = re.compile(
    r"((仓库|repo|repository|项目).{0,12}(哨兵|巡检|后台体检|深度体检)|repo\s+sentinel)",
    re.IGNORECASE,
)
_CODEX_MEMORY_CURATOR_RE = re.compile(
    r"((记忆|长期记忆).{0,12}(候选|管家|整理|沉淀).{0,12}(后台|异步|codex|深度)"
    r"|(后台|异步|codex|深度).{0,12}(记忆候选|记忆管家)|memory\s+curator)",
    re.IGNORECASE,
)
_CODEX_DOCUMENT_DIGEST_RE = re.compile(
    r"((文档|资料|材料|网页摘录).{0,12}(消化|摘要|总结|digest)|document\s+digest)",
    re.IGNORECASE,
)
_CODEX_BROWSER_RESEARCH_RE = re.compile(
    r"((浏览器|网页|网站|网购|购物|商品|browser).{0,24}(研究|调研|查阅|比价|资料|证据|research)"
    r"|(研究|调研|查阅|比价|资料|证据).{0,24}(浏览器|网页|网站|网购|商品|browser)"
    r"|browser\s+(research|workbench))",
    re.IGNORECASE,
)
_CODEX_FRONTEND_ACCEPTANCE_RE = re.compile(
    r"((前端|页面|ui|UI|浏览器|无头浏览器|headless).{0,24}(验收|验证|截图|效果|控制台|console|网络错误|network|acceptance)"
    r"|(验收|验证|截图|效果|控制台|console|网络错误|network).{0,24}(前端|页面|ui|UI|浏览器|无头浏览器|headless)"
    r"|frontend\s+acceptance)",
    re.IGNORECASE,
)
_CODEX_FRONTEND_REVIEW_RE = re.compile(
    r"((前端|页面|ui|UI|组件|卡片|布局).{0,18}(后台|异步|codex|深度).{0,18}(审查|巡检|优化|review)"
    r"|(后台|异步|codex|深度).{0,18}(前端|页面|组件|布局).{0,18}(审查|巡检|优化|review)"
    r"|frontend\s+review)",
    re.IGNORECASE,
)
_CODEX_WEB_CORPUS_COLLECTOR_RE = re.compile(
    r"((网页|网站|公开|授权|web).{0,24}(语料|训练语料|数据集|corpus).{0,24}(收集|采集|整理|清洗|collector)"
    r"|(语料|训练语料|数据集|corpus).{0,24}(网页|网站|公开|授权|web).{0,24}(收集|采集|整理|清洗|collector)"
    r"|web\s+corpus)",
    re.IGNORECASE,
)
_CODEX_LIGHTNOVEL_RECOMMENDER_RE = re.compile(
    r"((轻小说|小说|lightnovel).{0,28}(推荐|榜单|每日榜|排行|最新资源|封面|简介|recommend)"
    r"|(推荐|榜单|每日榜|排行|最新资源).{0,28}(轻小说|小说|lightnovel)"
    r"|lightnovel\s+recommend)",
    re.IGNORECASE,
)
_CODEX_EPUB_BUILDER_RE = re.compile(
    r"((epub|电子书|轻小说|小说|文本|图片).{0,28}(封装|生成|制作|打包|构建|builder)"
    r"|(网页|网站).{0,28}(轻小说|小说|文本|图片).{0,28}(epub|电子书|封装|打包)"
    r"|epub\s+builder)",
    re.IGNORECASE,
)
_CODEX_EPUB_CONTEXT_ACTION_RE = re.compile(
    r"(epub|电子书|封装|打包|制作|生成|构建|下载|购买|买下来)",
    re.IGNORECASE,
)
_CODEX_EPUB_RECOMMENDATION_SELECTION_RE = re.compile(
    r"(?:推荐的?\s*(?:第\s*)?([一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项)"
    r"|(?:第\s*)?([一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项).{0,16}(?:封装|打包|epub|电子书))",
    re.IGNORECASE,
)
_CODEX_EPUB_RECOMMENDATION_INDEX_WORDS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
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
_STEP_LEAD_IN_RE = re.compile(
    r"^\s*(?:请|请你|帮我|麻烦|旧角色|旧角色|natsume(?: blue)?|先|首先|first)\s*",
    re.IGNORECASE,
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
    r"(记住|记一下|记下来|写入记忆|保存.*记忆|记到记忆|remember this|save this memory|偏好是|我的偏好"
    r"|项目事实|测试事实|记忆事实|补(?:一个|充)?[^。！？\n]{0,24}事实|记录[^。！？\n]{0,24}事实)",
    re.IGNORECASE,
)
_TASK_MEMORY_FACT_RE = re.compile(
    r"(项目事实|测试事实|记忆事实|事实[:：]|补(?:一个|充)?[^。！？\n]{0,24}事实|记录[^。！？\n]{0,24}事实"
    r"|测试代号|海边灯塔|app-server预热|app server预热|预热指标)",
    re.IGNORECASE,
)
_TASK_CODE_RE = re.compile(
    r"(报错|错误|异常|修复|修一个|改代码|写代码|单测|测试|diff|patch|traceback|stack trace|compile|build|修.*bug|bug.*修"
    r"|代码|编程|写.*函数|函数|脚本|调试|实现|开发|\bcode\b|coding|programming|function|script|program"
    r"|python|javascript|typescript|java|golang|rust|c\+\+|debug|bug|class|algorithm|implement|develop)",
    re.IGNORECASE,
)
_TASK_PLUGIN_BUILD_RE = re.compile(
    r"((?:做|写|实现|开发|创建|新建|接入|加|补).{0,18}(?:插件|plugin)"
    r"|(?:插件|plugin).{0,18}(?:做|写|实现|开发|创建|新建|接入|加|补))",
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
    r"(我刚刚|上一句|上一条|上句说|你刚刚|刚才说到哪|刚刚说到哪|刚才聊到哪|刚刚聊到哪|what did i just say|what did you just say)",
    re.IGNORECASE,
)
_TASK_RECENT_TURN_STRICT_RE = re.compile(
    r"(上一句|上一条|上句说|刚才说到哪|刚刚说到哪|刚才聊到哪|刚刚聊到哪"
    r"|你(?:刚才|刚刚).{0,8}(?:说了?什么|说的是?什么|回了?什么)"
    r"|what did (?:i|you) just say)",
    re.IGNORECASE,
)
_TASK_CONTEXTUAL_CHAT_FOLLOWUP_RE = re.compile(
    r"(长对话|连续对话|连续性测试|上下文|前文|我刚才|我刚刚|你刚才|你刚刚|上一轮|上轮|前一轮|前面|刚才|刚刚|目前)"
    r".{0,48}(口令|暗号|关键词|锚点|记住|让你记|是什么|什么|本身|短句|变成|改成|包含|表达偏好|表达风格|风格|偏好|约定|测试报告|报告|场子|稳住|收尾|评价|新版|旧版|客服腔|更相信)"
    r"|(?:这个|那个|它|刚才那个|刚刚那个|临时).{0,24}(口令|暗号|关键词|锚点).{0,24}"
    r"(是什么|什么|本身|短句|变成|改成|包含|还记得|记得吗)"
    r"|(?:口令|暗号|关键词|锚点).{0,24}(是什么|什么|本身|短句|变成|改成|包含|还记得|记得吗)"
    r"|(?:测试报告|系统测试).{0,32}(要求|偏好|更相信|可复现|主观|体验|备注)"
    r"|评价.{0,40}(连续性测试|长对话).{0,40}(实际意义|意义|问题)"
    r"|(?:长对话|连续性).{0,48}(指标|测连续性|最该看)"
    r"|(?:再修正|修正|更新).{0,24}报告.{0,48}(可复现|主观|体验|备注)"
    r"|锚点.{0,24}(别名|小橙灯)"
    r"|小橙灯.{0,24}(指什么|是什么|也指)"
    r"|给这个表达风格起.{0,16}(名字|名称)"
    r"|(?:表达风格|表达偏好).{0,24}(名字|名称)"
    r"|更新一下.{0,48}(偏青|余温|新版)"
    r"|客服腔.{0,48}(自我修正|怎么修正|应该怎么)"
    r"|如果我说累.{0,48}(大道理|缓下来|不黏糊)"
    r"|按我们新版表达风格"
    r"|(?:上一轮|上轮).{0,32}场子"
    r"|稳定地在旁边"
    r"|今天先收工",
    re.IGNORECASE,
)
_RECOMMENDATION_FOLLOWUP_QUERY_RE = re.compile(
    r"("
    r"(?:第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?|这本|这部|那本|哪本|刚才推荐|刚刚推荐|上面那本|小说|轻小说|书)"
    r".{0,24}(?:好看|怎么样|如何|值得|推荐|能看|先看|要看|适合|对味)"
    r"|(?=.*(?:本|部|书|小说|轻小说|刚才推荐|刚刚推荐))(?:好看|不错|可以|有意思|值得|推荐|能看).{0,8}(?:吗|嘛|么|不|呢|\?|？)"
    r"|(?:先看哪本|哪本更|哪本好|哪本值得|哪本适合)"
    r")",
    re.IGNORECASE,
)
_USER_PREFERENCE_QUERY_RE = re.compile(
    r"("
    r"(?:你知道|知道)?我(?:到底|现在)?(?:喜欢|偏好|爱看|爱玩|爱听).{0,24}(?:什么|哪种|哪类|哪一类|啥|吗|嘛|么)"
    r"|我的.{0,12}(?:偏好|口味|喜好).{0,16}(?:是什么|什么|哪种|哪类|吗|嘛|么)"
    r"|我(?:喜欢|偏好|爱看|爱玩|爱听)的是.{0,16}(?:什么|哪种|哪类|吗|嘛|么)"
    r")",
    re.IGNORECASE,
)
_PREFERENCE_FEEDBACK_SIGNAL_RE = re.compile(
    r"("
    r"(?:我|个人)?(?:很|挺|还|比较|超)?(?:喜欢|不喜欢|讨厌|爱看|不爱看|想看|不想看|接受不了)"
    r"|(?:第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?|这本|这部|那本)"
    r".{0,12}(?:喜欢|不喜欢|不错|不行|可以|对味|没意思|别推)"
    r"|别(?:再)?推.{0,12}(?:这种|这个|这类)"
    r")",
    re.IGNORECASE,
)
_CONVERSATION_REPAIR_RE = re.compile(
    r"(没接上|接不上|承接不上|上下文断了|跑偏|误解|理解错|不是这个|不是这个意思|我说的是|我问的是|牛头不对马嘴|还是不对|还不对|又错了|还是错|不对吧|不太对)",
    re.IGNORECASE,
)
_QUALITY_META_FEEDBACK_RE = re.compile(
    r"(不像人话|不像人类|人类的思考|思考方式|不够聪明|不聪明|太笨|助手味|机械|呆|僵硬|模板|硬规则|废话|空话|尬|看不懂|没意义|负优化|质量|文本质量|意图识别)",
    re.IGNORECASE,
)
_TASK_CONTEXTUAL_CHAT_BLOCKER_RE = re.compile(
    r"(代码|仓库|文件|目录|路径|脚本|函数|接口|服务|部署|启动|host|edge|gateway|pytest|单测|跑测试|修复|报错|bug|diff|patch)",
    re.IGNORECASE,
)
_TASK_PROGRESS_QUERY_RE = re.compile(
    r"(进度|进展|任务状态|执行状态|结果呢|有结果吗|完成了\??|做好了吗|好了没|搞定了吗|到哪了|还要多久"
    r"|(?:这次|当前|聊天)?主链.{0,16}(效果|表现|耗时|延迟|怎么样|如何)"
    r"|\b(?:eta|done|status|progress|update)\b\??)",
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
_TASK_CHAT_CONTINUATION_RE = re.compile(
    r"^\s*(继续|继续吧|接着|接着来|继续说|接着说|然后呢|后面呢|那后面呢|continue|go on)\s*[。.!！?？]*\s*$",
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
_TASK_RESOURCE_MANAGER_STATUS_RE = re.compile(
    r"((自动)?资源(管家|管控|管理|监控).{0,12}(状态|运行|有没有|开着|启动|效果)"
    r"|resource\s+manager\s+(?:status|health))",
    re.IGNORECASE,
)
_TASK_RESOURCE_PRESSURE_ANALYSIS_RE = re.compile(
    r"((分析|诊断|排查|查明|定位|为什么|原因|根因).{0,24}(内存|显存|vram|GPU|gpu|资源).{0,24}(爆|拉爆|飙|飙高|占满|高|满|压力|卡)"
    r"|(内存|显存|vram|GPU|gpu|资源).{0,24}(爆|拉爆|飙|飙高|占满|高|满|压力|卡).{0,24}(分析|诊断|排查|查明|定位|为什么|原因|根因)"
    r"|(?:resource|memory|vram|gpu).{0,16}(?:root cause|analysis|diagnose|why))",
    re.IGNORECASE,
)
_TASK_RESOURCE_RELEASE_RE = re.compile(
    r"((释放|清理|回收|腾出).{0,10}(资源|内存|显存|vram|GPU|gpu|CPU|cpu)"
    r"|(资源|内存|显存|vram).{0,10}(释放|清理|回收|腾出)"
    r"|(?:release|free|cleanup).{0,12}(?:resource|memory|vram|gpu))",
    re.IGNORECASE,
)
_TASK_PROCESS_RESOURCE_TOP_RE = re.compile(
    r"((谁|哪些|哪个|进程|process).{0,12}(占用|吃|耗|占).{0,12}(资源|内存|显存|cpu|CPU|gpu|GPU|vram)"
    r"|(资源|内存|显存|cpu|CPU|gpu|GPU|vram).{0,12}(排行|热点|top|占用最高)"
    r"|(?:process|resource).{0,12}(?:top|hotspot|usage))",
    re.IGNORECASE,
)
_TASK_RESOURCE_SNAPSHOT_RE = re.compile(
    r"((资源|内存|显存|vram|GPU|gpu|CPU|cpu).{0,12}(状态|压力|占用|负载|快照|监控|管控)"
    r"|((系统|电脑|机器|本机).{0,12}(卡|卡顿|变慢|微妙的卡|资源压力))"
    r"|(?:resource|memory|vram|gpu|cpu).{0,12}(?:snapshot|status|pressure|usage))",
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
_PHATIC_AUDITORY_CHECK_RE = re.compile(
    r"^(?:"
    r"(?:你)?(?:能不能|能|可以|可不可以|有没有)?(?:听到|听见|听得到|听得见|听清|听清楚)"
    r"(?:我|我说话|我讲话|声音)?(?:吗|么)?"
    r"|(?:我)?(?:说话|讲话|发声|声音)(?:你)?(?:能不能|能|可以|可不可以)?"
    r"(?:听到|听见|听得到|听得见|听清|听清楚)(?:吗|么)?"
    r"|(?:有|有没有)(?:听到|听见)?声音(?:吗|么)?"
    r"|(?:刚才|刚刚)?(?:我)?(?:说了|说话|讲话)(?:什么)?(?:你)?(?:听到|听见|听得到|听得见)(?:吗|么)?"
    r")[?？!！。…~]*$",
    re.IGNORECASE,
)
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
_STATUS_REPLY_REQUEST_RE = re.compile(
    r"("
    r"用.{0,10}(?:一句|一句话|短句).{0,10}(?:回复|回答|告诉|说)"
    r"|(?:回复|回答|告诉我|告诉一下).{0,32}(?:是否|是不是|有没有|能不能|能否|在线|正常|通过|可用|通不通)"
    r")",
    re.IGNORECASE,
)
_STATUS_REPLY_DOMAIN_RE = re.compile(
    r"(主链|闭环|链路|gateway|onebot|qq|微信|wechat|edge|host|asr|tts|vts|thalamus|意图识别|场景路由|路由|在线|连通|可用)",
    re.IGNORECASE,
)
_CONVERSATIONAL_OPINION_CHAT_RE = re.compile(
    r"(你觉得|你认为|你看|怎么看|看法|体感|判断|评价|像正常聊天|直接说|用(?:一|两|2|三|几)句"
    r"|不要列清单|别讲测试指标|最需要继续观察|风险是什么|会先怀疑哪一段|先怀疑哪一段|先怀疑哪段)",
    re.IGNORECASE,
)
_CONVERSATIONAL_OPINION_ACTION_BLOCKER_RE = re.compile(
    r"(开始|修复|优化|整理|清理|删除|移除|接入|重构|实现|修改|改造|创建|新建|提交|查看|检查|验证|跑测试"
    r"|执行|打开|部署|改代码|写代码|应用|落地|补上|跑一下|测一下"
    r"|fix|implement|modify|refactor|commit|inspect|check|verify|run|execute|deploy)",
    re.IGNORECASE,
)
_WORKSPACE_EXPLICIT_INSPECT_RE = re.compile(
    r"(查看|看一下|看下|看看|帮我看|验证|检查|分析|评估|梳理|审查|报告|完成情况|接入情况"
    r"|inspect|verify|check|analy[sz]e|review|audit|status)",
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


def _looks_like_simple_technical_advice(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    compact_lower = normalized.lower().replace(" ", "")
    if any(marker in compact_lower for marker in _SIMPLE_TECH_ACTION_BLOCKERS):
        return False
    return bool(_SIMPLE_TECH_ADVICE_RE.search(normalized) and _SIMPLE_TECH_QUESTION_RE.search(normalized))


def _looks_like_status_reply_chat(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return bool(_STATUS_REPLY_REQUEST_RE.search(normalized) and _STATUS_REPLY_DOMAIN_RE.search(normalized))


def _looks_like_conversational_opinion_chat(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if not _CONVERSATIONAL_OPINION_CHAT_RE.search(normalized):
        return False
    if _CONVERSATIONAL_OPINION_ACTION_BLOCKER_RE.search(normalized):
        return False
    return True


def _build_conversational_opinion_chat_seed(text: str, *, route_source: str) -> dict[str, Any]:
    normalized = _normalize_text(text)
    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": "THALAMUS_RULE_CONVERSATIONAL_OPINION_CHAT",
        "confidence": 0.98,
        "route_source": route_source,
        "args": {
            "text": normalized,
        },
    }


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
    if _PHATIC_AUDITORY_CHECK_RE.match(cleaned):
        return "THALAMUS_RULE_PHATIC_AUDITORY_CHECK"
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
        from host_brain.persona.context.interaction_thread import detect_assistant_reply_followup_kind  # noqa: PLC0415

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


def _looks_like_contextual_chat_followup(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if not _TASK_CONTEXTUAL_CHAT_FOLLOWUP_RE.search(normalized):
        return False
    compact = re.sub(r"\s+", "", normalized).lower()
    if "连续对话" in compact:
        return True
    if "长对话" in compact or "连续性测试" in compact:
        return True
    if "口令" in compact or "暗号" in compact or "关键词" in compact:
        return True
    if "锚点" in compact:
        return True
    if "测试报告" in compact or "系统测试" in compact:
        return True
    if "表达风格" in compact or "表达偏好" in compact:
        return True
    if "场子" in compact or "收尾" in compact:
        return True
    return not _TASK_CONTEXTUAL_CHAT_BLOCKER_RE.search(normalized)


def _build_contextual_chat_followup_seed(text: str, *, route_source: str) -> dict[str, Any]:
    normalized = _normalize_text(text)
    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": "RULE_CONTEXTUAL_CHAT_FOLLOWUP",
        "confidence": 0.97,
        "route_source": route_source,
        "args": {
            "text": normalized,
        },
    }


def _semantic_domain_from_text(text: str, *, default: str = "general") -> str:
    normalized = _normalize_text(text)
    if re.search(r"(轻小说|小说|书|第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|部|步)|推荐)", normalized, re.IGNORECASE):
        return "lightnovel_recommendation"
    if re.search(r"(意图识别|主链|路由|对话|回复|文本质量|上下文|承接)", normalized, re.IGNORECASE):
        return "conversation_runtime"
    if re.search(r"(代码|仓库|项目|host|edge|gateway|服务|后台|codex)", normalized, re.IGNORECASE):
        return "workspace_runtime"
    return default


def _build_pragmatic_frame(
    *,
    text: str,
    semantic_intent: Mapping[str, Any] | None,
    route: str,
    target_id: str,
    route_reason: str,
) -> dict[str, Any]:
    normalized = _normalize_text(text)
    semantic = _coerce_semantic_intent(semantic_intent)
    semantic_type = _normalize_text(semantic.get("type"))
    domain = _normalize_text(semantic.get("domain")) or _semantic_domain_from_text(normalized)
    frame: dict[str, Any] = {
        "user_move": "open_chat",
        "implied_need": "natural_response",
        "conversation_anchor": _normalize_text(semantic.get("context_binding")) or "none",
        "likely_emotion": "neutral",
        "expected_response": "respond_to_the_user_message_directly",
        "answer_focus": "current_user_utterance",
        "do_not": [],
        "repair_mode": False,
        "confidence": _normalize_confidence(semantic.get("confidence")) or 0.62,
    }

    if semantic_type == "recommendation_followup_query":
        frame.update(
            {
                "user_move": "specific_followup_question",
                "implied_need": "wants_a_direct_judgement_about_the_referenced_recommendation",
                "conversation_anchor": "recent_recommendation",
                "likely_emotion": "curious_but_expects_continuity",
                "expected_response": "name_or_anchor_the_referenced_item_then_answer_whether_it_is_worth_reading",
                "answer_focus": "referenced_recommendation_item",
                "do_not": ["do_not_answer_as_generic_preference_query", "do_not_switch_medium", "do_not_ignore_rank_reference"],
                "confidence": 0.97,
            }
        )
    elif semantic_type == "user_preference_query":
        frame.update(
            {
                "user_move": "self_knowledge_question",
                "implied_need": "wants_natsume_to_show_memory_based_understanding_of_the_user",
                "conversation_anchor": "user_preference_profile",
                "likely_emotion": "checking_if_she_remembers",
                "expected_response": "answer_from_evidence_with_uncertainty_not_generic_disclaimer",
                "answer_focus": "known_preferences_and_evidence",
                "do_not": ["do_not_say_no_records_if_recent_evidence_exists", "do_not_use_mechanical_disclaimer"],
                "confidence": 0.96,
            }
        )
    elif semantic_type == "preference_feedback_signal":
        frame.update(
            {
                "user_move": "preference_feedback",
                "implied_need": "expects_the_feedback_to_be_understood_and_used_later",
                "conversation_anchor": "recent_recommendation_or_profile",
                "likely_emotion": "evaluative",
                "expected_response": "acknowledge_the_specific_preference_signal_without_overclaiming",
                "answer_focus": "new_feedback_signal",
                "do_not": ["do_not_turn_feedback_into_a_question", "do_not_overgeneralize_single_feedback"],
                "confidence": 0.93,
            }
        )
    elif semantic_type == "conversation_repair":
        frame.update(
            {
                "user_move": "repair_or_correction",
                "implied_need": "wants_the_assistant_to_notice_the_mismatch_and_rebind_context",
                "conversation_anchor": "recent_conversation",
                "likely_emotion": "frustrated_or_dissatisfied",
                "expected_response": "briefly_admit_the_mismatch_then_correct_the_understanding_concretely",
                "answer_focus": "where_context_was_lost_and_how_to_continue",
                "do_not": ["do_not_treat_as_casual_chat", "do_not_reply_with_empty_apology", "do_not_hide_behind_process_words"],
                "repair_mode": True,
                "confidence": 0.94,
            }
        )
    elif semantic_type == "meta_quality_feedback":
        frame.update(
            {
                "user_move": "quality_critique",
                "implied_need": "wants_a_diagnosis_and_a_smarter_next_fix_not_a_label",
                "conversation_anchor": "recent_conversation",
                "likely_emotion": "dissatisfied_but_still_collaborating",
                "expected_response": "explain_the_thinking_gap_and_propose_or_apply_a_concrete_improvement",
                "answer_focus": "why_the_system_feels_unhuman_and_what_changes",
                "do_not": ["do_not_defend_the_system", "do_not_only_add_categories", "do_not_sound_like_a_support_ticket"],
                "repair_mode": True,
                "confidence": 0.92,
            }
        )
    elif semantic_type == "tool_or_work_request" or _normalize_text(route, lower=True) == "skill":
        frame.update(
            {
                "user_move": "action_request",
                "implied_need": "wants_work_to_start_or_continue",
                "conversation_anchor": "work_backend",
                "likely_emotion": "directive",
                "expected_response": "frontstage_acknowledges_and_tracks_backend_work_without_becoming_a_tool_persona",
                "answer_focus": "work_handoff_and_progress",
                "do_not": ["do_not_claim_frontstage_personally_operates_terminal", "do_not_reply_as_cold_task_bot"],
                "confidence": 0.86,
            }
        )
    elif semantic_type == "continue_active_context":
        frame.update(
            {
                "user_move": "continue_or_next_step",
                "implied_need": "wants_the_current_active_task_or_topic_to_continue",
                "conversation_anchor": "active_task_or_topic",
                "likely_emotion": "directive_or_waiting",
                "expected_response": "continue_the_active_task_or_topic_without_resetting_context",
                "answer_focus": "next_step_of_current_context",
                "do_not": ["do_not_treat_as_presence_ping", "do_not_restart_topic", "do_not_ask_open_ended_what_to_do"],
                "confidence": 0.84,
            }
        )
    elif semantic_type == "phatic_or_presence_check":
        frame.update(
            {
                "user_move": "presence_ping",
                "implied_need": "wants_light_contact_or_reassurance",
                "conversation_anchor": "relationship_presence",
                "likely_emotion": "checking_presence",
                "expected_response": "short_warm_present_reply",
                "answer_focus": "presence",
                "do_not": ["do_not_overexplain_system_status"],
                "confidence": 0.88,
            }
        )

    frame["domain"] = domain
    if target_id:
        frame["target_id"] = _normalize_text(target_id)
    if route_reason:
        frame["route_reason"] = _normalize_text(route_reason)
    return frame


def _build_semantic_chat_seed(
    text: str,
    *,
    route_source: str,
    reason_code: str,
    semantic_type: str,
    dialogue_act: str,
    context_binding: str,
    answer_strategy: str,
    confidence: float,
    domain: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_text(text)
    semantic_intent = {
        "type": semantic_type,
        "dialogue_act": dialogue_act,
        "domain": domain or _semantic_domain_from_text(normalized),
        "requires_context": bool(context_binding and context_binding != "none"),
        "context_binding": context_binding,
        "answer_strategy": answer_strategy,
        "confidence": round(float(confidence), 4),
    }
    pragmatic_frame = _build_pragmatic_frame(
        text=normalized,
        semantic_intent=semantic_intent,
        route="chat",
        target_id="",
        route_reason=reason_code,
    )
    return {
        "route": "chat",
        "target_id": "",
        "task": normalized,
        "reason_code": reason_code,
        "confidence": confidence,
        "route_source": route_source,
        "semantic_intent": semantic_intent,
        "pragmatic_frame": pragmatic_frame,
        "args": {
            "text": normalized,
            "semantic_intent": semantic_intent,
            "pragmatic_frame": pragmatic_frame,
        },
    }


def _build_structured_chat_semantic_seed(text: str, *, route_source: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _RECOMMENDATION_FOLLOWUP_QUERY_RE.search(normalized):
        return _build_semantic_chat_seed(
            normalized,
            route_source=route_source,
            reason_code="THALAMUS_RULE_RECOMMENDATION_FOLLOWUP_QUERY",
            semantic_type="recommendation_followup_query",
            dialogue_act="ask_opinion_about_recent_recommendation",
            context_binding="recent_recommendation",
            answer_strategy="bind_recent_recommendation_item_then_answer",
            confidence=0.97,
            domain="lightnovel_recommendation",
        )
    if _USER_PREFERENCE_QUERY_RE.search(normalized):
        return _build_semantic_chat_seed(
            normalized,
            route_source=route_source,
            reason_code="THALAMUS_RULE_USER_PREFERENCE_QUERY",
            semantic_type="user_preference_query",
            dialogue_act="ask_user_preference_profile",
            context_binding="user_preference_profile",
            answer_strategy="use_preference_profile_evidence_pack",
            confidence=0.96,
            domain=_semantic_domain_from_text(normalized, default="user_preference"),
        )
    preference_feedback_match = _PREFERENCE_FEEDBACK_SIGNAL_RE.search(normalized)
    preference_feedback_has_anchor = bool(
        re.search(
            r"(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|项|部|步)?|这本|这部|那本|刚才推荐|刚刚推荐|推荐|小说|轻小说)",
            normalized,
            re.IGNORECASE,
        )
    )
    if preference_feedback_match and (len(normalized) <= 80 or preference_feedback_has_anchor):
        return _build_semantic_chat_seed(
            normalized,
            route_source=route_source,
            reason_code="THALAMUS_RULE_PREFERENCE_FEEDBACK_SIGNAL",
            semantic_type="preference_feedback_signal",
            dialogue_act="express_preference_or_recommendation_feedback",
            context_binding="recent_recommendation_or_profile",
            answer_strategy="acknowledge_feedback_and_let_preference_store_extract",
            confidence=0.93,
            domain=_semantic_domain_from_text(normalized, default="user_preference"),
        )
    if _CONVERSATION_REPAIR_RE.search(normalized):
        return _build_semantic_chat_seed(
            normalized,
            route_source=route_source,
            reason_code="THALAMUS_RULE_CONVERSATION_REPAIR",
            semantic_type="conversation_repair",
            dialogue_act="correct_or_repair_previous_understanding",
            context_binding="recent_conversation",
            answer_strategy="admit_mismatch_and_rebind_to_user_target",
            confidence=0.94,
            domain=_semantic_domain_from_text(normalized),
        )
    if _QUALITY_META_FEEDBACK_RE.search(normalized):
        return _build_semantic_chat_seed(
            normalized,
            route_source=route_source,
            reason_code="THALAMUS_RULE_META_QUALITY_FEEDBACK",
            semantic_type="meta_quality_feedback",
            dialogue_act="critique_system_or_reply_quality",
            context_binding="recent_conversation",
            answer_strategy="diagnose_quality_issue_without_generic_assistant_tone",
            confidence=0.92,
            domain=_semantic_domain_from_text(normalized),
        )
    return None


def _coerce_semantic_intent(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    semantic_type = _normalize_text(value.get("type"))
    dialogue_act = _normalize_text(value.get("dialogue_act"))
    if not semantic_type and not dialogue_act:
        return {}
    semantic = {
        "type": semantic_type or dialogue_act,
        "dialogue_act": dialogue_act or semantic_type,
        "domain": _normalize_text(value.get("domain")) or "general",
        "requires_context": bool(value.get("requires_context")),
        "context_binding": _normalize_text(value.get("context_binding")) or "none",
        "answer_strategy": _normalize_text(value.get("answer_strategy")) or "model_generate_natural_reply",
        "confidence": _normalize_confidence(value.get("confidence")) or 0.7,
    }
    semantic["requires_context"] = bool(semantic["requires_context"] or semantic["context_binding"] != "none")
    return semantic


def _coerce_pragmatic_frame(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    user_move = _normalize_text(value.get("user_move"))
    implied_need = _normalize_text(value.get("implied_need"))
    expected_response = _normalize_text(value.get("expected_response"))
    if not user_move and not implied_need and not expected_response:
        return {}
    do_not = [
        _normalize_text(item)
        for item in list(value.get("do_not") or [])
        if _normalize_text(item)
    ]
    return {
        "user_move": user_move or "open_chat",
        "implied_need": implied_need or "natural_response",
        "conversation_anchor": _normalize_text(value.get("conversation_anchor")) or "none",
        "likely_emotion": _normalize_text(value.get("likely_emotion")) or "neutral",
        "expected_response": expected_response or "respond_to_the_user_message_directly",
        "answer_focus": _normalize_text(value.get("answer_focus")) or "current_user_utterance",
        "domain": _normalize_text(value.get("domain")) or "general",
        "do_not": do_not,
        "repair_mode": bool(value.get("repair_mode")),
        "confidence": _normalize_confidence(value.get("confidence")) or 0.62,
    }


def _infer_semantic_intent_from_text(
    *,
    text: str,
    route: str,
    target_id: str,
    route_reason: str,
) -> dict[str, Any]:
    structured_seed = _build_structured_chat_semantic_seed(text, route_source="thalamus.semantic")
    if structured_seed is not None:
        return _coerce_semantic_intent(structured_seed.get("semantic_intent"))
    normalized_route = _normalize_text(route, lower=True)
    normalized_target = _normalize_text(target_id, lower=True)
    normalized_reason = _normalize_text(route_reason)
    if normalized_route == "skill":
        domain = _semantic_domain_from_text(text, default="workspace_runtime")
        return {
            "type": "tool_or_work_request",
            "dialogue_act": "request_action",
            "domain": domain,
            "requires_context": False,
            "context_binding": "none",
            "answer_strategy": "handoff_to_skill_or_codex_backend",
            "confidence": 0.86,
            "target_id": normalized_target,
        }
    if normalized_reason.startswith("THALAMUS_RULE_PHATIC") or normalized_reason == "THALAMUS_RULE_CONNECTIVITY_CHECK":
        if normalized_reason == "THALAMUS_RULE_PHATIC_CONTINUATION":
            return {
                "type": "continue_active_context",
                "dialogue_act": "continue_or_ask_next_step",
                "domain": _semantic_domain_from_text(text),
                "requires_context": True,
                "context_binding": "active_task_or_topic",
                "answer_strategy": "continue_current_task_or_topic",
                "confidence": 0.84,
            }
        return {
            "type": "phatic_or_presence_check",
            "dialogue_act": "light_social_contact",
            "domain": "companion_chat",
            "requires_context": False,
            "context_binding": "none",
            "answer_strategy": "short_warm_reply",
            "confidence": 0.88,
        }
    if normalized_route == "chat":
        return {
            "type": "open_chat",
            "dialogue_act": "chat",
            "domain": _semantic_domain_from_text(text),
            "requires_context": False,
            "context_binding": "none",
            "answer_strategy": "model_generate_natural_reply",
            "confidence": 0.62,
        }
    return {}


def _strip_step_lead_in(text: str) -> str:
    stripped = _normalize_text(text)
    while True:
        next_value = _STEP_LEAD_IN_RE.sub("", stripped).strip(" \t,，;；:：")
        if next_value == stripped:
            return stripped
        stripped = next_value


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
    if _TASK_MEMORY_RE.search(normalized) or _TASK_MEMORY_FACT_RE.search(normalized):
        return False
    return bool(_TASK_OPEN_APP_VERB_RE.search(normalized) and _TASK_OPEN_APP_HINT_RE.search(normalized))


def _build_memory_fact_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized or not _TASK_MEMORY_FACT_RE.search(normalized):
        return None
    return {
        "route": "skill",
        "target_id": "memory_store",
        "task": normalized,
        "reason_code": "THALAMUS_RULE_MEMORY_FACT_STORE",
        "confidence": 0.96,
        "route_source": "thalamus.rule",
        "args": {
            "content": normalized,
            "query": normalized,
            "text": normalized,
            "source": "thalamus",
            "user_message": normalized,
        },
    }


def _build_codex_workspace_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if _looks_like_conversational_opinion_chat(normalized):
        return None
    has_git_action = bool(_WORKSPACE_GIT_ACTION_RE.search(normalized))
    if not _WORKSPACE_DOMAIN_RE.search(normalized) and not has_git_action:
        return None
    has_write = bool(_WORKSPACE_WRITE_RE.search(normalized))
    has_inspect = bool(_WORKSPACE_INSPECT_RE.search(normalized) and _WORKSPACE_EXPLICIT_INSPECT_RE.search(normalized))
    has_active_verify = bool(_WORKSPACE_ACTIVE_VERIFY_RE.search(normalized))
    if not (has_write or has_inspect or has_active_verify or has_git_action):
        return None

    target_id = "agent_worker" if has_write or has_active_verify or has_git_action else "agent_inspect"
    run_mode = "worker" if target_id == "agent_worker" else "inspect"
    return {
        "route": "skill",
        "target_id": target_id,
        "task": normalized,
        "reason_code": f"THALAMUS_RULE_CODEX_WORKSPACE_{run_mode.upper()}",
        "confidence": 0.94 if target_id == "agent_worker" else 0.91,
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


def _parse_codex_recommendation_index(text: str) -> int | None:
    match = _CODEX_EPUB_RECOMMENDATION_SELECTION_RE.search(_normalize_text(text))
    if not match:
        return None
    raw = str(next((group for group in match.groups() if group), "")).strip()
    if not raw:
        return None
    if raw.isdigit():
        return max(1, int(raw))
    return _CODEX_EPUB_RECOMMENDATION_INDEX_WORDS.get(raw)


def _build_codex_production_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    target_id = ""
    recommendation_index = _parse_codex_recommendation_index(normalized)
    if recommendation_index:
        target_id = "agent_epub_builder"
    elif _CODEX_WORKBENCH_BRIEF_RE.search(normalized):
        target_id = "agent_workbench_brief"
    elif _CODEX_REPO_SENTINEL_RE.search(normalized) and _CODEX_PRODUCTION_BACKGROUND_RE.search(normalized):
        target_id = "agent_repo_sentinel"
    elif _CODEX_MEMORY_CURATOR_RE.search(normalized):
        target_id = "agent_memory_curator"
    elif _CODEX_DOCUMENT_DIGEST_RE.search(normalized):
        target_id = "agent_document_digest"
    elif _CODEX_EPUB_BUILDER_RE.search(normalized):
        target_id = "agent_epub_builder"
    elif _CODEX_WEB_CORPUS_COLLECTOR_RE.search(normalized):
        target_id = "agent_web_corpus_collector"
    elif _CODEX_LIGHTNOVEL_RECOMMENDER_RE.search(normalized):
        target_id = "agent_lightnovel_recommender"
    elif _CODEX_FRONTEND_ACCEPTANCE_RE.search(normalized):
        target_id = "agent_frontend_acceptance"
    elif _CODEX_BROWSER_RESEARCH_RE.search(normalized):
        target_id = "browser_research"
    elif _CODEX_FRONTEND_REVIEW_RE.search(normalized):
        target_id = "agent_frontend_review"
    if not target_id:
        return None
    url = _extract_first_url(normalized)
    args = {
        "task": normalized,
        "problem_statement": normalized,
        "start_background": True,
        "text": normalized,
        "user_message": normalized,
    }
    if url:
        args["urls"] = [url]
    if recommendation_index:
        args["recommendation_index"] = recommendation_index
    if target_id in {"agent_lightnovel_recommender", "agent_epub_builder"} or "lightnovel.fun" in normalized:
        args["site_adapter"] = "lightnovel.fun"
    return {
        "route": "skill",
        "target_id": target_id,
        "task": normalized,
        "reason_code": "THALAMUS_RULE_CODEX_PRODUCTION",
        "confidence": 0.93,
        "route_source": "thalamus.rule",
        "args": args,
    }


def _build_codex_epub_route_from_recommendation_context(
    text: str,
    recommendation_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized or not _CODEX_EPUB_CONTEXT_ACTION_RE.search(normalized):
        return None
    context = dict(recommendation_context or {})
    if not context:
        return None
    item = dict(context.get("referenced_item") or {})
    if not item:
        index = context.get("reference_index")
        try:
            target_rank = int(index)
        except Exception:
            target_rank = 0
        for candidate in list(context.get("items") or []):
            if not isinstance(candidate, Mapping):
                continue
            rank = int(candidate.get("rank") or 0)
            if target_rank and rank == target_rank:
                item = dict(candidate)
                break
        if not item and len(list(context.get("items") or [])) == 1 and isinstance(list(context.get("items") or [])[0], Mapping):
            item = dict(list(context.get("items") or [])[0])
    detail_url = _normalize_text(item.get("detail_url") or item.get("url"))
    title = _normalize_text(item.get("title") or item.get("name"))
    if not detail_url and not title:
        return None
    task = normalized
    if title:
        task = f"{normalized}：{title}"
    args = {
        "task": task,
        "problem_statement": task,
        "start_background": True,
        "text": normalized,
        "user_message": normalized,
        "site_adapter": "lightnovel.fun",
        "selected_recommendation": item,
        "recent_recommendation_context": context,
        "recommendation_index": context.get("reference_index") or item.get("rank"),
    }
    if detail_url:
        args["urls"] = [detail_url]
    if title:
        args["title"] = title
        args["candidate_titles"] = [title]
    if context.get("delivery_id"):
        args["delivery_id"] = context.get("delivery_id")
    return {
        "route": "skill",
        "target_id": "agent_epub_builder",
        "task": task,
        "reason_code": "THALAMUS_RULE_CODEX_EPUB_RECENT_RECOMMENDATION_CONTEXT",
        "confidence": 0.94,
        "route_source": "thalamus.context",
        "args": args,
    }


def _build_system_observation_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None

    if _TASK_RESOURCE_PRESSURE_ANALYSIS_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "resource_pressure_analysis",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_RESOURCE_PRESSURE_ANALYSIS",
            "confidence": 0.97,
            "route_source": "thalamus.rule",
            "args": {
                "top_n": 40,
                "sample_seconds": 0.5,
                "persist": True,
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _TASK_RESOURCE_MANAGER_STATUS_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "resource_manager_status",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_RESOURCE_MANAGER_STATUS",
            "confidence": 0.96,
            "route_source": "thalamus.rule",
            "args": {
                "create_if_missing": False,
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _TASK_RESOURCE_RELEASE_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "resource_release",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_RESOURCE_RELEASE_DRY_RUN",
            "confidence": 0.95,
            "route_source": "thalamus.rule",
            "args": {
                "mode": "dry_run",
                "dry_run": True,
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _TASK_PROCESS_RESOURCE_TOP_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "process_resource_top",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_PROCESS_RESOURCE_TOP",
            "confidence": 0.96,
            "route_source": "thalamus.rule",
            "args": {
                "limit": 20,
                "sort_by": "memory",
                "include_gpu": True,
                "text": normalized,
                "user_message": normalized,
            },
        }
    if _TASK_RESOURCE_SNAPSHOT_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "system_resource_snapshot",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_SYSTEM_RESOURCE_SNAPSHOT",
            "confidence": 0.96,
            "route_source": "thalamus.rule",
            "args": {
                "include_gpu": True,
                "include_processes": True,
                "include_network": False,
                "top_n": 12,
                "text": normalized,
                "user_message": normalized,
            },
        }
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


def _build_pentest_route_seed(text: str) -> dict[str, Any] | None:
    normalized = _normalize_text(text)
    if not normalized:
        return None
    has_exec_marker = bool(_PENTEST_EXEC_MARKER_RE.search(normalized))
    has_structured_scope = bool(_PENTEST_STRUCTURED_SCOPE_RE.search(normalized))
    has_operation_hint = bool(_PENTEST_OPERATION_HINT_RE.search(normalized))
    if not has_exec_marker and not (has_structured_scope and has_operation_hint):
        return None
    if _PENTEST_ADVICE_ONLY_RE.search(normalized):
        return None

    has_scope_hint = bool(_PENTEST_SCOPE_HINT_RE.search(normalized) or has_structured_scope)
    if not has_scope_hint:
        return None

    goal_type = _classify_pentest_requested_goal(normalized)

    args: dict[str, Any] = {
        "text": normalized,
        "user_message": normalized,
        "scope_hint_present": has_scope_hint,
        "requested_execution": True,
        "secure_run_required": True,
        "domain_pack_capability_id": "pentest_autonomous",
        "secure_run_surface": "pentest.secure-run",
    }

    if goal_type is not None:
        # Goal is untrusted request material for Domain Pack planning.  It is
        # never an execution strategy or an authority grant.
        args["requested_goal"] = {"goal_type": goal_type}

    return {
        "route": "delegate",
        "target_id": "pentest_agent",
        "task": normalized,
        "reason_code": "THALAMUS_RULE_PENTEST_AGENT",
        "confidence": 0.93,
        "route_source": "thalamus.rule",
        "args": args,
    }


def _classify_pentest_requested_goal(normalized: str) -> str | None:
    """Classify an untrusted requested goal for planning only."""
    # 细粒度意图优先（避免"拿下"/"打"等宽泛词抢跑）
    _FINE_GRAINED: list[tuple[str, str]] = [
        ("_PENTEST_INTENT_DOMAIN_ADMIN_RE", "domain_admin"),
        ("_PENTEST_INTENT_ROOT_RE", "root"),
        ("_PENTEST_INTENT_FLAG_RE", "read_flag"),
        ("_PENTEST_INTENT_CHAIN_RE", "chain_depth"),
        ("_PENTEST_INTENT_CRITICAL_RE", "n_critical"),
        ("_PENTEST_INTENT_SERVICE_RE", "service_compromise"),
    ]
    for ref_name, goal_type in _FINE_GRAINED:
        regex = globals().get(ref_name)
        if regex is not None and regex.search(normalized):
            return goal_type
    # 清场/全域拿下 — 必须在 shell 之前检查，因为"全部 getshell"是 takeover
    if _PENTEST_TAKEOVER_RE.search(normalized):
        return "domain_takeover"
    # Shell 最后：纯 getshell 不带清场语义
    if _PENTEST_INTENT_SHELL_RE.search(normalized):
        return "shell"
    return None


def _build_pentest_progress_seed(text: str) -> dict[str, Any] | None:
    """Route progress language to the owner-bound canonical read handoff."""
    normalized = _normalize_text(text)
    if not normalized:
        return None
    if not _PENTEST_PROGRESS_QUERY_RE.search(normalized):
        return None
    # 否定守卫：排除非渗透语境（情绪/系统状态等）
    if _PENTEST_PROGRESS_NEGATIVE_RE.search(normalized):
        return None
    return {
        "route": "delegate",
        "target_id": "pentest_agent",
        "task": normalized,
        "reason_code": "THALAMUS_RULE_PENTEST_PROGRESS",
        "confidence": 0.92,
        "route_source": "thalamus.rule",
        "args": {
            "text": normalized,
            "user_message": normalized,
            "action": "task_progress",
            "secure_principal_required": True,
        },
    }


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
    if _looks_like_conversational_opinion_chat(cleaned):
        return None

    if _contains_any(cleaned, _DIRECT_SUMMARIZE_MARKERS) and _contains_any(cleaned, _DIRECT_SUMMARIZE_CONTEXT_MARKERS):
        return ("skill", "session_summary", "THALAMUS_RULE_SESSION_SUMMARY")
    if _contains_any(cleaned, _DIRECT_EMOTION_MARKERS) and not _looks_like_companion_open_chat(cleaned):
        return ("skill", "emotion_query", "THALAMUS_RULE_EMOTION_QUERY")
    if _contains_any(cleaned, _DIRECT_BOND_MARKERS):
        return ("skill", "bond_query", "THALAMUS_RULE_BOND_QUERY")
    if _contains_any(cleaned, _DIRECT_MEMORY_RECALL_MARKERS):
        return ("skill", "memory_recall", "THALAMUS_RULE_MEMORY_RECALL")
    codex_production_seed = _build_codex_production_route_seed(cleaned)
    if codex_production_seed is not None:
        return (
            "skill",
            _normalize_text(codex_production_seed.get("target_id")),
            _normalize_text(codex_production_seed.get("reason_code")) or "THALAMUS_RULE_CODEX_PRODUCTION",
        )
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

    codex_production_seed = _build_codex_production_route_seed(cleaned)
    if codex_production_seed is not None:
        return codex_production_seed

    pentest_seed = _build_pentest_route_seed(cleaned)
    if pentest_seed is not None:
        return pentest_seed

    pentest_progress_seed = _build_pentest_progress_seed(cleaned)
    if pentest_progress_seed is not None:
        return pentest_progress_seed

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

    if _looks_like_status_reply_chat(cleaned):
        return {
            "route": "chat",
            "target_id": "",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_STATUS_REPLY_CHECK",
            "confidence": 0.98,
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
        codex_target = "agent_self_evolve" if codex_runtime_mode == "self_evolve" else "agent_self_heal"
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

    system_seed = _build_system_observation_route_seed(cleaned)
    if system_seed is not None:
        return system_seed

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

    if _looks_like_contextual_chat_followup(cleaned):
        return _build_contextual_chat_followup_seed(cleaned, route_source="thalamus.rule")

    workspace_seed = _build_codex_workspace_route_seed(cleaned)
    if workspace_seed is not None:
        return workspace_seed

    semantic_chat_seed = _build_structured_chat_semantic_seed(cleaned, route_source="thalamus.rule")
    if semantic_chat_seed is not None:
        return semantic_chat_seed

    if _looks_like_conversational_opinion_chat(cleaned):
        return _build_conversational_opinion_chat_seed(cleaned, route_source="thalamus.rule")

    system_seed = _build_system_observation_route_seed(cleaned)
    if system_seed is not None:
        return system_seed

    if _TASK_CHAT_CONTINUATION_RE.match(cleaned):
        return {
            "route": "chat",
            "target_id": "",
            "task": cleaned,
            "reason_code": "THALAMUS_RULE_PHATIC_CONTINUATION",
            "confidence": 0.96,
            "route_source": "thalamus.rule",
            "args": {
                "text": cleaned,
            },
        }

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

    if _TASK_RECENT_TURN_STRICT_RE.search(normalized):
        return {
            "route": "recent_turn",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_RECENT_TURN",
            "confidence": 0.97,
            "route_source": "rule",
        }

    if _looks_like_contextual_chat_followup(normalized):
        return _build_contextual_chat_followup_seed(normalized, route_source="rule")

    if _TASK_MEMORY_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "memory_store",
            "task": normalized,
            "reason_code": "RULE_FORCE_MEMORY_STORE",
            "confidence": 0.96,
            "route_source": "rule",
            "args": {
                "content": normalized,
                "query": normalized,
                "text": normalized,
                "source": "thalamus",
                "user_message": normalized,
            },
        }

    direct_seed = _build_direct_route_seed(normalized)
    if isinstance(direct_seed, dict):
        return direct_seed

    memory_fact_seed = _build_memory_fact_route_seed(normalized)
    if isinstance(memory_fact_seed, dict):
        return memory_fact_seed

    compact = re.sub(r"\s+", "", normalized).lower()

    if _TASK_RECENT_TURN_RE.search(normalized):
        return {
            "route": "recent_turn",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_RECENT_TURN",
            "confidence": 0.95,
            "route_source": "rule",
        }

    if _TASK_PROGRESS_QUERY_RE.search(compact):
        return {
            "route": "task_progress",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_TASK_PROGRESS",
            "confidence": 0.98,
            "route_source": "rule",
        }

    semantic_chat_seed = _build_structured_chat_semantic_seed(normalized, route_source="rule")
    if semantic_chat_seed is not None:
        return semantic_chat_seed

    if _looks_like_contextual_chat_followup(normalized):
        return _build_contextual_chat_followup_seed(normalized, route_source="rule")

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

    if _TASK_PLUGIN_BUILD_RE.search(normalized):
        return {
            "route": "skill",
            "target_id": "agent_worker",
            "task": normalized,
            "reason_code": "RULE_FORCE_agent_worker",
            "confidence": 0.93,
            "route_source": "rule",
        }

    if (
        _TASK_CODE_RE.search(normalized)
        and not _TASK_ENTERTAINMENT_RE.search(normalized)
        and not _TASK_CODE_NEGATION_RE.search(normalized)
    ):
        codex_target = "agent_inspect" if _looks_like_lightweight_codex_request(normalized) else "agent_worker"
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
    "agent_worker",
    "agent_inspect",
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
        or action in {"work_code", "agent_worker", "agent_inspect"}
        or action.startswith("codex_")
        or "codex" in reason
    ):
        return SCENE_WORK_CODE
    if route in {"task_progress", "recent_turn"}:
        return SCENE_CHAT
    if route in {"delegate", "skill", "plan"}:
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
                    "forbidden_actions": ["agent_worker", "long_reasoning", "skill_execution"],
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

    pentest_seed = _build_pentest_route_seed(normalized)
    if pentest_seed is not None:
        return pentest_seed

    if _looks_like_simple_technical_advice(normalized):
        return _build_simple_technical_chat_seed(normalized, route_source="rule.fastpath")

    if _TASK_RECENT_TURN_STRICT_RE.search(normalized):
        return {
            "route": "recent_turn",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_RECENT_TURN",
            "confidence": 0.97,
            "route_source": "rule.fastpath",
        }

    if _looks_like_contextual_chat_followup(normalized):
        return _build_contextual_chat_followup_seed(normalized, route_source="rule.fastpath")

    memory_steward_seed = _build_memory_steward_route_seed(normalized)
    if memory_steward_seed is not None:
        return memory_steward_seed

    codex_production_seed = _build_codex_production_route_seed(normalized)
    if codex_production_seed is not None:
        return codex_production_seed

    frontend_audit_seed = _build_frontend_audit_route_seed(normalized)
    if frontend_audit_seed is not None:
        return frontend_audit_seed

    workspace_seed = _build_codex_workspace_route_seed(normalized)
    if workspace_seed is not None:
        return workspace_seed

    semantic_chat_seed = _build_structured_chat_semantic_seed(normalized, route_source="rule.fastpath")
    if semantic_chat_seed is not None:
        return semantic_chat_seed

    compact = re.sub(r"\s+", "", normalized).lower()
    if _TASK_CHAT_CONTINUATION_RE.match(normalized):
        return {
            "route": "chat",
            "target_id": "",
            "task": normalized,
            "reason_code": "THALAMUS_RULE_PHATIC_CONTINUATION",
            "confidence": 0.96,
            "route_source": "rule.fastpath",
            "args": {
                "text": normalized,
            },
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
                    "forbidden_actions": ["agent_worker", "long_reasoning", "skill_execution"],
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

    if _TASK_PROGRESS_QUERY_RE.search(compact):
        return {
            "route": "task_progress",
            "target_id": "",
            "task": normalized,
            "reason_code": "RULE_TASK_PROGRESS",
            "confidence": 0.98,
            "route_source": "rule.fastpath",
        }

    semantic_chat_seed = _build_structured_chat_semantic_seed(normalized, route_source="rule.fastpath")
    if semantic_chat_seed is not None:
        return semantic_chat_seed
    if _looks_like_conversational_opinion_chat(normalized):
        return _build_conversational_opinion_chat_seed(normalized, route_source="rule.fastpath")
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


async def classify_task_intent(
    *,
    request_id: str,
    session_id: str,
    text: str,
    tribe_context: Mapping[str, Any] | None = None,
    attachments: Sequence[Mapping[str, Any]] | None = None,
    multimodal_observations: Mapping[str, Any] | None = None,
    application_ports: TribeApplicationPorts | None = None,
) -> dict[str, Any]:
    """Build a deterministic ingress route seed without spending model budget.

    Input classification runs before HostCommand admission.  It may normalize
    untrusted request material, but it must not call a model, create a plan,
    or derive an execution authority.  Model work starts only after the
    accepted TaskGraph has a node-bound budget and attempt.
    """
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
                "[%s] thalamus classify_task_intent: prefrontal_plan exists, retain deterministic ingress action=%s",
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
        return "agent_worker"
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
            "agent_inspect",
            "agent_worker",
            "agent_self_heal",
            "agent_self_evolve",
            "agent_sql_audit",
            "agent_translate_project",
            "agent_skill_forge",
            "agent_research",
            "agent_organ_scan",
            "agent_persona_drift_audit",
            "agent_dream_compile",
            "agent_workbench_brief",
            "agent_repo_sentinel",
            "agent_memory_curator",
            "agent_document_digest",
            "agent_frontend_review",
            "browser_research",
            "agent_frontend_acceptance",
            "agent_web_corpus_collector",
            "agent_lightnovel_recommender",
            "agent_epub_builder",
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
                if normalized_target == "agent_self_heal":
                    merged["run_mode"] = "self_heal"
                elif normalized_target == "agent_self_evolve":
                    merged["run_mode"] = "self_evolve"
                elif normalized_target == "agent_inspect":
                    merged["run_mode"] = "inspect"
                elif normalized_target == "agent_research":
                    merged["run_mode"] = "research"
                elif normalized_target == "agent_translate_project":
                    merged["run_mode"] = "translate"
                elif normalized_target == "agent_sql_audit":
                    merged["run_mode"] = "audit"
                elif normalized_target in {
                    "agent_workbench_brief",
                    "agent_document_digest",
                    "browser_research",
                }:
                    merged["run_mode"] = "research"
                elif normalized_target in {"agent_repo_sentinel", "agent_memory_curator", "agent_frontend_review", "agent_frontend_acceptance"}:
                    merged["run_mode"] = "inspect"
                elif normalized_target in {"agent_web_corpus_collector", "agent_lightnovel_recommender", "agent_epub_builder"}:
                    merged["run_mode"] = "worker"
                elif _looks_like_lightweight_codex_request(_normalize_text(merged.get("task")) or fallback_text):
                    merged["run_mode"] = "inspect"
                else:
                    merged["run_mode"] = "worker"
            resolved_run_mode = _normalize_text(merged.get("run_mode"), lower=True)
            if not explicit_workspace:
                if normalized_target in {
                    "agent_workbench_brief",
                    "agent_memory_curator",
                    "agent_document_digest",
                    "browser_research",
                    "agent_web_corpus_collector",
                    "agent_lightnovel_recommender",
                    "agent_epub_builder",
                }:
                    merged["workspace_strategy"] = "temp_workspace"
                elif normalized_target in {"agent_repo_sentinel", "agent_frontend_review", "agent_frontend_acceptance"}:
                    merged["workspace_strategy"] = "runtime_repo"
                elif resolved_run_mode == "inspect":
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


def _allocate_seq(
    *,
    session_id: str,
    existing_seq: Any,
    application_ports: TribeApplicationPorts | None,
) -> int:
    try:
        seq = int(existing_seq)
        if seq >= 0:
            return seq
    except (TypeError, ValueError):
        pass

    try:
        return int(
            application_ports.require("allocate_intent_seq")(
                session_id=session_id,
            )
        )
    except Exception:
        return 0


def _merge_preserving_truthy(primary: Mapping[str, Any] | None, secondary: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(primary or {})
    for key, value in dict(secondary or {}).items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = value
    return merged


@dataclass(slots=True)
class ThalamusAgent:
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
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
                    application_ports=self.application_ports,
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
        preference_pack: dict[str, Any] = {}
        try:
            preference_pack = dict(
                self.application_ports.require("build_user_preference_pack")(
                    person_id=_normalize_text(person_id) or None,
                    query_text=task or user_message,
                    session_id=_normalize_text(session_id) or None,
                    conversation_id=_normalize_text(session_id) or None,
                )
                or {}
            )
        except Exception:
            preference_pack = {}
        epub_context_seed = _build_codex_epub_route_from_recommendation_context(
            task or user_message,
            dict(preference_pack or {}).get("recent_recommendation_context"),
        )
        if epub_context_seed and not (route == "skill" and target_id == "agent_epub_builder"):
            target_id = _normalize_text(epub_context_seed.get("target_id")) or "agent_epub_builder"
            route = _normalize_text(epub_context_seed.get("route"), lower=True) or "skill"
            task = _normalize_text(epub_context_seed.get("task")) or task
            route_reason = _normalize_text(epub_context_seed.get("reason_code")) or route_reason
            route_source = _normalize_text(epub_context_seed.get("route_source")) or "thalamus.context"
            route_confidence = _normalize_confidence(epub_context_seed.get("confidence")) or 0.94
            action = _resolve_action(route=route, target_id=target_id, fallback=epub_context_seed.get("action"))
            args_payload = dict(epub_context_seed.get("args") or {})
            args_payload = _backfill_canonical_args(
                route=route,
                target_id=target_id,
                text=task or user_message,
                args_payload=args_payload,
            )
        semantic_intent = _coerce_semantic_intent(draft.get("semantic_intent")) or _coerce_semantic_intent(
            args_payload.get("semantic_intent")
        )
        if not semantic_intent:
            semantic_intent = _infer_semantic_intent_from_text(
                text=task or user_message,
                route=route,
                target_id=target_id,
                route_reason=route_reason,
            )
        if semantic_intent:
            args_payload["semantic_intent"] = semantic_intent
        pragmatic_frame = _coerce_pragmatic_frame(draft.get("pragmatic_frame")) or _coerce_pragmatic_frame(
            args_payload.get("pragmatic_frame")
        )
        if not pragmatic_frame:
            pragmatic_frame = _build_pragmatic_frame(
                text=task or user_message,
                semantic_intent=semantic_intent,
                route=route,
                target_id=target_id,
                route_reason=route_reason,
            )
        if pragmatic_frame:
            args_payload["pragmatic_frame"] = pragmatic_frame
        conversation_understanding = {}
        try:
            experience_read_pack = dict(
                self.application_ports.require(
                    "build_conversation_experience_read_pack"
                )(
                    session_id=_normalize_text(session_id),
                    person_id=_normalize_text(person_id) or None,
                    domain=_normalize_text(semantic_intent.get("domain"))
                    if semantic_intent
                    else None,
                    query_text=task or user_message,
                    lesson_limit=5,
                )
                or {}
            )
            previous_belief = dict(
                experience_read_pack.get("previous_belief") or {}
            )
            experience_lessons = [
                dict(item)
                for item in list(experience_read_pack.get("experience_lessons") or [])
                if isinstance(item, Mapping)
            ]
            if not preference_pack:
                try:
                    preference_pack = dict(
                        self.application_ports.require("build_user_preference_pack")(
                            person_id=_normalize_text(person_id) or None,
                            query_text=task or user_message,
                            session_id=_normalize_text(session_id) or None,
                            conversation_id=_normalize_text(session_id) or None,
                        )
                        or {}
                    )
                except Exception:
                    preference_pack = {}
            conversation_understanding = self.application_ports.require(
                "build_conversation_understanding_pack"
            )(
                text=task or user_message,
                semantic_intent=semantic_intent,
                pragmatic_frame=pragmatic_frame,
                recent_recommendation_context=dict(preference_pack or {}).get(
                    "recent_recommendation_context"
                ),
                previous_belief=previous_belief,
                experience_lessons=experience_lessons,
            )
        except Exception:
            conversation_understanding = {}
        if conversation_understanding:
            args_payload["conversation_understanding"] = conversation_understanding
        compiled = dict(draft)
        compiled.update(
            {
                "schema_version": str(draft.get("schema_version") or _INTENT_SCHEMA_VERSION).strip() or _INTENT_SCHEMA_VERSION,
                "id": _normalize_text(draft.get("id")) or f"intent::{request_id}",
                "ts_ms": int(draft.get("ts_ms") or ts_ms or now_ms()),
                "session_id": _normalize_text(draft.get("session_id")) or _normalize_text(session_id),
                "seq": _allocate_seq(
                    session_id=_normalize_text(session_id),
                    existing_seq=draft.get("seq"),
                    application_ports=self.application_ports,
                ),
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
        if semantic_intent:
            compiled["semantic_intent"] = semantic_intent
        if pragmatic_frame:
            compiled["pragmatic_frame"] = pragmatic_frame
        if conversation_understanding:
            compiled["conversation_understanding"] = conversation_understanding

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
            "semantic_intent_type": _normalize_text(semantic_intent.get("type")) if semantic_intent else None,
            "semantic_domain": _normalize_text(semantic_intent.get("domain")) if semantic_intent else None,
            "semantic_context_binding": _normalize_text(semantic_intent.get("context_binding")) if semantic_intent else None,
            "pragmatic_user_move": _normalize_text(pragmatic_frame.get("user_move")) if pragmatic_frame else None,
            "pragmatic_expected_response": _normalize_text(pragmatic_frame.get("expected_response")) if pragmatic_frame else None,
            "understanding_selected_intent": _normalize_text(conversation_understanding.get("selected_intent")) if conversation_understanding else None,
            "understanding_confidence": _normalize_confidence(conversation_understanding.get("confidence")) if conversation_understanding else None,
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
    "classify_task_intent_by_rules",
    "get_thalamus_agent",
    "resolve_scene_type_from_intent_decision",
    "set_thalamus_agent",
]
