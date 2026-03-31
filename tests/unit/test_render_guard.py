"""RenderGuard 核心专项测试（M40-K，T-RG-01~08）。

验证渲染输出授权的正确性。
"""

from __future__ import annotations

from typing import Any

import pytest

from host_brain.integrations.graph_streaming import (
    CommitSnapshot,
    RenderGuard,
    RenderGuardResult,
)
from host_brain.integrations.graph_contract import (
    RENDER_ALLOWED_FIELDS,
    RENDER_ALLOWED_FIELD_PREFIX,
)


def _make_commit_snapshot(
    intent_commit: str = "intent-001",
    locked_fields: dict[str, Any] | None = None,
) -> CommitSnapshot:
    return CommitSnapshot(
        intent_commit=intent_commit,
        s1_output_hash="hash-001",
        locked_fields=locked_fields or {"intent_commit": intent_commit, "s1_output": "output-001"},
        render_input_snapshot_ref="snapshot::req-001::12345",
        committed_at_ms=12345,
    )


# ---------------------------------------------------------------------------
# T-RG-01：render 输出只修改允许字段时 passed=True
# ---------------------------------------------------------------------------
def test_rg_01_allowed_fields_passed():
    """T-RG-01：render 输出只修改允许字段时 passed=True"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot()
    render_output = {"render_text": "Hello World", "render_html": "<p>Hello</p>"}
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=snapshot)
    assert result.passed is True
    assert result.violated_fields == []


# ---------------------------------------------------------------------------
# T-RG-02：render 输出修改 locked_fields 中的字段时 passed=False
# ---------------------------------------------------------------------------
def test_rg_02_locked_field_violation_failed():
    """T-RG-02：render 输出修改 locked_fields 中的字段时 passed=False"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot(locked_fields={"s1_output": "original-output"})
    render_output = {"render_text": "ok", "s1_output": "tampered-output"}  # s1_output 在 locked_fields 中
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=snapshot)
    assert result.passed is False
    assert "s1_output" in result.violated_fields


# ---------------------------------------------------------------------------
# T-RG-03：render 输出修改 intent_commit 时 passed=False
# ---------------------------------------------------------------------------
def test_rg_03_intent_commit_violation_failed():
    """T-RG-03：render 输出修改 intent_commit 时 passed=False"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot()
    render_output = {"render_text": "ok", "intent_commit": "tampered-intent"}
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=snapshot)
    assert result.passed is False
    assert "intent_commit" in result.violated_fields


# ---------------------------------------------------------------------------
# T-RG-04：violated_fields 包含所有违规字段名
# ---------------------------------------------------------------------------
def test_rg_04_violated_fields_contains_all_violations():
    """T-RG-04：violated_fields 包含所有违规字段名"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot(locked_fields={"s1_output": "original"})
    render_output = {
        "render_text": "ok",
        "intent_commit": "tampered",
        "s1_output": "tampered",
        "unknown_field": "value",  # 不在允许列表中
    }
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=snapshot)
    assert result.passed is False
    assert "intent_commit" in result.violated_fields
    assert "s1_output" in result.violated_fields
    assert "unknown_field" in result.violated_fields


# ---------------------------------------------------------------------------
# T-RG-05：render 输出修改 display_ 前缀字段时 passed=True
# ---------------------------------------------------------------------------
def test_rg_05_display_prefix_fields_allowed():
    """T-RG-05：render 输出修改 display_ 前缀字段时 passed=True"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot()
    render_output = {
        "display_title": "My Title",
        "display_subtitle": "My Subtitle",
        "display_footer": "Footer",
    }
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=snapshot)
    assert result.passed is True
    assert result.violated_fields == []


# ---------------------------------------------------------------------------
# T-RG-06：render 输出修改 render_text 时 passed=True
# ---------------------------------------------------------------------------
def test_rg_06_render_text_allowed():
    """T-RG-06：render 输出修改 render_text 时 passed=True"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot()
    render_output = {"render_text": "Hello World"}
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=snapshot)
    assert result.passed is True


# ---------------------------------------------------------------------------
# T-RG-07：commit_snapshot 为 None 时 passed=False
# ---------------------------------------------------------------------------
def test_rg_07_none_snapshot_failed():
    """T-RG-07：commit_snapshot 为 None 时 passed=False"""
    guard = RenderGuard()
    render_output = {"render_text": "Hello"}
    result = guard.validate_render_output(render_output=render_output, commit_snapshot=None)
    assert result.passed is False
    assert "commit_snapshot" in result.reason.lower() or "none" in result.reason.lower()


# ---------------------------------------------------------------------------
# T-RG-08：render 输出为空时 passed=True
# ---------------------------------------------------------------------------
def test_rg_08_empty_render_output_passed():
    """T-RG-08：render 输出为空时 passed=True"""
    guard = RenderGuard()
    snapshot = _make_commit_snapshot()
    result = guard.validate_render_output(render_output={}, commit_snapshot=snapshot)
    assert result.passed is True

    result2 = guard.validate_render_output(render_output=None, commit_snapshot=snapshot)
    assert result2.passed is True
