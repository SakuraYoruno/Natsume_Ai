"""JudgeAgent Handler（P14）。

职责：
- 读取 state["agent_artifacts"]（所有子 agent 的输出工件）
- 通过 ReAct 循环（evaluate_artifact + compare_artifacts 工具）推理最优工件
- 写入 state["judge_result"] 和 state["react_scratchpad"]
- 不写 __next_node__（由 graph_executor 决定下一跳为 renderer）
- 不写 committed_route_token

P14 直接替换固定链式逻辑为 ReAct 模式，不引入双模式开关。

P4 下游消费口径（MULTI_AGENT_P4_SCOPE §8）：
- Intent 图下若需判断子图成败，仅从 payload["subgraph_failure"]（优先）与
  payload["subgraph_commit_summary"] 读取；不得使用其它字段或别名。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_JUDGE
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent


def judge_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JudgeAgent handler（P14，ReAct 模式）。

    Observe → Think（LLM）→ Act（evaluate_artifact / compare_artifacts）→ ... → final_answer

    裁判逻辑（ReAct 循环内）：
    1. LLM 观察所有 agent_artifacts
    2. 调用 evaluate_artifact 评估各工件质量
    3. 调用 compare_artifacts 比较并推荐最优工件
    4. 产出 final_answer（judge_result 摘要）

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 judge_result、react_scratchpad、judge_agent_trace）
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        # 读取所有子 agent 工件
        agent_artifacts = dict(out.get("agent_artifacts") or {})

        if not agent_artifacts:
            # 没有工件：直接降级，不进入 ReAct 循环
            out["judge_result"] = {
                "status": "DEGRADED",
                "reason": "NO_ARTIFACTS",
                "selected_agent": None,
                "selected_artifact": None,
                "confidence": 0.0,
                "risk_flags": ["NO_ARTIFACTS"],
                "mode": "react_loop",
                "ts_ms": int(time.time() * 1000),
            }
            out["judge_agent_trace"] = {
                "agent_id": AGENT_ID_JUDGE,
                "mode": "react_loop",
                "artifact_ref": f"artifact://{request_id}/{AGENT_ID_JUDGE}_degraded",
                "total_steps": 0,
                "stopped_reason": "NO_ARTIFACTS",
                "ts_ms": int(time.time() * 1000),
            }
            return out

        # 构建 user_input：将 agent_artifacts 序列化为 LLM 可读的描述
        artifacts_summary = []
        for agent_id, artifact in agent_artifacts.items():
            confidence = float(artifact.get("confidence") or 0.0)
            summary = str(artifact.get("summary") or "")[:100]
            risk_flags = list(artifact.get("risk_flags") or [])
            has_error = bool(artifact.get("error"))
            artifacts_summary.append(
                f"- {agent_id}: confidence={confidence:.2f}, "
                f"summary='{summary}', risk_flags={risk_flags}, error={has_error}"
            )
        artifacts_desc = "\n".join(artifacts_summary)
        user_input = (
            f"请评估以下 {len(agent_artifacts)} 个 agent 工件，选出最优工件并给出裁判结论：\n"
            f"{artifacts_desc}\n\n"
            f"工件详情（JSON）：{json.dumps(agent_artifacts, ensure_ascii=False, default=str)[:800]}"
        )

        # 获取 JudgeAgent 可用工具
        tools = get_tools_for_agent(AGENT_ID_JUDGE, allow_side_effects=False)

        # 获取 LLM gateway
        gateway = ctx.get("gateway") or payload.get("_gateway")

        # 执行 ReAct 循环
        react_result = run_react_loop(
            agent_id=AGENT_ID_JUDGE,
            user_input=user_input,
            tools=tools,
            gateway=gateway,
            system_prompt_extra=(
                "你是裁判专家，负责评估多个 agent 的输出工件，选出最优工件。"
                "使用 evaluate_artifact 评估单个工件，使用 compare_artifacts 比较多个工件。"
                "最终给出 final_answer，格式：selected_agent=<agent_id>, reason=<原因>, confidence=<0.0-1.0>"
            ),
            max_steps=int(os.environ.get("JUDGE_AGENT_REACT_MAX_STEPS") or "3"),
            allow_side_effects=False,
            context=ctx,
        )

        # 写入 react_scratchpad
        out["react_scratchpad"] = react_result.to_scratchpad_dict()

        # ReAct 循环本身出错时降级
        if react_result.stopped_reason == "ERROR":
            out["judge_result"] = {
                "status": "DEGRADED",
                "reason": "REACT_LOOP_ERROR",
                "selected_agent": None,
                "selected_artifact": None,
                "all_agents_evaluated": list(agent_artifacts.keys()),
                "confidence": 0.0,
                "risk_flags": ["REACT_LOOP_ERROR"],
                "mode": "react_loop",
                "ts_ms": int(time.time() * 1000),
            }
            artifact_ref = f"artifact://{request_id}/{AGENT_ID_JUDGE}_{int(time.time() * 1000)}"
            out["judge_agent_trace"] = {
                "agent_id": AGENT_ID_JUDGE,
                "mode": "react_loop",
                "artifact_ref": artifact_ref,
                "artifacts_evaluated": len(agent_artifacts),
                "selected_agent": None,
                "judge_status": "DEGRADED",
                "total_steps": react_result.total_steps,
                "stopped_reason": react_result.stopped_reason,
                "ts_ms": int(time.time() * 1000),
            }
            return out

        # 从 final_answer 中解析 judge_result
        final_answer = react_result.final_answer or ""
        selected_agent, judge_confidence, judge_reason = _parse_judge_final_answer(
            final_answer, agent_artifacts
        )

        # 汇总风险标记
        all_risk_flags: list[str] = []
        for artifact in agent_artifacts.values():
            all_risk_flags.extend(list(artifact.get("risk_flags") or []))
        all_risk_flags = list(dict.fromkeys(all_risk_flags))

        high_risk = any(
            flag in all_risk_flags
            for flag in {"SAFETY_RISK:HIGH", "SAFETY_RISK:CRITICAL"}
        )
        judge_status = "DEGRADED" if high_risk or judge_confidence < 0.3 else "OK"

        selected_artifact = None
        if selected_agent and selected_agent in agent_artifacts:
            art = agent_artifacts[selected_agent]
            selected_artifact = {
                "artifact_ref": str(art.get("artifact_ref") or ""),
                "summary": str(art.get("summary") or ""),
                "confidence": float(art.get("confidence") or 0.0),
            }

        out["judge_result"] = {
            "status": judge_status,
            "reason": judge_reason,
            "selected_agent": selected_agent,
            "selected_artifact": selected_artifact,
            "all_agents_evaluated": list(agent_artifacts.keys()),
            "confidence": judge_confidence,
            "risk_flags": all_risk_flags,
            "mode": "react_loop",
            "ts_ms": int(time.time() * 1000),
        }

        artifact_ref = f"artifact://{request_id}/{AGENT_ID_JUDGE}_{int(time.time() * 1000)}"
        out["judge_agent_trace"] = {
            "agent_id": AGENT_ID_JUDGE,
            "mode": "react_loop",
            "artifact_ref": artifact_ref,
            "artifacts_evaluated": len(agent_artifacts),
            "selected_agent": selected_agent,
            "judge_status": judge_status,
            "total_steps": react_result.total_steps,
            "stopped_reason": react_result.stopped_reason,
            "ts_ms": int(time.time() * 1000),
        }

    except Exception as exc:
        # 降级：写入错误 judge_result，不抛异常
        out["judge_result"] = {
            "status": "DEGRADED",
            "reason": f"JUDGE_ERROR:{type(exc).__name__}",
            "selected_agent": None,
            "selected_artifact": None,
            "confidence": 0.0,
            "risk_flags": [f"JUDGE_ERROR:{type(exc).__name__}"],
            "mode": "react_loop",
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out


def _parse_judge_final_answer(
    final_answer: str,
    agent_artifacts: dict[str, Any],
) -> tuple[str | None, float, str]:
    """从 ReAct final_answer 中解析裁判结论。

    Returns:
        (selected_agent, confidence, reason)
    """
    # 尝试从 final_answer 中提取 selected_agent
    selected_agent: str | None = None
    confidence = 0.0
    reason = "OK"

    if not agent_artifacts:
        return selected_agent, confidence, "NO_ANSWER"

    # 尝试匹配 "selected_agent=xxx" 格式
    import re  # noqa: PLC0415
    match = re.search(r"selected_agent\s*=\s*(\w+)", final_answer)
    if match:
        candidate = match.group(1).strip()
        if candidate in agent_artifacts:
            selected_agent = candidate

    # 尝试匹配 confidence
    conf_match = re.search(r"confidence\s*=\s*([0-9.]+)", final_answer)
    if conf_match:
        try:
            confidence = float(conf_match.group(1))
            confidence = max(0.0, min(1.0, confidence))
        except ValueError:
            pass

    # 尝试匹配 reason
    reason_match = re.search(r"reason\s*=\s*([^\n,]+)", final_answer)
    if reason_match:
        reason = reason_match.group(1).strip()[:100]

    # 若未能解析出 selected_agent，回退到置信度最高的有效工件
    if selected_agent is None:
        valid = {
            aid: art for aid, art in agent_artifacts.items()
            if not art.get("error")
        }
        if valid:
            selected_agent = max(
                valid,
                key=lambda aid: float(valid[aid].get("confidence") or 0.0),
            )
            confidence = float(agent_artifacts[selected_agent].get("confidence") or 0.0)
            reason = "FALLBACK_MAX_CONFIDENCE"

    # 若 confidence 仍为 0，从工件中读取
    if confidence == 0.0 and selected_agent and selected_agent in agent_artifacts:
        confidence = float(agent_artifacts[selected_agent].get("confidence") or 0.0)

    return selected_agent, confidence, reason
