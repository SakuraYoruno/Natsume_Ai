from __future__ import annotations

from host_brain.app.chat_agent import (
    SubAgentContext,
    _build_chat_agent_quality_fallback_reply,
)
from host_brain.app.prompt_template_store import resolve_prompt_template_text


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
            agent_id="code_agent",
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
            agent_id="code_agent",
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
