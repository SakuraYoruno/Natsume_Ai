from __future__ import annotations

import json
import re
from unittest.mock import patch

from host_brain.app import top_agent
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _install_store(monkeypatch, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "top_agent.runtime_texts",
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_pick_non_repeating_variant_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        {
            "variant_selection": {
                "empty_variants_fallback": "custom-empty-variant",
            }
        },
    )

    assert top_agent._pick_non_repeating_variant(
        variants=[],
        last_reply="same",
        request_id="req-1",
    ) == "custom-empty-variant"


def test_maybe_build_phatic_fastpath_reply_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        {
            "phatic_fastpath": {
                "category_notes": {
                    "greeting": "custom-greeting-note",
                },
                "repeated_warning_template": " <dup:{last_assistant}>",
            }
        },
    )

    monkeypatch.setattr(top_agent, "_CONTINUATION_QUERY_RE", re.compile(r"^continue$"))
    monkeypatch.setattr(top_agent, "_GREETING_FASTPATH_RE", re.compile(r"^hello$"))
    monkeypatch.setattr(top_agent, "_PRESENCE_FASTPATH_RE", re.compile(r"^presence$"))
    monkeypatch.setattr(top_agent, "_ACK_FASTPATH_RE", re.compile(r"^ack$"))

    turns = [{"role": "assistant", "content": "last reply"}]
    with patch("host_brain.app.top_agent._load_continuity_turns", return_value=turns), patch(
        "host_brain.app.top_agent._looks_like_active_task_context",
        return_value=False,
    ):
        reply = top_agent._maybe_build_phatic_fastpath_reply(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="hello",
            render_target_lang="zh",
        )

    assert "custom-greeting-note" in str(reply)
    assert "<dup:last reply>" in str(reply)


def test_render_delegated_result_naturally_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        {
            "delegated_result": {
                "empty_visible_result": {
                    "code_agent": "custom-code-empty",
                    "default": "custom-default-empty",
                },
                "code_agent_prefix_template": "思路模板：{agent_result}",
            }
        },
    )

    with patch("host_brain.app.top_agent._extract_delegate_visible_text", return_value=""):
        assert top_agent._render_delegated_result_naturally(
            agent_id="code_agent",
            result_text="ignored",
            reply_template=None,
        ) == "custom-code-empty"
        assert top_agent._render_delegated_result_naturally(
            agent_id="planner",
            result_text="ignored",
            reply_template=None,
        ) == "custom-default-empty"

    with patch("host_brain.app.top_agent._extract_delegate_visible_text", return_value="final"):
        rendered = top_agent._render_delegated_result_naturally(
            agent_id="code_agent",
            result_text="ignored",
            reply_template=None,
        )

    assert rendered == "思路模板：final"


def test_task_progress_fastpath_uses_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        {
            "task_progress": {
                "selected_child": {
                    "failed_intro_template": "步骤{step_index}:{target_id}。",
                    "failed_status": " <failed> ",
                    "failed_error_template": "错误={error_text}。",
                    "failed_recovery_template": "恢复={recovery_text}。",
                }
            }
        },
    )

    monkeypatch.setattr(top_agent, "_TASK_PROGRESS_QUERY_RE", re.compile("progress"))
    monkeypatch.setattr(top_agent, "_TASK_FAILURE_QUERY_RE", re.compile("fail"))
    monkeypatch.setattr(top_agent, "_TASK_STEP_INDEX_RE", re.compile(r"step(\d+)"))

    class _Registry:
        def summarize_for_chat(self, **_kwargs):
            return {
                "status": "FAILED",
                "target_id": "deploy",
                "child_tasks": [
                    {
                        "target_id": "browser_navigate",
                        "status": "FAILED",
                        "error": "BOOM",
                        "recovery_result_text": "retry later",
                        "detail": "open page",
                        "meta": {"step_index": 2, "description": "open page"},
                    }
                ],
            }

    with patch("host_brain.app.dependencies.get_runtime_task_registry", return_value=_Registry()):
        result = top_agent._maybe_build_task_progress_fastpath(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="progress step2",
        )

    assert "步骤2:browser_navigate。" in str(result)
    assert "<failed>" in str(result)
    assert "错误=BOOM。" in str(result)
    assert "恢复=retry later。" in str(result)


def test_recent_turn_answer_uses_template_override(monkeypatch) -> None:
    store = _install_store(
        monkeypatch,
        {
            "recent_turn_answer": {
                "user_template": "custom-user={latest_user}",
                "both_user_item_template": "U={latest_user}",
                "both_assistant_item_template": "A={latest_assistant}",
                "both_wrapper_template": "BOTH::{facts}",
            }
        },
    )

    rows = [{"payload": {"input_preview": "u", "reply_preview": "a"}}]
    turns = [
        {"role": "user", "content": "latest user"},
        {"role": "assistant", "content": "latest assistant"},
    ]

    with patch("host_brain.app.top_agent_prompt._fetch_continuity_rows", return_value=rows), patch(
        "host_brain.app.top_agent_prompt._voice_rows_to_turns",
        return_value=turns,
    ), patch("host_brain.app.dependencies.get_ledger", return_value="LEDGER"), patch(
        "host_brain.app.dependencies.get_prompt_template_store",
        return_value=store,
    ), patch(
        "host_brain.app.top_agent._detect_recent_turn_query_role",
        side_effect=["user", "both"],
    ):
        user_answer = top_agent._maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        )
        both_answer = top_agent._maybe_build_recent_turn_answer(
            request_id="req",
            session_id="session",
            person_id="person",
            user_message="ignored",
            render_target_lang="zh",
        )

    assert user_answer == "custom-user=latest user"
    assert both_answer == "BOTH::U=latest user；A=latest assistant"


def test_builtin_top_agent_runtime_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("top_agent.runtime_texts")
    assert templates["variant_selection"]["empty_variants_fallback"] == "嗯……我在呢。"
    assert templates["task_progress"]["no_summary"] == "现在没有正在执行，或者刚结束但还值得汇报的任务。"
    assert templates["recent_turn_answer"]["user_template"] == "你上一句是「{latest_user}」。"
    assert templates["clarification_direct_answer"]["assistant_meaning_template"] == "我刚才的意思是：{latest_assistant}"
    assert templates["runtime_task_status"]["pre_dispatch_detail"] == "正在整理上下文和检索记忆。"
    assert templates["runtime_task_status"]["pre_dispatch_progress"] == "我正在整理上下文和检索记忆。"
    assert templates["runtime_task_status"]["failed_detail"] == "整条回复已经完成，但执行阶段有失败。"
    assert templates["runtime_task_status"]["completed_detail"] == "整条回复已经完成。"
    assert templates["llm_unavailable_fallback"] == "嗯……蓝现在脑子有点转不动，等一下再来找我好不好？"
