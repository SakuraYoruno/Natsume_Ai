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
from dataclasses import dataclass, field
from typing import Any

from host_brain.app.agents.tool_registry import (
    ToolSpec,
    execute_tool,
    is_tool_allowed,
)

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

_REACT_SYSTEM_PROMPT_TEMPLATE = """你是一个专业的 AI 助手，负责完成用户的任务。

你可以使用以下工具：
{tools_description}

每次回复必须是合法的 JSON 格式，包含以下字段之一：

1. 调用工具时：
{{
  "thought": "你的推理过程",
  "action_type": "tool_call",
  "action": {{
    "tool": "工具名称",
    "args": {{工具参数}}
  }}
}}

2. 给出最终答案时：
{{
  "thought": "你的推理过程",
  "action_type": "final_answer",
  "final_answer": "最终答案内容"
}}

规则：
- 每次只能调用一个工具
- 工具名称必须是上面列出的工具之一
- 如果不需要工具，直接给出 final_answer
- 回复必须是合法 JSON，不要包含其他内容
"""


def _build_tools_description(tools: list[ToolSpec]) -> str:
    """构建工具描述文本，用于 system prompt。"""
    if not tools:
        return "（无可用工具，请直接给出 final_answer）"
    lines = []
    for tool in tools:
        lines.append(f"- {tool.tool_id}：{tool.description}")
        params = tool.parameters.get("properties") or {}
        if params:
            param_desc = ", ".join(
                f"{k}（{v.get('description', '')}）"
                for k, v in params.items()
            )
            lines.append(f"  参数：{param_desc}")
    return "\n".join(lines)


def _parse_llm_react_output(raw_output: str) -> dict[str, Any]:
    """解析 LLM 的 ReAct 输出（JSON 格式）。

    Returns:
        解析后的字典，解析失败时返回 final_answer 格式
    """
    raw = str(raw_output or "").strip()
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
            "thought": "LLM 输出解析失败，直接返回原始内容",
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
) -> ReactResult:
    """执行 ReAct 循环。

    Args:
        agent_id: 执行此循环的 agent ID
        user_input: 用户输入
        tools: 可用工具列表（由 get_tools_for_agent() 获取）
        gateway: LLM gateway 实例（需支持 .complete(messages) 方法）
        system_prompt_extra: 额外的 system prompt 内容（agent 专属指令）
        max_steps: 最大循环步数（硬限制，默认 3）
        allow_side_effects: 是否允许有副作用的工具调用
        context: 执行上下文（含 request_id 等）

    Returns:
        ReactResult（含 final_answer、steps、stopped_reason）
    """
    ctx = dict(context or {})
    steps: list[ReactStep] = []
    risk_flags: list[str] = []

    # 构建 system prompt
    tools_desc = _build_tools_description(tools)
    system_prompt = _REACT_SYSTEM_PROMPT_TEMPLATE.format(tools_description=tools_desc)
    if system_prompt_extra:
        system_prompt = system_prompt + "\n\n" + system_prompt_extra.strip()

    # 构建初始消息列表
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
                # 无 gateway：直接返回 final_answer（降级）
                final_answer = f"[ReAct 降级] agent_id={agent_id}，无 LLM gateway"
                stopped_reason = "NO_LLM"
                steps.append(ReactStep(
                    step=step_num,
                    thought="无 LLM gateway，直接降级",
                    action_type="final_answer",
                    action=None,
                    observation=None,
                    final_answer=final_answer,
                ))
                break

            llm_response = gateway.complete(messages)
            raw_output = str(
                llm_response.get("content")
                or llm_response.get("text")
                or llm_response.get("response_text")
                or ""
            ).strip()

        except Exception as exc:
            # LLM 调用失败：降级
            final_answer = f"[ReAct LLM 错误] {type(exc).__name__}: {exc}"
            stopped_reason = "ERROR"
            risk_flags.append(f"LLM_ERROR:{type(exc).__name__}")
            steps.append(ReactStep(
                step=step_num,
                thought="LLM 调用失败",
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
            # 停止循环，返回最终答案
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
            # --- Act：执行工具调用 ---
            action = dict(parsed.get("action") or {})
            tool_id = str(action.get("tool") or "").strip()
            tool_args = dict(action.get("args") or {})

            # 校验工具是否允许调用
            if not is_tool_allowed(tool_id, agent_id, allow_side_effects=allow_side_effects):
                observation = {
                    "error": f"TOOL_NOT_ALLOWED:{tool_id}",
                    "result": None,
                }
                risk_flags.append(f"TOOL_NOT_ALLOWED:{tool_id}")
            else:
                # 执行工具
                observation = execute_tool(tool_id, tool_args)

            step_record = ReactStep(
                step=step_num,
                thought=thought,
                action_type="tool_call",
                action={"tool": tool_id, "args": tool_args},
                observation=observation,
                final_answer=None,
            )
            steps.append(step_record)

            # 把工具结果追加到消息历史，供下一轮 LLM 参考
            messages.append({"role": "assistant", "content": raw_output})
            obs_text = json.dumps(observation, ensure_ascii=False)
            messages.append({"role": "user", "content": f"工具执行结果：{obs_text}\n\n请继续。"})

        else:
            # 未知 action_type：视为 final_answer
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

    # 计算置信度（根据 stopped_reason 和 risk_flags）
    if stopped_reason == "FINAL_ANSWER":
        confidence = 0.85 if not risk_flags else 0.6
    elif stopped_reason == "MAX_STEPS":
        confidence = 0.5
        risk_flags.append("REACT_MAX_STEPS_REACHED")
        # MAX_STEPS 时用最后一步的 thought 作为 final_answer
        if steps and not final_answer:
            last_step = steps[-1]
            final_answer = str(last_step.final_answer or last_step.thought or "ReAct 达到最大步数")
    else:
        confidence = 0.3
        if not final_answer:
            final_answer = f"[ReAct 异常停止] stopped_reason={stopped_reason}"

    return ReactResult(
        agent_id=agent_id,
        final_answer=final_answer,
        steps=steps,
        stopped_reason=stopped_reason,
        total_steps=len(steps),
        confidence=confidence,
        risk_flags=risk_flags,
    )
