"""T-FALLBACK-01~05: Edge 兜底小模型门控单元测试（M15-D）。

覆盖需求：FR-VOICE-004
"""

from __future__ import annotations

import pytest

from edge_client.fallback_model import (
    VALID_TASK_TYPES,
    FallbackModelGate,
    FallbackOutput,
)
from host_brain.core.errors import FALLBACK_MODEL_UNAVAILABLE, VOICE_SCHEMA_INVALID


# ── 夹具 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def gate_unloaded() -> FallbackModelGate:
    """默认卸载状态的 FallbackModelGate。"""
    return FallbackModelGate()


@pytest.fixture
def gate_loaded() -> FallbackModelGate:
    """已加载状态的 FallbackModelGate。"""
    g = FallbackModelGate()
    g.load()
    return g


# ── T-FALLBACK-01: generate 成功，draft_only=True ────────────────────────────

class TestGenerateSuccess:
    """T-FALLBACK-01: generate 成功，draft_only=True。"""

    def test_returns_fallback_output(self, gate_loaded):
        result = gate_loaded.generate(
            "こんにちは",
            task_type="scene_response",
            request_id="req_001",
        )
        assert isinstance(result, FallbackOutput)

    def test_draft_only_always_true(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_002",
        )
        assert result.draft_only is True

    def test_text_not_empty_on_success(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_003",
        )
        assert result.text is not None

    def test_reason_code_none_on_success(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_004",
        )
        assert result.reason_code is None

    def test_output_id_present(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_005",
        )
        assert result.output_id
        assert len(result.output_id) > 0

    def test_all_valid_task_types(self, gate_loaded):
        for task_type in VALID_TASK_TYPES:
            result = gate_loaded.generate(
                "テスト",
                task_type=task_type,
                request_id=f"req_{task_type}",
            )
            assert result.draft_only is True


# ── T-FALLBACK-02: 不可用时快速失败，reason_code=FALLBACK_MODEL_UNAVAILABLE ──

class TestUnavailableFastFail:
    """T-FALLBACK-02: 不可用时快速失败，reason_code=FALLBACK_MODEL_UNAVAILABLE。"""

    def test_unloaded_returns_unavailable(self, gate_unloaded):
        result = gate_unloaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_unavail_001",
        )
        assert result.reason_code == FALLBACK_MODEL_UNAVAILABLE

    def test_unloaded_draft_only_still_true(self, gate_unloaded):
        result = gate_unloaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_unavail_002",
        )
        assert result.draft_only is True

    def test_unloaded_text_empty(self, gate_unloaded):
        result = gate_unloaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_unavail_003",
        )
        assert result.text == ""

    def test_unloaded_stage_present(self, gate_unloaded):
        result = gate_unloaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_unavail_004",
        )
        assert result.stage is not None


# ── T-FALLBACK-03: 输出不含裁决/执行事实宣告（QA 断言）────────────────────

class TestNoPseudoFacts:
    """T-FALLBACK-03: 输出不含裁决/执行事实宣告（QA 断言）。"""

    def test_as_dict_no_decision_field(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_qa_001",
        )
        d = result.as_dict()
        assert "decision" not in d

    def test_as_dict_no_execution_claim_field(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_qa_002",
        )
        d = result.as_dict()
        assert "execution_claim" not in d

    def test_as_dict_no_intent_draft_field(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_qa_003",
        )
        d = result.as_dict()
        assert "intent_draft" not in d

    def test_draft_only_field_in_dict(self, gate_loaded):
        result = gate_loaded.generate(
            "テスト",
            task_type="scene_response",
            request_id="req_qa_004",
        )
        d = result.as_dict()
        assert d["draft_only"] is True


# ── T-FALLBACK-04: load/unload 生命周期正确 ──────────────────────────────────

class TestLifecycle:
    """T-FALLBACK-04: load/unload 生命周期正确。"""

    def test_default_not_available(self):
        gate = FallbackModelGate()
        assert gate.is_available() is False

    def test_load_makes_available(self):
        gate = FallbackModelGate()
        gate.load()
        assert gate.is_available() is True

    def test_unload_makes_unavailable(self):
        gate = FallbackModelGate()
        gate.load()
        gate.unload()
        assert gate.is_available() is False

    def test_load_returns_true(self):
        gate = FallbackModelGate()
        result = gate.load()
        assert result is True

    def test_load_unload_cycle(self):
        gate = FallbackModelGate()
        gate.load()
        assert gate.is_available() is True
        gate.unload()
        assert gate.is_available() is False
        gate.load()
        assert gate.is_available() is True


# ── T-FALLBACK-05: task_type 非法 → VOICE_SCHEMA_INVALID ─────────────────────

class TestInvalidTaskType:
    """T-FALLBACK-05: task_type 非法 → VOICE_SCHEMA_INVALID。"""

    def test_invalid_task_type_raises(self, gate_loaded):
        with pytest.raises(ValueError) as exc_info:
            gate_loaded.generate(
                "テスト",
                task_type="invalid_task",
                request_id="req_invalid_001",
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_empty_task_type_raises(self, gate_loaded):
        with pytest.raises(ValueError) as exc_info:
            gate_loaded.generate(
                "テスト",
                task_type="",
                request_id="req_invalid_002",
            )
        assert exc_info.value.reason_code == VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]

    def test_valid_task_types_accepted(self, gate_loaded):
        for task_type in VALID_TASK_TYPES:
            result = gate_loaded.generate(
                "テスト",
                task_type=task_type,
                request_id=f"req_valid_{task_type}",
            )
            assert isinstance(result, FallbackOutput)
