from __future__ import annotations

from host_brain.app.chat_agent import (
    SubAgentContext,
    _build_chat_agent_quality_fallback_reply,
)
from host_brain.app.chat_agent_preprocessor import (
    _build_deterministic_sub_agent_reply,
    _resolve_minimal_fallback_text,
)
from tests.text_quality_assertions import assert_natsume_style


def test_quality_fallback_preserves_successful_delegate_result() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="把 hello world 翻译成英文",
        sub_agent_ctx=SubAgentContext(
            agent_id="translate_agent",
            task="translate hello world",
            result_text="Hello, world.",
            success=True,
        ),
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == "Hello, world."


def test_quality_fallback_strips_delegate_markers_and_extracts_visible_text() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="帮我整理一下结果",
        sub_agent_ctx=SubAgentContext(
            agent_id="intent_agent",
            task="summarize result",
            result_text='[DELEGATE:intent_agent] {"narrative":{"text":"整理好了，重点是先检查配置，再重试。"}}',
            success=True,
        ),
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == "整理好了，重点是先检查配置，再重试。"


def test_quality_fallback_extracts_react_final_answer_from_json() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="给我代码",
        sub_agent_ctx=SubAgentContext(
            agent_id="codex_worker",
            task="write fibonacci helper",
            result_text='{"thought":"先写代码","action_type":"final_answer","final_answer":"好的，这是可直接运行的 Python 代码。"}',
            success=True,
        ),
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == "好的，这是可直接运行的 Python 代码。"


def test_quality_fallback_preserves_code_block_newlines() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="给我代码",
        sub_agent_ctx=SubAgentContext(
            agent_id="codex_worker",
            task="write fibonacci helper",
            result_text='{"thought":"先写代码","action_type":"final_answer","final_answer":"```python\\nprint(1)\\n```"}',
            success=True,
        ),
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == "```python\nprint(1)\n```"


def test_quality_fallback_hides_internal_react_error_text() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="翻译一下这句话",
        sub_agent_ctx=SubAgentContext(
            agent_id="translate_agent",
            task="translate sentence",
            result_text="[ReAct LLM 错误] Timeout while waiting for gateway",
            success=True,
        ),
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == _resolve_minimal_fallback_text(kind="default", render_target_lang="zh")
    assert_natsume_style(
        user_message="翻译一下这句话",
        reply=reply,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        required_markers=("我",),
    )


def test_quality_fallback_presence_reply_stays_in_natsume_voice() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="在吗",
        sub_agent_ctx=None,
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == _resolve_minimal_fallback_text(kind="presence", render_target_lang="zh")
    assert_natsume_style(
        user_message="在吗",
        reply=reply,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        max_chars=12,
    )


def test_quality_fallback_greeting_reply_stays_in_natsume_voice() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="你好",
        sub_agent_ctx=None,
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == _resolve_minimal_fallback_text(kind="greeting", render_target_lang="zh")
    assert_natsume_style(
        user_message="你好",
        reply=reply,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        max_chars=12,
    )


def test_deterministic_reply_returns_codex_worker_result_directly() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="codex_worker",
            task="fix crash",
            result_text="先把空值判断补上，再把异常分支提前 return。",
            success=True,
        ),
        render_target_lang="zh",
    )

    assert reply == "先把空值判断补上，再把异常分支提前 return。"


def test_deterministic_reply_returns_intent_agent_steps_directly() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="intent_agent",
            task="给我一个三步方案",
            result_text="1. 先确认现状。\n2. 再拆成今天能做的两步。\n3. 最后留一个复盘点。",
            success=True,
        ),
        render_target_lang="zh",
    )

    assert reply == "1. 先确认现状。\n2. 再拆成今天能做的两步。\n3. 最后留一个复盘点。"


def test_deterministic_reply_hides_intent_agent_result_for_relational_followup() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="intent_agent",
            task="判断是否还会继续帮忙",
            result_text='{"thought":"用户在确认帮助意愿","action_type":"final_answer","final_answer":"当然会继续帮你的。"}',
            success=True,
        ),
        render_target_lang="zh",
        user_message="那你还会帮我吗",
    )

    assert reply is None


def test_quality_fallback_does_not_surface_intent_agent_result_for_relational_followup() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="那你还会帮我吗",
        sub_agent_ctx=SubAgentContext(
            agent_id="intent_agent",
            task="判断是否还会继续帮忙",
            result_text='{"thought":"用户在确认帮助意愿","action_type":"final_answer","final_answer":"当然会继续帮你的。"}',
            success=True,
        ),
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert "当然会继续帮你" not in reply


def test_deterministic_reply_renders_open_app_success_cn() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="open_app",
            task="可以帮我打开本地的酷狗音乐吗",
            result_text="已经替你打开了。",
            success=True,
            payload={
                "result": {
                    "status": "OK",
                    "detail": {
                        "success": True,
                        "app_name": "酷狗音乐",
                        "message": "已经替你打开了。",
                    },
                }
            },
        ),
        render_target_lang="zh",
    )

    assert reply == "好，已经替你打开酷狗音乐了。"


def test_deterministic_reply_renders_open_app_failure_cn() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="open_app",
            task="帮我打开本地的不存在应用",
            result_text="",
            success=False,
            error="APP_LAUNCH_FAILED",
            payload={
                "result": {
                    "status": "FAILED",
                    "detail": {
                        "success": False,
                        "app_name": "不存在应用",
                        "detail": "unable to resolve local app target",
                    },
                }
            },
        ),
        render_target_lang="zh",
    )

    assert reply == "我试了一下，不过这边没把不存在应用打开。卡住的是：unable to resolve local app target。"

