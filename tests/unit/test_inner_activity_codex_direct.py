from __future__ import annotations

import json

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations import codex_cli_provider
from host_brain.persona import inner_activity_agent as agent


def _reset_inner_activity_state() -> None:
    with agent._INNER_ACTIVITY_LLM_CACHE_LOCK:
        agent._INNER_ACTIVITY_LLM_CACHE["resolved_at_ms"] = 0
        agent._INNER_ACTIVITY_LLM_CACHE["runtime"] = None
    with agent._INNER_ACTIVITY_RESULT_CACHE_LOCK:
        agent._INNER_ACTIVITY_RESULT_CACHE.clear()
    with agent._INNER_ACTIVITY_DIRECT_CALL_LOCK:
        agent._INNER_ACTIVITY_LAST_DIRECT_CALL_MS = 0


def test_inner_activity_runtime_defaults_to_codex_direct(monkeypatch) -> None:
    _reset_inner_activity_state()
    monkeypatch.setattr(codex_cli_provider.shutil, "which", lambda name: "C:/tools/codex.exe" if name == "codex" else None)
    monkeypatch.setenv("HOST_CODEX_RUNTIME_MODEL", "gpt-5.4-mini")
    monkeypatch.delenv("HOST_CODEX_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_AGENT_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_CODEX_MODEL_ID", raising=False)

    runtime = agent._resolve_inner_activity_llm_runtime(now_ms=1800000000000)

    assert runtime is not None
    assert runtime["available"] is True
    assert runtime["provider_name"] == "codex_cli"
    assert runtime["model_id"] == "gpt-5.4-mini"
    assert runtime["llm_api_key"] == ""
    assert runtime["pool_bypass"] is True
    assert runtime["route_reason_code"] == "INNER_ACTIVITY_CODEX_CLI"


def test_inner_activity_runtime_default_codex_model_is_gpt55(monkeypatch) -> None:
    _reset_inner_activity_state()
    monkeypatch.setattr(codex_cli_provider.shutil, "which", lambda name: "C:/tools/codex.exe" if name == "codex" else None)
    monkeypatch.delenv("HOST_CODEX_RUNTIME_MODEL", raising=False)
    monkeypatch.delenv("HOST_CODEX_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_AGENT_PROVIDER", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_AGENT_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_CODEX_CLI_MODEL", raising=False)
    monkeypatch.delenv("HOST_INNER_ACTIVITY_CODEX_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_INNER_WORLD_LLM_MODEL_ID", raising=False)

    runtime = agent._resolve_inner_activity_llm_runtime(now_ms=1800000000000)

    assert runtime is not None
    assert runtime["available"] is True
    assert runtime["model_id"] == "gpt-5.5"


def test_run_inner_activity_llm_calls_codex_cli_and_logs_tokens(monkeypatch) -> None:
    _reset_inner_activity_state()
    monkeypatch.setenv("HOST_INNER_ACTIVITY_CODEX_MIN_INTERVAL_MS", "0")
    monkeypatch.setenv("HOST_INNER_ACTIVITY_RESULT_CACHE_TTL_MS", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setattr(agent.shutil, "which", lambda name: "C:/tools/codex.exe" if name == "codex" else None)
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)

    captured: dict[str, object] = {}

    def _fake_invoke_codex_cli_text(**kwargs):  # noqa: ANN003, ANN202
        captured.update(kwargs)
        return json.dumps({"note": "我先把节奏压稳一点。", "focus": "plan"}, ensure_ascii=False)

    monkeypatch.setattr(codex_cli_provider, "invoke_codex_cli_text", _fake_invoke_codex_cli_text)
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.invoke_llm_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"http provider should not be used: {kwargs}")),
    )
    monkeypatch.setattr(
        "host_brain.integrations.llm_helpers.complete_llm_with_pool_sync",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"pool should not be used: {kwargs}")),
    )

    raw = agent._run_inner_activity_llm(
        provider_name="codex_cli",
        model_id="gpt-5.4-mini",
        api_key="",
        timeout_ms=5000,
        prompt_text='{"goal":"generate_inner_activity_note"}',
        system_prompt="json only",
        gen_params={"temperature": 0.2},
        base_url_override=None,
    )

    assert "我先把节奏压稳一点" in raw
    assert captured["model_id"] == "gpt-5.4-mini"
    assert captured["timeout_ms"] == 5000
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["feature_key"] == "inner_activity"
    assert "Return exactly one compact JSON object" in str(captured["messages"])
    assert ledger.llm_pool_call_log == []
    assert ledger.llm_token_usage_log[-1]["scene_type"] == "INNER_ACTIVITY"
    assert ledger.llm_token_usage_log[-1]["provider"] == "codex_cli"


def test_inner_activity_direct_result_cache_prevents_repeat_codex_call(monkeypatch) -> None:
    _reset_inner_activity_state()
    monkeypatch.setenv("HOST_INNER_ACTIVITY_CODEX_MIN_INTERVAL_MS", "0")
    monkeypatch.setenv("HOST_INNER_ACTIVITY_RESULT_CACHE_TTL_MS", "60000")
    monkeypatch.setattr(codex_cli_provider.shutil, "which", lambda name: "C:/tools/codex.exe" if name == "codex" else None)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: SQLiteLedger(":memory:", check_same_thread=False))

    calls = {"count": 0}

    def _fake_invoke_codex_cli_text(**_kwargs):  # noqa: ANN003, ANN202
        calls["count"] += 1
        return json.dumps({"note": "先别急着把话说满。", "focus": "self"}, ensure_ascii=False)

    monkeypatch.setattr(codex_cli_provider, "invoke_codex_cli_text", _fake_invoke_codex_cli_text)

    kwargs = {
        "provider_name": "codex_cli",
        "model_id": "gpt-5.4-mini",
        "api_key": "",
        "timeout_ms": 5000,
        "prompt_text": '{"goal":"generate_inner_activity_note","same":true}',
        "system_prompt": "json only",
        "gen_params": {"temperature": 0.2},
        "base_url_override": None,
    }

    first = agent._run_inner_activity_llm(**kwargs)
    second = agent._run_inner_activity_llm(**kwargs)

    assert first == second
    assert calls["count"] == 1
