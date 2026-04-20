from host_brain.app.chat_agent import (
    _build_context_fast_prompt,
    _build_direct_fast_prompt,
    _resolve_compact_prompt_speed_profile,
    _should_use_direct_fast_prompt_path,
)


def test_should_use_direct_fast_prompt_path_requires_direct_fast_without_subagent() -> None:
    assert _should_use_direct_fast_prompt_path(
        intent_draft={"chat_speed_profile": "direct_fast"},
        sub_agent_ctx=None,
        deterministic_reply="",
    )
    assert not _should_use_direct_fast_prompt_path(
        intent_draft={"chat_speed_profile": "direct_fast"},
        sub_agent_ctx=object(),
        deterministic_reply="",
    )
    assert not _should_use_direct_fast_prompt_path(
        intent_draft={"chat_speed_profile": "default"},
        sub_agent_ctx=None,
        deterministic_reply="",
    )


def test_build_direct_fast_prompt_keeps_persona_and_brief_reply_contract() -> None:
    prompt_text, system_prompt, gen_params, runtime_payload = _build_direct_fast_prompt(
        user_message="今天有点累，抱抱我吧。",
        render_target_lang="zh",
        emotion_context={"emotion_tag": "TIRED"},
        bond_summary={"stage": "FRIEND"},
    )

    assert prompt_text == "今天有点累，抱抱我吧。"
    assert "你是夏目蓝" in system_prompt
    assert "当前关系阶段：FRIEND" in system_prompt
    assert "当前情绪：TIRED" in system_prompt
    assert "当前本地时间：" in system_prompt
    assert "1 到 3 句" in system_prompt
    assert "像年上的老师或姐姐" in system_prompt
    assert "不要甜腻撒娇" in system_prompt
    assert "只输出最终会对用户说的话" in system_prompt
    assert gen_params == {}
    assert runtime_payload == {}


def test_resolve_compact_prompt_speed_profile_supports_context_fast() -> None:
    assert _resolve_compact_prompt_speed_profile(
        intent_draft={"chat_speed_profile": "context_fast"},
        sub_agent_ctx=None,
        deterministic_reply="",
    ) == "context_fast"


def test_build_context_fast_prompt_keeps_persona_and_mid_chat_contract() -> None:
    prompt_text, system_prompt, gen_params, runtime_payload = _build_context_fast_prompt(
        user_message="我今天其实有点想和你认真聊聊最近的状态。",
        render_target_lang="zh",
        emotion_context={"emotion_tag": "TIRED"},
        bond_summary={"stage": "FRIEND"},
    )

    assert prompt_text == "我今天其实有点想和你认真聊聊最近的状态。"
    assert "你是夏目蓝" in system_prompt
    assert "中速快路径" in system_prompt
    assert "当前本地时间：" in system_prompt
    assert "1 到 3 句" in system_prompt
    assert "不要主动扩成长独白" in system_prompt
    assert "不要过度分析" in system_prompt
    assert "像年上的老师或姐姐" in system_prompt
    assert gen_params == {}
    assert runtime_payload["compact_prompt_profile"] == "context_fast"
    assert runtime_payload["response_policy"]["max_sentences"] == 3
    assert runtime_payload["response_policy"]["max_chars"] == 110
    assert runtime_payload["response_policy"]["allow_topic_shift"] is False
