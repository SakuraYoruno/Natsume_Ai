from __future__ import annotations

import json

from host_brain.app.handlers import repair_agent_handler as mod
from host_brain.app.prompt_template_store import PromptTemplateStore, resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _install_store(monkeypatch, content: dict) -> PromptTemplateStore:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "repair_agent_handler.runtime_texts",
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)
    return store


def test_repair_browser_backend_attempts_install_and_succeeds(monkeypatch) -> None:
    commands = []
    state = {"playwright": False}

    def fake_which(name):
        return None

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        joined = " ".join(command)
        if "pip install playwright" in joined or ("-m" in command and "playwright" in command and "install" in command):
            state["playwright"] = True
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(mod.shutil, "which", fake_which)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        mod.importlib.util,
        "find_spec",
        lambda name: object() if (name == "playwright" and state["playwright"]) else None,
    )
    monkeypatch.setattr(mod, "_resolve_python_executable", lambda: "python")

    result = mod._repair_browser_backend()

    assert result["success"] is True
    assert ["python", "-m", "pip", "install", "playwright"] in commands
    assert ["python", "-m", "playwright", "install", "chromium"] in commands


def test_repair_agent_handler_returns_success_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "_build_repair_result",
        lambda **_kwargs: {
            "success": True,
            "summary": "浏览器后端已经补齐，可以重试原任务。",
            "detail": "ok",
            "actions": [],
            "ok_actions": 0,
        },
    )

    out = mod.repair_agent_handler(
        {"user_input": "打开网页", "request_id": "req-001"},
        {"request_id": "req-001", "previous_agent_result": "BROWSER_UNAVAILABLE"},
    )

    assert out["react_scratchpad"]["decision_summary"] == "浏览器后端已经补齐，可以重试原任务。"
    assert out["agent_output"]["text"] == "浏览器后端已经补齐，可以重试原任务。"
    assert out["repair_agent_trace"]["success"] is True


def test_build_repair_result_uses_llm_planner_for_unknown_error(monkeypatch) -> None:
    monkeypatch.setenv(mod._REPAIR_PLANNER_ENABLED_ENV, "true")

    fake_plan = {
        "repair_type": "pip_install",
        "commands": [{"cmd": ["python", "-m", "pip", "install", "requests"], "timeout_s": 60, "desc": "安装 requests"}],
        "summary": "已安装缺失的 requests 包",
        "can_retry": True,
    }
    monkeypatch.setattr(mod, "_call_repair_planner_llm", lambda **_kwargs: fake_plan)

    executed_cmds = []

    def fake_run_command(cmd, *, timeout_s=120):
        executed_cmds.append(cmd)
        return {"command": cmd, "returncode": 0, "stdout": "ok", "stderr": "", "ok": True}

    monkeypatch.setattr(mod, "_run_command", fake_run_command)

    result = mod._build_repair_result(task_text="发送 HTTP 请求", previous_error="ModuleNotFoundError: requests")

    assert result["success"] is True
    assert result["ok_actions"] == 1
    assert executed_cmds == [["python", "-m", "pip", "install", "requests"]]


def test_build_repair_result_llm_planner_disabled_falls_back(monkeypatch) -> None:
    monkeypatch.setenv(mod._REPAIR_PLANNER_ENABLED_ENV, "false")

    result = mod._build_repair_result(task_text="某任务", previous_error="UNKNOWN_ERROR_XYZ")

    assert result["success"] is False
    assert "unsupported" in result["detail"]


def test_build_repair_result_llm_planner_returns_none_falls_back(monkeypatch) -> None:
    monkeypatch.setenv(mod._REPAIR_PLANNER_ENABLED_ENV, "true")
    monkeypatch.setattr(mod, "_call_repair_planner_llm", lambda **_kwargs: None)

    result = mod._build_repair_result(task_text="某任务", previous_error="SOME_UNKNOWN_ERROR")

    assert result["success"] is False
    assert result["ok_actions"] == 0


def test_is_safe_command_blocks_dangerous_commands() -> None:
    assert mod._is_safe_command(["python", "-m", "pip", "install", "requests"]) is True
    assert mod._is_safe_command(["rm", "-rf", "/"]) is False
    assert mod._is_safe_command(["cmd", "/c", "format", "C:"]) is False


def test_execute_repair_plan_blocks_dangerous_command() -> None:
    plan = {
        "repair_type": "system_command",
        "commands": [{"cmd": ["rm", "-rf", "/tmp/test"], "timeout_s": 10, "desc": "危险命令"}],
        "summary": "危险操作",
        "can_retry": False,
    }

    result = mod._execute_repair_plan(plan)

    assert result["ok_actions"] == 0
    assert result["actions"][0]["stderr"] == "BLOCKED_BY_SAFETY_GATE"


def test_call_repair_planner_llm_uses_cache_on_second_call(monkeypatch) -> None:
    mod._repair_plan_cache.clear()

    call_count = [0]
    fake_plan = {
        "repair_type": "pip_install",
        "commands": [{"cmd": ["python", "-m", "pip", "install", "foo"], "timeout_s": 60}],
        "summary": "安装 foo",
        "can_retry": True,
    }

    def fake_llm_call(task_text, previous_error):
        call_count[0] += 1
        return fake_plan

    monkeypatch.setattr(mod, "_call_repair_planner_llm", fake_llm_call)
    monkeypatch.setenv(mod._REPAIR_PLANNER_ENABLED_ENV, "true")
    monkeypatch.setattr(
        mod,
        "_run_command",
        lambda cmd, **_: {"command": cmd, "returncode": 0, "stdout": "", "stderr": "", "ok": True},
    )

    mod._build_repair_result(task_text="任务A", previous_error="ModuleNotFoundError: foo")
    assert call_count[0] == 1

    mod._build_repair_result(task_text="任务B", previous_error="ModuleNotFoundError: foo")
    assert call_count[0] == 1

    mod._repair_plan_cache.clear()


def test_build_repair_result_retries_llm_after_cache_expired(monkeypatch) -> None:
    mod._repair_plan_cache.clear()

    call_count = [0]
    fake_plan = {
        "repair_type": "pip_install",
        "commands": [{"cmd": ["python", "-m", "pip", "install", "foo"], "timeout_s": 60}],
        "summary": "安装 foo",
        "can_retry": True,
    }

    def fake_llm_call(task_text, previous_error):
        call_count[0] += 1
        return fake_plan

    current_ts = 2000.0
    fingerprint = mod._get_error_fingerprint("ModuleNotFoundError: foo")
    mod._repair_plan_cache[fingerprint] = (fake_plan, current_ts - mod._REPAIR_CACHE_TTL_S - 1)

    monkeypatch.setattr(mod.time, "time", lambda: current_ts)
    monkeypatch.setattr(mod, "_call_repair_planner_llm", fake_llm_call)
    monkeypatch.setenv(mod._REPAIR_PLANNER_ENABLED_ENV, "true")
    monkeypatch.setattr(
        mod,
        "_run_command",
        lambda cmd, **_: {"command": cmd, "returncode": 0, "stdout": "", "stderr": "", "ok": True},
    )

    mod._build_repair_result(task_text="任务A", previous_error="ModuleNotFoundError: foo")

    assert call_count[0] == 1

    mod._repair_plan_cache.clear()


def test_execute_repair_plan_partial_success_allows_retry(monkeypatch) -> None:
    call_results = [True, False]
    idx = [0]

    def fake_run_command(cmd, *, timeout_s=120):
        ok = call_results[idx[0]]
        idx[0] += 1
        return {"command": cmd, "returncode": 0 if ok else 1, "stdout": "", "stderr": "", "ok": ok}

    monkeypatch.setattr(mod, "_run_command", fake_run_command)

    plan = {
        "repair_type": "pip_install",
        "commands": [
            {"cmd": ["python", "-m", "pip", "install", "pkg_a"], "timeout_s": 60},
            {"cmd": ["python", "-m", "pip", "install", "pkg_b"], "timeout_s": 60},
        ],
        "summary": "",
        "can_retry": True,
    }

    result = mod._execute_repair_plan(plan)

    assert result["success"] is True
    assert result["all_success"] is False
    assert result["ok_actions"] == 1
    assert result["can_retry"] is True


def test_repair_agent_handler_trace_contains_can_retry(monkeypatch) -> None:
    monkeypatch.setattr(
        mod,
        "_build_repair_result",
        lambda **_kwargs: {
            "success": True,
            "all_success": True,
            "summary": "修复完成，可以重试。",
            "detail": "llm_planner:pip_install ok=1/1",
            "actions": [],
            "ok_actions": 1,
            "can_retry": True,
        },
    )

    out = mod.repair_agent_handler(
        {"user_input": "运行脚本", "request_id": "req-002"},
        {"request_id": "req-002", "previous_agent_result": "ModuleNotFoundError: requests"},
    )

    trace = out["repair_agent_trace"]
    assert "can_retry" in trace
    assert trace["can_retry"] is True
    assert trace["all_success"] is True
