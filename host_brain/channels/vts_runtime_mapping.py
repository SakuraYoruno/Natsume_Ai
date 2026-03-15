from __future__ import annotations

import logging
from pathlib import Path


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
