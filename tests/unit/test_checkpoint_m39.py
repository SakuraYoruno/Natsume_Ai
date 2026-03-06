"""Checkpoint / Interrupt / Resume 核心专项测试（M39-I，T-CKP-01~12）。

验证双时钟模型下断点续跑的正确性。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from host_brain.integrations.graph_checkpoint import (
    CheckpointManager,
    CheckpointStore,
    HostPolicyGuard,
    InterruptSignal,
    ResumeDecision,
    ResumeRequest,
    SyntheticTimeoutEvent,
)
from host_brain.integrations.graph_contract import (
    INTERRUPT_REASON_AIRLOCK,
    INTERRUPT_REASON_HUMAN_REVIEW,
    INTERRUPT_REASON_POLICY_TIMEOUT,
    RESUME_DENIED_DEADLINE_EXCEEDED,
    RESUME_DENIED_POLICY_EXPIRED,
    SYNTHETIC_EVENT_TIMEOUT,
    STATUS_OK,
)


def _make_state(**kwargs: Any) -> dict[str, Any]:
    return {
        "request_id": "req-ckp-001",
        "graph_id": "graph-ckp-001",
        "status": STATUS_OK,
        "payload": {"key": "value"},
        "trace": [],
        "pending_node_id": "node_a",
        "policy_start_ms": int(time.time() * 1000),
        "policy_timeout_ms": 30_000,
        **kwargs,
    }


# ---------------------------------------------------------------------------
# T-CKP-01：CheckpointStore.save() 返回唯一 checkpoint_id
# ---------------------------------------------------------------------------
def test_ckp_01_save_returns_unique_checkpoint_id():
    """T-CKP-01：CheckpointStore.save() 返回唯一 checkpoint_id"""
    store = CheckpointStore()
    state = _make_state()
    checkpoint_id = store.save(state)
    assert isinstance(checkpoint_id, str)
    assert len(checkpoint_id) > 0


# ---------------------------------------------------------------------------
# T-CKP-02：CheckpointStore.load() 返回保存的 GraphState
# ---------------------------------------------------------------------------
def test_ckp_02_load_returns_saved_state():
    """T-CKP-02：CheckpointStore.load() 返回保存的 GraphState"""
    store = CheckpointStore()
    state = _make_state(payload={"test_key": "test_value"})
    checkpoint_id = store.save(state)
    loaded = store.load(checkpoint_id)
    assert loaded is not None
    assert loaded["payload"]["test_key"] == "test_value"
    assert loaded["request_id"] == "req-ckp-001"


# ---------------------------------------------------------------------------
# T-CKP-03：CheckpointStore.load() 在 checkpoint_id 不存在时返回 None
# ---------------------------------------------------------------------------
def test_ckp_03_load_returns_none_for_nonexistent_id():
    """T-CKP-03：CheckpointStore.load() 在 checkpoint_id 不存在时返回 None"""
    store = CheckpointStore()
    result = store.load("nonexistent-checkpoint-id-12345")
    assert result is None


# ---------------------------------------------------------------------------
# T-CKP-04：HostPolicyGuard.check_resume_allowed() 在未超时时返回 allowed=True
# ---------------------------------------------------------------------------
def test_ckp_04_policy_guard_allows_resume_when_not_expired():
    """T-CKP-04：HostPolicyGuard.check_resume_allowed() 在未超时时返回 allowed=True"""
    guard = HostPolicyGuard()
    now = int(time.time() * 1000)
    signal = InterruptSignal(
        checkpoint_id="ckp-001",
        interrupt_reason=INTERRUPT_REASON_HUMAN_REVIEW,
        interrupted_at_ms=now,
        resume_deadline_at=now + 300_000,  # 5分钟后截止
    )
    decision = guard.check_resume_allowed(interrupt_signal=signal, now_ms=now)
    assert decision.allowed is True
    assert decision.synthetic_event is None


# ---------------------------------------------------------------------------
# T-CKP-05：HostPolicyGuard.check_resume_allowed() 在超过 resume_deadline_at 时返回 allowed=False
# ---------------------------------------------------------------------------
def test_ckp_05_policy_guard_denies_resume_when_deadline_exceeded():
    """T-CKP-05：HostPolicyGuard.check_resume_allowed() 在超过 resume_deadline_at 时返回 allowed=False"""
    guard = HostPolicyGuard()
    now = int(time.time() * 1000)
    signal = InterruptSignal(
        checkpoint_id="ckp-002",
        interrupt_reason=INTERRUPT_REASON_HUMAN_REVIEW,
        interrupted_at_ms=now - 600_000,  # 10分钟前中断
        resume_deadline_at=now - 300_000,  # 5分钟前截止（已过期）
    )
    decision = guard.check_resume_allowed(interrupt_signal=signal, now_ms=now)
    assert decision.allowed is False
    assert decision.synthetic_event is not None


# ---------------------------------------------------------------------------
# T-CKP-06：ResumeDecision.synthetic_event 在 allowed=False 时包含 TIMEOUT_SYNTHETIC 事件
# ---------------------------------------------------------------------------
def test_ckp_06_resume_decision_synthetic_event_on_denied():
    """T-CKP-06：ResumeDecision.synthetic_event 在 allowed=False 时包含 TIMEOUT_SYNTHETIC 事件"""
    guard = HostPolicyGuard()
    now = int(time.time() * 1000)
    signal = InterruptSignal(
        checkpoint_id="ckp-003",
        interrupt_reason=INTERRUPT_REASON_HUMAN_REVIEW,
        interrupted_at_ms=now - 600_000,
        resume_deadline_at=now - 1,  # 刚刚过期
    )
    decision = guard.check_resume_allowed(interrupt_signal=signal, now_ms=now)
    assert decision.allowed is False
    assert decision.synthetic_event is not None
    assert decision.synthetic_event["node_result"] == SYNTHETIC_EVENT_TIMEOUT
    assert decision.synthetic_event["error_code"] == RESUME_DENIED_DEADLINE_EXCEEDED


# ---------------------------------------------------------------------------
# T-CKP-07：SyntheticTimeoutEvent 包含 node_result=TIMEOUT_SYNTHETIC、error_code=DEADLINE_EXCEEDED、route_token=GOV_FALLBACK_SECONDARY
# ---------------------------------------------------------------------------
def test_ckp_07_synthetic_timeout_event_has_required_fields():
    """T-CKP-07：SyntheticTimeoutEvent 包含 node_result=TIMEOUT_SYNTHETIC、error_code=DEADLINE_EXCEEDED、route_token=GOV_FALLBACK_SECONDARY"""
    event = SyntheticTimeoutEvent(checkpoint_id="ckp-004")
    d = event.to_dict()
    assert d["node_result"] == SYNTHETIC_EVENT_TIMEOUT
    assert d["error_code"] == RESUME_DENIED_DEADLINE_EXCEEDED
    assert d["route_token"] == "GOV_FALLBACK_SECONDARY"
    assert d["checkpoint_id"] == "ckp-004"
    assert isinstance(d["ts_ms"], int)


# ---------------------------------------------------------------------------
# T-CKP-08：CheckpointManager.interrupt() 保存 checkpoint 并返回 InterruptSignal
# ---------------------------------------------------------------------------
def test_ckp_08_manager_interrupt_saves_checkpoint():
    """T-CKP-08：CheckpointManager.interrupt() 保存 checkpoint 并返回 InterruptSignal"""
    manager = CheckpointManager()
    state = _make_state()
    signal = manager.interrupt(state)
    assert isinstance(signal, InterruptSignal)
    assert signal.checkpoint_id != ""
    # 验证 checkpoint 可以加载
    loaded = manager.load_state(signal.checkpoint_id)
    assert loaded is not None


# ---------------------------------------------------------------------------
# T-CKP-09：CheckpointManager.interrupt() 写入 interrupt_reason 和 resume_deadline_at
# ---------------------------------------------------------------------------
def test_ckp_09_manager_interrupt_writes_reason_and_deadline():
    """T-CKP-09：CheckpointManager.interrupt() 写入 interrupt_reason 和 resume_deadline_at"""
    manager = CheckpointManager()
    state = _make_state()
    now = int(time.time() * 1000)
    signal = manager.interrupt(state, interrupt_reason=INTERRUPT_REASON_AIRLOCK)
    assert signal.interrupt_reason == INTERRUPT_REASON_AIRLOCK
    assert signal.resume_deadline_at > now
    assert signal.interrupted_at_ms >= now - 100  # 允许 100ms 误差


# ---------------------------------------------------------------------------
# T-CKP-10：CheckpointManager.resume() 在 allowed=True 时返回 ResumeDecision(allowed=True)
# ---------------------------------------------------------------------------
def test_ckp_10_manager_resume_returns_allowed_when_not_expired():
    """T-CKP-10：CheckpointManager.resume() 在 allowed=True 时返回 ResumeDecision(allowed=True)"""
    manager = CheckpointManager()
    state = _make_state()
    signal = manager.interrupt(state)
    # 立即恢复（未超时）
    decision = manager.resume(signal)
    assert decision.allowed is True
    assert decision.synthetic_event is None


# ---------------------------------------------------------------------------
# T-CKP-11：CheckpointManager.resume() 在 allowed=False 时返回 ResumeDecision(allowed=False, synthetic_event)
# ---------------------------------------------------------------------------
def test_ckp_11_manager_resume_returns_denied_when_expired():
    """T-CKP-11：CheckpointManager.resume() 在 allowed=False 时返回 ResumeDecision(allowed=False, synthetic_event)"""
    manager = CheckpointManager()
    state = _make_state()
    now = int(time.time() * 1000)
    # 构造已过期的 InterruptSignal
    signal = InterruptSignal(
        checkpoint_id="ckp-expired",
        interrupt_reason=INTERRUPT_REASON_HUMAN_REVIEW,
        interrupted_at_ms=now - 600_000,
        resume_deadline_at=now - 1,  # 已过期
    )
    decision = manager.resume(signal, now_ms=now)
    assert decision.allowed is False
    assert decision.synthetic_event is not None
    assert decision.synthetic_event["node_result"] == SYNTHETIC_EVENT_TIMEOUT


# ---------------------------------------------------------------------------
# T-CKP-12：两次 interrupt() 生成不同的 checkpoint_id（唯一性）
# ---------------------------------------------------------------------------
def test_ckp_12_interrupt_generates_unique_checkpoint_ids():
    """T-CKP-12：两次 interrupt() 生成不同的 checkpoint_id（唯一性）"""
    manager = CheckpointManager()
    state = _make_state()
    signal1 = manager.interrupt(state)
    signal2 = manager.interrupt(state)
    assert signal1.checkpoint_id != signal2.checkpoint_id
