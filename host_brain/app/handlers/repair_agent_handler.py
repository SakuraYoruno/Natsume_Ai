from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.sub_agent_protocol import AGENT_REPAIR
from host_brain.core.trace_redaction import build_public_agent_output, redact_legacy_trace_payload
from host_brain.integrations.graph_subgraph import write_agent_artifact_to_state

_logger = logging.getLogger(__name__)


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text


def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _repair_runtime_templates() -> dict[str, Any]:
    return _load_template_mapping("repair_agent_handler.runtime_texts")


def _resolve_python_executable() -> str:
    candidates = [
        os.path.join(os.getcwd(), ".venv", "Scripts", "python.exe"),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return sys.executable


def _command_exists(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(str(name or "").strip())
        if resolved:
            return resolved
    return None


def _run_command(command: list[str], *, timeout_s: int = 120) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(5, int(timeout_s)),
        check=False,
    )
    return {
        "command": list(command),
        "returncode": int(completed.returncode),
        "stdout": str(completed.stdout or "").strip(),
        "stderr": str(completed.stderr or "").strip(),
        "ok": completed.returncode == 0,
    }


def _playwright_module_available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _playwright_cli_available() -> bool:
    return _command_exists("playwright", "playwright.cmd") is not None


def _repair_browser_backend() -> dict[str, Any]:
    templates = _repair_runtime_templates()
    browser_templates = _mapping_section(templates, "browser_backend")

    actions: list[dict[str, Any]] = []
    python_exe = _resolve_python_executable()

    if not _playwright_module_available():
        actions.append(_run_command([python_exe, "-m", "pip", "install", "playwright"], timeout_s=300))
    if _playwright_module_available() or _playwright_cli_available():
        actions.append(_run_command([python_exe, "-m", "playwright", "install", "chromium"], timeout_s=300))

    browser_ready = _playwright_module_available()
    ok_actions = sum(1 for item in actions if item.get("ok"))
    if browser_ready:
        return {
            "success": True,
            "summary": _mapping_text(
                browser_templates,
                "success_summary",
            ),
            "detail": _mapping_text(
                browser_templates,
                "success_detail",
            ),
            "actions": actions,
            "ok_actions": ok_actions,
        }

    detail = _mapping_text(
        browser_templates,
        "failure_detail_default",
    )
    if not actions:
        detail = _mapping_text(
            browser_templates,
            "failure_detail_no_installer",
        )
    return {
        "success": False,
        "summary": _mapping_text(
            browser_templates,
            "failure_summary",
        ),
        "detail": detail,
        "actions": actions,
        "ok_actions": ok_actions,
    }


_REPAIR_PLANNER_ENABLED_ENV = "REPAIR_AGENT_LLM_ENABLED"
_REPAIR_PLANNER_TIMEOUT_MS = 15_000

_DANGEROUS_KEYWORDS = frozenset(
    [
        "rm -rf",
        "rmdir /s",
        "format",
        "del /f",
        "mkfs",
        "dd if=",
        ":(){:|:&};:",
        "shutdown",
        "reboot",
    ]
)


def _is_safe_command(cmd: list[str]) -> bool:
    cmd_text = " ".join(str(part) for part in cmd).lower()
    return not any(keyword in cmd_text for keyword in _DANGEROUS_KEYWORDS)


def _resolve_repair_planner_system_prompt() -> str:
    planner_templates = _mapping_section(_repair_runtime_templates(), "planner")
    return _mapping_text(
        planner_templates,
        "system_prompt",
    )


def _build_repair_planner_user_prompt(task_text: str, previous_error: str) -> str:
    planner_templates = _mapping_section(_repair_runtime_templates(), "planner")
    return _mapping_text(
        planner_templates,
        "user_prompt_template",
        task_text=task_text or _mapping_text(planner_templates, "missing_task_text"),
        previous_error=previous_error
        or _mapping_text(planner_templates, "missing_previous_error"),
    )


def _call_repair_planner_llm(task_text: str, previous_error: str) -> dict[str, Any] | None:
    try:
        from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
        from host_brain.model_runtime.live_prompts import REPAIR_AGENT_PLAN_PROMPT_ID  # noqa: PLC0415
        from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

        user_prompt = _build_repair_planner_user_prompt(task_text=task_text, previous_error=previous_error)
        result = complete_compiled_prompt_sync(
            prompt_id=REPAIR_AGENT_PLAN_PROMPT_ID,
            request_id=f"repair-planner::{int(time.time() * 1000)}",
            scene_type="REPAIR_PLANNER",
            cognitive_tier="B",
            deadline_ms=_REPAIR_PLANNER_TIMEOUT_MS,
            messages=[
                {"role": "system", "content": _resolve_repair_planner_system_prompt()},
                {"role": "user", "content": user_prompt},
            ],
            foreground=True,
            metadata={"chain": "repair_agent_planner"},
        )
        attempt_trace = model_attempt_result_trace(result)
        if not result.success:
            _logger.warning(
                "[RepairAgent] canonical planner unavailable reason=%s route=%s",
                result.error_code,
                attempt_trace.get("route_decision_id"),
            )
            return {
                "repair_type": "unsupported",
                "commands": [],
                "summary": "",
                "can_retry": False,
                "model_attempt_trace": attempt_trace,
            }
        plan = json.loads(str(result.content or "{}"))
        if not isinstance(plan, Mapping):
            return None
        plan = {**dict(plan), "model_attempt_trace": attempt_trace}
        _logger.info("[RepairAgent] LLM 规划器返回 repair_type=%s", plan.get("repair_type"))
        return plan
    except Exception as exc:
        _logger.warning("[RepairAgent] LLM 规划器调用失败（%s），降级到规则路径", exc)
        return None


def _execute_repair_plan(plan: dict[str, Any]) -> dict[str, Any]:
    templates = _repair_runtime_templates()
    execute_templates = _mapping_section(templates, "execute_plan")

    repair_type = str(plan.get("repair_type") or "unsupported").strip().lower()
    commands = list(plan.get("commands") or [])
    summary = str(plan.get("summary") or "").strip()
    can_retry = bool(plan.get("can_retry", True))
    model_attempt_trace = dict(plan.get("model_attempt_trace") or {})

    if repair_type == "unsupported" or not commands:
        result = {
            "success": False,
            "summary": summary
            or _mapping_text(
                execute_templates,
                "unsupported_summary",
            ),
            "detail": f"llm_planner:unsupported repair_type={repair_type}",
            "actions": [],
            "ok_actions": 0,
        }
        if model_attempt_trace:
            result["model_attempt_trace"] = model_attempt_trace
        return result

    actions: list[dict[str, Any]] = []
    for step in commands:
        cmd = list(step.get("cmd") or [])
        timeout_s = int(step.get("timeout_s") or 120)
        if not cmd:
            continue
        if not _is_safe_command(cmd):
            _logger.warning("[RepairAgent] 安全门控拒绝命令: %s", cmd)
            actions.append(
                {
                    "command": cmd,
                    "returncode": -1,
                    "stdout": "",
                    "stderr": "BLOCKED_BY_SAFETY_GATE",
                    "ok": False,
                }
            )
            continue
        actions.append(_run_command(cmd, timeout_s=timeout_s))

    ok_actions = sum(1 for action in actions if action.get("ok"))
    total_actions = len(actions)
    partial_success = ok_actions > 0
    all_success = ok_actions == total_actions

    if all_success:
        effective_summary = summary or _mapping_text(
            execute_templates,
            "all_success_summary",
        )
        effective_can_retry = can_retry
    elif partial_success:
        effective_summary = summary or _mapping_text(
            execute_templates,
            "partial_success_summary",
            ok_actions=ok_actions,
            total_actions=total_actions,
        )
        effective_can_retry = can_retry
    else:
        effective_summary = summary or _mapping_text(
            execute_templates,
            "all_failed_summary",
        )
        effective_can_retry = False

    result = {
        "success": partial_success,
        "all_success": all_success,
        "summary": effective_summary,
        "detail": f"llm_planner:{repair_type} ok={ok_actions}/{total_actions}",
        "actions": actions,
        "ok_actions": ok_actions,
        "can_retry": effective_can_retry,
    }
    if model_attempt_trace:
        result["model_attempt_trace"] = model_attempt_trace
    return result


def _build_repair_result(*, task_text: str, previous_error: str) -> dict[str, Any]:
    templates = _repair_runtime_templates()
    fallback_templates = _mapping_section(templates, "rule_fallback")
    normalized_error = str(previous_error or "").strip().upper()

    if (
        "BROWSER_UNAVAILABLE" in normalized_error
        or "PLAYWRIGHT" in normalized_error
    ):
        _logger.info("[RepairAgent] 快速路径：浏览器后端修复")
        return _repair_browser_backend()

    llm_enabled = str(os.environ.get(_REPAIR_PLANNER_ENABLED_ENV) or "true").strip().lower()
    if llm_enabled not in ("false", "0", "no", "off"):
        _logger.info("[RepairAgent] 尝试 canonical LLM 规划器，error_prefix=%s", normalized_error[:60])
        plan = _call_repair_planner_llm(task_text=task_text, previous_error=previous_error)
        if plan is not None:
            return _execute_repair_plan(plan)

    _logger.info("[RepairAgent] 规则兜底路径，error=%s", normalized_error[:60])
    return {
        "success": False,
        "summary": _mapping_text(
            fallback_templates,
            "summary",
        ),
        "detail": "unsupported repair target (llm_planner disabled or unavailable)",
        "actions": [],
        "ok_actions": 0,
    }


def repair_agent_handler(payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = dict(context or {})
    request_id = str(ctx.get("request_id") or payload.get("request_id") or "").strip()
    task_text = str(payload.get("user_input") or payload.get("query") or payload.get("task") or "").strip()
    previous_error = str(
        ctx.get("previous_agent_result") or payload.get("previous_agent_result") or payload.get("error") or ""
    ).strip()

    out = dict(payload)
    try:
        repair_result = _build_repair_result(task_text=task_text, previous_error=previous_error)
        final_answer = str(repair_result.get("summary") or "").strip()
        out["react_scratchpad"] = redact_legacy_trace_payload({
            "agent_id": AGENT_REPAIR,
            "steps": [],
            "final_answer": final_answer,
            "total_steps": 0,
            "stopped_reason": "REPAIR_COMPLETED" if repair_result.get("success") else "REPAIR_FAILED",
            "confidence": 0.9 if repair_result.get("success") else 0.4,
            "risk_flags": [],
            "ts_ms": int(time.time() * 1000),
        })
        out["agent_output"] = build_public_agent_output(final_answer)
        artifact_ref = f"artifact://{request_id}/{AGENT_REPAIR}_{int(time.time() * 1000)}"
        write_agent_artifact_to_state(
            out,
            agent_id=AGENT_REPAIR,
            artifact_ref=artifact_ref,
            summary=final_answer[:200],
            confidence=0.9 if repair_result.get("success") else 0.4,
            risk_flags=[],
        )
        out["repair_agent_trace"] = {
            "agent_id": AGENT_REPAIR,
            "artifact_ref": artifact_ref,
            "success": bool(repair_result.get("success")),
            "all_success": bool(repair_result.get("all_success", repair_result.get("success"))),
            "can_retry": bool(repair_result.get("can_retry", repair_result.get("success"))),
            "detail": str(repair_result.get("detail") or ""),
            "actions": list(repair_result.get("actions") or []),
            "ok_actions": int(repair_result.get("ok_actions") or 0),
            "ts_ms": int(time.time() * 1000),
        }
        if repair_result.get("model_attempt_trace"):
            out["repair_agent_trace"]["model_attempt"] = dict(
                repair_result["model_attempt_trace"]
            )
    except Exception as exc:
        handler_error_templates = _mapping_section(_repair_runtime_templates(), "handler_error")
        artifacts = out.setdefault("agent_artifacts", {})
        artifacts[AGENT_REPAIR] = {
            "artifact_ref": f"artifact://{request_id}/{AGENT_REPAIR}_error",
            "summary": _mapping_text(
                handler_error_templates,
                "summary",
            ),
            "confidence": 0.0,
            "risk_flags": [f"HANDLER_ERROR:{type(exc).__name__}"],
            "error": str(exc),
            "ts_ms": int(time.time() * 1000),
        }
    return out
