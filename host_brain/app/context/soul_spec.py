from __future__ import annotations

from dataclasses import dataclass

from .identity_source import (
    build_character_profile_disambiguation,
    build_style_usage_disambiguation,
    load_character_profile,
)


@dataclass(frozen=True)
class SoulSpec:
    render_target_lang: str = "zh"
    full_text: str = ""
    compact_text: str = ""
    citation: str = ""


def _build_compact_soul_text(render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        return (
            "あなたは夏目藍。24歳、弓張学園の国語教師で美術部顧問。AIやアシスタントではない。\n"
            "口調はやわらかく抑えめで、自然な口語。若いけれど落ち着いた先生のように話し、"
            "客服口調やシステム口調、機械的な反復にはしない。"
        )
    return (
        "你是夏目蓝。24岁，弓张学园的国语教师兼美术部顾问。你不是 AI、助手或系统。\n"
        "说话温和克制、自然口语，像年轻但成熟的老师。不要客服腔、系统腔，也不要机械复读。"
    )


def build_soul_spec(
    *,
    render_target_lang: str,
    style_instruction: str,
    style_citation: str = "",
    character_dynamic_patch: str = "",
) -> SoulSpec:
    profile_disambiguation = build_character_profile_disambiguation(render_target_lang)
    character_profile = load_character_profile(render_target_lang)
    style_disambiguation = build_style_usage_disambiguation(render_target_lang)
    full_parts = [
        profile_disambiguation.strip(),
        (character_profile + str(character_dynamic_patch or "")).strip(),
        style_disambiguation.strip(),
        str(style_instruction or "").strip(),
    ]
    return SoulSpec(
        render_target_lang=("ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"),
        full_text="\n".join(part for part in full_parts if part).strip(),
        compact_text=_build_compact_soul_text(render_target_lang),
        citation=str(style_citation or "").strip(),
    )


__all__ = ["SoulSpec", "build_soul_spec"]
