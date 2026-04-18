"""IDE and Codex skill executors."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from host_brain.airlock import SandboxExecutor
from host_brain.core.ids import new_event_id, now_ms
from host_brain.skills.project_locator import resolve_workspace

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

IDE_EXEC_ERROR = "IDE_EXEC_ERROR"
IDE_NOT_FOUND = "IDE_NOT_FOUND"
IDE_PATH_MISSING = "IDE_PATH_MISSING"
CODEX_CLI_UNAVAILABLE = "CODEX_CLI_UNAVAILABLE"
CODEX_EXEC_TIMEOUT = "CODEX_EXEC_TIMEOUT"

_MIN_CODEX_TIMEOUT_SECONDS = 15
_MAX_CODEX_TIMEOUT_SECONDS = 1_800
_DEFAULT_CODEX_TIMEOUT_SECONDS_BY_RUN_MODE = {
    "inspect": 120,
    "worker": 300,
    "self_heal": 600,
    "self_evolve": 900,
}
_MAX_CODEX_OUTPUT_CHARS = 24_000
_MAX_INSPECT_CONTEXT_CHARS = 2_500
_MAX_WORKER_CONTEXT_CHARS = 12_000
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CODEX_RUNS_ROOT = _REPO_ROOT / "artifacts" / "codex_runs"
_ALLOWED_CODEX_RUN_MODES = {"inspect", "worker", "self_heal", "self_evolve"}
_DEFAULT_DIRECT_CODEGEN_MODEL_ID = "gpt-4o-mini"
_DEFAULT_DIRECT_GEMINI_MODEL_ID = "gemini-2.5-flash-lite"
_DEFAULT_DIRECT_CODEGEN_TIMEOUT_SECONDS = 25


def _reject(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


def _degraded(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_DEGRADED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


_EDITOR_CMDS: dict[str, list[str]] = {
    "cursor": ["cursor"],
    "vscode": ["code", "code-insiders"],
    "pycharm": ["pycharm", "pycharm64", "charm"],
    "default": ["cursor", "code", "code-insiders", "pycharm", "notepad++", "notepad"],
}


def _find_editor_cmd(editor: str) -> str | None:
    candidates = _EDITOR_CMDS.get(editor.lower(), _EDITOR_CMDS["default"])
    for cmd in candidates:
        if shutil.which(cmd):
            return cmd
    return None


def _open_with_system(path: str) -> None:
    if sys.platform == "win32":
        import os

        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _coerce_codex_timeout_seconds(payload: dict[str, Any], *, run_mode: str) -> int:
    raw_value = payload.get("timeout_seconds") or payload.get("timeout_sec")
    default_timeout = _DEFAULT_CODEX_TIMEOUT_SECONDS_BY_RUN_MODE.get(
        str(run_mode or "").strip().lower(),
        _DEFAULT_CODEX_TIMEOUT_SECONDS_BY_RUN_MODE["inspect"],
    )
    try:
        timeout_value = int(raw_value) if raw_value is not None else default_timeout
    except (TypeError, ValueError):
        timeout_value = default_timeout
    return max(_MIN_CODEX_TIMEOUT_SECONDS, min(timeout_value, _MAX_CODEX_TIMEOUT_SECONDS))


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def _write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8", errors="replace")


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _coerce_codex_run_mode(payload: dict[str, Any]) -> str:
    raw_mode = str(payload.get("run_mode") or "").strip().lower()
    if raw_mode in _ALLOWED_CODEX_RUN_MODES:
        return raw_mode
    if payload.get("auto_apply"):
        return "worker"
    return "inspect"


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_direct_codegen_timeout_seconds() -> int:
    raw = str(os.environ.get("HOST_CODEX_INSPECT_DIRECT_TIMEOUT_SECONDS") or "").strip()
    try:
        timeout_value = int(raw) if raw else _DEFAULT_DIRECT_CODEGEN_TIMEOUT_SECONDS
    except ValueError:
        timeout_value = _DEFAULT_DIRECT_CODEGEN_TIMEOUT_SECONDS
    return max(5, min(timeout_value, 120))


def _resolve_direct_codegen_model_id() -> str:
    for env_name in (
        "HOST_CODEX_INSPECT_DIRECT_MODEL_ID",
        "HOST_CODEX_MODEL_ID",
        "HOST_OPENAI_MODEL_ID",
    ):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value
    return _DEFAULT_DIRECT_CODEGEN_MODEL_ID


def _resolve_direct_codegen_api_key() -> str | None:
    for env_name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value
    try:
        from host_brain.app.config import load_host_config
    except Exception:
        return None
    for provider_name in ("openai", "codex"):
        try:
            load_result = load_host_config(local_overrides={"HOST_LLM_PROVIDER": provider_name})
        except Exception as exc:
            logger.debug("failed to resolve %s api key from host config: %s", provider_name, exc)
            continue
        if not load_result.success or load_result.config is None:
            continue
        value = str(load_result.config.llm_api_key or "").strip()
        if value:
            return value
    return None


def _resolve_direct_codegen_base_url() -> str | None:
    for env_name in ("HOST_OPENAI_BASE_URL", "HOST_CODEX_BASE_URL"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value
    return None


def _resolve_direct_codegen_runtime() -> dict[str, str] | None:
    api_key = _resolve_direct_codegen_api_key()
    if api_key:
        return {
            "provider": "openai_compat",
            "api_key": api_key,
            "model_id": _resolve_direct_codegen_model_id(),
            "base_url": str(_resolve_direct_codegen_base_url() or "").strip(),
            "backend": "direct_openai",
        }
    try:
        from host_brain.app.model_router import resolve_gemini_key_pool
    except Exception:
        resolve_gemini_key_pool = None  # type: ignore[assignment]
    if resolve_gemini_key_pool is not None:
        try:
            gemini_keys = resolve_gemini_key_pool()
        except Exception as exc:
            logger.debug("failed to resolve gemini key pool for direct codegen: %s", exc)
            gemini_keys = []
        if gemini_keys:
            model_id = str(os.environ.get("HOST_GEMINI_MODEL_ID") or _DEFAULT_DIRECT_GEMINI_MODEL_ID).strip()
            base_url = str(
                os.environ.get("HOST_GEMINI_BASE_URL")
                or "https://generativelanguage.googleapis.com/v1beta/openai"
            ).strip()
            return {
                "provider": "gemini",
                "api_key": str(gemini_keys[0]).strip(),
                "model_id": model_id or _DEFAULT_DIRECT_GEMINI_MODEL_ID,
                "base_url": base_url,
                "backend": "direct_gemini_openai_compat",
            }
    return None


def _coerce_runs_root(payload: dict[str, Any]) -> Path:
    raw_root = str(payload.get("runs_root") or "").strip()
    candidate = Path(raw_root).expanduser() if raw_root else _DEFAULT_CODEX_RUNS_ROOT
    return candidate.resolve() if candidate.exists() else candidate


def _sanitize_segment(value: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip()).strip(".-")
    return cleaned or fallback


def _ensure_codex_artifact_dir(payload: dict[str, Any], *, request_id: str | None, event_id: str) -> Path:
    runs_root = _coerce_runs_root(payload)
    base_name = _sanitize_segment(request_id or event_id, "codex-run")
    artifact_dir = runs_root / base_name
    if artifact_dir.exists() and any(artifact_dir.iterdir()):
        artifact_dir = artifact_dir / _sanitize_segment(event_id, "event")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return artifact_dir


def _extract_codex_events(raw_stdout: str) -> tuple[list[dict[str, Any]], str | None]:
    events: list[dict[str, Any]] = []
    thread_id: str | None = None
    for raw_line in str(raw_stdout or "").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        events.append(payload)
        candidate = str(payload.get("thread_id") or "").strip()
        if payload.get("type") == "thread.started" and candidate:
            thread_id = candidate
    return events, thread_id


def _extract_non_event_output(raw_stdout: str) -> str:
    lines: list[str] = []
    for raw_line in str(raw_stdout or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if isinstance(parsed, dict):
                continue
        lines.append(line)
    return "\n".join(lines).strip()


def _collect_git_summary(workspace: Path) -> dict[str, Any]:
    git_dir = workspace / ".git"
    if not git_dir.exists():
        return {
            "changed_files": [],
            "git_diff_stat": None,
            "git_status": None,
            "status_map": {},
        }

    def _run_git(*args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return str(completed.stdout or "").strip()

    git_status = _run_git("status", "--short") or None
    status_map: dict[str, str] = {}
    if git_status:
        for raw_line in git_status.splitlines():
            line = str(raw_line or "").rstrip()
            if len(line) < 4:
                continue
            status = line[:2]
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[-1].strip()
            if path:
                status_map[path] = status
    changed_files = sorted(status_map.keys())
    git_diff_stat = _run_git("diff", "--stat") or None
    return {
        "changed_files": changed_files,
        "git_diff_stat": git_diff_stat,
        "git_status": git_status,
        "status_map": status_map,
    }


def _diff_git_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_map = dict(before.get("status_map") or {})
    after_map = dict(after.get("status_map") or {})
    delta_files = sorted(
        path for path, status in after_map.items() if str(before_map.get(path) or "") != str(status or "")
    )
    baseline_files = sorted(before_map.keys())
    baseline_dirty = bool(baseline_files)
    return {
        "changed_files": delta_files,
        "git_diff_stat": None if baseline_dirty else after.get("git_diff_stat"),
        "git_status": after.get("git_status"),
        "workspace_dirty_baseline": baseline_dirty,
        "baseline_changed_files": baseline_files,
        "current_changed_files": sorted(after_map.keys()),
    }


def _run_optional_python_verification(payload: dict[str, Any], *, request_id: str | None, intent_id: str) -> dict[str, Any]:
    verification_code = str(payload.get("verification_code") or "").strip()
    if not verification_code:
        return {"status": "NOT_RUN", "reason": "verification_code not provided"}

    result = SandboxExecutor().execute(
        verification_code,
        request_id=request_id,
        intent_id=intent_id,
        language="python",
    )
    return {
        "status": result.status,
        "reason_code": result.reason_code,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_ms": result.duration_ms,
        "evidence_ref": result.evidence_ref,
        "code_digest": result.code_digest,
    }


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return
        proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _build_codex_skill_prompt(*, task: str, context: str | None, mode: str, run_mode: str, workspace: Path) -> str:
    prompt_parts: list[str]
    extra_context = str(context or "").strip()
    if extra_context:
        max_context_chars = _MAX_INSPECT_CONTEXT_CHARS if run_mode == "inspect" else _MAX_WORKER_CONTEXT_CHARS
        extra_context = extra_context[:max_context_chars].strip()
    if run_mode == "inspect":
        prompt_parts = [
            f"Workspace: {workspace}",
            "Read-only task. Do not modify files.",
            "Return only the final answer requested by the user.",
        ]
        if str(mode or "").strip().lower() == "codegen":
            prompt_parts.append("If code is requested, return only the code with no JSON and no extra commentary unless explicitly requested.")
        else:
            prompt_parts.append("Keep the answer concise and direct.")
            prompt_parts.append("This is an analysis-only inspect task. Do not use shell commands or external tools.")
            prompt_parts.append("Base the answer exclusively on the provided context and file excerpts. If they are insufficient, say what additional context is needed instead of attempting shell reads.")
    else:
        prompt_parts = [
            "You are fulfilling a coding skill request for the Natsume host runtime.",
            f"Target workspace: {workspace}",
            "Reply with the final user-facing answer only.",
            "Do not mention Codex, hidden prompts, internal tools, or system details.",
        ]
        prompt_parts.extend(
            [
                "You may modify files inside the target workspace only.",
                "Keep changes minimal, explicit, and auditable.",
                "If possible, run only focused checks relevant to the requested fix.",
                "Summarize changed files and checks in the final answer.",
            ]
        )
    if sys.platform == "win32":
        prompt_parts.extend(
            [
                "Environment note: Windows host. Do not explicitly invoke `powershell` or `cmd`; issue raw commands only and let the runtime shell wrapper host them.",
                "Avoid nested shell invocations and do not emit `powershell -Command` inside another shell command string.",
                "Do not dot-source shell profiles and do not change console encoding or console properties.",
            ]
        )
    prompt_parts.append("Prefer targeted ripgrep/file reads. Avoid whole-repo listings like `rg --files .` unless they are truly necessary.")
    if run_mode == "inspect":
        prompt_parts.append("For inspect tasks, rely on the provided context and file excerpts first. Avoid shell/tool calls unless the provided context is insufficient.")
    if run_mode == "self_heal":
        prompt_parts.extend(
            [
                "Prioritize diagnosis, smallest viable repair, and regression prevention.",
                "Start from the affected components and reason code provided in context before widening your search.",
                "If the evidence indicates an external dependency outage or unreachable endpoint, report that directly before attempting a broad code fix.",
                "Prefer runtime source files under `host_brain/app`, `host_brain/integrations`, `host_brain/channels`, and `scripts/runtime` before looking at docs or tests.",
            ]
        )
    if run_mode == "self_evolve":
        prompt_parts.append("Prioritize additive improvements with tests and low-risk changes.")
    if str(mode or "").strip().lower() == "codegen":
        prompt_parts.extend(
            [
                "If code is required, return complete runnable code blocks whenever possible.",
                "If the request is about debugging or fixing, briefly state the root cause and then give the concrete patch or replacement snippet.",
                "Do not wrap the final answer in JSON.",
            ]
        )
    prompt_parts.append("User request:\n" + str(task or "").strip())
    if extra_context:
        prompt_parts.append("Additional context:\n" + extra_context)
    return "\n\n".join(prompt_parts).strip()


def _codex_prompt_profile(*, mode: str, run_mode: str) -> str:
    if run_mode == "inspect":
        return "inspect_light_codegen" if str(mode or "").strip().lower() == "codegen" else "inspect_light"
    return f"{run_mode}_full"


def _collect_runtime_diagnostics(*, stdout: str, stderr: str) -> dict[str, Any]:
    combined = "\n".join(part for part in (str(stdout or ""), str(stderr or "")) if part)
    reconnect_count = combined.count("Reconnecting...")
    if reconnect_count == 0:
        reconnect_count = combined.count("stream disconnected - retrying")
    transport_fallback_detected = "falling back to HTTP" in combined
    plugin_sync_forbidden_detected = "403 Forbidden" in combined and "plugins" in combined
    return {
        "reconnect_count": int(reconnect_count),
        "transport_fallback_detected": bool(transport_fallback_detected),
        "plugin_sync_forbidden_detected": bool(plugin_sync_forbidden_detected),
        "stderr_chars": len(str(stderr or "")),
        "stdout_chars": len(str(stdout or "")),
    }


def _strip_wrapping_code_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _extract_direct_codegen_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text_value = str(item.get("text") or item.get("content") or "").strip()
                if text_value:
                    parts.append(text_value)
            else:
                text_value = str(getattr(item, "text", "") or getattr(item, "content", "") or "").strip()
                if text_value:
                    parts.append(text_value)
        return "\n".join(parts).strip()
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "").strip()
    return str(content or "").strip()


def _should_use_direct_codegen_fast_path(
    *,
    mode: str,
    run_mode: str,
    workspace_source: str,
) -> bool:
    if str(mode or "").strip().lower() != "codegen":
        return False
    if str(run_mode or "").strip().lower() != "inspect":
        return False
    if str(workspace_source or "").strip().lower() != "temp_workspace":
        return False
    return _parse_bool_env("HOST_CODEX_INSPECT_DIRECT_LLM_ENABLED", default=True)


def _execute_direct_codegen_fast_path(
    *,
    prompt: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage  # noqa: PLC0415
    from langchain_openai import ChatOpenAI  # noqa: PLC0415

    runtime = _resolve_direct_codegen_runtime()
    if not runtime:
        raise RuntimeError("direct inspect codegen unavailable: no direct provider credentials configured")

    model_id = str(runtime.get("model_id") or "").strip() or _DEFAULT_DIRECT_CODEGEN_MODEL_ID
    api_key = str(runtime.get("api_key") or "").strip()
    base_url = str(runtime.get("base_url") or "").strip() or None
    llm = ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url=base_url,
        timeout=max(1.0, float(timeout_seconds)),
        max_retries=1,
        temperature=0.1,
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    output_text = _strip_wrapping_code_fence(_extract_direct_codegen_text(getattr(response, "content", "")))
    if not output_text:
        raise RuntimeError("direct inspect codegen returned empty output")
    return {
        "result_text": output_text[:_MAX_CODEX_OUTPUT_CHARS].strip(),
        "backend": str(runtime.get("backend") or "direct_openai"),
        "model_id": model_id,
        "base_url": base_url,
        "provider": str(runtime.get("provider") or "").strip() or None,
    }


def _pump_stream(stream: Any, sink: list[str]) -> None:
    if stream is None:
        return
    try:
        for chunk in iter(stream.readline, ""):
            if not chunk:
                break
            sink.append(str(chunk))
    except Exception:
        return


def _communicate_codex_process(
    *,
    proc: subprocess.Popen[str],
    prompt: str,
    timeout_seconds: int,
    run_mode: str,
    last_message_path: Path,
) -> tuple[str, str, bool, bool]:
    stdout_stream = getattr(proc, "stdout", None)
    stderr_stream = getattr(proc, "stderr", None)
    stdin_stream = getattr(proc, "stdin", None)
    if stdout_stream is None or stderr_stream is None:
        try:
            returncode_stdout, returncode_stderr = proc.communicate(input=prompt, timeout=timeout_seconds)
            return str(returncode_stdout or ""), str(returncode_stderr or ""), False, False
        except subprocess.TimeoutExpired as exc:
            _terminate_process_tree(proc)
            return str(exc.stdout or ""), str(exc.stderr or ""), True, False

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_thread = threading.Thread(target=_pump_stream, args=(stdout_stream, stdout_parts), daemon=True)
    stderr_thread = threading.Thread(target=_pump_stream, args=(stderr_stream, stderr_parts), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    try:
        if stdin_stream is not None:
            stdin_stream.write(prompt)
            stdin_stream.flush()
            stdin_stream.close()
    except Exception:
        pass

    timed_out = False
    completed_from_output = False
    started_at = time.monotonic()
    last_seen_output = ""
    last_seen_at = 0.0

    while True:
        if proc.poll() is not None:
            break
        current_output = _read_text_file(last_message_path)
        if run_mode == "inspect" and current_output:
            now = time.monotonic()
            if current_output == last_seen_output:
                if last_seen_at and (now - last_seen_at) >= 1.0:
                    completed_from_output = True
                    _terminate_process_tree(proc)
                    break
            else:
                last_seen_output = current_output
                last_seen_at = now
        if (time.monotonic() - started_at) >= timeout_seconds:
            timed_out = True
            _terminate_process_tree(proc)
            break
        time.sleep(0.25)

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    return "".join(stdout_parts), "".join(stderr_parts), timed_out, completed_from_output


def execute_open_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    path = payload.get("path") or ""
    workspace = payload.get("workspace") or None
    request_id = payload.get("request_id")

    if not path:
        return _reject(
            event_id=event_id,
            reason_code=IDE_PATH_MISSING,
            detail="path is required",
            ts_ms=ts,
            request_id=request_id,
        )

    cmd = _find_editor_cmd("cursor") or _find_editor_cmd("vscode")
    if cmd is None:
        return _degraded(
            event_id=event_id,
            reason_code=IDE_NOT_FOUND,
            detail="cursor/code not found in PATH",
            ts_ms=ts,
            request_id=request_id,
        )

    try:
        args = [cmd]
        if workspace:
            args.append(str(workspace))
        args.append(str(path))
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"open_cursor::{str(path)[:80]}",
            "cmd": cmd,
            "path": path,
            "workspace": workspace,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("open_cursor failed: %s", exc)
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )


def execute_run_pycharm_action(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    action = payload.get("action") or ""
    project_path = payload.get("project_path") or None
    request_id = payload.get("request_id")

    if not action:
        return _reject(
            event_id=event_id,
            reason_code="IDE_ACTION_MISSING",
            detail="action is required",
            ts_ms=ts,
            request_id=request_id,
        )

    cmd = _find_editor_cmd("pycharm")
    if cmd is None:
        return _degraded(
            event_id=event_id,
            reason_code=IDE_NOT_FOUND,
            detail="pycharm/charm not found in PATH",
            ts_ms=ts,
            request_id=request_id,
        )

    try:
        args = [cmd]
        if project_path:
            args.append(str(project_path))
        args.extend(["--action", str(action)])
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"pycharm_action::{action}",
            "cmd": cmd,
            "action": action,
            "project_path": project_path,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("run_pycharm_action failed: %s", exc)
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )


def execute_codex_codegen(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    task = str(payload.get("task") or "").strip()
    context = str(payload.get("context") or "").strip() or None
    request_id = payload.get("request_id") or payload.get("session_id")

    if not task:
        return _reject(
            event_id=event_id,
            reason_code="IDE_TASK_MISSING",
            detail="task is required",
            ts_ms=ts,
            request_id=request_id,
        )

    try:
        workspace_resolution = resolve_workspace(payload, request_id=str(request_id or "").strip() or None)
    except ValueError as exc:
        return _reject(
            event_id=event_id,
            reason_code="IDE_WORKSPACE_UNRESOLVED",
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )

    workspace = workspace_resolution.workspace
    mode = str(payload.get("mode") or "codegen").strip().lower() or "codegen"
    run_mode = _coerce_codex_run_mode(payload)
    timeout_seconds = _coerce_codex_timeout_seconds(payload, run_mode=run_mode)
    prompt_profile = _codex_prompt_profile(mode=mode, run_mode=run_mode)
    prompt = _build_codex_skill_prompt(task=task, context=context, mode=mode, run_mode=run_mode, workspace=workspace)
    artifact_dir = _ensure_codex_artifact_dir(payload, request_id=str(request_id or "").strip() or None, event_id=event_id)
    last_message_path = artifact_dir / "last_message.txt"
    stdout_path = artifact_dir / "stdout.log"
    stderr_path = artifact_dir / "stderr.log"
    events_path = artifact_dir / "events.jsonl"
    metadata_path = artifact_dir / "metadata.json"
    changed_files_path = artifact_dir / "changed_files.json"
    verification_path = artifact_dir / "verification.json"

    if _should_use_direct_codegen_fast_path(
        mode=mode,
        run_mode=run_mode,
        workspace_source=workspace_resolution.source,
    ):
        try:
            direct_timeout_seconds = _coerce_direct_codegen_timeout_seconds()
            direct_result = _execute_direct_codegen_fast_path(
                prompt=prompt,
                timeout_seconds=direct_timeout_seconds,
            )
            git_summary_before = _collect_git_summary(workspace)
            git_summary_after = _collect_git_summary(workspace)
            git_summary = _diff_git_summary(git_summary_before, git_summary_after)
            verification = _run_optional_python_verification(
                payload,
                request_id=str(request_id or "").strip() or None,
                intent_id=event_id,
            )
            runtime_diagnostics = {
                "reconnect_count": 0,
                "transport_fallback_detected": False,
                "plugin_sync_forbidden_detected": False,
                "stderr_chars": 0,
                "stdout_chars": len(str(direct_result.get("result_text") or "")),
                "execution_backend": direct_result.get("backend"),
            }
            _write_text_file(stdout_path, str(direct_result.get("result_text") or ""))
            _write_text_file(stderr_path, "")
            _write_text_file(events_path, "")
            _write_text_file(last_message_path, str(direct_result.get("result_text") or ""))
            _write_json_file(changed_files_path, git_summary)
            _write_json_file(verification_path, verification)
            metadata = {
                "event_id": event_id,
                "request_id": request_id,
                "mode": mode,
                "run_mode": run_mode,
                "workspace": str(workspace),
                "workspace_source": workspace_resolution.source,
                "repo_alias": workspace_resolution.repo_alias,
                "git_url": workspace_resolution.git_url,
                "created_workspace": workspace_resolution.created_workspace,
                "cloned_workspace": workspace_resolution.cloned_workspace,
                "thread_id": None,
                "resume_thread_id": None,
                "resumed": False,
                "timed_out": False,
                "completed_from_output": False,
                "prompt_profile": "inspect_direct_openai_codegen",
                "prompt_chars": len(prompt),
                "timeout_seconds": direct_timeout_seconds,
                "runtime_diagnostics": runtime_diagnostics,
                "returncode": 0,
                "artifact_dir": str(artifact_dir),
                "sandbox_mode": "direct-llm",
                "execution_backend": direct_result.get("backend"),
                "model_id": direct_result.get("model_id"),
                "base_url": direct_result.get("base_url"),
                "ts_ms": ts,
            }
            _write_json_file(metadata_path, metadata)
            return {
                "status": STATUS_EXECUTED,
                "event_id": event_id,
                "manifest_ref": payload.get("manifest_ref") or f"codex_codegen::{task[:60]}",
                "task": task,
                "workspace": str(workspace),
                "mode": mode,
                "run_mode": run_mode,
                "thread_id": None,
                "resumed": False,
                "artifacts_dir": str(artifact_dir),
                "timed_out": False,
                "completed_from_output": False,
                "prompt_profile": "inspect_direct_openai_codegen",
                "prompt_chars": len(prompt),
                "timeout_seconds": direct_timeout_seconds,
                "runtime_diagnostics": runtime_diagnostics,
                "stderr": "",
                "stdout": "",
                "returncode": 0,
                "changed_files": git_summary.get("changed_files") or [],
                "git_diff_stat": git_summary.get("git_diff_stat"),
                "verification": verification,
                "request_id": request_id,
                "ts_ms": ts,
                "result_text": direct_result.get("result_text"),
                "text": direct_result.get("result_text"),
                "detail": "direct inspect codegen completed",
                "execution_backend": direct_result.get("backend"),
                "model_id": direct_result.get("model_id"),
            }
        except Exception as exc:
            logger.warning("direct inspect codegen fast path failed, falling back to codex CLI: %s", exc)

    codex_cmd = shutil.which("codex")
    if codex_cmd is None:
        logger.info("[codex_codegen] codex CLI unavailable task=%r context=%r", task, context)
        return _degraded(
            event_id=event_id,
            reason_code=CODEX_CLI_UNAVAILABLE,
            detail="codex CLI not found in PATH",
            ts_ms=ts,
            request_id=request_id,
        )

    resume_thread_id = str(payload.get("resume_thread_id") or "").strip() or None
    sandbox_mode = "read-only" if run_mode == "inspect" else "workspace-write"
    if resume_thread_id:
        command = [
            codex_cmd,
            "exec",
            "resume",
            resume_thread_id,
            "--skip-git-repo-check",
            "--json",
            "-o",
            str(last_message_path),
            "-",
        ]
    else:
        command = [
            codex_cmd,
            "exec",
            "--sandbox",
            sandbox_mode,
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(workspace),
            "-o",
            str(last_message_path),
            "-",
        ]

    proc: subprocess.Popen[str] | None = None
    timed_out = False
    completed_from_output = False
    stdout = ""
    stderr = ""
    git_summary_before = _collect_git_summary(workspace)
    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout, stderr, timed_out, completed_from_output = _communicate_codex_process(
            proc=proc,
            prompt=prompt,
            timeout_seconds=timeout_seconds,
            run_mode=run_mode,
            last_message_path=last_message_path,
        )

        _write_text_file(stdout_path, stdout)
        _write_text_file(stderr_path, stderr)

        events, parsed_thread_id = _extract_codex_events(stdout)
        _write_text_file(events_path, "\n".join(json.dumps(event, ensure_ascii=False) for event in events))

        non_event_output = _extract_non_event_output(stdout)
        output_text = _read_text_file(last_message_path)
        if not output_text:
            output_text = non_event_output
        output_text = output_text[:_MAX_CODEX_OUTPUT_CHARS].strip()

        thread_id = parsed_thread_id or resume_thread_id
        git_summary_after = _collect_git_summary(workspace)
        git_summary = _diff_git_summary(git_summary_before, git_summary_after)
        verification = _run_optional_python_verification(payload, request_id=str(request_id or "").strip() or None, intent_id=event_id)
        runtime_diagnostics = _collect_runtime_diagnostics(stdout=stdout, stderr=stderr)
        _write_json_file(changed_files_path, git_summary)
        _write_json_file(verification_path, verification)

        metadata = {
            "event_id": event_id,
            "request_id": request_id,
            "mode": mode,
            "run_mode": run_mode,
            "workspace": str(workspace),
            "workspace_source": workspace_resolution.source,
            "repo_alias": workspace_resolution.repo_alias,
            "git_url": workspace_resolution.git_url,
            "created_workspace": workspace_resolution.created_workspace,
            "cloned_workspace": workspace_resolution.cloned_workspace,
            "thread_id": thread_id,
            "resume_thread_id": resume_thread_id,
            "resumed": bool(resume_thread_id),
            "timed_out": timed_out,
            "completed_from_output": completed_from_output,
            "prompt_profile": prompt_profile,
            "prompt_chars": len(prompt),
            "timeout_seconds": timeout_seconds,
            "runtime_diagnostics": runtime_diagnostics,
            "returncode": None if proc is None else proc.returncode,
            "artifact_dir": str(artifact_dir),
            "sandbox_mode": sandbox_mode if not resume_thread_id else "session-resume",
            "ts_ms": ts,
        }
        _write_json_file(metadata_path, metadata)

        common_result = {
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"codex_codegen::{task[:60]}",
            "task": task,
            "workspace": str(workspace),
            "mode": mode,
            "run_mode": run_mode,
            "thread_id": thread_id,
            "resumed": bool(resume_thread_id),
            "artifacts_dir": str(artifact_dir),
            "timed_out": timed_out,
            "completed_from_output": completed_from_output,
            "prompt_profile": prompt_profile,
            "prompt_chars": len(prompt),
            "timeout_seconds": timeout_seconds,
            "runtime_diagnostics": runtime_diagnostics,
            "stderr": str(stderr or "")[:2048],
            "stdout": str(stdout or "")[:2048],
            "returncode": None if proc is None else proc.returncode,
            "changed_files": git_summary.get("changed_files") or [],
            "git_diff_stat": git_summary.get("git_diff_stat"),
            "verification": verification,
            "request_id": request_id,
            "ts_ms": ts,
        }

        if output_text:
            detail = "codex exec completed"
            if completed_from_output:
                detail = "codex exec completed from last_message capture"
            elif timed_out:
                detail = "codex exec timed out after producing output"
            return {
                "status": STATUS_EXECUTED,
                "result_text": output_text,
                "text": output_text,
                "detail": detail,
                **common_result,
            }

        if timed_out:
            return {
                "status": STATUS_DEGRADED,
                "reason_code": CODEX_EXEC_TIMEOUT,
                "detail": f"codex exec timed out after {timeout_seconds}s without producing output",
                "manifest_ref": payload.get("manifest_ref") or f"codex_codegen::{task[:60]}",
                **common_result,
            }

        return {
            "status": STATUS_REJECTED,
            "reason_code": IDE_EXEC_ERROR,
            "detail": str(stderr or non_event_output or "codex exec failed")[:2048],
            "manifest_ref": payload.get("manifest_ref") or f"codex_codegen::{task[:60]}",
            **common_result,
        }
    except Exception as exc:
        logger.warning("codex CLI failed: %s", exc)
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )
    finally:
        if proc is not None and proc.poll() is None:
            _terminate_process_tree(proc)


def execute_open_file_in_editor(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    path = payload.get("path") or ""
    editor = str(payload.get("editor") or "default").strip().lower()
    line = payload.get("line")
    column = payload.get("column")
    request_id = payload.get("request_id")

    if not path:
        return _reject(
            event_id=event_id,
            reason_code=IDE_PATH_MISSING,
            detail="path is required",
            ts_ms=ts,
            request_id=request_id,
        )

    cmd = _find_editor_cmd(editor)
    if cmd is None:
        try:
            _open_with_system(str(path))
            return {
                "status": STATUS_EXECUTED,
                "event_id": event_id,
                "manifest_ref": payload.get("manifest_ref") or f"open_file::{str(path)[:80]}",
                "cmd": "system_default",
                "path": path,
                "request_id": request_id,
                "ts_ms": ts,
            }
        except Exception as exc:
            return _reject(
                event_id=event_id,
                reason_code=IDE_EXEC_ERROR,
                detail=str(exc),
                ts_ms=ts,
                request_id=request_id,
            )

    try:
        args = [cmd]
        if line is not None and cmd in ("code", "code-insiders", "cursor"):
            col_part = f":{column}" if column is not None else ""
            args.extend(["--goto", f"{path}:{line}{col_part}"])
        else:
            args.append(str(path))
        subprocess.Popen(args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"open_file::{str(path)[:80]}",
            "cmd": cmd,
            "path": path,
            "line": line,
            "column": column,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except Exception as exc:
        logger.error("open_file_in_editor failed: %s", exc)
        return _reject(
            event_id=event_id,
            reason_code=IDE_EXEC_ERROR,
            detail=str(exc),
            ts_ms=ts,
            request_id=request_id,
        )


_ACTION_MAP = {
    "cursor": execute_open_cursor,
    "open": execute_open_cursor,
    "pycharm": execute_run_pycharm_action,
    "codex": execute_codex_codegen,
    "open_file_in_editor": execute_open_file_in_editor,
}


def execute_ide_skill(payload: dict[str, Any]) -> dict[str, Any]:
    tool = str(payload.get("tool") or "").strip().lower()
    action = str(payload.get("action") or "").strip().lower()

    handler = _ACTION_MAP.get(tool) or _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "IDE_UNKNOWN_ACTION",
            "detail": f"unknown ide tool/action: tool={tool!r} action={action!r}",
            "manifest_ref": None,
            "ts_ms": now_ms(),
        }
    return handler(payload)
