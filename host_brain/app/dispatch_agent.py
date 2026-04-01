"""dispatch_agent.py — DispatchAgent 调度层（Phase XV P1+P2）。

调度器：决定「做什么」，自己不生成任何用户可见文字。
职责：
  1. 硬规则快速路由（<1ms，无 LLM）
  2. LLM 意图分类（仅在规则无法判断时，可选开关）
  3. 委托子 Agent（code/translate/intent/vision）
  4. 调用 SkillLoader 执行 Skill（P2 完整接入）
  5. 将结果包装成 ChatAgentInput 交给 ChatAgent

架构原则：
  - DispatchAgent 使用轻量模型（gemini-2.5-flash，temperature=0.1）
  - 输出固定格式 JSON：{"route": "chat"|"delegate"|"skill", ...}
  - 所有用户可见文字由 ChatAgent 生成，DispatchAgent 不 yield RENDER 事件
  - 子 Agent gateway 使用 resolve_subagent_llm_runtime（gemini-2.5-flash）
  - Skill 执行走 SkillLoader 正规接口（registry + TokenService + ledger）

关系：
  input_routes.py
       │  调用
       ▼
  dispatch_agent_sse_generator()   ← 本模块主入口
       │
       ├── [chat]     → ChatAgentInput(sub_agent_context=None)   → chat_agent
       ├── [delegate] → SubAgent.dispatch() → ChatAgentInput(sub_agent_context=result)  → chat_agent
       └── [skill]    → SkillLoader.execute() → ChatAgentInput(sub_agent_context=result) → chat_agent
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, Optional

_logger = logging.getLogger("Natsume.dispatch_agent")

# ---------------------------------------------------------------------------
# 硬规则路由：不需要 LLM，<1ms
# ---------------------------------------------------------------------------

# 明确的 Skill 触发词（优先级最高）
_SKILL_KEYWORD_MAP: dict[str, str] = {
    # 搜索/查询类
    "搜索": "web_search", "搜一下": "web_search", "查一下": "web_search",
    "查询": "web_search", "找一下": "web_search", "帮我搜": "web_search",
    "帮我查": "web_search", "search": "web_search",
    # 记忆类
    "记住": "memory_write", "记一下": "memory_write",
    "帮我记": "memory_write", "备忘": "memory_write",
    # 文件类
    "读取文件": "file_read", "打开文件": "file_read",
    "写文件": "file_write", "保存文件": "file_write",
    # 浏览器类
    "打开网页": "browser_navigate", "访问网站": "browser_navigate",
    "截图": "browser_screenshot", "帮我截图": "browser_screenshot",
}

# 明确的子 Agent 委托触发词
_DELEGATE_KEYWORD_MAP: dict[str, str] = {
    "写代码": "code_agent", "帮我写代码": "code_agent",
    "编程": "code_agent", "写个脚本": "code_agent",
    "调试": "code_agent", "debug": "code_agent",
    "实现": "code_agent", "写个函数": "code_agent",
    "翻译": "translate_agent", "帮我翻译": "translate_agent",
    "translate": "translate_agent",
    "识别图片": "vision_agent", "图片里": "vision_agent",
    "这张图": "vision_agent", "看看图": "vision_agent",
}

# 近期对话回查触发词（硬规则，不走 LLM）
_RECENT_TURN_USER_RE = re.compile(r"(我刚才|我上一句|我上句|我前一句|我前面).*?(说了什么|发了什么|是什么)")
_RECENT_TURN_ASSISTANT_RE = re.compile(r"(你刚才|你上一句|你上句|你前一句|你前面).*?(说了什么|回了什么|回复了什么|是什么)")
_RECENT_TURN_GENERIC_RE = re.compile(r"(上一句|上句|前一句|刚才说的什么|刚刚说的什么).*?(是什么|是什么来着|说了什么)?")


def _hard_rule_route(
    user_message: str,
) -> tuple[str, str, str]:
    """硬规则路由（无 LLM，<1ms）。

    Returns:
        (route_type, target_id, reason)
        route_type: "chat" | "delegate" | "skill" | "recent_turn"
        target_id: skill_id 或 agent_id（chat 路由时为空）
        reason: 路由原因（审计用）
    """
    msg = str(user_message or "").strip()
    if not msg:
        return "chat", "", "empty_message"

    # 近期对话回查（最高优先级硬规则）
    if _RECENT_TURN_USER_RE.search(msg) or _RECENT_TURN_ASSISTANT_RE.search(msg) or \
       _RECENT_TURN_GENERIC_RE.search(msg):
        return "recent_turn", "", "recent_turn_query"

    # Skill 触发词检测
    msg_lower = msg.lower()
    for keyword, skill_id in _SKILL_KEYWORD_MAP.items():
        if keyword in msg or keyword in msg_lower:
            return "skill", skill_id, f"keyword:{keyword}"

    # 子 Agent 委托触发词检测
    for keyword, agent_id in _DELEGATE_KEYWORD_MAP.items():
        if keyword in msg or keyword in msg_lower:
            return "delegate", agent_id, f"keyword:{keyword}"

    # 默认：闲聊路由
    return "chat", "", "default_chat"


# ---------------------------------------------------------------------------
# LLM 路由（仅在硬规则返回 "chat" 且消息复杂时使用）
# ---------------------------------------------------------------------------

_DISPATCH_SYSTEM_PROMPT = """你是一个意图分类器，输出固定格式 JSON，决定如何处理用户消息。

可选路由：
- "chat"：闲聊/情感/日常对话，直接由 ChatAgent 处理
- "delegate"：需要执行代码/翻译/图像分析等任务，委托给子 Agent
- "skill"：需要搜索/记忆/文件操作等，调用 Skill 执行

输出格式（严格 JSON，不要额外文字）：
{"route": "chat"|"delegate"|"skill", "target_id": "agent_id或skill_id或空", "task": "任务描述或空"}

子 Agent ID: code_agent, translate_agent, vision_agent, intent_agent
Skill ID: web_search, memory_write, memory_recall, file_read, file_write, browser_navigate

规则：
- 80% 的消息是闲聊，优先返回 {"route": "chat", "target_id": "", "task": ""}
- 只有明确的任务请求才委托，不要过度委托
- 不确定时返回 chat"""

_DISPATCH_PROMPT_TEMPLATE = """用户消息：{message}

输出 JSON："""


async def _llm_route_dispatch(
    *,
    user_message: str,
    llm_runtime: Dict[str, Any],
    request_id: str,
) -> tuple[str, str, str]:
    """LLM 意图分类（仅在硬规则无法判断时使用）。

    Returns:
        (route_type, target_id, task)
    """
    try:
        from host_brain.integrations.llm_helpers import astream_llm_chunks  # noqa: PLC0415

        provider_name = str(llm_runtime["provider_name"])
        model_id = str(llm_runtime["model_id"])
        api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        timeout_ms = int(llm_runtime.get("timeout_ms") or 5_000)
        base_url = str(llm_runtime.get("base_url_override") or "").strip() or None
        # DispatchAgent 专属：低温度，JSON 输出，非流式
        gen_params = {
            "temperature": 0.1,
            "top_p": 0.80,
            "max_tokens": 150,
        }

        full_text = ""
        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            prompt_text=_DISPATCH_PROMPT_TEMPLATE.format(message=user_message[:500]),
            system_prompt=_DISPATCH_SYSTEM_PROMPT,
            gen_params=gen_params,
            base_url_override=base_url,
        ):
            full_text += chunk

        # 解析 JSON
        text = full_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(
                line for line in lines[1:]
                if not line.strip().startswith("```")
            )
        parsed = json.loads(text.strip())
        route = str(parsed.get("route") or "chat").strip()
        target_id = str(parsed.get("target_id") or "").strip()
        task = str(parsed.get("task") or "").strip()

        if route not in ("chat", "delegate", "skill"):
            route = "chat"

        return route, target_id, task

    except Exception as exc:
        _logger.debug("[%s] DispatchAgent LLM route failed: %s, fallback to chat", request_id, exc)
        return "chat", "", ""


# ---------------------------------------------------------------------------
# DispatchAgent 主生成器
# ---------------------------------------------------------------------------

async def dispatch_agent_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Dict[str, Any],
    emotion_context: Dict[str, Any],
    emotion_snapshot: Dict[str, Any],
    governor_result: Dict[str, Any],
    intent_id: Optional[str],
    metrics: Any,
    # 预处理数据（由 top_agent 现有流程传入）
    person_id: str,
    sanitized_message: str,
    recall_slot_text: Dict[str, str],
    bond_summary: Dict[str, Any],
    retrieval_evidence_items: list,
    retrieval_summary: Dict[str, Any],
    render_target_lang: str,
    llm_runtime: Dict[str, Any],
    pii_ingress_stats: Dict[str, Any],
) -> AsyncGenerator[str, None]:
    """DispatchAgent SSE 生成器（Phase XV 核心调度层）。

    流程：
      1. 硬规则路由（<1ms）
      2. [可选] LLM 意图分类（仅规则不确定时）
      3. 路由到：chat / delegate(SubAgent) / skill(SkillLoader)
      4. 所有路径的结果统一经 ChatAgent 人格渲染后输出

    调用者：top_agent.py（原 top_agent_sse_generator 内部）
    """
    from host_brain.app.chat_agent import (  # noqa: PLC0415
        ChatAgentInput,
        SubAgentContext,
        chat_agent_sse_generator,
        _sse,
    )

    # ── 1. 硬规则路由 ─────────────────────────────────────────────────
    route, target_id, route_reason = _hard_rule_route(sanitized_message)

    _logger.info(
        "[%s] DispatchAgent hard_rule_route: route=%s target=%s reason=%s",
        request_id, route, target_id, route_reason,
    )

    # ── 2. 近期对话回查（最高优先级，绕过 LLM）──────────────────────
    if route == "recent_turn":
        # 直接复用 top_agent 的 _maybe_build_recent_turn_answer
        from host_brain.app.top_agent import (  # noqa: PLC0415
            _maybe_build_recent_turn_answer,
            _airlock_sanitize_output,
            _async_post_process,
            _build_reply_preview,
        )
        recent_answer = _maybe_build_recent_turn_answer(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            render_target_lang=render_target_lang,
        )
        if recent_answer:
            narrative = _airlock_sanitize_output(recent_answer)
            total_latency_ms = int(time.time() * 1000) - ts_ms
            yield _sse({
                "phase": "COMMIT", "done": True, "decision": "APPROVE",
                "narrative": narrative, "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "dispatch_agent:recent_turn",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            })
            asyncio.create_task(_async_post_process(
                request_id=request_id, person_id=person_id, session_id=session_id,
                user_message=sanitized_message, narrative=narrative,
                emotion_context=emotion_context, bond_summary={}, ts_ms=ts_ms,
                render_target_lang=render_target_lang, total_latency_ms=total_latency_ms,
                llm_latency_ms=0, first_token_ms=None, provider_name="", model_id="",
                buffer_stream=False, delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "RECENT_TURN_DIRECT"},
                gov_decision=governor_result, pii_ingress_stats=pii_ingress_stats,
                pii_egress_stats={},
            ))
            return

    # ── 3. LLM 路由（规则路由为 chat 但消息复杂时启用）──────────────
    # 当前策略：仅在硬规则为 "chat" 且消息较长（可能是隐式任务请求）时进行二次分类
    # 这样 80% 的短消息闲聊直接跳过 LLM 分类，保持 <1ms 延迟
    use_llm_route = (
        route == "chat"
        and len(sanitized_message) > 20
        and _parse_bool_env("HOST_DISPATCH_LLM_ROUTE_ENABLED", default=False)
    )
    task_description = sanitized_message  # 默认任务描述
    if use_llm_route:
        yield _sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})
        try:
            from host_brain.app.graph_config_builder import resolve_dispatch_agent_llm_runtime  # noqa: PLC0415
            dispatch_runtime = resolve_dispatch_agent_llm_runtime(base_runtime=llm_runtime)
            route, target_id, task_description = await _llm_route_dispatch(
                user_message=sanitized_message,
                llm_runtime=dispatch_runtime,
                request_id=request_id,
            )
            if not task_description:
                task_description = sanitized_message
            _logger.info(
                "[%s] DispatchAgent LLM_route: route=%s target=%s",
                request_id, route, target_id,
            )
        except Exception as _dr_exc:
            _logger.debug("[%s] DispatchAgent LLM route error: %s", request_id, _dr_exc)
            route, target_id, task_description = "chat", "", sanitized_message

    # ── 4. 构建 ChatAgentInput 基础部分（所有路径共用）──────────────
    chat_input = ChatAgentInput(
        request_id=request_id,
        session_id=session_id,
        user_message=sanitized_message,
        person_id=person_id,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        recall_slot_text=recall_slot_text,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        reply_channel=str(getattr(request_body, "reply_channel", None) or ""),
        suppress_edge_tts=getattr(request_body, "suppress_edge_tts", None),
        suppress_edge_vts=getattr(request_body, "suppress_edge_vts", None),
        reply_depth_policy=(
            "gateway" if str(getattr(request_body, "reply_channel", "") or "").upper() == "GATEWAY"
            else "normal"
        ),
        thread_id=getattr(request_body, "thread_id", None),
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        llm_runtime=llm_runtime,
    )

    # ── 5. 路由分发 ───────────────────────────────────────────────────

    if route == "chat":
        # 闲聊路径：直接交给 ChatAgent
        _logger.info("[%s] DispatchAgent → chat (reason=%s)", request_id, route_reason)
        async for event in chat_agent_sse_generator(chat_input):
            yield event
        return

    elif route == "delegate":
        # 子 Agent 委托路径
        _logger.info("[%s] DispatchAgent → delegate: %s (reason=%s)", request_id, target_id, route_reason)
        yield _sse({"phase": "PRESENCE", "event": "DELEGATING", "agent": target_id, "request_id": request_id})

        sub_ctx = await _dispatch_to_sub_agent(
            agent_id=target_id,
            task=task_description,
            user_message=sanitized_message,
            request_id=request_id,
            session_id=session_id,
            llm_runtime=llm_runtime,
            # Phase XV P5：注入情感/Bond/语言上下文
            emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL").upper(),
            bond_stage=str((bond_summary or {}).get("stage") or "STRANGER").upper(),
            render_target_lang=render_target_lang,
        )

        # 子 Agent 结果注入 ChatAgent 渲染（P1 核心改造：不再用 _render_delegated_result_naturally）
        chat_input.sub_agent_context = sub_ctx
        async for event in chat_agent_sse_generator(chat_input):
            yield event
        return

    elif route == "skill":
        # Skill 执行路径
        _logger.info("[%s] DispatchAgent → skill: %s (reason=%s)", request_id, target_id, route_reason)
        yield _sse({"phase": "PRESENCE", "event": "SKILL_RUNNING", "skill_id": target_id, "request_id": request_id})

        skill_ctx = await _execute_skill(
            skill_id=target_id,
            user_message=sanitized_message,
            request_id=request_id,
            session_id=session_id,
        )

        # Skill 结果注入 ChatAgent 渲染
        chat_input.sub_agent_context = skill_ctx
        async for event in chat_agent_sse_generator(chat_input):
            yield event
        return

    else:
        # 未知路由，降级到闲聊
        _logger.warning("[%s] DispatchAgent unknown route=%s, fallback to chat", request_id, route)
        async for event in chat_agent_sse_generator(chat_input):
            yield event


# ---------------------------------------------------------------------------
# 子 Agent 委托
# ---------------------------------------------------------------------------

async def _dispatch_to_sub_agent(
    *,
    agent_id: str,
    task: str,
    user_message: str,
    request_id: str,
    session_id: str,
    llm_runtime: Dict[str, Any],
    # Phase XV P5：任务级上下文（可选）
    emotion_tag: str = "NEUTRAL",
    bond_stage: str = "STRANGER",
    render_target_lang: str = "zh",
    recent_turns: Optional[list] = None,
    previous_agent_result: Optional[str] = None,
) -> "SubAgentContext":
    """委托子 Agent 执行任务，返回 SubAgentContext。

    Phase XV P1+P5 改造：
    - 子 Agent gateway 使用 resolve_subagent_llm_runtime（gemini-2.5-flash）
    - context 注入任务级上下文（recent_turns / emotion_tag / bond_stage / previous_agent_result）
    - 子 Agent 结果不再直接输出，统一经 ChatAgent 人格渲染
    """
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415
    from host_brain.app.sub_agent_protocol import (  # noqa: PLC0415
        SubAgentRequest, get_sub_agent_dispatcher, ALL_AGENT_IDS,
    )
    from host_brain.app.top_agent import _build_subagent_gateway_from_runtime  # noqa: PLC0415

    # 验证 agent_id 合法性
    if agent_id not in ALL_AGENT_IDS:
        _logger.warning("[%s] Unknown agent_id=%s, skipping delegate", request_id, agent_id)
        return SubAgentContext(
            agent_id=agent_id, task=task, success=False,
            error=f"未知子 Agent: {agent_id}",
        )

    started_ms = int(time.time() * 1000)
    try:
        from host_brain.app.graph_config_builder import resolve_subagent_llm_runtime  # noqa: PLC0415
        subagent_runtime = resolve_subagent_llm_runtime(base_runtime=llm_runtime)
    except Exception:
        subagent_runtime = llm_runtime

    gateway = _build_subagent_gateway_from_runtime(subagent_runtime)

    # Phase XV P5：构建任务级上下文，让子 Agent 知道历史和情感
    task_context: Dict[str, Any] = {
        "emotion_tag": emotion_tag,
        "bond_stage": bond_stage,
        "render_target_lang": render_target_lang,
    }
    if gateway is not None:
        task_context["gateway"] = gateway
    if recent_turns:
        # 最多注入最近 3 轮，控制 token 消耗
        task_context["recent_turns"] = list(recent_turns)[-3:]
    if previous_agent_result:
        task_context["previous_agent_result"] = str(previous_agent_result)[:1000]

    try:
        dispatcher = get_sub_agent_dispatcher()
        sub_resp = await dispatcher.dispatch(SubAgentRequest(
            agent_id=agent_id,
            task=task or user_message,
            user_message=user_message,
            request_id=request_id,
            session_id=session_id,
            context=task_context,
        ))
        latency_ms = int(time.time() * 1000) - started_ms
        return SubAgentContext(
            agent_id=sub_resp.agent_id,
            task=task,
            result_text=str(sub_resp.result or ""),
            success=bool(sub_resp.success),
            latency_ms=latency_ms,
            error="" if sub_resp.success else str(getattr(sub_resp, "error", "") or ""),
        )
    except Exception as exc:
        latency_ms = int(time.time() * 1000) - started_ms
        _logger.warning("[%s] SubAgent dispatch failed: %s", request_id, exc)
        return SubAgentContext(
            agent_id=agent_id, task=task, success=False,
            latency_ms=latency_ms, error=str(exc)[:200],
        )


# ---------------------------------------------------------------------------
# Skill 执行
# ---------------------------------------------------------------------------

async def _execute_skill(
    *,
    skill_id: str,
    user_message: str,
    request_id: str,
    session_id: str,
) -> "SubAgentContext":
    """执行 Skill，返回 SubAgentContext。

    使用 SkillLoader 正规接口：
      1. 从 DI 容器获取 registry / ledger
      2. 实例化 TokenService，签发 approval_token
      3. 构造 intent dict + governor_output dict
      4. 调用 SkillLoader.execute()（同步，用 asyncio.to_thread 包装）
      5. 从 SkillExecutionResult.detail 中提取执行结果文本
    """
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415

    started_ms = int(time.time() * 1000)

    def _run_sync() -> tuple[str, bool, str]:
        """在线程池中执行同步 Skill 调用，返回 (result_text, success, error)。"""
        from host_brain.app.dependencies import get_skill_registry, get_ledger  # noqa: PLC0415
        from host_brain.skills.loader import SkillLoader  # noqa: PLC0415
        from host_brain.governor.tokens import TokenService  # noqa: PLC0415
        from host_brain.core.ids import new_event_id  # noqa: PLC0415

        registry = get_skill_registry()
        ledger = get_ledger()

        # 验证 skill 存在
        meta = registry.get(skill_id)
        if meta is None:
            return "", False, f"Skill '{skill_id}' not found in registry"

        # 实例化 TokenService 并签发 approval_token
        # 每次 Skill 执行用独立的 TokenService 实例（与 ToolBroker 的实例分离）
        token_service = TokenService()
        intent_id = new_event_id()
        seq = 1
        approval_token_ref = token_service.issue_approval_token(
            intent_id=intent_id,
            skill_name=skill_id,
            session_id=session_id,
            seq=seq,
        )

        # 构造 intent dict
        intent = {
            "id": intent_id,
            "action": skill_id,
            "args": {
                "query": user_message,
                "user_message": user_message,
            },
            "session_id": session_id,
            "seq": seq,
            "request_id": request_id,
        }

        # 构造 governor_output（CACHE 级别审批，低风险 Skill 直接放行）
        governor_output = {
            "decision": "APPROVE",
            "tokens": {
                "approval_token_ref": approval_token_ref,
            },
            "committed_route_token": {
                "approval_tier": "cache",  # 低风险 Skill 用 LIGHT 审计
            },
        }

        # 执行 Skill
        loader = SkillLoader(
            registry=registry,
            token_service=token_service,
            ledger=ledger,
        )
        result = loader.execute(
            intent=intent,
            governor_output=governor_output,
            context={
                "session_id": session_id,
                "request_id": request_id,
                "emotion_tag": "NEUTRAL",
                "mood_state": "steady",
            },
        )

        # 提取结果文本
        if result.status == "SUCCESS":
            # detail 是 audit row dict，handler 执行结果在 result_digest 字段
            detail = result.detail or {}
            result_digest = str(detail.get("result_digest") or "")
            # 尝试解析 result_digest（可能是 dict repr）
            result_text = result_digest[:2000] if result_digest else f"Skill {skill_id} 执行成功"
            return result_text, True, ""
        elif result.status == "IDEMPOTENT_REPLAY":
            return f"（已执行过相同的 {skill_id} 操作）", True, ""
        else:
            return "", False, f"{result.result_code}: {result.status}"

    try:
        result_text, success, error = await asyncio.to_thread(_run_sync)
        latency_ms = int(time.time() * 1000) - started_ms
        return SubAgentContext(
            agent_id=f"skill:{skill_id}",
            task=user_message,
            result_text=result_text,
            success=success,
            latency_ms=latency_ms,
            error=error,
        )
    except Exception as exc:
        latency_ms = int(time.time() * 1000) - started_ms
        _logger.warning("[%s] Skill '%s' execute error: %s", request_id, skill_id, exc)
        return SubAgentContext(
            agent_id=f"skill:{skill_id}",
            task=user_message,
            result_text="",
            success=False,
            latency_ms=latency_ms,
            error=str(exc)[:200],
        )


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _parse_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    return raw in ("1", "true", "yes", "on") if raw else default
