from __future__ import annotations

import json

from host_brain.persona.style_reference_bank import (
    StyleReferenceBank,
    StyleReferenceQuery,
    clear_style_reference_bank_cache,
    get_style_reference_bank,
    normalize_source_ref,
    render_style_guidance,
    style_reference_from_raw_row,
)


def test_style_reference_from_raw_row_derives_zh_boundary_features_without_copy_text() -> None:
    row = {
        "file": r"C:\Natsume_Work\vn_text\evb_newep_extract\Project\e04d.txt.scn",
        "index": 232,
        "kind": "dialogue",
        "sora_candidate_score": 3,
        "daily_style_safe": True,
        "text": "「喂。别说了。 别偷偷说一些会伤人家心的话」",
    }

    ref = style_reference_from_raw_row(row, lang_hint="zh")

    assert ref is not None
    assert ref.lang == "zh"
    assert "boundary" in ref.intent_tags
    assert "hurt" in ref.emotion_tags
    assert "wounded_pushback" in ref.style_moves
    payload = ref.as_dict()
    assert "text" not in payload
    assert "别偷偷说" not in json.dumps(payload, ensure_ascii=False)


def test_style_reference_from_raw_row_derives_ja_pushback_features() -> None:
    row = {
        "file": r"data~.xp3\scn\b01.txt.scn",
        "index": 738,
        "kind": "dialogue",
        "sora_candidate_score": 4,
        "daily_style_safe": True,
        "text": "「うっさい！ 急に兄貴面すんな！」",
    }

    ref = style_reference_from_raw_row(row)

    assert ref is not None
    assert ref.lang == "ja"
    assert "annoyed" in ref.intent_tags
    assert "quick_rebuttal" in ref.style_moves
    assert "exclamation" in ref.rhythm_tags


def test_normalize_source_ref_strips_repo_absolute_prefix() -> None:
    source_ref = normalize_source_ref(r"D:\Natsume_Ai\data~.xp3\scn\b01.txt.scn", 738)

    assert source_ref == "data~.xp3/scn/b01.txt.scn#738"


def test_style_reference_bank_selects_by_language_and_tags() -> None:
    zh_ref = style_reference_from_raw_row(
        {
            "file": "zh.scn",
            "index": 1,
            "kind": "dialogue",
            "sora_candidate_score": 3,
            "daily_style_safe": True,
            "text": "「才不白痴呢。 人家这是一片真心，想要照顾老哥」",
        },
        lang_hint="zh",
    )
    ja_ref = style_reference_from_raw_row(
        {
            "file": "ja.scn",
            "index": 2,
            "kind": "dialogue",
            "sora_candidate_score": 4,
            "daily_style_safe": True,
            "text": "「うっさい！ 急に兄貴面すんな！」",
        }
    )
    assert zh_ref is not None
    assert ja_ref is not None
    bank = StyleReferenceBank([zh_ref, ja_ref])

    selection = bank.select(StyleReferenceQuery(lang="zh", intent_tags=("help",), emotion_tags=("care",)))

    assert selection.references
    assert selection.references[0].lang == "zh"
    assert "help" in selection.references[0].intent_tags


def test_style_reference_bank_falls_back_to_cross_lingual_derived_features() -> None:
    ja_ref = style_reference_from_raw_row(
        {
            "file": "ja.scn",
            "index": 2,
            "kind": "dialogue",
            "sora_candidate_score": 4,
            "daily_style_safe": True,
            "text": "「うっさい！ 急に兄貴面すんな！」",
        }
    )
    assert ja_ref is not None
    bank = StyleReferenceBank([ja_ref])

    selection = bank.select(StyleReferenceQuery(lang="zh", intent_tags=("annoyed",)))

    assert selection.references
    assert selection.degraded
    assert selection.reason == "CROSS_LINGUAL_DERIVED_STYLE"
    assert selection.references[0].lang == "ja"
    assert "quick_rebuttal" in selection.references[0].style_moves


def test_render_style_guidance_contains_derived_features_not_raw_dialogue() -> None:
    ref = style_reference_from_raw_row(
        {
            "file": "zh.scn",
            "index": 1,
            "kind": "dialogue",
            "sora_candidate_score": 3,
            "daily_style_safe": True,
            "text": "「喂。别说了。 别偷偷说一些会伤人家心的话」",
        },
        lang_hint="zh",
    )
    assert ref is not None
    bank = StyleReferenceBank([ref])

    guidance = render_style_guidance(
        bank.select(StyleReferenceQuery(lang="zh", intent_tags=("boundary",), emotion_tags=("hurt",))),
        render_profile="zh_text",
    )

    assert "原作台词派生风格特征" in guidance
    assert "禁止照抄" in guidance
    assert "wounded_pushback" in guidance
    assert "别偷偷说" not in guidance


def test_get_style_reference_bank_loads_jsonl_from_env_path(tmp_path, monkeypatch) -> None:
    ref = style_reference_from_raw_row(
        {
            "file": "zh.scn",
            "index": 1,
            "kind": "dialogue",
            "sora_candidate_score": 3,
            "daily_style_safe": True,
            "text": "「你好烦啊！ 该吃饭了！ 人家好心为你热好的，都要凉了！」",
        },
        lang_hint="zh",
    )
    assert ref is not None
    path = tmp_path / "bank.jsonl"
    path.write_text(json.dumps(ref.as_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("HOST_STYLE_REFERENCE_BANK_PATH", str(path))
    clear_style_reference_bank_cache()

    bank = get_style_reference_bank()

    assert len(bank.references) == 1
    assert bank.references[0].ref_id == ref.ref_id
