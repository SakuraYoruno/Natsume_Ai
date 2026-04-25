from __future__ import annotations

from host_brain.app.context.soul_runtime import CompiledSoul
from host_brain.persona import soul_reply_surface as surface


class _MemoryStoreStub:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self.remembered: list[tuple[str, str]] = []

    def sample(self, *, key: str, exclude=None) -> str:  # noqa: ANN001
        _ = key, exclude
        return self._text

    def remember(self, *, key: str, text: str, **kwargs) -> None:  # noqa: ANN003
        _ = kwargs
        self.remembered.append((key, text))


class _ExcludeAwareMemoryStoreStub:
    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)

    def sample(self, *, key: str, exclude=None) -> str:  # noqa: ANN001
        _ = key
        excluded = {str(item or "").strip() for item in list(exclude or []) if str(item or "").strip()}
        for item in self._texts:
            if item not in excluded:
                return item
        return self._texts[0] if self._texts else ""


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


def test_build_phatic_fallback_text_covers_variants(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True))

    assert surface.build_phatic_fallback_text(family="phatic_wave", variant="greeting") == ""
    assert surface.build_phatic_fallback_text(family="phatic_wave", variant="presence") == ""
    assert surface.build_phatic_fallback_text(family="playful_wave", variant="") == ""
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="", playful=False))
    assert surface.build_phatic_fallback_text(family="other", variant="") == ""


def test_build_image_social_fallback_text_covers_variants(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True))

    assert surface.build_image_social_fallback_text(family="soft_emotion_ack", variant="distress_image") == ""
    assert surface.build_image_social_fallback_text(family="soft_emotion_ack", variant="backoff_hold") == ""
    assert surface.build_image_social_fallback_text(family="cute_image_ack", variant="cute_ping") == ""
    assert surface.build_image_social_fallback_text(family="tease_image_ack", variant="deny_nudge") == ""
    assert surface.build_image_social_fallback_text(family="tease_image_ack", variant="") == ""


def test_build_boundary_and_help_fallback_texts_cover_specific_and_default(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True))

    assert surface.build_boundary_fallback_text(family="short_boundary_then_reason", variant="plain_reason") == ""
    assert surface.build_boundary_fallback_text(family="soft_close_boundary", variant="settle_repeat") == ""
    assert surface.build_boundary_fallback_text(family="other", variant="") == ""
    assert surface.build_help_fallback_text(family="short_conditional_commitment", variant="plain_conditional") == ""
    assert surface.build_help_fallback_text(family="steady_commitment_then_wait", variant="quiet_hold") == ""
    assert surface.build_help_fallback_text(family="other", variant="") == ""
    assert surface.build_repair_fallback_text() == ""


def test_build_identity_fallback_and_hints_cover_major_families(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True, teasing=True))

    assert surface.build_identity_fallback_text(family="brief_name_only", variant="plain_name") == ""
    assert surface.build_identity_fallback_text(family="ack_repeat_then_light_probe", variant="confirm_real_question") == ""
    assert surface.build_identity_fallback_text(family="ack_name_then_wait", variant="") == ""
    assert surface.build_identity_fallback_text(family="hold_name_then_open_floor", variant="open_other_topic") == ""
    assert surface.build_identity_fallback_text(family="soft_close_then_open_floor", variant="settle_then_open") == ""
    assert "熟人间撒娇" in surface.build_identity_naturalization_hint(family="soft_close_then_open_floor")
    assert "顺手认下自己" in surface.build_identity_naturalization_hint(family="brief_name_only")
    assert "顺嘴解释" in surface.build_boundary_reply_contract(family="short_boundary_then_reason")
    assert "顺手把别的话头接回去" in surface.build_help_reply_contract(family="hold_support_without_repromise")
    assert "被叫住时自然认领自己" in surface.build_identity_reply_contract(family="brief_name_only")
    assert "固定开放问句" in surface.build_identity_reply_contract(family="soft_close_then_open_floor", variant="settle_then_open")


def test_build_image_social_and_help_naturalization_hints(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True))

    assert "顺手哄一句" in surface.build_image_social_naturalization_hint(family="soft_emotion_ack")
    assert "可爱表情蹭了一下" in surface.build_image_social_naturalization_hint(family="cute_image_ack")
    assert "轻轻逗一句" in surface.build_image_social_naturalization_hint(family="tease_image_ack")
    assert "顺着图里的语气自然接一句" in surface.build_image_social_naturalization_hint(family="other")
    assert "正式承诺" in surface.build_help_naturalization_hint(family="soft_reassure_then_open_floor")
    assert "嘴上轻轻应下来" in surface.build_help_naturalization_hint(family="short_conditional_commitment")
    assert "划界线" in surface.build_boundary_naturalization_hint(family="soft_close_boundary")
    assert "顺手解释一句" in surface.build_boundary_naturalization_hint(family="short_boundary_then_reason")


def test_contract_and_hint_builders_return_empty_in_ja(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True))

    assert surface.build_phatic_fallback_text(family="phatic_wave", render_target_lang="ja") == ""
    assert surface.build_image_social_fallback_text(family="soft_emotion_ack", render_target_lang="ja") == ""
    assert surface.build_boundary_fallback_text(family="short_boundary_then_reason", render_target_lang="ja") == ""
    assert surface.build_help_fallback_text(family="short_conditional_commitment", render_target_lang="ja") == ""
    assert surface.build_repair_fallback_text(render_target_lang="ja") == ""
    assert surface.build_identity_fallback_text(family="brief_name_only", render_target_lang="ja") == ""
    assert surface.build_identity_reply_contract(family="brief_name_only", render_target_lang="ja") == ""
    assert surface.build_boundary_reply_contract(family="short_boundary_then_reason", render_target_lang="ja") == ""
    assert surface.build_help_reply_contract(family="short_conditional_commitment", render_target_lang="ja") == ""
    assert surface.build_identity_naturalization_hint(family="brief_name_only", render_target_lang="ja") == ""
    assert surface.build_boundary_naturalization_hint(family="short_boundary_then_reason", render_target_lang="ja") == ""
    assert surface.build_help_naturalization_hint(family="short_conditional_commitment", render_target_lang="ja") == ""
    assert surface.build_image_social_naturalization_hint(family="soft_emotion_ack", render_target_lang="ja") == ""


def test_build_deterministic_primary_reply_text_returns_blank_for_non_primary_topics(monkeypatch) -> None:
    monkeypatch.setattr(surface, "get_soul_voice_profile", lambda: surface.SoulVoiceProfile(display_name="新海天", preferred_address="哥哥", playful=True))
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub("哥哥，怎么又问起这个啦？我是新海天呀。"))

    identity_plan = type(
        "IdentityPlanStub",
        (),
        {
            "primary_topic": "identity",
            "tactic_family": "ack_repeat_then_light_probe",
            "tactic_variant": "confirm_real_question",
            "response_form_reason": "default_text",
        },
    )()
    plan = type(
        "PlanStub",
        (),
        {
            "primary_topic": "identity",
            "tactic_family": "brief_name_only",
            "tactic_variant": "plain_name",
            "response_form_reason": "default_text",
        },
    )()

    assert "新海天" in surface.build_deterministic_primary_reply_text(plan=identity_plan, render_target_lang="zh")
    boundary_plan = type(
        "BoundaryPlanStub",
        (),
        {
            "primary_topic": "motivation",
            "tactic_family": "short_boundary_then_reason",
            "tactic_variant": "plain_reason",
            "response_form_reason": "default_text",
        },
    )()
    help_plan = type(
        "HelpPlanStub",
        (),
        {
            "primary_topic": "help_commitment",
            "tactic_family": "short_conditional_commitment",
            "tactic_variant": "plain_conditional",
            "response_form_reason": "default_text",
        },
    )()
    reject_plan = type(
        "RejectPlanStub",
        (),
        {
            "primary_topic": "rejection",
            "tactic_family": "hold_repair_space",
            "tactic_variant": "plain",
            "response_form_reason": "default_text",
        },
    )()
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub("哥哥，看见了我也不会装作没看到。"))
    assert surface.build_deterministic_primary_reply_text(plan=boundary_plan, render_target_lang="zh") == ""
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub("哥哥，真有事就说呀，我会回应的。"))
    assert surface.build_deterministic_primary_reply_text(plan=help_plan, render_target_lang="zh") == ""
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub("哥哥，好嘛，我收一点。你要是不想听这个，我们就先停在这。"))
    assert surface.build_deterministic_primary_reply_text(plan=reject_plan, render_target_lang="zh") == ""
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub(""))
    plan.primary_topic = "default_smalltalk"
    assert surface.build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh") == ""


def test_build_deterministic_primary_reply_text_avoids_repeating_previous_assistant_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_reply_surface_memory_store",
        lambda: _ExcludeAwareMemoryStoreStub(
            [
                "哥哥，怎么又问起这个啦？我是新海天呀。",
                "哥哥，我是新海天呀。你怎么还确认这个。",
            ]
        ),
    )
    plan = type(
        "IdentityPlanStub",
        (),
        {
            "primary_topic": "identity",
            "tactic_family": "ack_repeat_then_light_probe",
            "tactic_variant": "confirm_real_question",
            "response_form_reason": "default_text",
        },
    )()

    text = surface.build_deterministic_primary_reply_text(
        plan=plan,
        render_target_lang="zh",
        exclude_texts=("哥哥，怎么又问起这个啦？我是新海天呀。",),
    )

    assert text == "哥哥，我是新海天呀。你怎么还确认这个。"


def test_build_deterministic_primary_reply_text_falls_back_to_turn_history(monkeypatch) -> None:
    memory_store = _MemoryStoreStub("")
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: memory_store)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_conversation_turn_store",
        lambda: object(),
    )
    monkeypatch.setattr(
        "host_brain.persona.reply_surface_memory.sample_reply_surface_from_turn_history",
        lambda **kwargs: "哥哥，怎么又问起这个啦？我是新海天呀。",
    )
    plan = type(
        "IdentityPlanStub",
        (),
        {
            "primary_topic": "identity",
            "tactic_family": "ack_repeat_then_light_probe",
            "tactic_variant": "confirm_real_question",
            "response_form_reason": "default_text",
        },
    )()

    text = surface.build_deterministic_primary_reply_text(plan=plan, render_target_lang="zh")

    assert text == "哥哥，怎么又问起这个啦？我是新海天呀。"
    assert memory_store.remembered


def test_reply_surface_sample_guard_rejects_polluted_assistant_followup_shapes() -> None:
    assert surface.is_reply_surface_sample_disallowed(primary_topic="assistant_reply_followup", text="")
    assert surface.is_reply_surface_sample_disallowed(primary_topic="assistant_reply_followup", text="唔。")
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，刚才那句我没接住，不是在敷衍你。你是在问前面那截吧？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，你是不是想知道我刚才那个唔是啥意思？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，你问这个是什么意思呀？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥你问哪个呀？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，你问刚才那个短回复呀？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，刚才那声有点短。",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，刚才那声“唔”是没什么事啦。",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥刚才那声不是卡了吗？你刚才想说什么来着？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥刚才只是卡了一下啦！",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="“唔”就是……嗯，那个意思啦！",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥你问那个呀，就是……嗯？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥你刚才那个“唔”是在思考什么呀？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="唔？哥哥你刚才是不是被什么吓到了呀？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="抱歉，我不太清楚。",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="assistant_reply_followup",
        text="哥哥，是想说知道什么来着？",
    )
    assert surface.is_reply_surface_sample_disallowed(
        primary_topic="greeting",
        text="唔。",
    )


def test_assistant_followup_primary_reply_skips_semantic_fallback_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.dependencies.get_reply_surface_memory_store", lambda: _MemoryStoreStub(""))
    monkeypatch.setattr("host_brain.app.dependencies.get_conversation_turn_store", lambda: object())
    monkeypatch.setattr(
        "host_brain.persona.reply_surface_memory.sample_reply_surface_from_turn_history",
        lambda **kwargs: "",
    )
    plan = type(
        "AssistantFollowupPlanStub",
        (),
        {
            "primary_topic": "assistant_reply_followup",
            "tactic_family": "explain_previous_reply",
            "tactic_variant": "clarify_meaning",
            "response_form_reason": "default_text",
            "reply_contract": "上一条助手回复前接的用户话是“知道哪个秘密”。上一条助手回复是“唔。”。本轮必须围绕这段上下文补清楚。",
        },
    )()

    text = surface.build_deterministic_primary_reply_text(
        plan=plan,
        render_target_lang="zh",
        allow_semantic_fallback=False,
    )

    assert text == ""
