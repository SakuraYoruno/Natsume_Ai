"""ReAct 循环核心实现（M-MA6）。

ReAct = Reason + Act，在 Governed Runtime 约束下的实现：
- Observe：读取 state（用户输入 + 上下文 + 历史工件）
- Think：LLM 推理，产出 thought + action（JSON 格式）
- Act：handler 校验 action，执行对应工具
- Observe：读取执行结果，写入 observation
- 判断：是否需要继续循环（max_steps 限制）

关键约束：
- LLM 产出的 action 只是提案，handler 负责校验和执行
- 工具调用结果写入 react_scratchpad，不直接修改 GraphState 控制字段
- max_steps 硬限制（默认 3），防止无限循环
- 有副作用的工具调用只允许在 SCENE_EXEC_INTENT 场景
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.agents.tool_registry import (
    ToolSpec,
    execute_tool,
    is_tool_allowed,
)
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text

# ---------------------------------------------------------------------------
# ReAct 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ReactStep:
    """ReAct 单步记录。"""
    step: int
    thought: str
    action_type: str                    # tool_call / final_answer / error
    action: dict[str, Any] | None       # tool_call 时：{"tool": "...", "args": {...}}
    observation: dict[str, Any] | None  # 工具执行结果
    final_answer: str | None            # final_answer 时的答案
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "thought": self.thought,
            "action_type": self.action_type,
            "action": self.action,
            "observation": self.observation,
            "final_answer": self.final_answer,
            "ts_ms": self.ts_ms,
        }


@dataclass
class ReactResult:
    """ReAct 循环执行结果。"""
    agent_id: str
    final_answer: str
    steps: list[ReactStep]
    stopped_reason: str                 # FINAL_ANSWER / MAX_STEPS / ERROR / NO_LLM
    total_steps: int
    confidence: float = 0.8
    risk_flags: list[str] = field(default_factory=list)

    def to_scratchpad_dict(self) -> dict[str, Any]:
        """转换为 react_scratchpad 格式，写入 payload。"""
        return {
            "agent_id": self.agent_id,
            "steps": [s.to_dict() for s in self.steps],
            "final_answer": self.final_answer,
            "total_steps": self.total_steps,
            "stopped_reason": self.stopped_reason,
            "confidence": self.confidence,
            "risk_flags": list(self.risk_flags),
            "ts_ms": int(time.time() * 1000),
        }


# ---------------------------------------------------------------------------
# ReAct 循环实现
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FIX-2.2：消息历史修剪（防止超出 context window）
# ---------------------------------------------------------------------------

_MAX_REACT_HISTORY_CHARS = 8000  # 约 2000 tokens


def _trim_messages(
    messages: list[dict[str, str]],
    *,
    max_chars: int = _MAX_REACT_HISTORY_CHARS,
) -> list[dict[str, str]]:
    """修剪消息历史，始终保留 system prompt + 原始 user 问题。

    当消息总字符数超过 max_chars 时，从中间截断历史工具调用记录，
    保留头部（system + 原始 user）和尾部（最近的工具调用结果）。

    Args:
        messages: 消息列表
        max_chars: 最大字符数阈值（默认 8000，约 2000 tokens）

    Returns:
        修剪后的消息列表
    """
    total_chars = sum(len(m.get("content") or "") for m in messages)
    if total_chars <= max_chars:
        return messages

    # 始终保留 system prompt + 原始 user 问题（前 2 条）
    preserved_head = messages[:2]
    tail = messages[2:]
    kept_tail: list[dict[str, str]] = []
    remaining = max_chars - sum(len(m.get("content") or "") for m in preserved_head)

    # 从尾部向前保留尽可能多的消息
    for msg in reversed(tail):
        content_len = len(msg.get("content") or "")
        if remaining - content_len < 0:
            break
        kept_tail.insert(0, msg)
        remaining -= content_len

    # 若有截断，插入截断提示
    if len(kept_tail) < len(tail):
        omitted = len(tail) - len(kept_tail)
        kept_tail.insert(0, {
            "role": "user",
            "content": f"[历史工具调用记录已截断，共 {omitted} 轮被省略]",
        })

    return preserved_head + kept_tail


# ---------------------------------------------------------------------------
# FIX-2.1：并行工具调用（READ 工具并发，WRITE 工具串行）
# ---------------------------------------------------------------------------

# 写工具集合（有副作用，必须串行执行）
_WRITE_TOOL_PREFIXES = ("write_", "update_", "delete_", "create_", "insert_", "save_", "set_")


def _is_write_tool(tool_id: str) -> bool:
    """判断工具是否为写工具（有副作用，需串行执行）。"""
    tid = str(tool_id or "").strip().lower()
    return any(tid.startswith(prefix) for prefix in _WRITE_TOOL_PREFIXES)


def _execute_tools_parallel(
    tool_calls: list[dict[str, Any]],
    *,
    agent_id: str,
    allow_side_effects: bool,
    context: dict[str, Any] | None = None,
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """并发执行多个工具调用。

    READ 工具（无副作用）并发执行，WRITE 工具（有副作用）串行执行，
    保证写操作的顺序安全。

    Args:
        tool_calls: 工具调用列表，每项格式 {"tool": str, "args": dict}
        agent_id: 执行此调用的 agent ID
        allow_side_effects: 是否允许有副作用的工具调用
        context: 执行上下文（含 request_id 等）
        max_workers: READ 工具最大并发线程数

    Returns:
        与 tool_calls 等长的结果列表，顺序与输入一致
    """
    ctx = dict(context or {})
    results: list[dict[str, Any] | None] = [None] * len(tool_calls)

    # 分离 READ 和 WRITE 工具
    read_calls = [(i, c) for i, c in enumerate(tool_calls)
                  if not _is_write_tool(str(c.get("tool") or ""))]
    write_calls = [(i, c) for i, c in enumerate(tool_calls)
                   if _is_write_tool(str(c.get("tool") or ""))]

    def _run_one(idx: int, call: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        tool_id = str(call.get("tool") or "").strip()
        if not is_tool_allowed(tool_id, agent_id, allow_side_effects=allow_side_effects):
            return idx, {"error": f"TOOL_NOT_ALLOWED:{tool_id}", "result": None}
        runtime_args = dict(call.get("args") or {})
        if ctx:
            runtime_args["_react_context"] = dict(ctx)
        return idx, execute_tool(tool_id, runtime_args)

    # READ 工具并发执行
    if read_calls:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(read_calls))) as executor:
            futures = {executor.submit(_run_one, i, c): i for i, c in read_calls}
            for future in as_completed(futures):
                idx, obs = future.result()
                results[idx] = obs

    # WRITE 工具串行执行（保证顺序和安全）
    for idx, call in write_calls:
        _, obs = _run_one(idx, call)
        results[idx] = obs

    # 兜底：确保没有 None（理论上不会发生）
    return [r if r is not None else {"error": "TOOL_EXEC_MISSING", "result": None} for r in results]


def _build_tools_description(tools: list[ToolSpec]) -> str:
    """构建工具描述文本，用于 system prompt。"""
    templates = _load_template_mapping("react_loop.prompt_templates")
    if not tools:
        return _mapping_text(
            templates,
            "no_tools_description",
        )
    lines: list[str] = []
    for tool in tools:
        lines.append(
            _mapping_text(
                templates,
                "tool_line",
                tool_id=tool.tool_id,
                description=tool.description,
            )
        )
        params = tool.parameters.get("properties") or {}
        if params:
            param_desc = ", ".join(
                f"{key}({value.get('description', '')})"
                for key, value in params.items()
            )
            lines.append(
                _mapping_text(
                    templates,
                    "tool_params_line",
                    param_desc=param_desc,
                )
            )
    return "\n".join(lines)


def _parse_llm_react_output(raw_output: str) -> dict[str, Any]:
    """解析 LLM 的 ReAct 输出（JSON 格式）。

    Returns:
        解析后的字典，解析失败时返回 final_answer 格式
    """
    raw = str(raw_output or "").strip()
    templates = _load_template_mapping("react_loop.prompt_templates")
    if not raw:
        return {"thought": "", "action_type": "final_answer", "final_answer": ""}

    # 尝试提取 JSON 块
    if "```json" in raw:
        start = raw.find("```json") + 7
        end = raw.find("```", start)
        raw = raw[start:end].strip() if end > start else raw
    elif "```" in raw:
        start = raw.find("```") + 3
        end = raw.find("```", start)
        raw = raw[start:end].strip() if end > start else raw

    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("not a dict")
        return parsed
    except (json.JSONDecodeError, ValueError):
        # 解析失败：把原始输出作为 final_answer
        return {
            "thought": _mapping_text(
                templates,
                "parse_failure_thought",
            ),
            "action_type": "final_answer",
            "final_answer": raw,
        }



def run_react_loop(
    *,
    agent_id: str,
    user_input: str,
    tools: list[ToolSpec],
    gateway: Any,
    system_prompt_extra: str = "",
    max_steps: int = 3,
    allow_side_effects: bool = False,
    context: dict[str, Any] | None = None,
    governor: Any = None,
    evidence_plane: Any = None,
) -> ReactResult:
    """执行 ReAct 循环（FIX-5.2：基于 LangGraph GovernedToolNode）。

    使用 LangGraph 原生 ToolNode 执行工具调用，Governor 门控和 Evidence Plane 审计
    通过 GovernedToolNode 保留（不可绕过）。

    Args:
        agent_id: 执行此循环的 agent ID
        user_input: 用户输入
        tools: 可用工具列表（由 get_tools_for_agent() 获取）
        gateway: LLM gateway 实例（需支持 .complete(messages) 方法）
        system_prompt_extra: 额外的 system prompt 内容（agent 专属指令）
        max_steps: 最大循环步数（硬限制，默认 3）
        allow_side_effects: 是否允许有副作用的工具调用
        context: 执行上下文（含 request_id 等）
        governor: Governor 实例（可选，None 时跳过门控）
        evidence_plane: Evidence Plane 实例（可选，None 时跳过审计）

    Returns:
        ReactResult（含 final_answer、steps、stopped_reason）
    """
    from host_brain.integrations.graph_governed_tool_node import GovernedToolNode  # noqa: PLC0415

    ctx = dict(context or {})
    steps: list[ReactStep] = []
    risk_flags: list[str] = []
    templates = _load_template_mapping("react_loop.prompt_templates")

    # 构建 GovernedToolNode（LangGraph ToolNode + Governor 门控）
    # 将 ToolSpec 列表转换为 LangChain Tool 格式
    lc_tools = _tool_specs_to_lc_tools(tools, agent_id=agent_id, allow_side_effects=allow_side_effects, ctx=ctx)
    governed_tool_node = GovernedToolNode(
        tools=lc_tools,
        governor=governor,
        evidence_plane=evidence_plane,
    )

    # 构建 system prompt
    tools_desc = _build_tools_description(tools)
    system_prompt = _mapping_text(
        templates,
        "system_prompt_template",
        tools_description=tools_desc,
    )
    if system_prompt_extra:
        system_prompt = system_prompt + "\n\n" + system_prompt_extra.strip()

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    final_answer = ""
    stopped_reason = "MAX_STEPS"

    for step_num in range(1, max_steps + 1):
        # --- Think：调用 LLM ---
        try:
            if gateway is None:
                final_answer = _mapping_text(templates, "no_llm_final_answer", agent_id=agent_id)
                stopped_reason = "NO_LLM"
                steps.append(ReactStep(
                    step=step_num,
                    thought=_mapping_text(templates, "no_llm_thought"),
                    action_type="final_answer",
                    action=None,
                    observation=None,
                    final_answer=final_answer,
                ))
                break

            # FIX-2.2：每步 LLM 调用前修剪消息历史，防止超出 context window
            messages = _trim_messages(messages)
            llm_response = gateway.complete(messages)
            raw_output = str(
                llm_response.get("content")
                or llm_response.get("text")
                or llm_response.get("response_text")
                or ""
            ).strip()

        except Exception as exc:
            final_answer = _mapping_text(templates, "llm_error_final_answer",
                                         error_type=type(exc).__name__, error=str(exc))
            stopped_reason = "ERROR"
            risk_flags.append(f"LLM_ERROR:{type(exc).__name__}")
            steps.append(ReactStep(
                step=step_num,
                thought=_mapping_text(templates, "llm_error_thought"),
                action_type="error",
                action=None,
                observation={"error": str(exc)},
                final_answer=final_answer,
            ))
            break

        # --- 解析 LLM 输出 ---
        parsed = _parse_llm_react_output(raw_output)
        thought = str(parsed.get("thought") or "").strip()
        action_type = str(parsed.get("action_type") or "final_answer").strip()

        if action_type == "final_answer":
            final_answer = str(parsed.get("final_answer") or raw_output).strip()
            stopped_reason = "FINAL_ANSWER"
            steps.append(ReactStep(
                step=step_num,
                thought=thought,
                action_type="final_answer",
                action=None,
                observation=None,
                final_answer=final_answer,
            ))
            break

        elif action_type == "tool_call":
            # --- Act：通过 GovernedToolNode 执行工具调用 ---
            action = dict(parsed.get("action") or {})
            tool_id = str(action.get("tool") or "").strip()
            tool_args = dict(action.get("args") or {})

            # 构建 tool_calls 格式（GovernedToolNode 接受的格式）
            multi_calls = parsed.get("actions")
            if isinstance(multi_calls, list) and len(multi_calls) > 1:
                tool_calls_input = [{"tool": c.get("tool", ""), "args": c.get("args", {})} for c in multi_calls]
            else:
                tool_calls_input = [{"tool": tool_id, "args": tool_args}]

            # 通过 GovernedToolNode 执行（Governor 门控 + LangGraph ToolNode + Evidence Plane）
            node_state = {
                "tool_calls": tool_calls_input,
                "request_id": str(ctx.get("request_id") or ""),
                "agent_id": agent_id,
            }
            node_result = governed_tool_node(node_state)

            # 检查 Governor 是否拒绝
            route_token = str(node_result.get("committed_route_token") or "").strip()
            if route_token and route_token != "GOV_APPROVE":
                observation = {"error": f"GOV_REJECTED:{route_token}", "result": None}
                risk_flags.append(f"GOV_REJECTED:{route_token}")
            else:
                tool_results = list(node_result.get("tool_results") or [])
                if len(tool_results) == 1:
                    observation = tool_results[0].get("result") or tool_results[0]
                elif tool_results:
                    observation = {"multi_tool_results": tool_results}
                else:
                    observation = {"result": None}

            steps.append(ReactStep(
                step=step_num,
                thought=thought,
                action_type="tool_call",
                action={"tool": tool_id, "args": tool_args},
                observation=observation,
                final_answer=None,
            ))

            messages.append({"role": "assistant", "content": raw_output})
            obs_text = json.dumps(observation, ensure_ascii=False)
            messages.append({
                "role": "user",
                "content": _mapping_text(templates, "tool_result_message", obs_text=obs_text),
            })

        else:
            final_answer = raw_output
            stopped_reason = "FINAL_ANSWER"
            steps.append(ReactStep(
                step=step_num,
                thought=thought,
                action_type="final_answer",
                action=None,
                observation=None,
                final_answer=final_answer,
            ))
            break

    if stopped_reason == "FINAL_ANSWER":
        confidence = 0.85 if not risk_flags else 0.6
    elif stopped_reason == "MAX_STEPS":
        confidence = 0.5
        risk_flags.append("REACT_MAX_STEPS_REACHED")
        if steps and not final_answer:
            last_step = steps[-1]
            final_answer = str(
                last_step.final_answer
                or last_step.thought
                or _mapping_text(templates, "max_steps_final_answer")
            )
    else:
        confidence = 0.3
        if not final_answer:
            final_answer = _mapping_text(templates, "abnormal_stop_final_answer", stopped_reason=stopped_reason)

    return ReactResult(
        agent_id=agent_id,
        final_answer=final_answer,
        steps=steps,
        stopped_reason=stopped_reason,
        total_steps=len(steps),
        confidence=confidence,
        risk_flags=risk_flags,
    )


def _tool_specs_to_lc_tools(
    tools: list[ToolSpec],
    *,
    agent_id: str,
    allow_side_effects: bool,
    ctx: dict[str, Any],
) -> list[Any]:
    """将 ToolSpec 列表转换为 LangChain Tool 格式（供 GovernedToolNode 使用）。

    FIX-5.2：GovernedToolNode 内部使用 LangGraph ToolNode，
    需要 LangChain Tool 格式（有 name、description、func 属性）。
    """
    try:
        from langchain_core.tools import StructuredTool  # noqa: PLC0415
    except ImportError:
        return []

    lc_tools = []
    for spec in tools:
        if spec.has_side_effects and not allow_side_effects:
            continue
        if spec.executor is None:
            continue

        _spec = spec
        _ctx = dict(ctx)
        _agent_id = agent_id
        _allow_se = allow_side_effects

        def _make_func(s: ToolSpec, c: dict, aid: str, ase: bool) -> Any:
            def _fn(**kwargs: Any) -> Any:
                if not is_tool_allowed(s.tool_id, aid, allow_side_effects=ase):
                    return {"error": f"TOOL_NOT_ALLOWED:{s.tool_id}", "result": None}
                runtime_args = dict(kwargs)
                if c:
                    runtime_args["_react_context"] = dict(c)
                return execute_tool(s.tool_id, runtime_args)
            return _fn

        lc_tool = StructuredTool.from_function(
            func=_make_func(_spec, _ctx, _agent_id, _allow_se),
            name=spec.tool_id,
            description=spec.description,
            args_schema=None,
        )
        lc_tools.append(lc_tool)
    return lc_tools


# ---------------------------------------------------------------------------
# FIX-2.4：ReAct 循环异步流式版本
# ---------------------------------------------------------------------------



async def astream_react_loop(
    *,
    agent_id: str,
    user_input: str,
    tools: list[ToolSpec],
    gateway: Any,
    system_prompt_extra: str = "",
    max_steps: int = 3,
    allow_side_effects: bool = False,
    context: dict[str, Any] | None = None,
):
    """ReAct 循环的异步流式版本，逐步 yield 每个 ReactStep，最后 yield ReactResult。

    实现说明（FIX-2.4）：
    当前为简化实现——在独立线程中运行同步 run_react_loop()，
    完成后批量 yield 所有 steps，最后 yield ReactResult。
    这保证了与现有同步代码的完全兼容，同时提供异步接口供 FastAPI 等框架使用。

    真正的逐步流式（每个 LLM token 实时推送）需要修改 gateway.complete() 为流式接口，
    见 FIX-PLAN §5.2 中期迁移方案。

    Args:
        agent_id: 执行此循环的 agent ID
        user_input: 用户输入
        tools: 可用工具列表
        gateway: LLM gateway 实例
        system_prompt_extra: 额外的 system prompt 内容
        max_steps: 最大循环步数（硬限制，默认 3）
        allow_side_effects: 是否允许有副作用的工具调用
        context: 执行上下文

    Yields:
        ReactStep（每个执行步骤）
        ReactResult（最终结果，最后一个 yield）
    """
    import asyncio
    import threading

    loop = asyncio.get_event_loop()
    result_future: asyncio.Future = loop.create_future()

    def _sync_runner() -> None:
        try:
            result = run_react_loop(
                agent_id=agent_id,
                user_input=user_input,
                tools=tools,
                gateway=gateway,
                system_prompt_extra=system_prompt_extra,
                max_steps=max_steps,
                allow_side_effects=allow_side_effects,
                context=context,
            )
            loop.call_soon_threadsafe(result_future.set_result, result)
        except Exception as exc:
            loop.call_soon_threadsafe(result_future.set_exception, exc)

    thread = threading.Thread(target=_sync_runner, daemon=True)
    thread.start()

    try:
        final_result: ReactResult = await asyncio.wait_for(result_future, timeout=120.0)
    except asyncio.TimeoutError:
        # 超时：构造降级结果
        final_result = ReactResult(
            agent_id=agent_id,
            final_answer="[ReAct 循环超时]",
            steps=[],
            stopped_reason="ERROR",
            total_steps=0,
            confidence=0.0,
            risk_flags=["ASTREAM_TIMEOUT"],
        )

    # 逐步 yield 每个 ReactStep
    for step in final_result.steps:
        yield step

    # 最后 yield ReactResult
    yield final_result
