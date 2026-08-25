"""P7-1：预算契约与 budget breach 失败码单测（MULTI_AGENT_P7_SCOPE §3.2）。"""

from __future__ import annotations

from host_brain.integrations.graph_contract import (
    BUDGET_BREACH_FAILURE_CODES,
    BUDGET_CONFIG_GRAPH_TIMEOUT_MS,
    BUDGET_CONFIG_NODE_TIMEOUT_MS,
    BUDGET_LEVEL_GRAPH,
    BUDGET_LEVEL_NODE,
    BUDGET_LEVELS,
    BUDGET_LEVEL_SUBGRAPH,
    CLOCK_EVENT_EXEC_TIMEOUT,
    CLOCK_EVENT_POLICY_TIMEOUT,
    LOCAL_LOOP_FAILURE_MAX_STEPS,
    LOCAL_LOOP_FAILURE_TIMEOUT,
    LOCAL_LOOP_CONFIG_MAX_RETRY,
    LOCAL_LOOP_CONFIG_MAX_STEPS,
    LOCAL_LOOP_CONFIG_TIMEOUT_MS,
    SUPERVISOR_FAILURE_LLM_TIMEOUT,
    is_budget_breach_failure_code,
)
from host_brain.core.errors import ORCH_TIMEOUT


def test_budget_levels_constants() -> None:
    """预算层级常量为 graph / subgraph / node。"""
    assert BUDGET_LEVEL_GRAPH == "graph"
    assert BUDGET_LEVEL_SUBGRAPH == "subgraph"
    assert BUDGET_LEVEL_NODE == "node"
    assert set(BUDGET_LEVELS) == {"graph", "subgraph", "node"}


def test_budget_config_keys_match_existing() -> None:
    """预算配置键名与现有 contract 一致。"""
    assert BUDGET_CONFIG_GRAPH_TIMEOUT_MS == "policy_timeout_ms"
    assert BUDGET_CONFIG_NODE_TIMEOUT_MS == "timeout_ms"
    assert LOCAL_LOOP_CONFIG_TIMEOUT_MS == "local_loop_timeout_ms"
    assert LOCAL_LOOP_CONFIG_MAX_STEPS == "max_local_steps"
    assert LOCAL_LOOP_CONFIG_MAX_RETRY == "max_retry_count"


def test_budget_breach_failure_codes_contains_expected() -> None:
    """BUDGET_BREACH_FAILURE_CODES 包含图级/子图级/节点级/Supervisor 超时与步数突破。"""
    assert ORCH_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert CLOCK_EVENT_POLICY_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert CLOCK_EVENT_EXEC_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert LOCAL_LOOP_FAILURE_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert LOCAL_LOOP_FAILURE_MAX_STEPS in BUDGET_BREACH_FAILURE_CODES
    assert SUPERVISOR_FAILURE_LLM_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert len(BUDGET_BREACH_FAILURE_CODES) == 6


def test_is_budget_breach_failure_code() -> None:
    """is_budget_breach_failure_code 对预算突破码返回 True，其它返回 False。"""
    assert is_budget_breach_failure_code(LOCAL_LOOP_FAILURE_TIMEOUT) is True
    assert is_budget_breach_failure_code(ORCH_TIMEOUT) is True
    assert is_budget_breach_failure_code(SUPERVISOR_FAILURE_LLM_TIMEOUT) is True
    assert is_budget_breach_failure_code("LOCAL_LOOP_INTERNAL_ERROR") is False
    assert is_budget_breach_failure_code("LLM_BAD_JSON") is False
    assert is_budget_breach_failure_code(None) is False
    assert is_budget_breach_failure_code("") is False
