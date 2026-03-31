"""M26-G：mood_state → emotion_tag 约束映射测试（T-MOOD-CONST-01~04）。

验收标准：
- T-MOOD-CONST-01：mood_to_emotion_constraint("critical") 返回 ["NEUTRAL", "SAD"]
- T-MOOD-CONST-02：mood_to_emotion_constraint("engaged") 返回包含 EXCITED 的列表
- T-MOOD-CONST-03：_validate_emotion_tag("EXCITED", mood_state="critical") 降级为 NEUTRAL 并写警告日志
- T-MOOD-CONST-04：_validate_emotion_tag("HAPPY", mood_state="steady") 返回 HAPPY（在允许范围内）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.core.metabolism import (
    MOOD_EMOTION_CONSTRAINTS,
    mood_to_emotion_constraint,
)
from host_brain.core.qa import (
    DEFAULT_EMOTION_TAG,
    VIOLATION_INVALID_EMOTION_TAG,
    _validate_emotion_tag,
)


# ---------------------------------------------------------------------------
# T-MOOD-CONST-01：mood_to_emotion_constraint("critical") 返回 ["NEUTRAL", "SAD"]
# ---------------------------------------------------------------------------

class TestMoodToEmotionConstraintCritical:
    """T-MOOD-CONST-01"""

    def test_critical_returns_neutral_sad(self) -> None:
        result = mood_to_emotion_constraint("critical")
        assert result == ["NEUTRAL", "SAD"]

    def test_critical_first_element_is_neutral(self) -> None:
        """降级默认值（第一个元素）应为 NEUTRAL。"""
        result = mood_to_emotion_constraint("critical")
        assert result[0] == "NEUTRAL"

    def test_critical_does_not_contain_excited(self) -> None:
        result = mood_to_emotion_constraint("critical")
        assert "EXCITED" not in result

    def test_critical_does_not_contain_happy(self) -> None:
        result = mood_to_emotion_constraint("critical")
        assert "HAPPY" not in result


# ---------------------------------------------------------------------------
# T-MOOD-CONST-02：mood_to_emotion_constraint("engaged") 返回包含 EXCITED 的列表
# ---------------------------------------------------------------------------

class TestMoodToEmotionConstraintEngaged:
    """T-MOOD-CONST-02"""

    def test_engaged_contains_excited(self) -> None:
        result = mood_to_emotion_constraint("engaged")
        assert "EXCITED" in result

    def test_engaged_contains_happy(self) -> None:
        result = mood_to_emotion_constraint("engaged")
        assert "HAPPY" in result

    def test_engaged_contains_surprised(self) -> None:
        result = mood_to_emotion_constraint("engaged")
        assert "SURPRISED" in result


# ---------------------------------------------------------------------------
# 其他 mood_state 覆盖
# ---------------------------------------------------------------------------

class TestMoodToEmotionConstraintOthers:

    @pytest.mark.parametrize("mood_state", list(MOOD_EMOTION_CONSTRAINTS.keys()))
    def test_all_mood_states_return_non_empty_list(self, mood_state: str) -> None:
        result = mood_to_emotion_constraint(mood_state)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_unknown_mood_state_falls_back_to_steady(self) -> None:
        """未知 mood_state 应 fallback 到 steady 的约束范围。"""
        result = mood_to_emotion_constraint("unknown_state")
        steady = mood_to_emotion_constraint("steady")
        assert result == steady

    def test_tired_does_not_contain_excited(self) -> None:
        result = mood_to_emotion_constraint("tired")
        assert "EXCITED" not in result

    def test_tired_contains_neutral(self) -> None:
        result = mood_to_emotion_constraint("tired")
        assert "NEUTRAL" in result

    def test_returns_copy_not_reference(self) -> None:
        """返回的列表应为副本，修改不影响原始常量。"""
        result = mood_to_emotion_constraint("steady")
        original_len = len(MOOD_EMOTION_CONSTRAINTS["steady"])
        result.append("FAKE_TAG")
        assert len(MOOD_EMOTION_CONSTRAINTS["steady"]) == original_len


# ---------------------------------------------------------------------------
# T-MOOD-CONST-03：_validate_emotion_tag("EXCITED", mood_state="critical") 降级
# ---------------------------------------------------------------------------

class TestValidateEmotionTagWithMoodState:
    """T-MOOD-CONST-03 & T-MOOD-CONST-04"""

    def test_excited_under_critical_downgraded_to_neutral(self) -> None:
        """T-MOOD-CONST-03：EXCITED 在 critical 下降级为 NEUTRAL。"""
        result = _validate_emotion_tag("EXCITED", mood_state="critical")
        assert result == "NEUTRAL"

    def test_excited_under_critical_writes_qa_violation(self) -> None:
        """T-MOOD-CONST-03 扩展：降级时写 QA 警告日志。"""
        mock_ledger = MagicMock()
        mock_ledger.log_qa_violation = MagicMock()

        result = _validate_emotion_tag(
            "EXCITED",
            mood_state="critical",
            ledger=mock_ledger,
            request_id="req_mood_test",
        )

        assert result == "NEUTRAL"
        mock_ledger.log_qa_violation.assert_called_once()
        call_kwargs = mock_ledger.log_qa_violation.call_args.kwargs
        assert call_kwargs["code"] == VIOLATION_INVALID_EMOTION_TAG
        assert "critical" in call_kwargs["detail"]
        assert "EXCITED" in call_kwargs["detail"]

    def test_happy_under_steady_passes(self) -> None:
        """T-MOOD-CONST-04：HAPPY 在 steady 下通过，返回 HAPPY。"""
        result = _validate_emotion_tag("HAPPY", mood_state="steady")
        assert result == "HAPPY"

    def test_calm_under_tired_passes(self) -> None:
        result = _validate_emotion_tag("CALM", mood_state="tired")
        assert result == "CALM"

    def test_excited_under_engaged_passes(self) -> None:
        result = _validate_emotion_tag("EXCITED", mood_state="engaged")
        assert result == "EXCITED"

    def test_no_mood_state_no_constraint(self) -> None:
        """不提供 mood_state 时，不做约束校验，EXCITED 正常通过。"""
        result = _validate_emotion_tag("EXCITED")
        assert result == "EXCITED"

    def test_invalid_tag_still_downgraded_regardless_of_mood(self) -> None:
        """非法枚举值无论 mood_state 如何都降级为 NEUTRAL。"""
        result = _validate_emotion_tag("INVALID_TAG", mood_state="engaged")
        assert result == DEFAULT_EMOTION_TAG
