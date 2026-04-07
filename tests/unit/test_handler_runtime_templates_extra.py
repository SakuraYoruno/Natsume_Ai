from __future__ import annotations

import json

from host_brain.app.handlers.chat_agent_handler import (
    _resolve_chat_agent_draft_summary_default,
    _resolve_chat_agent_error_summary,
)
from host_brain.app.handlers.code_agent_handler import _resolve_code_agent_error_summary
from host_brain.app.handlers.emotion_agent_handler import _resolve_emotion_agent_error_summary
from host_brain.app.handlers.intent_agent_handler import _resolve_intent_agent_error_summary
from host_brain.app.handlers.judge_agent_handler import (
    _build_judge_user_input,
    _resolve_judge_system_prompt,
)
from host_brain.app.handlers.retrieval_agent_handler import (
    _resolve_retrieval_agent_error_summary,
    _resolve_retrieval_agent_system_prompt,
)
from host_brain.app.handlers.supervisor_dispatch_handler import (
    _build_supervisor_user_prompt,
    _resolve_supervisor_system_prompt,
)
from host_brain.app.handlers.translate_agent_handler import (
    _resolve_translate_agent_error_summary,
    _resolve_translate_agent_system_prompt,
)
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _install_store(monkeypatch, template_id: str, content: str) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(template_id, content=content, enabled=True)

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_retrieval_runtime_templates_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "retrieval_agent_handler.runtime_texts",
        json.dumps(
            {
                "system_prompt_extra": "custom retrieval prompt",
                "handler_error_summary": "custom retrieval error",
            },
            ensure_ascii=False,
        ),
    )

    assert _resolve_retrieval_agent_system_prompt() == "custom retrieval prompt"
    assert _resolve_retrieval_agent_error_summary() == "custom retrieval error"


def test_translate_runtime_templates_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "translate_agent_handler.runtime_texts",
        json.dumps(
            {
                "system_prompt_extra": "custom translate prompt",
                "handler_error_summary": "custom translate error",
            },
            ensure_ascii=False,
        ),
    )

    assert _resolve_translate_agent_system_prompt() == "custom translate prompt"
    assert _resolve_translate_agent_error_summary() == "custom translate error"


def test_chat_runtime_templates_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "chat_agent_handler.runtime_texts",
        json.dumps(
            {
                "draft_summary_default": "custom chat summary",
                "handler_error_summary": "custom chat error",
            },
            ensure_ascii=False,
        ),
    )

    assert _resolve_chat_agent_draft_summary_default() == "custom chat summary"
    assert _resolve_chat_agent_error_summary() == "custom chat error"


def test_error_summary_templates_override(monkeypatch) -> None:
    _install_store(monkeypatch, "code_agent_handler.error_summary", "custom code error")
    assert _resolve_code_agent_error_summary() == "custom code error"

    _install_store(monkeypatch, "intent_agent_handler.error_summary", "custom intent error")
    assert _resolve_intent_agent_error_summary() == "custom intent error"

    _install_store(monkeypatch, "emotion_agent_handler.error_summary", "custom emotion error")
    assert _resolve_emotion_agent_error_summary() == "custom emotion error"


def test_judge_prompt_templates_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "judge_agent_handler.prompt_templates",
        json.dumps(
            {
                "system_prompt_extra": "custom judge prompt",
                "user_input_template": "ARTS={artifact_count}|DESC={artifacts_desc}|JSON={artifacts_json}",
            },
            ensure_ascii=False,
        ),
    )

    user_input = _build_judge_user_input(
        {
            "code_agent": {
                "confidence": 0.9,
                "summary": "code summary",
                "risk_flags": [],
            }
        }
    )

    assert _resolve_judge_system_prompt() == "custom judge prompt"
    assert user_input.startswith("ARTS=1|DESC=- code_agent:")
    assert "|JSON={" in user_input


def test_supervisor_prompt_templates_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "supervisor_dispatch_handler.prompt_templates",
        json.dumps(
            {
                "system_prompt": "custom supervisor prompt",
                "user_prompt_template": "SCENE={scene_type}|INPUT={user_input}",
            },
            ensure_ascii=False,
        ),
    )

    assert _resolve_supervisor_system_prompt() == "custom supervisor prompt"
    assert _build_supervisor_user_prompt("WORK_CODE", "write code") == "SCENE=WORK_CODE|INPUT=write code"


def test_builtin_handler_runtime_templates_are_clean() -> None:
    chat_templates = resolve_prompt_template_json_merged("chat_agent_handler.runtime_texts")
    retrieval_templates = resolve_prompt_template_json_merged("retrieval_agent_handler.runtime_texts")
    translate_templates = resolve_prompt_template_json_merged("translate_agent_handler.runtime_texts")
    judge_templates = resolve_prompt_template_json_merged("judge_agent_handler.prompt_templates")
    supervisor_templates = resolve_prompt_template_json_merged("supervisor_dispatch_handler.prompt_templates")

    assert chat_templates["draft_summary_default"] == "聊天回复已生成"
    assert retrieval_templates["system_prompt_extra"] == "你是检索专家，负责从知识库中提取相关信息。"
    assert translate_templates["handler_error_summary"] == "TranslateAgent 执行异常"
    assert judge_templates["system_prompt_extra"].startswith("你是裁判专家")
    assert supervisor_templates["user_prompt_template"] == "场景类型：{scene_type}\n用户输入：{user_input}"
