from __future__ import annotations

import time
from types import SimpleNamespace
import ast
from pathlib import Path
from contextlib import contextmanager
import os
import subprocess
import sys

import httpx
import pytest

from host_brain.pentest import kali_bridge
from host_brain.pentest import lab_isolation
from host_brain.pentest.agent.opsec import OpsecEngine
from host_brain.pentest.lab_isolation import (
    LabIsolationDenied,
    bind_executor_effect,
    bind_lab_capability,
    consume_executor_capability,
)
from host_brain.pentest.utils.http_client import PentestHTTPClient
from tests.task0491_capability_helper import issue_test_lab_capability


def _capability(
    *,
    tool_id: str,
    target: str,
    action: str,
    effect_args: dict[str, object] | None = None,
):
    return issue_test_lab_capability(
        tool_id=tool_id,
        target=target,
        action=action,
        effect_args=effect_args or {"target": target},
    )


@contextmanager
def _effect(capability):
    with bind_lab_capability(capability):
        with bind_executor_effect(
            capability.tool_id,
            capability.target,
            capability.action,
            capability.effect_args_digest,
        ):
            with consume_executor_capability():
                yield


@pytest.mark.parametrize("mode", ["READ_ONLY", "SHADOW", "invalid-mode"])
def test_http_boundary_rejects_before_client_or_dns(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", mode)
    client = PentestHTTPClient(retries=0)
    builds = 0

    def forbidden_build():
        nonlocal builds
        builds += 1
        raise AssertionError("network client must not be built")

    monkeypatch.setattr(client, "_build_client", forbidden_build)
    response = client.get("http://example.com/")

    assert builds == 0
    assert response.status_code == 0
    assert response.error and response.error.startswith("PENTEST_")


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.8/",
        "http://10.2.3.4/",
        "https://8.8.8.8/",
        "https://example.com/",
    ],
)
def test_lab_http_boundary_rejects_lan_public_and_dns_without_building_client(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    client = PentestHTTPClient(retries=0)
    builds = 0

    def forbidden_build():
        nonlocal builds
        builds += 1
        raise AssertionError("target policy must run first")

    monkeypatch.setattr(client, "_build_client", forbidden_build)
    cap = _capability(tool_id="pentest_http_probe", target="http://127.0.0.1:8080", action="http_request")
    with _effect(cap):
        response = client.get(url)

    assert builds == 0
    assert response.status_code == 0
    assert response.error and "REJECTED" in response.error


def test_redirect_target_is_reauthorized_before_second_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls: list[str] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, method: str, url: str, **_kwargs):
            calls.append(url)
            request = httpx.Request(method, url)
            return httpx.Response(
                302,
                headers={"location": "https://example.com/escape"},
                request=request,
            )

    client = PentestHTTPClient(retries=0, follow_redirects=True)
    monkeypatch.setattr(client, "_build_client", lambda: FakeClient())
    cap = _capability(tool_id="pentest_http_probe", target="http://127.0.0.1:8080/start", action="http_request")
    with _effect(cap):
        response = client.get("http://127.0.0.1:8080/start")

    assert calls == ["http://127.0.0.1:8080/start"]
    assert response.status_code == 0
    assert response.error and response.error.startswith("PENTEST_LAB_REDIRECT_REJECTED:")


def test_same_host_redirect_cannot_change_to_management_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls: list[str] = []

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def request(self, method: str, url: str, **_kwargs):
            calls.append(url)
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1:2375/containers/json"},
                request=httpx.Request(method, url),
            )

    client = PentestHTTPClient(retries=0, follow_redirects=True)
    monkeypatch.setattr(client, "_build_client", lambda: FakeClient())
    cap = _capability(
        tool_id="pentest_http_probe",
        target="http://127.0.0.1:8080/start",
        action="http_request",
    )
    with _effect(cap):
        response = client.get("http://127.0.0.1:8080/start")

    assert calls == ["http://127.0.0.1:8080/start"]
    assert response.error and "endpoint_mismatch" in response.error


@pytest.mark.parametrize("mode", ["READ_ONLY", "SHADOW", "invalid-mode"])
def test_kali_process_choke_point_rejects_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", mode)
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("subprocess must not be invoked")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    with pytest.raises(LabIsolationDenied, match="PENTEST_"):
        kali_bridge._checked_subprocess_run(["nmap", "127.0.0.1"])
    assert calls == 0


def test_process_requires_capability_and_executor_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("subprocess must not be invoked")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    with pytest.raises(LabIsolationDenied, match="missing_executor_binding"):
        kali_bridge._checked_subprocess_run(["nmap", "127.0.0.1"])
    assert calls == 0


def test_native_fixture_process_can_run_with_exact_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    monkeypatch.setenv("PATH", "C:\\attacker-bin" if os.name == "nt" else "/tmp/attacker-bin")
    monkeypatch.setenv("PENTEST_CALLER_SECRET", "must-not-leak")
    trusted_executable = str(Path(sys.executable).resolve())
    monkeypatch.setattr(
        lab_isolation,
        "_TRUSTED_EXECUTABLE_REGISTRY",
        {"nmap": (trusted_executable,)},
    )
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="fixture", stderr="")

    monkeypatch.setattr(kali_bridge.subprocess, "run", fake_run)
    cap = _capability(tool_id="pentest_service_detect", target="192.0.2.9", action="process_spawn")
    with _effect(cap):
        result = kali_bridge._checked_subprocess_run(
            ["nmap", "-sV", "192.0.2.9"],
            capture_output=True,
            text=True,
            encoding="UTF-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    assert result.returncode == 0
    assert len(calls) == 1
    command, run_kwargs = calls[0]
    assert command == [trusted_executable, "-sV", "192.0.2.9"]
    assert run_kwargs["cwd"] == str(lab_isolation._TRUSTED_SUBPROCESS_CWD.resolve())
    assert run_kwargs["stdin"] == subprocess.DEVNULL
    assert run_kwargs["close_fds"] is True
    assert run_kwargs["capture_output"] is True
    assert run_kwargs["text"] is True
    assert run_kwargs["timeout"] == 30.0
    assert run_kwargs["check"] is False
    safe_env = run_kwargs["env"]
    assert isinstance(safe_env, dict)
    assert safe_env["PATH"] != os.environ["PATH"]
    assert "PENTEST_CALLER_SECRET" not in safe_env


@pytest.mark.parametrize(
    "executable",
    [
        "/tmp/nmap",
        "C:\\attacker\\nmap.exe",
        ".\\nmap",
        "tools/nmap",
        "..\\nmap",
        "nmap:alternate-stream",
    ],
)
def test_process_rejects_absolute_relative_and_drive_qualified_executables(
    monkeypatch: pytest.MonkeyPatch,
    executable: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("untrusted executable must not run")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    cap = _capability(tool_id="pentest_service_detect", target="127.0.0.1", action="process_spawn")
    with _effect(cap):
        with pytest.raises(LabIsolationDenied, match="PENTEST_LAB_PROCESS_REJECTED"):
            kali_bridge._checked_subprocess_run([executable, "-sV", "127.0.0.1"])
    assert calls == 0


def test_process_rejects_unresolved_logical_executable_without_path_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    monkeypatch.setenv("PATH", str(Path(sys.executable).resolve().parent))
    monkeypatch.setattr(lab_isolation, "_TRUSTED_EXECUTABLE_REGISTRY", {"nmap": ()})
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("ambient PATH must not resolve executables")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    cap = _capability(tool_id="pentest_service_detect", target="127.0.0.1", action="process_spawn")
    with _effect(cap):
        with pytest.raises(LabIsolationDenied, match="executable_untrusted"):
            kali_bridge._checked_subprocess_run(["nmap", "-sV", "127.0.0.1"])
    assert calls == 0


@pytest.mark.parametrize(
    "unsafe_kwargs",
    [
        {"executable": "nmap"},
        {"env": {"PATH": "/tmp/attacker"}},
        {"cwd": "/tmp"},
        {"shell": False},
        {"preexec_fn": lambda: None},
        {"pass_fds": ()},
        {"start_new_session": False},
        {"creationflags": 0},
        {"startupinfo": None},
        {"stdin": None},
        {"input": "fixture"},
        {"stdout": subprocess.PIPE},
        {"stderr": subprocess.PIPE},
    ],
    ids=[
        "executable",
        "env",
        "cwd",
        "shell",
        "preexec_fn",
        "pass_fds",
        "start_new_session",
        "creationflags",
        "startupinfo",
        "stdin",
        "input",
        "stdout",
        "stderr",
    ],
)
def test_process_rejects_execution_semantics_kwargs(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kwargs: dict[str, object],
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("unsafe subprocess kwargs must not run")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    cap = _capability(tool_id="pentest_service_detect", target="127.0.0.1", action="process_spawn")
    with _effect(cap):
        with pytest.raises(LabIsolationDenied, match="PENTEST_LAB_PROCESS_REJECTED:kwarg"):
            kali_bridge._checked_subprocess_run(
                ["nmap", "-sV", "127.0.0.1"],
                **unsafe_kwargs,
            )
    assert calls == 0


@pytest.mark.parametrize(
    ("invalid_kwargs", "reason"),
    [
        ({"capture_output": False}, "capture_output"),
        ({"text": False}, "text_mode"),
        ({"encoding": "latin-1"}, "encoding"),
        ({"errors": "ignore"}, "errors"),
        ({"check": True}, "check_mode"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": -1}, "timeout"),
        ({"timeout": 601}, "timeout"),
        ({"timeout": "5"}, "timeout"),
        ({"timeout": True}, "timeout"),
    ],
)
def test_process_validates_allowed_kwarg_types_and_ranges(
    monkeypatch: pytest.MonkeyPatch,
    invalid_kwargs: dict[str, object],
    reason: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid subprocess kwargs must not run")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    cap = _capability(tool_id="pentest_service_detect", target="127.0.0.1", action="process_spawn")
    with _effect(cap):
        with pytest.raises(LabIsolationDenied, match=reason):
            kali_bridge._checked_subprocess_run(
                ["nmap", "-sV", "127.0.0.1"],
                **invalid_kwargs,
            )
    assert calls == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["docker", "run", "--rm", "image", "nmap", "127.0.0.1"],
        ["wsl", "-d", "kali-linux", "--", "nmap", "127.0.0.1"],
        ["nmap", "--privileged", "127.0.0.1"],
    ],
)
def test_container_and_escape_processes_are_rejected_even_with_capability(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("container process must not be invoked")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    cap = _capability(tool_id="pentest_service_detect", target="127.0.0.1", action="process_spawn")
    with _effect(cap):
        with pytest.raises(LabIsolationDenied, match="PENTEST_LAB_PROCESS_REJECTED"):
            kali_bridge._checked_subprocess_run(argv)
    assert calls == 0


@pytest.mark.parametrize(
    "argv",
    [
        ["python", "-c", "open('escape','w')", "127.0.0.1"],
        ["curl", "--url=http://8.8.8.8", "127.0.0.1"],
        ["nmap", "-iL", "targets.txt", "127.0.0.1"],
        ["nmap", "--script=../../escape.nse", "127.0.0.1"],
        ["nmap", "--script-args-file=secrets.txt", "127.0.0.1"],
        ["nmap", "--datadir", "C:/escape", "127.0.0.1"],
        ["nmap", "-oN", "C:/escape.txt", "127.0.0.1"],
        ["nmap", "--url=http://8.8.8.8", "127.0.0.1"],
    ],
)
def test_process_executable_and_argv_grammar_reject_escape_vectors(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("escape process must not run")

    monkeypatch.setattr(kali_bridge.subprocess, "run", forbidden_run)
    cap = _capability(
        tool_id="pentest_service_detect",
        target="127.0.0.1",
        action="process_spawn",
    )
    with _effect(cap):
        with pytest.raises(LabIsolationDenied, match="PENTEST_LAB_PROCESS_REJECTED"):
            kali_bridge._checked_subprocess_run(argv)
    assert calls == 0


def test_metasploit_rpc_mutation_and_missing_binding_never_open_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    import urllib.request

    calls = 0

    def forbidden_opener(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("urllib backend must not open")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden_opener)
    mutation = kali_bridge.MetasploitWrapper._rpc_call("module.execute", "token", "exploit", {})
    metadata = kali_bridge.MetasploitWrapper._rpc_call("module.search", "token", "fixture")
    assert mutation["error"] == "PENTEST_LAB_RPC_REJECTED:indirect_effect"
    assert str(metadata["error"]).endswith("read_only")
    assert calls == 0


def test_opsec_direct_docker_bypass_is_rejected_before_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    import subprocess

    calls = 0

    def forbidden_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("opsec docker subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    output = OpsecEngine._docker_run("pentest-container", "whoami")
    assert calls == 0
    assert "PENTEST_LAB_PROCESS_REJECTED:read_only" in output


def test_reverse_shell_listener_rejects_before_socket_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    from host_brain.pentest import reverse_shell

    calls = 0

    def forbidden_socket(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("listener socket must not be created")

    monkeypatch.setattr(reverse_shell.socket, "socket", forbidden_socket)
    result = reverse_shell.ReverseShellListener("127.0.0.1").start_listener(port=0)
    assert calls == 0
    assert result["status"] == "rejected"
    assert result["error"] == "PENTEST_LAB_LISTENER_REJECTED:read_only"


def test_oob_listener_rejects_wildcard_before_http_server_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    from host_brain.pentest import oob_server

    calls = 0

    def forbidden_server(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("OOB HTTP server must not be created")

    monkeypatch.setattr(oob_server, "_ThreadingHTTPServer", forbidden_server)
    server = oob_server.OOBCallbackServer(port=0, bind="0.0.0.0")
    server.start()
    assert calls == 0
    assert server.is_running() is False
    assert server.last_error == "PENTEST_LAB_LISTENER_REJECTED:read_only"
    assert server.host == "127.0.0.1"


def test_dns_entrypoints_reject_before_resolver_and_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    from host_brain.pentest.utils import dns_resolver

    calls = 0

    def forbidden(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("DNS transport must not be called")

    monkeypatch.setattr(dns_resolver.socket, "getaddrinfo", forbidden)
    if dns_resolver._HAS_DNSPYTHON:
        monkeypatch.setattr(dns_resolver.dns.resolver, "resolve", forbidden)
        monkeypatch.setattr(dns_resolver.dns.query, "xfr", forbidden)

    assert dns_resolver.resolve_A("example.com") == []
    assert dns_resolver.resolve_MX("example.com") == []
    assert dns_resolver.resolve_PTR("8.8.8.8") is None
    assert dns_resolver.resolve_all("example.com")["error"].endswith("read_only")
    assert dns_resolver.check_AXFR("example.com", "8.8.8.8")["error"].endswith("read_only")
    assert calls == 0


def test_direct_raw_socket_helpers_reject_before_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    from host_brain.pentest import phase3_misconfig, phase3_request_smuggling
    from host_brain.pentest.tools import tool_executors

    create_calls = 0
    socket_calls = 0

    def forbidden_create(*_args, **_kwargs):
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("raw connection must not be created")

    def forbidden_socket(*_args, **_kwargs):
        nonlocal socket_calls
        socket_calls += 1
        raise AssertionError("raw socket must not be created")

    monkeypatch.setattr(phase3_misconfig.socket, "create_connection", forbidden_create)
    monkeypatch.setattr(tool_executors.socket, "create_connection", forbidden_create)
    monkeypatch.setattr(tool_executors.socket, "socket", forbidden_socket)

    assert phase3_misconfig.audit_tls("127.0.0.1")["status"] == "rejected"
    assert phase3_request_smuggling._send_raw_http("127.0.0.1", 80, "GET / HTTP/1.0\r\n\r\n") is None
    assert tool_executors._read_tcp_banner("127.0.0.1", 80) == ""
    assert tool_executors._udp_probe_port("127.0.0.1", 53) is None
    assert create_calls == 0
    assert socket_calls == 0


def test_private_playwright_and_direct_ldap_paths_are_stably_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    import asyncio
    from host_brain.pentest.tools.headless_crawler import _crawl_with_playwright
    from host_brain.pentest.phase5_ad_advanced import cross_forest_enum

    browser = asyncio.run(_crawl_with_playwright("http://127.0.0.1:8080/"))
    ldap = cross_forest_enum("127.0.0.1", "fixture.test")
    assert browser["error"] == "PENTEST_LAB_BROWSER_REJECTED:backend_not_isolated"
    assert ldap["status"] == "rejected"
    assert str(ldap["error"]).endswith("read_only")


def test_subprocess_calls_are_confined_to_checked_choke_functions() -> None:
    root = Path(__file__).resolve().parents[2]
    pentest_root = root / "host_brain/pentest"
    raw_calls: list[tuple[Path, str]] = []
    for path in pentest_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "subprocess" or node.func.attr != "run":
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents.get(parent)
            function_name = parent.name if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            raw_calls.append((path, function_name))
    assert raw_calls == [(pentest_root / "lab_isolation.py", "run_isolated_subprocess")]

    raw_urlopen_calls: list[Path] = []
    for path in pentest_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "urlopen":
                raw_urlopen_calls.append(path)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "urlopen":
                raw_urlopen_calls.append(path)
    assert raw_urlopen_calls == []
