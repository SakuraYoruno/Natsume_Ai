"""P7-2：失败收口矩阵与契约一致性断言（MULTI_AGENT_P7_SCOPE §3.3）。"""

from __future__ import annotations

from pathlib import Path

from host_brain.core.errors import ORCH_TIMEOUT
from host_brain.integrations.graph_contract import (
    BUDGET_BREACH_FAILURE_CODES,
    CLOCK_EVENT_EXEC_TIMEOUT,
    CLOCK_EVENT_POLICY_TIMEOUT,
    LOCAL_LOOP_FAILURE_CODES,
    SUBGRAPH_FAILURE_KEYS,
    SUPERVISOR_PROPOSAL_FAILURE_CODES,
)


def test_failure_matrix_doc_exists() -> None:
    """P7-2 失败收口矩阵文档存在。"""
    root = Path(__file__).resolve().parents[2]
    path = root / "docs" / "50_project" / "MULTI_AGENT_P7_2_FAILURE_MATRIX_2026-03.md"
    assert path.is_file(), "MULTI_AGENT_P7_2_FAILURE_MATRIX_2026-03.md 须存在"


def test_matrix_referenced_codes_in_contract() -> None:
    """矩阵中引用的失败码均在 graph_contract / core.errors 中定义。"""
    assert ORCH_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert CLOCK_EVENT_POLICY_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert CLOCK_EVENT_EXEC_TIMEOUT in BUDGET_BREACH_FAILURE_CODES
    assert len(SUPERVISOR_PROPOSAL_FAILURE_CODES) >= 5
    assert "failed_node" in SUBGRAPH_FAILURE_KEYS
    assert "error_code" in SUBGRAPH_FAILURE_KEYS
    assert "LOCAL_LOOP_MAX_STEPS" in LOCAL_LOOP_FAILURE_CODES
    assert "LOCAL_LOOP_TIMEOUT" in LOCAL_LOOP_FAILURE_CODES
    assert "LOCAL_LOOP_INTERNAL_ERROR" in LOCAL_LOOP_FAILURE_CODES
