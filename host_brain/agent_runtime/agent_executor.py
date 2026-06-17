"""AgentExecutor — 基于 API Agent 替代本地 CLI 子进程。

使用 ReAct 循环（Think → Act → Observe）+ 沙箱工具执行，
通过 LLM 池子（complete_llm_with_pool_sync）驱动，不再依赖本地 CLI 二进制。

启用条件：HOST_NATSUME_AGENT_ENABLED=true
Provider/Model 选择：由 HOST_LLM_POOL_TIER_* 控制，scene=NATSUME_AGENT，tier 按 run_mode 分：
  inspect/research → C, audit/translate/worker/self_heal/self_evolve → B
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from host_brain.agent_runtime.agent_prompt import build_agent_system_prompt
from host_brain.agent_runtime.agent_tools import (
    build_agent_tool_specs,
    execute_agent_tool,
    is_tool_allowed_for_run_mode,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.work_order import AgentWorkOrder

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

_DEFAULT_MAX_STEPS = 8
_DEFAULT_TIMEOUT_MS = 300_000  # 5 分钟
_NATSUME_AGENT_SCENE = "NATSUME_AGENT"
_MAX_JSON_RETRIES = 1  # JSON 解析失败时每步最多重试次数

# run_mode → cognitive tier: 只读浏览用 C，生产/危险操作用 B
_NATSUME_AGENT_TIER_BY_MODE: dict[str, str] = {
    "inspect": "C",
    "research": "C",
    "audit": "B",
    "translate": "B",
    "worker": "B",
    "self_heal": "B",
    "self_evolve": "B",
}


def _resolve_agent_tier(run_mode: str) -> str:
    mode = str(run_mode or "inspect").strip().lower()
    return _NATSUME_AGENT_TIER_BY_MODE.get(mode, "B")


def _parse_bool(value: Any, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "on"}


def _env_int(name: str, *, default: int, minimum: int = 1, maximum: int = 100) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip() or default)
        return max(minimum, min(maximum, value))
    except (ValueError, TypeError):
        return default


def agent_enabled() -> bool:
    """检查是否启用 Agent 路径。"""
    return _parse_bool(os.environ.get("HOST_NATSUME_AGENT_ENABLED"), default=False)


# ---------------------------------------------------------------------------
# ReAct 步骤数据结构
# ---------------------------------------------------------------------------

class AgentStep:
    """单步 ReAct 记录。"""
    def __init__(
        self,
        step: int,
        thought: str = "",
        tool_id: str = "",
        tool_args: dict[str, Any] | None = None,
        observation: dict[str, Any] | None = None,
        final_answer: str = "",
        duration_ms: int = 0,
    ) -> None:
        self.step = step
        self.thought = thought
        self.tool_id = tool_id
        self.tool_args = tool_args or {}
        self.observation = observation or {}
        self.final_answer = final_answer
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "thought": self.thought,
            "tool_id": self.tool_id,
            "tool_args": self.tool_args,
            "observation": self.observation,
            "final_answer": self.final_answer,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# LLM 输出解析
# ---------------------------------------------------------------------------

def _parse_llm_action(raw_text: str) -> dict[str, Any]:
    """将 LLM 输出解析为 thought + action 结构。

    预期格式：
    {
        "thought": "推理过程...",
        "tool_id": "agent_read_file",
        "tool_args": {"path": "..."},
        "final_answer": "..."
    }

    解析失败时返回 _parse_error=True 的 fallback 动作，
    调用方应重试而非直接结束。
    """
    text = str(raw_text or "").strip()

    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        text = text[start:end].strip() if end > start else text
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        text = text[start:end].strip() if end > start else text

    brace_start = text.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    text = text[brace_start:i + 1]
                    break

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("not a dict")
        return parsed
    except (json.JSONDecodeError, ValueError):
        return {
            "thought": "",
            "tool_id": "agent_finish",
            "tool_args": {},
            "final_answer": raw_text,
            "_parse_error": True,
        }


def _extract_final_answer(action: dict[str, Any], *, raw_output: str = "") -> str:
    """从 LLM action 中深度提取 final_answer。

    优先级：
    1. 顶层 final_answer
    2. 顶层 result
    3. tool_args.result / summary / final_answer / output
    4. raw_output（fallback）
    """
    # 1. 顶层 final_answer
    fa = action.get("final_answer")
    if isinstance(fa, str) and fa.strip():
        return fa.strip()

    # 2. 顶层 result
    fa = action.get("result")
    if isinstance(fa, str) and fa.strip():
        return fa.strip()

    # 3. 嵌套在 tool_args 内
    tool_args = action.get("tool_args")
    if isinstance(tool_args, dict):
        for key in ("result", "summary", "final_answer", "output"):
            val = tool_args.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                inner = val.get("result") or val.get("summary") or ""
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()

    # 4. fallback
    return str(raw_output or "").strip()


def _build_summary(final_answer: str, *, stopped_reason: str, max_chars: int = 280) -> str:
    """从 final_answer 构建有意义的摘要。"""
    if stopped_reason != "FINISH":
        return f"Agent executor stopped: {stopped_reason}"

    fa = str(final_answer or "").strip()
    if not fa:
        return "Agent executor completed with empty output."

    # 如果 answer 是 JSON，尝试解包
    if fa.startswith("{"):
        try:
            parsed = json.loads(fa)
            # 优先找自然语言字段
            inner = parsed.get("final_answer") or parsed.get("result") or parsed.get("summary") or ""
            if isinstance(inner, dict):
                inner = inner.get("result") or inner.get("summary") or str(inner)
            if isinstance(inner, str) and inner.strip():
                fa = inner.strip()
            else:
                # 结构化 JSON（如 git diff）→ 描述结构而非裸露数据
                structure_hint = _describe_structured_output(parsed)
                if structure_hint:
                    return structure_hint
        except (json.JSONDecodeError, TypeError):
            pass

    # 截断长文本
    if len(fa) > max_chars:
        cut = fa.rfind("。", 0, max_chars)
        if cut > max_chars // 2:
            return fa[:cut + 1]
        cut = fa.rfind(". ", 0, max_chars)
        if cut > max_chars // 2:
            return fa[:cut + 1]
        cut = fa.rfind(" ", 0, max_chars)
        if cut > max_chars // 2:
            return fa[:cut] + "…"
        fa = fa[:max_chars - 1] + "…"

    return fa


def _describe_structured_output(parsed: dict[str, Any]) -> str:
    """将结构化 JSON 输出转为简短描述句。"""
    pieces: list[str] = []

    diffstat = parsed.get("diffstat")
    if isinstance(diffstat, dict):
        added = int(diffstat.get("added", 0))
        changed = int(diffstat.get("changed", 0))
        deleted = int(diffstat.get("deleted", 0))
        if added or changed or deleted:
            pieces.append(f"{changed} file(s) changed (+{added}/-{deleted})")

    files = parsed.get("files")
    if isinstance(files, list) and files:
        names = [str(f.get("path", "") or "").strip() for f in files[:5] if isinstance(f, dict)]
        names = [n for n in names if n]
        if names:
            pieces.append("touched: " + ", ".join(names[:4]))

    untracked = parsed.get("untracked")
    if isinstance(untracked, list) and untracked:
        pieces.append(f"{len(untracked)} untracked file(s)")

    if pieces:
        return "Agent completed. " + "; ".join(pieces) + "."
    return ""


# ---------------------------------------------------------------------------
# LLM 消息构建
# ---------------------------------------------------------------------------

def _build_tool_description_for_prompt(tool_specs: list) -> str:
    """构建工具列表的可读描述。"""
    lines: list[str] = []
    for spec in tool_specs:
        tid = str(getattr(spec, "tool_id", ""))
        desc = str(getattr(spec, "description", ""))
        params = getattr(spec, "parameters", {})
        if params is None:
            params = {}
        param_str = json.dumps(params, ensure_ascii=False)
        lines.append(f"- **{tid}**: {desc}\n  Parameters: {param_str}")
    return "\n".join(lines)


def _build_step_messages(
    system_prompt: str,
    user_objective: str,
    steps: list[AgentStep],
) -> list[dict[str, str]]:
    """构建 LLM 消息列表。"""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_objective},
    ]
    for s in steps:
        if s.thought:
            messages.append({"role": "assistant", "content": s.thought})
        if s.tool_id and s.tool_id != "agent_finish":
            obs_str = json.dumps(s.observation, ensure_ascii=False)
            messages.append({"role": "user", "content": f"Tool `{s.tool_id}` result:\n{obs_str}"})
    return messages


# ---------------------------------------------------------------------------
# AgentExecutor
# ---------------------------------------------------------------------------

class AgentExecutor:
    """基于 ReAct 循环 + LLM 池子的 Agent 执行器。

    替代本地 CLI 子进程调度。Provider/Model/Key 由 LLM 池子自动管理，
    无需单独的 provider 配置——通过 scene=NATSUME_AGENT、tier=B 接入池子。
    """

    def __init__(self) -> None:
        self._max_steps = _env_int(
            "HOST_NATSUME_AGENT_MAX_STEPS",
            default=_DEFAULT_MAX_STEPS,
            minimum=1, maximum=50,
        )
        self._timeout_ms = _env_int(
            "HOST_NATSUME_AGENT_TIMEOUT_MS",
            default=_DEFAULT_TIMEOUT_MS,
            minimum=30_000, maximum=1_800_000,
        )

    def execute(self, work_order: AgentWorkOrder, *, workspace: Path) -> AgentResult:
        """执行 AgentWorkOrder 并通过 LLM 池子驱动 ReAct 循环。

        Args:
            work_order: 已校验的 AgentWorkOrder
            workspace: 已解析的工作区路径

        Returns:
            AgentResult（状态 + 输出 + changed_files + verification）
        """
        start_ts = time.time()
        run_mode = str(work_order.run_mode or "inspect").strip().lower()
        sandbox_enabled = run_mode in {"worker", "self_heal", "self_evolve", "audit"}
        request_id = str(work_order.metadata.get("request_id") or work_order.work_order_id)

        # 1. 构建工具
        tool_specs = build_agent_tool_specs(run_mode=run_mode)
        tools_desc = _build_tool_description_for_prompt(tool_specs)

        # 2. 构建 system prompt
        system_extra = build_agent_system_prompt(
            work_order,
            workspace=workspace,
            tools_description=tools_desc,
        )
        system_prompt = (
            "You are a code execution agent. You think step-by-step, then call exactly ONE tool per turn.\n"
            "Output a JSON object with: thought (your reasoning), tool_id (tool name), tool_args (parameters).\n"
            "\n"
            "BROWSER tips for SPA / JS-heavy pages:\n"
            "  - After browser_navigate, first use browser_scrape with selector=\"body\" to get full page text.\n"
            "  - If scrape returns 0 results, use browser_execute_script with script:\n"
            "    (() => document.body.innerText.substring(0, 2000))()\n"
            "    to inspect what's actually rendered in the DOM.\n"
            "  - Then use more specific selectors based on what you find.\n"
            "  - browser_execute_script is the most powerful tool — use it to extract structured data via JS.\n"
            "\n"
            "CRITICAL — when you call agent_finish:\n"
            "  - ALWAYS provide a `final_answer` field with a natural-language summary of your findings.\n"
            "  - The final_answer must be self-contained: describe what you did, what you found, and the outcome.\n"
            "  - Do NOT put your results in tool_args — put them directly in final_answer.\n"
            "  - If you include raw data (diffs, configs), wrap them in prose explanation.\n"
            "Example: {\"thought\":\"...\",\"tool_id\":\"agent_finish\",\"final_answer\":\"I found 3 bugs: 1) ...\"}\n\n"
            + system_extra
        )

        # 3. ReAct 循环
        from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415

        steps: list[AgentStep] = []
        final_answer = ""
        stopped_reason = "MAX_STEPS"
        risk_flags: list[str] = []
        last_provider = ""
        last_model = ""

        objective_msg = f"Execute the WorkOrder.\n\nObjective: {work_order.objective}"

        for step_num in range(1, self._max_steps + 1):
            elapsed_ms = int((time.time() - start_ts) * 1000)
            if elapsed_ms > self._timeout_ms:
                stopped_reason = "TIMEOUT"
                risk_flags.append("agent_timeout")
                break

            step_start = time.time()
            messages = _build_step_messages(system_prompt, objective_msg, steps)
            step_timeout = min(self._timeout_ms - elapsed_ms, 120_000)

            try:
                pool_result = complete_llm_with_pool_sync(
                    request_id=request_id,
                    scene_type=_NATSUME_AGENT_SCENE,
                    cognitive_tier=_resolve_agent_tier(run_mode),
                    messages=messages,
                    timeout_ms=step_timeout,
                    response_format={"type": "json_object"},
                )
                raw_output = pool_result.content
                last_provider = pool_result.final_provider
                last_model = pool_result.final_model
            except Exception as exc:
                logger.warning("Agent executor pool LLM call failed (step %d): %s", step_num, exc)
                stopped_reason = "LLM_ERROR"
                risk_flags.append("agent_llm_error")
                final_answer = f"LLM call failed at step {step_num}: {exc}"
                break

            if not raw_output:
                stopped_reason = "EMPTY_RESPONSE"
                risk_flags.append("agent_empty_response")
                break

            # 解析 LLM 输出（带 JSON 重试）
            json_retries_left = _MAX_JSON_RETRIES
            while True:
                action = _parse_llm_action(raw_output)
                # JSON 解析失败 → 重试
                if action.pop("_parse_error", False) and json_retries_left > 0:
                    json_retries_left -= 1
                    logger.debug("Agent executor step %d JSON parse failed, retrying (%d left)",
                                 step_num, json_retries_left)
                    # 注入错误反馈到消息列表，让模型修正
                    retry_messages = messages + [
                        {"role": "assistant", "content": raw_output[:2000]},
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was NOT valid JSON. "
                                "You MUST output a JSON object with: thought, tool_id, tool_args.\n"
                                "Example: {\"thought\":\"...\",\"tool_id\":\"agent_read_file\",\"tool_args\":{\"path\":\"...\"}}\n"
                                "When done: {\"thought\":\"...\",\"tool_id\":\"agent_finish\",\"final_answer\":\"your complete results...\"}\n"
                                "Please try again with valid JSON only."
                            ),
                        },
                    ]
                    retry_timeout = min(self._timeout_ms - int((time.time() - start_ts) * 1000), 60_000)
                    try:
                        pool_result = complete_llm_with_pool_sync(
                            request_id=request_id,
                            scene_type=_NATSUME_AGENT_SCENE,
                            cognitive_tier=_resolve_agent_tier(run_mode),
                            messages=retry_messages,
                            timeout_ms=retry_timeout,
                            response_format={"type": "json_object"},
                        )
                        raw_output = pool_result.content
                        last_provider = pool_result.final_provider
                        last_model = pool_result.final_model
                        continue  # 重新解析
                    except Exception as exc:
                        logger.warning("Agent executor JSON retry LLM call failed: %s", exc)
                        # 重试失败，退出内层循环，用原始 raw_output 作为 final_answer
                        action = {
                            "thought": "",
                            "tool_id": "agent_finish",
                            "tool_args": {},
                            "final_answer": raw_output,
                        }
                break  # 解析成功或重试耗尽

            thought = str(action.get("thought") or "").strip()
            tool_id = str(action.get("tool_id") or "").strip().lower()
            tool_args = dict(action.get("tool_args") or {})

            if not tool_id:
                tool_id = "agent_finish"
                tool_args = {}

            # 权限检查
            if not is_tool_allowed_for_run_mode(tool_id, run_mode):
                step_duration = int((time.time() - step_start) * 1000)
                obs = {"error": f"tool `{tool_id}` not allowed in run_mode=`{run_mode}`"}
                steps.append(AgentStep(
                    step=step_num, thought=thought,
                    tool_id=tool_id, tool_args=tool_args,
                    observation=obs, duration_ms=step_duration,
                ))
                continue

            # finish → 退出循环
            if tool_id == "agent_finish":
                step_duration = int((time.time() - step_start) * 1000)
                final_answer = _extract_final_answer(action, raw_output=raw_output)
                steps.append(AgentStep(
                    step=step_num, thought=thought,
                    tool_id="agent_finish", tool_args=tool_args,
                    final_answer=final_answer, duration_ms=step_duration,
                ))
                stopped_reason = "FINISH"
                break

            # 执行工具
            obs = execute_agent_tool(
                tool_id=tool_id,
                args=tool_args,
                workspace=workspace,
                sandbox_enabled=sandbox_enabled,
            )
            step_duration = int((time.time() - step_start) * 1000)
            steps.append(AgentStep(
                step=step_num, thought=thought,
                tool_id=tool_id, tool_args=tool_args,
                observation=obs, duration_ms=step_duration,
            ))

            if obs.get("error"):
                logger.debug("Agent executor tool %s error: %s", tool_id, obs["error"])

        # 4. 构建结果
        result_text = final_answer or "Agent did not produce a final answer."
        status = "EXECUTED" if stopped_reason == "FINISH" else "DEGRADED"
        summary = _build_summary(final_answer, stopped_reason=stopped_reason)

        return AgentResult(
            status=status,
            work_order_id=work_order.work_order_id,
            summary=summary,
            result_text=result_text[:24000],
            changed_files=[],
            git_diff_stat=None,
            artifacts_dir="",
            thread_id=None,
            verification={"status": "NOT_RUN", "reason": f"agent_stopped_{stopped_reason.lower()}"},
            risk_flags=risk_flags,
            review_required=work_order.review_required,
            detail=f"Agent finished with {len(steps)} steps, reason: {stopped_reason}",
            reason_code=None if stopped_reason == "FINISH" else f"AGENT_{stopped_reason}",
            metadata={
                "pool_provider": last_provider,
                "pool_model": last_model,
                "total_steps": len(steps),
                "stopped_reason": stopped_reason,
                "max_steps": self._max_steps,
                "steps": [s.to_dict() for s in steps],
            },
        )


__all__ = ["AgentExecutor", "agent_enabled"]

