from __future__ import annotations

from host_brain.persona.assistant_followup_surface import (
    build_emergency_surface_reply,
    candidate_resolves_filler_meaning,
    candidate_repeats_context_without_progress,
    candidate_resolves_specific_reference,
    context_anchor_variants,
    context_is_repeat_request,
    context_needs_specific_reference,
    extract_context_anchor_terms,
)


def test_context_classification_separates_repeat_from_missing_object() -> None:
    assert context_is_repeat_request("刚才到底讲了什么")
    assert not context_needs_specific_reference("刚才到底讲了什么")

    assert not context_is_repeat_request("知道哪个新秘密")
    assert context_needs_specific_reference("知道哪个新秘密")


def test_anchor_terms_accept_generic_marker_when_modifier_changes() -> None:
    assert extract_context_anchor_terms("知道哪个新秘密甲") == ["新秘密甲"]
    assert "秘密" in context_anchor_variants("知道哪个新秘密甲")

    assert candidate_resolves_specific_reference("哥哥，你问的是哪个秘密呀？", "知道哪个新秘密甲")
    assert not candidate_resolves_specific_reference("哥哥你问哪个呀？", "知道哪个新秘密甲")


def test_context_echo_detection_rejects_circular_meaning_question() -> None:
    assert candidate_repeats_context_without_progress("哥哥，你问这个是什么意思呀？", "这个是什么意思")
    assert not candidate_repeats_context_without_progress("刚才只唔了一下啦，哥哥，你说的是哪句？", "这个是什么意思")


def test_filler_meaning_must_resolve_the_filler_instead_of_vague_loop() -> None:
    assert not candidate_resolves_filler_meaning("哥哥你问那个呀，就是……嗯？", "这个是什么意思", "唔。")
    assert not candidate_resolves_filler_meaning("“唔”就是那个意思啦。", "这个是什么意思", "唔。")
    assert candidate_resolves_filler_meaning("刚刚只是顺口应了一声啦，不是在解释什么。", "这个是什么意思", "唔。")
    assert candidate_resolves_filler_meaning("哥哥你指的是哪件事呀？", "知道哪个秘密", "唔。")
