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
    AGENT_ID_RETRIEVAL,
    AGENT_ID_TRANSLATE,
    get_agent_meta,
)
from host_brain.app.graph_config_builder import AGENT_DISPATCH_NODE_ID
from host_brain.integrations.graph_contract import (
    SUPERVISOR_DECISION_SOURCE_LLM,
    SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE,
    SUPERVISOR_DECISION_SOURCE_RULE,
    SUPERVISOR_FAILURE_LLM_BAD_JSON,
    SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR,
    SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID,
    SUPERVISOR_FAILURE_LLM_TIMEOUT,
    SUPERVISOR_PROPOSAL_VALID_RISK_MODES,
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

    # 2. SUPERVISOR_MODE 只允许 rule 或 llm（P5-2）
    # P15：同时支持 SUPERVISOR_AUTO_ENABLED=true 作为 SUPERVISOR_MODE=llm 的别名
    # 优先级：SUPERVISOR_MODE=llm > SUPERVISOR_AUTO_ENABLED=true > 默认 rule
    _raw_mode = str(os.environ.get("SUPERVISOR_MODE") or "rule").strip().lower()
    _auto_enabled = _parse_bool(os.environ.get("SUPERVISOR_AUTO_ENABLED"), default=False)
    supervisor_mode = "llm" if (_raw_mode == "llm" or _auto_enabled) else "rule"
    use_llm_proposal = supervisor_mode == "llm"

    # 3. 图内 allowlist：与 agent_dispatch_handler 后续使用的保持一致（P5-2）
    allowlist_raw = ctx.get("agent_dispatch_allowlist")
    allowlist: list[str] | None = None
    if isinstance(allowlist_raw, (list, tuple)):
        allowlist = [str(x).strip() for x in allowlist_raw if str(x).strip()]

    # 4. 规则默认值（用于 rule 路径与 llm 失败回退）
    rule_agent = _SCENE_DEFAULT_AGENT.get(scene_type, AGENT_ID_CHAT)
    rule_risk_mode = _SCENE_DEFAULT_RISK_MODE.get(scene_type, RISK_MODE_SAFE)
    rule_need_judge = _SCENE_NEED_JUDGE.get(scene_type, False)

    selected_agent = rule_agent
    risk_mode = rule_risk_mode
    need_judge = rule_need_judge
    decision_source = SUPERVISOR_DECISION_SOURCE_RULE
    supervisor_failure_code: str | None = None

    if use_llm_proposal:
        # P5-2：LLM 模式四步 — 调 LLM、解析 JSON、契约校验、编译为运行时字段；失败统一回退 rule
        llm_proposal, failure_code = _try_llm_proposal(
            payload=payload,
            scene_type=scene_type,
            context=ctx,
        )
        if llm_proposal is None:
            selected_agent = rule_agent
            risk_mode = rule_risk_mode
            need_judge = rule_need_judge
            decision_source = SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
            supervisor_failure_code = failure_code or SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR
        else:
            # 契约口径（P5）：缺失 need_judge/risk_mode 允许，handler 补 rule 默认；显式非法 risk_mode 判 LLM_SCHEMA_INVALID
            raw_rm = str(llm_proposal.get("risk_mode") or "").strip().lower()
            proposal_to_validate = {
                "selected_agent": str(llm_proposal.get("selected_agent") or "").strip(),
                "risk_mode": raw_rm if raw_rm else rule_risk_mode,  # 仅缺失时补默认，显式非法则原样送校验
                "need_judge": _parse_bool(llm_proposal.get("need_judge"), rule_need_judge),
            }
            is_valid, validation_failure = validate_supervisor_proposal(proposal_to_validate, allowlist=allowlist)
            if not is_valid:
                selected_agent = rule_agent
                risk_mode = rule_risk_mode
                need_judge = rule_need_judge
                decision_source = SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
                supervisor_failure_code = validation_failure or SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR
            else:
                selected_agent = proposal_to_validate["selected_agent"]
                risk_mode = proposal_to_validate["risk_mode"]
                need_judge = proposal_to_validate["need_judge"]
                decision_source = SUPERVISOR_DECISION_SOURCE_LLM

    # 5. 下一跳与输出（仅写安全字段，不写控图键）
    next_node = AGENT_DISPATCH_NODE_ID
    agent_meta = get_agent_meta(selected_agent) or {}

    out = dict(payload)
    out["active_agent_id"] = selected_agent
    out["handoff_history"] = [
        {
            "from": "supervisor",
            "to": selected_agent,
            "reason": f"scene_type={scene_type} {decision_source}",
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
    # P5-2：观测字段落稳，供 P5-4 metrics 消费
    out["supervisor_dispatch"] = {
        "dispatcher_node": node_id,
        "scene_type": scene_type,
        "supervisor_mode": supervisor_mode,
        "supervisor_decision_source": decision_source,
        "supervisor_failure_code": supervisor_failure_code,
        "supervisor_selected_agent": selected_agent,
        "selected_agent": selected_agent,
        "agent_display_name": str(agent_meta.get("display_name") or selected_agent),
        "next_node": next_node,
        "risk_mode": risk_mode,
        "need_judge": need_judge,
        "dispatched_at_ms": int(time.time() * 1000),
    }
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
- retrieval_agent：处理知识库检索、信息查找（低风险，只读）

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
- 检索/查找知识库 → retrieval_agent，risk_mode=safe，need_judge=true
- 不确定时 → chat_agent（保守选择）
"""


def _try_llm_proposal(
    payload: dict[str, Any],
    scene_type: str,
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """P5-2：调 LLM 拿 proposal 原文、解析 JSON；不在此做 schema/allowlist 校验。

    失败时返回 (None, failure_code)，由 handler 统一回退 rule。

    Returns:
        (proposal_dict, None) 成功；(None, failure_code) 失败。
    """
    import json as _json

    gateway = context.get("gateway") or payload.get("_gateway")
    if gateway is None:
        return None, SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR

    user_input = str(
        payload.get("user_input")
        or payload.get("query")
        or ""
    ).strip()
    if not user_input:
        return None, SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR

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

        if not raw:
            return None, SUPERVISOR_FAILURE_LLM_BAD_JSON

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
            return None, SUPERVISOR_FAILURE_LLM_BAD_JSON
        # JSON 可解析但缺 selected_agent → schema invalid，非 bad json
        if not str(proposal.get("selected_agent") or "").strip():
            return None, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
        return proposal, None

    except TimeoutError:
        return None, SUPERVISOR_FAILURE_LLM_TIMEOUT
    except (_json.JSONDecodeError, TypeError, ValueError):
        return None, SUPERVISOR_FAILURE_LLM_BAD_JSON
    except Exception:
        return None, SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR
