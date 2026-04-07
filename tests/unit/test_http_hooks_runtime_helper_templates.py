from __future__ import annotations

import json
import logging

from host_brain.app.http_hooks_runtime_helpers import _build_llm_generator
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_build_llm_generator_uses_prompt_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "http_hooks_runtime_helpers.proactive_talk_llm_prompt",
        content=json.dumps(
            {
                "time_period_line": "T={time_period};",
                "greeting_line": "U={user_name};",
                "bond_stage_line": "B={stage}|{hint};",
                "emotion_hint_line": "E={emotion_hint};",
                "motive_hint_line": "M={motive_hint};",
                "recent_topics_line": "R={topics_str};",
                "topic_hint_line": "H={topic_hint};",
                "requirements_line": "REQ",
                "prompt_separator": "",
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    captured: dict[str, object] = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'data: {"phase":"RENDER","chunk":"hello"}\n'

    def _fake_urlopen(req, timeout=30):
        captured["timeout"] = timeout
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr("host_brain.app.http_hooks_runtime_helpers._urlreq.urlopen", _fake_urlopen)

    generator = _build_llm_generator(
        logger=logging.getLogger("tests.http_hooks_runtime_helper_templates"),
        recent_topics=["话题A", "话题B"],
        diary_snippet="",
        dream_snippet="",
    )
    result = generator(
        {
            "time_period": "morning",
            "user_name": "阿夏",
            "topic_hint": "天气",
            "bond_summary": {"stage": "FRIEND", "capability_hint": "可以亲近一点"},
            "emotion_hint": "calm",
            "motive_hint": "reach_out",
        }
    )

    assert result == {"greeting": "hello", "content": "hello"}
    assert captured["timeout"] == 30
    assert captured["payload"]["message"] == (
        "T=morning;"
        "U=阿夏;"
        "B=FRIEND|可以亲近一点;"
        "E=calm;"
        "M=reach_out;"
        "R=话题A、话题B;"
        "H=天气;"
        "REQ"
    )


def test_builtin_proactive_talk_prompt_templates_are_clean() -> None:
    templates = resolve_prompt_template_json_merged("http_hooks_runtime_helpers.proactive_talk_llm_prompt")
    assert templates["time_period_line"] == "你是夏目藍，现在是{time_period}时段。"
    assert templates["greeting_line"] == "请主动发起一句话问候{user_name}。"
    assert templates["requirements_line"] == "要求：自然、温暖、20字以内，不要重复打招呼模板。"
