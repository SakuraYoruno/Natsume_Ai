"""P5-1 契约先行：Supervisor proposal schema、失败码、allowlist 校验。P5-2 handler 观测与回退。"""

from __future__ import annotations

import pytest

from host_brain.integrations.graph_contract import (
    SUPERVISOR_DECISION_SOURCE_LLM,
    SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE,
    SUPERVISOR_DECISION_SOURCE_RULE,
    SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED,
    SUPERVISOR_FAILURE_LLM_BAD_JSON,
    SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID,
    SUPERVISOR_PROPOSAL_FAILURE_CODES,
    SUPERVISOR_PROPOSAL_FORBIDDEN_KEYS,
    SUPERVISOR_PROPOSAL_REQUIRED_KEYS,
    SUPERVISOR_PROPOSAL_VALID_RISK_MODES,
    validate_supervisor_proposal_for_graph,
)


def test_proposal_required_keys() -> None:
    assert "selected_agent" in SUPERVISOR_PROPOSAL_REQUIRED_KEYS


def test_proposal_forbidden_keys_fixed() -> None:
    assert "__next_node__" in SUPERVISOR_PROPOSAL_FORBIDDEN_KEYS
    assert "committed_route_token" in SUPERVISOR_PROPOSAL_FORBIDDEN_KEYS
    assert "active_agent_id" in SUPERVISOR_PROPOSAL_FORBIDDEN_KEYS


def test_failure_codes_fixed() -> None:
    assert SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED in SUPERVISOR_PROPOSAL_FAILURE_CODES
    assert "LLM_TIMEOUT" in SUPERVISOR_PROPOSAL_FAILURE_CODES
    assert "LLM_BAD_JSON" in SUPERVISOR_PROPOSAL_FAILURE_CODES
    assert "LLM_SCHEMA_INVALID" in SUPERVISOR_PROPOSAL_FAILURE_CODES
    assert "LLM_INTERNAL_ERROR" in SUPERVISOR_PROPOSAL_FAILURE_CODES


def test_valid_risk_modes() -> None:
    assert SUPERVISOR_PROPOSAL_VALID_RISK_MODES == frozenset({"safe", "guarded", "strict"})


def test_decision_sources() -> None:
    assert SUPERVISOR_DECISION_SOURCE_RULE == "rule"
    assert SUPERVISOR_DECISION_SOURCE_LLM == "llm"
    assert SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE == "llm_fallback_rule"


def test_validate_proposal_empty_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph({}, ["intent_agent", "translate_agent"])
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_missing_selected_agent_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"reason": "test", "risk_mode": "safe"}, ["intent_agent"]
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_forbidden_key_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "intent_agent", "__next_node__": "x"}, ["intent_agent"]
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_agent_not_in_allowlist_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "emotion_agent", "risk_mode": "safe"},
        ["intent_agent", "translate_agent"],
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED


def test_validate_proposal_invalid_risk_mode_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "intent_agent", "risk_mode": "invalid_mode"},
        ["intent_agent"],
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_valid_passes() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "intent_agent", "reason": "用户请求代码", "need_judge": True, "risk_mode": "strict"},
        ["intent_agent", "translate_agent"],
    )
    assert valid
    assert code is None


def test_validate_proposal_empty_allowlist_allows_any_agent() -> None:
    """allowlist 为空时视为不限制（向后兼容或测试）。"""
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "intent_agent", "risk_mode": "strict"}, []
    )
    assert valid
    assert code is None


# ---------------------------------------------------------------------------
# P5-2：supervisor_dispatch_handler rule/llm 双模与观测字段
# ---------------------------------------------------------------------------

def test_supervisor_handler_rule_path_writes_observability_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUPERVISOR_MODE=rule 时写出 supervisor_mode / supervisor_decision_source / supervisor_selected_agent。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "rule")
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler

    payload = {"scene_type": "WORK_CODE", "request_id": "p5-test"}
    ctx = {"request_id": "p5-test", "node_id": "supervisor_dispatch"}
    out = supervisor_dispatch_handler(payload, ctx)
    sd = out.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_mode") == "rule"
    assert sd.get("supervisor_decision_source") == "scene_type:WORK_CODE:rule_fallback"
    assert sd.get("supervisor_selected_agent") == "intent_agent"
    assert sd.get("supervisor_failure_code") is None
    assert out.get("active_agent_id") == "intent_agent"
    assert sd.get("need_judge") is True


def test_supervisor_handler_llm_fallback_when_no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUPERVISOR_MODE=llm 现在只作观测，缺少 intent_draft 时仍走 scene_type fallback。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "llm")
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler

    payload = {"scene_type": "WORK_CODE", "request_id": "p5-test", "user_input": "写代码"}
    ctx = {"request_id": "p5-test", "node_id": "supervisor_dispatch"}  # 无 gateway
    out = supervisor_dispatch_handler(payload, ctx)
    sd = out.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_mode") == "llm"
    assert sd.get("supervisor_decision_source") == "scene_type:WORK_CODE:rule_fallback"
    assert out.get("active_agent_id") == "intent_agent"


def test_supervisor_handler_uses_graph_allowlist_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    """context 提供 agent_dispatch_allowlist 时，与 agent_dispatch 使用同一 allowlist。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "rule")
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler

    payload = {"scene_type": "WORK_CODE", "request_id": "p5-test"}
    ctx = {
        "request_id": "p5-test",
        "node_id": "supervisor_dispatch",
        "agent_dispatch_allowlist": ["intent_agent", "translate_agent"],
    }
    out = supervisor_dispatch_handler(payload, ctx)
    assert out.get("active_agent_id") == "intent_agent"


def test_supervisor_handler_consumes_llm_route_from_intent_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUPERVISOR_MODE=llm 时直接复用 thalamus 已编好的 llm route。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "llm")
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler

    payload = {
        "scene_type": "WORK_CODE",
        "request_id": "p5-test",
        "intent_draft": {
            "route": "skill",
            "target_id": "codex_codegen",
            "route_source": "llm",
        },
    }
    ctx = {
        "request_id": "p5-test",
        "node_id": "supervisor_dispatch",
        "agent_dispatch_allowlist": ["intent_agent", "translate_agent"],
    }
    out = supervisor_dispatch_handler(payload, ctx)
    sd = out.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == "intent_draft:skill:llm"
    assert sd.get("intent_target_id") == "codex_codegen"
    assert out.get("active_agent_id") == "intent_agent"
