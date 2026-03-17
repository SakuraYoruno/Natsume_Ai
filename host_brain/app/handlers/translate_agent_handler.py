"""TranslateAgent Handler（M-MA5 / P10 升级版）。

职责：
- 处理翻译请求（SCENE_WORK_TRANSLATE）
- TRANSLATE_AGENT_REACT_ENABLED=true 时：接入 ReAct 循环（run_react_loop）
- TRANSLATE_AGENT_REACT_ENABLED=false 时：读取现有节点链输出（向后兼容）
- 产出 AgentArtifact 工件，写入 state["agent_artifacts"]

对应节点：workbench_translate
ReAct 循环：run_react_loop（P6 已封板，只读工具，allow_side_effects=False）
"""

from __future__ import annotations

import os
import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
from host_brain.app.agents.react_loop import run_react_loop
from host_brain.app.agents.tool_registry import get_tools_for_agent
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _parse_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def translate_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """TranslateAgent handler（P10）。

    TRANSLATE_AGENT_REACT_ENABLED=true 时走 ReAct 循环：
        Observe → Think（LLM）→ Act（只读工具）→ Observe → ... → final_answer

    TRANSLATE_AGENT_REACT_ENABLED=false 时走原有节点链输出读取（向后兼容）。

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 agent_artifacts[translate_agent]）
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    react_enabled = _parse_bool_env("TRANSLATE_AGENT_REACT_ENABLED", default=False)

    out = dict(payload)

    try:
        if react_enabled:
            # ----------------------------------------------------------------
            # P10：ReAct 循环模式（只读工具，无副作用）
            # ----------------------------------------------------------------
            user_input = str(
                payload.get("user_input")
                or payload.get("query")
                or ""
            ).strip()

            # 获取 TranslateAgent 可用工具（只读，allow_side_effects=False）
            tools = get_tools_for_agent(AGENT_ID_TRANSLATE, allow_side_effects=False)

            # 获取 LLM gateway（从 context 或 payload 中读取）
            gateway = ctx.get("gateway") or payload.get("_gateway")

            # 执行 ReAct 循环（最多 3 步，翻译场景无需多步推理）
            react_result = run_react_loop(
                agent_id=AGENT_ID_TRANSLATE,
                user_input=user_input,
                tools=tools,
                gateway=gateway,
                system_prompt_extra="你是翻译专家，负责生成高质量的翻译草案。",
                max_steps=int(os.environ.get("TRANSLATE_AGENT_REACT_MAX_STEPS") or "3"),
                allow_side_effects=False,
                context=ctx,
            )

            # 写入 react_scratchpad（工作区，供 JudgeAgent 和 trace 读取）
            out["react_scratchpad"] = react_result.to_scratchpad_dict()

            artifact_ref = f"artifact://{request_id}/{AGENT_ID_TRANSLATE}_{int(time.time() * 1000)}"
            write_agent_artifact_to_state(
                out,
                agent_id=AGENT_ID_TRANSLATE,
                artifact_ref=artifact_ref,
                summary=react_result.final_answer[:200],
                confidence=react_result.confidence,
                risk_flags=react_result.risk_flags,
            )

            out["translate_agent_trace"] = {
                "agent_id": AGENT_ID_TRANSLATE,
                "mode": "react_loop",
                "artifact_ref": artifact_ref,
                "total_steps": react_result.total_steps,
                "stopped_reason": react_result.stopped_reason,
                "react_enabled": True,
                "ts_ms": int(time.time() * 1000),
            }

        else:
            # ----------------------------------------------------------------
            # P3.5：无前置 model 输出时由本 handler 调用 workbench_translate（executor 不再先跑 model）
            # ----------------------------------------------------------------
            if not out.get("workbench_translate") and callable(ctx.get("invoke_model_node")):
                model_out = ctx["invoke_model_node"]("workbench_translate")
                if model_out:
                    out["workbench_translate"] = model_out.get("workbench_translate") or model_out
                    if isinstance(model_out.get("__model_calls__"), list):
                        out["__model_calls__"] = list(out.get("__model_calls__") or []) + model_out["__model_calls__"]

            translate_node_output = dict(out.get("workbench_translate") or {})
            draft_summary = str(
                translate_node_output.get("payload", {}).get("translation")
                or translate_node_output.get("payload", {}).get("response_text")
                or "翻译草案已生成"
            ).strip()[:200]

            gateway_status = str(translate_node_output.get("gateway_status") or "OK").strip().upper()
            confidence = 0.88 if gateway_status == "OK" else 0.2
            risk_flags = []
            if gateway_status != "OK":
                risk_flags.append(f"GATEWAY_STATUS:{gateway_status}")

            artifact_ref = f"artifact://{request_id}/{AGENT_ID_TRANSLATE}_{int(time.time() * 1000)}"

            write_agent_artifact_to_state(
                out,
                agent_id=AGENT_ID_TRANSLATE,
                artifact_ref=artifact_ref,
                summary=draft_summary,
                confidence=confidence,
                risk_flags=risk_flags,
            )

            out["translate_agent_trace"] = {
                "agent_id": AGENT_ID_TRANSLATE,
                "mode": "legacy_chain",
                "artifact_ref": artifact_ref,
                "gateway_status": gateway_status,
                "react_enabled": False,
                "ts_ms": int(time.time() * 1000),
            }

    except Exception as exc:
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_ID_TRANSLATE] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_ID_TRANSLATE}_error",
            "summary": "TranslateAgent 执行异常",
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }

    return out
