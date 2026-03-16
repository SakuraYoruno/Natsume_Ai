"""JudgeAgent Handler（M-MA5）。

职责：
- 读取 state["agent_artifacts"]（所有子 agent 的输出工件）
- 评估工件质量（置信度、风险标记）
- 选择最优工件或合并多个工件
- 写入 state["judge_result"]
- 不写 __next_node__（由 graph_executor 决定下一跳为 renderer）
- 不写 committed_route_token

JudgeAgent 是多 agent 编排的统一收口，所有非聊天场景都经过此 handler。
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_JUDGE


def judge_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JudgeAgent handler（M-MA5）。

    裁判汇总子 agent 输出工件，写入 judge_result。

    裁判逻辑：
    1. 读取所有 agent_artifacts
    2. 过滤掉有错误的工件
    3. 按置信度排序，选择最优工件
    4. 检查风险标记，决定是否降级
    5. 写入 judge_result

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 judge_result）
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        # 读取所有子 agent 工件（GraphState 顶层字段）
        agent_artifacts = dict(out.get("agent_artifacts") or {})

        if not agent_artifacts:
            # 没有工件：降级，写入空 judge_result
            out["judge_result"] = {
                "status": "DEGRADED",
                "reason": "NO_ARTIFACTS",
                "selected_agent": None,
                "selected_artifact": None,
                "confidence": 0.0,
                "risk_flags": ["NO_ARTIFACTS"],
                "ts_ms": int(time.time() * 1000),
            }
            return out

        # 过滤有错误的工件
        valid_artifacts = {
            agent_id: artifact
            for agent_id, artifact in agent_artifacts.items()
            if not artifact.get("error")
        }

        if not valid_artifacts:
            # 所有工件都有错误：降级
            out["judge_result"] = {
                "status": "DEGRADED",
                "reason": "ALL_ARTIFACTS_FAILED",
                "selected_agent": None,
                "selected_artifact": None,
                "confidence": 0.0,
                "risk_flags": ["ALL_ARTIFACTS_FAILED"],
                "ts_ms": int(time.time() * 1000),
            }
            return out

        # 按置信度排序，选择最优工件
        best_agent_id = max(
            valid_artifacts,
            key=lambda aid: float(valid_artifacts[aid].get("confidence") or 0.0),
        )
        best_artifact = valid_artifacts[best_agent_id]
        best_confidence = float(best_artifact.get("confidence") or 0.0)

        # 汇总所有工件的风险标记
        all_risk_flags: list[str] = []
        for artifact in valid_artifacts.values():
            all_risk_flags.extend(list(artifact.get("risk_flags") or []))
        # 去重
        all_risk_flags = list(dict.fromkeys(all_risk_flags))

        # 判断是否需要降级
        high_risk = any(
            flag in all_risk_flags
            for flag in {"SAFETY_RISK:HIGH", "SAFETY_RISK:CRITICAL"}
        )
        judge_status = "DEGRADED" if high_risk or best_confidence < 0.3 else "OK"

        out["judge_result"] = {
            "status": judge_status,
            "reason": "HIGH_RISK" if high_risk else ("LOW_CONFIDENCE" if best_confidence < 0.3 else "OK"),
            "selected_agent": best_agent_id,
            "selected_artifact": {
                "artifact_ref": str(best_artifact.get("artifact_ref") or ""),
                "summary": str(best_artifact.get("summary") or ""),
                "confidence": best_confidence,
            },
            "all_agents_evaluated": list(valid_artifacts.keys()),
            "confidence": best_confidence,
            "risk_flags": all_risk_flags,
            "ts_ms": int(time.time() * 1000),
        }

        # judge_agent 追踪
        out["judge_agent_trace"] = {
            "agent_id": AGENT_ID_JUDGE,
            "artifacts_evaluated": len(agent_artifacts),
            "artifacts_valid": len(valid_artifacts),
            "selected_agent": best_agent_id,
            "judge_status": judge_status,
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
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
