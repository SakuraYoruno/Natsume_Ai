"""M26-F：VTS 热键配置文件抽离测试（T-VTS-CFG-01~04）。

验收标准：
- T-VTS-CFG-01：load_from_config_file() 从 YAML 文件加载映射表，与文件内容一致
- T-VTS-CFG-02：YAML 文件缺失时 load_from_config_file() 抛出 FileNotFoundError（含明确错误信息）
- T-VTS-CFG-03：YAML 文件格式非法时抛出 ValueError（含字段名）
- T-VTS-CFG-04：DEFAULT_EMOTION_MAPPING 作为 fallback，YAML 未配置的 emotion_tag 使用默认值
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from host_brain.channels.vts_mapping_store import DEFAULT_EMOTION_MAPPING, VTSMappingStore


# ---------------------------------------------------------------------------
# 辅助：写临时 YAML 文件
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "vts_hotkey_config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8-sig")
    return str(p)


# ---------------------------------------------------------------------------
# T-VTS-CFG-01：load_from_config_file() 从 YAML 文件加载映射表，与文件内容一致
# ---------------------------------------------------------------------------

class TestLoadFromConfigFileBasic:
    """T-VTS-CFG-01"""

    def test_load_returns_correct_mapping(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, """
            meta:
              model_name: "测试模型"
              config_version: "v1.0.0"
            emotion_mapping:
              NEUTRAL: "exp_neutral_01"
              HAPPY:   "exp_happy_01"
              SAD:     "exp_sad_01"
              EXCITED: "exp_excited_custom"
        """)
        result = VTSMappingStore.load_from_config_file(yaml_path)

        assert result["NEUTRAL"] == "exp_neutral_01"
        assert result["HAPPY"] == "exp_happy_01"
        assert result["SAD"] == "exp_sad_01"
        assert result["EXCITED"] == "exp_excited_custom"

    def test_load_normalizes_keys_to_uppercase(self, tmp_path: Path) -> None:
        """emotion_tag 键应被规范化为大写。"""
        yaml_path = _write_yaml(tmp_path, """
            emotion_mapping:
              happy: "exp_happy_01"
              Sad:   "exp_sad_01"
        """)
        result = VTSMappingStore.load_from_config_file(yaml_path)
        assert "HAPPY" in result
        assert "SAD" in result

    def test_load_real_config_file(self) -> None:
        """T-VTS-CFG-01 扩展：加载项目实际配置文件（configs/vts/vts_hotkey_config.yaml）。"""
        real_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "configs", "vts", "vts_hotkey_config.yaml"
        )
        real_path = os.path.normpath(real_path)
        if not os.path.exists(real_path):
            pytest.skip("实际配置文件不存在，跳过")

        result = VTSMappingStore.load_from_config_file(real_path)
        # 实际配置文件应包含所有基础 emotion_tag
        for tag in ("NEUTRAL", "HAPPY", "SAD", "ANGRY", "SURPRISED"):
            assert tag in result, f"实际配置文件缺少 emotion_tag: {tag!r}"


# ---------------------------------------------------------------------------
# T-VTS-CFG-02：YAML 文件缺失时抛出 FileNotFoundError（含明确错误信息）
# ---------------------------------------------------------------------------

class TestLoadFromConfigFileMissing:
    """T-VTS-CFG-02"""

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        missing_path = str(tmp_path / "nonexistent.yaml")
        with pytest.raises(FileNotFoundError) as exc_info:
            VTSMappingStore.load_from_config_file(missing_path)
        # 错误信息应包含文件路径
        assert "nonexistent.yaml" in str(exc_info.value)

    def test_missing_file_error_message_is_descriptive(self, tmp_path: Path) -> None:
        missing_path = str(tmp_path / "missing_config.yaml")
        with pytest.raises(FileNotFoundError) as exc_info:
            VTSMappingStore.load_from_config_file(missing_path)
        msg = str(exc_info.value)
        # 错误信息应有引导性说明
        assert "vts_hotkey_config_guide" in msg or "配置文件" in msg


# ---------------------------------------------------------------------------
# T-VTS-CFG-03：YAML 文件格式非法时抛出 ValueError（含字段名）
# ---------------------------------------------------------------------------

class TestLoadFromConfigFileInvalidFormat:
    """T-VTS-CFG-03"""

    def test_missing_emotion_mapping_node_raises_value_error(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, """
            meta:
              model_name: "测试"
            # 故意缺少 emotion_mapping 节点
        """)
        with pytest.raises(ValueError) as exc_info:
            VTSMappingStore.load_from_config_file(yaml_path)
        assert "emotion_mapping" in str(exc_info.value)

    def test_emotion_mapping_wrong_type_raises_value_error(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, """
            emotion_mapping: "这不是一个字典"
        """)
        with pytest.raises(ValueError) as exc_info:
            VTSMappingStore.load_from_config_file(yaml_path)
        assert "emotion_mapping" in str(exc_info.value)

    def test_root_not_dict_raises_value_error(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, """
            - item1
            - item2
        """)
        with pytest.raises(ValueError):
            VTSMappingStore.load_from_config_file(yaml_path)


# ---------------------------------------------------------------------------
# T-VTS-CFG-04：DEFAULT_EMOTION_MAPPING 作为 fallback
# ---------------------------------------------------------------------------

class TestLoadFromConfigFileFallback:
    """T-VTS-CFG-04"""

    def test_yaml_only_partial_mapping_fallback_fills_rest(self, tmp_path: Path) -> None:
        """YAML 只配置部分 emotion_tag，其余从 DEFAULT_EMOTION_MAPPING 补全。"""
        yaml_path = _write_yaml(tmp_path, """
            emotion_mapping:
              HAPPY: "exp_happy_custom"
        """)
        result = VTSMappingStore.load_from_config_file(yaml_path)

        # YAML 中配置的优先
        assert result["HAPPY"] == "exp_happy_custom"

        # DEFAULT_EMOTION_MAPPING 中其余 tag 应作为 fallback 存在
        for tag, expr_id in DEFAULT_EMOTION_MAPPING.items():
            if tag != "HAPPY":
                assert tag in result, f"fallback 缺少 emotion_tag: {tag!r}"
                assert result[tag] == expr_id, (
                    f"fallback 值不匹配: {tag!r} -> {result[tag]!r}，期望 {expr_id!r}"
                )

    def test_yaml_overrides_default_mapping(self, tmp_path: Path) -> None:
        """YAML 中配置的值应覆盖 DEFAULT_EMOTION_MAPPING 中的同名 tag。"""
        yaml_path = _write_yaml(tmp_path, """
            emotion_mapping:
              NEUTRAL: "exp_neutral_custom_override"
        """)
        result = VTSMappingStore.load_from_config_file(yaml_path)
        assert result["NEUTRAL"] == "exp_neutral_custom_override"
