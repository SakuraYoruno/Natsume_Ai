"""P5-5：Supervisor rule/llm 双模集成 smoke。

第一组：成功路径（mock LLM 返回合法 proposal）— WORK_CODE/WORK_TRANSLATE/AUTO + decision_source=llm。
第二组：回退路径（无 gateway / bad json / schema invalid / agent not allowed）— decision_source=llm_fallback_rule，failure_code 对应。
"""

from __future__ import annotations

import pytest

from host_brain.app.execution_profile import PROFILE_WORKFLOW_SUPERVISED
from host_brain.app.graph_config_builder import (
    JUDGE_AGENT_NODE_ID,
    build_supervised_graph,
)
from host_brain.integrations.graph_contract import (
    SUPERVISOR_DECISION_SOURCE_LLM,
    SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE,
    SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED,
    SUPERVISOR_FAILURE_LLM_BAD_JSON,
    SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID,
)


def _run_supervised_graph_with_supervisor_mock(
    monkeypatch: pytest.MonkeyPatch,
    *,
    llm_returns: tuple[dict | None, str | None],
    scene_type: str = "WORK_CODE",
) -> dict:
    """运行 Supervised 图一次，supervisor_dispatch 的 _try_llm_proposal 由调用方注入。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "llm")
    from host_brain.app.dependencies import get_graph_runtime
    from host_brain.app.handlers import supervisor_dispatch_handler as mod

    monkeypatch.setattr(mod, "_try_llm_proposal", lambda *a, **k: llm_returns)

    runtime = get_graph_runtime()
    request_id = "p5-5-smoke-001"
    payload = {
        "request_id": request_id,
        "scene_type": scene_type,
        "execution_profile": PROFILE_WORKFLOW_SUPERVISED,
        "committed_route_token": "GOV_OK_RENDER",
        "message": "写一段代码",
        "user_input": "写一段 Python",
    }
    config = build_supervised_graph(
        provider_name="mock",
        model_id="mock",
        timeout_ms=5000,
        available=True,
        api_key=None,
        provider_fn=lambda _: None,
    )
    result = runtime.run_graph(
        request_id=request_id,
        graph_config=config,
        payload=payload,
        dependency_statuses={"mock": {"available": True, "detail": None}},
        timeout_ms=15_000,
    )
    return dict(result.output or {})


def test_p5_success_path_llm_proposal_work_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功路径：WORK_CODE + llm 模式，mock 返回合法 proposal → decision_source=llm，仍在 WORKFLOW_SUPERVISED。"""
    from tests.unit.conftest import mock_llm_runtime
    mock_llm_runtime(monkeypatch)
    monkeypatch.setenv("HOST_LLM_PROVIDER", "mock")

    valid_proposal = {"selected_agent": "code_agent", "reason": "用户请求代码", "risk_mode": "guarded", "need_judge": True}
    out = _run_supervised_graph_with_supervisor_mock(monkeypatch, llm_returns=(valid_proposal, None), scene_type="WORK_CODE")

    pl = out.get("payload") or {}
    assert pl.get("execution_profile") == PROFILE_WORKFLOW_SUPERVISED
    sd = pl.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == SUPERVISOR_DECISION_SOURCE_LLM
    assert sd.get("supervisor_selected_agent") == "code_agent"
    assert sd.get("supervisor_failure_code") is None
    trace = out.get("graph_trace") or []
    node_ids = [t.get("node_id") for t in trace if t.get("node_id")]
    assert "supervisor_dispatch" in node_ids
    assert JUDGE_AGENT_NODE_ID in node_ids or "judge_agent" in node_ids


def test_p5_fallback_no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退路径：无 gateway → llm_fallback_rule，failure_code=LLM_INTERNAL_ERROR，仍 WORKFLOW_SUPERVISED。"""
    from tests.unit.conftest import mock_llm_runtime
    mock_llm_runtime(monkeypatch)

    out = _run_supervised_graph_with_supervisor_mock(
        monkeypatch, llm_returns=(None, "LLM_INTERNAL_ERROR"), scene_type="WORK_CODE"
    )
    pl = out.get("payload") or {}
    assert pl.get("execution_profile") == PROFILE_WORKFLOW_SUPERVISED
    sd = pl.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
    assert sd.get("supervisor_failure_code") == "LLM_INTERNAL_ERROR"
    assert pl.get("active_agent_id") == "code_agent"  # rule 默认


def test_p5_fallback_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退路径：bad json → llm_fallback_rule，failure_code=LLM_BAD_JSON。"""
    from tests.unit.conftest import mock_llm_runtime
    mock_llm_runtime(monkeypatch)

    out = _run_supervised_graph_with_supervisor_mock(
        monkeypatch, llm_returns=(None, SUPERVISOR_FAILURE_LLM_BAD_JSON), scene_type="WORK_TRANSLATE"
    )
    pl = out.get("payload") or {}
    assert pl.get("execution_profile") == PROFILE_WORKFLOW_SUPERVISED
    sd = pl.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
    assert sd.get("supervisor_failure_code") == SUPERVISOR_FAILURE_LLM_BAD_JSON
    assert pl.get("active_agent_id") == "translate_agent"  # rule 默认 WORK_TRANSLATE


def test_p5_fallback_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退路径：proposal 非法 risk_mode → llm_fallback_rule，failure_code=LLM_SCHEMA_INVALID。"""
    from tests.unit.conftest import mock_llm_runtime
    mock_llm_runtime(monkeypatch)

    invalid_proposal = {"selected_agent": "code_agent", "risk_mode": "invalid_mode"}
    out = _run_supervised_graph_with_supervisor_mock(
        monkeypatch, llm_returns=(invalid_proposal, None), scene_type="WORK_CODE"
    )
    sd = (out.get("payload") or {}).get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
    assert sd.get("supervisor_failure_code") == SUPERVISOR_FAILURE_LLM_SCHEMA_INVALID


def test_p5_fallback_agent_not_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """回退路径：selected_agent 不在 allowlist → llm_fallback_rule，failure_code=LLM_AGENT_NOT_ALLOWED。"""
    from tests.unit.conftest import mock_llm_runtime
    mock_llm_runtime(monkeypatch)

    # safety_agent 不在 _dispatch_allowlist 中，用于测试 AGENT_NOT_ALLOWED 路径
    proposal_not_in_allowlist = {"selected_agent": "safety_agent", "risk_mode": "strict", "need_judge": True}
    out = _run_supervised_graph_with_supervisor_mock(
        monkeypatch, llm_returns=(proposal_not_in_allowlist, None), scene_type="WORK_CODE"
    )
    sd = (out.get("payload") or {}).get("supervisor_dispatch") or {}
    assert sd.get("supervisor_decision_source") == SUPERVISOR_DECISION_SOURCE_LLM_FALLBACK_RULE
    assert sd.get("supervisor_failure_code") == SUPERVISOR_FAILURE_LLM_AGENT_NOT_ALLOWED
    assert (out.get("payload") or {}).get("active_agent_id") == "code_agent"  # rule 默认


def test_p5_rule_mode_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """SUPERVISOR_MODE=rule 时行为与基线一致，decision_source=rule。"""
    monkeypatch.setenv("SUPERVISOR_MODE", "rule")
    from tests.unit.conftest import mock_llm_runtime
    mock_llm_runtime(monkeypatch)
    from host_brain.app.dependencies import get_graph_runtime
    from host_brain.app.graph_config_builder import build_supervised_graph

    runtime = get_graph_runtime()
    payload = {
        "request_id": "p5-5-rule",
        "scene_type": "WORK_CODE",
        "execution_profile": PROFILE_WORKFLOW_SUPERVISED,
        "committed_route_token": "GOV_OK_RENDER",
    }
    config = build_supervised_graph(
        provider_name="mock", model_id="mock", timeout_ms=5000, available=True, api_key=None, provider_fn=lambda _: None,
    )
    result = runtime.run_graph(
        request_id="p5-5-rule",
        graph_config=config,
        payload=payload,
        dependency_statuses={"mock": {"available": True, "detail": None}},
        timeout_ms=15_000,
    )
    pl = (result.output or {}).get("payload") or {}
    sd = pl.get("supervisor_dispatch") or {}
    assert sd.get("supervisor_mode") == "rule"
    assert sd.get("supervisor_decision_source") == "rule"
    assert pl.get("execution_profile") == PROFILE_WORKFLOW_SUPERVISED
