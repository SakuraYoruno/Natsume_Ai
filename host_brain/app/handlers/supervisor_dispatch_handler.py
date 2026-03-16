"""Supervisor 分派 Handler：多 agent 编排的主 agent 入口。

职责：
1. 读取 scene_type、user_input、agent_runtime 预算
2. 根据场景规则（或 LLM 提案）选派子 agent
3. 校验提案合法性（agent_id 在 allowlist 内、risk_mode 合规）
4. 写入 payload["agent_runtime"]["plan"]
5. 写入 __next_node__（trusted handler 专属权）

关键约束（来自 ModelPlane.md）：
- LLM 输出只是提案，handler 负责校验和编译
- 不得直接把 LLM 输出的 selected_agent 写入 __next_node__，必须经过 allowlist 校验
- 不得写 committed_route_token（只有 Host Governor 可以写）
"""

from __future__ import annotations

import os
import time
from typing import Any

from host_brain.app.agents.agent_contract import (
    RISK_MODE_GUARDED,
    RISK_MODE_SAFE,
    RISK_MODE_STRICT,
    validate_supervisor_proposal,
)
from host_brain.app.agents.agent_registry import (
    AGENT_ID_CHAT,
    AGENT_ID_CODE,
    AGENT_ID_INTENT,
    AGENT_ID_JUDGE,
    AGENT_ID_TRANSLATE,
    get_agent_meta,
)
from host_brain.app.prompt_builder import (
    CHAT_DIRECT_NODE_ID,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)

# ---------------------------------------------------------------------------
# 场景 → 默认 agent 映射（规则路由，不依赖 LLM）
# ---------------------------------------------------------------------------

_SCENE_DEFAULT_AGENT: dict[str, str] = {
    SCENE_CHAT: AGENT_ID_CHAT,
    SCENE_WORK_CODE: AGENT_ID_CODE,
    SCENE_WORK_TRANSLATE: AGENT_ID_TRANSLATE,
    SCENE_EXEC_INTENT: AGENT_ID_INTENT,
    SCENE_VISION_UI: AGENT_ID_INTENT,
}

# 场景 → 默认风险模式
_SCENE_DEFAULT_RISK_MODE: dict[str, str] = {
    SCENE_CHAT: RISK_MODE_SAFE,
    SCENE_WORK_CODE: RISK_MODE_GUARDED,
    SCENE_WORK_TRANSLATE: RISK_MODE_SAFE,
    SCENE_EXEC_INTENT: RISK_MODE_STRICT,
    SCENE_VISION_UI: RISK_MODE_STRICT,
}

# 场景 → 是否需要 judge
_SCENE_NEED_JUDGE: dict[str, bool] = {
    SCENE_CHAT: False,       # 聊天快路径不需要 judge
    SCENE_WORK_CODE: True,
    SCENE_WORK_TRANSLATE: True,
    SCENE_EXEC_INTENT: True,
    SCENE_VISION_UI: True,
}

# agent_id → 对应的图节点 ID（用于写入 __next_node__）
_AGENT_TO_NODE: dict[str, str] = {
    AGENT_ID_CHAT: CHAT_DIRECT_NODE_ID,
    AGENT_ID_CODE: "workbench_code_assist",
    AGENT_ID_TRANSLATE: "workbench_translate",
    AGENT_ID_INTENT: "core_reasoner_primary",
    AGENT_ID_JUDGE: "consistency_verifier",
}


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def supervisor_dispatch_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Supervisor 分派 handler（trusted handler，可写 __next_node__）。

    第一阶段：基于规则路由（不调用 LLM），直接按 scene_type 选派 agent。
    第二阶段：可扩展为 LLM 提案 + handler 校验模式。

    Args:
        payload: 当前图 payload
        context: 执行上下文（含 request_id、node_id 等）

    Returns:
        更新后的 payload，含 __next_node__ 和 agent_runtime
    """
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    node_id = str(ctx.get("node_id") or "supervisor_dispatch").strip()

    # 1. 读取场景类型
    scene_type = str(payload.get("scene_type") or SCENE_CHAT).strip().upper() or SCENE_CHAT

    # 2. 检查多 agent 开关（灰度控制）
    multi_agent_enabled = _parse_bool(
        os.environ.get("MULTI_AGENT_ENABLED"),
        default=False,  # 默认关闭，灰度上线
    )
    supervisor_auto_enabled = _parse_bool(
        os.environ.get("SUPERVISOR_AUTO_ENABLED"),
        default=False,
    )

    # 3. 选派 agent（规则路由 or LLM 提案）
    selected_agent = _SCENE_DEFAULT_AGENT.get(scene_type, AGENT_ID_CHAT)
    risk_mode = _SCENE_DEFAULT_RISK_MODE.get(scene_type, RISK_MODE_SAFE)
    need_judge = _SCENE_NEED_JUDGE.get(scene_type, False)
    proposal_source = "rule"

    # M-MA6：SUPERVISOR_AUTO_ENABLED=true 时，调用 LLM 产出分派提案
    if supervisor_auto_enabled and multi_agent_enabled:
        llm_proposal = _try_llm_proposal(
            payload=payload,
            scene_type=scene_type,
            context=ctx,
        )
        if llm_proposal is not None:
            selected_agent = llm_proposal.get("selected_agent", selected_agent)
            risk_mode = llm_proposal.get("risk_mode", risk_mode)
            need_judge = bool(llm_proposal.get("need_judge", need_judge))
            proposal_source = "llm"

    # 4. 构建分派提案
    proposal_dict = {
        "selected_agent": selected_agent,
        "reason": f"scene_type={scene_type} {proposal_source}路由",
        "risk_mode": risk_mode,
        "need_judge": need_judge,
        "budget": {
            "max_hops": 2,
            "timeout_ms": 12000,
        },
    }

    # 5. 校验提案合法性（allowlist 校验）
    is_valid, validation_reason = validate_supervisor_proposal(proposal_dict)
    if not is_valid:
        # 提案非法：降级到聊天快路径
        selected_agent = AGENT_ID_CHAT
        risk_mode = RISK_MODE_SAFE
        need_judge = False
        validation_reason = f"PROPOSAL_INVALID_FALLBACK_TO_CHAT:{validation_reason}"

    # 6. 确定下一跳节点（trusted handler 专属权）
    next_node = _AGENT_TO_NODE.get(selected_agent, CHAT_DIRECT_NODE_ID)

    # 7. 获取 agent 元数据（用于 trace）
    agent_meta = get_agent_meta(selected_agent) or {}

    # 8. 构建输出（第三阶段：直接写 GraphState 顶层字段，不再写 payload["agent_runtime"]）
    out = dict(payload)
    # 写入 GraphState 顶层 agent 字段
    out["active_agent_id"] = selected_agent
    out["handoff_history"] = [
        {
            "from": "supervisor",
            "to": selected_agent,
            "reason": f"scene_type={scene_type} 规则路由",
            "ts_ms": int(time.time() * 1000),
        }
    ]
    out["agent_budget"] = {
        "max_hops": 2,
        "per_agent_timeout_ms": 12000,
        "total_budget_ms": 30000,
    }
    out["agent_artifacts"] = {}
    out["judge_result"] = None
    # supervisor_dispatch 追踪信息（不影响路由）
    out["supervisor_dispatch"] = {
        "dispatcher_node": node_id,
        "scene_type": scene_type,
        "selected_agent": selected_agent,
        "agent_display_name": str(agent_meta.get("display_name") or selected_agent),
        "next_node": next_node,
        "risk_mode": risk_mode,
        "need_judge": need_judge,
        "validation_reason": validation_reason,
        "proposal_source": proposal_source,          # M-MA6：rule / llm
        "multi_agent_enabled": multi_agent_enabled,
        "supervisor_auto_enabled": supervisor_auto_enabled,
        "dispatched_at_ms": int(time.time() * 1000),
    }
    # trusted handler 写入 __next_node__（不经过 LLM 直接输出）
    out["__next_node__"] = next_node

    return out


# ---------------------------------------------------------------------------
# M-MA6：Supervisor LLM 提案模式
# ---------------------------------------------------------------------------

_SUPERVISOR_SYSTEM_PROMPT = """你是一个任务分派专家，负责根据用户输入和场景类型，选择最合适的 AI agent 来处理任务。

可用的 agent：
- chat_agent：处理普通聊天、问答、闲聊（低风险，快路径）
- code_agent：处理代码生成、代码修改、代码解释（中风险）
- translate_agent：处理文本翻译（低风险）
- intent_agent：处理执行意图、工具调用、复杂任务规划（高风险）

你必须返回合法的 JSON 格式：
{
  "selected_agent": "agent_id",
  "reason": "选择原因",
  "risk_mode": "safe|guarded|strict",
  "need_judge": true|false
}

规则：
- 只能选择上面列出的 agent
- 聊天/问答 → chat_agent，risk_mode=safe，need_judge=false
- 代码相关 → code_agent，risk_mode=guarded，need_judge=true
- 翻译相关 → translate_agent，risk_mode=safe，need_judge=true
- 执行/工具/复杂任务 → intent_agent，risk_mode=strict，need_judge=true
- 不确定时 → chat_agent（保守选择）
"""


def _try_llm_proposal(
    payload: dict[str, Any],
    scene_type: str,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    """尝试调用 LLM 产出 Supervisor 分派提案。

    失败时返回 None（降级到规则路由）。

    Args:
        payload: 当前图 payload
        scene_type: 场景类型
        context: 执行上下文

    Returns:
        解析后的提案字典，或 None（失败时）
    """
    import json as _json

    gateway = context.get("gateway") or payload.get("_gateway")
    if gateway is None:
        return None  # 无 gateway，降级到规则路由

    user_input = str(
        payload.get("user_input")
        or payload.get("query")
        or ""
    ).strip()
    if not user_input:
        return None

    try:
        messages = [
            {"role": "system", "content": _SUPERVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"场景类型：{scene_type}\n用户输入：{user_input}"},
        ]
        response = gateway.complete(messages)
        raw = str(
            response.get("content")
            or response.get("text")
            or response.get("response_text")
            or ""
        ).strip()

        # 提取 JSON
        if "```json" in raw:
            start = raw.find("```json") + 7
            end = raw.find("```", start)
            raw = raw[start:end].strip() if end > start else raw
        elif "```" in raw:
            start = raw.find("```") + 3
            end = raw.find("```", start)
            raw = raw[start:end].strip() if end > start else raw

        proposal = _json.loads(raw)
        if not isinstance(proposal, dict):
            return None
        if not proposal.get("selected_agent"):
            return None
        return proposal

    except Exception:
        return None  # 任何异常都降级到规则路由
