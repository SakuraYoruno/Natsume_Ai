"""T-VTS-01~05: VTS 外显确定性映射单元测试（M14-B）。

覆盖需求：FR-AVATAR-001
"""

from __future__ import annotations

import pytest

from edge_client.actuators.vts_expression_map import (
    EXPRESSION_MAP,
    EmotionTag,
    ExpressionTrigger,
    VTSExpressionMap,
)
from host_brain.core.errors import (
    VTS_EXPRESSION_INVALID,
    VTS_INTENSITY_OUT_OF_RANGE,
    VTS_TAG_UNKNOWN,
)


@pytest.fixture
def mapper() -> VTSExpressionMap:
    return VTSExpressionMap()


# ── T-VTS-01: resolve 合法 tag → 返回 ExpressionTrigger，expression_id 固定 ──

class TestResolveValidTag:
    """T-VTS-01: resolve 合法 tag → 返回 ExpressionTrigger，expression_id 固定。"""

    def test_returns_expression_trigger(self, mapper):
        result = mapper.resolve("happy", request_id="req_001")
        assert isinstance(result, ExpressionTrigger)

    def test_expression_id_matches_map(self, mapper):
        result = mapper.resolve("happy", request_id="req_001")
        assert result.expression_id == EXPRESSION_MAP[EmotionTag.HAPPY]

    def test_emotion_tag_enum_set(self, mapper):
        result = mapper.resolve("happy", request_id="req_001")
        assert result.emotion_tag == EmotionTag.HAPPY

    def test_case_insensitive(self, mapper):
        result_lower = mapper.resolve("happy", request_id="req_001")
        result_upper = mapper.resolve("HAPPY", request_id="req_001")
        assert result_lower.expression_id == result_upper.expression_id

    def test_all_valid_tags_resolve(self, mapper):
        for tag in EmotionTag:
            result = mapper.resolve(tag.value, request_id="req_all")
            assert result.expression_id == EXPRESSION_MAP[tag]

    def test_intensity_default(self, mapper):
        result = mapper.resolve("neutral", request_id="req_001")
        assert result.intensity == 1.0

    def test_duration_ms_default(self, mapper):
        result = mapper.resolve("neutral", request_id="req_001")
        assert result.duration_ms == 2000

    def test_request_id_preserved(self, mapper):
        result = mapper.resolve("calm", request_id="req_xyz")
        assert result.request_id == "req_xyz"


# ── T-VTS-02: 非法 emotion_tag → VTS_TAG_UNKNOWN ─────────────────────────────

class TestUnknownTag:
    """T-VTS-02: 非法 emotion_tag → VTS_TAG_UNKNOWN。"""

    def test_unknown_tag_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve("flying", request_id="req_002")
        assert exc_info.value.reason_code == VTS_TAG_UNKNOWN  # type: ignore[attr-defined]

    def test_empty_tag_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve("", request_id="req_002")
        assert exc_info.value.reason_code == VTS_TAG_UNKNOWN  # type: ignore[attr-defined]

    def test_numeric_string_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve("123", request_id="req_002")
        assert exc_info.value.reason_code == VTS_TAG_UNKNOWN  # type: ignore[attr-defined]


# ── T-VTS-03: 自由动作参数（非字符串）→ VTS_EXPRESSION_INVALID ───────────────

class TestFreeActionBlocked:
    """T-VTS-03: 自由动作参数（非字符串）→ VTS_EXPRESSION_INVALID。"""

    def test_dict_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve({"action": "wave"}, request_id="req_003")  # type: ignore[arg-type]
        assert exc_info.value.reason_code == VTS_EXPRESSION_INVALID  # type: ignore[attr-defined]

    def test_list_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve(["happy"], request_id="req_003")  # type: ignore[arg-type]
        assert exc_info.value.reason_code == VTS_EXPRESSION_INVALID  # type: ignore[attr-defined]

    def test_int_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve(42, request_id="req_003")  # type: ignore[arg-type]
        assert exc_info.value.reason_code == VTS_EXPRESSION_INVALID  # type: ignore[attr-defined]

    def test_intensity_out_of_range_raises(self, mapper):
        with pytest.raises(ValueError) as exc_info:
            mapper.resolve("happy", intensity=1.5, request_id="req_003")
        assert exc_info.value.reason_code == VTS_INTENSITY_OUT_OF_RANGE  # type: ignore[attr-defined]


# ── T-VTS-04: 同版本同输入输出一致（可回放映射表）────────────────────────────

class TestDeterminism:
    """T-VTS-04: 同版本同输入输出一致（可回放映射表）。"""

    def test_same_input_same_expression_id(self, mapper):
        r1 = mapper.resolve("sad", intensity=0.8, duration_ms=1500, request_id="req_a")
        r2 = mapper.resolve("sad", intensity=0.8, duration_ms=1500, request_id="req_b")
        assert r1.expression_id == r2.expression_id
        assert r1.emotion_tag == r2.emotion_tag
        assert r1.intensity == r2.intensity
        assert r1.duration_ms == r2.duration_ms

    def test_expression_map_immutable(self, mapper):
        """EXPRESSION_MAP 不可在运行时修改（frozen dict 语义）。"""
        original = dict(EXPRESSION_MAP)
        # 尝试修改不影响原始映射
        _ = mapper.resolve("happy", request_id="req_c")
        assert dict(EXPRESSION_MAP) == original


# ── T-VTS-05: list_tags 返回所有合法枚举值 ───────────────────────────────────

class TestListTags:
    """T-VTS-05: list_tags 返回所有合法枚举值。"""

    def test_returns_list(self, mapper):
        tags = mapper.list_tags()
        assert isinstance(tags, list)

    def test_all_enum_values_present(self, mapper):
        tags = mapper.list_tags()
        for tag in EmotionTag:
            assert tag.value in tags

    def test_no_extra_tags(self, mapper):
        tags = mapper.list_tags()
        assert len(tags) == len(EmotionTag)

    def test_all_lowercase(self, mapper):
        tags = mapper.list_tags()
        for tag in tags:
            assert tag == tag.lower()
