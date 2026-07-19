"""WorkOrder-first Agent runtime executor."""

from __future__ import annotations

from contextlib import nullcontext
import logging
from pathlib import Path
import secrets
import time
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_judge import (
    ACCEPTANCE_CRITERION_FAILED,
    AcceptanceDecision,
    judge_acceptance_result,
)
from host_brain.agent_runtime.agent_executor import AgentExecutor
from host_brain.agent_runtime.agent_tools import (
    AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.prompt_profiles import build_agent_prompt, prompt_profile_name
from host_brain.contracts.agent_runtime.projection_recovery import (
    WORKER_TIMING_SCHEMA_VERSION,
    validate_projection_recovery_context,
)
from host_brain.agent_runtime.result_contract import AgentResult, blocked_result
from host_brain.contracts.agent_runtime.review_policy import ReviewPolicy
from host_brain.agent_runtime.verification_policy import (
    VERIFICATION_REQUIRED_NOT_PASSED,
    VerificationGate,
    VerificationPolicy,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy
from host_brain.agent_runtime.workspace_runtime import (
    DurableWorkspaceRuntime,
    DurableWorkspaceRuntimeError,
)
from host_brain.core.ids import new_event_id, now_ms
from host_brain.core.subprocess_utils import run as _subprocess_run
from host_brain.core.trace_redaction import sanitize_public_payload
from host_brain.evolution_lab import validate_evolution_branch

logger = logging.getLogger(__name__)


def _opaque_executor_failure(exc: BaseException) -> str:
    error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return f"error_type={error_type} incident={secrets.token_hex(24)}"


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
        workspace_runtime: DurableWorkspaceRuntime | None = None,
        verification_policy: VerificationPolicy | None = None,
        review_policy: ReviewPolicy | None = None,
        model_attempt_service: Any = None,
        prompt_registry: Any = None,
    ) -> None:
        self.artifact_store = artifact_store or AgentArtifactStore()
        self.workspace_runtime = workspace_runtime
        self.workspace_policy = workspace_policy or WorkspacePolicy()
        self.verification_policy = verification_policy or VerificationPolicy()
        self.review_policy = review_policy or ReviewPolicy()
        self.model_attempt_service = model_attempt_service
        self.prompt_registry = prompt_registry

    def execute(
        self,
        work_order: AgentWorkOrder | dict[str, Any],
        *,
        workspace_execution_authority: dict[str, Any] | None = None,
        projection_recovery_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        order = work_order if isinstance(work_order, AgentWorkOrder) else AgentWorkOrder.from_mapping(work_order)
        event_id = new_event_id()
        ts = now_ms()
        run_dir: Path | None = None
        governed_stage = None
        recovery_envelope: dict[str, Any] | None = None

        try:
            order.validate()
            if order.requires_write:
                result = blocked_result(
                    work_order_id=order.work_order_id,
                    summary=(
                        "Agent write execution requires a governed Tool TaskGraph node."
                    ),
                    artifacts_dir="",
                    reason_code=AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                    detail=(
                        "The proposal-only Agent runtime cannot execute or accept a "
                        "write-capable WorkOrder."
                    ),
                    review_required=True,
                    risk_flags=[AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED],
                )
                result.verification = {
                    "status": "NOT_RUN",
                    "reason_code": AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                }
                result.acceptance = {
                    "decision": "NOT_RUN",
                    "reason_code": AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
                }
                result.metadata = {
                    "execution_authority": "proposal_only_agent_executor",
                    "model_attempts": [],
                    "side_effects_executed": False,
                }
                return result.to_dict()
            effective_workspace_policy = self.workspace_policy
            if (
                self.workspace_runtime is not None
                and str(dict(order.workspace or {}).get("strategy") or "").strip().lower()
                == "durable_workspace"
            ):
                effective_workspace_policy = WorkspacePolicy(
                    workspace_allocator=self.workspace_runtime.allocator,
                    workspace_lease_provider=lambda candidate, workspace_id: (
                        self.workspace_runtime.lease_for_order(
                            candidate,
                            workspace_id,
                            execution_authority=workspace_execution_authority,
                        )
                    ),
                )
            resolution = effective_workspace_policy.resolve(order)
            allowed, reason = effective_workspace_policy.enforce_write_policy(order, resolution)
            if not allowed:
                result = blocked_result(
                    work_order_id=order.work_order_id,
                    summary="Agent WorkOrder blocked by workspace or approval policy.",
                    artifacts_dir="",
                    detail=reason,
                    risk_flags=["policy_block"],
                    review_required=self.review_policy.review_required(order),
                )
                return result.to_dict()
            if order.run_mode == "self_evolve":
                current_branch = _current_git_branch(resolution.path)
                branch_ok, branch_reason = validate_evolution_branch(current_branch)
                if not branch_ok:
                    result = blocked_result(
                        work_order_id=order.work_order_id,
                        summary="Agent self-evolve WorkOrders require an experiment branch.",
                        artifacts_dir="",
                        detail=branch_reason or "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED",
                        risk_flags=["evolution_branch_required"],
                        review_required=True,
                        reason_code=branch_reason or "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED",
                    )
                    return result.to_dict()

            # Prompt construction may validate or normalize private WorkOrder
            # material.  Do it before allocating a governed stage so a
            # preparation exception cannot strand a half-written owner.
            prompt = build_agent_prompt(order, workspace=resolution.path)
            if workspace_execution_authority is not None:
                if (
                    self.workspace_runtime is None
                    or resolution.source != "durable_workspace"
                    or not resolution.workspace_id
                ):
                    raise DurableWorkspaceRuntimeError(
                        "WORKSPACE_RUNTIME_BINDING_INVALID",
                        "governed execution has no durable Workspace staging owner",
                    )
                governed_stage = self.artifact_store.prepare_governed_stage(
                    workspace_id=resolution.workspace_id,
                    execution_authority=workspace_execution_authority,
                )
                run_dir = governed_stage.stage_dir
            else:
                if projection_recovery_context is not None:
                    raise ValueError(
                        "projection recovery context requires governed execution"
                    )
                run_dir = self.artifact_store.prepare_run_dir(order.work_order_id)
            # Raw WorkOrder material is written only after validation, policy
            # enforcement and durable Workspace/Attempt ownership succeed.
            self.artifact_store.write_json(run_dir / "work_order.json", order.to_dict())
            self.artifact_store.write_text(run_dir / "prompt.txt", prompt)
            if projection_recovery_context is not None:
                recovery_envelope = validate_projection_recovery_context(
                    projection_recovery_context
                )
                self.artifact_store.write_json(
                    run_dir / "projection_recovery.json",
                    recovery_envelope,
                )
        except Exception as exc:
            if governed_stage is not None:
                try:
                    self.artifact_store.discard_unsealed_governed_stage(
                        governed_stage
                    )
                except Exception:
                    logger.exception(
                        "Could not safely discard incomplete governed Agent stage"
                    )
            reason_code = str(getattr(exc, "reason_code", "") or "").strip() or None
            failure = _opaque_executor_failure(exc)
            result = blocked_result(
                work_order_id=order.work_order_id,
                summary="Agent WorkOrder could not be prepared.",
                artifacts_dir="",
                detail=failure,
                risk_flags=[
                    "workspace_grant_required" if reason_code == "WORKSPACE_WRITE_GRANT_REQUIRED" else "work_order_invalid"
                ],
                review_required=order.review_required,
                reason_code=reason_code,
            )
            return result.to_dict()

        assert run_dir is not None

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
        workspace_scope = (
            self.workspace_runtime.heartbeat_scope(resolution)
            if self.workspace_runtime is not None
            and resolution.source == "durable_workspace"
            else None
        )
        scope = workspace_scope or nullcontext()
        worker_started_ts_ms = now_ms()
        worker_started_ns = time.monotonic_ns()
        try:
            with scope:
                try:
                    if self.model_attempt_service is None or self.prompt_registry is None:
                        raise RuntimeError("AGENT_MODEL_RUNTIME_NOT_COMPOSED")
                    result = AgentExecutor(
                        model_attempt_service=self.model_attempt_service,
                        prompt_registry=self.prompt_registry,
                    ).execute(
                        order,
                        workspace=resolution.path,
                        cancelled=(
                            workspace_scope.cancelled
                            if workspace_scope is not None
                            else None
                        ),
                    )
                except Exception as exc:
                    failure = _opaque_executor_failure(exc)
                    logger.warning(
                        "Canonical AgentExecutor execution failed: %s",
                        failure,
                    )
                    result = AgentResult(
                        status="FAILED",
                        work_order_id=order.work_order_id,
                        summary="Agent executor execution crashed.",
                        artifacts_dir=str(run_dir),
                        verification={"status": "NOT_RUN", "reason": "agent crashed"},
                        risk_flags=["agent_error"],
                        review_required=self.review_policy.review_required(order),
                        reason_code="AGENT_ERROR",
                        detail=failure,
                    )

                if workspace_scope is not None:
                    workspace_scope.assert_can_start_action(
                        "post-execution git summary"
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

                if workspace_scope is not None:
                    workspace_scope.assert_can_start_action(
                        "post-execution verification"
                    )
                verification = self.verification_policy.run(
                    order.verification_plan,
                    workspace=resolution.path,
                    request_id=str(order.metadata.get("request_id") or "").strip() or None,
                    intent_id=event_id,
                    work_order_id=order.work_order_id,
                )
        except DurableWorkspaceRuntimeError as exc:
            failure = _opaque_executor_failure(exc)
            logger.error(
                "Durable workspace lease lost for %s: %s",
                order.work_order_id,
                failure,
            )
            result = AgentResult(
                status="FAILED",
                work_order_id=order.work_order_id,
                summary="Agent execution lost its durable workspace lease.",
                artifacts_dir=str(run_dir),
                verification={"status": "NOT_RUN", "reason_code": exc.reason_code},
                risk_flags=["workspace_lease_lost"],
                review_required=True,
                reason_code=exc.reason_code,
                detail=f"{exc.reason_code}; {failure}",
            )
            git_summary = {
                "changed_files": [],
                "git_diff_stat": None,
                "git_status": None,
                "workspace_dirty_baseline": bool(before.get("status_map")),
                "baseline_changed_files": sorted(dict(before.get("status_map") or {}).keys()),
                "current_changed_files": [],
            }
            verification = dict(result.verification)
        metadata = dict(result.metadata or {})
        metadata.update(
            {
                "execution_authority": "canonical_agent_executor",
                "workspace": str(resolution.path),
                "workspace_source": resolution.source,
                "workspace_id": resolution.workspace_id,
                "workspace_lease_owner": resolution.lease_owner,
                "workspace_lease_fencing_token": resolution.lease_fencing_token,
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
        if recovery_envelope is not None:
            worker_finished_ts_ms = now_ms()
            worker_elapsed_ms = max(
                1,
                (time.monotonic_ns() - worker_started_ns + 999_999) // 1_000_000,
            )
            recovery_envelope = {
                **recovery_envelope,
                "worker_timing": {
                    "schema_version": WORKER_TIMING_SCHEMA_VERSION,
                    "started_ts_ms": worker_started_ts_ms,
                    "finished_ts_ms": worker_finished_ts_ms,
                    "elapsed_ms": worker_elapsed_ms,
                },
            }
            # This authority is measured inside the canonical executor after
            # Agent/verification complete and before the immutable seal.
            self.artifact_store.write_json(
                run_dir / "projection_recovery.json",
                recovery_envelope,
            )
        self._persist_result(run_dir, result, order=order, event_id=event_id, ts_ms=ts)
        payload = result.to_dict()
        if governed_stage is not None:
            seal = self.artifact_store.seal_governed_stage(governed_stage)
            # This private transfer object never enters result.json.  The
            # proposal boundary converts it to a bounded protected ref/digest.
            payload["_protected_stage"] = seal.protected_payload()
        return payload

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


def execute_agent_runtime_payload(
    payload: dict[str, Any],
    *,
    model_attempt_service: Any = None,
    prompt_registry: Any = None,
) -> dict[str, Any]:
    """Execute a normalized payload with ports supplied only by trusted wiring.

    ``payload`` deliberately has no service-injection fields.  The only caller
    that may supply model ports is the composed executor-registry adapter after
    it has revalidated its sealed governed execution context.
    """

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
    return AgentRuntimeExecutor(
        model_attempt_service=model_attempt_service,
        prompt_registry=prompt_registry,
    ).execute(AgentWorkOrder.from_mapping(raw_order))


__all__ = ["AgentRuntimeExecutor", "execute_agent_runtime_payload"]
