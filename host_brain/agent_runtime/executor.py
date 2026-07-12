"""WorkOrder-first Agent runtime executor."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from host_brain.agent_runtime.acceptance_judge import (
    ACCEPTANCE_CRITERION_FAILED,
    AcceptanceDecision,
    judge_acceptance_result,
)
from host_brain.agent_runtime.agent_executor import AgentExecutor
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.prompt_profiles import build_agent_prompt, prompt_profile_name
from host_brain.agent_runtime.result_contract import AgentResult, blocked_result
from host_brain.agent_runtime.review_policy import ReviewPolicy
from host_brain.agent_runtime.verification_policy import (
    VERIFICATION_REQUIRED_NOT_PASSED,
    VerificationGate,
    VerificationPolicy,
)
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.core.ids import new_event_id, now_ms
from host_brain.core.subprocess_utils import run as _subprocess_run
from host_brain.core.trace_redaction import sanitize_public_payload
from host_brain.evolution_lab import validate_evolution_branch

logger = logging.getLogger(__name__)


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


def _apply_required_verification_gate(result: AgentResult, order: AgentWorkOrder) -> AgentResult:
    if not VerificationGate.required_plan_not_passed(order.verification_plan, result.verification):
        return result
    verification = dict(result.verification or {})
    status = str(verification.get("status") or "NOT_RUN").strip() or "NOT_RUN"
    reason = str(verification.get("reason_code") or VERIFICATION_REQUIRED_NOT_PASSED).strip()
    result.status = "BLOCKED"
    result.summary = "Agent WorkOrder blocked because required verification did not pass."
    result.reason_code = VERIFICATION_REQUIRED_NOT_PASSED
    result.detail = f"required verification status={status} reason_code={reason}"
    result.review_required = True
    result.risk_flags = list(dict.fromkeys([*result.risk_flags, "required_verification_failed"]))
    metadata = dict(result.metadata or {})
    metadata["verification_gate"] = {
        "required": True,
        "status": status,
        "reason_code": reason,
    }
    result.metadata = metadata
    return result


def _apply_acceptance_judge(result: AgentResult, order: AgentWorkOrder) -> AgentResult:
    if not order.acceptance_criteria:
        return result
    critic_candidate = None
    if isinstance(result.metadata.get("acceptance_critic"), dict):
        critic_candidate = dict(result.metadata.get("acceptance_critic") or {})
    judgement = judge_acceptance_result(
        result.to_dict(),
        work_order_id=order.work_order_id,
        acceptance_criteria=tuple(order.acceptance_criteria),
        critic_candidate=critic_candidate,
        task_graph_id=str(order.metadata.get("task_graph_id") or "").strip() or None,
        task_node_id=str(order.metadata.get("task_node_id") or "").strip() or None,
        attempt_id=str(order.metadata.get("attempt_id") or "").strip() or None,
        started_at_ms=None,
        finished_at_ms=now_ms(),
    )
    result.acceptance = judgement.to_dict()
    metadata = dict(result.metadata or {})
    metadata["acceptance_judge"] = {
        "decision": judgement.decision.value,
        "reason_code": judgement.reason_code,
        "criterion_count": len(judgement.criteria),
    }
    result.metadata = metadata
    if judgement.decision is AcceptanceDecision.FAIL and result.status == "EXECUTED":
        result.status = "FAILED"
        result.summary = "Agent WorkOrder failed acceptance criteria."
        result.reason_code = ACCEPTANCE_CRITERION_FAILED
        result.detail = f"acceptance decision={judgement.decision.value} reason_code={judgement.reason_code}"
        result.risk_flags = list(dict.fromkeys([*result.risk_flags, "acceptance_failed"]))
    elif judgement.decision is AcceptanceDecision.REVIEW:
        result.review_required = True
    return result


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

        prompt = build_agent_prompt(order, workspace=resolution.path)
        self.artifact_store.write_text(run_dir / "prompt.txt", prompt)

        empty_git_summary = {
            "changed_files": [],
            "git_diff_stat": None,
            "git_status": None,
            "status_map": {},
        }
        try:
            before = _collect_git_summary(resolution.path)
        except Exception:
            logger.warning("Could not collect pre-execution git state for %s", order.work_order_id)
            before = empty_git_summary

        logger.info("Canonical AgentExecutor selected for %s", order.work_order_id)
        try:
            result = AgentExecutor().execute(order, workspace=resolution.path)
        except Exception as exc:
            logger.warning("Canonical AgentExecutor execution failed: %s", exc)
            result = AgentResult(
                status="FAILED",
                work_order_id=order.work_order_id,
                summary="Agent executor execution crashed.",
                artifacts_dir=str(run_dir),
                verification={"status": "NOT_RUN", "reason": "agent crashed"},
                risk_flags=["agent_error"],
                review_required=self.review_policy.review_required(order),
                reason_code="AGENT_ERROR",
                detail=str(exc),
            )

        try:
            after = _collect_git_summary(resolution.path)
            git_summary = _diff_git_summary(before, after)
        except Exception:
            logger.warning("Could not collect post-execution git state for %s", order.work_order_id)
            git_summary = {
                "changed_files": [],
                "git_diff_stat": None,
                "git_status": None,
                "workspace_dirty_baseline": bool(before.get("status_map")),
                "baseline_changed_files": sorted(dict(before.get("status_map") or {}).keys()),
                "current_changed_files": [],
            }

        verification = self.verification_policy.run(
            order.verification_plan,
            workspace=resolution.path,
            request_id=str(order.metadata.get("request_id") or "").strip() or None,
            intent_id=event_id,
            work_order_id=order.work_order_id,
        )
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "execution_authority": "canonical_agent_executor",
                "workspace": str(resolution.path),
                "workspace_source": resolution.source,
                "prompt_profile": prompt_profile_name(order),
                "event_id": event_id,
                "ts_ms": ts,
            }
        )
        result.metadata = metadata
        result.verification = verification
        result.changed_files = list(git_summary.get("changed_files") or [])
        result.git_diff_stat = git_summary.get("git_diff_stat")
        result.artifacts_dir = str(run_dir)
        self.artifact_store.write_json(run_dir / "changed_files.json", git_summary)
        self.artifact_store.write_json(run_dir / "verification.json", verification)
        result = _apply_required_verification_gate(result, order)
        result = _apply_acceptance_judge(result, order)
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
