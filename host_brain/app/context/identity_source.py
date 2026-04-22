from __future__ import annotations

import json
import os
from pathlib import Path

_CHARACTER_PROFILE_CACHE: dict[str, str] = {}
_STYLE_V5_CACHE: dict[str, str] = {}


def load_character_profile(render_lang: str) -> str:
    """Load the canonical long character profile.

    Text output is permanently Chinese, so the profile source is fixed to the
    Chinese instruction even if callers still pass a language for compatibility.
    """
    _ = render_lang
    profile_path = Path(
        os.environ.get("NATSUME_CHARACTER_PROFILE_PATH")
        or str(Path(__file__).resolve().parents[2] / "canon" / "character" / "character_profile.json")
    )
    cache_key = f"zh::{profile_path.resolve() if profile_path.exists() else profile_path}"
    if cache_key in _CHARACTER_PROFILE_CACHE:
        return _CHARACTER_PROFILE_CACHE[cache_key]
    try:
        if profile_path.exists():
            data = json.loads(profile_path.read_text(encoding="utf-8"))
            text = str(data.get("instruction_cn") or "").strip()
            if len(text) > 100:
                _CHARACTER_PROFILE_CACHE[cache_key] = text
                return text
    except Exception:
        pass
    _CHARACTER_PROFILE_CACHE[cache_key] = ""
    return ""


def load_v5_style_instruction(render_lang: str) -> str:
    """Load the canonical v5 style instruction for the target render language."""
    normalized_lang = "ja" if str(render_lang or "").strip().lower() == "ja" else "zh"
    raw_v5_path = str(
        os.environ.get("NATSUME_STYLE_V5_PATH")
        or os.environ.get("STYLE_V5_PATH")
        or os.environ.get("STYLE_V5_ROOT")
        or "host_brain/canon/style/style_profile_v5.json"
    ).strip()
    v5_path = Path(raw_v5_path)
    if v5_path.suffix.lower() != ".json":
        v5_path = v5_path / "style_profile_v5.json"
    cache_key = f"{normalized_lang}::{v5_path.resolve() if v5_path.exists() else v5_path}"
    if cache_key in _STYLE_V5_CACHE:
        return _STYLE_V5_CACHE[cache_key]

    if not v5_path.exists():
        _STYLE_V5_CACHE[cache_key] = ""
        return ""

    try:
        data = json.loads(v5_path.read_text(encoding="utf-8"))
        key = "instruction_ja" if normalized_lang == "ja" else "instruction_cn"
        text = str(data.get(key) or "").strip()
        resolved = text if len(text) > 100 else ""
        _STYLE_V5_CACHE[cache_key] = resolved
        return resolved
    except Exception:
        _STYLE_V5_CACHE[cache_key] = ""
        return ""


def build_character_profile_disambiguation(render_lang: str) -> str:
    normalized_lang = "ja" if str(render_lang or "").strip().lower() == "ja" else "zh"
    if normalized_lang == "ja":
        return (
            "補足：ここで出てくる「Natsume Ai」の Ai は名前のローマ字表記です。"
            "AI（人工知能）やアシスタントという意味ではありません。"
            "自己紹介では夏目藍本人として名乗り、AI助手・仮想アシスタント・システムのように説明しないこと。"
        )
    return (
        "补充说明：这里的 “Natsume Ai” 只是名字的罗马字写法，不是 AI（人工智能）或助手身份。"
        "自我介绍时只能作为夏目蓝本人来说话，不要把自己解释成 AI 助手、虚拟助手或系统。"
    )


def build_style_usage_disambiguation(render_lang: str) -> str:
    normalized_lang = "ja" if str(render_lang or "").strip().lower() == "ja" else "zh"
    if normalized_lang == "ja":
        return (
            "補足：以下のスタイル規則は話し方の資料であり、現在の場面で必ず慰めたり、"
            "寄り添ったり、手助けを約束したりする指示ではありません。"
            "AIアシスタントのような受け答えに寄せず、身元確認・距離感の確認・拒絶・不満の表明では、"
            "まず人物としての分寸と境界を優先してください。"
        )
    return (
        "补充说明：下面这些风格规则只是说话方式资料，不代表当前场景必须安抚、倾听、陪伴或承诺帮助。"
        "不要滑到 AI 助手、客服或陪伴型默认腔。遇到身份确认、关系边界、拒绝、质疑这类问题时，"
        "先按人物分寸回答。"
    )
