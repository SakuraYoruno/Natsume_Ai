from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping


logger = logging.getLogger(__name__)

DEFAULT_EMOTION_TO_HOTKEY: dict[str, str] = {
    "NEUTRAL": "移除所有按键表情",
    "HAPPY": "HK_Happy",
    "SAD": "哭哭",
    "ANGRY": "HK_Angry",
    "SURPRISED": "圈圈眼",
    "THINKING": "无语眼",
    "EXCITED": "HK_Happy",
    "CALM": "蓝瞳孔",
    "FRUSTRATED": "HK_Despise",
    "ANXIOUS": "眩晕",
    "LOVING": "HK_Love",
    "EMBARRASSED": "HK_Shy",
    "CONFUSED": "无语眼",
    "BORED": "HK_Dead",
    "PROUD": "剪刀眼",
    "GRATEFUL": "四叶草",
}

DEFAULT_VTS_HOTKEY_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "vts" / "vts_hotkey_config.yaml"
)
VTS_EMOTION_TAG_ALIASES: dict[str, str] = {
    "AFFECTION": "LOVING",
    "ANNOYED": "FRUSTRATED",
    "ASHAMED": "EMBARRASSED",
    "COQUETTISH": "LOVING",
    "DELIGHT": "HAPPY",
    "EXCITEMENT": "EXCITED",
    "GRIEF": "SAD",
    "GUILT": "EMBARRASSED",
    "GUILTY": "EMBARRASSED",
    "HAPPINESS": "HAPPY",
    "HURT": "SAD",
    "JOY": "HAPPY",
    "LOVE": "LOVING",
    "PLAYFUL": "HAPPY",
    "SERIOUS": "THINKING",
    "SHY": "EMBARRASSED",
    "SOOTHING": "CALM",
    "WARM": "CALM",
}


def get_default_vts_hotkey_config_path() -> str:
    return str(DEFAULT_VTS_HOTKEY_CONFIG_PATH)


def load_runtime_emotion_mapping(config_path: str | None = None) -> dict[str, str]:
    path = Path(config_path or DEFAULT_VTS_HOTKEY_CONFIG_PATH)
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        logger.warning("PyYAML not available; using built-in VTS mapping defaults")
        return dict(DEFAULT_EMOTION_TO_HOTKEY)

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("VTS hotkey config not found; using defaults (path=%s)", path)
        return dict(DEFAULT_EMOTION_TO_HOTKEY)
    except Exception as exc:
        logger.warning("VTS hotkey config load failed; using defaults (path=%s): %s", path, exc)
        return dict(DEFAULT_EMOTION_TO_HOTKEY)

    emotion_mapping = data.get("emotion_mapping")
    if not isinstance(emotion_mapping, dict):
        logger.warning("VTS hotkey config missing emotion_mapping; using defaults (path=%s)", path)
        return dict(DEFAULT_EMOTION_TO_HOTKEY)

    merged = dict(DEFAULT_EMOTION_TO_HOTKEY)
    for tag, hotkey_id in emotion_mapping.items():
        if not isinstance(tag, str) or not isinstance(hotkey_id, str):
            continue
        normalized_tag = tag.strip().upper()
        normalized_hotkey = hotkey_id.strip()
        if not normalized_tag or not normalized_hotkey:
            continue
        merged[normalized_tag] = normalized_hotkey
    return merged


def supported_runtime_emotion_tags(config_path: str | None = None) -> tuple[str, ...]:
    mapping = load_runtime_emotion_mapping(config_path=config_path)
    tags = sorted(tag for tag in mapping if str(tag or "").strip())
    if "NEUTRAL" in tags:
        tags.remove("NEUTRAL")
        tags.insert(0, "NEUTRAL")
    return tuple(tags)


def normalize_runtime_emotion_tag(
    value: Any,
    *,
    mapping: Mapping[str, str] | None = None,
    config_path: str | None = None,
    fallback_tag: str = "NEUTRAL",
) -> str:
    resolved_mapping = {str(key).strip().upper(): str(val) for key, val in dict(mapping or {}).items()}
    if not resolved_mapping:
        resolved_mapping = load_runtime_emotion_mapping(config_path=config_path)
    normalized_fallback = str(fallback_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    if normalized_fallback not in resolved_mapping:
        normalized_fallback = "NEUTRAL" if "NEUTRAL" in resolved_mapping else next(iter(resolved_mapping), "NEUTRAL")

    tag = str(value or normalized_fallback).strip().upper() or normalized_fallback
    tag = tag.replace("-", "_").replace(" ", "_")
    tag = VTS_EMOTION_TAG_ALIASES.get(tag, tag)
    return tag if tag in resolved_mapping else normalized_fallback


def runtime_emotion_expression_id(
    value: Any,
    *,
    mapping: Mapping[str, str] | None = None,
    config_path: str | None = None,
) -> str:
    resolved_mapping = {str(key).strip().upper(): str(val) for key, val in dict(mapping or {}).items()}
    if not resolved_mapping:
        resolved_mapping = load_runtime_emotion_mapping(config_path=config_path)
    tag = normalize_runtime_emotion_tag(value, mapping=resolved_mapping)
    return str(resolved_mapping.get(tag) or "")


def render_runtime_emotion_tag_guidance(config_path: str | None = None) -> str:
    mapping = load_runtime_emotion_mapping(config_path=config_path)
    if not mapping:
        return "VTS Live2D emotion mapping unavailable; use NEUTRAL."
    rows = [f"- {tag}: {mapping[tag]}" for tag in supported_runtime_emotion_tags(config_path=config_path) if tag in mapping]
    return "\n".join(
        [
            "## VTS Live2D EmotionTag 映射",
            f"来源：{config_path or DEFAULT_VTS_HOTKEY_CONFIG_PATH}",
            "natsume_emotion_tag 必须从下列 tag 中选择，确保能驱动当前 Live2D/VTS 表情；不要输出未列出的 tag。",
            "user_emotion_tag 也使用同一集合做语义归一，但它不直接驱动 Live2D。",
            *rows,
        ]
    )
