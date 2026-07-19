from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

import host_brain.agent_runtime.accepted_result as accepted_result_module
import host_brain.agent_runtime.executor as agent_runtime_executor_module
from host_brain.agent_runtime.accepted_result import InternalAcceptedResultHandler
from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.internal_result_proposal import (
    InternalAgentProposalAcceptance,
    InternalAgentProposalError,
    InternalAgentProposalVerifier,
    build_public_internal_agent_result,
    validate_internal_agent_result_payload,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.app.internal_task_runtime import InternalAgentCommandService
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from natsume_contracts.task import (
    HostCommandSurface,
    build_host_command,
)


class _CommandReader:
    def get(self, _command_id: str) -> None:
        return None


class _ProjectionReader:
    def load(self, _receipt_ref: str) -> None:
        return None


class _SensitiveCommitFailureRepository:
    def get_by_command(self, _command_id: str) -> None:
        return None

    def append(self, **_kwargs: Any) -> None:
        raise RuntimeError("password=do-not-persist bearer=private-value")


@pytest.mark.asyncio
async def test_unknown_result_commit_redacts_sensitive_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = {
        "command_id": "host-command://internal/opaque-error",
        "work_order_id": "work-order-opaque-error",
    }
    monkeypatch.setattr(
        accepted_result_module,
        "_accepted_payload",
        lambda _payload: (canonical, {}),
    )
    handler = InternalAcceptedResultHandler(
        command_repository=_CommandReader(),
        projection_store=_ProjectionReader(),
        result_repository=_SensitiveCommitFailureRepository(),
    )

    async def _reopen(_canonical, _preview):  # noqa: ANN001, ANN202
        return {
            "status": "EXECUTED",
            "summary": "safe result",
        }

    monkeypatch.setattr(handler, "_reopen", _reopen)

    with pytest.raises(AcceptanceEffectOutcomeUnknown) as raised:
        await handler({})

    detail = raised.value.detail
    assert "password" not in detail
    assert "bearer" not in detail
    assert "do-not-persist" not in detail
    assert "private-value" not in detail
    assert "error_type=builtins.RuntimeError" in detail
    assert "error_digest=sha256:" in detail


def test_internal_result_projection_rejects_secret_and_oversize_payload() -> None:
    with pytest.raises(InternalAgentProposalError, match="secret-like"):
        validate_internal_agent_result_payload(
            {
                "status": "EXECUTED",
                "metadata": {"access_token": "must-not-enter-projection"},
            }
        )

    with pytest.raises(InternalAgentProposalError, match="size limit"):
        validate_internal_agent_result_payload(
            {
                "status": "EXECUTED",
                "summary": "x" * 16_777_217,
            }
        )


def test_public_projection_strips_real_executor_local_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _AgentExecutor:
        def __init__(self, **_dependencies: Any) -> None:
            pass

        def execute(self, order, *, workspace, cancelled=None):  # noqa: ANN001, ANN202
            assert cancelled is None
            assert workspace.is_absolute()
            return AgentResult(
                status="EXECUTED",
                work_order_id=order.work_order_id,
                summary="password=must-not-enter-the-public-projection",
                result_text="safe public result",
                verification={"status": "PASS"},
                acceptance={"decision": "ACCEPT"},
                review_required=False,
            )

    monkeypatch.setattr(agent_runtime_executor_module, "AgentExecutor", _AgentExecutor)
    monkeypatch.setattr(
        agent_runtime_executor_module,
        "_collect_git_summary",
        lambda _workspace: {
            "changed_files": [],
            "git_diff_stat": None,
            "git_status": None,
            "status_map": {},
        },
    )
    raw = AgentRuntimeExecutor(
        artifact_store=AgentArtifactStore(tmp_path / "runs"),
        model_attempt_service=object(),
        prompt_registry=object(),
    ).execute(
        AgentWorkOrder(
            work_order_id="work-order-real-result-shape",
            domain="code",
            run_mode="inspect",
            objective="exercise the canonical result shape",
            workspace={
                "strategy": "temp_workspace",
                "runs_root": str(tmp_path / "workspaces"),
            },
            allowed_actions=["read_files", "generate_report"],
            review_policy={"review_required": False},
        )
    )

    assert raw["artifacts_dir"]
    assert raw["metadata"]["workspace"]
    assert "workspace_lease_fencing_token" in raw["metadata"]
    assert str(raw["summary"]).startswith("redacted://sha256/")

    public = build_public_internal_agent_result(raw)
    encoded = str(public)
    assert "artifacts_dir" not in public
    assert public["artifact_refs"][0].startswith(
        "opaque-agent-artifact://sha256/"
    )
    assert "workspace" not in public["metadata"]
    assert "workspace_source" not in public["metadata"]
    assert "workspace_lease_owner" not in public["metadata"]
    assert "workspace_lease_fencing_token" not in public["metadata"]
    assert str(tmp_path) not in encoded
    assert "must-not-enter-the-public-projection" not in encoded
    validate_internal_agent_result_payload(public)


@pytest.mark.parametrize(
    ("result_status", "inner_decision", "review_required", "reason_code"),
    [
        ("EXECUTED", "NOT_RUN", False, "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"),
        ("EXECUTED", "RETRY", False, "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"),
        ("EXECUTED", "REVIEW", False, "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"),
        ("EXECUTED", "SPLIT", False, "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"),
        ("FAILED", "ACCEPT", False, "INTERNAL_AGENT_RESULT_NOT_EXECUTED"),
        ("EXECUTED", "ACCEPT", True, "INTERNAL_AGENT_REVIEW_REQUIRED"),
    ],
)
def test_internal_result_gates_reject_non_terminal_inner_outcomes(
    result_status: str,
    inner_decision: str,
    review_required: bool,
    reason_code: str,
) -> None:
    command = build_host_command(
        surface=HostCommandSurface.INTERNAL,
        request_id=f"request-inner-{inner_decision.lower()}",
        session_id="session-inner-acceptance",
        principal_id="principal-inner-acceptance",
        input_ref="internal-input://sha256/" + "4" * 64,
        input_sha256="sha256:" + "4" * 64,
        root_work_order_id="work-order-inner-acceptance",
        graph_id="graph-inner-acceptance",
        graph_revision=1,
        route_type="delegate",
        target_id="agent_runtime",
        business_scope="internal-agent:code",
        operation_fingerprint="sha256:" + "5" * 64,
        created_at_ms=1_900_000_000_000,
        ingress_claim_ref="ingress-claim://internal/inner-acceptance",
        ingress_audit_ref="audit://internal/inner-acceptance",
        governor_grant_ref="governor-grant://internal/inner-acceptance",
    )
    work_order = WorkOrderV3(
        work_order_id=command.root_work_order_id,
        domain="code",
        run_mode="inspect",
        objective="reject an incomplete inner acceptance",
    )
    context = {
        "work_order_id": work_order.work_order_id,
        "graph_id": command.graph_id,
        "node_id": "node-inner-acceptance",
        "attempt_id": "attempt-inner-acceptance",
    }

    class _ProjectionStore:
        @staticmethod
        def load(_receipt_ref: str) -> None:
            return None

    class _Authority:
        @staticmethod
        def require(**_kwargs: Any) -> dict[str, Any]:
            return {
                "result": {
                    "status": result_status,
                    "acceptance": {"decision": inner_decision},
                    "review_required": review_required,
                }
            }

    verifier = InternalAgentProposalVerifier(projection_store=_ProjectionStore())
    verifier._authority = _Authority()  # type: ignore[assignment]
    acceptance = InternalAgentProposalAcceptance(
        projection_store=_ProjectionStore(),
    )
    acceptance._authority = _Authority()  # type: ignore[assignment]
    verification_id = "verify-" + hashlib.sha256(
        command.command_id.encode("utf-8")
    ).hexdigest()[:40]
    verification = verifier.verify(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit={"receipt_ref": "worker-projection://inner-acceptance"},
        evidence_refs=(),
    )
    assert verification["status"] == "FAIL"
    assert verification["reason_code"] == reason_code

    judgement = acceptance.judge(
        command=command,
        work_order=work_order,
        execution_context=context,
        worker_commit={"receipt_ref": "worker-projection://inner-acceptance"},
        verification_result={
            "verification_id": verification_id,
            "status": "PASS",
            "work_order_id": work_order.work_order_id,
            "task_graph_id": command.graph_id,
            "task_node_id": context["node_id"],
            "attempt_id": context["attempt_id"],
        },
        evidence_refs=(),
    )

    assert judgement["decision"] == "REJECT"
    assert judgement["reason_code"] == reason_code

    result = {
        "status": result_status,
        "acceptance": {"decision": inner_decision},
        "review_required": review_required,
    }
    digest = accepted_result_module._canonical_digest(result)
    projection = {
        "acceptance_effect_intents": [
            {
                "effect_type": "internal.agent.result_commit",
                "commit_scope": "NODE",
                "payload": {
                    "schema_version": "Natsume.internal.agent_result_proposal.v1",
                    "work_order_id": work_order.work_order_id,
                    "target_id": "agent_runtime",
                    "operation_fingerprint": command.operation_fingerprint,
                    "result_digest": digest,
                    "result": result,
                },
            }
        ]
    }
    with pytest.raises(
        accepted_result_module.InternalAcceptedResultError,
        match="not exact terminal success",
    ):
        accepted_result_module._proposal_result(
            projection,
            canonical={
                "work_order_id": work_order.work_order_id,
                "target_id": "agent_runtime",
                "operation_fingerprint": command.operation_fingerprint,
                "result_digest": digest,
            },
        )

@pytest.mark.asyncio
async def test_internal_claim_stays_open_until_exact_result_commit() -> None:
    command = build_host_command(
        surface=HostCommandSurface.INTERNAL,
        request_id="request-internal-effect-recovery",
        session_id="session-internal-effect-recovery",
        principal_id="principal-internal-effect-recovery",
        input_ref="internal-input://sha256/" + "1" * 64,
        input_sha256="sha256:" + "1" * 64,
        root_work_order_id="work-order-internal-effect-recovery",
        graph_id="graph-internal-effect-recovery",
        graph_revision=1,
        route_type="delegate",
        target_id="agent_runtime",
        business_scope="internal-agent:code",
        operation_fingerprint="sha256:" + "2" * 64,
        created_at_ms=1_900_000_000_000,
        ingress_claim_ref="ingress-claim://internal/effect-recovery",
        ingress_audit_ref="audit://internal/effect-recovery",
        governor_grant_ref="governor-grant://internal/effect-recovery",
    )

    class Runtime:
        terminal: dict[str, Any] = {}
        exact_reads = 0

        @staticmethod
        def existing_command(*, request_id: str) -> dict[str, Any]:
            assert request_id == command.request_id
            return {"command": command}

        async def execute_stream(self, **_kwargs: Any):
            yield dict(self.terminal)

        async def exact_accepted_result_receipt(
            self,
            *,
            command_id: str,
            expected_receipt_ref: str,
        ) -> str:
            assert command_id == command.command_id
            self.exact_reads += 1
            return expected_receipt_ref

    class Claims:
        def __init__(self) -> None:
            self.completed: list[tuple[str, str]] = []

        def complete_claim(
            self,
            *,
            claim_ref: str,
            result_ref: str,
            at_ms: int | None = None,
        ) -> None:
            del at_ms
            self.completed.append((claim_ref, result_ref))

    runtime = Runtime()
    claims = Claims()
    service = InternalAgentCommandService(
        runtime=runtime,  # type: ignore[arg-type]
        intent_engine=type("Intent", (), {"evaluate_intent": lambda self: None})(),
        claim_result_store=claims,
    )
    work_order = AgentWorkOrder(
        work_order_id="work-order-internal-effect-recovery",
        domain="code",
        run_mode="inspect",
        objective="test exact result recovery",
    )
    base = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "execution_receipt": {"status": "ACCEPTED", "accepted": True},
    }
    for effect_status in ("RECOVERY_REQUIRED", "FAILED", "NO_EFFECTS"):
        runtime.terminal = {
            **base,
            "internal_agent_effect_status": effect_status,
            "receipt_ref": "worker-projection://must-not-seal-claim",
        }
        await service.execute(
            work_order=work_order,
            request_id=command.request_id,
            session_id=command.session_id,
            ts_ms=command.created_at_ms,
            command_authority={},
        )
    assert claims.completed == []
    assert runtime.exact_reads == 0

    result_ref = "agentresult://sha256/" + "3" * 64
    runtime.terminal = {
        **base,
        "internal_agent_effect_status": "COMMITTED",
        "internal_agent_result_receipt_ref": result_ref,
        "receipt_ref": result_ref,
    }
    await service.execute(
        work_order=work_order,
        request_id=command.request_id,
        session_id=command.session_id,
        ts_ms=command.created_at_ms,
        command_authority={},
    )

    assert runtime.exact_reads == 1
    assert claims.completed == [(command.ingress_claim_ref, result_ref)]
