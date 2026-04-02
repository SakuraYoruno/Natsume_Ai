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

import ast
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
    "记住": "memory_store", "记一下": "memory_store",
    "帮我记": "memory_store", "备忘": "memory_store",
    # 文件类
    "读取文件": "file_read", "打开文件": "file_read",
    "写文件": "file_write", "保存文件": "file_write",
    "列出目录": "file_list", "查看目录": "file_list", "文件列表": "file_list",
    # 系统类
    "系统信息": "system_info", "本机信息": "system_info",
    "进程列表": "process_list", "看看进程": "process_list",
    "网络状态": "network_status", "网络连通": "network_status",
    # Hub 技能类
    "安装技能": "hub_skill_install", "安装skill": "hub_skill_install", "安装 hub 技能": "hub_skill_install",
    "搜索技能": "hub_skill_search", "查找技能": "hub_skill_search", "搜技能": "hub_skill_search",
    "已安装技能": "hub_skill_list", "列出技能": "hub_skill_list", "技能列表": "hub_skill_list",
    "卸载技能": "hub_skill_remove", "删除技能": "hub_skill_remove",
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


_TASK_PROGRESS_QUERY_RE = re.compile(r"(进度|做到哪|到哪了|还在.*(执行|跑|做)|上个任务.*(怎么样|进度)|刚才.*任务.*(怎么样|进度)|修好了吗|处理到哪)" )


def _hard_rule_route(
    user_message: str,
) -> tuple[str, str, str]:
    """硬规则路由（无 LLM，<1ms）。

    Returns:
        (route_type, target_id, reason)
        route_type: "chat" | "delegate" | "skill" | "recent_turn" | "task_progress"
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

    if _TASK_PROGRESS_QUERY_RE.search(msg):
        return "task_progress", "", "task_progress_query"

    # Skill 触发词检测
    msg_lower = msg.lower()
    for keyword, skill_id in sorted(
        _SKILL_KEYWORD_MAP.items(),
        key=lambda item: (-len(str(item[0])), str(item[0])),
    ):
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

def _extract_result_code(error_text: str | None) -> str | None:
    text = str(error_text or "").strip()
    if not text:
        return None
    match = re.match(r"([A-Z0-9_]+)\s*:", text)
    if match:
        return match.group(1).strip()
    if text.isupper() and " " not in text:
        return text
    return None


def _format_elapsed_ms(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms)) / 1000.0
    if seconds < 1.0:
        return f"{int(max(0, elapsed_ms))}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    remain = int(seconds % 60)
    return f"{minutes}m{remain:02d}s"


def _build_task_progress_sub_context(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
) -> "SubAgentContext":
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    registry = get_runtime_task_registry()
    summary = registry.summarize_for_chat(
        person_id=person_id,
        session_id=session_id,
        exclude_request_id=request_id,
    )
    if not summary:
        return SubAgentContext(
            agent_id="task_progress_direct",
            task="task_progress_query",
            result_text="现在没有正在执行，或者刚结束但还值得汇报的任务。",
            success=True,
        )

    status = str(summary.get("status") or "UNKNOWN").strip().upper()
    target_id = str(summary.get("target_id") or "任务").strip() or "任务"
    elapsed_text = _format_elapsed_ms(int(summary.get("elapsed_ms") or 0))
    progress_message = str(summary.get("progress_message") or summary.get("detail") or "").strip()
    result_code = str(summary.get("result_code") or "").strip()
    recovery_status = str(summary.get("recovery_status") or "").strip()
    recovery_result_text = str(summary.get("recovery_result_text") or "").strip()
    recovery_error = str(summary.get("recovery_error") or "").strip()

    if status in {"RUNNING", "RECOVERING"}:
        parts = [f"当前有个任务还在进行，是 {target_id}。", f"已经跑了 {elapsed_text}。"]
        if progress_message:
            parts.append(progress_message)
        if recovery_status == "RUNNING":
            parts.append("我也已经开始自动补救了。")
        elif recovery_result_text:
            parts.append(f"补救分析里提到：{recovery_result_text}")
        return SubAgentContext(agent_id="task_progress_direct", task="task_progress_query", result_text="".join(parts), success=True)

    parts = [f"最近一个相关任务是 {target_id}。", f"状态是 {status}。"]
    if progress_message:
        parts.append(progress_message)
    if result_code:
        parts.append(f"结果码是 {result_code}。")
    if recovery_result_text:
        parts.append(f"自动补救给出的建议是：{recovery_result_text}")
    elif recovery_error:
        parts.append(f"补救过程也没有完全顺利：{recovery_error}")
    return SubAgentContext(agent_id="task_progress_direct", task="task_progress_query", result_text="".join(parts), success=True)


_PARALLEL_SAFE_SKILL_IDS = {
    "web_search",
    "system_info",
    "process_list",
    "network_status",
    "memory_recall",
    "read_only_status",
    "session_summary",
}


def _route_target_allows_parallel(route: str, target_id: str) -> bool:
    normalized_route = str(route or "").strip().lower()
    normalized_target = str(target_id or "").strip()
    if normalized_route == "delegate":
        try:
            from host_brain.app.agents.agent_registry import get_agent_meta  # noqa: PLC0415

            meta = get_agent_meta(normalized_target) or {}
            return bool(meta.get("allow_parallel", False)) and not bool(meta.get("has_side_effects", False))
        except Exception:
            return False
    if normalized_route == "skill":
        if normalized_target in _PARALLEL_SAFE_SKILL_IDS:
            return True
        try:
            from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415

            meta = get_skill_registry().get(normalized_target)
            if meta is None:
                return False
            return int(getattr(meta, "risk_level", 99) or 99) <= 1 and not bool(getattr(meta, "require_confirm", False))
        except Exception:
            return False
    return False


async def _maybe_build_multi_step_plan(
    *,
    user_message: str,
    request_id: str,
    llm_runtime: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from host_brain.app.dependencies import get_planning_agent, get_skill_registry  # noqa: PLC0415

    planning_agent = get_planning_agent()
    normalized = str(user_message or "").strip()
    if not normalized:
        return None

    try:
        raw_sub_tasks = planning_agent._decompose_request(normalized)  # type: ignore[attr-defined]
    except Exception:
        raw_sub_tasks = []

    if not planning_agent.needs_planning(normalized) and (not isinstance(raw_sub_tasks, list) or len(raw_sub_tasks) < 2):
        return None

    try:
        plan_outline = planning_agent.create_plan(
            task_id=f"plan::{request_id}",
            request=normalized,
            available_skills=list(get_skill_registry().list_skill_names()),
        )
    except Exception:
        return None

    if not isinstance(raw_sub_tasks, list) or len(raw_sub_tasks) < 2:
        return None

    resolved_steps: list[dict[str, Any]] = []
    dispatch_runtime = None
    for index, sub_task in enumerate(raw_sub_tasks, start=1):
        description = str((sub_task or {}).get("description") or "").strip()
        if not description:
            continue
        route, target_id, route_reason = _hard_rule_route(description)
        task_description = description
        if route == "chat":
            try:
                from host_brain.app.graph_config_builder import resolve_dispatch_agent_llm_runtime  # noqa: PLC0415

                dispatch_runtime = dispatch_runtime or resolve_dispatch_agent_llm_runtime(base_runtime=llm_runtime)
                route, target_id, task_description = await _llm_route_dispatch(
                    user_message=description,
                    llm_runtime=dispatch_runtime,
                    request_id=f"{request_id}::plan::{index}",
                )
                route_reason = f"llm_plan:{route}"
            except Exception:
                route, target_id, task_description = "chat", "", description
        if route not in {"skill", "delegate"} or not str(target_id or "").strip():
            continue
        resolved_steps.append({
            "step_index": index,
            "description": description,
            "route": route,
            "target_id": str(target_id).strip(),
            "task_description": str(task_description or description).strip() or description,
            "route_reason": route_reason,
            "allow_parallel": _route_target_allows_parallel(route, str(target_id).strip()),
        })

    if len(resolved_steps) < 2:
        return None

    return {
        "plan_id": f"plan::{request_id}",
        "outline": plan_outline.as_dict(),
        "steps": resolved_steps,
        "parallel_enabled": len(resolved_steps) > 1 and all(bool(step.get("allow_parallel")) for step in resolved_steps),
    }


async def _execute_multi_step_plan(
    *,
    plan: dict[str, Any],
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    llm_runtime: dict[str, Any] | None,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any],
    render_target_lang: str,
):
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    registry, parent_task = _get_or_create_runtime_task(
        request_id=request_id,
        task_kind="plan",
        target_id="plan_execution",
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        detail="正在生成并执行多步骤计划。",
        progress_message="我正在拆解这件事，准备分步处理。",
        meta={"plan": dict(plan or {})},
    )

    steps = list(plan.get("steps") or [])
    if not steps:
        return None

    async def _run_plan_step(step: dict[str, Any]) -> dict[str, Any]:
        step_index = int(step.get("step_index") or 0)
        route = str(step.get("route") or "").strip()
        target_id = str(step.get("target_id") or "").strip()
        description = str(step.get("description") or "").strip()
        task_description = str(step.get("task_description") or description).strip() or description
        child_task = registry.create_task(
            task_kind=f"plan_step:{route}",
            target_id=target_id,
            request_id=f"{request_id}::step::{step_index}",
            session_id=session_id,
            person_id=person_id,
            user_message=task_description,
            detail=f"计划第{step_index}步：{description}",
            progress_message=f"计划第{step_index}步正在处理：{target_id}。",
            meta=dict(step),
            parent_task_id=parent_task.task_id,
        )
        if route == "skill":
            ctx = await _execute_skill(
                skill_id=target_id,
                user_message=task_description,
                request_id=request_id,
                session_id=session_id,
            )
        else:
            ctx = await _dispatch_to_sub_agent(
                agent_id=target_id,
                task=task_description,
                user_message=task_description,
                request_id=request_id,
                session_id=session_id,
                llm_runtime=llm_runtime,
                emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL").upper(),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER").upper(),
                render_target_lang=render_target_lang,
            )

        if ctx.success:
            registry.mark_completed(
                child_task.task_id,
                detail=f"计划第{step_index}步已完成。",
                result_text=ctx.result_text,
            )
            return {"step": step, "context": ctx, "child_task_id": child_task.task_id}

        failure_code = _extract_result_code(ctx.error)
        registry.mark_failed(
            child_task.task_id,
            detail=f"计划第{step_index}步失败。",
            error=ctx.error,
            result_code=failure_code,
            result_text=ctx.result_text,
        )
        recovery_plan = _build_failure_recovery_plan(
            target_id=target_id,
            user_message=task_description,
            error_text=ctx.error,
        )
        if recovery_plan:
            registry.mark_recovering(
                child_task.task_id,
                detail=str(recovery_plan.get("summary") or "正在自动补救。"),
                recovery_plan=recovery_plan,
            )
            recovery_ctx = await _dispatch_to_sub_agent(
                agent_id=str(recovery_plan.get("agent_id") or "intent_agent"),
                task=str(recovery_plan.get("task") or task_description),
                user_message=task_description,
                request_id=request_id,
                session_id=session_id,
                llm_runtime=llm_runtime,
                emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL").upper(),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER").upper(),
                render_target_lang=render_target_lang,
                previous_agent_result=ctx.error or ctx.result_text,
            )
            registry.attach_recovery_result(
                child_task.task_id,
                recovery_status="DONE" if recovery_ctx.success else "FAILED",
                recovery_result_text=recovery_ctx.result_text,
                recovery_error=recovery_ctx.error,
            )
            ctx = SubAgentContext(
                agent_id="plan_step_recovery",
                task=task_description,
                result_text=recovery_ctx.result_text,
                success=False,
                latency_ms=ctx.latency_ms + recovery_ctx.latency_ms,
                error=ctx.error,
            )
        return {"step": step, "context": ctx, "child_task_id": child_task.task_id}

    started_ms = int(time.time() * 1000)
    results: list[dict[str, Any]] = []
    if bool(plan.get("parallel_enabled")):
        registry.update_task(
            parent_task.task_id,
            status="RUNNING",
            detail=f"计划包含 {len(steps)} 个可并行子任务，正在并行处理。",
            progress_message=f"我正在并行处理 {len(steps)} 个子任务。",
        )
        results = list(await asyncio.gather(*(_run_plan_step(step) for step in steps)))
    else:
        for index, step in enumerate(steps, start=1):
            registry.update_task(
                parent_task.task_id,
                status="RUNNING",
                detail=f"计划第 {index}/{len(steps)} 步正在执行：{step.get('target_id') or step.get('description')}",
                progress_message=f"我正在处理计划第 {index}/{len(steps)} 步。",
            )
            results.append(await _run_plan_step(step))

    elapsed_ms = int(time.time() * 1000) - started_ms
    success_count = 0
    summary_lines: list[str] = []
    failure_messages: list[str] = []
    structured_steps: list[dict[str, Any]] = []
    for item in results:
        step = dict(item.get("step") or {})
        ctx = item.get("context")
        step_index = int(step.get("step_index") or 0)
        target_id = str(step.get("target_id") or "步骤").strip()
        result_text = str(getattr(ctx, "result_text", "") or "").strip()
        error_text = str(getattr(ctx, "error", "") or "").strip()
        child_task_id = str(item.get("child_task_id") or "").strip() or None
        recovery_result_text = ""
        recovery_error = ""
        if child_task_id:
            child_snapshot = registry.get(child_task_id)
            if child_snapshot is not None:
                recovery_result_text = str(child_snapshot.recovery_result_text or "").strip()
                recovery_error = str(child_snapshot.recovery_error or "").strip()
        step_success = bool(getattr(ctx, "success", False))
        structured_steps.append({
            "step_index": step_index,
            "target_id": target_id,
            "route": str(step.get("route") or "").strip(),
            "description": str(step.get("description") or "").strip(),
            "task_description": str(step.get("task_description") or "").strip(),
            "success": step_success,
            "result_text": result_text,
            "error": error_text,
            "recovery_result_text": recovery_result_text,
            "recovery_error": recovery_error,
            "child_task_id": child_task_id,
        })
        if step_success:
            success_count += 1
            summary_lines.append(f"第{step_index}步（{target_id}）已完成：{result_text[:180]}")
        else:
            failure_line = f"第{step_index}步（{target_id}）失败：{error_text or 'FAILED'}"
            if result_text:
                failure_line += f"；补救建议：{result_text[:180]}"
            summary_lines.append(failure_line)
            failure_messages.append(error_text or "FAILED")

    plan_payload = {
        "parallel_enabled": bool(plan.get("parallel_enabled")),
        "total_steps": len(results),
        "success_count": success_count,
        "steps": structured_steps,
    }

    parent_success = success_count == len(results)
    if parent_success:
        registry.update_task(
            parent_task.task_id,
            status="RUNNING",
            detail="多步骤计划已经执行完，正在整理最终回复。",
            progress_message="多步骤计划已经执行完，我正在整理最终回复。",
            result_text="\n".join(summary_lines),
        )
        return SubAgentContext(
            agent_id="plan_execution",
            task=user_message,
            result_text="\n".join(summary_lines),
            success=True,
            latency_ms=elapsed_ms,
            error="",
            payload=plan_payload,
        )

    registry.update_task(
        parent_task.task_id,
        status="RUNNING",
        detail="多步骤计划已经执行完，但有步骤失败，正在整理失败说明。",
        progress_message="多步骤计划里有步骤失败，我正在整理说明和建议。",
        error=" | ".join(failure_messages)[:500],
        result_code="PLAN_PARTIAL_FAILURE",
        result_text="\n".join(summary_lines),
    )
    return SubAgentContext(
        agent_id="plan_execution",
        task=user_message,
        result_text="\n".join(summary_lines),
        success=False,
        latency_ms=elapsed_ms,
        error="PLAN_PARTIAL_FAILURE",
        payload=plan_payload,
    )



def _get_or_create_runtime_task(
    *,
    request_id: str,
    task_kind: str,
    target_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    detail: str,
    progress_message: str,
    meta: dict[str, Any] | None = None,
):
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    registry = get_runtime_task_registry()
    existing = registry.get_by_request_id(request_id)
    if existing is not None:
        registry.update_task(
            existing.task_id,
            task_kind=task_kind,
            target_id=target_id,
            session_id=session_id,
            person_id=person_id,
            user_message=user_message,
            status="RUNNING",
            detail=detail,
            progress_message=progress_message,
            error=None,
            result_code=None,
            result_text="",
            recovery_plan=None,
            recovery_status=None,
            recovery_error=None,
            recovery_result_text="",
            meta=dict(meta or {}),
        )
        refreshed = registry.get(existing.task_id)
        return registry, (refreshed or existing)
    created = registry.create_task(
        task_kind=task_kind,
        target_id=target_id,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        detail=detail,
        progress_message=progress_message,
        meta=dict(meta or {}),
    )
    return registry, created


def _build_failure_recovery_plan(
    *,
    target_id: str,
    user_message: str,
    error_text: str,
) -> dict[str, Any] | None:
    normalized_target = str(target_id or "").strip()
    normalized_error = str(error_text or "").strip()
    result_code = _extract_result_code(normalized_error) or normalized_error.split(":", 1)[0].strip().upper() or "FAILED"
    if result_code in {"FILE_NOT_FOUND", "FILE_PATH_MISSING", "FILE_EXEC_ERROR"}:
        return {
            "strategy": "delegate",
            "agent_id": "intent_agent",
            "summary": "原技能失败，正在让执行专家分析文件问题并推断可行修复方案。",
            "task": f"用户原请求：{user_message}。刚才执行 {normalized_target} 失败，错误是 {normalized_error}。请分析最可能的原因，并给出简短修复建议；如果能从原句推断更合理的文件路径，也请直接指出。",
            "reason_code": result_code,
        }
    if result_code in {"BROWSER_UNAVAILABLE", "BROWSER_TIMEOUT", "BROWSER_BACKEND_UNSUPPORTED"}:
        return {
            "strategy": "delegate",
            "agent_id": "repair_agent",
            "summary": "原技能失败，正在尝试自动补齐浏览器后端并恢复执行能力。",
            "task": f"用户原请求：{user_message}。刚才执行 {normalized_target} 失败，错误是 {normalized_error}。请自动安装或补齐浏览器执行后端，然后返回是否可以重试。",
            "reason_code": result_code,
            "retry_original": True,
        }
    if result_code in {"SKILL_EXECUTION_ERROR", "FAILED", "BOND_UNAVAILABLE", "NETWORK_UNAVAILABLE"}:
        return {
            "strategy": "delegate",
            "agent_id": "intent_agent",
            "summary": "原技能失败，正在让执行专家补做故障分析。",
            "task": f"用户原请求：{user_message}。刚才执行 {normalized_target} 失败，错误是 {normalized_error}。请分析失败原因，并给出最合适的下一步建议。",
            "reason_code": result_code,
        }
    return None


async def _retry_after_repair(
    *,
    route_kind: str,
    target_id: str,
    task_description: str,
    user_message: str,
    request_id: str,
    session_id: str,
    llm_runtime: dict[str, Any] | None,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any],
    render_target_lang: str,
) -> "SubAgentContext":
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415

    if route_kind == "skill":
        return await _execute_skill(
            skill_id=target_id,
            user_message=user_message,
            request_id=request_id,
            session_id=session_id,
        )
    if route_kind == "delegate":
        return await _dispatch_to_sub_agent(
            agent_id=target_id,
            task=task_description,
            user_message=user_message,
            request_id=request_id,
            session_id=session_id,
            llm_runtime=llm_runtime or {},
            emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL").upper(),
            bond_stage=str((bond_summary or {}).get("stage") or "STRANGER").upper(),
            render_target_lang=render_target_lang,
        )
    return SubAgentContext(
        agent_id=target_id,
        task=task_description,
        result_text="",
        success=False,
        latency_ms=0,
        error="UNSUPPORTED_RETRY_ROUTE",
    )


def _build_dispatch_system_prompt() -> str:
    skill_lines: list[str] = []
    try:
        from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
        registry = get_skill_registry()
        for skill_name in registry.list_skill_names()[:80]:
            meta = registry.get(skill_name)
            description = str(getattr(meta, "description", "") or "").strip()
            if description:
                skill_lines.append(f"- {skill_name}: {description}")
            else:
                skill_lines.append(f"- {skill_name}")
    except Exception:
        skill_lines = [
            "- web_search: 网络搜索",
            "- memory_store: 记忆写入",
            "- file_read: 文件读取",
            "- file_write: 文件写入",
            "- browser_navigate: 浏览器打开网页",
        ]

    skill_manifest = "\n".join(skill_lines)
    return f"""你是一个意图分类器，输出固定格式 JSON，决定如何处理用户消息。

可选路由：
- "chat"：闲聊/情感/日常对话，直接由 ChatAgent 处理
- "delegate"：需要执行代码/翻译/图像分析等任务，委托给子 Agent
- "skill"：需要搜索/记忆/文件操作/系统操作/浏览器操作等，调用 Skill 执行

输出格式（严格 JSON，不要额外文字）：
{{"route": "chat"|"delegate"|"skill", "target_id": "agent_id或skill_id或空", "task": "任务描述或空"}}

子 Agent ID: code_agent, translate_agent, vision_agent, intent_agent
当前已注册 Skill：
{skill_manifest}

规则：
- 80% 的消息是闲聊，优先返回 {{"route": "chat", "target_id": "", "task": ""}}
- 只有明确的任务请求才委托，不要过度委托
- 如果某个已注册 skill 明显比子 Agent 更贴切，优先选 skill
- 不确定时返回 chat"""


_DISPATCH_SYSTEM_PROMPT = """你是一个意图分类器，输出固定格式 JSON，决定如何处理用户消息。

可选路由：
- "chat"：闲聊/情感/日常对话，直接由 ChatAgent 处理
- "delegate"：需要执行代码/翻译/图像分析等任务，委托给子 Agent
- "skill"：需要搜索/记忆/文件操作等，调用 Skill 执行

输出格式（严格 JSON，不要额外文字）：
{"route": "chat"|"delegate"|"skill", "target_id": "agent_id或skill_id或空", "task": "任务描述或空"}

子 Agent ID: code_agent, translate_agent, vision_agent, intent_agent
Skill ID: web_search, memory_store, file_read, file_write, file_list, system_info, process_list, network_status, browser_navigate, browser_screenshot, hub_skill_install, hub_skill_search, hub_skill_list, hub_skill_remove

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
            system_prompt=_build_dispatch_system_prompt(),
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
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    task_registry = get_runtime_task_registry()

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

    # ── 5. 多步骤计划执行（优先于单步 skill/delegate）────────────────────

    plan = None
    if route in {"chat", "skill", "delegate"}:
        try:
            plan = await _maybe_build_multi_step_plan(
                user_message=sanitized_message,
                request_id=request_id,
                llm_runtime=llm_runtime,
            )
        except Exception as _plan_exc:
            _logger.debug("[%s] DispatchAgent plan build failed: %s", request_id, _plan_exc)

    if plan is not None:
        _logger.info(
            "[%s] DispatchAgent → plan_execution steps=%d parallel=%s",
            request_id,
            len(list(plan.get("steps") or [])),
            bool(plan.get("parallel_enabled")),
        )
        yield _sse({
            "phase": "PRESENCE",
            "event": "PLAN_RUNNING",
            "request_id": request_id,
            "step_count": len(list(plan.get("steps") or [])),
            "parallel": bool(plan.get("parallel_enabled")),
        })
        plan_ctx = await _execute_multi_step_plan(
            plan=plan,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            llm_runtime=llm_runtime,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            render_target_lang=render_target_lang,
        )
        if plan_ctx is not None:
            chat_input.sub_agent_context = plan_ctx
            async for event in chat_agent_sse_generator(chat_input):
                yield event
            return

    # ── 5. 路由分发 ───────────────────────────────────────────────────

    if route == "task_progress":
        _logger.info("[%s] DispatchAgent → task_progress (reason=%s)", request_id, route_reason)
        from host_brain.app.top_agent import _airlock_sanitize_output, _async_post_process  # noqa: PLC0415
        progress_ctx = _build_task_progress_sub_context(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
        )
        narrative = _airlock_sanitize_output(str(progress_ctx.result_text or "现在没有正在执行，或者刚结束但还值得汇报的任务。").strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _sse({
            "phase": "COMMIT", "done": True, "decision": "APPROVE",
            "narrative": narrative, "request_id": request_id,
            "audit_refs": {
                "request_id": request_id,
                "path": "dispatch_agent:task_progress",
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
            retrieval_summary={"status": "SKIPPED", "reason_code": "TASK_PROGRESS_DIRECT"},
            gov_decision=governor_result, pii_ingress_stats=pii_ingress_stats,
            pii_egress_stats={},
        ))
        return

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

        task_registry, runtime_task = _get_or_create_runtime_task(
            request_id=request_id,
            task_kind="sub_agent",
            target_id=target_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            detail=f"delegating to {target_id}",
            progress_message=f"我正在处理 {target_id} 这条委托。",
            meta={"route_reason": route_reason, "task_description": task_description},
        )

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

        if sub_ctx.success:
            task_registry.update_task(
                runtime_task.task_id,
                status="RUNNING",
                detail=f"{target_id} 已完成，正在整理回复。",
                progress_message=f"{target_id} 已完成，我正在把结果整理成自然回复。",
                result_text=sub_ctx.result_text,
                result_code=None,
                error=None,
            )
        else:
            failure_code = _extract_result_code(sub_ctx.error)
            task_registry.update_task(
                runtime_task.task_id,
                status="RUNNING",
                detail=f"{target_id} 执行阶段失败，正在整理回复。",
                progress_message="执行阶段出了点问题，我正在整理怎么告诉你。",
                error=sub_ctx.error,
                result_code=failure_code,
                result_text=sub_ctx.result_text,
            )
            recovery_plan = _build_failure_recovery_plan(
                target_id=target_id,
                user_message=sanitized_message,
                error_text=sub_ctx.error,
            )
            if recovery_plan and str(recovery_plan.get("agent_id") or "") != target_id:
                task_registry.mark_recovering(
                    runtime_task.task_id,
                    detail=str(recovery_plan.get("summary") or "正在补救中。"),
                    recovery_plan=recovery_plan,
                )
                yield _sse({
                    "phase": "PRESENCE",
                    "event": "SELF_HEALING",
                    "request_id": request_id,
                    "task_id": runtime_task.task_id,
                    "agent": recovery_plan.get("agent_id"),
                })
                recovery_ctx = await _dispatch_to_sub_agent(
                    agent_id=str(recovery_plan.get("agent_id") or "intent_agent"),
                    task=str(recovery_plan.get("task") or sanitized_message),
                    user_message=sanitized_message,
                    request_id=request_id,
                    session_id=session_id,
                    llm_runtime=llm_runtime,
                    emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL").upper(),
                    bond_stage=str((bond_summary or {}).get("stage") or "STRANGER").upper(),
                    render_target_lang=render_target_lang,
                    previous_agent_result=sub_ctx.error or sub_ctx.result_text,
                )
                task_registry.attach_recovery_result(
                    runtime_task.task_id,
                    recovery_status="DONE" if recovery_ctx.success else "FAILED",
                    recovery_result_text=recovery_ctx.result_text,
                    recovery_error=recovery_ctx.error,
                )
                task_registry.update_task(
                    runtime_task.task_id,
                    status="RUNNING",
                    detail="自动补救分析已经完成，正在整理回复。",
                    progress_message="自动补救分析已经完成，我正在整理回复。",
                )
                sub_ctx = SubAgentContext(
                    agent_id="delegate_failure_recovery",
                    task=task_description,
                    result_text=recovery_ctx.result_text,
                    success=False,
                    latency_ms=sub_ctx.latency_ms + recovery_ctx.latency_ms,
                    error=sub_ctx.error,
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

        task_registry, runtime_task = _get_or_create_runtime_task(
            request_id=request_id,
            task_kind="skill",
            target_id=target_id,
            session_id=session_id,
            person_id=person_id,
            user_message=sanitized_message,
            detail=f"running skill {target_id}",
            progress_message=f"我正在执行 {target_id}。",
            meta={"route_reason": route_reason},
        )

        skill_ctx = await _execute_skill(
            skill_id=target_id,
            user_message=sanitized_message,
            request_id=request_id,
            session_id=session_id,
        )

        if skill_ctx.success:
            task_registry.update_task(
                runtime_task.task_id,
                status="RUNNING",
                detail=f"{target_id} 已完成，正在整理回复。",
                progress_message=f"{target_id} 已完成，我正在把结果整理成自然回复。",
                result_text=skill_ctx.result_text,
                result_code=None,
                error=None,
            )
        else:
            failure_code = _extract_result_code(skill_ctx.error)
            task_registry.update_task(
                runtime_task.task_id,
                status="RUNNING",
                detail=f"{target_id} 执行阶段失败，正在整理回复。",
                progress_message="执行阶段出了点问题，我正在整理怎么告诉你。",
                error=skill_ctx.error,
                result_code=failure_code,
                result_text=skill_ctx.result_text,
            )
            recovery_plan = _build_failure_recovery_plan(
                target_id=target_id,
                user_message=sanitized_message,
                error_text=skill_ctx.error,
            )
            if recovery_plan:
                task_registry.mark_recovering(
                    runtime_task.task_id,
                    detail=str(recovery_plan.get("summary") or "正在补救中。"),
                    recovery_plan=recovery_plan,
                )
                yield _sse({
                    "phase": "PRESENCE",
                    "event": "SELF_HEALING",
                    "request_id": request_id,
                    "task_id": runtime_task.task_id,
                    "agent": recovery_plan.get("agent_id"),
                })
                recovery_agent_id = str(recovery_plan.get("agent_id") or "intent_agent")
                recovery_ctx = await _dispatch_to_sub_agent(
                    agent_id=recovery_agent_id,
                    task=str(recovery_plan.get("task") or sanitized_message),
                    user_message=sanitized_message,
                    request_id=request_id,
                    session_id=session_id,
                    llm_runtime=llm_runtime,
                    emotion_tag=str(emotion_context.get("emotion_tag") or "NEUTRAL").upper(),
                    bond_stage=str((bond_summary or {}).get("stage") or "STRANGER").upper(),
                    render_target_lang=render_target_lang,
                    previous_agent_result=skill_ctx.error or skill_ctx.result_text,
                )
                task_registry.attach_recovery_result(
                    runtime_task.task_id,
                    recovery_status="DONE" if recovery_ctx.success else "FAILED",
                    recovery_result_text=recovery_ctx.result_text,
                    recovery_error=recovery_ctx.error,
                )
                if recovery_ctx.success and bool(recovery_plan.get("retry_original")):
                    yield _sse({
                        "phase": "PRESENCE",
                        "event": "RETRYING",
                        "request_id": request_id,
                        "task_id": runtime_task.task_id,
                        "target": target_id,
                    })
                    retried_ctx = await _retry_after_repair(
                        route_kind="skill",
                        target_id=target_id,
                        task_description=sanitized_message,
                        user_message=sanitized_message,
                        request_id=request_id,
                        session_id=session_id,
                        llm_runtime=llm_runtime,
                        emotion_context=emotion_context,
                        bond_summary=bond_summary,
                        render_target_lang=render_target_lang,
                    )
                    if retried_ctx.success:
                        task_registry.update_task(
                            runtime_task.task_id,
                            status="RUNNING",
                            detail=f"{target_id} 已在自动修复后重试成功，正在整理回复。",
                            progress_message=f"{target_id} 已在自动修复后重试成功。",
                            error=None,
                            result_code=None,
                            result_text=retried_ctx.result_text,
                        )
                        skill_ctx = retried_ctx
                    else:
                        task_registry.update_task(
                            runtime_task.task_id,
                            status="RUNNING",
                            detail="自动修复已执行，但重试后仍然失败，正在整理回复。",
                            progress_message="自动修复已经执行，但重试后还是失败了。",
                            error=retried_ctx.error or skill_ctx.error,
                            result_code=_extract_result_code(retried_ctx.error) or failure_code,
                            result_text=retried_ctx.result_text or recovery_ctx.result_text,
                        )
                        skill_ctx = SubAgentContext(
                            agent_id="skill_failure_recovery",
                            task=sanitized_message,
                            result_text=recovery_ctx.result_text,
                            success=False,
                            latency_ms=skill_ctx.latency_ms + recovery_ctx.latency_ms + retried_ctx.latency_ms,
                            error=retried_ctx.error or skill_ctx.error,
                        )
                else:
                    task_registry.update_task(
                        runtime_task.task_id,
                        status="RUNNING",
                        detail="自动补救分析已经完成，正在整理回复。",
                        progress_message="自动补救分析已经完成，我正在整理回复。",
                    )
                    skill_ctx = SubAgentContext(
                        agent_id="skill_failure_recovery",
                        task=sanitized_message,
                        result_text=recovery_ctx.result_text,
                        success=False,
                        latency_ms=skill_ctx.latency_ms + recovery_ctx.latency_ms,
                        error=skill_ctx.error,
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

_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_PATH_RE = re.compile(r"[A-Za-z]:(?:\\|/(?!/))[^\s\"'<>|]+")


def _extract_memory_store_content(user_message: str) -> str:
    text = str(user_message or "").strip()
    if not text:
        return ""
    text = re.sub(r"^\s*(?:蓝蓝|夏目蓝|夏目藍|natsume ai|natsume|小蓝|阿蓝)[，,、:：\s]*", "", text, count=1, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:请)?(?:帮我)?(?:记住|记一下|记下|备忘(?:一下)?)[，,、:：\s]*", "", text, count=1)
    return text.strip(" \t\r\n。！？!?，,；;：:")


def _extract_first_url(user_message: str) -> str:
    match = _URL_RE.search(str(user_message or ""))
    return str(match.group(0)).strip() if match else ""


def _extract_hub_skill_source(user_message: str) -> str:
    raw_message = str(user_message or "").strip()
    direct_url = _extract_first_url(raw_message)
    if direct_url:
        return direct_url
    scheme_match = re.search(r"(?:github|skillshub|clawhub):[^\s，。；,!?！？]+", raw_message, re.IGNORECASE)
    if scheme_match:
        return str(scheme_match.group(0)).strip()
    path_value = _extract_first_path(raw_message)
    if path_value:
        return path_value
    normalized = raw_message
    for marker in ("安装 hub 技能", "安装技能", "安装skill"):
        if marker in normalized:
            normalized = normalized.split(marker, 1)[-1].strip(" ：:")
            break
    normalized = re.sub(r"\s+(?:强制|覆盖|覆盖安装|立即激活|现在激活)$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized.strip("，。；！？,.!? ")


def _extract_first_path(user_message: str) -> str:
    match = _PATH_RE.search(str(user_message or ""))
    if not match:
        return ""
    return str(match.group(0)).strip().rstrip("，。,；;！？!?:：")


def _extract_file_read_window(user_message: str) -> tuple[int, int | None]:
    text = str(user_message or "")
    line_match = re.search(r"第\s*(\d+)\s*行", text)
    if line_match:
        line_no = max(1, int(line_match.group(1)))
        return line_no, 1
    first_lines_match = re.search(r"前\s*(\d+)\s*行", text)
    if first_lines_match:
        return 1, max(1, int(first_lines_match.group(1)))
    return 1, None


def _extract_file_write_content(user_message: str) -> str:
    text = str(user_message or "").strip()
    for marker in ("内容：", "内容:", "写入：", "写入:"):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx + len(marker):].strip()
    return ""


def _build_skill_args(skill_id: str, user_message: str) -> dict[str, Any]:
    raw_message = str(user_message or "").strip()
    if skill_id == "memory_store":
        content = _extract_memory_store_content(raw_message) or raw_message
        return {
            "content": content,
            "memory_type": "episodic",
            "source": "chat_api",
            "user_message": raw_message,
        }
    if skill_id == "file_read":
        offset_line, limit_lines = _extract_file_read_window(raw_message)
        return {
            "path": _extract_first_path(raw_message),
            "offset_line": offset_line,
            "limit_lines": limit_lines,
            "encoding": "utf-8",
            "user_message": raw_message,
        }
    if skill_id == "file_write":
        return {
            "path": _extract_first_path(raw_message),
            "content": _extract_file_write_content(raw_message),
            "encoding": "utf-8",
            "create_dirs": True,
            "backup": True,
            "user_message": raw_message,
        }
    if skill_id == "browser_navigate":
        return {
            "url": _extract_first_url(raw_message),
            "wait_until": "load",
            "timeout_ms": 30000,
            "user_message": raw_message,
        }
    if skill_id == "browser_screenshot":
        screenshot_path = _extract_first_path(raw_message)
        return {
            "save_path": screenshot_path if screenshot_path.lower().endswith((".png", ".jpg", ".jpeg")) else None,
            "selector": None,
            "full_page": True,
            "user_message": raw_message,
        }
    if skill_id == "file_list":
        return {
            "path": _extract_first_path(raw_message),
            "pattern": "*",
            "recursive": ("递归" in raw_message or "全部" in raw_message or "所有" in raw_message),
            "include_hidden": False,
            "max_items": 50,
            "user_message": raw_message,
        }
    if skill_id == "system_info":
        return {
            "include_cpu": True,
            "include_memory": True,
            "include_disk": True,
            "include_network": ("网络" in raw_message),
            "include_gpu": False,
            "user_message": raw_message,
        }
    if skill_id == "process_list":
        return {
            "limit": 20,
            "sort_by": "memory",
            "user_message": raw_message,
        }
    if skill_id == "network_status":
        return {
            "host": "8.8.8.8",
            "timeout_ms": 3000,
            "get_public_ip": False,
            "user_message": raw_message,
        }
    if skill_id == "hub_skill_install":
        return {
            "source": _extract_hub_skill_source(raw_message),
            "force": ("强制" in raw_message or "覆盖" in raw_message),
            "activate_now": True,
            "user_message": raw_message,
        }
    if skill_id == "hub_skill_search":
        normalized_query = raw_message
        for marker in ("搜索技能", "查找技能", "搜技能"):
            if marker in normalized_query:
                normalized_query = normalized_query.split(marker, 1)[-1].strip(" ：:")
                break
        return {
            "query": normalized_query,
            "user_message": raw_message,
        }
    if skill_id == "hub_skill_list":
        return {
            "user_message": raw_message,
        }
    if skill_id == "hub_skill_remove":
        normalized_name = raw_message
        for marker in ("卸载技能", "删除技能"):
            if marker in normalized_name:
                normalized_name = normalized_name.split(marker, 1)[-1].strip(" ：:")
                break
        return {
            "skill_name": normalized_name,
            "user_message": raw_message,
        }
    return {
        "query": raw_message,
        "text": raw_message,
        "content": raw_message,
        "request": raw_message,
        "url": _extract_first_url(raw_message) or None,
        "path": _extract_first_path(raw_message) or None,
        "user_message": raw_message,
    }


def _make_skill_episodic_writer(
    *,
    episodic_store: Any,
    request_id: str,
    intent_id: str,
    skill_id: str,
    default_emotion_tag: str = "NEUTRAL",
):
    def _writer(
        *,
        content: str,
        tags: list[str] | None = None,
        importance: float = 0.5,
        source: str | None = None,
        emotion_tag: str | None = None,
        session_id: str | None = None,
    ) -> str:
        del tags, importance, source, session_id
        result = episodic_store.write_memory(
            text=str(content or "").strip(),
            evidence_refs=[f"skill:{skill_id}", f"request:{request_id}"],
            request_id=request_id,
            intent_id=intent_id,
            emotion_tag=str(emotion_tag or default_emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
        )
        if not getattr(result, "success", False):
            raise RuntimeError(str(getattr(result, "reason_code", None) or getattr(result, "detail", "episodic write failed")))
        return str(getattr(result, "memory_id", "") or "")

    return _writer


def _coerce_structured_skill_result(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return dict(raw)
    text = str(raw or "").strip()
    if not text or text[0] not in "[{":
        return None
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return dict(parsed)
    return None


def _summarize_skill_result_text(skill_id: str, payload: dict[str, Any] | None, fallback_text: str) -> str:
    structured = dict(payload or {})
    detail = structured.get("detail") if isinstance(structured.get("detail"), dict) else {}
    message = str((detail or {}).get("message") or "").strip()
    if skill_id == "hub_skill_install":
        name = str((detail or {}).get("name") or "这个技能").strip() or "这个技能"
        activated = bool((detail or {}).get("activated"))
        if message:
            return message[:2000]
        return (f"技能 {name} 已安装并激活" if activated else f"技能 {name} 已安装")[:2000]
    if skill_id == "hub_skill_search":
        total = int((detail or {}).get("total") or 0)
        query = str(structured.get("query") or structured.get("action") or "相关技能").strip() or "相关技能"
        return f"已搜索到 {total} 个与{query}相关的技能"[:2000]
    if skill_id == "hub_skill_list":
        total = int((detail or {}).get("total") or 0)
        return f"当前已安装 {total} 个 Hub 技能"[:2000]
    if skill_id == "hub_skill_remove":
        name = str((detail or {}).get("name") or "该技能").strip() or "该技能"
        if message:
            return message[:2000]
        return f"技能 {name} 已移除"[:2000]
    if message:
        return message[:2000]
    fallback = str(fallback_text or "").strip()
    return fallback[:2000] if fallback else f"Skill {skill_id} 执行成功"


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

    def _run_sync() -> tuple[str, bool, str, dict[str, Any]]:
        """在线程池中执行同步 Skill 调用，返回 (result_text, success, error, payload)。"""
        from host_brain.app.dependencies import get_skill_registry, get_ledger, get_episodic_store  # noqa: PLC0415
        from host_brain.skills.loader import SkillLoader  # noqa: PLC0415
        from host_brain.governor.tokens import TokenService  # noqa: PLC0415
        from host_brain.core.ids import new_event_id  # noqa: PLC0415

        registry = get_skill_registry()
        ledger = get_ledger()

        # 验证 skill 存在
        meta = registry.get(skill_id)
        if meta is None:
            return "", False, f"Skill '{skill_id}' not found in registry", {}

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
            "args": _build_skill_args(skill_id, user_message),
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
        skill_context = {
            "session_id": session_id,
            "request_id": request_id,
            "emotion_tag": "NEUTRAL",
            "mood_state": "steady",
        }
        if skill_id.startswith("memory_"):
            try:
                episodic_store = get_episodic_store()
                skill_context["episodic_writer"] = _make_skill_episodic_writer(
                    episodic_store=episodic_store,
                    request_id=request_id,
                    intent_id=intent_id,
                    skill_id=skill_id,
                    default_emotion_tag="NEUTRAL",
                )
            except Exception as exc:
                _logger.warning("[%s] prepare memory backend for skill %s failed: %s", request_id, skill_id, exc)
        result = loader.execute(
            intent=intent,
            governor_output=governor_output,
            context=skill_context,
        )

        # 提取结果文本
        if result.status == "SUCCESS":
            detail = result.detail or {}
            payload = dict(getattr(result, "runtime_payload", None) or {})
            if not payload:
                payload = _coerce_structured_skill_result(detail.get("result_digest")) or {}
            result_digest = str(detail.get("result_digest") or "")
            result_text = _summarize_skill_result_text(skill_id, payload, result_digest)
            return result_text, True, "", payload
        elif result.status == "IDEMPOTENT_REPLAY":
            return f"（已执行过相同的 {skill_id} 操作）", True, "", {}
        else:
            detail = result.detail or {}
            payload = dict(getattr(result, "runtime_payload", None) or {})
            if not payload:
                payload = _coerce_structured_skill_result(detail.get("result_digest")) or {}
            return "", False, f"{result.result_code}: {result.status}", payload

    try:
        result_text, success, error, payload = await asyncio.to_thread(_run_sync)
        latency_ms = int(time.time() * 1000) - started_ms
        return SubAgentContext(
            agent_id=f"skill:{skill_id}",
            task=user_message,
            result_text=result_text,
            success=success,
            latency_ms=latency_ms,
            error=error,
            payload=payload,
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
