from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Any

from host_brain.app.agents.agent_contract import (
    RISK_MODE_GUARDED,
    RISK_MODE_SAFE,
    RISK_MODE_STRICT,
    validate_supervisor_proposal,
)
from host_brain.app.agents.agent_registry import (
    AGENT_ID_CHAT,
    AGENT_ID_EMOTION,
    AGENT_ID_INTENT,
    AGENT_ID_RETRIEVAL,
    AGENT_ID_TRANSLATE,
    get_agent_meta,
    is_agent_allowed,
)
from host_brain.app.graph_config_builder import AGENT_DISPATCH_NODE_ID
from host_brain.app.prompt_builder import (
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged


def _load_prompt_templates() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("supervisor_dispatch_handler.prompt_templates")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(mapping: Mapping[str, Any], key: str, *, default: str = "", **kwargs: Any) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _resolve_supervisor_system_prompt() -> str:
    templates = _load_prompt_templates()
    return _mapping_text(templates, "system_prompt")


def _build_supervisor_user_prompt(scene_type: str, user_input: str) -> str:
    templates = _load_prompt_templates()
    return _mapping_text(
        templates,
        "user_prompt_template",
        scene_type=scene_type,
        user_input=user_input,
    )

# ---------------------------------------------------------------------------
# 场景 → Agent 映射（Phase XIV 遗留，仅作兜底，优先级低于 intent_draft.route）
# ---------------------------------------------------------------------------
_SCENE_DEFAULT_AGENT: dict[str, str] = {
    SCENE_CHAT: AGENT_ID_CHAT,
    SCENE_WORK_CODE: AGENT_ID_INTENT,
    SCENE_WORK_TRANSLATE: AGENT_ID_TRANSLATE,
    SCENE_EXEC_INTENT: AGENT_ID_INTENT,
    SCENE_VISION_UI: AGENT_ID_INTENT,
}

_SCENE_DEFAULT_RISK_MODE: dict[str, str] = {
    SCENE_CHAT: RISK_MODE_SAFE,
    SCENE_WORK_CODE: RISK_MODE_STRICT,
    SCENE_WORK_TRANSLATE: RISK_MODE_SAFE,
    SCENE_EXEC_INTENT: RISK_MODE_STRICT,
    SCENE_VISION_UI: RISK_MODE_STRICT,
}

_SCENE_NEED_JUDGE: dict[str, bool] = {
    SCENE_CHAT: False,
    SCENE_WORK_CODE: True,
    SCENE_WORK_TRANSLATE: True,
    SCENE_EXEC_INTENT: True,
    SCENE_VISION_UI: True,
}

# ---------------------------------------------------------------------------
# intent_draft.route → Agent 映射（Phase XV 新增，优先级高于 _SCENE_DEFAULT_AGENT）
# ---------------------------------------------------------------------------
_ROUTE_TO_AGENT: dict[str, str] = {
    "chat":         AGENT_ID_CHAT,
    "delegate":     "",            # 由 target_id 决定，见下方逻辑
    "skill":        AGENT_ID_INTENT,   # skill 由 intent_agent 执行
    "plan":         AGENT_ID_INTENT,   # 多步规划由 intent_agent 执行
    "task_progress": AGENT_ID_CHAT,    # 进度查询走 chat 快速回复
    "recent_turn":  AGENT_ID_CHAT,     # 最近对话回溯走 chat
}

_ROUTE_TO_RISK_MODE: dict[str, str] = {
    "chat":         RISK_MODE_SAFE,
    "delegate":     RISK_MODE_GUARDED,
    "skill":        RISK_MODE_STRICT,
    "plan":         RISK_MODE_STRICT,
    "task_progress": RISK_MODE_SAFE,
    "recent_turn":  RISK_MODE_SAFE,
}

_ROUTE_NEED_JUDGE: dict[str, bool] = {
    "chat":         False,
    "delegate":     True,
    "skill":        True,
    "plan":         True,
    "task_progress": False,
    "recent_turn":  False,
}

# delegate 时合法的 target_id → agent_id 映射
_DELEGATE_TARGET_TO_AGENT: dict[str, str] = {
    "translate_agent":  AGENT_ID_TRANSLATE,
    "intent_agent":     AGENT_ID_INTENT,
    "vision_agent":     AGENT_ID_INTENT,   # vision_agent 暂复用 intent_agent
    "retrieval_agent":  AGENT_ID_RETRIEVAL,
    "emotion_agent":    AGENT_ID_EMOTION,
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


def _resolve_agent_from_intent_draft(
    intent_draft: dict[str, Any],
    *,
    scene_type: str,
) -> tuple[str, str, bool, str] | None:
    """从 intent_draft.route/target_id 解析 (agent_id, risk_mode, need_judge, decision_source)。

    返回 None 表示 intent_draft 无有效路由信息，调用方应降级到 scene_type 映射。
    """
    route = str(intent_draft.get("route") or "").strip().lower()
    if not route or route not in _ROUTE_TO_AGENT:
        return None

    target_id = str(intent_draft.get("target_id") or "").strip()
    risk_mode = _ROUTE_TO_RISK_MODE.get(route, RISK_MODE_SAFE)
    need_judge = _ROUTE_NEED_JUDGE.get(route, False)

    if route == "delegate":
        # delegate 时由 target_id 决定具体 agent
        agent_id = _DELEGATE_TARGET_TO_AGENT.get(target_id, AGENT_ID_INTENT)
    else:
        agent_id = _ROUTE_TO_AGENT[route]

    # 验证 agent_id 在 allowlist 内
    if not is_agent_allowed(agent_id):
        agent_id = AGENT_ID_CHAT
        risk_mode = RISK_MODE_SAFE
        need_judge = False

    route_source = str(intent_draft.get("route_source") or "rule").strip()
    decision_source = f"intent_draft:{route}:{route_source}"
    return agent_id, risk_mode, need_judge, decision_source


def supervisor_dispatch_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Supervisor 分派处理器。

    Phase XV 改造：
    - 优先消费 intent_draft.route/target_id（由 thalamus_agent 统一收口后填充）
    - _SCENE_DEFAULT_AGENT 降为兜底（scene_type 有效时使用）
    - SUPERVISOR_MODE=llm 不再独立调用 LLM，改为复用 intent_draft 中已有的 LLM 路由结果
    """
    ctx = dict(context or {})
    node_id = str(ctx.get("node_id") or "supervisor_dispatch").strip()
    scene_type = str(payload.get("scene_type") or SCENE_CHAT).strip().upper() or SCENE_CHAT

    # ── 从 intent_draft 解析路由（Phase XV 主路径）──────────────────────────
    intent_draft = dict(payload.get("intent_draft") or {})
    intent_result = _resolve_agent_from_intent_draft(intent_draft, scene_type=scene_type)

    allowlist_raw = ctx.get("agent_dispatch_allowlist")
    allowlist: list[str] | None = None
    if isinstance(allowlist_raw, (list, tuple)):
        allowlist = [str(item).strip() for item in allowlist_raw if str(item).strip()]

    # ── 兜底：scene_type 映射（Phase XIV 遗留，仅在 intent_draft 无有效路由时使用）──
    rule_agent = _SCENE_DEFAULT_AGENT.get(scene_type, AGENT_ID_CHAT)
    rule_risk_mode = _SCENE_DEFAULT_RISK_MODE.get(scene_type, RISK_MODE_SAFE)
    rule_need_judge = _SCENE_NEED_JUDGE.get(scene_type, False)

    # 情感深度分析覆盖（保留原有逻辑）
    inferred_emotion = payload.get("inferred_emotion") or {}
    needs_deep = bool(payload.get("needs_deep_emotion_analysis")) if isinstance(inferred_emotion, dict) else False
    if needs_deep and rule_agent == AGENT_ID_CHAT:
        rule_agent = AGENT_ID_EMOTION
        rule_need_judge = False

    # ── 决策优先级：intent_draft > scene_type 映射 ──────────────────────────
    if intent_result is not None:
        selected_agent, risk_mode, need_judge, decision_source = intent_result
        # 情感深度分析覆盖（对 intent_draft 路径同样生效）
        if needs_deep and selected_agent == AGENT_ID_CHAT:
            selected_agent = AGENT_ID_EMOTION
            need_judge = False
            decision_source = f"{decision_source}:emotion_override"
    else:
        # 降级到 scene_type 映射
        selected_agent = rule_agent
        risk_mode = rule_risk_mode
        need_judge = rule_need_judge
        decision_source = f"scene_type:{scene_type}:rule_fallback"

    # ── SUPERVISOR_MODE 观测（不再影响决策，仅记录）──────────────────────────
    # Phase XV：SUPERVISOR_MODE=llm 不再独立调用 LLM，intent_draft 中已包含 LLM 路由结果。
    # route_source="llm" 时，说明 thalamus 的 canonical classifier 已走过 LLM 兜底。
    raw_mode = str(os.environ.get("SUPERVISOR_MODE") or "rule").strip().lower()
    supervisor_mode = "llm" if raw_mode == "llm" else "rule"
    supervisor_failure_code: str | None = None

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
        # Phase XV 新增：记录 intent_draft 路由信息，便于 trace
        "intent_route": str(intent_draft.get("route") or ""),
        "intent_target_id": str(intent_draft.get("target_id") or ""),
        "intent_route_source": str(intent_draft.get("route_source") or ""),
    }
    out["__next_node__"] = next_node

    return out


def _try_llm_proposal(
    payload: dict[str, Any],
    scene_type: str,
    context: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    import json as _json

    gateway = context.get("gateway") or payload.get("_gateway")
    if gateway is None:
        return None, SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR

    user_input = str(payload.get("user_input") or payload.get("query") or "").strip()
    if not user_input:
        return None, SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR

    try:
        messages = [
            {"role": "system", "content": _resolve_supervisor_system_prompt()},
            {"role": "user", "content": _build_supervisor_user_prompt(scene_type, user_input)},
        ]
        response = gateway.complete(messages)
        raw = str(response.get("content") or response.get("text") or response.get("response_text") or "").strip()

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
        if not str(proposal.get("selected_agent") or "").strip():
            return None, SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
        return proposal, None
    except TimeoutError:
        return None, SUPERVISOR_FAILURE_LLM_TIMEOUT
    except (_json.JSONDecodeError, TypeError, ValueError):
        return None, SUPERVISOR_FAILURE_LLM_BAD_JSON
    except Exception:
        return None, SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR
