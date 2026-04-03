"""chat_agent.py — ChatAgent 人格渲染管线（Phase XV）。

夏目蓝本人的"说话"模块。
职责：拿到上下文→构建 prompt→流式生成→反思链→净化→COMMIT。
调用者：DispatchAgent（通过 chat_agent_sse_generator 函数）。

架构原则：
  - ChatAgent 只负责「说出来」，不做路由/调度决策。
  - 所有输入通过 ChatAgentInput 结构体传入（sub_agent_context 可带子 Agent 结果）。
  - 所有输出经完整人格管线：角色人设 + 情感 + Bond + 风格蒸馏 + 反思链。
  - 不调用 SkillLoader，不做委托判断，不感知 sys_mode 路由。

关系：
  DispatchAgent  ──────────────▶  ChatAgent
   (路由决策/        ChatAgentInput   (人格渲染/
    Skill执行/        结构体传递       LLM流式生成/
    子Agent调度)                       COMMIT输出)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

_logger = logging.getLogger("Natsume.chat_agent")

_FALLBACK_GREETING_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_FALLBACK_PRESENCE_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_FALLBACK_AFFECTION_RE = re.compile(r"(想你|好想你|我想你了|想你啦|惦记你)")
_FALLBACK_STRENGTH_RE = re.compile(r"(特长|擅长|你会什么|你能做什么|你会做什么)")

_DELEGATE_MARKER_RE = re.compile(r"\[DELEGATE:[^\]]+\]", re.IGNORECASE)



# ---------------------------------------------------------------------------
# 输入/输出结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentContext:
    """子 Agent 执行结果，注入 ChatAgent prompt。"""
    agent_id: str = ""
    task: str = ""
    result_text: str = ""
    success: bool = True
    latency_ms: int = 0
    error: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatAgentInput:
    """ChatAgent 的完整输入上下文。"""
    # ── 必填 ──
    request_id: str = ""
    session_id: str = ""
    user_message: str = ""           # 净化后的用户消息
    person_id: str = ""

    # ── 情感 / Bond 上下文 ──
    emotion_context: Dict[str, Any] = field(default_factory=dict)
    emotion_snapshot: Dict[str, Any] = field(default_factory=dict)
    bond_summary: Dict[str, Any] = field(default_factory=dict)

    # ── 检索结果（RecallFusion 输出）──
    recall_slot_text: Dict[str, str] = field(default_factory=dict)
    retrieval_evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_summary: Dict[str, Any] = field(default_factory=lambda: {"status": "SKIPPED"})

    # ── 子 Agent 执行结果（执行路径时由 DispatchAgent 填入）──
    sub_agent_context: Optional[SubAgentContext] = None

    # ── 通道参数 ──
    render_target_lang: str = "zh"   # Phase XV: 双轨，文字通道永远 zh
    reply_channel: Optional[str] = None   # GATEWAY / ASR / WEB 等
    suppress_edge_tts: Optional[bool] = None
    suppress_edge_vts: Optional[bool] = None
    reply_depth_policy: str = "normal"    # "normal" / "gateway"（QQ 要求更短）

    # ── 链路追踪 ──
    thread_id: Optional[str] = None
    ts_ms: int = 0
    intent_draft: Dict[str, Any] = field(default_factory=dict)

    # ── LLM 运行时（由调用者 resolve 后传入，避免重复 resolve）──
    llm_runtime: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# 工具函数（从 top_agent.py 复用）
# ---------------------------------------------------------------------------

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_output(text: str) -> str:
    """出口净化：null byte + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text


def _strip_internal_delegate_markers(text: str) -> str:
    normalized = str(text or "")
    normalized = _DELEGATE_MARKER_RE.sub("", normalized)
    normalized = normalized.replace("{agent_result}", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    import re as _re
    _EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
        ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
        ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
        ("ANGRY", "生气|气死|烦|腹立|むかつく"),
        ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
        ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
    ]
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if _re.search(pattern, text, _re.IGNORECASE):
            return tag
    return "NEUTRAL"


async def _post_speak_sentence(
    *,
    text: str,
    utterance_id: str,
    emotion_tag: str,
    request_id: str,
    session_id: str,
) -> None:
    """Phase XV P7：向 Edge 发送单句 TTS 请求（fire-and-forget）。

    语音通道永远使用日语（lang_hint="ja"），不受 render_target_lang 影响。
    """
    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        await post_speak(
            text=text,
            utterance_id=utterance_id,
            request_id=request_id,
            session_id=session_id,
            emotion_tag=emotion_tag,
            lang_hint="ja",  # Phase XV: 语音通道永远日语
        )
    except Exception as exc:
        _logger.debug("[%s] P7 sentence TTS failed (non-critical): %s", request_id, exc)


def _extract_visible_text(raw: str) -> str:
    """从 LLM 原始输出中提取可见文本（支持 JSON / fenced JSON 包装格式）。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = str(fenced.group(1) or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    return str(narrative.get("text_zh") or narrative.get("text") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text

def _extract_tts_text(raw: str) -> str:
    """提取 TTS 语音通道文本（日语，text_ja 字段）。"""
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    # Phase XV P6：TTS 语音通道取 text_ja
                    return str(narrative.get("text_ja") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return ""


def _normalize_plan_step_description(description: str) -> str:
    text = str(description or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:蓝蓝|夏目蓝|夏目藍|小蓝|阿蓝)[，,、:：\s]*", "", text)
    prefixes = ("先", "然后", "再", "接着", "顺便", "帮我", "给我", "请")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
    return text.strip(" 　。！？!?，,；;：:")


def _render_plan_step_success_cn(step: Dict[str, Any]) -> str:
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or "步骤").strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""))
    result_text = str(step.get("result_text") or "").strip()

    if target_id == "web_search":
        topic = description or "相关内容"
        topic = re.sub(r"^(?:搜索(?:一下)?|查(?:一下)?|帮我搜索(?:一下)?|帮我查(?:一下)?|给我搜索(?:一下)?|给我查(?:一下)?)\s*", "", topic)
        return f"第{step_index}步，我已经帮你查过{topic or '相关内容'}了。"
    if target_id == "memory_store":
        if description.startswith("记住"):
            remembered = description.removeprefix("记住").strip()
            if remembered:
                if remembered.startswith("我"):
                    remembered = "你" + remembered[1:]
                return f"第{step_index}步，我也记住{remembered}了。"
        return f"第{step_index}步，我已经把这件事记下来了。"
    if target_id == "system_info":
        return f"第{step_index}步，我已经把系统信息看过了。"
    if target_id == "process_list":
        return f"第{step_index}步，我也把进程列表看过了。"
    if target_id == "network_status":
        return f"第{step_index}步，我已经检查过网络状态了。"
    if target_id == "hub_skill_search":
        topic = description or "相关内容"
        topic = re.sub(r"^(?:搜索技能|查找技能|搜技能)\s*", "", topic).strip() or "相关内容"
        return f"第{step_index}步，我帮你搜到和{topic}相关的技能了。"
    if target_id == "hub_skill_list":
        return f"第{step_index}步，我也把现在已装好的 Hub 技能整理给你了。"
    if target_id == "hub_skill_install":
        return f"第{step_index}步，我已经把这个 Hub 技能装好了。"
    if target_id == "hub_skill_remove":
        return f"第{step_index}步，我已经把这个 Hub 技能卸载掉了。"
    if target_id == "file_read":
        return f"第{step_index}步，我已经把文件内容看过了。"
    if target_id == "file_write":
        return f"第{step_index}步，我已经把文件写好了。"
    if target_id == "file_list":
        return f"第{step_index}步，我已经把目录内容看过了。"
    if target_id == "code_agent":
        return f"第{step_index}步，我已经把代码相关的处理整理好了。"
    if target_id == "intent_agent":
        return f"第{step_index}步，我已经把执行方案确认好了。"
    if result_text:
        return f"第{step_index}步，{description or target_id}这部分我已经处理好了。"
    return f"第{step_index}步，我已经把{description or target_id}这部分处理好了。"


def _render_plan_step_failure_cn(step: Dict[str, Any]) -> str:
    step_index = int(step.get("step_index") or 0)
    target_id = str(step.get("target_id") or "步骤").strip()
    description = _normalize_plan_step_description(str(step.get("description") or step.get("task_description") or ""))
    error_text = str(step.get("error") or "").strip()
    recovery_text = str(step.get("recovery_result_text") or "").strip()
    base = f"第{step_index}步，{description or target_id}这部分没有顺利完成。"
    if error_text:
        base += f"原因大概是：{error_text}。"
    if recovery_text:
        base += f"我另外整理了一个补救建议：{recovery_text}。"
    return base


def _build_plan_execution_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    steps = list(payload.get("steps") or [])
    if not steps:
        return None
    total_steps = int(payload.get("total_steps") or len(steps))
    parallel_enabled = bool(payload.get("parallel_enabled"))
    success_count = int(payload.get("success_count") or 0)
    intro = (
        f"我把这件事分成了{total_steps}步，刚才已经并行处理过了。"
        if parallel_enabled and total_steps > 1
        else f"我把这件事分成了{total_steps}步，刚才已经按顺序处理过了。"
    )
    parts = [intro]
    for raw_step in steps:
        step = dict(raw_step or {})
        if bool(step.get("success")):
            parts.append(_render_plan_step_success_cn(step))
        else:
            parts.append(_render_plan_step_failure_cn(step))
    if success_count == len(steps):
        parts.append("现在这些都已经处理好了。")
    else:
        parts.append("有没做顺的地方，我也把补救建议一起整理给你了。")
    return "".join(parts)


def _infer_hub_skill_name_from_task(task: str, *, markers: tuple[str, ...], default_name: str) -> str:
    normalized = str(task or "").strip()
    for marker in markers:
        if marker in normalized:
            normalized = normalized.split(marker, 1)[-1].strip(" ：:")
            break
    normalized = re.sub(r"^(?:蓝蓝|夏目蓝|夏目蓝蓝|natsume ai|natsume)[，,\s:：]*", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"\s+(?:强制|覆盖|覆盖安装|立即激活|现在激活)$", "", normalized, flags=re.IGNORECASE).strip()
    normalized = normalized.strip("，。；！？,.!? ")
    return normalized or default_name


def _format_discovery_source_names_cn(values: list[Any]) -> str:
    names = []
    for item in values:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            names.append(name)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return "、".join(names)


def _format_hub_skill_name_list_cn(values: list[Any], *, limit: int = 6) -> str:
    names = [str(item or "").strip() for item in values if str(item or "").strip()]
    if not names:
        return ""
    preview = "、".join(names[:limit])
    if len(names) > limit:
        preview += " 等"
    return preview


def _build_hub_skill_install_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    name = str(detail.get("name") or _infer_hub_skill_name_from_task(ctx.task, markers=("安装 hub 技能", "安装技能", "安装skill"), default_name="这个技能")).strip() or "这个技能"
    message = str(detail.get("message") or ctx.result_text or ctx.error or "").strip()
    success = bool(detail.get("success", ctx.success))
    activated = bool(detail.get("activated"))
    repair_attempted = bool(detail.get("repair_attempted"))
    repair_succeeded = bool(detail.get("repair_succeeded"))
    repair_actions = int(detail.get("repair_actions") or 0)
    active_skill_names = list(detail.get("active_skill_names") or [])
    if not success:
        parts = [f"{name} 这个技能还没有装好。"]
        if message:
            parts.append(f"卡住的原因大概是：{message}。")
        return "".join(parts)
    parts = [f"我已经把 {name} 装好了。"]
    if activated:
        parts.append("现在它已经接进我的能力里了。")
    else:
        parts.append("不过它还没完全激活，我还得继续补一下。")
    preview = _format_hub_skill_name_list_cn(active_skill_names)
    if preview:
        parts.append(f"当前已经挂上的技能里，有 {preview}。")
    if repair_attempted:
        if repair_succeeded:
            if repair_actions > 0:
                parts.append(f"安装过程中我还顺手修了 {repair_actions} 处依赖问题。")
            else:
                parts.append("安装过程中我也顺手做过一次自动补救。")
        else:
            parts.append("安装时我试着自动补救过，不过还有一部分没完全修好。")
    elif message and message not in {"installed ok", "install failed"}:
        if not (message.startswith("-") or "Installed " in message or "->" in message):
            parts.append(f"补充一下，{message}。")
    return "".join(parts)


def _build_hub_skill_search_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    query = str(detail.get("query") or payload.get("query") or _infer_hub_skill_name_from_task(ctx.task, markers=("搜索技能", "查找技能", "搜技能"), default_name="相关技能")).strip() or "相关技能"
    registry_error = str(detail.get("registry_error") or "").strip()
    discovery_sources = list(detail.get("discovery_sources") or [])
    if total <= 0:
        if registry_error:
            source_names = _format_discovery_source_names_cn(discovery_sources)
            if source_names:
                return f"我刚帮你连技能仓库看了一下，不过现在仓库暂时不可用。报错是：{registry_error}。你也可以先去 {source_names} 那边看看。"
            return f"我刚帮你连技能仓库看了一下，不过现在仓库暂时不可用。报错是：{registry_error}。"
        return f"我刚帮你搜了一下，暂时还没找到和 {query} 相关的技能。"
    previews: list[str] = []
    for item in skills[:3]:
        row = dict(item or {})
        name = str(row.get("name") or "未命名技能").strip() or "未命名技能"
        description = str(row.get("description") or "").strip()
        author = str(row.get("author") or "").strip()
        summary = name
        if description:
            summary += f"：{description}"
        if author:
            summary += f"（作者 {author}）"
        previews.append(summary)
    parts = [f"我帮你搜到 {total} 个相关技能。"]
    if previews:
        parts.append("比较靠前的是：" + "；".join(previews) + "。")
    return "".join(parts)


def _build_hub_skill_list_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    skills = list(detail.get("skills") or [])
    total = int(detail.get("total") or len(skills))
    if total <= 0:
        return "我这里现在还没有装新的 Hub 技能。"
    names = [dict(item or {}).get("name") for item in skills]
    preview = _format_hub_skill_name_list_cn(names)
    if preview:
        return f"我现在已经装了 {total} 个 Hub 技能，像 {preview} 这些都能用了。"
    return f"我现在已经装了 {total} 个 Hub 技能。"


def _build_hub_skill_remove_reply_cn(ctx: SubAgentContext) -> str | None:
    payload = dict(ctx.payload or {})
    detail = dict(payload.get("detail") or {})
    name = str(detail.get("name") or _infer_hub_skill_name_from_task(ctx.task, markers=("卸载技能", "删除技能"), default_name="这个技能")).strip() or "这个技能"
    message = str(detail.get("message") or ctx.result_text or ctx.error or "").strip()
    success = bool(detail.get("success", ctx.success))
    if success:
        if message and message not in {"removed", "remove failed"}:
            return f"我已经把 {name} 卸载掉了。{message}。"
        return f"我已经把 {name} 卸载掉了。"
    parts = [f"{name} 这个技能还没有卸载成功。"]
    if message:
        parts.append(f"现在卡住的地方是：{message}。")
    return "".join(parts)


def _build_hub_skill_reply_cn(ctx: SubAgentContext) -> str | None:
    agent_id = str(ctx.agent_id or "").strip()
    if agent_id == "skill:hub_skill_install":
        return _build_hub_skill_install_reply_cn(ctx)
    if agent_id == "skill:hub_skill_search":
        return _build_hub_skill_search_reply_cn(ctx)
    if agent_id == "skill:hub_skill_list":
        return _build_hub_skill_list_reply_cn(ctx)
    if agent_id == "skill:hub_skill_remove":
        return _build_hub_skill_remove_reply_cn(ctx)
    return None


def _build_deterministic_sub_agent_reply(
    ctx: SubAgentContext | None,
    *,
    render_target_lang: str,
) -> str | None:
    if ctx is None:
        return None
    agent_id = str(ctx.agent_id or "").strip()
    if render_target_lang != "zh":
        return None
    if agent_id == "plan_execution":
        return _build_plan_execution_reply_cn(ctx)
    if agent_id.startswith("skill:hub_skill_"):
        return _build_hub_skill_reply_cn(ctx)
    return None


def _build_chat_agent_fallback_reply(
    *,
    user_message: str,
    sub_agent_ctx: SubAgentContext | None,
    bond_summary: Dict[str, Any] | None,
    render_target_lang: str,
) -> str:
    text = str(user_message or "").strip()
    if render_target_lang == "ja":
        return "ちょっと待ってね……うまく言葉を整えるから。"

    if sub_agent_ctx is not None:
        agent_id = str(sub_agent_ctx.agent_id or "").strip()
        if agent_id == "code_agent":
            return "我先把这件事整理成更清楚一点的说法给你……刚才那一步没有顺好，但我不会把内部草稿直接丢给你。"
        if agent_id == "recent_turn_direct":
            return "我刚才那句话，我换个更清楚的说法告诉你。"
        if agent_id == "phatic_fastpath":
            return "我在呢。你想和我说什么？"

    if _FALLBACK_STRENGTH_RE.search(text):
        return _build_installed_capabilities_reply(render_target_lang=render_target_lang)
    if _FALLBACK_AFFECTION_RE.search(text):
        return "听你这么说，我心里也会暖一下。怎么突然想到我了？"
    if _FALLBACK_PRESENCE_RE.match(text):
        return "我在。怎么了？"
    if _FALLBACK_GREETING_RE.match(text):
        return "你好啊。今天是想和我随便聊聊，还是有事想和我说？"
    return "等我一下……我把这句话整理得更自然一点，再好好跟你说。"


def _build_installed_capabilities_reply(*, render_target_lang: str) -> str:
    if render_target_lang == "ja":
        return "今の私が使える能力を整理して伝えるね。少し待って。"

    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        from host_brain.app.agents.tool_registry import get_tools_for_agent  # noqa: PLC0415
        from host_brain.app.sub_agent_protocol import AGENT_CAPABILITIES  # noqa: PLC0415

        registry = get_skill_registry()
        skill_names = sorted(str(name).strip() for name in registry.list_skill_names() if str(name).strip())

        agent_summaries: list[str] = []
        for agent_id, desc in AGENT_CAPABILITIES.items():
            tools = get_tools_for_agent(agent_id, allow_side_effects=False)
            if not tools:
                continue
            tool_ids = [str(tool.tool_id or "").strip() for tool in tools if str(tool.tool_id or "").strip()]
            preview = "、".join(tool_ids[:4])
            if len(tool_ids) > 4:
                preview += " 等"
            agent_summaries.append(f"- {agent_id}：{desc}（可用工具：{preview}）")

        parts: list[str] = ["我现在能调动的能力，大致是这些："]
        if agent_summaries:
            parts.extend(agent_summaries[:6])
        if skill_names:
            preview_skills = "、".join(skill_names[:10])
            if len(skill_names) > 10:
                preview_skills += " 等"
            parts.append(f"另外，当前已安装的技能还有：{preview_skills}。")
        parts.append("如果你愿意，我也可以按你的问题，直接帮你挑最合适的一项来用。")
        return "\n".join(parts)
    except Exception:
        return "我现在能做的事不少，比如整理思路、翻译、执行操作、回忆信息、还有一些带工具的辅助能力。你如果告诉我想做什么，我可以直接替你选最合适的。"


def _should_buffer_stream_output(
    *,
    emotion_context: Any,
    bond_summary: Any,
) -> bool:
    """高风险对话缓冲判断。"""
    env_key = "HOST_STREAM_REFLECTION_BUFFER_ENABLED"
    raw = str(os.environ.get(env_key, "")).strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from host_brain.app.dependencies import get_reflection_chain  # noqa: PLC0415
        reflection = get_reflection_chain()
        return bool(
            reflection.should_reflect(
                emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""),
                intent=str((bond_summary or {}).get("intent") or ""),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 子 Agent 结果渲染（P1 改造：不再用正则拼接，统一走 ChatAgent 人格渲染）
# ---------------------------------------------------------------------------

def _build_sub_agent_context_prompt(ctx: SubAgentContext) -> str:
    """将子 Agent 结果转换为注入 ChatAgent prompt 的上下文块。

    Phase XV 核心改造：子 Agent 结果不再直接输出给用户，
    而是作为 context 注入 ChatAgent，由 ChatAgent 用夏目蓝语气重新组织。
    """
    if not ctx:
        return ""

    agent_id = str(ctx.agent_id or "").strip()
    task = str(ctx.task or "").strip()
    result_text = str(ctx.result_text or "").strip()[:2000]

    if not ctx.success or ctx.error:
        recovery_hint = f"  补充信息：{result_text}\n" if result_text else ""
        return (
            f"[子 Agent 执行结果]\n"
            f"  执行状态：失败\n"
            f"  Agent：{agent_id}\n"
            f"  任务：{task}\n"
            f"  错误：{ctx.error or '未知错误'}\n"
            f"{recovery_hint}"
            "  说明：请用夏目蓝的语气自然告知用户这次处理没有顺利完成。"
            " 如果有补充信息，就把它整理成有帮助的下一步建议。"
            " 不要提内部模块、Agent、网关、JSON、草稿、降级字样。\n"
        )

    if not result_text:
        return ""

    if agent_id == "code_agent":
        return (
            f"[子 Agent 执行结果]\n"
            f"  Agent：{agent_id}\n"
            f"  任务：{task}\n"
            f"  结果：{result_text}\n"
            "  说明：这是一份技术向中间结果。请把它整理成夏目蓝会对用户说的话。"
            " 先给用户一个简洁结论，再自然展开。不要说‘代码侧结果不稳定’、不要提 Agent、工具、草稿、JSON、ReAct、网关。"
            " 不要逐字照搬原文，要像自己理解后转述。\n"
        )

    if agent_id == "recent_turn_direct":
        return (
            "[当前已确认的信息]\n"
            f"  结果：{result_text}\n"
            "  说明：这里提供的是已核实的事实，不是最终台词。"
            " 请用自然、简洁、像夏目蓝本人说话的方式直接回答。"
            " 不要复读‘已确认事实’这种字样，不要说‘我看不到’或‘我这里没有看到’。\n"
        )

    if agent_id == "phatic_fastpath":
        return (
            "[当前语义目标]\n"
            f"  结果：{result_text}\n"
            "  说明：这是寒暄场景的语义目标，不是最终台词。"
            " 请只保留核心意图，用夏目蓝自己的说话方式重新组织。"
            " 要自然、克制，不要模板化，不要机械重复上一句，也不要堆太多省略号。\n"
        )

    if agent_id == "task_progress_direct":
        return (
            "[当前任务进度]\n"
            f"  信息：{result_text}\n"
            "  说明：这是当前任务状态摘要，不是最终台词。"
            " 请用夏目蓝本人说话的方式，直接告诉用户现在做到哪了；如果任务失败，也顺手说清下一步建议。\n"
        )

    if agent_id == "plan_execution":
        return (
            "[多步骤计划执行结果]\n"
            f"  任务：{task}\n"
            f"  结果：{result_text}\n"
            "  说明：这是父Agent整理好的多步骤执行摘要。"
            " 请用夏目蓝本人的语气，把完成了哪些步骤、有没有失败、失败后的建议，按自然口语重新说出来。\n"
        )

    return (
        f"[子 Agent 执行结果]\n"
        f"  Agent：{agent_id}\n"
        f"  任务：{task}\n"
        f"  结果：{result_text}\n"
        "  说明：请用夏目蓝的语气自然地呈现上述结果，不要逐字复读，要像自己说话一样组织语言。\n"
    )


# ---------------------------------------------------------------------------
# ChatAgent 主生成器
# ---------------------------------------------------------------------------

async def chat_agent_sse_generator(
    agent_input: ChatAgentInput,
) -> AsyncGenerator[str, None]:
    """ChatAgent SSE 生成器（Phase XV 核心）。

    职责：接收 ChatAgentInput → 构建完整人格 prompt → LLM 流式生成
          → SentenceSplitter → 反思链 → 净化 → COMMIT → 异步后处理。

    调用者：DispatchAgent（top_agent.py 改造后调用此函数）。
    """
    request_id = agent_input.request_id
    session_id = agent_input.session_id
    user_message = agent_input.user_message
    person_id = agent_input.person_id
    emotion_context = agent_input.emotion_context
    emotion_snapshot = agent_input.emotion_snapshot
    bond_summary = agent_input.bond_summary
    recall_slot_text = agent_input.recall_slot_text
    retrieval_evidence_items = agent_input.retrieval_evidence_items
    retrieval_summary = agent_input.retrieval_summary
    render_target_lang = agent_input.render_target_lang  # Phase XV: 固定 "zh"
    thread_id = agent_input.thread_id
    ts_ms = agent_input.ts_ms or int(time.time() * 1000)
    intent_draft = agent_input.intent_draft or {}
    sub_agent_ctx = agent_input.sub_agent_context
    deterministic_reply = _build_deterministic_sub_agent_reply(sub_agent_ctx, render_target_lang=render_target_lang)

    prompt_text = ""
    system_prompt = ""
    gen_params: Dict[str, Any] = {}

    # 确定性子结果渲染不依赖 LLM，也不需要构建完整 prompt。
    if not deterministic_reply:
        # ── 1. 构建 prompt（复用完整 top_agent_prompt 管线）──────────────
        from host_brain.app.top_agent_prompt import build_top_agent_prompt  # noqa: PLC0415

        # 如果有子 Agent 结果，注入到 user_message 作为额外上下文
        # Phase XV P1: 子 Agent 结果统一经 ChatAgent 人格渲染，不直接输出
        effective_user_message = user_message
        sub_agent_context_prompt = ""
        if sub_agent_ctx:
            sub_agent_context_prompt = _build_sub_agent_context_prompt(sub_agent_ctx)
            # 子 Agent 结果作为额外 context 注入到 intent_draft，让 prompt_builder 感知
            intent_draft = {
                **intent_draft,
                "sub_agent_context": sub_agent_context_prompt,
                "sub_agent_id": sub_agent_ctx.agent_id,
            }

        # 回复深度策略：Gateway 通道使用更短的回复
        reply_depth_hint = ""
        if agent_input.reply_depth_policy == "gateway":
            reply_depth_hint = "（回复简洁，12-60字，适合移动端消息）"

        try:
            prompt_text, system_prompt, gen_params = build_top_agent_prompt(
                user_message=effective_user_message + reply_depth_hint,
                render_target_lang=render_target_lang,
                emotion_context=emotion_context,
                emotion_snapshot=emotion_snapshot,
                retrieval_summary=retrieval_summary,
                retrieval_evidence_items=retrieval_evidence_items,
                person_id=person_id,
                session_id=session_id,
                request_id=request_id,
                thread_id=thread_id,
                bond_summary=bond_summary,
                recall_slot_text=recall_slot_text if recall_slot_text else None,
                extra_context_prompt=sub_agent_context_prompt,
            )
        except Exception as _pb_exc:
            _logger.warning("[%s] ChatAgent prompt_builder failed: %s", request_id, _pb_exc)
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "REJECT",
                "reason_code": "PROMPT_BUILD_FAILED", "request_id": request_id,
                "narrative": "……嗯，蓝这边有点小状况，等一下再来找我好不好？",
            })
            return

    # ── 2. 解析 LLM 运行时（ChatAgent 专用配置）─────────────────────
    if deterministic_reply:
        llm_runtime = {
            "available": True,
            "provider_name": "host",
            "model_id": "deterministic-plan-renderer",
            "timeout_ms": 0,
            "llm_api_key": "",
            "base_url_override": "",
        }
    else:
        llm_runtime = agent_input.llm_runtime
        if llm_runtime is None:
            try:
                from host_brain.app.graph_config_builder import (  # noqa: PLC0415
                    resolve_host_llm_runtime_async,
                    resolve_chat_agent_llm_runtime,
                )
                base_runtime = await resolve_host_llm_runtime_async(
                    rollout_key=session_id,
                    scene_type="CHAT",
                )
                llm_runtime = resolve_chat_agent_llm_runtime(base_runtime=base_runtime)
            except Exception as _rt_exc:
                _logger.warning("[%s] ChatAgent llm_runtime resolve failed: %s", request_id, _rt_exc)
                yield _sse({
                    "phase": "COMMIT", "done": True, "decision": "REJECT",
                    "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                    "narrative": "……抱歉，蓝现在脑子有点转不动，过一会儿再来找我吧。",
                })
                return

        if not llm_runtime.get("available"):
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "REJECT",
                "reason_code": "LLM_UNAVAILABLE", "request_id": request_id,
                "narrative": "……抱歉，蓝现在脑子有点转不动，过一会儿再来找我吧。",
            })
            return

    provider_name = str(llm_runtime["provider_name"])
    model_id = str(llm_runtime["model_id"])
    timeout_ms = int(llm_runtime["timeout_ms"])
    api_key = str(llm_runtime.get("llm_api_key") or "").strip()
    base_url = str(llm_runtime.get("base_url_override") or "").strip() or None

    # ChatAgent 专属 gen_params（高温度，自然流畅）
    agent_gen_params = dict(gen_params or {})
    agent_overrides = llm_runtime.get("agent_gen_params") or {}
    agent_gen_params.update({
        k: v for k, v in agent_overrides.items()
        if v is not None
    })

    # ── 3. 流式生成 + SentenceSplitter ──────────────────────────────
    from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415
    from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake  # noqa: PLC0415
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415

    # 诊断日志：确认 system_prompt 是否包含人设（前 150 字）
    _logger.info(
        "[%s] ChatAgent system_prompt preview: %s",
        request_id,
        (system_prompt or "")[:150].replace("\n", "↵"),
    )

    splitter = SentenceSplitter()
    full_text = ""
    rendered_text = ""
    first_token_ms: int | None = None
    sentence_index = 0
    llm_started_ms = int(time.time() * 1000)

    buffer_stream = _should_buffer_stream_output(
        emotion_context=emotion_context,
        bond_summary=bond_summary or None,
    )
    if deterministic_reply:
        buffer_stream = False
        provider_name = "host"
        model_id = "deterministic-plan-renderer"
        timeout_ms = 0
        api_key = ""
        base_url = None

    # Phase XV P7：分句 TTS 开关
    # suppress_edge_tts=True 时（Gateway 模式），本地 TTS 不触发
    _suppress_tts = bool(agent_input.suppress_edge_tts)
    _tts_sentence_stream_enabled = (
        not _suppress_tts
        and str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        not in ("0", "false", "no", "off")
    )
    # 分句 TTS 用的 SentenceSplitter（对 text_ja 分句）
    tts_splitter = SentenceSplitter() if _tts_sentence_stream_enabled else None  # type: ignore[assignment]
    tts_sentence_index = 0
    # 追踪已渲染的 text_ja（用于增量提取）
    _tts_rendered_text = ""

    try:
        if deterministic_reply:
            full_text = str(deterministic_reply).strip()
            rendered_text = full_text
            first_token_ms = 0
            if full_text:
                yield _sse({"phase": "RENDER", "chunk": full_text})
                for sentence in splitter.feed(full_text):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
        else:
            async for chunk in astream_llm_chunks(
                provider_name=provider_name,
                model_id=model_id,
                api_key=api_key,
                timeout_ms=timeout_ms,
                prompt_text=prompt_text,
                system_prompt=system_prompt,
                gen_params=agent_gen_params,
                base_url_override=base_url,
            ):
                if first_token_ms is None:
                    first_token_ms = int(time.time() * 1000) - llm_started_ms

                full_text += chunk
                visible = _extract_visible_text(full_text)
                if not visible:
                    continue

                if visible.startswith(rendered_text):
                    new_chunk = visible[len(rendered_text):]
                else:
                    new_chunk = visible
                if not new_chunk:
                    continue

                rendered_text = visible

                if buffer_stream:
                    continue

                yield _sse({"phase": "RENDER", "chunk": new_chunk})

                for sentence in splitter.feed(new_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

                if _tts_sentence_stream_enabled and tts_splitter is not None:
                    _tts_full = _extract_tts_text(full_text)
                    if _tts_full and _tts_full.startswith(_tts_rendered_text):
                        _tts_new = _tts_full[len(_tts_rendered_text):]
                        _tts_rendered_text = _tts_full
                        for _tts_sentence in tts_splitter.feed(_tts_new):
                            if _tts_sentence.strip():
                                _etag = _infer_emotion_tag_for_tts(
                                    _tts_sentence,
                                    str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                                )
                                asyncio.create_task(_post_speak_sentence(
                                    text=_tts_sentence,
                                    utterance_id=f"utt_{request_id}_{tts_sentence_index:03d}",
                                    emotion_tag=_etag,
                                    request_id=request_id,
                                    session_id=session_id,
                                ))
                                tts_sentence_index += 1

    except (TimeoutError, Exception) as _llm_exc:
        _logger.warning("[%s] ChatAgent LLM failed: %s", request_id, _llm_exc)
        if not full_text.strip():
            full_text = _build_chat_agent_fallback_reply(
                user_message=user_message,
                sub_agent_ctx=sub_agent_ctx,
                bond_summary=bond_summary,
                render_target_lang=render_target_lang,
            )
            if not buffer_stream:
                yield _sse({"phase": "RENDER", "chunk": full_text})
                for sentence in splitter.feed(full_text):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1

    # ── 4. 组装 narrative ────────────────────────────────────────────
    narrative = repair_utf8_latin1_mojibake(full_text) or full_text
    narrative = _extract_visible_text(narrative) or narrative

    # 提取 TTS 文本（Phase XV 双轨语言：text_ja 走语音通道）
    tts_text = _extract_tts_text(full_text)

    # ── 5. apply_response_intelligence（反思链 + 多模态后缀）─────────
    response_intelligence_meta: dict[str, Any] = {}
    try:
        from host_brain.app.intelligent_runtime import apply_response_intelligence  # noqa: PLC0415

        narrative, response_intelligence_meta = apply_response_intelligence(
            request_id=request_id,
            user_message=user_message,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary or None,
            render_lang=render_target_lang,
            allow_rewrite=buffer_stream,
        )

        if buffer_stream and narrative:
            rendered_text = narrative
            yield _sse({"phase": "RENDER", "chunk": narrative})
            for sentence in splitter.feed(narrative):
                yield _sse({
                    "phase": "SENTENCE",
                    "sentence": sentence,
                    "sentence_index": sentence_index,
                })
                sentence_index += 1
        elif narrative.startswith(rendered_text):
            postprocess_chunk = narrative[len(rendered_text):]
            if postprocess_chunk:
                rendered_text = narrative
                yield _sse({"phase": "RENDER", "chunk": postprocess_chunk})
                for sentence in splitter.feed(postprocess_chunk):
                    yield _sse({
                        "phase": "SENTENCE",
                        "sentence": sentence,
                        "sentence_index": sentence_index,
                    })
                    sentence_index += 1
    except Exception as _ri_exc:
        response_intelligence_meta = {
            "reflection": {"status": "DEGRADED", "reason_code": str(_ri_exc)[:100]},
        }
        _logger.debug("[%s] ChatAgent response_intelligence failed: %s", request_id, _ri_exc)

    # flush 剩余
    remainder = splitter.flush()
    if remainder:
        yield _sse({
            "phase": "SENTENCE",
            "sentence": remainder,
            "sentence_index": sentence_index,
        })
        sentence_index += 1

    # ── 6. 出口净化 ──────────────────────────────────────────────────
    narrative = _strip_internal_delegate_markers(narrative)
    narrative = _airlock_sanitize_output(narrative)

    # PII 出口检查
    _pii_egress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415
        if _check_channel("EGRESS"):
            _pii_eg = pii_scrub_text(narrative, context="egress")
            narrative = _pii_eg.scrubbed_text
            if _pii_eg.has_pii:
                _pii_egress_stats = {
                    "detections": _pii_eg.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_eg.detections}),
                }
    except Exception:
        pass

    # ── 7. INPUT_COMPLETED 同步写入 ──────────────────────────────────
    total_latency_ms = int(time.time() * 1000) - ts_ms
    llm_latency_ms = int(time.time() * 1000) - llm_started_ms

    try:
        from host_brain.app.dependencies import get_ledger as _get_pre_ledger  # noqa: PLC0415
        _get_pre_ledger().log_voice_event({
            "request_id": request_id,
            "session_id": session_id,
            "event_type": "INPUT_COMPLETED",
            "direction": "HOST_INTERNAL",
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "EMPTY_NARRATIVE",
            "stage": "chat_agent.pre_commit",
            "detail": (
                f"source={'GATEWAY' if agent_input.reply_channel else 'CHAT_API'} "
                f"text_len={len(user_message)} reply_len={len(narrative)}"
            ),
            "payload": {
                "source": agent_input.reply_channel or "CHAT_API",
                "person_id": person_id,
                "input_preview": _build_reply_preview(user_message, limit=160),
                "reply_preview": _build_reply_preview(narrative, limit=160),
                "render_target_lang": render_target_lang,
                "sub_agent_id": sub_agent_ctx.agent_id if sub_agent_ctx else None,
            },
            "ts_ms": int(time.time() * 1000),
        })
    except Exception as _ve_exc:
        _logger.debug("[%s] pre-commit INPUT_COMPLETED failed: %s", request_id, _ve_exc)

    # ── 8. COMMIT ────────────────────────────────────────────────────
    yield _sse({
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "narrative": narrative,
        "request_id": request_id,
        "audit_refs": {
            "request_id": request_id,
            "path": f"chat_agent:sub={sub_agent_ctx.agent_id}" if sub_agent_ctx else "chat_agent:direct",
            "latency_ms": total_latency_ms,
            "first_token_ms": first_token_ms,
            "llm_latency_ms": llm_latency_ms,
            "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
            "stream_buffered": buffer_stream,
            "model_id": model_id,
            "render_target_lang": render_target_lang,
            "pii_stats": {"egress": _pii_egress_stats} if _pii_egress_stats else {},
        },
    })

    # ── 9. 异步后处理（fire-and-forget）─────────────────────────────
    # 复用 top_agent._async_post_process（不动后处理逻辑）
    try:
        from host_brain.app.top_agent import _async_post_process  # noqa: PLC0415
        asyncio.create_task(
            _async_post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=user_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary=bond_summary,
                ts_ms=ts_ms,
                render_target_lang=render_target_lang,
                total_latency_ms=total_latency_ms,
                llm_latency_ms=llm_latency_ms,
                first_token_ms=first_token_ms,
                provider_name=provider_name,
                model_id=model_id,
                buffer_stream=buffer_stream,
                delegated_agents=(
                    [{"agent_id": sub_agent_ctx.agent_id,
                      "success": sub_agent_ctx.success,
                      "latency_ms": sub_agent_ctx.latency_ms}]
                    if sub_agent_ctx else []
                ),
                retrieval_summary=retrieval_summary,
                gov_decision={"decision": "APPROVE"},
                pii_ingress_stats={},
                pii_egress_stats=_pii_egress_stats,
                # Phase XV trace 增强：传入 LLM 输入摘要和召回证据
                llm_input_digest=(system_prompt or "")[:500] or None,
                retrieval_evidence_items=list(retrieval_evidence_items or []),
            )
        )
    except Exception as _pp_exc:
        _logger.debug("[%s] ChatAgent post_process task failed: %s", request_id, _pp_exc)
