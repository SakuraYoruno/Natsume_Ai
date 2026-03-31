"""M28-N：ToolMeta 情感适用性字段测试（T-TOOL-EMO-REQ-01~03）"""

from __future__ import annotations

import pytest
from host_brain.mcp.tool_registry import ToolMeta


class TestToolMetaEmotionRequirement:
    """T-TOOL-EMO-REQ-01~03：ToolMeta 情感适用性字段测试"""

    def test_tool_meta_has_emotion_requirement_field(self):
        """T-TOOL-EMO-REQ-01：ToolMeta 包含 emotion_requirement 字段"""
        meta = ToolMeta(
            provider_id="test-provider",
            tool_id="test-tool",
            name="Test Tool",
            description="A test tool",
            access_level="PUBLIC",
            risk_level="LOW",
            reversible=True,
        )
        assert hasattr(meta, "emotion_requirement")
        assert isinstance(meta.emotion_requirement, list)

    def test_emotion_requirement_default_is_empty(self):
        """T-TOOL-EMO-REQ-02：emotion_requirement 默认为空列表（无限制）"""
        meta = ToolMeta(
            provider_id="test-provider",
            tool_id="test-tool",
            name="Test Tool",
            description="A test tool",
            access_level="PUBLIC",
            risk_level="LOW",
            reversible=True,
        )
        assert meta.emotion_requirement == []

    def test_emotion_requirement_in_as_dict(self):
        """T-TOOL-EMO-REQ-03：as_dict() 包含 emotion_requirement 字段"""
        meta = ToolMeta(
            provider_id="test-provider",
            tool_id="test-tool",
            name="Test Tool",
            description="A test tool",
            access_level="PUBLIC",
            risk_level="LOW",
            reversible=True,
            emotion_requirement=["HAPPY", "EXCITED"],
        )
        d = meta.as_dict()
        assert "emotion_requirement" in d
        assert d["emotion_requirement"] == ["HAPPY", "EXCITED"]

    def test_from_dict_parses_emotion_requirement(self):
        """from_dict() 正确解析 emotion_requirement 字段"""
        data = {
            "provider_id": "test-provider",
            "tool_id": "test-tool",
            "name": "Test Tool",
            "description": "A test tool",
            "access_level": "PUBLIC",
            "risk_level": "LOW",
            "reversible": True,
            "emotion_requirement": ["CALM", "THINKING"],
        }
        meta = ToolMeta.from_dict(data)
        assert meta.emotion_requirement == ["CALM", "THINKING"]
