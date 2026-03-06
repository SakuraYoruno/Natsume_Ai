"""T-VTS-Degraded-01: Host VTS 通道 DEGRADED 状态契约测试。

验收断言：
- Edge 不可达时 resolve_vts_expression 返回 DEGRADED + neutral 备选
- 未知 emotion_tag 映射时返回 DEGRADED + neutral 备选（而非 REJECTED）
- DEGRADED 结果 success=True（表示有有效 fallback 输出）
- reason_code = VTS_DEGRADED_FALLBACK
- expression_id = exp_neutral_01（neutral 备选）
- avatar_action_log 可回溯降级原因
- edge_available=True + 已知 tag 行为不变（APPLIED）
- edge_available=True + free action 仍 REJECTED（不变）
- edge_available=True + 空 tag 仍 REJECTED（不变）
- STATUS_DEGRADED 常量值正确
- 现有 T-VTS-HW-01 / T-E2E-WeChat-01 无回归
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.vts import (
    DEGRADED_FALLBACK_EXPRESSION_ID,
    DEGRADED_FALLBACK_EXPRESSION_TAG,
    EMOTION_TO_EXPRESSION_ID,
    S_AVATAR_EXPRESSION_MAP,
    S_AVATAR_TALK_GATE,
    STATUS_APPLIED,
    STATUS_DEGRADED,
    STATUS_REJECTED,
    TALK_END,
    TALK_START,
    TRIGGER_HOST,
    TRIGGER_LLM,
    VTSActionResult,
    request_talk_gate,
    resolve_vts_expression,
)
from host_brain.core.errors import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    AVATAR_TRIGGER_INVALID,
    VTS_CONNECTION_FAILED,
    VTS_DEGRADED_FALLBACK,
)


FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "channels"
NOW_MS = 1_800_000_100_000


def _load_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


def _make_ledger() -> SQLiteLedger:
    return SQLiteLedger(db_path=":memory:")


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.1: Edge 不可达 → DEGRADED
# ---------------------------------------------------------------------------

class TestEdgeUnavailableDegraded:
    """Edge 不可达时返回 DEGRADED + neutral 备选。"""

    def test_edge_unavailable_returns_degraded(self) -> None:
        """edge_available=False → status=DEGRADED。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_deg_001",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        assert result.success is True
        assert result.status == STATUS_DEGRADED
        assert result.reason_code == VTS_DEGRADED_FALLBACK
        assert result.expression_id == DEGRADED_FALLBACK_EXPRESSION_ID
        assert result.stage == S_AVATAR_EXPRESSION_MAP
        assert "edge not available" in result.detail

    def test_edge_unavailable_preserves_original_emotion_tag(self) -> None:
        """Edge 不可达时保留原始 emotion_tag。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="ANGRY",
            ledger=ledger,
            request_id="req_deg_002",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        assert result.emotion_tag == "ANGRY"
        assert result.expression_id == "exp_neutral_01"

    def test_edge_unavailable_with_none_tag(self) -> None:
        """Edge 不可达 + 无 tag → 使用 NEUTRAL 作为 tag。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag=None,
            ledger=ledger,
            request_id="req_deg_003",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        assert result.success is True
        assert result.status == STATUS_DEGRADED
        assert result.emotion_tag == DEGRADED_FALLBACK_EXPRESSION_TAG
        assert result.expression_id == DEGRADED_FALLBACK_EXPRESSION_ID

    def test_edge_unavailable_audit_logged(self) -> None:
        """Edge 不可达降级写入 avatar_action_log。"""
        ledger = _make_ledger()
        resolve_vts_expression(
            emotion_tag="SAD",
            ledger=ledger,
            request_id="req_deg_004",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        rows = ledger.avatar_action_log
        assert len(rows) == 1
        row = rows[0]
        assert row["request_id"] == "req_deg_004"
        assert row["status"] == STATUS_DEGRADED
        assert row["reason_code"] == VTS_DEGRADED_FALLBACK
        assert row["expression_id"] == "exp_neutral_01"
        assert row["stage"] == S_AVATAR_EXPRESSION_MAP

    def test_edge_unavailable_fixture_driven(self) -> None:
        """基于 fixture 验证 Edge 不可达降级。"""
        data = _load_fixture("vts_edge_unreachable_degraded.json")
        ledger = _make_ledger()
        inp = data["input"]
        result = resolve_vts_expression(
            emotion_tag=inp["emotion_tag"],
            ledger=ledger,
            request_id=inp["request_id"],
            trigger=inp["trigger"],
            edge_available=inp["edge_available"],
            ts_ms=NOW_MS,
        )
        exp = data["expected"]
        assert result.success is (exp["success"])
        assert result.status == exp["status"]
        assert result.reason_code == exp["reason_code"]
        assert result.expression_id == exp["expression_id"]


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.2: 未知映射 → DEGRADED
# ---------------------------------------------------------------------------

class TestUnknownTagDegraded:
    """未知 emotion_tag 映射时返回 DEGRADED + neutral 备选。"""

    def test_unknown_tag_returns_degraded(self) -> None:
        """不在映射表中的 tag → DEGRADED（而非 REJECTED）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="SLEEPY",
            ledger=ledger,
            request_id="req_deg_010",
            edge_available=True,
            ts_ms=NOW_MS,
        )
        assert result.success is True
        assert result.status == STATUS_DEGRADED
        assert result.reason_code == VTS_DEGRADED_FALLBACK
        assert result.expression_id == DEGRADED_FALLBACK_EXPRESSION_ID
        assert "unsupported emotion_tag" in result.detail
        assert "SLEEPY" in result.detail

    def test_unknown_tag_preserves_original(self) -> None:
        """未知 tag 保留原始值（大写归一化后）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="sleepy",
            ledger=ledger,
            request_id="req_deg_011",
            ts_ms=NOW_MS,
        )
        assert result.emotion_tag == "SLEEPY"
        assert result.expression_id == "exp_neutral_01"
        assert result.status == STATUS_DEGRADED

    def test_unknown_tag_audit_logged(self) -> None:
        """未知 tag 降级写入审计。"""
        ledger = _make_ledger()
        resolve_vts_expression(
            emotion_tag="SLEEPY",
            ledger=ledger,
            request_id="req_deg_012",
            ts_ms=NOW_MS,
        )
        rows = ledger.avatar_action_log
        assert len(rows) == 1
        row = rows[0]
        assert row["emotion_tag"] == "SLEEPY"
        assert row["status"] == STATUS_DEGRADED
        assert row["reason_code"] == VTS_DEGRADED_FALLBACK
        assert row["expression_id"] == "exp_neutral_01"

    def test_unknown_tag_fixture_driven(self) -> None:
        """基于 fixture 验证未知 tag 降级。"""
        data = _load_fixture("vts_unknown_tag_degraded.json")
        ledger = _make_ledger()
        inp = data["input"]
        result = resolve_vts_expression(
            emotion_tag=inp["emotion_tag"],
            ledger=ledger,
            request_id=inp["request_id"],
            trigger=inp["trigger"],
            edge_available=inp["edge_available"],
            ts_ms=NOW_MS,
        )
        exp = data["expected"]
        assert result.success is (exp["success"])
        assert result.status == exp["status"]
        assert result.reason_code == exp["reason_code"]
        assert result.emotion_tag == exp["emotion_tag"]
        assert result.expression_id == exp["expression_id"]


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.3: 正常路径无回归
# ---------------------------------------------------------------------------

class TestNormalPathRegression:
    """确保正常路径（APPLIED/REJECTED）行为无变化。"""

    @pytest.mark.parametrize("tag,expected_expr", [
        ("HAPPY", "exp_happy_01"),
        ("SAD", "exp_sad_01"),
        ("ANGRY", "exp_angry_01"),
        ("NEUTRAL", "exp_neutral_01"),
        ("SURPRISED", "exp_surprised_01"),
        ("THINKING", "exp_thinking_01"),
    ])
    def test_known_tags_still_applied(self, tag: str, expected_expr: str) -> None:
        """已知 tag + edge_available=True → APPLIED（无回归）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag=tag,
            ledger=ledger,
            request_id=f"req_reg_{tag}",
            ts_ms=NOW_MS,
        )
        assert result.success is True
        assert result.status == STATUS_APPLIED
        assert result.expression_id == expected_expr
        assert result.reason_code is None

    def test_empty_tag_still_rejected(self) -> None:
        """空 tag 仍 REJECTED（不降级）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="",
            ledger=ledger,
            request_id="req_reg_empty",
            ts_ms=NOW_MS,
        )
        assert result.success is False
        assert result.status == STATUS_REJECTED
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_none_tag_still_rejected(self) -> None:
        """None tag 仍 REJECTED（不降级）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag=None,
            ledger=ledger,
            request_id="req_reg_none",
            ts_ms=NOW_MS,
        )
        assert result.success is False
        assert result.status == STATUS_REJECTED
        assert result.reason_code == AVATAR_TAG_INVALID

    def test_free_action_still_rejected(self) -> None:
        """free-form action 仍 REJECTED（不降级）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_reg_free",
            raw_expression_id="custom_exp",
            ts_ms=NOW_MS,
        )
        assert result.success is False
        assert result.status == STATUS_REJECTED
        assert result.reason_code == AVATAR_FREE_ACTION_BLOCKED

    def test_free_action_with_edge_down_still_prioritizes_edge_down(self) -> None:
        """Edge 不可达优先于 free action check（DEGRADED 而非 REJECTED）。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_reg_edge_free",
            raw_expression_id="custom_exp",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        # Edge 不可达时直接降级，不检查 free action
        assert result.success is True
        assert result.status == STATUS_DEGRADED
        assert result.reason_code == VTS_DEGRADED_FALLBACK


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.4: Talk gate 无回归
# ---------------------------------------------------------------------------

class TestTalkGateRegression:
    """确保 talk gate 行为完全不受 DEGRADED 影响。"""

    def test_talk_start_unchanged(self) -> None:
        ledger = _make_ledger()
        result = request_talk_gate(
            action=TALK_START,
            ledger=ledger,
            request_id="req_gate_001",
            ts_ms=NOW_MS,
        )
        assert result.success is True
        assert result.status == STATUS_APPLIED
        assert result.stage == S_AVATAR_TALK_GATE

    def test_talk_end_unchanged(self) -> None:
        ledger = _make_ledger()
        result = request_talk_gate(
            action=TALK_END,
            ledger=ledger,
            request_id="req_gate_002",
            ts_ms=NOW_MS,
        )
        assert result.success is True
        assert result.status == STATUS_APPLIED

    def test_invalid_gate_action_unchanged(self) -> None:
        ledger = _make_ledger()
        result = request_talk_gate(
            action="INVALID",
            ledger=ledger,
            request_id="req_gate_003",
            ts_ms=NOW_MS,
        )
        assert result.success is False
        assert result.status == STATUS_REJECTED
        assert result.reason_code == AVATAR_TRIGGER_INVALID


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.5: 降级常量与映射
# ---------------------------------------------------------------------------

class TestDegradedConstants:
    """验证降级常量值正确。"""

    def test_status_degraded_value(self) -> None:
        assert STATUS_DEGRADED == "DEGRADED"

    def test_fallback_expression_tag(self) -> None:
        assert DEGRADED_FALLBACK_EXPRESSION_TAG == "NEUTRAL"

    def test_fallback_expression_id(self) -> None:
        assert DEGRADED_FALLBACK_EXPRESSION_ID == "exp_neutral_01"

    def test_fallback_id_matches_mapping(self) -> None:
        assert DEGRADED_FALLBACK_EXPRESSION_ID == EMOTION_TO_EXPRESSION_ID["NEUTRAL"]

    def test_vts_degraded_fallback_error_code(self) -> None:
        assert VTS_DEGRADED_FALLBACK == "VTS_DEGRADED_FALLBACK"


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.6: 审计可回溯性
# ---------------------------------------------------------------------------

class TestAuditTraceability:
    """审计记录可完整回溯降级场景。"""

    def test_multiple_degraded_events_traceable(self) -> None:
        """多个降级事件按序可查。"""
        ledger = _make_ledger()
        resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_trace_001",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        resolve_vts_expression(
            emotion_tag="UNKNOWN_EMOTION",
            ledger=ledger,
            request_id="req_trace_002",
            edge_available=True,
            ts_ms=NOW_MS + 1,
        )
        resolve_vts_expression(
            emotion_tag="SAD",
            ledger=ledger,
            request_id="req_trace_003",
            edge_available=True,
            ts_ms=NOW_MS + 2,
        )

        rows = ledger.avatar_action_log
        assert len(rows) == 3

        # 第一条：Edge 不可达降级
        assert rows[0]["status"] == STATUS_DEGRADED
        assert rows[0]["reason_code"] == VTS_DEGRADED_FALLBACK
        assert rows[0]["request_id"] == "req_trace_001"

        # 第二条：未知 tag 降级
        assert rows[1]["status"] == STATUS_DEGRADED
        assert rows[1]["reason_code"] == VTS_DEGRADED_FALLBACK
        assert rows[1]["emotion_tag"] == "UNKNOWN_EMOTION"

        # 第三条：正常 APPLIED
        assert rows[2]["status"] == STATUS_APPLIED
        assert rows[2]["reason_code"] is None

    def test_degraded_and_rejected_interleaved(self) -> None:
        """降级和拒绝交替时审计链正确。"""
        ledger = _make_ledger()
        # DEGRADED (edge down)
        r1 = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_mix_001",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        # REJECTED (empty tag)
        r2 = resolve_vts_expression(
            emotion_tag="",
            ledger=ledger,
            request_id="req_mix_002",
            edge_available=True,
            ts_ms=NOW_MS + 1,
        )
        # APPLIED (normal)
        r3 = resolve_vts_expression(
            emotion_tag="NEUTRAL",
            ledger=ledger,
            request_id="req_mix_003",
            edge_available=True,
            ts_ms=NOW_MS + 2,
        )

        assert r1.status == STATUS_DEGRADED
        assert r2.status == STATUS_REJECTED
        assert r3.status == STATUS_APPLIED

        rows = ledger.avatar_action_log
        assert len(rows) == 3
        statuses = [r["status"] for r in rows]
        assert statuses == [STATUS_DEGRADED, STATUS_REJECTED, STATUS_APPLIED]


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.7: VTSActionResult 结构不变
# ---------------------------------------------------------------------------

class TestResultStructure:
    """VTSActionResult 字段结构不变。"""

    def test_result_has_all_fields(self) -> None:
        """DEGRADED 结果包含所有必要字段。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_struct_001",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        assert hasattr(result, "success")
        assert hasattr(result, "request_id")
        assert hasattr(result, "trigger")
        assert hasattr(result, "emotion_tag")
        assert hasattr(result, "expression_id")
        assert hasattr(result, "status")
        assert hasattr(result, "reason_code")
        assert hasattr(result, "stage")
        assert hasattr(result, "detail")

    def test_degraded_result_is_frozen(self) -> None:
        """VTSActionResult 是 frozen dataclass，不可变。"""
        ledger = _make_ledger()
        result = resolve_vts_expression(
            emotion_tag="HAPPY",
            ledger=ledger,
            request_id="req_struct_002",
            edge_available=False,
            ts_ms=NOW_MS,
        )
        with pytest.raises(AttributeError):
            result.status = "APPLIED"  # type: ignore


# ---------------------------------------------------------------------------
# T-VTS-Degraded-01.8: Fixture 结构验证
# ---------------------------------------------------------------------------

class TestFixtureStructure:

    @pytest.mark.parametrize("fixture_name", [
        "vts_edge_unreachable_degraded.json",
        "vts_unknown_tag_degraded.json",
    ])
    def test_fixture_loadable(self, fixture_name: str) -> None:
        """Fixture 文件可加载且结构正确。"""
        data = _load_fixture(fixture_name)
        assert "scenario" in data
        assert "input" in data
        assert "expected" in data

    def test_edge_unreachable_fixture_values(self) -> None:
        data = _load_fixture("vts_edge_unreachable_degraded.json")
        assert data["expected"]["status"] == STATUS_DEGRADED
        assert data["expected"]["reason_code"] == VTS_DEGRADED_FALLBACK
        assert data["input"]["edge_available"] is False

    def test_unknown_tag_fixture_values(self) -> None:
        data = _load_fixture("vts_unknown_tag_degraded.json")
        assert data["expected"]["status"] == STATUS_DEGRADED
        assert data["expected"]["reason_code"] == VTS_DEGRADED_FALLBACK
        assert data["input"]["edge_available"] is True
        assert data["expected"]["emotion_tag"] == "SLEEPY"
