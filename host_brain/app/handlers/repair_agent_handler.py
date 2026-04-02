from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

from host_brain.app.sub_agent_protocol import AGENT_REPAIR
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state


def _resolve_python_executable() -> str:
    candidates = [
        os.path.join(os.getcwd(), '.venv', 'Scripts', 'python.exe'),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return sys.executable


def _command_exists(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(str(name or '').strip())
        if resolved:
            return resolved
    return None


def _run_command(command: list[str], *, timeout_s: int = 120) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=max(5, int(timeout_s)),
        check=False,
    )
    return {
        'command': list(command),
        'returncode': int(completed.returncode),
        'stdout': str(completed.stdout or '').strip(),
        'stderr': str(completed.stderr or '').strip(),
        'ok': completed.returncode == 0,
    }


def _agent_browser_available() -> bool:
    return _command_exists('agent-browser', 'agent-browser.cmd') is not None


def _playwright_module_available() -> bool:
    return importlib.util.find_spec('playwright') is not None


def _playwright_cli_available() -> bool:
    return _command_exists('playwright', 'playwright.cmd') is not None


def _repair_browser_backend() -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    npm = _command_exists('npm.cmd', 'npm')
    python_exe = _resolve_python_executable()

    if not _agent_browser_available() and npm:
        actions.append(_run_command([npm, 'i', '-g', 'agent-browser'], timeout_s=300))
    agent_browser_cmd = _command_exists('agent-browser', 'agent-browser.cmd')
    if agent_browser_cmd:
        actions.append(_run_command([agent_browser_cmd, 'install'], timeout_s=300))

    if not _playwright_module_available():
        actions.append(_run_command([python_exe, '-m', 'pip', 'install', 'playwright'], timeout_s=300))
    if _playwright_module_available() or _playwright_cli_available():
        actions.append(_run_command([python_exe, '-m', 'playwright', 'install', 'chromium'], timeout_s=300))

    browser_ready = _agent_browser_available() or _playwright_module_available()
    ok_actions = sum(1 for item in actions if item.get('ok'))
    if browser_ready:
        return {
            'success': True,
            'summary': '浏览器后端已经补齐，可以重试原任务。',
            'detail': 'browser backend repaired',
            'actions': actions,
            'ok_actions': ok_actions,
        }
    detail = 'browser backend repair failed'
    if not actions:
        detail = 'no installer command available for browser backend repair'
    return {
        'success': False,
        'summary': '浏览器后端这边还是没补齐，暂时还不能自动重试。',
        'detail': detail,
        'actions': actions,
        'ok_actions': ok_actions,
    }


def _build_repair_result(*, task_text: str, previous_error: str) -> dict[str, Any]:
    normalized_error = str(previous_error or '').strip().upper()
    normalized_task = str(task_text or '').strip()
    if 'BROWSER_UNAVAILABLE' in normalized_error or 'PLAYWRIGHT' in normalized_error or 'AGENT-BROWSER' in normalized_error:
        return _repair_browser_backend()
    return {
        'success': False,
        'summary': '这次故障类型还没有接入自动修复器。',
        'detail': 'unsupported repair target',
        'actions': [],
        'ok_actions': 0,
    }


def repair_agent_handler(payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get('request_id') or payload.get('request_id') or '').strip()
    task_text = str(payload.get('user_input') or payload.get('query') or payload.get('task') or '').strip()
    previous_error = str(ctx.get('previous_agent_result') or payload.get('previous_agent_result') or payload.get('error') or '').strip()

    out = dict(payload)
    try:
        repair_result = _build_repair_result(task_text=task_text, previous_error=previous_error)
        final_answer = str(repair_result.get('summary') or '').strip()
        out['react_scratchpad'] = {
            'agent_id': AGENT_REPAIR,
            'steps': [],
            'final_answer': final_answer,
            'total_steps': 0,
            'stopped_reason': 'REPAIR_COMPLETED' if repair_result.get('success') else 'REPAIR_FAILED',
            'confidence': 0.9 if repair_result.get('success') else 0.4,
            'risk_flags': [],
            'ts_ms': int(time.time() * 1000),
        }
        artifact_ref = f'artifact://{request_id}/{AGENT_REPAIR}_{int(time.time() * 1000)}'
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_REPAIR,
            artifact_ref=artifact_ref,
            summary=final_answer[:200],
            confidence=0.9 if repair_result.get('success') else 0.4,
            risk_flags=[],
        )
        out['repair_agent_trace'] = {
            'agent_id': AGENT_REPAIR,
            'artifact_ref': artifact_ref,
            'success': bool(repair_result.get('success')),
            'detail': str(repair_result.get('detail') or ''),
            'actions': list(repair_result.get('actions') or []),
            'ok_actions': int(repair_result.get('ok_actions') or 0),
            'ts_ms': int(time.time() * 1000),
        }
    except Exception as exc:
        artifacts = out.setdefault('agent_artifacts', {})
        artifacts[AGENT_REPAIR] = {
            'artifact_ref': f'artifact://{request_id}/{AGENT_REPAIR}_error',
            'summary': 'RepairAgent 执行异常',
            'confidence': 0.0,
            'risk_flags': [f'HANDLER_ERROR:{type(exc).__name__}'],
            'error': str(exc),
            'ts_ms': int(time.time() * 1000),
        }
    return out
