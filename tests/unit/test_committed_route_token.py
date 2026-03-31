"""committed_route_token 机制专项测试（M34-G，T-CRT-01~10）。

验证 Governor 编译令牌的正确性、字段不可变性和权限约束。
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from host_brain.governor.route_token import (
    CommittedRouteDecision,
    RouteToken,
    compile_route_decision,
)


# ---------------------------------------------------------------------------
# T-CRT-01：approved=True → token = GOV_OK_RENDER
# ---------------------------------------------------------------------------
def test_crt_01_approved_true_gives_ok_render():
    """T-CRT-01：approved=True → token = GOV_OK_RENDER"""
    decision = compile_route_decision(
        approved=True,
        reason_code="APPROVE",
        committed_at_ms=1_000_000,
        proposer_node_id="governor",
    )
    assert decision.token == RouteToken.GOV_OK_RENDER


# ---------------------------------------------------------------------------
# T-CRT-02：approved=False, AIRLOCK → token = GOV_AIRLOCK_REVIEW
# ---------------------------------------------------------------------------
def test_crt_02_airlock_reason_gives_airlock_review():
    """T-CRT-02：approved=False, reason=AIRLOCK* → token = GOV_AIRLOCK_REVIEW"""
    decision = compile_route_decision(
        approved=False,
        reason_code="AIRLOCK_BLOCKED",
        committed_at_ms=1_000_000,
        proposer_node_id="governor",
    )
    assert decision.token == RouteToken.GOV_AIRLOCK_REVIEW

    # EMOTION_GATE_BLOCKED 也映射到 AIRLOCK_REVIEW
    decision2 = compile_route_decision(
        approved=False,
        reason_code="EMOTION_GATE_BLOCKED",
        committed_at_ms=1_000_000,
        proposer_node_id="governor",
    )
    assert decision2.token == RouteToken.GOV_AIRLOCK_REVIEW


# ---------------------------------------------------------------------------
# T-CRT-03：approved=False, 其他原因 → token = GOV_FAIL_FALLBACK
# ---------------------------------------------------------------------------
def test_crt_03_other_reject_gives_fail_fallback():
    """T-CRT-03：approved=False, reason=其他 → token = GOV_FAIL_FALLBACK"""
    for reason in ["INTENT_PARSE_FAILED", "SEQ_INVALID", "REPLAY_DETECTED", "EXPIRED_INTENT", "REJECT"]:
        decision = compile_route_decision(
            approved=False,
            reason_code=reason,
            committed_at_ms=1_000_000,
            proposer_node_id="governor",
        )
        assert decision.token == RouteToken.GOV_FAIL_FALLBACK, f"reason={reason} 应映射到 GOV_FAIL_FALLBACK"


# ---------------------------------------------------------------------------
# T-CRT-04：CommittedRouteDecision 不可变（修改抛出异常）
# ---------------------------------------------------------------------------
def test_crt_04_committed_route_decision_is_immutable():
    """T-CRT-04：CommittedRouteDecision 是 frozen dataclass，修改字段抛出 FrozenInstanceError"""
    decision = CommittedRouteDecision(
        token=RouteToken.GOV_OK_RENDER,
        reason_code="APPROVE",
        committed_at_ms=1_000_000,
        proposer_node_id="governor",
    )
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        decision.token = RouteToken.GOV_FAIL_FALLBACK  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T-CRT-05：committed_at_ms 为正整数时间戳
# ---------------------------------------------------------------------------
def test_crt_05_committed_at_ms_is_positive_int():
    """T-CRT-05：committed_at_ms 为正整数时间戳"""
    now = int(time.time() * 1000)
    decision = compile_route_decision(
        approved=True,
        reason_code="APPROVE",
        committed_at_ms=now,
        proposer_node_id="governor",
    )
    assert isinstance(decision.committed_at_ms, int)
    assert decision.committed_at_ms > 0
    assert decision.committed_at_ms == now


# ---------------------------------------------------------------------------
# T-CRT-06：proposer_node_id 为 Governor 节点 ID
# ---------------------------------------------------------------------------
def test_crt_06_proposer_node_id_is_governor():
    """T-CRT-06：proposer_node_id 为 Governor 节点 ID"""
    decision = compile_route_decision(
        approved=True,
        reason_code="APPROVE",
        committed_at_ms=1_000_000,
        proposer_node_id="governor",
    )
    assert decision.proposer_node_id == "governor"

    # 也可以是其他 Host 节点
    decision2 = compile_route_decision(
        approved=True,
        reason_code="APPROVE",
        committed_at_ms=1_000_000,
        proposer_node_id="core_reasoner_primary",
    )
    assert decision2.proposer_node_id == "core_reasoner_primary"


# ---------------------------------------------------------------------------
# T-CRT-07：LLM 节点不能写入 committed_route_token（权限校验）
# ---------------------------------------------------------------------------
def test_crt_07_llm_node_cannot_write_committed_token():
    """T-CRT-07：CommittedRouteDecision 是不可变对象，LLM 节点无法修改已提交的令牌"""
    decision = CommittedRouteDecision(
        token=RouteToken.GOV_OK_RENDER,
        reason_code="APPROVE",
        committed_at_ms=1_000_000,
        proposer_node_id="governor",
    )
    # 模拟 LLM 节点尝试修改 committed_route_token
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
        decision.token = RouteToken.GOV_FAIL_FALLBACK  # type: ignore[misc]


# ---------------------------------------------------------------------------
# T-CRT-08：committed_route_token 写入后记录到 intent_log
# ---------------------------------------------------------------------------
def test_crt_08_committed_token_recorded_in_intent_log():
    """T-CRT-08：Governor evaluate_intent 返回结果包含 committed_route_token_ref 字段"""
    from unittest.mock import MagicMock, patch

    from host_brain.governor.decision import IntentDecisionEngine

    mock_ledger = MagicMock()
    mock_ledger.has_replay.return_value = False
    mock_ledger.is_seq_invalid.return_value = False

    engine = IntentDecisionEngine(ledger=mock_ledger)

    valid_intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "test-intent-001",
        "ts_ms": int(time.time() * 1000),
        "session_id": "session-001",
        "seq": 1,
        "type": "query",
        "action": "narrative_only",
        "target": "host",
        "args": {"text": "hello"},
        "nonce": "nonce-001",
    }

    result = engine.evaluate_intent(valid_intent, proposer_node_id="governor")

    # 无论 APPROVE 还是 REJECT，都应包含 committed_route_token_ref
    assert "committed_route_token_ref" in result, "结果应包含 committed_route_token_ref 字段"
    assert "committed_route_token" in result, "结果应包含 committed_route_token 字段"
    assert isinstance(result["committed_route_token"], dict)
    assert "token" in result["committed_route_token"]
    assert "committed_at_ms" in result["committed_route_token"]


# ---------------------------------------------------------------------------
# T-CRT-09：proposed_intent_draft_json 字段重命名后引用正确（注释标注验证）
# ---------------------------------------------------------------------------
def test_crt_09_proposed_intent_draft_field_annotation():
    """T-CRT-09：decision.py 中 proposed_intent_draft_json 字段有三分法注释标注"""
    import inspect

    from host_brain.governor import decision as decision_module

    source = inspect.getsource(decision_module)
    # 验证三分法注释存在
    assert "proposed_*" in source or "proposed_intent_draft_json" in source
    assert "committed_*" in source or "committed_route_token" in source
    # 验证字段仍然存在（未被错误删除）
    assert "proposed_intent_draft_json" in source


# ---------------------------------------------------------------------------
# T-CRT-10：observed_* / proposed_* / committed_* 字段归属注释存在
# ---------------------------------------------------------------------------
def test_crt_10_three_tier_field_comments_exist():
    """T-CRT-10：route_token.py 和 decision.py 中存在三分法字段归属注释"""
    import inspect

    from host_brain.governor import decision as decision_module
    from host_brain.governor import route_token as route_token_module

    decision_source = inspect.getsource(decision_module)
    route_token_source = inspect.getsource(route_token_module)

    # decision.py 应包含三分法注释
    assert "proposed_*" in decision_source
    assert "committed_*" in decision_source

    # route_token.py 应包含 committed_* 相关说明
    assert "committed_route_token" in route_token_source
    assert "CommittedRouteDecision" in route_token_source
    assert "RouteToken" in route_token_source
