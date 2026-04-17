from __future__ import annotations

from host_brain.app.chat_agent import (
    SubAgentContext,
    _build_chat_agent_quality_fallback_reply,
)
from host_brain.app.chat_agent_preprocessor import _build_deterministic_sub_agent_reply
from host_brain.app.prompt_template_store import resolve_prompt_template_text
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
            agent_id="codex_codegen",
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
            agent_id="codex_codegen",
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

    assert reply == resolve_prompt_template_text("chat_agent.minimal_fallback.default")
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

    assert reply == resolve_prompt_template_text("chat_agent.minimal_fallback.presence")
    assert_natsume_style(
        user_message="在吗",
        reply=reply,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        max_chars=12,
        required_markers=("我",),
    )


def test_quality_fallback_greeting_reply_stays_in_natsume_voice() -> None:
    reply = _build_chat_agent_quality_fallback_reply(
        user_message="你好",
        sub_agent_ctx=None,
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    assert reply == resolve_prompt_template_text("chat_agent.minimal_fallback.greeting")
    assert_natsume_style(
        user_message="你好",
        reply=reply,
        emotion_tag="neutral",
        bond_stage="STRANGER",
        max_chars=12,
    )


def test_deterministic_reply_returns_codex_codegen_result_directly() -> None:
    reply = _build_deterministic_sub_agent_reply(
        SubAgentContext(
            agent_id="codex_codegen",
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
