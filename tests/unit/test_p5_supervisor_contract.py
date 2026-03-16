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
    valid, code = validate_supervisor_proposal_for_graph({}, ["code_agent", "translate_agent"])
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_missing_selected_agent_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"reason": "test", "risk_mode": "safe"}, ["code_agent"]
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_forbidden_key_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "code_agent", "__next_node__": "x"}, ["code_agent"]
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_agent_not_in_allowlist_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "intent_agent", "risk_mode": "strict"},
        ["code_agent", "translate_agent"],
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED


def test_validate_proposal_invalid_risk_mode_fails() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "code_agent", "risk_mode": "invalid_mode"},
        ["code_agent"],
    )
    assert not valid
    assert code == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_validate_proposal_valid_passes() -> None:
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "code_agent", "reason": "用户请求代码", "need_judge": True, "risk_mode": "guarded"},
        ["code_agent", "translate_agent"],
    )
    assert valid
    assert code is None


def test_validate_proposal_empty_allowlist_allows_any_agent() -> None:
    """allowlist 为空时视为不限制（向后兼容或测试）。"""
    valid, code = validate_supervisor_proposal_for_graph(
        {"selected_agent": "code_agent", "risk_mode": "safe"}, []
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
    assert sd.get("supervisor_decision_source") == "rule"
    assert sd.get("supervisor_selected_agent") == "code_agent"
    assert sd.get("supervisor_failure_code") is None
    assert out.get("active_agent_id") == "code_agent"
    assert sd.get("need_judge") is True


def test_supervisor_handler_llm_fallback_when_no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUPERVISOR_MODE=llm 且无 gateway 时统一回退 rule，写出 llm_fallback_rule 与 failure_code。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "llm")
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler

    payload = {"scene_type": "WORK_CODE", "request_id": "p5-test", "user_input": "写代码"}
    ctx = {"request_id": "p5-test", "node_id": "supervisor_dispatch"}  # 无 gateway
    out = supervisor_dispatch_handler(payload, ctx)
    sd = out.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_mode") == "llm"
    assert sd.get("supervisor_decision_source") == "llm_fallback_rule"
    assert sd.get("supervisor_failure_code") == "LLM_INTERNAL_ERROR"
    assert out.get("active_agent_id") == "code_agent"  # rule 默认


def test_supervisor_handler_uses_graph_allowlist_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    """context 提供 agent_dispatch_allowlist 时，与 agent_dispatch 使用同一 allowlist。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "rule")
    from host_brain.app.handlers.supervisor_dispatch_handler import supervisor_dispatch_handler

    payload = {"scene_type": "WORK_CODE", "request_id": "p5-test"}
    ctx = {
        "request_id": "p5-test",
        "node_id": "supervisor_dispatch",
        "agent_dispatch_allowlist": ["code_agent", "translate_agent"],
    }
    out = supervisor_dispatch_handler(payload, ctx)
    assert out.get("active_agent_id") == "code_agent"


def test_supervisor_handler_invalid_risk_mode_fallback_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式非法 risk_mode 判 LLM_SCHEMA_INVALID，回退到 rule（缺失才补默认，非法不补）。"""
    from host_brain.integrations.graph_contract import SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID

    monkeypatch.setenv("SUPERVISOR_MODE", "llm")
    from host_brain.app.handlers import supervisor_dispatch_handler as mod

    def _fake_llm_proposal(*args: object, **kwargs: object) -> tuple[dict[str, object], None]:
        return ({"selected_agent": "code_agent", "risk_mode": "invalid_mode"}, None)

    monkeypatch.setattr(mod, "_try_llm_proposal", _fake_llm_proposal)
    payload = {"scene_type": "WORK_CODE", "request_id": "p5-test"}
    ctx = {
        "request_id": "p5-test",
        "node_id": "supervisor_dispatch",
        "agent_dispatch_allowlist": ["code_agent", "translate_agent"],
    }
    out = mod.supervisor_dispatch_handler(payload, ctx)
    sd = out.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == "llm_fallback_rule"
    assert sd.get("supervisor_failure_code") == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID
    assert out.get("active_agent_id") == "code_agent"  # rule 默认 WORK_CODE → code_agent
