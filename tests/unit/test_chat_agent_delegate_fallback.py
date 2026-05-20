from __future__ import annotations

from host_brain.app.chat_agent import (
    SubAgentContext,
    _build_chat_agent_quality_fallback_reply,
    _build_deterministic_sub_agent_reply as _build_runtime_deterministic_sub_agent_reply,
)
from host_brain.app.chat_agent_preprocessor import (
    _build_deterministic_sub_agent_reply,
)


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


def test_deterministic_reply_formats_system_info_result() -> None:
    ctx = SubAgentContext(
        agent_id="system_info",
        task="查一下系统状态",
        result_text="{'cpu': {'percent': 56.2}}",
        success=True,
        payload={
            "result": {
                "status": "OK",
                "action": "info",
                "detail": {
                    "platform": {"system": "Windows", "release": "10", "machine": "AMD64"},
                    "cpu": {"count_logical": 16, "count_physical": 8, "percent": 56.2},
                    "memory": {"total_gb": 31.15, "available_gb": 9.96, "percent": 68.0},
                    "disk": {"total_gb": 1562.07, "free_gb": 633.17, "percent": 59.5},
                },
            },
        },
    )

    reply = _build_deterministic_sub_agent_reply(ctx, render_target_lang="zh")

    assert reply is not None
    assert "系统状态查到了" in reply
    assert "CPU 56.2%" in reply
    assert "内存 68.0%" in reply
    assert "磁盘 59.5%" in reply


def test_runtime_deterministic_reply_formats_system_info_result() -> None:
    ctx = SubAgentContext(
        agent_id="system_info",
        task="查一下系统状态",
        result_text="{'cpu': {'percent': 12.3}}",
        success=True,
        payload={
            "result": {
                "status": "OK",
                "action": "info",
                "detail": {
                    "cpu": {"count_logical": 16, "count_physical": 8, "percent": 12.3},
                    "memory": {"total_gb": 32.0, "available_gb": 20.0, "percent": 37.5},
                },
            },
        },
    )

    reply = _build_runtime_deterministic_sub_agent_reply(ctx, render_target_lang="zh")

    assert reply is not None
    assert "系统状态查到了" in reply
    assert "CPU 12.3%" in reply
    assert "内存 37.5%" in reply


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
