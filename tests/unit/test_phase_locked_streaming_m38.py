"""Phase-Locked Streaming 核心专项测试（M38-J，T-STR-01~12）。

验证三类流的正确性和隔离约束。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from host_brain.integrations.graph_streaming import (
    CommitSnapshot,
    CommitStreamEmitter,
    PhaseLockGuard,
    PhaseViolationError,
    PresenceEvent,
    PresenceStreamEmitter,
    RenderEvent,
    RenderStreamEmitter,
    StreamPhase,
)
from host_brain.integrations.graph_contract import (
    STREAM_PHASE_PRESENCE,
    STREAM_PHASE_COMMIT,
    STREAM_PHASE_RENDER,
    STREAM_EVENT_NODE_START,
    STREAM_EVENT_NODE_END,
    STREAM_EVENT_RENDER_PRECONDITION_FAILED,
)


def _make_commit_snapshot(intent_commit: str = "test_intent") -> CommitSnapshot:
    return CommitSnapshot(
        intent_commit=intent_commit,
        s1_output_hash="abc123",
        locked_fields={"intent_commit": intent_commit},
        render_input_snapshot_ref="snapshot::req-001::1234567890",
        committed_at_ms=int(time.time() * 1000),
    )


# ---------------------------------------------------------------------------
# T-STR-01：PresenceStreamEmitter.emit() 返回 PresenceEvent，phase = PRESENCE
# ---------------------------------------------------------------------------
def test_str_01_presence_emitter_returns_presence_event():
    """T-STR-01：PresenceStreamEmitter.emit() 返回 PresenceEvent，phase = PRESENCE"""
    emitter = PresenceStreamEmitter()
    event = emitter.emit(node_id="n1", event_type=STREAM_EVENT_NODE_START)
    assert isinstance(event, PresenceEvent)
    assert event.phase == STREAM_PHASE_PRESENCE
    assert event.node_id == "n1"
    assert event.event_type == STREAM_EVENT_NODE_START


# ---------------------------------------------------------------------------
# T-STR-02：PresenceEvent 不包含 committed_* 字段（隔离约束）
# ---------------------------------------------------------------------------
def test_str_02_presence_event_no_committed_fields():
    """T-STR-02：PresenceEvent 不包含 committed_* 字段（隔离约束）"""
    emitter = PresenceStreamEmitter()
    event = emitter.emit(node_id="n1")
    d = event.to_dict()
    committed_keys = [k for k in d.keys() if str(k).startswith("committed_")]
    assert committed_keys == [], f"PresenceEvent 不应包含 committed_* 字段，但发现: {committed_keys}"


# ---------------------------------------------------------------------------
# T-STR-03：CommitStreamEmitter.commit() 在 committed_route_token 为空时抛出 PhaseViolationError
# ---------------------------------------------------------------------------
def test_str_03_commit_emitter_raises_on_empty_token():
    """T-STR-03：CommitStreamEmitter.commit() 在 committed_route_token 为空时抛出 PhaseViolationError"""
    emitter = CommitStreamEmitter()
    with pytest.raises(PhaseViolationError):
        emitter.commit(committed_route_token=None, payload={})


def test_str_03b_commit_emitter_raises_on_wrong_token():
    """T-STR-03b：CommitStreamEmitter.commit() 在 token 不是 GOV_OK_RENDER 时抛出 PhaseViolationError"""
    emitter = CommitStreamEmitter()
    with pytest.raises(PhaseViolationError):
        emitter.commit(committed_route_token="GOV_FAIL_FALLBACK", payload={})


# ---------------------------------------------------------------------------
# T-STR-04：CommitStreamEmitter.commit() 在 committed_route_token = GOV_OK_RENDER 时返回 CommitSnapshot
# ---------------------------------------------------------------------------
def test_str_04_commit_emitter_returns_snapshot_on_ok_render():
    """T-STR-04：CommitStreamEmitter.commit() 在 committed_route_token = GOV_OK_RENDER 时返回 CommitSnapshot"""
    emitter = CommitStreamEmitter()
    snapshot = emitter.commit(
        committed_route_token="GOV_OK_RENDER",
        payload={"intent_commit": "test_intent", "s1_output": "result"},
        request_id="req-001",
    )
    assert isinstance(snapshot, CommitSnapshot)
    assert snapshot.intent_commit == "test_intent"
    assert snapshot.s1_output_hash != ""
    assert snapshot.render_input_snapshot_ref.startswith("snapshot::req-001::")


# ---------------------------------------------------------------------------
# T-STR-05：CommitSnapshot 包含 intent_commit、s1_output_hash、locked_fields、render_input_snapshot_ref
# ---------------------------------------------------------------------------
def test_str_05_commit_snapshot_has_required_fields():
    """T-STR-05：CommitSnapshot 包含 intent_commit、s1_output_hash、locked_fields、render_input_snapshot_ref"""
    emitter = CommitStreamEmitter()
    snapshot = emitter.commit(
        committed_route_token="GOV_OK_RENDER",
        payload={"intent_commit": "intent_x"},
        request_id="req-002",
    )
    d = snapshot.to_dict()
    assert "intent_commit" in d
    assert "s1_output_hash" in d
    assert "locked_fields" in d
    assert "render_input_snapshot_ref" in d
    assert "committed_at_ms" in d
    assert isinstance(d["s1_output_hash"], str) and len(d["s1_output_hash"]) == 64  # SHA-256


# ---------------------------------------------------------------------------
# T-STR-06：RenderStreamEmitter.render() 在 commit_snapshot 为 None 时抛出 PhaseViolationError
# ---------------------------------------------------------------------------
def test_str_06_render_emitter_raises_on_none_snapshot():
    """T-STR-06：RenderStreamEmitter.render() 在 commit_snapshot 为 None 时抛出 PhaseViolationError"""
    emitter = RenderStreamEmitter()
    with pytest.raises(PhaseViolationError):
        emitter.render(commit_snapshot=None, chunk="hello")


# ---------------------------------------------------------------------------
# T-STR-07：RenderStreamEmitter.render() 在 commit_snapshot 存在时返回 RenderEvent
# ---------------------------------------------------------------------------
def test_str_07_render_emitter_returns_event_on_valid_snapshot():
    """T-STR-07：RenderStreamEmitter.render() 在 commit_snapshot 存在时返回 RenderEvent"""
    emitter = RenderStreamEmitter()
    snapshot = _make_commit_snapshot()
    event = emitter.render(commit_snapshot=snapshot, chunk="渲染内容")
    assert isinstance(event, RenderEvent)
    assert event.phase == STREAM_PHASE_RENDER
    assert event.snapshot_ref == snapshot.render_input_snapshot_ref
    assert event.chunk == "渲染内容"


# ---------------------------------------------------------------------------
# T-STR-08：PhaseLockGuard.validate_render_precondition() 在 intent_commit 未变化时返回 True
# ---------------------------------------------------------------------------
def test_str_08_phase_lock_guard_returns_true_when_intent_unchanged():
    """T-STR-08：PhaseLockGuard.validate_render_precondition() 在 intent_commit 未变化时返回 True"""
    guard = PhaseLockGuard()
    snapshot = _make_commit_snapshot(intent_commit="intent_stable")
    result = guard.validate_render_precondition(
        commit_snapshot=snapshot,
        current_intent_commit="intent_stable",
    )
    assert result is True


# ---------------------------------------------------------------------------
# T-STR-09：PhaseLockGuard.validate_render_precondition() 在 intent_commit 变化时返回 False
# ---------------------------------------------------------------------------
def test_str_09_phase_lock_guard_returns_false_when_intent_changed():
    """T-STR-09：PhaseLockGuard.validate_render_precondition() 在 intent_commit 变化时返回 False"""
    guard = PhaseLockGuard()
    snapshot = _make_commit_snapshot(intent_commit="intent_original")
    result = guard.validate_render_precondition(
        commit_snapshot=snapshot,
        current_intent_commit="intent_changed",
    )
    assert result is False


# ---------------------------------------------------------------------------
# T-STR-10：Presence 事件不能作为下游节点输入（presence_events 不在 payload 中）
# ---------------------------------------------------------------------------
def test_str_10_presence_events_not_in_payload():
    """T-STR-10：Presence 事件不能作为下游节点输入（presence_events 不在 payload 中）"""
    emitter = PresenceStreamEmitter()
    event = emitter.emit(node_id="n1")
    d = event.to_dict()
    # Presence 事件只有 phase、node_id、event_type、chunk、ts_ms
    # 不应包含 payload 相关字段
    assert "payload" not in d
    assert "intent_commit" not in d
    assert "s1_output" not in d


# ---------------------------------------------------------------------------
# T-STR-11：Render 不能消费未提交状态（commit_snapshot 为 None 时 render 失败）
# ---------------------------------------------------------------------------
def test_str_11_render_cannot_consume_uncommitted_state():
    """T-STR-11：Render 不能消费未提交状态（commit_snapshot 为 None 时 render 失败）"""
    emitter = RenderStreamEmitter()
    with pytest.raises(PhaseViolationError) as exc_info:
        emitter.render(commit_snapshot=None)
    assert "commit_snapshot" in str(exc_info.value).lower() or "None" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T-STR-12：三类流事件的 ts_ms 为合法时间戳
# ---------------------------------------------------------------------------
def test_str_12_all_stream_events_have_valid_ts_ms():
    """T-STR-12：三类流事件的 ts_ms 为合法时间戳"""
    before = int(time.time() * 1000)

    presence_emitter = PresenceStreamEmitter()
    commit_emitter = CommitStreamEmitter()
    render_emitter = RenderStreamEmitter()

    p_event = presence_emitter.emit(node_id="n1")
    c_snapshot = commit_emitter.commit(
        committed_route_token="GOV_OK_RENDER",
        payload={"intent_commit": "x"},
        request_id="req-ts",
    )
    r_event = render_emitter.render(commit_snapshot=c_snapshot)

    after = int(time.time() * 1000) + 100

    assert before <= p_event.ts_ms <= after
    assert before <= c_snapshot.committed_at_ms <= after
    assert before <= r_event.ts_ms <= after
