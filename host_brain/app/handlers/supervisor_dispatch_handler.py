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
    AGENT_ID_CODE,
    AGENT_ID_EMOTION,
    AGENT_ID_INTENT,
    AGENT_ID_RETRIEVAL,
    AGENT_ID_TRANSLATE,
    get_agent_meta,
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
from host_brain.integrations.graph_contract import (
    SUPERVISOR_DECISION_SOURCE_LLM,
    SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE,
    SUPERVISOR_DECISION_SOURCE_RULE,
    SUPERVISOR_FAILURE_LLM_BAD_JSON,
    SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR,
    SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID,
    SUPERVISOR_FAILURE_LLM_TIMEOUT,
)

_SCENE_DEFAULT_AGENT: dict[str, str] = {
    SCENE_CHAT: AGENT_ID_CHAT,
    SCENE_WORK_CODE: AGENT_ID_CODE,
    SCENE_WORK_TRANSLATE: AGENT_ID_TRANSLATE,
    SCENE_EXEC_INTENT: AGENT_ID_INTENT,
    SCENE_VISION_UI: AGENT_ID_INTENT,
}

_SCENE_DEFAULT_RISK_MODE: dict[str, str] = {
    SCENE_CHAT: RISK_MODE_SAFE,
    SCENE_WORK_CODE: RISK_MODE_GUARDED,
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


def _resolve_supervisor_system_prompt() -> str:
    templates = _load_template_mapping("supervisor_dispatch_handler.prompt_templates")
    return _mapping_text(
        templates,
        "system_prompt",
    )


def _build_supervisor_user_prompt(scene_type: str, user_input: str) -> str:
    templates = _load_template_mapping("supervisor_dispatch_handler.prompt_templates")
    return _mapping_text(
        templates,
        "user_prompt_template",
        scene_type=scene_type,
        user_input=user_input,
    )


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
    ctx = dict(context or {})
    node_id = str(ctx.get("node_id") or "supervisor_dispatch").strip()
    scene_type = str(payload.get("scene_type") or SCENE_CHAT).strip().upper() or SCENE_CHAT

    raw_mode = str(os.environ.get("SUPERVISOR_MODE") or "rule").strip().lower()
    supervisor_mode = "llm" if raw_mode == "llm" else "rule"
    use_llm_proposal = supervisor_mode == "llm"

    allowlist_raw = ctx.get("agent_dispatch_allowlist")
    allowlist: list[str] | None = None
    if isinstance(allowlist_raw, (list, tuple)):
        allowlist = [str(item).strip() for item in allowlist_raw if str(item).strip()]

    rule_agent = _SCENE_DEFAULT_AGENT.get(scene_type, AGENT_ID_CHAT)
    rule_risk_mode = _SCENE_DEFAULT_RISK_MODE.get(scene_type, RISK_MODE_SAFE)
    rule_need_judge = _SCENE_NEED_JUDGE.get(scene_type, False)

    inferred_emotion = payload.get("inferred_emotion") or {}
    needs_deep = bool(payload.get("needs_deep_emotion_analysis")) if isinstance(inferred_emotion, dict) else False
    if needs_deep and rule_agent == AGENT_ID_CHAT:
        rule_agent = AGENT_ID_EMOTION
        rule_need_judge = False

    selected_agent = rule_agent
    risk_mode = rule_risk_mode
    need_judge = rule_need_judge
    decision_source = SUPERVISOR_DECISION_SOURCE_RULE
    supervisor_failure_code: str | None = None

    if use_llm_proposal:
        llm_proposal, failure_code = _try_llm_proposal(payload=payload, scene_type=scene_type, context=ctx)
        if llm_proposal is None:
            selected_agent = rule_agent
            risk_mode = rule_risk_mode
            need_judge = rule_need_judge
            decision_source = SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
            supervisor_failure_code = failure_code or SUPERVISOR_FAILURE_LLM_INTERNAL_ERROR
        else:
            raw_rm = str(llm_proposal.get("risk_mode") or "").strip().lower()
            proposal_to_validate = {
                "selected_agent": str(llm_proposal.get("selected_agent") or "").strip(),
                "risk_mode": raw_rm if raw_rm else rule_risk_mode,
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
