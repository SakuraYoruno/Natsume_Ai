"""Codex CLI backed text generation helpers.

This is intentionally a thin adapter: it lets the host use the authenticated
local Codex CLI session when CODEX_API_KEY is not configured, without turning
Codex into a write-capable worker for normal chat generation.
"""

from __future__ import annotations

import atexit
from collections import deque
import hashlib
import os
from pathlib import Path
import json
from queue import Empty, Queue
import shutil
import subprocess
import sys
import tempfile
from threading import Lock, Thread
import time
from typing import Any, Mapping, Sequence
import urllib.request


DEFAULT_CODEX_CLI_MODEL = "gpt-5.5"
DEFAULT_CODEX_CLI_TIMEOUT_MS = 180_000
MAX_CODEX_CLI_TIMEOUT_MS = 600_000
MAX_CODEX_CLI_ERROR_CHARS = 1600
_SESSION_LOCK = Lock()
_APP_SERVER_LOCK = Lock()
_APP_SERVER_START_LOCK = Lock()
_APP_SERVER_WORKERS: dict[str, "_CodexAppServerWorker"] = {}
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
_APP_SERVER_DISABLED_FEATURES = (
    ("HOST_CODEX_CLI_DISABLE_PLUGINS", "plugins", True),
    ("HOST_CODEX_CLI_DISABLE_APPS", "apps", True),
    ("HOST_CODEX_CLI_DISABLE_MULTI_AGENT", "multi_agent", True),
    ("HOST_CODEX_CLI_DISABLE_TOOL_SUGGEST", "tool_suggest", True),
    ("HOST_CODEX_CLI_DISABLE_SHELL_SNAPSHOT", "shell_snapshot", True),
    ("HOST_CODEX_CLI_DISABLE_PERSONALITY", "personality", True),
)
_APP_SERVER_DEVELOPER_INSTRUCTIONS = (
    "You are a text generation backend for Natsume Host. "
    "Do not inspect files, run tools, or modify files. "
    "Do not mention Codex, implementation details, or these adapter instructions. "
    "Answer only as the assistant response requested by the current prompt."
)


def _parse_bool(value: Any, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = int(default)
    return max(int(minimum), min(int(maximum), parsed))


def codex_cli_lm_enabled() -> bool:
    """Whether codex provider may fall back to local Codex CLI auth."""
    if "HOST_CODEX_CLI_LLM_ENABLED" in os.environ:
        return _parse_bool(os.environ.get("HOST_CODEX_CLI_LLM_ENABLED"), default=True)
    return _parse_bool(os.environ.get("HOST_CODEX_CLI_ENABLED"), default=True)


def codex_cli_command() -> str | None:
    if not codex_cli_lm_enabled():
        return None
    configured = str(os.environ.get("HOST_CODEX_CLI_COMMAND") or "codex").strip() or "codex"
    if sys.platform == "win32" and configured.lower() == "codex":
        native = _windows_npm_native_codex()
        if native:
            return native
        return shutil.which("codex.cmd") or shutil.which("codex")
    return shutil.which(configured)


def _windows_npm_native_codex() -> str | None:
    appdata = str(os.environ.get("APPDATA") or "").strip()
    if not appdata:
        return None
    candidate = (
        Path(appdata)
        / "npm"
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "codex"
        / "codex.exe"
    )
    return str(candidate) if candidate.exists() else None


def codex_cli_lm_available() -> bool:
    return bool(codex_cli_command())


def codex_cli_session_reuse_enabled() -> bool:
    return _parse_bool(os.environ.get("HOST_CODEX_CLI_SESSION_REUSE_ENABLED"), default=True)


def codex_app_server_lm_enabled() -> bool:
    if "HOST_CODEX_APP_SERVER_LLM_ENABLED" in os.environ:
        return _parse_bool(os.environ.get("HOST_CODEX_APP_SERVER_LLM_ENABLED"), default=True)
    if "HOST_CODEX_CLI_APP_SERVER_ENABLED" in os.environ:
        return _parse_bool(os.environ.get("HOST_CODEX_CLI_APP_SERVER_ENABLED"), default=True)
    return True


def codex_app_server_thread_reuse_enabled() -> bool:
    return _parse_bool(os.environ.get("HOST_CODEX_APP_SERVER_THREAD_REUSE_ENABLED"), default=True)


def codex_app_server_rollback_after_turn_enabled() -> bool:
    return _parse_bool(os.environ.get("HOST_CODEX_APP_SERVER_ROLLBACK_AFTER_TURN"), default=True)


def codex_exec_fallback_enabled() -> bool:
    return _parse_bool(os.environ.get("HOST_CODEX_CLI_EXEC_FALLBACK_ENABLED"), default=True)


def resolve_codex_cli_model(model_id: str | None = None) -> str:
    return (
        str(
            model_id
            or os.environ.get("HOST_CODEX_MODEL_ID")
            or os.environ.get("HOST_CODEX_RUNTIME_MODEL")
            or DEFAULT_CODEX_CLI_MODEL
        ).strip()
        or DEFAULT_CODEX_CLI_MODEL
    )


def resolve_codex_cli_reasoning_effort(model_id: str | None = None, effort: str | None = None) -> str:
    raw = str(effort or os.environ.get("HOST_CODEX_CLI_REASONING_EFFORT") or "").strip().lower()
    normalized_model = str(model_id or "").strip().lower()
    if not raw or raw in {"default", "inherit"}:
        if normalized_model.startswith("gpt-5.3-codex-spark"):
            raw = "low"
        elif normalized_model.startswith("gpt-5.5"):
            raw = "low"
        else:
            raw = "none"
    if raw in {"none", "minimal"} and normalized_model.startswith("gpt-5.3-codex-spark"):
        return "low"
    if raw == "minimal" and normalized_model.startswith("gpt-5.5"):
        return "low"
    if raw == "minimal" and normalized_model == "gpt-5.4-mini":
        return "none"
    return raw if raw in _REASONING_EFFORTS else "none"


def resolve_codex_cli_timeout_ms(timeout_ms: int | None = None) -> int:
    configured = os.environ.get("HOST_CODEX_CLI_TIMEOUT_MS")
    if configured is not None:
        return _parse_int(
            configured,
            default=int(timeout_ms or DEFAULT_CODEX_CLI_TIMEOUT_MS),
            minimum=1_000,
            maximum=MAX_CODEX_CLI_TIMEOUT_MS,
        )
    if timeout_ms is not None and int(timeout_ms or 0) > 0:
        return _parse_int(timeout_ms, default=DEFAULT_CODEX_CLI_TIMEOUT_MS, minimum=1_000, maximum=MAX_CODEX_CLI_TIMEOUT_MS)
    return DEFAULT_CODEX_CLI_TIMEOUT_MS


def _resolve_run_root() -> Path:
    raw = str(os.environ.get("HOST_CODEX_CLI_RUN_ROOT") or "").strip()
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "natsume_codex_cli_lm"


def resolve_codex_cli_home() -> Path | None:
    raw = str(os.environ.get("HOST_CODEX_CLI_HOME") or os.environ.get("HOST_CODEX_APP_SERVER_CODEX_HOME") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _resolve_workdir(run_root: Path) -> Path:
    raw = str(os.environ.get("HOST_CODEX_CLI_WORKDIR") or "").strip()
    if raw:
        return Path(raw)
    return run_root / "workspace"


def _session_state_path(run_root: Path) -> Path:
    raw = str(os.environ.get("HOST_CODEX_CLI_SESSION_STATE_PATH") or "").strip()
    if raw:
        return Path(raw)
    return run_root / "sessions.json"


def _read_session_state(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), Mapping) else payload
    return {str(k): str(v) for k, v in dict(sessions).items() if str(k).strip() and str(v).strip()}


def _write_session_state(path: Path, sessions: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"sessions": dict(sessions)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _session_key(
    *,
    feature_key: str | None,
    model_id: str,
    sandbox: str,
    workdir: Path,
    response_format: Any = None,
) -> str:
    configured = str(os.environ.get("HOST_CODEX_CLI_SESSION_KEY") or "").strip()
    if configured:
        feature = configured
    else:
        feature = str(feature_key or "").strip()
    if not feature:
        feature = "json_object" if _wants_json_object(response_format) else "text"
    try:
        workdir_text = str(workdir.resolve())
    except Exception:
        workdir_text = str(workdir)
    return f"{feature}|model={model_id}|sandbox={sandbox}|cwd={workdir_text}"


def _app_server_key(
    *,
    command: str,
    model_id: str,
    sandbox: str,
    workdir: Path,
) -> str:
    """Return the process-level cache key for one Codex app-server.

    Feature identity intentionally does not participate in this key. Feature
    isolation is handled by per-feature thread ids inside the shared server
    process, which prevents background pools from spawning one app-server per
    feature.
    """
    try:
        workdir_text = str(workdir.resolve())
    except Exception:
        workdir_text = str(workdir)
    codex_home = resolve_codex_cli_home()
    codex_home_text = ""
    if codex_home is not None:
        try:
            codex_home_text = str(codex_home.expanduser().resolve())
        except Exception:
            codex_home_text = str(codex_home.expanduser())
    disabled_features = ",".join(
        f"{feature_name}={int(_parse_bool(os.environ.get(env_name), default=default))}"
        for env_name, feature_name, default in _APP_SERVER_DISABLED_FEATURES
    )
    return "|".join(
        (
            f"command={command}",
            f"home={codex_home_text}",
            f"model={model_id}",
            f"sandbox={sandbox}",
            f"cwd={workdir_text}",
            f"reasoning={resolve_codex_cli_reasoning_effort(model_id)}",
            f"disabled={disabled_features}",
            f"fast={int(_parse_bool(os.environ.get('HOST_CODEX_CLI_FAST_MODE'), default=True))}",
            f"web_disabled={int(_parse_bool(os.environ.get('HOST_CODEX_CLI_DISABLE_WEB_SEARCH'), default=True))}",
        )
    )


def _get_session_id(path: Path, key: str) -> str | None:
    with _SESSION_LOCK:
        return _read_session_state(path).get(key)


def _set_session_id(path: Path, key: str, session_id: str | None) -> None:
    with _SESSION_LOCK:
        sessions = _read_session_state(path)
        if session_id:
            sessions[key] = session_id
        else:
            sessions.pop(key, None)
        _write_session_state(path, sessions)


def _extract_thread_id(stdout: str) -> str | None:
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and payload.get("type") == "thread.started":
            thread_id = str(payload.get("thread_id") or "").strip()
            if thread_id:
                return thread_id
    return None


def _message_content_to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                text = item.get("text")
                if text is None:
                    text = item.get("content")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, Mapping):
        text = value.get("text")
        if text is None:
            text = value.get("content")
        return str(text or "")
    return str(value or "")


def _wants_json_object(response_format: Any = None) -> bool:
    if isinstance(response_format, Mapping):
        return str(response_format.get("type") or "").strip().lower() == "json_object"
    return str(response_format or "").strip().lower() == "json_object"


def _apply_lightweight_flags(cmd: list[str], *, resume: bool) -> list[str]:
    insert_at = 3 if resume else 2
    flags: list[str] = []
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_DISABLE_PLUGINS"), default=True):
        flags.extend(["--disable", "plugins"])
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_DISABLE_SHELL_SNAPSHOT"), default=True):
        flags.extend(["--disable", "shell_snapshot"])
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_DISABLE_PERSONALITY"), default=True):
        flags.extend(["--disable", "personality"])
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_FAST_MODE"), default=True):
        flags.extend(["--enable", "fast_mode"])
    if flags:
        cmd[insert_at:insert_at] = flags
    return cmd


def _apply_reasoning_effort(cmd: list[str], *, model_id: str | None = None) -> list[str]:
    reasoning_effort = resolve_codex_cli_reasoning_effort(model_id)
    if not reasoning_effort or reasoning_effort.lower() in {"inherit", "default"}:
        return cmd
    anchor = cmd.index("-m") if "-m" in cmd else len(cmd)
    cmd[anchor:anchor] = ["-c", f'model_reasoning_effort="{reasoning_effort}"']
    return cmd


def _build_codex_app_server_command(command: str, *, model_id: str) -> list[str]:
    reasoning_effort = resolve_codex_cli_reasoning_effort(model_id)
    cmd = [command, "app-server"]
    for env_name, feature_name, default in _APP_SERVER_DISABLED_FEATURES:
        if _parse_bool(os.environ.get(env_name), default=default):
            cmd.extend(["--disable", feature_name])
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_FAST_MODE"), default=True):
        cmd.extend(["--enable", "fast_mode"])
    cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_DISABLE_WEB_SEARCH"), default=True):
        cmd.extend(["-c", 'web_search="disabled"'])
    cmd.extend(["--listen", "stdio://"])
    return cmd


def _wrap_windows_command(cmd: list[str]) -> list[str]:
    if sys.platform == "win32" and str(cmd[0]).strip().lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/d", "/s", "/c", *cmd]
    return cmd


def _build_codex_cli_env(run_dir: Path) -> dict[str, str]:
    return build_codex_cli_env(run_dir)


def _proxy_env_present(env: Mapping[str, str]) -> bool:
    return any(str(env.get(name) or "").strip() for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"))


def _inherit_system_proxy_env(env: dict[str, str]) -> None:
    if _proxy_env_present(env):
        return
    proxy_inherit_flag = os.environ.get("HOST_CODEX_CLI_INHERIT_SYSTEM_PROXY_ENABLED")
    if proxy_inherit_flag is None:
        proxy_inherit_flag = os.environ.get("HOST_CODEX_CLI_INHERIT_SYSTEM_PROXY")
    if not _parse_bool(proxy_inherit_flag, default=True):
        return
    try:
        if sys.platform == "win32" and hasattr(urllib.request, "getproxies_registry"):
            proxies = urllib.request.getproxies_registry()
        else:
            proxies = urllib.request.getproxies()
    except Exception:
        return
    if not isinstance(proxies, Mapping):
        return
    http_proxy = str(proxies.get("http") or "").strip()
    https_proxy = str(proxies.get("https") or http_proxy or "").strip()
    all_proxy = str(proxies.get("all") or "").strip()
    no_proxy = str(proxies.get("no") or proxies.get("no_proxy") or "").strip()
    if https_proxy:
        env.setdefault("HTTPS_PROXY", https_proxy)
        env.setdefault("https_proxy", https_proxy)
    if http_proxy:
        env.setdefault("HTTP_PROXY", http_proxy)
        env.setdefault("http_proxy", http_proxy)
    if all_proxy:
        env.setdefault("ALL_PROXY", all_proxy)
        env.setdefault("all_proxy", all_proxy)
    if no_proxy:
        env.setdefault("NO_PROXY", no_proxy)
        env.setdefault("no_proxy", no_proxy)


def build_codex_cli_env(run_dir: Path, *, temp_dir_name: str = ".tmp") -> dict[str, str]:
    env = dict(os.environ)
    _inherit_system_proxy_env(env)
    codex_home = resolve_codex_cli_home()
    if codex_home is not None:
        codex_home.mkdir(parents=True, exist_ok=True)
        env["CODEX_HOME"] = str(codex_home.resolve())
    if _parse_bool(os.environ.get("HOST_CODEX_CLI_STRIP_API_KEYS"), default=True):
        for key in (
            "OPENAI_API_KEY",
            "CODEX_API_KEY",
            "GEMINI_API_KEY",
            "DEEPSEEK_API_KEY",
            "MINIMAX_API_KEY",
            "ANTHROPIC_API_KEY",
        ):
            env.pop(key, None)
    temp_dir = run_dir / temp_dir_name
    temp_dir.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(temp_dir.resolve())
    env["TMP"] = str(temp_dir.resolve())
    env["TMPDIR"] = str(temp_dir.resolve())
    return env


def _subprocess_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _compact_process_detail(*values: Any) -> str:
    text = "\n".join(str(value or "").strip() for value in values if str(value or "").strip())
    return " ".join(text.split())[:MAX_CODEX_CLI_ERROR_CHARS]


def _terminate_codex_cli_processes(proc: subprocess.Popen[str], *, marker: str | None = None) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                creationflags=_subprocess_creationflags(),
            )
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            marker_text = str(marker or "").replace("'", "''").strip()
            if marker_text:
                ps = (
                    "$marker = '"
                    + marker_text
                    + "'; "
                    + "Get-CimInstance Win32_Process | "
                    + "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($marker) -and ($_.Name -eq 'node.exe' -or $_.Name -like 'codex*.exe') } | "
                    + "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                    creationflags=_subprocess_creationflags(),
                )
        else:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_codex_cli_process(
    cmd: list[str],
    *,
    input_text: str,
    workdir: Path,
    env: Mapping[str, str],
    timeout_s: float,
    marker: str | None,
) -> subprocess.CompletedProcess[str]:
    popen_cmd = _wrap_windows_command(cmd)
    proc = subprocess.Popen(
        popen_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(workdir),
        env=dict(env),
        creationflags=_subprocess_creationflags(),
    )
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=max(1.0, float(timeout_s)))
    except subprocess.TimeoutExpired as exc:
        _terminate_codex_cli_processes(proc, marker=marker)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = "", ""
        detail = _compact_process_detail(stdout, stderr)
        raise TimeoutError(f"Codex CLI timed out after {int(float(timeout_s) * 1000)}ms: {detail}") from exc
    return subprocess.CompletedProcess(cmd, int(proc.returncode or 0), stdout=stdout, stderr=stderr)


def _worker_dir_name(session_key: str) -> str:
    digest = hashlib.sha1(str(session_key or "codex").encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"app-server-{digest}"


class _CodexAppServerWorker:
    def __init__(
        self,
        *,
        command: str,
        model_id: str,
        sandbox: str,
        workdir: Path,
        run_root: Path,
        server_key: str | None = None,
        feature_key: str | None = None,
        session_key: str | None = None,
    ) -> None:
        resolved_server_key = str(server_key or session_key or "codex").strip() or "codex"
        self.command = command
        self.model_id = model_id
        self.sandbox = sandbox
        self.workdir = workdir
        self.run_root = run_root
        self.server_key = resolved_server_key
        self.session_key = str(session_key or resolved_server_key).strip() or resolved_server_key
        self.feature_key = feature_key
        self.run_dir = run_root / _worker_dir_name(resolved_server_key)
        self.thread_reuse = codex_app_server_thread_reuse_enabled()
        self.thread_ids: dict[str, str] = {}
        self.proc: subprocess.Popen[str] | None = None
        self._stdout_queue: Queue[str] = Queue()
        self._stderr_tail: deque[str] = deque(maxlen=80)
        self._request_id = 0
        self._lock = Lock()

    def terminate(self) -> None:
        proc = self.proc
        self.proc = None
        self.thread_ids.clear()
        if proc and proc.poll() is None:
            _terminate_codex_cli_processes(proc, marker=str(self.run_dir.resolve()))

    def generate(
        self,
        *,
        prompt: str,
        timeout_ms: int,
        thread_key: str | None = None,
        response_format: Any = None,
    ) -> str:
        timeout_s = max(1.0, resolve_codex_cli_timeout_ms(timeout_ms) / 1000.0)
        deadline = time.monotonic() + timeout_s
        resolved_thread_key = str(thread_key or self.session_key or self.server_key).strip() or self.server_key
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self._ensure_started_locked(deadline=deadline)
            else:
                with _APP_SERVER_START_LOCK:
                    self._ensure_started_locked(deadline=deadline)
            thread_id = self._ensure_thread_locked(thread_key=resolved_thread_key, deadline=deadline)
            result = self._run_turn_locked(
                thread_id=thread_id,
                prompt=prompt,
                response_format=response_format,
                deadline=deadline,
            )
            return result

    def _ensure_started_locked(self, *, deadline: float) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.terminate()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._stdout_queue = Queue()
        self._stderr_tail = deque(maxlen=80)
        self._request_id = 0
        cmd = _build_codex_app_server_command(self.command, model_id=self.model_id)
        env = _build_codex_cli_env(self.run_dir)
        self.proc = subprocess.Popen(
            _wrap_windows_command(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.workdir),
            env=env,
            creationflags=_subprocess_creationflags(),
        )
        Thread(target=self._read_stdout, name="codex-app-server-stdout", daemon=True).start()
        Thread(target=self._read_stderr, name="codex-app-server-stderr", daemon=True).start()
        (self.run_dir / "command.json").write_text(
            json.dumps(
                {
                    "cmd": cmd,
                    "feature_key": self.feature_key,
                    "model_id": self.model_id,
                    "pid": self.proc.pid,
                    "server_key": self.server_key,
                    "codex_home": env.get("CODEX_HOME"),
                    "thread_reuse": self.thread_reuse,
                    "thread_isolation": "per-feature-thread-key",
                    "transport": "app-server",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self._request_locked(
            "initialize",
            {
                "clientInfo": {"name": "natsume-host", "title": None, "version": "0"},
                "capabilities": {"experimentalApi": False, "optOutNotificationMethods": []},
            },
            deadline=deadline,
        )

    def _ensure_thread_locked(self, *, thread_key: str, deadline: float) -> str:
        resolved_thread_key = str(thread_key or "default").strip() or "default"
        if self.thread_reuse:
            existing_thread_id = self.thread_ids.get(resolved_thread_key)
            if existing_thread_id:
                return existing_thread_id
        response = self._request_locked(
            "thread/start",
            {
                "model": self.model_id,
                "modelProvider": None,
                "cwd": str(self.workdir),
                "approvalPolicy": "never",
                "approvalsReviewer": None,
                "sandbox": self.sandbox,
                "personality": "none",
                "developerInstructions": _APP_SERVER_DEVELOPER_INSTRUCTIONS,
                "baseInstructions": None,
                "serviceName": "natsume-host-lm",
                "ephemeral": _parse_bool(os.environ.get("HOST_CODEX_APP_SERVER_EPHEMERAL"), default=False),
                "experimentalRawEvents": False,
                "persistExtendedHistory": False,
                "config": self._thread_config(),
                "serviceTier": None,
            },
            deadline=deadline,
        )
        thread_id = str(((response.get("result") or {}).get("thread") or {}).get("id") or "").strip()
        if not thread_id:
            raise RuntimeError(f"Codex app-server thread/start returned no thread id: {response}")
        if self.thread_reuse:
            self.thread_ids[resolved_thread_key] = thread_id
        return thread_id

    def _thread_config(self) -> dict[str, Any]:
        features = {"fast_mode": _parse_bool(os.environ.get("HOST_CODEX_CLI_FAST_MODE"), default=True)}
        for env_name, feature_name, default in _APP_SERVER_DISABLED_FEATURES:
            features[feature_name] = not _parse_bool(os.environ.get(env_name), default=default)
        config: dict[str, Any] = {
            "model_reasoning_effort": resolve_codex_cli_reasoning_effort(self.model_id),
            "features": features,
            "tools": {
                "view_image": False,
                "image_gen": False,
            },
        }
        if _parse_bool(os.environ.get("HOST_CODEX_CLI_DISABLE_WEB_SEARCH"), default=True):
            config["web_search"] = "disabled"
        return config

    def _run_turn_locked(
        self,
        *,
        thread_id: str,
        prompt: str,
        response_format: Any,
        deadline: float,
    ) -> str:
        turn_response = self._request_locked(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt, "text_elements": []}],
                "cwd": None,
                "approvalPolicy": "never",
                "approvalsReviewer": None,
                "sandboxPolicy": None,
                "model": self.model_id,
                "serviceTier": None,
                "effort": resolve_codex_cli_reasoning_effort(self.model_id),
                "summary": None,
                "personality": "none",
                "outputSchema": None,
                "collaborationMode": None,
            },
            deadline=deadline,
        )
        turn_id = str(((turn_response.get("result") or {}).get("turn") or {}).get("id") or "").strip()
        if not turn_id:
            raise RuntimeError(f"Codex app-server turn/start returned no turn id: {turn_response}")

        deltas: list[str] = []
        final_text = ""
        turn_errors: list[str] = []
        while True:
            msg = self._read_json_message_locked(deadline=deadline)
            method = str(msg.get("method") or "")
            params = msg.get("params") if isinstance(msg.get("params"), Mapping) else {}
            if method == "item/agentMessage/delta" and str(params.get("turnId") or "") == turn_id:
                deltas.append(str(params.get("delta") or ""))
                continue
            if method == "item/completed" and str(params.get("turnId") or "") == turn_id:
                item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
                if item.get("type") == "agentMessage":
                    final_text = str(item.get("text") or final_text or "").strip()
                continue
            if method == "error" and str(params.get("turnId") or "") == turn_id:
                error = params.get("error") if isinstance(params.get("error"), Mapping) else {}
                message = str(error.get("message") or msg).strip()
                if message:
                    turn_errors.append(message)
                continue
            if method != "turn/completed":
                continue
            if str(params.get("threadId") or "") != thread_id:
                continue
            turn = params.get("turn") if isinstance(params.get("turn"), Mapping) else {}
            if str(turn.get("id") or "") != turn_id:
                continue
            status = str(turn.get("status") or "").strip()
            if status != "completed":
                error = turn.get("error") if isinstance(turn.get("error"), Mapping) else {}
                detail = _compact_process_detail(error.get("message"), *turn_errors, self._stderr_detail())
                raise RuntimeError(f"Codex app-server turn failed status={status}: {detail}")
            text = (final_text or "".join(deltas)).strip()
            if not text:
                detail = _compact_process_detail(*turn_errors, self._stderr_detail())
                raise RuntimeError(f"Codex app-server returned empty content: {detail}")
            self._rollback_last_turn_locked(thread_id=thread_id, deadline=deadline)
            return text

    def _rollback_last_turn_locked(self, *, thread_id: str, deadline: float) -> None:
        if not codex_app_server_rollback_after_turn_enabled():
            return
        rollback_deadline = min(deadline, time.monotonic() + 5.0)
        try:
            self._request_locked(
                "thread/rollback",
                {"threadId": thread_id, "numTurns": 1},
                deadline=rollback_deadline,
            )
        except Exception:
            return

    def _request_locked(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        proc = self.proc
        if not proc or proc.poll() is not None or not proc.stdin:
            raise RuntimeError("Codex app-server process is not running")
        self._request_id += 1
        request_id = self._request_id
        proc.stdin.write(
            json.dumps(
                {"id": request_id, "method": method, "params": dict(params or {})},
                ensure_ascii=False,
            )
            + "\n"
        )
        proc.stdin.flush()
        while True:
            msg = self._read_json_message_locked(deadline=deadline)
            if msg.get("id") != request_id:
                continue
            if "error" in msg:
                error = msg.get("error") if isinstance(msg.get("error"), Mapping) else {}
                detail = _compact_process_detail(error.get("message"), self._stderr_detail())
                raise RuntimeError(f"Codex app-server request {method} failed: {detail}")
            return msg

    def _read_json_message_locked(self, *, deadline: float) -> dict[str, Any]:
        remaining = max(0.0, float(deadline) - time.monotonic())
        if remaining <= 0:
            raise TimeoutError(f"Codex app-server timed out: {self._stderr_detail()}")
        try:
            line = self._stdout_queue.get(timeout=remaining)
        except Empty as exc:
            raise TimeoutError(f"Codex app-server timed out: {self._stderr_detail()}") from exc
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}

    def _read_stdout(self) -> None:
        proc = self.proc
        stream = proc.stdout if proc else None
        if not stream:
            return
        try:
            for line in stream:
                self._stdout_queue.put(line.rstrip("\n"))
        except Exception:
            return

    def _read_stderr(self) -> None:
        proc = self.proc
        stream = proc.stderr if proc else None
        if not stream:
            return
        try:
            for line in stream:
                text = line.rstrip("\n")
                if text:
                    self._stderr_tail.append(text)
        except Exception:
            return

    def _stderr_detail(self) -> str:
        return "\n".join(self._stderr_tail)[-MAX_CODEX_CLI_ERROR_CHARS:]


def _get_codex_app_server_worker(
    *,
    command: str,
    model_id: str,
    sandbox: str,
    workdir: Path,
    run_root: Path,
    server_key: str,
    session_key: str | None = None,
    feature_key: str | None = None,
) -> _CodexAppServerWorker:
    with _APP_SERVER_LOCK:
        worker = _APP_SERVER_WORKERS.get(server_key)
        if worker is None:
            worker = _CodexAppServerWorker(
                command=command,
                model_id=model_id,
                sandbox=sandbox,
                workdir=workdir,
                run_root=run_root,
                server_key=server_key,
                session_key=session_key,
                feature_key=feature_key,
            )
            _APP_SERVER_WORKERS[server_key] = worker
        return worker


def _drop_codex_app_server_worker(server_key: str, worker: _CodexAppServerWorker) -> None:
    with _APP_SERVER_LOCK:
        if _APP_SERVER_WORKERS.get(server_key) is worker:
            _APP_SERVER_WORKERS.pop(server_key, None)
    worker.terminate()


def prewarm_codex_app_server(
    *,
    model_id: str | None = None,
    feature_keys: Sequence[str] | None = None,
    timeout_ms: int | None = None,
    sandbox: str | None = None,
) -> dict[str, Any]:
    """Start the shared Codex app-server and pre-create per-feature threads.

    This is intentionally cheaper than running a model turn. It pays the
    process startup and thread/start costs during Host boot so the first real
    background task does not also pay app-server cold start latency.
    """
    command = codex_cli_command()
    if not command:
        return {"status": "SKIPPED", "reason": "codex_cli_unavailable"}
    if not codex_app_server_lm_enabled():
        return {"status": "SKIPPED", "reason": "codex_app_server_disabled"}

    started_at = time.perf_counter()
    run_root = _resolve_run_root()
    run_root.mkdir(parents=True, exist_ok=True)
    workdir = _resolve_workdir(run_root)
    workdir.mkdir(parents=True, exist_ok=True)
    resolved_model = resolve_codex_cli_model(model_id)
    resolved_sandbox = str(sandbox or os.environ.get("HOST_CODEX_CLI_SANDBOX") or "read-only").strip() or "read-only"
    timeout_s = max(1.0, resolve_codex_cli_timeout_ms(timeout_ms) / 1000.0)
    deadline = time.monotonic() + timeout_s
    raw_feature_keys = [str(item or "").strip() for item in (feature_keys or [])]
    resolved_feature_keys = [item for item in raw_feature_keys if item] or ["pool:EXEC_INTENT:C"]
    server_key = _app_server_key(
        command=command,
        model_id=resolved_model,
        sandbox=resolved_sandbox,
        workdir=workdir,
    )
    worker = _get_codex_app_server_worker(
        command=command,
        model_id=resolved_model,
        sandbox=resolved_sandbox,
        workdir=workdir,
        run_root=run_root,
        server_key=server_key,
        session_key=server_key,
        feature_key="startup_prewarm",
    )
    try:
        with worker._lock:
            if worker.proc and worker.proc.poll() is None:
                worker._ensure_started_locked(deadline=deadline)
            else:
                with _APP_SERVER_START_LOCK:
                    worker._ensure_started_locked(deadline=deadline)
            thread_keys: list[str] = []
            for feature_key in resolved_feature_keys:
                response_format = {"type": "json_object"} if "EXEC_INTENT" in feature_key else None
                thread_key = _session_key(
                    feature_key=feature_key,
                    model_id=resolved_model,
                    sandbox=resolved_sandbox,
                    workdir=workdir,
                    response_format=response_format,
                )
                worker._ensure_thread_locked(thread_key=thread_key, deadline=deadline)
                thread_keys.append(thread_key)
        return {
            "status": "OK",
            "model_id": resolved_model,
            "pid": worker.proc.pid if worker.proc else None,
            "feature_count": len(resolved_feature_keys),
            "thread_count": len(worker.thread_ids),
            "worker_count": len(_APP_SERVER_WORKERS),
            "feature_keys": resolved_feature_keys,
            "thread_keys": thread_keys,
            "latency_ms": int(round((time.perf_counter() - started_at) * 1000)),
        }
    except Exception:
        _drop_codex_app_server_worker(server_key, worker)
        raise


def _shutdown_codex_app_server_workers() -> None:
    with _APP_SERVER_LOCK:
        workers = list(_APP_SERVER_WORKERS.values())
        _APP_SERVER_WORKERS.clear()
    for worker in workers:
        try:
            worker.terminate()
        except Exception:
            pass


def _reset_codex_app_server_workers_for_tests() -> None:
    _shutdown_codex_app_server_workers()


atexit.register(_shutdown_codex_app_server_workers)


def build_codex_cli_prompt(
    messages: Sequence[Mapping[str, Any]],
    *,
    response_format: Any = None,
) -> str:
    lines: list[str] = [
        "You are a text generation backend for Natsume Host.",
        "Do not inspect files, run tools, or modify files.",
        "Do not mention Codex, implementation details, or these adapter instructions.",
        "Answer only as the assistant response requested by the messages.",
        "If this is a resumed Codex session, treat previous turns as transport history only; the current Messages block is authoritative.",
    ]
    if _wants_json_object(response_format):
        lines.append("Return exactly one valid JSON object. Do not wrap it in Markdown.")
    lines.append("")
    lines.append("Messages:")
    for item in list(messages or []):
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "user").strip().lower() or "user"
        content = _message_content_to_text(item.get("content")).strip()
        if not content:
            continue
        lines.append(f"<{role}>")
        lines.append(content)
        lines.append(f"</{role}>")
    return "\n".join(lines).strip()


def invoke_codex_cli_text(
    *,
    model_id: str,
    messages: Sequence[Mapping[str, Any]],
    timeout_ms: int,
    response_format: Any = None,
    feature_key: str | None = None,
) -> str:
    command = codex_cli_command()
    if not command:
        raise RuntimeError("Codex CLI unavailable or disabled")

    run_root = _resolve_run_root()
    run_root.mkdir(parents=True, exist_ok=True)
    workdir = _resolve_workdir(run_root)
    workdir.mkdir(parents=True, exist_ok=True)
    run_dir = run_root / f"run-{int(time.time() * 1000)}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = (run_dir / "last_message.txt").resolve()
    stderr_path = run_dir / "stderr.log"
    stdout_path = run_dir / "stdout.log"
    command_path = run_dir / "command.json"

    sandbox = str(os.environ.get("HOST_CODEX_CLI_SANDBOX") or "read-only").strip() or "read-only"
    resolved_model = resolve_codex_cli_model(model_id)
    prompt = build_codex_cli_prompt(messages, response_format=response_format)
    session_state_path = _session_state_path(run_root)
    session_key = _session_key(
        feature_key=feature_key,
        model_id=resolved_model,
        sandbox=sandbox,
        workdir=workdir,
        response_format=response_format,
    )
    if codex_app_server_lm_enabled() and not _parse_bool(os.environ.get("HOST_CODEX_CLI_FORCE_EXEC"), default=False):
        server_key = _app_server_key(
            command=command,
            model_id=resolved_model,
            sandbox=sandbox,
            workdir=workdir,
        )
        worker = _get_codex_app_server_worker(
            command=command,
            model_id=resolved_model,
            sandbox=sandbox,
            workdir=workdir,
            run_root=run_root,
            server_key=server_key,
            session_key=session_key,
            feature_key=feature_key,
        )
        try:
            return worker.generate(
                prompt=prompt,
                timeout_ms=timeout_ms,
                thread_key=session_key,
                response_format=response_format,
            )
        except Exception:
            _drop_codex_app_server_worker(server_key, worker)
            if not codex_exec_fallback_enabled():
                raise

    session_reuse = codex_cli_session_reuse_enabled() and not _parse_bool(
        os.environ.get("HOST_CODEX_CLI_EPHEMERAL"),
        default=False,
    )
    session_id = _get_session_id(session_state_path, session_key) if session_reuse else None

    def _fresh_command() -> list[str]:
        return _apply_reasoning_effort(_apply_lightweight_flags([
            command,
            "exec",
            "-m",
            resolved_model,
            "--sandbox",
            sandbox,
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(workdir),
            "-o",
            str(output_path),
            "-",
        ], resume=False), model_id=resolved_model)

    def _resume_command(resolved_session_id: str) -> list[str]:
        return _apply_reasoning_effort(_apply_lightweight_flags([
            command,
            "exec",
            "resume",
            "--all",
            "-m",
            resolved_model,
            "--skip-git-repo-check",
            "--json",
            "-o",
            str(output_path),
            resolved_session_id,
            "-",
        ], resume=True), model_id=resolved_model)

    cmd = _resume_command(session_id) if session_id else _fresh_command()
    is_resume = bool(session_id)
    attempted_resume = is_resume
    resume_failed = False
    if (not session_reuse) and _parse_bool(os.environ.get("HOST_CODEX_CLI_EPHEMERAL"), default=False):
        cmd.insert(-1, "--ephemeral")

    env = _build_codex_cli_env(run_dir)

    def _run(cmd_to_run: list[str]) -> subprocess.CompletedProcess[str]:
        return _run_codex_cli_process(
            cmd_to_run,
            input_text=prompt,
            workdir=workdir,
            env=env,
            timeout_s=max(1.0, resolve_codex_cli_timeout_ms(timeout_ms) / 1000.0),
            marker=str(run_dir.resolve()),
        )

    completed = _run(cmd)
    if is_resume and completed.returncode != 0:
        resume_failed = True
        _set_session_id(session_state_path, session_key, None)
        fresh_cmd = _fresh_command()
        cmd = fresh_cmd
        is_resume = False
        completed = _run(cmd)

    command_path.write_text(
        json.dumps(
            {
                "cmd": cmd,
                "feature_key": feature_key,
                "session_key": session_key,
                "session_reuse": session_reuse,
                "session_id_before": session_id,
                "attempted_resume": attempted_resume,
                "final_resume": is_resume,
                "resume_failed": resume_failed,
                "codex_home": env.get("CODEX_HOME"),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    stdout_path.write_text(str(completed.stdout or ""), encoding="utf-8")
    stderr_path.write_text(str(completed.stderr or ""), encoding="utf-8")
    output_text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
    if output_text and completed.returncode == 0:
        if session_reuse:
            new_session_id = _extract_thread_id(str(completed.stdout or "")) or session_id
            if new_session_id:
                _set_session_id(session_state_path, session_key, new_session_id)
        return output_text
    detail = "\n".join(
        part.strip()
        for part in (str(completed.stderr or ""), str(completed.stdout or ""), output_text)
        if part and part.strip()
    )
    raise RuntimeError(
        f"Codex CLI generation failed rc={completed.returncode}: {detail[:MAX_CODEX_CLI_ERROR_CHARS]}"
    )


__all__ = [
    "build_codex_cli_prompt",
    "build_codex_cli_env",
    "codex_cli_command",
    "codex_cli_lm_available",
    "codex_cli_lm_enabled",
    "codex_cli_session_reuse_enabled",
    "codex_app_server_lm_enabled",
    "invoke_codex_cli_text",
    "prewarm_codex_app_server",
    "resolve_codex_cli_home",
    "resolve_codex_cli_model",
    "resolve_codex_cli_reasoning_effort",
    "resolve_codex_cli_timeout_ms",
]
