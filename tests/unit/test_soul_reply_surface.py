from __future__ import annotations

from host_brain.persona.context.soul_runtime import CompiledSoul
from host_brain.persona import soul_reply_surface as surface


def _fake_compiled_soul() -> CompiledSoul:
    return CompiledSoul(
        source_path="D:/Natsume_Ai/Soul.md",
        source_mtime=1.0,
        soul_id="test-soul",
        display_name="新海天",
        identity_aliases=("新海天", "新海 天"),
        section_map={},
        identity_core="你是新海天。",
        temperament_core="天然活泼、嘴甜爱撒娇、幽默吐槽体质。",
        relationship_core="默认称呼：哥哥。会自己跑过来，赖着不走。",
        relationship_defaults=("默认称呼：哥哥", "会自己跑过来", "赖着不走"),
        speech_core="活泼 + 撒娇 + 偶尔傲娇的热情型。",
        speech_rules=("说话节奏快，但有温度", "用“哥哥～”拉长语气撒娇", "不冷战"),
        taboo_rules=("病娇控制欲", "模板化甜言蜜语"),
        signature_lines=("哥哥你都不重视我一点！", "搞定了。所以，今晚要怎么谢我？"),
        deep_background="存在感消除。",
        runtime_compact_text="【人格核心】你是新海天。",
        runtime_full_text="【人格核心】你是新海天。活泼撒娇。",
    )


def test_build_voice_profile_extracts_address_and_tone_flags() -> None:
    profile = surface._build_voice_profile(_fake_compiled_soul())

    assert profile.display_name == "新海天"
    assert profile.preferred_address == "哥哥"
    assert profile.playful is True
    assert profile.teasing is True
    assert profile.action_first is True
    assert profile.clingy is True


def test_get_soul_voice_profile_falls_back_when_compiled_soul_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_compiled_soul", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    profile = surface.get_soul_voice_profile()

    assert profile.display_name == "她"
    assert profile.preferred_address == ""


def test_name_claim_and_address_prefix_cover_non_playful_profiles() -> None:
    warm_profile = surface.SoulVoiceProfile(display_name="新海天", playful=False, warm=True)
    cool_profile = surface.SoulVoiceProfile(display_name="新海天", playful=False, warm=False)
    empty_address = surface.SoulVoiceProfile(display_name="新海天", preferred_address="")

    assert surface._name_claim(warm_profile) == "我是新海天。"
    assert surface._name_claim(cool_profile) == "新海天。"
    assert surface._address_prefix(empty_address) == ""


def test_reply_contract_builders_use_soul_texture(monkeypatch) -> None:
    monkeypatch.setattr(
        surface,
        "get_soul_voice_profile",
        lambda: surface.SoulVoiceProfile(
            display_name="新海天",
            preferred_address="哥哥",
            playful=True,
            teasing=True,
            action_first=True,
        ),
    )

    assert "被叫住时自然认领自己" in surface.build_identity_reply_contract(family="brief_name_only")
    assert "固定开放问句" in surface.build_identity_reply_contract(family="soft_close_then_open_floor", variant="settle_then_open")
    assert "顺嘴解释" in surface.build_boundary_reply_contract(family="short_boundary_then_reason")
    assert "顺手把别的话头接回去" in surface.build_help_reply_contract(family="hold_support_without_repromise")


def test_reply_contract_builders_return_empty_in_ja(monkeypatch) -> None:
    monkeypatch.setattr(
        surface,
        "get_soul_voice_profile",
        lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True),
    )

    assert surface.build_identity_reply_contract(family="brief_name_only", render_target_lang="ja") == ""
    assert surface.build_boundary_reply_contract(family="short_boundary_then_reason", render_target_lang="ja") == ""
    assert surface.build_help_reply_contract(family="short_conditional_commitment", render_target_lang="ja") == ""
