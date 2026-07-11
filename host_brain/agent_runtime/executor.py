"""WorkOrder-first Agent runtime executor."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
from host_brain.core.subprocess_utils import run as _subprocess_run
import sys
from typing import Any

from host_brain.agent_runtime.agent_executor import AgentExecutor, agent_enabled
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.prompt_profiles import build_agent_prompt, prompt_profile_name
from host_brain.agent_runtime.result_contract import AgentResult, blocked_result
from host_brain.agent_runtime.review_policy import ReviewPolicy
from host_brain.agent_runtime.verification_policy import VerificationPolicy
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.core.ids import new_event_id, now_ms
from host_brain.core.trace_redaction import sanitize_public_payload
from host_brain.evolution_lab import validate_evolution_branch
from host_brain.integrations.codex_cli_provider import build_codex_cli_env, codex_cli_command

logger = logging.getLogger(__name__)

MAX_CODEX_OUTPUT_CHARS = 24_000


def evaluate_task_runtime_shadow(
    store: Any,
    graph_id: str,
    graph_revision: int,
    *,
    confirmed_owner: str,
    now_ts_ms: int,
    artifact_refs: dict[str, str] | None = None,
    budget_allowed: dict[str, bool] | None = None,
    legacy_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Read-only TASK-0469 adapter; it never enters the execution path."""
    from host_brain.agent_runtime.task_scheduler import (  # noqa: PLC0415
        CanonicalShadowScheduler,
        evaluate_configured_shadow,
    )
    from host_brain.app.settings.core import get_core_settings  # noqa: PLC0415

    settings = get_core_settings()
    report = evaluate_configured_shadow(
        CanonicalShadowScheduler(),
        store,
        graph_id,
        graph_revision,
        runtime_mode=settings.task_runtime_mode,
        configured_owner=settings.task_runtime_shadow_owner,
        confirmed_owner=confirmed_owner,
        now_ts_ms=now_ts_ms,
        artifact_refs=artifact_refs,
        budget_allowed=budget_allowed,
        legacy_decisions=legacy_decisions,
    )
    return None if report is None else report.to_dict()


def _collect_git_summary(workspace: Path) -> dict[str, Any]:
    if not (workspace / ".git").exists():
        return {"changed_files": [], "git_diff_stat": None, "git_status": None, "status_map": {}}

    def _run_git(*args: str) -> str:
        completed = _subprocess_run(
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
            line = raw_line.rstrip()
            if len(line) < 4:
                continue
            status = line[:2]
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[-1].strip()
            if path:
                status_map[path] = status
    return {
        "changed_files": sorted(status_map.keys()),
        "git_diff_stat": _run_git("diff", "--stat") or None,
        "git_status": git_status,
        "status_map": status_map,
    }


def _diff_git_summary(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_map = dict(before.get("status_map") or {})
    after_map = dict(after.get("status_map") or {})
    delta_files = sorted(
        path for path, status in after_map.items() if str(before_map.get(path) or "") != str(status or "")
    )
    return {
        "changed_files": delta_files,
        "git_diff_stat": None if before_map else after.get("git_diff_stat"),
        "git_status": after.get("git_status"),
        "workspace_dirty_baseline": bool(before_map),
        "baseline_changed_files": sorted(before_map.keys()),
        "current_changed_files": sorted(after_map.keys()),
    }


def _current_git_branch(workspace: Path) -> str:
    if not (workspace / ".git").exists():
        return ""
    completed = _subprocess_run(
        ["git", "-C", str(workspace), "branch", "--show-current"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    return str(completed.stdout or "").strip()


def _extract_thread_id(stdout: str) -> str | None:
    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "thread.started":
            thread_id = str(payload.get("thread_id") or "").strip()
            if thread_id:
                return thread_id
    return None


def _process_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _merge_process_text(*values: Any) -> str:
    parts = [_process_text(value) for value in values if _process_text(value)]
    if not parts:
        return ""
    merged: list[str] = []
    for part in parts:
        if part not in merged:
            merged.append(part)
    return "".join(merged)


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            _subprocess_run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True, timeout=10, check=False)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _metadata_text(order: AgentWorkOrder, key: str, env_name: str) -> str:
    value = str(order.metadata.get(key) or os.environ.get(env_name) or "").strip()
    return value


_CODEX_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def _env_token(value: Any) -> str:
    text = str(value or "").strip()
    out: list[str] = []
    previous_underscore = False
    for char in text:
        if char.isalnum():
            out.append(char.upper())
            previous_underscore = False
        elif not previous_underscore:
            out.append("_")
            previous_underscore = True
    return "".join(out).strip("_")


def _valid_reasoning_effort(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in _CODEX_REASONING_EFFORTS else ""


def _resolve_codex_runtime_reasoning_effort(order: AgentWorkOrder) -> str:
    explicit = _valid_reasoning_effort(order.metadata.get("codex_reasoning_effort"))
    if explicit:
        return explicit
    task_token = _env_token(
        order.metadata.get("reasoning_task")
        or order.metadata.get("task_type")
        or order.metadata.get("task_kind")
    )
    skill_token = _env_token(order.metadata.get("skill_id"))
    domain_token = _env_token(order.domain)
    mode_token = _env_token(order.run_mode)
    env_names = []
    if skill_token:
        env_names.append(f"HOST_CODEX_RUNTIME_REASONING_SKILL_{skill_token}")
    if task_token:
        env_names.append(f"HOST_CODEX_RUNTIME_REASONING_TASK_{task_token}")
    if domain_token and mode_token:
        env_names.append(f"HOST_CODEX_RUNTIME_REASONING_DOMAIN_{domain_token}_MODE_{mode_token}")
    if domain_token:
        env_names.append(f"HOST_CODEX_RUNTIME_REASONING_DOMAIN_{domain_token}")
    if mode_token:
        env_names.append(f"HOST_CODEX_RUNTIME_REASONING_MODE_{mode_token}")
    env_names.append("HOST_CODEX_RUNTIME_REASONING_EFFORT")
    for env_name in env_names:
        resolved = _valid_reasoning_effort(os.environ.get(env_name))
        if resolved:
            return resolved
    return ""


def _build_codex_command(
    *,
    codex_cmd: str,
    order: AgentWorkOrder,
    workspace: Path,
    last_message_path: Path,
    sandbox_mode: str,
) -> list[str]:
    command = [codex_cmd, "exec"]
    model = _metadata_text(order, "codex_model", "HOST_CODEX_RUNTIME_MODEL")
    if model:
        command.extend(["-m", model])
    reasoning_effort = _resolve_codex_runtime_reasoning_effort(order)
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    command.extend(
        [
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
    )
    return command


def _build_codex_subprocess_env(*, run_dir: Path) -> dict[str, str]:
    env = build_codex_cli_env(run_dir, temp_dir_name=".codex_tmp")

    home = str(Path.home())
    if home and home != ".":
        env.setdefault("HOME", home)
        env.setdefault("USERPROFILE", home)
        if os.name == "nt":
            env.setdefault("APPDATA", str(Path(home) / "AppData" / "Roaming"))
            env.setdefault("LOCALAPPDATA", str(Path(home) / "AppData" / "Local"))
    return env


class AgentRuntimeExecutor:
    def __init__(
        self,
        *,
        artifact_store: AgentArtifactStore | None = None,
        workspace_policy: WorkspacePolicy | None = None,
        verification_policy: VerificationPolicy | None = None,
        review_policy: ReviewPolicy | None = None,
    ) -> None:
        self.artifact_store = artifact_store or AgentArtifactStore()
        self.workspace_policy = workspace_policy or WorkspacePolicy()
        self.verification_policy = verification_policy or VerificationPolicy()
        self.review_policy = review_policy or ReviewPolicy()

    def execute(self, work_order: AgentWorkOrder | dict[str, Any]) -> dict[str, Any]:
        order = work_order if isinstance(work_order, AgentWorkOrder) else AgentWorkOrder.from_mapping(work_order)
        run_dir = self.artifact_store.prepare_run_dir(order.work_order_id)
        self.artifact_store.write_json(run_dir / "work_order.json", order.to_dict())
        event_id = new_event_id()
        ts = now_ms()

        try:
            order.validate()
            resolution = self.workspace_policy.resolve(order)
            allowed, reason = self.workspace_policy.enforce_write_policy(order, resolution)
            if not allowed:
                result = blocked_result(
                    work_order_id=order.work_order_id,
                    summary="Agent WorkOrder blocked by workspace or approval policy.",
                    artifacts_dir=str(run_dir),
                    detail=reason,
                    risk_flags=["policy_block"],
                    review_required=self.review_policy.review_required(order),
                )
                self._persist_result(run_dir, result, order=order, event_id=event_id, ts_ms=ts)
                return result.to_dict()
            if order.run_mode == "self_evolve":
                current_branch = _current_git_branch(resolution.path)
                branch_ok, branch_reason = validate_evolution_branch(current_branch)
                if not branch_ok:
                    result = blocked_result(
                        work_order_id=order.work_order_id,
                        summary="Agent self-evolve WorkOrders require an experiment branch.",
                        artifacts_dir=str(run_dir),
                        detail=branch_reason or "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED",
                        risk_flags=["evolution_branch_required"],
                        review_required=True,
                        reason_code=branch_reason or "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED",
                    )
                    self._persist_result(run_dir, result, order=order, event_id=event_id, ts_ms=ts)
                    return result.to_dict()
        except Exception as exc:
            result = blocked_result(
                work_order_id=order.work_order_id,
                summary="Agent WorkOrder could not be prepared.",
                artifacts_dir=str(run_dir),
                detail=str(exc),
                risk_flags=["work_order_invalid"],
                review_required=order.review_required,
            )
            self._persist_result(run_dir, result, order=order, event_id=event_id, ts_ms=ts)
            return result.to_dict()

        # ── Agent 路径：HOST_NATSUME_AGENT_ENABLED=true 时使用 API Agent ──
        if agent_enabled():
            logger.info(
                "Agent executor path selected for %s (provider=%s)",
                order.work_order_id,
                os.environ.get("HOST_NATSUME_AGENT_PROVIDER", "deepseek"),
            )
            try:
                agent_result = AgentExecutor().execute(order, workspace=resolution.path)
            except Exception as agent_exc:
                logger.warning("Agent executor execution failed: %s", agent_exc)
                agent_result = AgentResult(
                    status="FAILED",
                    work_order_id=order.work_order_id,
                    summary="Agent executor execution crashed.",
                    artifacts_dir=str(run_dir),
                    verification={"status": "NOT_RUN", "reason": "agent crashed"},
                    risk_flags=["agent_error"],
                    review_required=self.review_policy.review_required(order),
                    reason_code="AGENT_ERROR",
                    detail=str(agent_exc),
                )
            # 写入 prompt（与 CLI 路径保持一致性）
            self.artifact_store.write_text(
                run_dir / "prompt.txt",
                build_agent_prompt(order, workspace=resolution.path),
            )
            # 收集 git diff
            after = _collect_git_summary(resolution.path)
            git_summary = {"changed_files": [], "git_diff_stat": None}
            try:
                before = _collect_git_summary(resolution.path)
                git_summary = _diff_git_summary({"changed_files": [], "git_diff_stat": None}, after)
            except Exception:
                pass
            # 运行验证
            verification = self.verification_policy.run(
                order.verification_plan,
                workspace=resolution.path,
                request_id=str(order.metadata.get("request_id") or "").strip() or None,
                intent_id=event_id,
            )
            # 合并 agent 元数据
            agent_result.verification = verification
            agent_result.changed_files = list(git_summary.get("changed_files") or [])
            agent_result.git_diff_stat = git_summary.get("git_diff_stat")
            agent_result.artifacts_dir = str(run_dir)
            self.artifact_store.write_json(run_dir / "changed_files.json", git_summary)
            self.artifact_store.write_json(run_dir / "verification.json", verification)
            self._persist_result(run_dir, agent_result, order=order, event_id=event_id, ts_ms=ts)
            return agent_result.to_dict()

        prompt = build_agent_prompt(order, workspace=resolution.path)
        self.artifact_store.write_text(run_dir / "prompt.txt", prompt)
        codex_cmd = codex_cli_command()
        if codex_cmd is None:
            result = AgentResult(
                status="DEGRADED",
                work_order_id=order.work_order_id,
                summary="Codex CLI is unavailable.",
                result_text="",
                artifacts_dir=str(run_dir),
                verification={"status": "NOT_RUN", "reason": "codex CLI unavailable"},
                risk_flags=["codex_cli_unavailable"],
                review_required=self.review_policy.review_required(order),
                reason_code="CODEX_CLI_UNAVAILABLE",
                detail="codex CLI not found in PATH or disabled",
            )
            self._persist_result(run_dir, result, order=order, event_id=event_id, ts_ms=ts)
            return result.to_dict()

        last_message_path = (run_dir / "last_message.txt").resolve()
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        events_path = run_dir / "events.jsonl"
        sandbox_mode = "read-only" if order.run_mode in {"inspect", "research", "audit", "translate"} else "workspace-write"
        command = _build_codex_command(
            codex_cmd=codex_cmd,
            order=order,
            workspace=resolution.path,
            last_message_path=last_message_path,
            sandbox_mode=sandbox_mode,
        )
        codex_env = _build_codex_subprocess_env(run_dir=run_dir)
        codex_home = codex_env.get("CODEX_HOME")

        timeout_seconds = int(order.metadata.get("timeout_seconds") or order.verification_plan.get("timeout_seconds") or 300)
        before = _collect_git_summary(resolution.path)
        proc: subprocess.Popen[str] | None = None
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(resolution.path),
                env=codex_env,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = proc.communicate(input=prompt, timeout=max(15, min(timeout_seconds, 1800)))
            self.artifact_store.write_text(stdout_path, stdout)
            self.artifact_store.write_text(stderr_path, stderr)
            events = [line for line in str(stdout or "").splitlines() if line.strip().startswith("{")]
            self.artifact_store.write_text(events_path, "\n".join(events))
            output_text = self.artifact_store.read_text(last_message_path)[:MAX_CODEX_OUTPUT_CHARS]
            after = _collect_git_summary(resolution.path)
            git_summary = _diff_git_summary(before, after)
            verification = self.verification_policy.run(
                order.verification_plan,
                workspace=resolution.path,
                request_id=str(order.metadata.get("request_id") or "").strip() or None,
                intent_id=event_id,
            )
            self.artifact_store.write_json(run_dir / "changed_files.json", git_summary)
            self.artifact_store.write_json(run_dir / "verification.json", verification)
            status = "EXECUTED" if output_text and proc.returncode == 0 else "FAILED"
            summary = "Agent WorkOrder executed." if status == "EXECUTED" else "Agent WorkOrder failed."
            failure_detail = (stderr or stdout or "codex exec failed")[:2048]
            result = AgentResult(
                status=status,
                work_order_id=order.work_order_id,
                summary=summary,
                result_text=output_text,
                changed_files=list(git_summary.get("changed_files") or []),
                git_diff_stat=git_summary.get("git_diff_stat"),
                artifacts_dir=str(run_dir),
                thread_id=_extract_thread_id(stdout),
                verification=verification,
                risk_flags=[] if status == "EXECUTED" else ["codex_exec_failed"],
                review_required=self.review_policy.review_required(order),
                detail=failure_detail if status == "FAILED" else "codex exec completed",
                reason_code=None if status == "EXECUTED" else "CODEX_EXEC_FAILED",
                metadata={
                    "workspace": str(resolution.path),
                    "workspace_source": resolution.source,
                    "sandbox_mode": sandbox_mode,
                    "prompt_profile": prompt_profile_name(order),
                    "codex_home": codex_home,
                    "codex_model": _metadata_text(order, "codex_model", "HOST_CODEX_RUNTIME_MODEL") or None,
                    "codex_reasoning_effort": _resolve_codex_runtime_reasoning_effort(order) or None,
                    "returncode": proc.returncode,
                    "event_id": event_id,
                    "ts_ms": ts,
                },
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _process_text(getattr(exc, "output", ""))
            stderr = _process_text(getattr(exc, "stderr", ""))
            if proc is not None:
                _terminate_process_tree(proc)
                try:
                    extra_stdout, extra_stderr = proc.communicate(timeout=10)
                    stdout = _merge_process_text(stdout, extra_stdout)
                    stderr = _merge_process_text(stderr, extra_stderr)
                except Exception:
                    pass
            self.artifact_store.write_text(stdout_path, stdout)
            self.artifact_store.write_text(stderr_path, stderr)
            events = [line for line in str(stdout or "").splitlines() if line.strip().startswith("{")]
            self.artifact_store.write_text(events_path, "\n".join(events))
            after = _collect_git_summary(resolution.path)
            git_summary = _diff_git_summary(before, after)
            verification = {"status": "NOT_RUN", "reason": "codex execution timed out"}
            self.artifact_store.write_json(run_dir / "changed_files.json", git_summary)
            self.artifact_store.write_json(run_dir / "verification.json", verification)
            result = AgentResult(
                status="DEGRADED",
                work_order_id=order.work_order_id,
                summary="Agent WorkOrder timed out.",
                artifacts_dir=str(run_dir),
                thread_id=_extract_thread_id(stdout),
                verification=verification,
                risk_flags=["codex_exec_timeout"],
                review_required=self.review_policy.review_required(order),
                reason_code="CODEX_EXEC_TIMEOUT",
                detail=(
                    f"codex exec timed out after {timeout_seconds}s; "
                    f"partial logs persisted to {stdout_path.name}/{stderr_path.name}"
                ),
                metadata={
                    "workspace": str(resolution.path),
                    "workspace_source": resolution.source,
                    "sandbox_mode": sandbox_mode,
                    "prompt_profile": prompt_profile_name(order),
                    "codex_home": codex_home,
                    "codex_model": _metadata_text(order, "codex_model", "HOST_CODEX_RUNTIME_MODEL") or None,
                    "codex_reasoning_effort": _resolve_codex_runtime_reasoning_effort(order) or None,
                    "timeout_seconds": timeout_seconds,
                    "returncode": getattr(proc, "returncode", None),
                    "event_id": event_id,
                    "ts_ms": ts,
                },
            )
        except Exception as exc:
            logger.warning("Agent runtime execution failed: %s", exc)
            result = AgentResult(
                status="FAILED",
                work_order_id=order.work_order_id,
                summary="Agent WorkOrder execution crashed.",
                artifacts_dir=str(run_dir),
                verification={"status": "NOT_RUN", "reason": "executor crashed"},
                risk_flags=["codex_executor_error"],
                review_required=self.review_policy.review_required(order),
                reason_code="CODEX_EXECUTOR_ERROR",
                detail=str(exc),
            )
        self._persist_result(run_dir, result, order=order, event_id=event_id, ts_ms=ts)
        return result.to_dict()

    def _persist_result(self, run_dir: Path, result: AgentResult, *, order: AgentWorkOrder, event_id: str, ts_ms: int) -> None:
        metadata = sanitize_public_payload({
            "event_id": event_id,
            "ts_ms": ts_ms,
            "work_order_id": order.work_order_id,
            "domain": order.domain,
            "run_mode": order.run_mode,
            "artifact_dir": str(run_dir),
            **dict(result.metadata or {}),
        })
        self.artifact_store.write_json(run_dir / "metadata.json", metadata)
        self.artifact_store.write_json(run_dir / "result.json", result.to_dict())
        if not (run_dir / "changed_files.json").exists():
            self.artifact_store.write_json(run_dir / "changed_files.json", {"changed_files": result.changed_files})
        if not (run_dir / "verification.json").exists():
            self.artifact_store.write_json(run_dir / "verification.json", result.verification)


def execute_agent_runtime_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    raw_order = data.get("work_order")
    if not isinstance(raw_order, dict):
        run_mode = str(data.get("run_mode") or "inspect").strip().lower()
        if run_mode in {"worker", "self_heal", "self_evolve"}:
            order = AgentWorkOrder.from_mapping(
                {
                    "work_order_id": data.get("work_order_id") or f"blocked_{new_event_id()}",
                    "domain": data.get("domain") or "code",
                    "run_mode": run_mode,
                    "objective": data.get("objective") or data.get("task") or "Missing WorkOrder",
                    "workspace": data.get("workspace") if isinstance(data.get("workspace"), dict) else {},
                    "review_policy": {"review_required": True},
                }
            )
            result = blocked_result(
                work_order_id=order.work_order_id,
                summary="Write-capable Agent execution requires a WorkOrder.",
                reason_code="AGENT_WORKORDER_REQUIRED",
                detail="payload.work_order is required for write-capable agent run modes",
                risk_flags=["missing_work_order"],
                review_required=True,
            )
            return result.to_dict()
        raw_order = {
            "work_order_id": data.get("work_order_id"),
            "domain": data.get("domain") or "code",
            "run_mode": run_mode,
            "objective": data.get("objective") or data.get("task") or "",
            "workspace": data.get("workspace") if isinstance(data.get("workspace"), dict) else {"strategy": "temp_workspace"},
            "input_pack": {"user_request": data.get("task") or data.get("objective") or ""},
            "allowed_actions": ["read_files", "generate_report"],
            "forbidden_actions": ["write_files", "modify_secret_files"],
            "review_policy": {"review_required": False},
            "metadata": {"request_id": data.get("request_id")},
        }
    return AgentRuntimeExecutor().execute(AgentWorkOrder.from_mapping(raw_order))


__all__ = ["AgentRuntimeExecutor", "execute_agent_runtime_payload"]
