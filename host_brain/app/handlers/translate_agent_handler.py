"""TranslateAgent Handler（M-MA5）。

职责：
- 处理翻译请求（SCENE_WORK_TRANSLATE）
- 产出 AgentArtifact 工件，写入 state["agent_artifacts"]

对应节点：workbench_translate
"""

from __future__ import annotations

import time
from typing import Any

from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def translate_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """TranslateAgent handler（M-MA5）。

    读取 workbench_translate 节点的输出，封装为 AgentArtifact 工件。

    Args:
        payload: 当前图 payload
        context: 执行上下文

    Returns:
        更新后的 state（含 agent_artifacts[translate_agent]）
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()

    out = dict(payload)

    try:
        translate_node_output = dict(payload.get("workbench_translate") or {})
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
            "artifact_ref": artifact_ref,
            "gateway_status": gateway_status,
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
