from __future__ import annotations

import importlib.util
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any

from host_brain.app.sub_agent_protocol import AGENT_REPAIR
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state

_logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 环境修复：Python / 浏览器后端工具函数
# ──────────────────────────────────────────────


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


# ──────────────────────────────────────────────
# 修复历史缓存（相同错误码不重复调用 LLM）
# ──────────────────────────────────────────────

# 缓存结构：{error_fingerprint: (plan_or_None, timestamp_s)}
_repair_plan_cache: dict[str, tuple[dict[str, Any] | None, float]] = {}
_REPAIR_CACHE_TTL_S = 300  # 5 分钟内相同错误复用缓存


def _get_error_fingerprint(previous_error: str) -> str:
    """提取错误指纹：取错误文本前 120 字符的小写，用于缓存 key。"""
    return previous_error.strip().lower()[:120]


# ──────────────────────────────────────────────
# LLM 驱动的通用修复规划器
# ──────────────────────────────────────────────

_REPAIR_PLANNER_SYSTEM_PROMPT = """\
你是一个运行环境自动修复专家。用户会告诉你一个任务失败的错误信息，你需要：
1. 分析错误根因（缺少哪个依赖/工具/配置）
2. 输出一个 JSON 修复计划，格式如下：

{
  "repair_type": "pip_install" | "npm_install" | "system_command" | "config_fix" | "unsupported",
  "commands": [
    {"cmd": ["python", "-m", "pip", "install", "xxx"], "timeout_s": 120, "desc": "安装 xxx 包"},
    ...
  ],
  "summary": "简短说明修复了什么（中文，20字以内）",
  "can_retry": true
}

规则：
- 只输出 JSON，不要任何额外文字
- commands 中每条命令必须是可直接执行的列表形式
- 如果无法判断如何修复，repair_type 填 "unsupported"，commands 为空列表
- 不要生成危险命令（rm -rf、格式化、删除系统文件等）
- pip install 使用 python -m pip install 形式
- npm install 全局包使用 npm i -g 形式
"""

_REPAIR_PLANNER_ENABLED_ENV = 'REPAIR_AGENT_LLM_ENABLED'
_REPAIR_PLANNER_PROVIDER_ENV = 'REPAIR_AGENT_LLM_PROVIDER'
_REPAIR_PLANNER_MODEL_ENV = 'REPAIR_AGENT_LLM_MODEL_ID'
_REPAIR_PLANNER_TIMEOUT_MS = 15_000

# 危险命令关键词黑名单（安全门控）
_DANGEROUS_KEYWORDS = frozenset([
    'rm -rf', 'rmdir /s', 'format', 'del /f', 'mkfs',
    'dd if=', ':(){:|:&};:', 'shutdown', 'reboot',
])


def _is_safe_command(cmd: list[str]) -> bool:
    """安全门控：拒绝包含危险关键词的命令。"""
    cmd_str = ' '.join(str(c) for c in cmd).lower()
    return not any(kw in cmd_str for kw in _DANGEROUS_KEYWORDS)


def _call_repair_planner_llm(task_text: str, previous_error: str) -> dict[str, Any] | None:
    """调用 LLM 生成修复计划，失败时返回 None（降级到规则路径）。

    注意：缓存命中检查在调用方 _build_repair_result 中完成，本函数只负责纯粹的 LLM 调用和写缓存。
    """
    fingerprint = _get_error_fingerprint(previous_error)
    now = time.time()
    try:
        from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
            _load_api_key_from_env,
            stream_llm_chunks,
        )

        provider_name = str(os.environ.get(_REPAIR_PLANNER_PROVIDER_ENV) or 'gemini').strip().lower()
        model_id = str(
            os.environ.get(_REPAIR_PLANNER_MODEL_ENV)
            or os.environ.get('HOST_CHAT_AGENT_MODEL_ID')
            or 'gemini-2.0-flash'
        ).strip()
        api_key = _load_api_key_from_env(provider_name)
        if not api_key:
            _logger.warning('[RepairAgent] LLM 规划器缺少 API Key（provider=%s），降级到规则路径', provider_name)
            return None

        user_prompt = (
            f'任务描述：{task_text or "（未提供）"}\n'
            f'失败错误：{previous_error or "（未提供）"}\n\n'
            '请输出修复计划 JSON。'
        )

        chunks: list[str] = []
        for chunk in stream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=_REPAIR_PLANNER_TIMEOUT_MS,
            prompt_text=user_prompt,
            system_prompt=_REPAIR_PLANNER_SYSTEM_PROMPT,
            gen_params={'temperature': 0.1, 'top_p': 0.8},
        ):
            chunks.append(chunk)

        raw = ''.join(chunks).strip()
        # 提取 JSON（LLM 有时会包裹在 ```json ... ``` 中）
        if '```' in raw:
            import re  # noqa: PLC0415
            m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
            raw = m.group(1).strip() if m else raw
        plan = json.loads(raw)
        _logger.info('[RepairAgent] LLM 规划器返回 repair_type=%s', plan.get('repair_type'))
        _repair_plan_cache[fingerprint] = (plan, now)
        return plan
    except Exception as exc:
        _logger.warning('[RepairAgent] LLM 规划器调用失败（%s），降级到规则路径', exc)
        # 缓存 None，避免短时间内对同一错误反复尝试失败的 LLM 调用
        _repair_plan_cache[fingerprint] = (None, now)
        return None


def _execute_repair_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """执行 LLM 生成的修复计划，返回标准修复结果。"""
    repair_type = str(plan.get('repair_type') or 'unsupported').strip().lower()
    commands = list(plan.get('commands') or [])
    summary = str(plan.get('summary') or '').strip()
    can_retry = bool(plan.get('can_retry', True))

    if repair_type == 'unsupported' or not commands:
        return {
            'success': False,
            'summary': summary or '这次故障类型 LLM 也无法自动修复。',
            'detail': f'llm_planner:unsupported repair_type={repair_type}',
            'actions': [],
            'ok_actions': 0,
        }

    actions: list[dict[str, Any]] = []
    for step in commands:
        cmd = list(step.get('cmd') or [])
        timeout_s = int(step.get('timeout_s') or 120)
        if not cmd:
            continue
        # 安全门控：拒绝危险命令
        if not _is_safe_command(cmd):
            _logger.warning('[RepairAgent] 安全门控拒绝命令: %s', cmd)
            actions.append({
                'command': cmd,
                'returncode': -1,
                'stdout': '',
                'stderr': 'BLOCKED_BY_SAFETY_GATE',
                'ok': False,
            })
            continue
        actions.append(_run_command(cmd, timeout_s=timeout_s))

    ok_actions = sum(1 for a in actions if a.get('ok'))
    total_actions = len(actions)
    # 部分成功策略：只要有命令成功执行（非全部被安全门控拦截），就允许重试
    # 全部失败 / 全部被拦截 → 不允许重试
    partial_success = ok_actions > 0
    all_success = ok_actions == total_actions

    if all_success:
        effective_summary = summary or '修复完成，可以重试。'
        effective_can_retry = can_retry
    elif partial_success:
        effective_summary = summary or f'部分修复步骤完成（{ok_actions}/{total_actions}），尝试重试。'
        effective_can_retry = can_retry  # 尊重 LLM 的判断
    else:
        effective_summary = summary or '所有修复步骤均失败，无法自动重试。'
        effective_can_retry = False

    return {
        'success': partial_success,  # 部分成功也视为修复有效
        'all_success': all_success,
        'summary': effective_summary,
        'detail': f'llm_planner:{repair_type} ok={ok_actions}/{total_actions}',
        'actions': actions,
        'ok_actions': ok_actions,
        'can_retry': effective_can_retry,
    }


def _build_repair_result(*, task_text: str, previous_error: str) -> dict[str, Any]:
    """修复结果构建器：快速路径（规则）→ LLM 规划器 → 兜底。

    优先级：
    1. 浏览器后端错误 → 直接走规则快速路径（无需 LLM，速度快）
    2. LLM_ENABLED=true → 调用 LLM 规划器生成通用修复计划
    3. 其他已知错误码 → 规则路径兜底
    4. 完全未知 → 返回不支持
    """
    normalized_error = str(previous_error or '').strip().upper()

    # ── 快速路径：浏览器后端（规则，无需 LLM）──
    if 'BROWSER_UNAVAILABLE' in normalized_error or 'PLAYWRIGHT' in normalized_error or 'AGENT-BROWSER' in normalized_error:
        _logger.info('[RepairAgent] 快速路径：浏览器后端修复')
        return _repair_browser_backend()

    # ── LLM 规划器路径（通用，需要 API Key）──
    llm_enabled = str(os.environ.get(_REPAIR_PLANNER_ENABLED_ENV) or 'true').strip().lower()
    if llm_enabled not in ('false', '0', 'no', 'off'):
        # 缓存命中检查：相同错误指纹在 TTL 内直接复用上次结果，跳过 LLM 调用
        fingerprint = _get_error_fingerprint(previous_error)
        now = time.time()
        if fingerprint in _repair_plan_cache:
            cached_plan, cached_ts = _repair_plan_cache[fingerprint]
            if now - cached_ts < _REPAIR_CACHE_TTL_S:
                _logger.info('[RepairAgent] 命中修复缓存，跳过 LLM 调用，fingerprint=%s…', fingerprint[:40])
                if cached_plan is not None:
                    return _execute_repair_plan(cached_plan)
                # 缓存的是 None（上次 LLM 调用失败），直接走兜底
                _logger.info('[RepairAgent] 缓存命中但上次 LLM 失败，走规则兜底')
        else:
            _logger.info('[RepairAgent] 尝试 LLM 规划器，error_prefix=%s', normalized_error[:60])
            plan = _call_repair_planner_llm(task_text=task_text, previous_error=previous_error)
            # 无论成功与否都写缓存（None 表示 LLM 失败，TTL 内不再重试）
            _repair_plan_cache[fingerprint] = (plan, now)
            if plan is not None:
                return _execute_repair_plan(plan)

    # ── 规则兜底：已知错误码 ──
    _logger.info('[RepairAgent] 规则兜底路径，error=%s', normalized_error[:60])
    return {
        'success': False,
        'summary': '这次故障类型暂时无法自动修复，建议手动检查依赖环境。',
        'detail': 'unsupported repair target (llm_planner disabled or unavailable)',
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
            'all_success': bool(repair_result.get('all_success', repair_result.get('success'))),
            'can_retry': bool(repair_result.get('can_retry', repair_result.get('success'))),
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
