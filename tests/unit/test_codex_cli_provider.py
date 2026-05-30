from __future__ import annotations

import json
from pathlib import Path
import subprocess

from host_brain.app import graph_config_llm_runtime, provider_factory
from host_brain.integrations import codex_cli_provider, llm_helpers


def _install_fake_codex_cli(monkeypatch, tmp_path: Path, *, output_text: str) -> dict[str, object]:
    captured: dict[str, object] = {}
    monkeypatch.setenv("HOST_CODEX_CLI_LLM_ENABLED", "1")
    monkeypatch.setenv("HOST_CODEX_CLI_APP_SERVER_ENABLED", "0")
    monkeypatch.setenv("HOST_CODEX_CLI_RUN_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_provider, "_windows_npm_native_codex", lambda: None)
    monkeypatch.setattr(codex_cli_provider.shutil, "which", lambda _cmd: "codex")

    def _fake_run(cmd, *, input_text="", **kwargs):  # noqa: ANN001, ANN202
        captured.setdefault("cmds", []).append(list(cmd))
        captured["cmd"] = list(cmd)
        captured["input"] = input_text
        captured["timeout"] = kwargs.get("timeout_s")
        captured["env"] = dict(kwargs.get("env") or {})
        output_path = Path(cmd[cmd.index("-o") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout='{"type":"thread.started","thread_id":"session-1"}\n', stderr="")

    monkeypatch.setattr(codex_cli_provider, "_run_codex_cli_process", _fake_run)
    return captured


def test_codex_cli_provider_invokes_exec_without_api_key(monkeypatch, tmp_path) -> None:
    captured = _install_fake_codex_cli(
        monkeypatch,
        tmp_path,
        output_text='{"narrative":{"text":"在。"}}',
    )

    text = llm_helpers.invoke_llm_text(
        provider_name="codex",
        model_id="gpt-5.4-mini",
        api_key="",
        timeout_ms=1000,
        messages=[{"role": "user", "content": "在吗"}],
        response_format={"type": "json_object"},
    )

    assert json.loads(text)["narrative"]["text"] == "在。"
    assert captured["cmd"][:2] == ["codex", "exec"]
    assert ["-m", "gpt-5.4-mini"] == captured["cmd"][captured["cmd"].index("-m") : captured["cmd"].index("-m") + 2]
    assert captured["cmd"].count("--disable") == 3
    assert "plugins" in captured["cmd"]
    assert "shell_snapshot" in captured["cmd"]
    assert "personality" in captured["cmd"]
    assert "--enable" in captured["cmd"]
    assert "fast_mode" in captured["cmd"]
    assert "-c" in captured["cmd"]
    assert 'model_reasoning_effort="none"' in captured["cmd"]
    assert "--ephemeral" not in captured["cmd"]
    assert "--sandbox" in captured["cmd"]
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "CODEX_API_KEY" not in captured["env"]
    assert "Return exactly one valid JSON object" in str(captured["input"])


def test_codex_cli_provider_can_use_isolated_codex_home(monkeypatch, tmp_path) -> None:
    isolated_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOST_CODEX_CLI_HOME", str(isolated_home))
    captured = _install_fake_codex_cli(
        monkeypatch,
        tmp_path / "run-root",
        output_text='{"narrative":{"text":"隔离。"}}',
    )

    text = llm_helpers.invoke_llm_text(
        provider_name="codex",
        model_id="gpt-5.4-mini",
        api_key="",
        timeout_ms=1000,
        messages=[{"role": "user", "content": "在吗"}],
        response_format={"type": "json_object"},
    )

    assert json.loads(text)["narrative"]["text"] == "隔离。"
    assert captured["env"]["CODEX_HOME"] == str(isolated_home.resolve())
    assert isolated_home.exists()


def test_codex_cli_provider_inherits_system_proxy_when_env_missing(monkeypatch, tmp_path) -> None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    proxy_result = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    if hasattr(codex_cli_provider.urllib.request, "getproxies_registry"):
        monkeypatch.setattr(codex_cli_provider.urllib.request, "getproxies_registry", lambda: proxy_result)
    else:
        monkeypatch.setattr(codex_cli_provider.urllib.request, "getproxies", lambda: proxy_result)

    env = codex_cli_provider.build_codex_cli_env(tmp_path)

    assert env["HTTPS_PROXY"] == "http://127.0.0.1:7890"
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7890"
    assert env["https_proxy"] == "http://127.0.0.1:7890"
    assert env["http_proxy"] == "http://127.0.0.1:7890"


def test_codex_cli_provider_prefers_app_server_worker(monkeypatch, tmp_path) -> None:
    codex_cli_provider._reset_codex_app_server_workers_for_tests()  # noqa: SLF001
    instances: list[object] = []

    monkeypatch.setenv("HOST_CODEX_CLI_LLM_ENABLED", "1")
    monkeypatch.setenv("HOST_CODEX_CLI_APP_SERVER_ENABLED", "1")
    monkeypatch.setenv("HOST_CODEX_CLI_RUN_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_provider, "_windows_npm_native_codex", lambda: None)
    monkeypatch.setattr(codex_cli_provider.shutil, "which", lambda _cmd: "codex")

    class _FakeWorker:
        def __init__(self, **kwargs):  # noqa: ANN001, ANN204
            self.kwargs = dict(kwargs)
            self.calls: list[dict[str, object]] = []
            self.terminated = False
            instances.append(self)

        def generate(self, **kwargs):  # noqa: ANN001, ANN202
            self.calls.append(dict(kwargs))
            return '{"narrative":{"text":"常驻。"}}'

        def terminate(self) -> None:
            self.terminated = True

    def _fail_exec(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("exec fallback should not run when app-server succeeds")

    monkeypatch.setattr(codex_cli_provider, "_CodexAppServerWorker", _FakeWorker)
    monkeypatch.setattr(codex_cli_provider, "_run_codex_cli_process", _fail_exec)

    kwargs = {
        "model_id": "gpt-5.4-mini",
        "timeout_ms": 1000,
        "messages": [{"role": "user", "content": "在吗"}],
        "response_format": {"type": "json_object"},
        "feature_key": "pool:CHAT_SURFACE:A",
    }

    first = codex_cli_provider.invoke_codex_cli_text(**kwargs)
    second = codex_cli_provider.invoke_codex_cli_text(**kwargs)

    assert json.loads(first)["narrative"]["text"] == "常驻。"
    assert json.loads(second)["narrative"]["text"] == "常驻。"
    assert len(instances) == 1
    worker = instances[0]
    assert len(worker.calls) == 2
    assert worker.kwargs["feature_key"] == "pool:CHAT_SURFACE:A"
    assert "pool:CHAT_SURFACE:A" in worker.kwargs["session_key"]
    assert "Return exactly one valid JSON object" in worker.calls[0]["prompt"]
    codex_cli_provider._reset_codex_app_server_workers_for_tests()  # noqa: SLF001


def test_codex_app_server_worker_speaks_jsonrpc(monkeypatch, tmp_path) -> None:
    writes: list[dict[str, object]] = []

    class _FakeStdin:
        def write(self, text: str) -> None:
            writes.append(json.loads(text))

        def flush(self) -> None:
            return None

    class _FakeProc:
        pid = 1234

        def __init__(self) -> None:
            self.stdin = _FakeStdin()
            self.stdout = iter(
                [
                    json.dumps({"id": 1, "result": {}}) + "\n",
                    json.dumps({"id": 2, "result": {"thread": {"id": "thread-1"}}}) + "\n",
                    json.dumps({"id": 3, "result": {"turn": {"id": "turn-1"}}}) + "\n",
                    json.dumps(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": "thread-1",
                                "turnId": "turn-1",
                                "itemId": "item-1",
                                "delta": '{"ok":true}',
                            },
                        }
                    )
                    + "\n",
                    json.dumps(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": "thread-1",
                                "turn": {"id": "turn-1", "status": "completed"},
                            },
                        }
                    )
                    + "\n",
                    json.dumps({"id": 4, "result": {}}) + "\n",
                ]
            )
            self.stderr = iter(())

        def poll(self):  # noqa: ANN202
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout=None):  # noqa: ANN001, ANN202
            return 0

    monkeypatch.setattr(codex_cli_provider.subprocess, "Popen", lambda *_args, **_kwargs: _FakeProc())

    worker = codex_cli_provider._CodexAppServerWorker(  # noqa: SLF001
        command="codex",
        model_id="gpt-5.5",
        sandbox="read-only",
        workdir=tmp_path / "workspace",
        run_root=tmp_path,
        session_key="test-session",
        feature_key="inner_activity",
    )

    text = worker.generate(prompt="Return JSON", timeout_ms=1000, response_format={"type": "json_object"})

    assert text == '{"ok":true}'
    assert [item["method"] for item in writes] == ["initialize", "thread/start", "turn/start", "thread/rollback"]
    assert writes[2]["params"]["threadId"] == "thread-1"
    assert writes[2]["params"]["input"][0]["text"] == "Return JSON"
    assert writes[1]["params"]["config"]["tools"]["view_image"] is False
    assert writes[1]["params"]["config"]["tools"]["image_gen"] is False


def test_codex_cli_provider_maps_minimal_effort_for_gpt54_mini(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CODEX_CLI_REASONING_EFFORT", "minimal")

    assert codex_cli_provider.resolve_codex_cli_reasoning_effort("gpt-5.4-mini") == "none"
    assert codex_cli_provider.resolve_codex_cli_reasoning_effort("gpt-5.5") == "low"


def test_codex_cli_provider_defaults_to_gpt55_with_low_reasoning(monkeypatch) -> None:
    monkeypatch.delenv("HOST_CODEX_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_CODEX_RUNTIME_MODEL", raising=False)
    monkeypatch.delenv("HOST_CODEX_CLI_REASONING_EFFORT", raising=False)

    assert codex_cli_provider.resolve_codex_cli_model() == "gpt-5.5"
    assert codex_cli_provider.resolve_codex_cli_reasoning_effort("gpt-5.5") == "low"


def test_codex_cli_provider_maps_spark_effort_to_low(monkeypatch) -> None:
    monkeypatch.delenv("HOST_CODEX_CLI_REASONING_EFFORT", raising=False)

    assert codex_cli_provider.resolve_codex_cli_reasoning_effort("gpt-5.3-codex-spark") == "low"
    assert codex_cli_provider.resolve_codex_cli_reasoning_effort("gpt-5.3-codex-spark", "none") == "low"
    assert codex_cli_provider.resolve_codex_cli_reasoning_effort("gpt-5.3-codex-spark", "minimal") == "low"


def test_codex_cli_provider_reuses_recorded_session(monkeypatch, tmp_path) -> None:
    captured = _install_fake_codex_cli(
        monkeypatch,
        tmp_path,
        output_text='{"narrative":{"text":"还在。"}}',
    )

    kwargs = {
        "model_id": "gpt-5.4-mini",
        "timeout_ms": 1000,
        "messages": [{"role": "user", "content": "在吗"}],
        "response_format": {"type": "json_object"},
        "feature_key": "inner_activity",
    }

    codex_cli_provider.invoke_codex_cli_text(**kwargs)
    codex_cli_provider.invoke_codex_cli_text(**kwargs)

    first, second = captured["cmds"]
    assert first[:2] == ["codex", "exec"]
    assert first[2] != "resume"
    assert second[:3] == ["codex", "exec", "resume"]
    assert "session-1" in second


def test_llm_pool_keeps_codex_cli_candidate_without_key(monkeypatch, tmp_path) -> None:
    _install_fake_codex_cli(
        monkeypatch,
        tmp_path,
        output_text='{"narrative":{"text":"我在。"}}',
    )

    result = llm_helpers.complete_llm_with_pool_sync(
        request_id="test-codex-cli-pool",
        scene_type="CHAT_SURFACE",
        cognitive_tier="A",
        provider_name="codex",
        model_id="gpt-5.4-mini",
        api_key="",
        timeout_ms=1000,
        messages=[{"role": "user", "content": "在吗"}],
        response_format={"type": "json_object"},
    )

    assert result.pool_exhausted is False
    assert result.final_provider == "codex"
    assert json.loads(result.content)["narrative"]["text"] == "我在。"


def test_provider_factory_uses_codex_cli_when_key_missing(monkeypatch) -> None:
    monkeypatch.setattr(provider_factory, "codex_cli_lm_available", lambda: True)
    monkeypatch.setattr(
        provider_factory,
        "invoke_codex_cli_text",
        lambda **_kwargs: '{"narrative":{"text":"在。"},"intent_draft":{}}',
    )

    provider_fn = provider_factory.build_host_provider_fn_for_node(
        provider_name="codex",
        model_id="gpt-5.4-mini",
        api_key=None,
        timeout_ms=1000,
        node_id="chat_direct_ja",
    )

    result = json.loads(provider_fn({"input": {"user_message": "在吗"}}))

    assert result["narrative"]["text"] == "在。"
    assert result["meta"]["draft_only"] is True


def test_runtime_marks_codex_cli_available_without_key(monkeypatch) -> None:
    monkeypatch.setattr(graph_config_llm_runtime, "codex_cli_lm_available", lambda: True)

    assert graph_config_llm_runtime._is_provider_available(  # noqa: SLF001
        provider_name="codex",
        api_key=None,
        configured_available=True,
    )
