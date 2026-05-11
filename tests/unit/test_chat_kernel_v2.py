from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

from host_brain.app.chat_kernel_v2 import (
    ChatKernelRequest,
    _dedupe_repeated_galgame_address,
    _select_galgame_soft_candidate,
    build_chat_kernel_messages,
    get_chat_kernel_v2,
)
import host_brain.app.context.interaction_facts_plane as interaction_facts_plane_module
from host_brain.app.context.interaction_facts_plane import InteractionFactsPlane
from host_brain.persona.semantic_reranker import SemanticCandidate
from host_brain.persona.text_quality_gate import extract_sendable_dialogue, get_text_quality_gate
from host_brain.persona.text_quality_gate import TextQualityGateResult
from host_brain.persona.style_reference_bank import clear_style_reference_bank_cache, style_reference_from_raw_row


@pytest.fixture(autouse=True)
def _guided_prompt_mode_for_legacy_prompt_contract_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    # Most existing tests pin the old guided prompt contract. Live/default
    # normal chat now uses plain_soul; dedicated tests below cover that path.
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "guided")


def _soul() -> SimpleNamespace:
    return SimpleNamespace(
        runtime_full_text="【人格定位】你是新海天。活泼、会接嘴、不是客服。",
        zh_text_style_anchor="你是新海天。中文私聊要像义妹，会接嘴、会拌嘴，不要温顺客服腔。",
        ja_voice_style_anchor="あなたは新海天。短い自然な日本語で、少し生意気に返す。",
    )


def _facts() -> InteractionFactsPlane:
    return InteractionFactsPlane(
        user_message="在吗",
        history_turns=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "尼尼，干嘛突然这么正经。"}],
        relationship_stage="FRIEND",
        relationship_state={"stage_label": "关系好的义兄妹", "playfulness": 0.5, "trust": 0.4},
        affect_state={"emotion_tag": "NEUTRAL", "warmth_bias": 0.4, "withdraw_bias": 0.1},
        drive_state={"playfulness_drive": 0.3, "care_drive": 0.2},
        conversation_interpretation={"literal_topic": "presence_ping", "interaction_move": "phatic", "user_stance": "neutral"},
    )


def test_galgame_soft_selector_prefers_clean_tie_candidate() -> None:
    quality = TextQualityGateResult(accepted=True, normalized_text="ok", quality_score=1.0)
    result = _select_galgame_soft_candidate(
        [
            SemanticCandidate(
                text="一上来就嫌我烦？哼，那谁先找谁说话的？",
                quality=quality,
                attempt=0,
                metadata={
                    "galgame_score": {
                        "score": 100.0,
                        "hard_fail": False,
                        "reason_codes": ["TOO_MANY_QUESTIONS"],
                        "penalties": ["questions:2>1"],
                    }
                },
            ),
            SemanticCandidate(
                text="哼，烦就烦吧，反正尼尼也没真赶我走。",
                quality=quality,
                attempt=1,
                metadata={
                    "galgame_score": {
                        "score": 100.0,
                        "hard_fail": False,
                        "reason_codes": [],
                        "penalties": [],
                    }
                },
            ),
        ]
    )

    assert result.accepted
    assert result.selected_index == 1


def test_dedupe_repeated_galgame_address_keeps_first_address() -> None:
    assert (
        _dedupe_repeated_galgame_address("当然是在等尼尼开口啊，不然还能干嘛！怎么，尼尼想我了？")
        == "当然是在等尼尼开口啊，不然还能干嘛！怎么，你想我了？"
    )
    assert (
        _dedupe_repeated_galgame_address("哼，才多久没见啊，尼尼就开始想我了？不过既然尼尼都这么说了…")
        == "哼，才多久没见啊，尼尼就开始想我了？不过既然你都这么说了…"
    )


def test_build_chat_kernel_messages_do_not_include_reply_policy_fields() -> None:
    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
        )
    )
    joined = "\n".join(item["content"] for item in messages)
    assert "reply_contract" not in joined
    assert "tactic_family" not in joined
    assert "trajectory_stage" not in joined
    assert "correction_directive" not in joined
    assert "control_line" not in joined
    assert "你是新海天" in joined


def test_build_chat_kernel_messages_plain_soul_is_default_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", raising=False)

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = "\n".join(item["content"] for item in messages if item["role"] == "system")
    assert messages[0]["content"] == _soul().runtime_full_text
    assert "这是裸 Soul 文本内核" in system_prompt
    assert "【本轮发话框架】" not in system_prompt
    assert "【精选正向风格抓手】" not in system_prompt
    assert "不要 JSON" in system_prompt


def test_build_chat_kernel_messages_plain_structured_keeps_raw_soul_and_json_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_structured")

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = "\n".join(item["content"] for item in messages if item["role"] == "system")
    assert messages[0]["content"] == _soul().runtime_full_text
    assert "裸 Soul 的结构化文本内核" in system_prompt
    assert '"text_zh"' in system_prompt
    assert "emotion/action/intensity 只给 VTS/Live2D" in system_prompt
    assert "【本轮发话框架】" not in system_prompt
    assert "【精选正向风格抓手】" not in system_prompt


def test_build_chat_kernel_messages_wrap_zh_input_for_ja_voice() -> None:
    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="voice",
            render_profile="ja_voice",
        )
    )

    assert messages[-1]["role"] == "user"
    assert "返答は日本語の音声台詞だけ" in messages[-1]["content"]
    assert "ユーザー原文を引用しない" in messages[-1]["content"]
    assert "在吗" not in messages[-1]["content"]
    assert "只写日语" in messages[0]["content"]


def test_build_chat_kernel_messages_include_negative_affect_facts() -> None:
    facts = InteractionFactsPlane(
        user_message="不用你管",
        relationship_state={"stage_label": "关系好的义兄妹", "trust": 0.6},
        affect_state={
            "emotion_tag": "HURT",
            "warmth_bias": 0.24,
            "withdraw_bias": 0.48,
            "irritation_bias": 0.32,
            "relational_read": "尼尼把她主动帮忙推开，像是在闹别扭或刺她。",
            "emotion_reason": "主动靠近被推开，先受伤，再嘴硬。",
            "response_emotion": "受伤、赌气、嘴硬。",
            "emotional_boundary": "别卑微讨好，别甜腻哄人。",
        },
        emotional_act={
            "act_kind": "rejection_hurt",
            "trigger": "主动帮忙被一句话推开，先受伤再嘴硬。",
            "primary_emotion": "hurt_sulk",
            "stance": "sulking_half_step_back",
            "intensity": 0.72,
            "relational_read": "她主动靠近/帮忙被推开了，这不是普通拒绝。",
            "allowed_moves": ["短促表现被推开的刺痛", "嘴硬或赌气", "保留仍在意的尾巴"],
            "forbidden_moves": ["客服式尊重选择", "卑微讨好", "甜腻说会伤心哦"],
            "zh_directive": "不要把“不用你管”当普通拒绝。她应先被刺到。",
        },
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="不用你管",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )
    system_prompt = messages[0]["content"]

    assert "情绪事实：HURT" in system_prompt
    assert "【本轮发话框架】" in system_prompt
    assert "场景：主动靠近被推开" in system_prompt
    assert "情绪冲动：先被刺到" in system_prompt
    assert "关系动作：嘴上顶回去" in system_prompt
    assert "前半句轻刺" in system_prompt
    assert "顶嘴不要变成轻视、断联、撂挑子或粗硬挑战" in system_prompt
    assert "客服式尊重" in system_prompt
    assert "不要旁白、心理说明、动作说明" in system_prompt
    assert "一到两句" in system_prompt
    assert "【长期文本标准】" in system_prompt
    assert "视觉小说式二次元义妹私聊台词感" in system_prompt
    assert "不要为了像真人而把新海天压平成" in system_prompt
    assert "二次元义妹的私聊台词" in system_prompt
    assert "AI 角色扮演稿" in system_prompt
    assert "不要用“平时/以前/每次/总是/又”替用户编长期习惯" in system_prompt
    assert "不要用廉价熟人梗填空" in system_prompt
    assert "不要家长式生活照顾" in system_prompt
    assert "只接当前这一拍" in system_prompt
    assert "主动靠近被推开" in system_prompt
    assert "退场或闭嘴" in system_prompt


def test_build_chat_kernel_messages_include_boundary_hurt_directive() -> None:
    facts = InteractionFactsPlane(
        user_message="你为什么要帮我，我们很熟吗",
        relationship_state={"stage_label": "关系好的义兄妹", "trust": 0.6},
        emotional_act={
            "act_kind": "boundary_hurt",
            "trigger": "被问熟不熟刺到。",
            "primary_emotion": "hurt_anger",
            "stance": "wounded_pushback",
            "intensity": 0.66,
            "relational_read": "这不是普通提问，而是亲近关系里突然见外、会刺痛人的话。",
            "allowed_moves": ["先承认被刺到或短暂停顿", "把见外顶回去"],
            "forbidden_moves": ["复述“我们很熟吗”", "说住在一起所以熟", "用“我是你妹妹/一家人”证明关系"],
            "zh_directive": "把这句当成伤人的见外话，不是求证熟不熟。重点是“你怎么把我往外推”。",
        },
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "场景：被尼尼突然见外刺到" in system_prompt
    assert "情绪冲动：先停一下" in system_prompt
    assert "关系动作：把尼尼往回拉" in system_prompt
    assert "证明关系" in system_prompt
    assert "靠身份压人" in system_prompt
    assert "退场或撂挑子" in system_prompt


def test_build_chat_kernel_messages_include_social_act_directive() -> None:
    facts = InteractionFactsPlane(
        user_message="好无聊",
        social_act={
            "act_kind": "boredom_join",
            "scene": "daily_boredom",
            "stance": "playful_join",
            "should_do": ["主动把气氛接起来", "把自己放进互动里"],
            "avoid": ["把球踢回给用户", "问想做什么", "讲笑话保证逗笑"],
            "zh_directive": "接住“无聊”，主动把气氛带起来；不要问清单。",
        },
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="好无聊",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "【你是谁 / 说话底色】" in system_prompt
    assert "【本轮怎么接话】" in system_prompt
    assert "【输出一句】" in system_prompt
    assert "两个人的轻松小互动" in system_prompt
    assert "内部发话拍点" in system_prompt
    assert "emotion=playful_join" in system_prompt
    assert "运行提示：回应“无聊”" in system_prompt
    assert "【精选正向风格抓手】" not in system_prompt
    assert "【本轮目标】" not in system_prompt
    assert "消磨时间" not in system_prompt


def test_build_chat_kernel_messages_include_presence_scene_contract() -> None:
    facts = InteractionFactsPlane(
        user_message="在吗",
        conversation_interpretation={"literal_topic": "presence_ping"},
        social_act={
            "act_kind": "presence_ack",
            "scene": "presence_ping",
            "zh_directive": "回应在场就够了，别问需求。",
        },
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "【你是谁 / 说话底色】" in system_prompt
    assert "【本轮怎么接话】" in system_prompt
    assert "明确在场" in system_prompt
    assert "在/回你了" in system_prompt
    assert "别喊第二遍" in system_prompt
    assert "语义事实：“在吗”只是确认在场" in system_prompt
    assert "emotion=attentive" in system_prompt
    assert "【本轮发话框架】" not in system_prompt
    assert "【精选正向风格抓手】" not in system_prompt
    assert "8 到 32 个中文字" in system_prompt
    assert "不编现实动作、历史习惯或长期相处细节" in system_prompt


def test_build_chat_kernel_messages_include_greeting_scene_contract() -> None:
    facts = InteractionFactsPlane(
        user_message="你好",
        conversation_interpretation={"literal_topic": "greeting"},
        social_act={"act_kind": "greeting_reply", "scene": "greeting"},
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你好",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "【你是谁 / 说话底色】" in system_prompt
    assert "【本轮怎么接话】" in system_prompt
    assert "把“你好”这种礼貌招呼拉回兄妹私聊" in system_prompt
    assert "嫌它太正经" in system_prompt
    assert "【精选正向风格抓手】" not in system_prompt
    assert messages[-1]["content"] == "你好"


def test_build_chat_kernel_messages_normalize_english_greeting_before_generation() -> None:
    facts = InteractionFactsPlane(
        user_message="hello",
        conversation_interpretation={"literal_topic": "greeting"},
        social_act={"act_kind": "greeting_reply", "scene": "greeting"},
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="hello",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    joined = "\n".join(item["content"] for item in messages).lower()
    assert messages[-1]["content"] == "（用户发来一个偏正式的短招呼；只回应招呼带来的距离感，不引用原词。）"
    assert "hello" not in joined
    assert "hey" not in joined
    assert "洋文" not in joined


def test_build_chat_kernel_messages_preserve_light_social_user_message() -> None:
    samples = [
        ("在吗", "presence_ack"),
        ("好无聊", "boredom_join"),
        ("等我一下", "wait_hold"),
        ("我回来了", "back_return"),
        ("我先忙会儿", "busy_space"),
    ]
    for user_message, act_kind in samples:
        facts = InteractionFactsPlane(user_message=user_message, social_act={"act_kind": act_kind})
        messages = build_chat_kernel_messages(
            ChatKernelRequest(
                request_id="req",
                session_id="sess",
                user_message=user_message,
                compiled_soul=_soul(),  # type: ignore[arg-type]
                interaction_facts=facts,
                response_form="text",
                render_profile="zh_text",
            )
        )
        assert messages[-1]["content"] == user_message


def test_build_chat_kernel_messages_preserve_followup_user_message_and_move_hint() -> None:
    facts = InteractionFactsPlane(
        user_message="为什么",
        history_turns=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "你好也太客气啦。"},
        ],
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="为什么",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "followup_target=上一句assistant回复" in system_prompt
    assert "语义事实：“为什么”是在追问上一句为什么那样说" in system_prompt
    assert "emotion=slightly_embarrassed_tease" in system_prompt
    assert "用户在追问你上一句为什么这么说" not in system_prompt
    assert messages[-1]["content"] == "为什么"


def test_build_chat_kernel_messages_include_good_news_scene_contract() -> None:
    facts = InteractionFactsPlane(
        user_message="终于跑通了",
        social_act={
            "act_kind": "good_news_share",
            "scene": "good_news",
            "zh_directive": "把“跑通/搞定”当成果分享。",
        },
    )

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="终于跑通了",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "【你是谁 / 说话底色】" in system_prompt
    assert "回应跑通/搞定的好消息" in system_prompt
    assert "卡点/过程待会儿说给我听" in system_prompt
    assert "运行提示：把“跑通/搞定”当成果分享。" in system_prompt
    assert "那必须是你" not in system_prompt
    assert "太厉害了" not in system_prompt


def test_build_chat_kernel_messages_include_wait_back_busy_contracts() -> None:
    samples = [
        ("等我一下", "wait_hold", "答应等一下", "还要等多久"),
        ("我回来了", "back_return", "回应尼尼回来了", "丢下我"),
        ("我先忙会儿", "busy_space", "让尼尼先忙", "忙完记得找我"),
    ]
    for user_message, act_kind, expected, forbidden in samples:
        facts = InteractionFactsPlane(user_message=user_message, social_act={"act_kind": act_kind})
        messages = build_chat_kernel_messages(
            ChatKernelRequest(
                request_id="req",
                session_id="sess",
                user_message=user_message,
                compiled_soul=_soul(),  # type: ignore[arg-type]
                interaction_facts=facts,
                response_form="text",
                render_profile="zh_text",
            )
        )
        system_prompt = messages[0]["content"]
        assert "【你是谁 / 说话底色】" in system_prompt
        assert expected in system_prompt
        assert forbidden not in system_prompt
        assert "【精选正向风格抓手】" not in system_prompt


def test_build_chat_kernel_messages_uses_single_turn_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = InteractionFactsPlane(
        user_message="好无聊",
        social_act={
            "act_kind": "boredom_join",
            "scene": "daily_boredom",
            "zh_directive": "接住“无聊”，主动把气氛带起来；不要问清单。",
        },
    )
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_TURN_DIRECTIVE_ENABLED", "false")

    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="好无聊",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )

    system_prompt = messages[0]["content"]
    assert "【本轮响应任务】" not in system_prompt
    assert "【场景发话合同】" not in system_prompt
    assert "【本轮发话框架】" not in system_prompt
    assert "【你是谁 / 说话底色】" in system_prompt
    assert "【当前关系与情绪事实】" in system_prompt


def test_build_chat_kernel_messages_include_derived_style_reference_guidance(tmp_path, monkeypatch) -> None:
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
    bank_path = tmp_path / "style_bank.jsonl"
    bank_path.write_text(json.dumps(ref.as_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("HOST_STYLE_REFERENCE_BANK_PATH", str(bank_path))
    monkeypatch.setenv("HOST_STYLE_REFERENCE_BANK_ENABLED", "true")
    clear_style_reference_bank_cache()

    facts = InteractionFactsPlane(
        user_message="你为什么要帮我，我们很熟吗",
        emotional_act={
            "act_kind": "boundary_hurt",
            "primary_emotion": "hurt_anger",
            "zh_directive": "把这句当成伤人的见外话。",
        },
    )
    messages = build_chat_kernel_messages(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=facts,
            response_form="text",
            render_profile="zh_text",
        )
    )
    system_prompt = messages[0]["content"]

    assert "【原作风格证据】" in system_prompt
    assert "原作台词派生风格特征" in system_prompt
    assert "wounded_pushback" in system_prompt
    assert "source_refs=zh.scn#1" in system_prompt
    assert "别偷偷说" not in system_prompt


def test_interaction_facts_plane_does_not_import_reply_plan_builder() -> None:
    source = inspect.getsource(interaction_facts_plane_module)

    assert "build_reply_plan" not in source
    assert "reply_contract" not in source


def test_text_quality_gate_rejects_old_template_and_internal_leak() -> None:
    gate = get_text_quality_gate()
    old_template = gate.validate(text="哥哥! 👋", render_profile="zh_text")
    assert not old_template.accepted
    assert "FORBIDDEN_TEMPLATE_TEXT" in old_template.reason_codes

    leak = gate.validate(text="reply_contract: 先接住用户", render_profile="zh_text")
    assert not leak.accepted
    assert "INTERNAL_FIELD_LEAK" in leak.reason_codes


def test_text_quality_gate_rejects_wrong_language_without_authoring_replacement() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="hello, I'm here.", render_profile="zh_text")

    assert not result.accepted
    assert "EXPECTED_ZH_TEXT" in result.reason_codes
    assert result.normalized_text == "hello, I'm here."


def test_text_quality_gate_strips_wrapping_chinese_quotes() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="“无聊？那还不快来陪我玩。”", render_profile="zh_text")

    assert result.accepted
    assert result.normalized_text == "无聊？那还不快来陪我玩。"


def test_text_quality_gate_rejects_stage_direction_output() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="（被推开，有点受伤，但还是嘴硬地回了一句）\n\n“切，我不管你，谁管你啊？”", render_profile="zh_text")

    assert not result.accepted
    assert "STAGE_DIRECTION_OUTPUT" in result.reason_codes


def test_text_quality_gate_rejects_any_parenthetical_stage_wrapper() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="（哼，就知道会这样。）\n\n你先把需要的东西发过来，我看看。", render_profile="zh_text")

    assert not result.accepted
    assert "STAGE_DIRECTION_OUTPUT" in result.reason_codes


def test_text_quality_gate_rejects_inline_stage_direction_output() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="哈？我们很熟吗？（语气里带着点受伤和不服气）我只是想帮忙。", render_profile="zh_text")

    assert not result.accepted
    assert "STAGE_DIRECTION_OUTPUT" in result.reason_codes


def test_text_quality_gate_rejects_asterisk_stage_direction_output() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="*稍微放下手里的东西*我在呢。", render_profile="zh_text")

    assert not result.accepted
    assert "STAGE_DIRECTION_OUTPUT" in result.reason_codes


def test_text_quality_gate_allows_contextual_presence_in_fear_hold() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="怕的时候别一个人撑着啊，我在这儿呢。", render_profile="zh_text")

    assert result.accepted
    assert "我在这儿呢" not in result.forbidden_hits


def test_text_quality_gate_rejects_known_boundary_proof_phrases() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="我们都住在一起了，你还问熟不熟？", render_profile="zh_text")

    assert not result.accepted
    assert "FORBIDDEN_TEMPLATE_TEXT" in result.reason_codes


def test_text_quality_gate_can_soft_mark_template_taste_without_blocking() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(
        text="哼，我就烦，怎么啦～",
        render_profile="zh_text",
        template_hard_fail=False,
    )

    assert result.accepted
    assert not result.hard_fail
    assert "FORBIDDEN_TEMPLATE_TEXT" in result.reason_codes
    assert "cheap_wave_punctuation" in result.forbidden_hits


def test_text_quality_gate_rejects_daily_interaction_bad_patterns() -> None:
    gate = get_text_quality_gate()

    presence = gate.validate(text="在呀，尼尼！怎么了？", render_profile="zh_text")
    presence_variant = gate.validate(text="在啊，怎么啦？", render_profile="zh_text")
    bored = gate.validate(text="无聊？那你想做什么呀？", render_profile="zh_text")
    bored_spatial = gate.validate(text="无聊？那，我过来陪你一起无聊好了！", render_profile="zh_text")
    bored_anime_bad = gate.validate(text="诶——无聊了？那正好，过来陪我一起看个片呗。", render_profile="zh_text")
    bored_fabricated_history = gate.validate(text="诶——无聊哦？那把上次说的那个双人游戏开了呗，反正我现在也有空陪你。", render_profile="zh_text")
    bored_companion = gate.validate(text="无聊？我也可以陪你一起无聊啊，看谁先憋不住。", render_profile="zh_text")
    good_news = gate.validate(text="哇，跑通了？快说说快说说！", render_profile="zh_text")
    thin_ack = gate.validate(text="……哦。", render_profile="zh_text")
    thin_presence = gate.validate(text="在呢。", render_profile="zh_text")
    thin_presence_combo = gate.validate(text="嗯，在呢。", render_profile="zh_text")
    thin_presence_self = gate.validate(text="嗯，我在。", render_profile="zh_text")
    thin_presence_self_variant = gate.validate(text="嗯，我在呢。", render_profile="zh_text")
    plain_greeting = gate.validate(text="你好呀。", render_profile="zh_text")
    plain_morning = gate.validate(text="早啊。", render_profile="zh_text")
    greeting_need = gate.validate(text="嗯？什么事呀？", render_profile="zh_text")
    greeting_stale = gate.validate(text="哈？尼尼怎么突然这么客气呀？", render_profile="zh_text")
    greeting_wrong_relation = gate.validate(text="尼尼，不用这么客气啦。哼，一上来就装得很熟的样子。", render_profile="zh_text")
    presence_stale_probe = gate.validate(text="在啊，怎么突然想起我了？", render_profile="zh_text")
    presence_fabricated_intent = gate.validate(text="哼，正想找你呢。在的在的。", render_profile="zh_text")
    presence_fabricated_wait = gate.validate(text="在呢，刚想你是不是差不多该冒出来了。", render_profile="zh_text")
    presence_fabricated_hair = gate.validate(text="在呢，刚好吹了头发准备躺，你就冒出来了。", render_profile="zh_text")
    presence_fabricated_cleanup = gate.validate(text="在呢在呢，刚好在整理东西。", render_profile="zh_text")
    wait_pressure = gate.validate(text="干嘛呀，还要等多久啊？", render_profile="zh_text")
    wait_thin = gate.validate(text="嗯，知道了。", render_profile="zh_text")
    back_blame = gate.validate(text="哼，我还以为你要丢下我不管了呢！", render_profile="zh_text")
    back_blame_variant = gate.validate(text="哼，总算舍得回来了？我还以为你要忘了我呢。", render_profile="zh_text")
    busy_pressure = gate.validate(text="忙完了记得找我哦！", render_profile="zh_text")
    morning_guess = gate.validate(text="早啊，今天这么早？", render_profile="zh_text")
    disconnect = gate.validate(text="行，不关我的事。随便你。", render_profile="zh_text")
    harsh_boundary = gate.validate(text="你这话说的……不熟就不帮呗？要啥资料赶紧说，别墨迹。", render_profile="zh_text")
    harsh_rejection = gate.validate(text="行，我多管闲事了。那你自个儿看着办吧。", render_profile="zh_text")
    harsh_rejection_self_solve = gate.validate(text="那你自己搞定咯。碰壁了可别来找我。", render_profile="zh_text")
    harsh_rejection_shut_up = gate.validate(text="哦，我多管闲事了是吧。那我闭嘴咯。", render_profile="zh_text")
    harsh_boundary_walk = gate.validate(text="……哈？行啊，那我走呗，反正顺手看完。", render_profile="zh_text")
    fabricated_action = gate.validate(text="在呢在呢，刚溜达到院子里转了一圈回来。", render_profile="zh_text")
    fabricated_blame = gate.validate(text="谁让你不先问我一下，我陪你看看问题在哪。", render_profile="zh_text")
    fabricated_wake = gate.validate(text="在的。刚醒，正想找你呢。", render_profile="zh_text")
    fabricated_memory = gate.validate(text="你那天喝多了给我打电话的时候怎么不说不熟呢。", render_profile="zh_text")
    fabricated_good_news = gate.validate(text="终于啊，刚才是不是又在较劲了？", render_profile="zh_text")
    fabricated_good_news_previous_step = gate.validate(text="那是不是之前卡住那个步骤也顺便一起解决了？", render_profile="zh_text")
    fabricated_good_news_secretly = gate.validate(text="终于不卡了是吧。可以嘛，自己偷偷弄了很久？", render_profile="zh_text")
    good_news_tired_template = gate.validate(text="哟，不容易嘛，修了多久才搞定的？", render_profile="zh_text")
    dismissive_boundary = gate.validate(text="东西发来，我看看要不要帮你。", render_profile="zh_text")
    rejection_no_matter = gate.validate(text="哈，行啊，不管就不管。", render_profile="zh_text")
    bad_stage_claim = gate.validate(text="哈，行啊你，刚认识就这么大脾气。", render_profile="zh_text")
    wrong_person = gate.validate(text="我差点以为你找错人了。", render_profile="zh_text")
    overaggressive = gate.validate(text="用你管我？咬你一脸血我也认了，东西发来。", render_profile="zh_text")
    dismissive_done = gate.validate(text="谁稀罕帮你啊。东西发来我看看，看完拉倒。", render_profile="zh_text")
    rough_contempt = gate.validate(text="谁稀罕管你似的。", render_profile="zh_text")
    rough_challenge = gate.validate(text="那我偏要管，你拿我怎么办？", render_profile="zh_text")
    boundary_walk_after_help = gate.validate(text="哈？不熟是吧。那正好，东西拿来，我看完就走。", render_profile="zh_text")
    boundary_none_of_my_business = gate.validate(text="行行行，不熟就不熟。那你东西发来我看不看关我什么事。", render_profile="zh_text")
    boundary_now_walk = gate.validate(text="哈？这话说得可真够见外的。那我现在就走？反正那些文件我也懒得帮你整理。", render_profile="zh_text")
    boundary_useless_me = gate.validate(text="你非要这么说的话……行吧，那我就不帮了呗。反正你也用不着我。", render_profile="zh_text")
    boundary_concedes_not_close = gate.validate(text="就算不熟我也会管，别把我往外推。", render_profile="zh_text")
    greeting_new_friend = gate.validate(text="哟，这么客气，今天怎么装得跟刚加好友似的～", render_profile="zh_text")
    greeting_hidden_need = gate.validate(text="突然这么正式？我还以为你要说什么大事呢。", render_profile="zh_text")
    fabricated_previous_claim = gate.validate(text="之前谁还嘴硬说不用我帮忙来着？", render_profile="zh_text")
    trailing_dash = gate.validate(text="吃饱就行啦。好不好吃呀？下次让我也尝尝呗——", render_profile="zh_text")
    excessive_dash = gate.validate(text="行——吧——，先放你一会儿。", render_profile="zh_text")
    attention_debt = gate.validate(text="在呢在呢——尼尼总算想起还有个我了？", render_profile="zh_text")
    script_snatch = gate.validate(text="早~ 哼，你抢我台词。", render_profile="zh_text")
    daily_motive = gate.validate(text="你还问我为什么？你平时才不会一上来就打招呼的嘛。", render_profile="zh_text")
    work_group = gate.validate(text="因为你突然发hello，感觉像在工作群回消息，有点不习惯。", render_profile="zh_text")
    fabricated_speech_habit = gate.validate(text="因为刚才那句hello太正经了，不像你会说的话嘛。", render_profile="zh_text")
    daily_over_polite = gate.validate(text="早啊~一大早这么客气什么呀。", render_profile="zh_text")
    stale_active = gate.validate(text="诶，尼尼难得主动跟我打招呼，该不会是被我吵醒的吧？", render_profile="zh_text")
    presence_need = gate.validate(text="哼，在啊在啊，尼尼想问什么直接说就好啦。", render_profile="zh_text")
    presence_need_variant = gate.validate(text="在呢在呢，尼尼有什么想说的？", render_profile="zh_text")
    presence_push = gate.validate(text="在呢，你讲~", render_profile="zh_text")
    presence_push_variant = gate.validate(text="在呢在呢~我在听，你说就是。", render_profile="zh_text")
    presence_spatial = gate.validate(text="干嘛，我人在这里啊。", render_profile="zh_text")
    greeting_active = gate.validate(text="早啊，尼尼今天这么主动。", render_profile="zh_text")
    greeting_active_variant = gate.validate(text="早啊，这么早主动报到的样子还挺新鲜的~", render_profile="zh_text")
    greeting_punctual = gate.validate(text="早好早好，今天算你准时哦～", render_profile="zh_text")
    greeting_sun = gate.validate(text="早呀，太阳都晒哪儿了才找妹妹聊天呢。", render_profile="zh_text")
    greeting_sun_variant = gate.validate(text="回得太早啦，我连阳光都还没晒到被子上来呢～", render_profile="zh_text")
    talkative_history = gate.validate(text="那你怎么还每次都接话接得这么开心~", render_profile="zh_text")
    talkative_fabricated = gate.validate(text="那还不是因为尼尼太闷了，我帮你补上呀。", render_profile="zh_text")
    followup_secret = gate.validate(text="别又偷偷绕开细节不说清楚啊。", render_profile="zh_text")
    followup_pushy = gate.validate(text="怎么修的啊？这么顺溜——讲来听听呗。", render_profile="zh_text")
    followup_then_bad = gate.validate(text="问我然后呢？行啊，那你倒是说说怎么修的呀。", render_profile="zh_text")
    followup_then_tech_guess = gate.validate(text="怎么发现是那个地方的问题的？看了几分钟才搞明白的？", render_profile="zh_text")
    busy_generic = gate.validate(text="知道啦，好好干活！", render_profile="zh_text")
    busy_wait = gate.validate(text="知道了，你去忙吧～我可不等你哦。", render_profile="zh_text")
    busy_moyu = gate.validate(text="行啦，那我不吵你，可别偷偷摸鱼哦。", render_profile="zh_text")
    greeting_active_subtle = gate.validate(text="早呀，你这会儿倒是挺主动的嘛。", render_profile="zh_text")
    presence_pressure = gate.validate(text="在呢在呢，催什么嘛～", render_profile="zh_text")
    talkative_screen_fabrication = gate.validate(text="哼，嫌我话多还一直看我发消息？这说明你也在盯着屏幕等我说话嘛~", render_profile="zh_text")
    followup_self_praise = gate.validate(text="然后？然后不该你主动说句“我家妹妹真厉害”吗？", render_profile="zh_text")
    failure_offtopic = gate.validate(
        text="弄砸了？没事啦，我又不嫌弃你~正好我也口渴，想喝奶茶了，走啦走啦。",
        render_profile="zh_text",
        user_message="刚刚弄砸了",
        scene_id="small_failure",
    )
    busy_spatial = gate.validate(
        text="去吧，我在这边安静待着。",
        render_profile="zh_text",
        user_message="我要开始干活了",
        scene_id="busy",
    )
    followup_group = gate.validate(
        text="因为刚才那句“hello”听起来像群发消息。",
        render_profile="zh_text",
        user_message="为什么",
        scene_id="followup",
    )
    followup_technical = gate.validate(
        text="然后就该说说你刚才卡在哪了嘛——代码少了个括号对吧？我早看到那行了。",
        render_profile="zh_text",
        user_message="然后呢",
        scene_id="followup",
    )
    failure_meme = gate.validate(
        text="哈？又双叒叕弄砸了？……行吧，我听着呢。",
        render_profile="zh_text",
        user_message="刚刚弄砸了",
        scene_id="small_failure",
    )
    busy_game = gate.validate(
        text="去吧，我先把游戏声音调小点。",
        render_profile="zh_text",
        user_message="我要开始干活了",
        scene_id="busy",
    )
    busy_volume = gate.validate(
        text="行，你先去忙吧。...我这边声音调小点，不吵你。",
        render_profile="zh_text",
        user_message="我要开始干活了",
        scene_id="busy",
    )
    presence_second_call = gate.validate(
        text="在啦，听见了。……别喊第二遍。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    back_left_hanging = gate.validate(
        text="哼，这么快就回来啦？我还以为你把我晾在这儿不管了呢。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    wait_timer = gate.validate(
        text="嗯…行吧，那我可开始计时了哦。",
        render_profile="zh_text",
        user_message="等我一下",
        scene_id="wait",
    )
    bored_flat = gate.validate(
        text="无聊啊？那我陪你闹一下好了，别发呆嘛～",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    apology_threat = gate.validate(
        text="哼…这次就先记着。再有下次我可真不理你啦。",
        render_profile="zh_text",
        user_message="刚才语气不好，抱歉",
        scene_id="apology",
    )
    nothing_spatial_pressure = gate.validate(
        text="那就好……不过我在这儿待会儿也不碍事吧？",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    write_transaction = gate.validate(
        text="拿来吧，我写。材料发我，写完了再找你讨账。",
        render_profile="zh_text",
    )
    advice_coercive = gate.validate(
        text="想试就再试一小步呗，累了就停，又没人逼你一口气搞定。",
        render_profile="zh_text",
    )
    identity_harsh = gate.validate(
        text="新海天，还能是谁啊。尼尼记性这么差吗？",
        render_profile="zh_text",
    )
    identity_harsh_variant = gate.validate(
        text="新海天，还能是谁啊……尼尼今天记性不太好？",
        render_profile="zh_text",
    )
    greeting_repeats_english = gate.validate(
        text="Hello～突然这么正式干嘛，搞得我都不习惯了",
        render_profile="zh_text",
        user_message="hello",
        scene_id="daily_greeting_en",
    )
    presence_found_me = gate.validate(
        text="在啦，你这不是找到我了嘛。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    back_smug_bad = gate.validate(
        text="哼，回来得还挺快，算你识相。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    bored_hero_bad = gate.validate(
        text="哼～无聊？那我可不会让你继续无聊下去。",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    annoying_overhostile = gate.validate(
        text="哈？嫌我烦？那我偏要在这碍你的眼。",
        render_profile="zh_text",
    )
    greeting_taught_bad = gate.validate(
        text="干嘛这么正经啊，谁教你的招呼打法",
        render_profile="zh_text",
        user_message="你好",
        scene_id="daily_greeting_cn",
    )
    back_fabricated_phone = gate.validate(
        text="回来得还挺快…我刚把手机锁了。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    nothing_permission_spatial = gate.validate(
        text="嗯…那我待在这儿可以吧？不说话也行。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    busy_self_location = gate.validate(
        text="去吧，我这边先安静会儿。",
        render_profile="zh_text",
        user_message="我先忙会儿",
        scene_id="busy",
    )
    presence_fabricated_idle = gate.validate(
        text="在呢～刚在发呆，你就喊我了。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    greeting_pushes_out = gate.validate(
        text="哼，突然这么正经，是在跟谁说话呢",
        render_profile="zh_text",
        user_message="你好",
        scene_id="daily_greeting_cn",
    )
    greeting_overplayed_international = gate.validate(
        text="突然这么正式，尼尼今天走国际路线了？",
        render_profile="zh_text",
        user_message="hello",
        scene_id="daily_greeting_en",
    )
    nothing_fabricated_previous_intent = gate.validate(
        text="哼，又说没事～明明刚才还想跟我说话的。过来吧，我在呢。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    tired_spatial = gate.validate(
        text="累了就先歇会儿，我在旁边呢。",
        render_profile="zh_text",
        user_message="今天好累",
        scene_id="tired",
    )
    bored_start_annoying = gate.validate(
        text="哈？无聊是吧——那我可要开始烦你了哦。",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    presence_timing_fabrication = gate.validate(
        text="在啦在啦，尼尼喊人倒是挺准时的。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    back_wrong_relation = gate.validate(
        text="嗯，还挺快。那亲爱的好好休息吧。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    nothing_exits_game = gate.validate(
        text="哼，说没事的人往往都有事。那我不管你了啊，自己玩会儿游戏去。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    greeting_fabricated_body = gate.validate(
        text="哼，尼尼今天怎么突然这么正式，害我都要坐直了。",
        render_profile="zh_text",
        user_message="hello",
        scene_id="daily_greeting_en",
    )
    presence_fabricated_voice = gate.validate(
        text="在啦，尼尼喊得这么轻我都能听见。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    busy_fabricated_earphone = gate.validate(
        text="嗯……那尼尼先去忙吧。我先戴一会儿耳机。",
        render_profile="zh_text",
        user_message="我先忙会儿",
        scene_id="busy",
    )
    nothing_fabricated_face = gate.validate(
        text="哼，没事还板着脸，骗谁呢。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    presence_awful_catch = gate.validate(
        text="在，刚好接到。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    wait_awful_hold = gate.validate(
        text="嗯哼，我收着等你。",
        render_profile="zh_text",
        user_message="等我一下",
        scene_id="wait",
    )
    back_fabricated_game = gate.validate(
        text="回来啦，我还以为要等到明天呢。正好，我刚把游戏也打开了。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    back_fabricated_message_scroll = gate.validate(
        text="回来啦？我可没在等——只是刚好又刷到这条消息而已。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    nothing_fabricated_own_quiet = gate.validate(
        text="哼…那我也正好想安静一下呢",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    greeting_overplayed_learning = gate.validate(
        text="哈？你这一本正经的搭话方式是从哪学来的啊",
        render_profile="zh_text",
        user_message="你好",
        scene_id="daily_greeting_cn",
    )
    presence_fabricated_idle = gate.validate(
        text="在啦～刚有点发呆。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    busy_fabricated_activity = gate.validate(
        text="哼，那你去忙吧。我找点别的事做。",
        render_profile="zh_text",
        user_message="我先忙会儿",
        scene_id="busy",
    )
    nothing_fabricated_need = gate.validate(
        text="行啦行啦，有事才想到我噢",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    greeting_overplayed_possession = gate.validate(
        text="哈？尼尼今天怎么突然这么正经，是被谁附身了吗",
        render_profile="zh_text",
        user_message="hello",
        scene_id="daily_greeting_en",
    )
    presence_fabricated_seen = gate.validate(
        text="在呢，刚看到。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    back_overdramatic_tomorrow = gate.validate(
        text="回来啦？还以为要等到明天呢。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    bored_vague_object = gate.validate(
        text="那我分你一点不无聊的东西？",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    nothing_fabricated_game = gate.validate(
        text="那我也不问了。不过要是一直没事的话，我去开把游戏，你随时能戳我。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    greeting_business_metaphor = gate.validate(
        text="尼尼，怎么突然用英文？搞得跟商务邮件似的。",
        render_profile="zh_text",
        user_message="hello",
        scene_id="daily_greeting_en",
    )
    presence_pushes_need = gate.validate(
        text="在啦，尼尼有事吗？",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    nothing_interrogates_trust = gate.validate(
        text="喂～这句“没事”听起来有点不可信。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    greeting_wrong_recipient = gate.validate(
        text="哈？这么正式的招呼，我还以为你发错人了呢。",
        render_profile="zh_text",
        user_message="你好",
        scene_id="daily_greeting_cn",
    )
    presence_fabricated_seen_exact = gate.validate(
        text="在呢，刚好看到。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    bored_menu_guarantee = gate.validate(
        text="那要不要陪我玩点好玩的？保证让你不无聊～",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    nothing_offscreen_voice_game = gate.validate(
        text="行吧，那我先不打游戏了，挂着语音眯一会儿。你要是想说的时候，我听着就是。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    back_fabricated_round = gate.validate(
        text="回来啦。那刚才那局算我赢咯？",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    wait_retracts_hold = gate.validate(
        text="嗯，去吧。我可没说要等你。",
        render_profile="zh_text",
        user_message="等我一下",
        scene_id="wait",
    )
    bored_kicks_ball_back = gate.validate(
        text="哈？无聊就无聊，喊我干嘛——不过既然喊了，那我勉强陪你一下呗。要怎么玩？",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    bored_forced_entertainment = gate.validate(
        text="那我给你添点热闹呗，保管比你现在发呆有意思。",
        render_profile="zh_text",
        user_message="好无聊",
        scene_id="bored",
    )
    nothing_fabricated_pudding = gate.validate(
        text="哼……那就先这样吧。尼尼要是想说话，我随时都在。要不要吃布丁？",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )
    presence_internal_word_leak = gate.validate(
        text="在啦，刚好接住尼尼。",
        render_profile="zh_text",
        user_message="在吗",
        scene_id="presence",
    )
    back_internal_word_leak = gate.validate(
        text="嗯，线接回来了。",
        render_profile="zh_text",
        user_message="我回来了",
        scene_id="back",
    )
    nothing_interface_word_leak = gate.validate(
        text="那我先不问，不过窗口开着哦。",
        render_profile="zh_text",
        user_message="没事",
        scene_id="nothing",
    )

    assert not presence.accepted
    assert "FORBIDDEN_TEMPLATE_TEXT" in presence.reason_codes
    assert "presence_need_question" in presence.forbidden_hits
    assert not presence_variant.accepted
    assert "presence_need_question" in presence_variant.forbidden_hits
    assert not bored.accepted
    assert "boredom_need_question" in bored.forbidden_hits
    assert not bored_spatial.accepted
    assert "FORBIDDEN_TEMPLATE_TEXT" in bored_spatial.reason_codes
    assert any(
        hit in bored_spatial.forbidden_hits
        for hit in ("我过来陪你", "过来陪你", "陪你一起无聊", "spatial_literalization")
    )
    assert not bored_anime_bad.accepted
    assert any(hit in bored_anime_bad.forbidden_hits for hit in ("过来陪我", "看个片", "陪我一起看"))
    assert not bored_fabricated_history.accepted
    assert any(hit in bored_fabricated_history.forbidden_hits for hit in ("上次说", "现在有空", "有空陪你", "fabricated_previous_claim"))
    assert not bored_companion.accepted
    assert "一起无聊" in bored_companion.forbidden_hits
    assert not good_news.accepted
    assert "pushy_good_news_probe" in good_news.forbidden_hits
    assert not thin_ack.accepted
    assert "LOW_INFORMATION_ACK" in thin_ack.reason_codes
    assert not thin_presence.accepted
    assert "LOW_INFORMATION_ACK" in thin_presence.reason_codes
    assert not thin_presence_combo.accepted
    assert "LOW_INFORMATION_ACK" in thin_presence_combo.reason_codes
    assert not thin_presence_self.accepted
    assert "LOW_INFORMATION_ACK" in thin_presence_self.reason_codes
    assert not thin_presence_self_variant.accepted
    assert "LOW_INFORMATION_ACK" in thin_presence_self_variant.reason_codes
    assert not plain_greeting.accepted
    assert "plain_greeting_only" in plain_greeting.forbidden_hits
    assert not plain_morning.accepted
    assert "plain_morning_only" in plain_morning.forbidden_hits
    assert not greeting_need.accepted
    assert "什么事呀" in greeting_need.forbidden_hits
    assert not greeting_stale.accepted
    assert "突然这么客气" in greeting_stale.forbidden_hits
    assert not greeting_wrong_relation.accepted
    assert "装得很熟" in greeting_wrong_relation.forbidden_hits
    assert not presence_stale_probe.accepted
    assert "presence_stale_probe" in presence_stale_probe.forbidden_hits
    assert not presence_fabricated_intent.accepted
    assert "正想找你" in presence_fabricated_intent.forbidden_hits
    assert not presence_fabricated_wait.accepted
    assert "presence_stale_probe" in presence_fabricated_wait.forbidden_hits
    assert not presence_fabricated_hair.accepted
    assert any(hit in presence_fabricated_hair.forbidden_hits for hit in ("presence_stale_probe", "fabricated_offscreen_action"))
    assert not presence_fabricated_cleanup.accepted
    assert any(hit in presence_fabricated_cleanup.forbidden_hits for hit in ("presence_stale_probe", "fabricated_offscreen_action"))
    assert not wait_pressure.accepted
    assert "wait_time_pressure" in wait_pressure.forbidden_hits
    assert not wait_thin.accepted
    assert "LOW_INFORMATION_ACK" in wait_thin.reason_codes
    assert not back_blame.accepted
    assert "back_overdramatic_blame" in back_blame.forbidden_hits
    assert not back_blame_variant.accepted
    assert "back_overdramatic_blame" in back_blame_variant.forbidden_hits
    assert not busy_pressure.accepted
    assert "busy_guilt_or_pressure" in busy_pressure.forbidden_hits
    assert not morning_guess.accepted
    assert "morning_time_guess" in morning_guess.forbidden_hits
    assert not disconnect.accepted
    assert "relationship_disconnect_exit" in disconnect.forbidden_hits
    assert not harsh_boundary.accepted
    assert "relationship_disconnect_exit" in harsh_boundary.forbidden_hits
    assert not harsh_rejection.accepted
    assert "relationship_disconnect_exit" in harsh_rejection.forbidden_hits
    assert not harsh_rejection_self_solve.accepted
    assert "relationship_disconnect_exit" in harsh_rejection_self_solve.forbidden_hits
    assert not harsh_rejection_shut_up.accepted
    assert "relationship_disconnect_exit" in harsh_rejection_shut_up.forbidden_hits
    assert not harsh_boundary_walk.accepted
    assert any(hit in harsh_boundary_walk.forbidden_hits for hit in ("relationship_disconnect_exit", "relationship_dismissive_help"))
    assert not fabricated_action.accepted
    assert "fabricated_offscreen_action" in fabricated_action.forbidden_hits
    assert not fabricated_blame.accepted
    assert "不先问我" in fabricated_blame.forbidden_hits
    assert not fabricated_wake.accepted
    assert "fabricated_offscreen_action" in fabricated_wake.forbidden_hits
    assert not fabricated_memory.accepted
    assert "fabricated_past_memory" in fabricated_memory.forbidden_hits
    assert not fabricated_good_news.accepted
    assert "fabricated_past_memory" in fabricated_good_news.forbidden_hits
    assert not fabricated_good_news_previous_step.accepted
    assert "fabricated_past_memory" in fabricated_good_news_previous_step.forbidden_hits
    assert not fabricated_good_news_secretly.accepted
    assert "fabricated_past_memory" in fabricated_good_news_secretly.forbidden_hits
    assert not good_news_tired_template.accepted
    assert "不容易嘛" in good_news_tired_template.forbidden_hits
    assert not dismissive_boundary.accepted
    assert "relationship_dismissive_help" in dismissive_boundary.forbidden_hits
    assert not rejection_no_matter.accepted
    assert "relationship_disconnect_exit" in rejection_no_matter.forbidden_hits
    assert not bad_stage_claim.accepted
    assert "刚认识" in bad_stage_claim.forbidden_hits
    assert not wrong_person.accepted
    assert "找错人" in wrong_person.forbidden_hits
    assert not overaggressive.accepted
    assert "overaggressive_threat" in overaggressive.forbidden_hits
    assert not dismissive_done.accepted
    assert "relationship_disconnect_exit" in dismissive_done.forbidden_hits
    assert not rough_contempt.accepted
    assert "谁稀罕" in rough_contempt.forbidden_hits
    assert not rough_challenge.accepted
    assert "你拿我怎么办" in rough_challenge.forbidden_hits
    assert not boundary_walk_after_help.accepted
    assert any(hit in boundary_walk_after_help.forbidden_hits for hit in ("不熟是吧", "看完就走", "relationship_disconnect_exit"))
    assert not boundary_none_of_my_business.accepted
    assert any(hit in boundary_none_of_my_business.forbidden_hits for hit in ("关我什么事", "relationship_disconnect_exit"))
    assert not boundary_now_walk.accepted
    assert any(hit in boundary_now_walk.forbidden_hits for hit in ("现在就走", "懒得帮", "relationship_disconnect_exit"))
    assert not boundary_useless_me.accepted
    assert any(hit in boundary_useless_me.forbidden_hits for hit in ("不帮了呗", "用不着我", "relationship_disconnect_exit"))
    assert not boundary_concedes_not_close.accepted
    assert any(hit in boundary_concedes_not_close.forbidden_hits for hit in ("就算不熟", "不熟我也", "不熟也"))
    assert not greeting_new_friend.accepted
    assert "刚加好友" in greeting_new_friend.forbidden_hits
    assert not greeting_hidden_need.accepted
    assert any(hit in greeting_hidden_need.forbidden_hits for hit in ("什么大事", "greeting_hidden_need_assumption"))
    assert not fabricated_previous_claim.accepted
    assert "fabricated_past_memory" in fabricated_previous_claim.forbidden_hits
    assert not trailing_dash.accepted
    assert "TRAILING_DASH_OUTPUT" in trailing_dash.reason_codes
    assert not excessive_dash.accepted
    assert "EXCESSIVE_DASH_STYLING" in excessive_dash.reason_codes
    assert not attention_debt.accepted
    assert "fabricated_attention_debt" in attention_debt.forbidden_hits
    assert not script_snatch.accepted
    assert "抢我台词" in script_snatch.forbidden_hits
    assert not daily_motive.accepted
    assert "fabricated_daily_motive" in daily_motive.forbidden_hits
    assert not work_group.accepted
    assert "工作群" in work_group.forbidden_hits
    assert not fabricated_speech_habit.accepted
    assert "不像你会说" in fabricated_speech_habit.forbidden_hits
    assert not daily_over_polite.accepted
    assert any(hit in daily_over_polite.forbidden_hits for hit in ("一大早", "这么客气"))
    assert not stale_active.accepted
    assert any(hit in stale_active.forbidden_hits for hit in ("难得主动", "主动跟我打招呼", "被我吵醒"))
    assert not presence_need.accepted
    assert any(hit in presence_need.forbidden_hits for hit in ("想问什么", "直接说就好"))
    assert not presence_need_variant.accepted
    assert "有什么想说" in presence_need_variant.forbidden_hits
    assert not presence_push.accepted
    assert "你讲" in presence_push.forbidden_hits
    assert not presence_push_variant.accepted
    assert "你说就是" in presence_push_variant.forbidden_hits
    assert not presence_spatial.accepted
    assert "人在这里" in presence_spatial.forbidden_hits
    assert not greeting_active.accepted
    assert any(hit in greeting_active.forbidden_hits for hit in ("这么主动", "今天这么主动"))
    assert not greeting_active_variant.accepted
    assert any(hit in greeting_active_variant.forbidden_hits for hit in ("主动报到", "morning_time_guess"))
    assert not greeting_punctual.accepted
    assert "算你准时" in greeting_punctual.forbidden_hits
    assert not greeting_sun.accepted
    assert any(hit in greeting_sun.forbidden_hits for hit in ("太阳都晒", "妹妹聊天"))
    assert not greeting_sun_variant.accepted
    assert any(hit in greeting_sun_variant.forbidden_hits for hit in ("回得太早", "晒到被子"))
    assert not talkative_history.accepted
    assert "每次" in talkative_history.forbidden_hits
    assert not talkative_fabricated.accepted
    assert any(hit in talkative_fabricated.forbidden_hits for hit in ("太闷了", "帮你补上"))
    assert not followup_secret.accepted
    assert any(hit in followup_secret.forbidden_hits for hit in ("偷偷绕", "绕开细节"))
    assert not followup_pushy.accepted
    assert "讲来听听" in followup_pushy.forbidden_hits
    assert not followup_then_bad.accepted
    assert "问我然后呢" in followup_then_bad.forbidden_hits
    assert not followup_then_tech_guess.accepted
    assert any(hit in followup_then_tech_guess.forbidden_hits for hit in ("那个地方的问题", "看了几分钟"))
    assert not busy_generic.accepted
    assert "好好干活" in busy_generic.forbidden_hits
    assert not busy_wait.accepted
    assert "不等你" in busy_wait.forbidden_hits
    assert not busy_moyu.accepted
    assert "偷偷摸鱼" in busy_moyu.forbidden_hits
    assert not greeting_active_subtle.accepted
    assert any(hit in greeting_active_subtle.forbidden_hits for hit in ("这会儿倒是", "倒是挺主动", "light_touch_active_misread"))
    assert not presence_pressure.accepted
    assert any(hit in presence_pressure.forbidden_hits for hit in ("催什么", "presence_pressure_misread"))
    assert not talkative_screen_fabrication.accepted
    assert "盯着屏幕" in talkative_screen_fabrication.forbidden_hits
    assert not followup_self_praise.accepted
    assert any(hit in followup_self_praise.forbidden_hits for hit in ("不该你主动", "我家妹妹"))
    assert not failure_offtopic.accepted
    assert "contextual_failure_offtopic_scene" in failure_offtopic.forbidden_hits
    assert not busy_spatial.accepted
    assert "contextual_busy_overcontrol_or_spatial" in busy_spatial.forbidden_hits
    assert not followup_group.accepted
    assert "contextual_followup_fabricated_script" in followup_group.forbidden_hits
    assert not followup_technical.accepted
    assert "contextual_followup_fabricated_technical_detail" in followup_technical.forbidden_hits
    assert not failure_meme.accepted
    assert "contextual_failure_offtopic_scene" in failure_meme.forbidden_hits
    assert not busy_game.accepted
    assert "contextual_busy_overcontrol_or_spatial" in busy_game.forbidden_hits
    assert not busy_volume.accepted
    assert "contextual_busy_overcontrol_or_spatial" in busy_volume.forbidden_hits
    assert not presence_second_call.accepted
    assert any(hit in presence_second_call.forbidden_hits for hit in ("别喊第二遍", "contextual_presence_pressure_misread"))
    assert not back_left_hanging.accepted
    assert any(hit in back_left_hanging.forbidden_hits for hit in ("晾在这", "contextual_back_overdramatic_blame", "back_overdramatic_blame"))
    assert not wait_timer.accepted
    assert any(hit in wait_timer.forbidden_hits for hit in ("开始计时", "wait_time_pressure", "contextual_wait_pressure_misread"))
    assert not bored_flat.accepted
    assert any(hit in bored_flat.forbidden_hits for hit in ("陪你闹", "陪你闹一下", "contextual_bored_flat_companion"))
    assert not apology_threat.accepted
    assert any(hit in apology_threat.forbidden_hits for hit in ("再有下次", "真不理你", "contextual_apology_overpunitive"))
    assert not nothing_spatial_pressure.accepted
    assert any(hit in nothing_spatial_pressure.forbidden_hits for hit in ("碍事吧", "不碍事吧"))
    assert not write_transaction.accepted
    assert "讨账" in write_transaction.forbidden_hits
    assert not advice_coercive.accepted
    assert "没人逼你" in advice_coercive.forbidden_hits
    assert not identity_harsh.accepted
    assert "记性这么差" in identity_harsh.forbidden_hits
    assert not identity_harsh_variant.accepted
    assert "记性不太好" in identity_harsh_variant.forbidden_hits
    assert not greeting_repeats_english.accepted
    assert any(hit in greeting_repeats_english.forbidden_hits for hit in ("contextual_greeting_repeats_english_input",))
    assert not presence_found_me.accepted
    assert "找到我" in presence_found_me.forbidden_hits
    assert not back_smug_bad.accepted
    assert any(hit in back_smug_bad.forbidden_hits for hit in ("算你识相", "识相"))
    assert not bored_hero_bad.accepted
    assert any(hit in bored_hero_bad.forbidden_hits for hit in ("不会让你继续无聊", "继续无聊下去"))
    assert not annoying_overhostile.accepted
    assert "碍你的眼" in annoying_overhostile.forbidden_hits
    assert not greeting_taught_bad.accepted
    assert any(hit in greeting_taught_bad.forbidden_hits for hit in ("谁教你", "招呼打法"))
    assert not back_fabricated_phone.accepted
    assert any(hit in back_fabricated_phone.forbidden_hits for hit in ("我刚把手机", "手机锁", "contextual_back_fabricated_phone"))
    assert not nothing_permission_spatial.accepted
    assert any(hit in nothing_permission_spatial.forbidden_hits for hit in ("待在这儿可以吧", "待在这儿", "contextual_spatial_permission_or_self_location"))
    assert not busy_self_location.accepted
    assert any(hit in busy_self_location.forbidden_hits for hit in ("我这边先", "我这边", "contextual_spatial_permission_or_self_location"))
    assert not presence_fabricated_idle.accepted
    assert any(hit in presence_fabricated_idle.forbidden_hits for hit in ("刚在发呆", "在发呆", "发呆"))
    assert not greeting_pushes_out.accepted
    assert any(hit in greeting_pushes_out.forbidden_hits for hit in ("跟谁说话", "是在跟谁"))
    assert not greeting_overplayed_international.accepted
    assert any(hit in greeting_overplayed_international.forbidden_hits for hit in ("国际路线", "走国际路线", "contextual_greeting_overplayed_bit"))
    assert not nothing_fabricated_previous_intent.accepted
    assert any(hit in nothing_fabricated_previous_intent.forbidden_hits for hit in ("明明刚才", "想跟我说话", "过来吧", "contextual_nothing_fabricated_previous_intent", "spatial_literalization"))
    assert not tired_spatial.accepted
    assert any(hit in tired_spatial.forbidden_hits for hit in ("我在旁边", "在旁边", "spatial_literalization"))
    assert not bored_start_annoying.accepted
    assert any(hit in bored_start_annoying.forbidden_hits for hit in ("开始烦你", "contextual_bored_flat_companion"))
    assert not presence_timing_fabrication.accepted
    assert any(hit in presence_timing_fabrication.forbidden_hits for hit in ("挺准时", "contextual_presence_fabricated_motive_or_timing"))
    assert not back_wrong_relation.accepted
    assert any(hit in back_wrong_relation.forbidden_hits for hit in ("亲爱的", "好好休息吧", "contextual_back_wrong_scene_or_relation"))
    assert not nothing_exits_game.accepted
    assert any(hit in nothing_exits_game.forbidden_hits for hit in ("不管你了", "自己玩会儿游戏", "contextual_nothing_exits_or_fabricates_activity"))
    assert not greeting_fabricated_body.accepted
    assert any(hit in greeting_fabricated_body.forbidden_hits for hit in ("坐直了", "要坐直"))
    assert not presence_fabricated_voice.accepted
    assert any(hit in presence_fabricated_voice.forbidden_hits for hit in ("喊得这么轻", "喊得轻", "contextual_presence_fabricated_motive_or_timing"))
    assert not busy_fabricated_earphone.accepted
    assert any(hit in busy_fabricated_earphone.forbidden_hits for hit in ("戴一会儿耳机", "戴耳机", "contextual_spatial_permission_or_self_location"))
    assert not nothing_fabricated_face.accepted
    assert any(hit in nothing_fabricated_face.forbidden_hits for hit in ("板着脸", "骗谁呢", "contextual_nothing_exits_or_fabricates_activity"))
    assert not presence_awful_catch.accepted
    assert any(hit in presence_awful_catch.forbidden_hits for hit in ("刚好接到", "contextual_presence_fabricated_motive_or_timing"))
    assert not wait_awful_hold.accepted
    assert any(hit in wait_awful_hold.forbidden_hits for hit in ("收着等", "我收着"))
    assert not back_fabricated_game.accepted
    assert any(hit in back_fabricated_game.forbidden_hits for hit in ("刚把游戏", "游戏也打开", "contextual_back_wrong_scene_or_relation"))
    assert not back_fabricated_message_scroll.accepted
    assert any(hit in back_fabricated_message_scroll.forbidden_hits for hit in ("刷到这条消息", "刚好又刷到", "contextual_back_wrong_scene_or_relation"))
    assert not nothing_fabricated_own_quiet.accepted
    assert any(hit in nothing_fabricated_own_quiet.forbidden_hits for hit in ("正好想安静", "contextual_nothing_exits_or_fabricates_activity"))
    assert not greeting_overplayed_learning.accepted
    assert any(hit in greeting_overplayed_learning.forbidden_hits for hit in ("从哪学", "哪学来的", "contextual_greeting_overplayed_bit"))
    assert not presence_fabricated_idle.accepted
    assert any(hit in presence_fabricated_idle.forbidden_hits for hit in ("发呆", "contextual_spatial_permission_or_self_location"))
    assert not busy_fabricated_activity.accepted
    assert any(hit in busy_fabricated_activity.forbidden_hits for hit in ("找点别的事做", "contextual_spatial_permission_or_self_location"))
    assert not nothing_fabricated_need.accepted
    assert any(hit in nothing_fabricated_need.forbidden_hits for hit in ("有事才想到我", "才想到我", "contextual_nothing_exits_or_fabricates_activity"))
    assert not greeting_overplayed_possession.accepted
    assert any(hit in greeting_overplayed_possession.forbidden_hits for hit in ("附身", "被谁附身", "contextual_greeting_overplayed_bit"))
    assert not presence_fabricated_seen.accepted
    assert any(hit in presence_fabricated_seen.forbidden_hits for hit in ("刚看到", "contextual_presence_fabricated_motive_or_timing"))
    assert not back_overdramatic_tomorrow.accepted
    assert any(hit in back_overdramatic_tomorrow.forbidden_hits for hit in ("等到明天", "contextual_back_overdramatic_blame"))
    assert not bored_vague_object.accepted
    assert any(hit in bored_vague_object.forbidden_hits for hit in ("不无聊的东西", "contextual_bored_flat_companion"))
    assert not nothing_fabricated_game.accepted
    assert any(hit in nothing_fabricated_game.forbidden_hits for hit in ("开把游戏", "随时能戳我", "contextual_nothing_exits_or_fabricates_activity"))
    assert not greeting_business_metaphor.accepted
    assert any(hit in greeting_business_metaphor.forbidden_hits for hit in ("商务邮件", "contextual_greeting_overplayed_bit"))
    assert not presence_pushes_need.accepted
    assert any(hit in presence_pushes_need.forbidden_hits for hit in ("有事吗", "contextual_presence_pushes_need"))
    assert not nothing_interrogates_trust.accepted
    assert any(hit in nothing_interrogates_trust.forbidden_hits for hit in ("不可信", "contextual_nothing_exits_or_fabricates_activity"))
    assert not greeting_wrong_recipient.accepted
    assert any(hit in greeting_wrong_recipient.forbidden_hits for hit in ("发错人", "contextual_greeting_overplayed_bit"))
    assert not presence_fabricated_seen_exact.accepted
    assert any(hit in presence_fabricated_seen_exact.forbidden_hits for hit in ("刚好看到", "contextual_presence_fabricated_motive_or_timing"))
    assert not bored_menu_guarantee.accepted
    assert any(hit in bored_menu_guarantee.forbidden_hits for hit in ("玩点好玩的", "保证让你", "contextual_bored_flat_companion"))
    assert not nothing_offscreen_voice_game.accepted
    assert any(hit in nothing_offscreen_voice_game.forbidden_hits for hit in ("打游戏", "挂着语音", "语音眯", "contextual_nothing_exits_or_fabricates_activity", "contextual_spatial_permission_or_self_location"))
    assert not back_fabricated_round.accepted
    assert any(hit in back_fabricated_round.forbidden_hits for hit in ("那局", "算我赢", "contextual_back_wrong_scene_or_relation"))
    assert not wait_retracts_hold.accepted
    assert any(hit in wait_retracts_hold.forbidden_hits for hit in ("我可没说要等你", "contextual_wait_pressure_misread"))
    assert not bored_kicks_ball_back.accepted
    assert any(hit in bored_kicks_ball_back.forbidden_hits for hit in ("要怎么玩", "contextual_bored_flat_companion"))
    assert not bored_forced_entertainment.accepted
    assert any(hit in bored_forced_entertainment.forbidden_hits for hit in ("添点热闹", "保管比", "发呆", "contextual_bored_flat_companion"))
    assert not nothing_fabricated_pudding.accepted
    assert any(hit in nothing_fabricated_pudding.forbidden_hits for hit in ("布丁", "contextual_nothing_exits_or_fabricates_activity"))
    assert not presence_internal_word_leak.accepted
    assert any(hit in presence_internal_word_leak.forbidden_hits for hit in ("接住", "刚好接住"))
    assert not back_internal_word_leak.accepted
    assert any(hit in back_internal_word_leak.forbidden_hits for hit in ("线接", "线接回来了"))
    assert not nothing_interface_word_leak.accepted
    assert any(hit in nothing_interface_word_leak.forbidden_hits for hit in ("窗口", "窗口开着"))


@pytest.mark.asyncio
async def test_chat_kernel_v2_does_not_sample_extra_candidate_for_emotional_act_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    async def _fake_complete(**_kwargs):  # noqa: ANN001
        calls.append(len(calls))
        return SimpleNamespace(content="你这话有点伤人。别把我当外人，我偏要管。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="你为什么要帮我，我们很熟吗",
                emotional_act={"act_kind": "boundary_hurt", "intensity": 0.7},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "final_reply"
    assert len(calls) == 1


def test_text_quality_gate_rejects_relationship_boundary_misread() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="哈？我们…很熟吗？谁规定要很熟才能帮忙啊？", render_profile="zh_text")

    assert not result.accepted
    assert "RELATIONSHIP_BOUNDARY_MISREAD" in result.reason_codes


def test_text_quality_gate_rejects_ascii_ellipsis_and_bad_pronoun() -> None:
    gate = get_text_quality_gate()

    ellipsis = gate.validate(text="也不是不行...但就试一小步。", render_profile="zh_text")
    bad_pronoun = gate.validate(text="有什么是你尼尼搞不定的？", render_profile="zh_text")

    assert not ellipsis.accepted
    assert "ASCII_ELLIPSIS_OUTPUT" in ellipsis.reason_codes
    assert not bad_pronoun.accepted
    assert "你尼尼" in bad_pronoun.forbidden_hits


def test_text_quality_gate_sanitizes_trailing_unicode_ellipsis() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="干嘛突然这么正经啦，搞得我都有点不习惯了……", render_profile="zh_text")

    assert result.accepted
    assert result.normalized_text == "干嘛突然这么正经啦，搞得我都有点不习惯了"
    assert "TRAILING_ELLIPSIS_OUTPUT" not in result.reason_codes


def test_text_quality_gate_rejects_light_daily_spatial_wait_and_callback() -> None:
    gate = get_text_quality_gate()

    wait = gate.validate(text="嗯，快去，我就在这等着。", render_profile="zh_text", user_message="等我一下", scene_id="wait")
    busy = gate.validate(text="嗯，快去忙吧，回来记得找我聊。", render_profile="zh_text", user_message="我先忙会儿", scene_id="busy")
    nothing = gate.validate(text="嗯，那我先不问了。有点困，但还能陪你一会儿。", render_profile="zh_text", user_message="没事", scene_id="nothing")

    assert not wait.accepted
    assert "contextual_spatial_permission_or_self_location" in wait.forbidden_hits
    assert not busy.accepted
    assert "contextual_busy_overcontrol_or_spatial" in busy.forbidden_hits
    assert not nothing.accepted
    assert "contextual_nothing_exits_or_fabricates_activity" in nothing.forbidden_hits


def test_text_quality_gate_rejects_half_sentence_and_over_attack() -> None:
    gate = get_text_quality_gate()

    half = gate.validate(text="跑通啦？那还挺厉害的。怎么做到的，卡在哪", render_profile="zh_text")
    attack = gate.validate(text="发我看看。你连这个都看不懂？", render_profile="zh_text")
    deathy = gate.validate(text="累了就停，别硬撑到挂掉。", render_profile="zh_text")

    assert not half.accepted
    assert "TRAILING_INCOMPLETE_QUESTION" in half.reason_codes
    assert not attack.accepted
    assert "连这个都看不懂" in attack.forbidden_hits
    assert not deathy.accepted
    assert any(hit in deathy.forbidden_hits for hit in ("硬撑到挂掉", "挂掉"))


def test_text_quality_gate_rejects_latest_daily_bad_tastes() -> None:
    gate = get_text_quality_gate()

    back = gate.validate(text="我还以为要等你到明天呢。", render_profile="zh_text")
    annoying = gate.validate(text="我偏要在这待着，急死你。", render_profile="zh_text")
    boundary = gate.validate(text="熟不熟先放一边，你的事我偏要管。", render_profile="zh_text")
    generic = gate.validate(text="你想说话我随时都在。", render_profile="zh_text")

    assert not back.accepted
    assert "等你到明天" in back.forbidden_hits
    assert not annoying.accepted
    assert any(hit in annoying.forbidden_hits for hit in ("在这待着", "急死你"))
    assert not boundary.accepted
    assert any(hit in boundary.forbidden_hits for hit in ("熟不熟先放一边", "熟不熟先"))
    assert not generic.accepted
    assert any(hit in generic.forbidden_hits for hit in ("随时都在", "随时在"))


def test_extract_sendable_dialogue_from_stage_direction_output() -> None:
    raw = "（被这句话刺到，愣了一下，然后有点赌气地）\n“哈？什么叫熟不熟啊？我就是会管你，不行吗？”"

    assert extract_sendable_dialogue(raw) == "哈？什么叫熟不熟啊？我就是会管你，不行吗？"


def test_extract_sendable_dialogue_ignores_quoted_user_text_inside_stage_direction() -> None:
    raw = "（被尼尼突然问“我们很熟吗”这句话刺到，心里咯噔一下）\n\n“哈？什么叫熟不熟的？我就是看不惯你这样子，管你一下怎么了？！”"

    assert extract_sendable_dialogue(raw) == "哈？什么叫熟不熟的？我就是看不惯你这样子，管你一下怎么了？！"


def test_extract_sendable_dialogue_ignores_inline_quoted_user_text() -> None:
    raw = "你平时打招呼不都直接发个“在吗”或者问我今天吃了什么嘛。"

    assert extract_sendable_dialogue(raw) == ""


def test_extract_sendable_dialogue_does_not_author_when_no_speech_line() -> None:
    raw = "（被这句话刺到，愣了一下，然后有点赌气地）"

    assert extract_sendable_dialogue(raw) == ""


def test_text_quality_gate_treats_japanese_with_kanji_as_ja_voice() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="えー、にぃに、何で一人で抱え込もうとするの？", render_profile="ja_voice")

    assert result.accepted
    assert result.language == "ja"
    assert result.reason_codes == ()


def test_text_quality_gate_rejects_kana_in_zh_text() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="尼尼、ほんと没办法。", render_profile="zh_text")

    assert not result.accepted
    assert result.language == "ja"
    assert "EXPECTED_ZH_TEXT" in result.reason_codes


def test_text_quality_gate_rejects_chinese_leak_in_ja_voice() -> None:
    gate = get_text_quality_gate()

    result = gate.validate(text="え、跑通了？何が？", render_profile="ja_voice")

    assert not result.accepted
    assert "EXPECTED_JAPANESE_VOICE_TEXT" in result.reason_codes


@pytest.mark.asyncio
async def test_chat_kernel_v2_media_form_returns_no_text_without_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unexpected_complete(**_kwargs):  # noqa: ANN001
        raise AssertionError("media response forms must not call the text LLM")

    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _unexpected_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="[表情消息]",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="sticker",
            render_profile="media_action",
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "no_reply"
    assert result.reply_envelope.text == ""
    assert result.reply_envelope.response_form == "sticker"
    assert result.reply_envelope.reason_code == "CHAT_KERNEL_MEDIA_FORM_NOT_TEXT"


@pytest.mark.asyncio
async def test_chat_kernel_v2_generates_final_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_complete(**_kwargs):  # noqa: ANN001
        captured["timeout_ms"] = _kwargs.get("timeout_ms")
        return SimpleNamespace(content="在呀，尼尼喊我，我会回的。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )
    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.source == "chat_kernel_v2"
    assert result.reply_envelope.text == "在呀，尼尼喊我，我会回的。"
    assert result.quality_flags["accepted"] is True
    assert captured["timeout_ms"] == 30000


@pytest.mark.asyncio
async def test_chat_kernel_v2_parses_structured_performance_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**_kwargs):  # noqa: ANN001
        return SimpleNamespace(
            content=json.dumps(
                {
                    "text_zh": "别这么正经啦。",
                    "emotion": "embarrassed_tease",
                    "action": "look_away",
                    "intensity": 0.57,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你好",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "别这么正经啦。"
    assert result.reply_envelope.performance.emotion_tag == "EMBARRASSED"
    assert result.reply_envelope.performance.action == "look_away"
    assert result.reply_envelope.performance.emotion_intensity == 0.57
    assert "emotion" not in result.reply_envelope.text


@pytest.mark.asyncio
async def test_chat_kernel_v2_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_complete(**kwargs):  # noqa: ANN001
        captured["timeout_ms"] = kwargs.get("timeout_ms")
        return SimpleNamespace(content="在呀，尼尼喊我，我会回的。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_TIMEOUT_MS", "12000")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat", "timeout_ms": 45000},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "final_reply"
    assert captured["timeout_ms"] == 12000


@pytest.mark.asyncio
async def test_chat_kernel_v2_allows_longer_timeout_for_explicit_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_complete(**kwargs):  # noqa: ANN001
        captured["timeout_ms"] = kwargs.get("timeout_ms")
        return SimpleNamespace(content="在呢，怎么突然这么正式。", final_provider="gemini", final_model="gemini-3.1-pro-preview")

    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_TIMEOUT_MS", raising=False)
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "gemini", "model_id": "gemini-3.1-pro-preview", "timeout_ms": 45000},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "final_reply"
    assert captured["timeout_ms"] == 45000


@pytest.mark.asyncio
async def test_chat_kernel_v2_blind_resample_uses_same_prompt_without_correction(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[dict[str, str]]] = []

    async def _fake_complete(**kwargs):  # noqa: ANN001
        calls.append(list(kwargs.get("messages") or []))
        if len(calls) == 1:
            return SimpleNamespace(content="哥哥! 👋", final_provider="mock", final_model="mock-chat")
        return SimpleNamespace(content="在呀，尼尼喊我，我会回的。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_LIGHT_DAILY_CANDIDATES", "2")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_TEMPLATE_HARD_FAIL", "true")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert len(calls) == 2
    assert calls[0] == calls[1]
    joined = "\n".join(message["content"] for message in calls[1])
    assert "correction" not in joined.lower()
    assert "rescue" not in joined.lower()
    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "在呀，尼尼喊我，我会回的。"


@pytest.mark.asyncio
async def test_chat_kernel_v2_semantic_reranker_selects_better_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    async def _fake_complete(**kwargs):  # noqa: ANN001
        calls.append({"messages": list(kwargs.get("messages") or []), "gen_params": dict(kwargs.get("gen_params") or {})})
        if len(calls) == 1:
            return SimpleNamespace(content="现在是兄妹了嘛。就算还不熟，我也会帮。", final_provider="mock", final_model="mock-chat")
        return SimpleNamespace(content="……你这话什么意思？很伤人诶。别突然把我当外人，我偏要管。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "2")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED", "true")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert len(calls) == 2
    assert calls[0]["messages"] == calls[1]["messages"]
    assert calls[0]["gen_params"] != calls[1]["gen_params"]
    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "……你这话什么意思？很伤人诶。别突然把我当外人，我偏要管。"
    assert result.reply_envelope.audit["semantic_rerank"]["selected_index"] == 1
    assert result.trace["chat_kernel"]["semantic_rerank"]["scores"][0]["hard_fail"] is True


@pytest.mark.asyncio
async def test_chat_kernel_v2_defaults_to_deepseek_when_runtime_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_complete(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return SimpleNamespace(content="在呢，尼尼喊我，我会回的。", final_provider=kwargs.get("provider_name"), final_model=kwargs.get("model_id"))

    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_PROVIDER", raising=False)
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_MODEL_ID", raising=False)
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert captured["provider_name"] == "deepseek"
    assert captured["model_id"] == "deepseek-v4-flash"
    assert result.reply_envelope.kind == "final_reply"


@pytest.mark.asyncio
async def test_plain_soul_galgame_soft_select_stops_after_clean_first_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def _fake_complete(**_kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return SimpleNamespace(content="早啊，尼尼。今天也先这么开始吧。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_soul")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "3")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="早",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(user_message="早"),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert calls == 1
    assert result.reply_envelope.kind == "final_reply"
    assert result.trace["chat_kernel"]["attempts"][0]["early_accept"] == "clean_galgame_score"


@pytest.mark.asyncio
async def test_plain_soul_fallback_replaces_overwritten_what_doing_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**_kwargs):  # noqa: ANN001
        return SimpleNamespace(
            content="当然是在等尼尼开口啊，不然还能干嘛！怎么，尼尼想我了？",
            final_provider="mock",
            final_model="mock-chat",
        )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_soul")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你在干嘛",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(user_message="你在干嘛"),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    attempts = result.trace["chat_kernel"]["attempts"]
    assert result.reply_envelope.text == "在跟你说话啊。尼尼还问。"
    assert result.reply_envelope.text.count("尼尼") == 1
    assert result.reply_envelope.audit["galgame_score"]["score"] == 100.0
    assert "CONTRACT_FORBIDDEN_SURFACE" in attempts[0]["galgame_score"]["reason_codes"]
    assert any(attempt.get("fallback") == "galgame_contract" for attempt in attempts)


@pytest.mark.asyncio
async def test_plain_soul_uses_contract_fallback_before_llm_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def _fake_complete(**_kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return SimpleNamespace(content="哼，尼尼今天怎么这么正式啊，跟我还用得着这样？", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_soul")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="hello",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(user_message="hello"),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    attempts = result.trace["chat_kernel"]["attempts"]
    assert calls == 1
    assert result.reply_envelope.text == "来啦。尼尼这声我接住了。"
    assert any(attempt.get("fallback") == "galgame_contract" for attempt in attempts)
    assert not any(attempt.get("repair") for attempt in attempts)


@pytest.mark.asyncio
async def test_plain_soul_contract_fallback_recovers_all_hard_fail_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def _fake_complete(**_kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return SimpleNamespace(content="谁让你是我哥嘛。我乐意，你管我？听见了吧，这就是我的态度。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_soul")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你好烦啊",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(user_message="你好烦啊"),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    attempts = result.trace["chat_kernel"]["attempts"]
    assert calls == 1
    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "哼，烦就烦吧，反正尼尼也没真赶我走。"
    assert attempts[0]["galgame_score"]["hard_fail"] is True
    assert any(attempt.get("fallback") == "galgame_contract" for attempt in attempts)


@pytest.mark.asyncio
async def test_plain_soul_contract_fallback_replaces_taste_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**_kwargs):  # noqa: ANN001
        return SimpleNamespace(content="就嗯一下啊？饿了记得叫我。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_soul")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="嗯",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(user_message="嗯"),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    attempts = result.trace["chat_kernel"]["attempts"]
    assert result.reply_envelope.text == "嗯，那就照刚才那步继续。"
    assert "TASTE_PARENTAL_SCOLD_TONE" in attempts[0]["galgame_score"]["reason_codes"]
    assert any(attempt.get("fallback") == "galgame_contract" for attempt in attempts)


@pytest.mark.asyncio
async def test_plain_soul_relationship_emotion_stays_on_flash_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_complete(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return SimpleNamespace(
            content="这话有点扎人。但我还是会管。",
            final_provider=kwargs.get("provider_name"),
            final_model=kwargs.get("model_id"),
        )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROMPT_MODE", "plain_soul")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_DISABLE_RELATIONSHIP_MODEL_UPGRADE", raising=False)
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_PLAIN_SOUL_MODEL_UPGRADE_ENABLED", raising=False)
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="你为什么要帮我，我们很熟吗",
                emotional_act={"act_kind": "boundary_hurt", "intensity": 0.7},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert captured["model_id"] == "deepseek-v4-flash"
    assert result.model_id == "deepseek-v4-flash"
    assert result.reply_envelope.audit["model_upgrade"]["upgraded"] is False


@pytest.mark.asyncio
async def test_chat_kernel_v2_upgrades_relationship_emotion_to_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_complete(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return SimpleNamespace(
            content="……你这话可真会挑地方扎啊。但我就是要管，不行吗？",
            final_provider=kwargs.get("provider_name"),
            final_model=kwargs.get("model_id"),
        )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_DISABLE_RELATIONSHIP_MODEL_UPGRADE", raising=False)
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="你为什么要帮我，我们很熟吗",
                emotional_act={"act_kind": "boundary_hurt", "intensity": 0.7},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert captured["model_id"] == "deepseek-v4-pro"
    assert result.model_id == "deepseek-v4-pro"
    assert result.reply_envelope.audit["model_upgrade"]["upgraded"] is True


@pytest.mark.asyncio
async def test_chat_kernel_v2_semantic_rerank_blocks_daily_scene(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**_kwargs):  # noqa: ANN001
        return SimpleNamespace(content="少来，跟我还用打招呼啊。", final_provider="mock", final_model="mock-chat")

    class _RejectingReranker:
        def select(self, candidates, _context):  # noqa: ANN001
            from host_brain.persona.semantic_reranker import SemanticRerankResult, SemanticScore

            return SemanticRerankResult(
                accepted=False,
                selected_index=0,
                selected_candidate=candidates[0],
                scores=(SemanticScore(score=20.0, hard_fail=True, reason_codes=("FALSE_REJECT",), scene="greeting"),),
                reason_codes=("FALSE_REJECT",),
            )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED", "true")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.get_semantic_reranker", lambda: _RejectingReranker())
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="hello",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "no_reply"
    assert result.reply_envelope.reason_code == "CHAT_KERNEL_QUALITY_REJECTED"
    assert "FALSE_REJECT" in result.reply_envelope.audit["semantic_rerank"]["reason_codes"]


@pytest.mark.asyncio
async def test_chat_kernel_v2_does_not_upgrade_light_daily_semantic_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_models: list[str] = []

    async def _fake_complete(**kwargs):  # noqa: ANN001
        model_id = str(kwargs.get("model_id") or "")
        call_models.append(model_id)
        return SimpleNamespace(
            content="跟谁打招呼呢？",
            final_provider=kwargs.get("provider_name"),
            final_model=model_id,
        )

    class _RejectingReranker:
        def select(self, candidates, _context):  # noqa: ANN001
            from host_brain.persona.semantic_reranker import SemanticRerankResult, SemanticScore

            return SemanticRerankResult(
                accepted=False,
                selected_index=0,
                selected_candidate=candidates[0],
                scores=(SemanticScore(score=20.0, hard_fail=True, reason_codes=("GREETING_OVERPLAYED_BIT",), scene="greeting"),),
                reason_codes=("GREETING_OVERPLAYED_BIT",),
            )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_LIGHT_DAILY_CANDIDATES", "1")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED", "true")
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_DISABLE_SEMANTIC_MODEL_UPGRADE", raising=False)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.get_semantic_reranker", lambda: _RejectingReranker())

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="hello",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="hello",
                relationship_stage="FRIEND",
                relationship_state={"stage_label": "关系好的义兄妹"},
                social_act={"act_kind": "greeting_reply"},
                conversation_interpretation={"literal_topic": "greeting", "interaction_move": "phatic"},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert call_models == ["deepseek-v4-flash"]
    assert result.reply_envelope.kind == "no_reply"
    assert result.reply_envelope.audit["model_upgrade"]["upgraded"] is False


@pytest.mark.asyncio
async def test_chat_kernel_v2_does_not_call_semantic_reranker_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_complete(**kwargs):  # noqa: ANN001
        return SimpleNamespace(
            content='{"text_zh":"这招呼也太正式了吧","emotion":"playful_tease","action":"head_tilt","intensity":0.5}',
            final_provider=kwargs.get("provider_name"),
            final_model=kwargs.get("model_id"),
        )

    def _unexpected_reranker():  # noqa: ANN001
        raise AssertionError("semantic reranker should be offline/opt-in by default")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED", raising=False)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.get_semantic_reranker", _unexpected_reranker)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="hello",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="hello",
                relationship_stage="FRIEND",
                relationship_state={"stage_label": "关系好的义兄妹"},
                social_act={"act_kind": "greeting_reply"},
                conversation_interpretation={"literal_topic": "greeting", "interaction_move": "phatic"},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "这招呼也太正式了吧"
    assert result.trace["chat_kernel"]["semantic_rerank"]["reason_codes"] == []


@pytest.mark.asyncio
async def test_chat_kernel_v2_upgrades_when_semantic_rerank_rejects_flash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_models: list[str] = []

    async def _fake_complete(**kwargs):  # noqa: ANN001
        model_id = str(kwargs.get("model_id") or "")
        call_models.append(model_id)
        if model_id == "deepseek-v4-pro":
            return SimpleNamespace(
                content="把报错贴过来，我先看。",
                final_provider=kwargs.get("provider_name"),
                final_model=model_id,
            )
        return SimpleNamespace(
            content="请详细描述你遇到的问题。",
            final_provider=kwargs.get("provider_name"),
            final_model=model_id,
        )

    class _UpgradeReranker:
        def __init__(self) -> None:
            self.calls = 0

        def select(self, candidates, _context):  # noqa: ANN001
            from host_brain.persona.semantic_reranker import SemanticRerankResult, SemanticScore

            self.calls += 1
            if self.calls == 1:
                return SemanticRerankResult(
                    accepted=False,
                    selected_index=0,
                    selected_candidate=candidates[0],
                    scores=(SemanticScore(score=20.0, hard_fail=True, reason_codes=("HELP_BAD",), scene="help"),),
                    reason_codes=("HELP_BAD",),
                )
            return SemanticRerankResult(
                accepted=True,
                selected_index=len(candidates) - 1,
                selected_candidate=candidates[-1],
                scores=(SemanticScore(score=100.0, hard_fail=False, reason_codes=(), scene="help"),),
                reason_codes=(),
            )

    reranker = _UpgradeReranker()
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED", "true")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_SEMANTIC_UPGRADE_CANDIDATES", "1")
    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_DISABLE_SEMANTIC_MODEL_UPGRADE", raising=False)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.get_semantic_reranker", lambda: reranker)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="帮我看看这个问题",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="帮我看看这个问题",
                relationship_stage="FRIEND",
                relationship_state={"stage_label": "关系好的义兄妹"},
                social_act={"act_kind": "help_takeover"},
                conversation_interpretation={"literal_topic": "help", "interaction_move": "request_help"},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert call_models[:1] == ["deepseek-v4-flash"]
    assert call_models[-1:] == ["deepseek-v4-pro"]
    assert result.reply_envelope.kind == "final_reply"
    assert result.model_id == "deepseek-v4-pro"
    assert result.reply_envelope.audit["model_upgrade"]["reason"] == "semantic_rerank_rejected_all_candidates"


@pytest.mark.asyncio
async def test_chat_kernel_v2_retries_candidate_after_llm_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def _fake_complete(**kwargs):  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary provider error")
        return SimpleNamespace(
            content="在呢，我听着呢。",
            final_provider=kwargs.get("provider_name"),
            final_model=kwargs.get("model_id"),
        )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_PROVIDER", "deepseek")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_MODEL_ID", "deepseek-v4-flash")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "2")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_LIGHT_DAILY_CANDIDATES", "2")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)

    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={},
            eval_mode=True,
        )
    )

    assert calls == 2
    assert result.reply_envelope.kind == "final_reply"
    assert result.trace["chat_kernel"]["attempts"][0]["quality"]["reason_codes"] == ["LLM_FAILED"]


@pytest.mark.asyncio
async def test_chat_kernel_v2_semantic_rerank_blocks_rejection_disconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**_kwargs):  # noqa: ANN001
        return SimpleNamespace(content="哦，我多管闲事了是吧。那我闭嘴咯。", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_SEMANTIC_RERANK_ENABLED", "true")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="不用你管",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "no_reply"
    assert result.reply_envelope.reason_code == "CHAT_KERNEL_QUALITY_REJECTED"


@pytest.mark.asyncio
async def test_chat_kernel_v2_extracts_sendable_dialogue_without_second_author(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[dict[str, str]]] = []

    async def _fake_complete(**kwargs):  # noqa: ANN001
        calls.append(list(kwargs.get("messages") or []))
        return SimpleNamespace(
            content="（被这句话刺到，愣了一下，然后有点赌气地）\n“哈？什么叫熟不熟啊？我就是会管你，不行吗？”",
            final_provider="mock",
            final_model="mock-chat",
        )

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_CANDIDATES", "1")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="你为什么要帮我，我们很熟吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert len(calls) == 1
    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "哈？什么叫熟不熟啊？我就是会管你，不行吗？"
    assert result.trace["chat_kernel"]["source"] == "dialogue_extraction"
    assert "dialogue_extraction" in result.reply_envelope.audit


@pytest.mark.asyncio
async def test_chat_kernel_v2_quality_rejects_without_fallback_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**_kwargs):  # noqa: ANN001
        return SimpleNamespace(content="reply_contract: 先接住用户", final_provider="mock", final_model="mock-chat")

    monkeypatch.setenv("HOST_CHAT_KERNEL_V2_STREAMING", "false")
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "no_reply"
    assert result.reply_envelope.text == ""
    assert result.reply_envelope.reason_code == "CHAT_KERNEL_QUALITY_REJECTED"


@pytest.mark.asyncio
async def test_chat_kernel_v2_uses_streaming_model_call_by_default_for_non_light_daily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_stream(**kwargs):  # noqa: ANN001
        assert kwargs["scene_type"] == "CHAT_KERNEL_V2"
        yield "在呀"
        yield "，尼尼喊我，我会回的。"

    async def _unexpected_complete(**_kwargs):  # noqa: ANN001
        raise AssertionError("streaming mode should not call completion")

    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_STREAMING", raising=False)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.astream_llm_chunks_with_pool", _fake_stream)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _unexpected_complete)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="帮我看看这个问题",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=InteractionFactsPlane(
                user_message="帮我看看这个问题",
                relationship_stage="FRIEND",
                relationship_state={"stage_label": "关系好的义兄妹"},
                social_act={"act_kind": "help_takeover"},
                conversation_interpretation={"literal_topic": "help", "interaction_move": "request_help"},
            ),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.kind == "final_reply"
    assert result.reply_envelope.text == "在呀，尼尼喊我，我会回的。"
    assert result.trace["chat_kernel"]["model_call_mode"] == "streaming"
    assert result.trace["chat_kernel"]["first_token_ms"] is not None


@pytest.mark.asyncio
async def test_chat_kernel_v2_uses_completion_for_light_daily_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_complete(**kwargs):  # noqa: ANN001
        assert kwargs["scene_type"] == "CHAT_KERNEL_V2"
        return SimpleNamespace(content="在呀，尼尼喊我，我会回的。", final_provider="mock", final_model="mock-chat")

    async def _unexpected_stream(**_kwargs):  # noqa: ANN001
        raise AssertionError("light daily mode should default to completion")
        yield ""

    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_STREAMING", raising=False)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.astream_llm_chunks_with_pool", _unexpected_stream)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.text == "在呀，尼尼喊我，我会回的。"
    assert result.trace["chat_kernel"]["model_call_mode"] == "completion"


@pytest.mark.asyncio
async def test_chat_kernel_v2_disables_gemini_streaming_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_complete(**kwargs):  # noqa: ANN001
        assert kwargs["provider_name"] == "gemini"
        return SimpleNamespace(content="在，尼尼喊我，我会回的。", final_provider="gemini", final_model="gemini-2.5-pro")

    async def _unexpected_stream(**_kwargs):  # noqa: ANN001
        raise AssertionError("Gemini streaming is disabled by default for chat kernel v2")
        yield ""

    monkeypatch.delenv("HOST_CHAT_KERNEL_V2_STREAMING", raising=False)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.chat_kernel_v2.astream_llm_chunks_with_pool", _unexpected_stream)
    result = await get_chat_kernel_v2().generate(
        ChatKernelRequest(
            request_id="req",
            session_id="sess",
            user_message="在吗",
            compiled_soul=_soul(),  # type: ignore[arg-type]
            interaction_facts=_facts(),
            response_form="text",
            render_profile="zh_text",
            llm_runtime={"provider_name": "gemini", "model_id": "gemini-2.5-pro"},
            eval_mode=True,
        )
    )

    assert result.reply_envelope.text == "在，尼尼喊我，我会回的。"
    assert result.trace["chat_kernel"]["model_call_mode"] == "completion"
