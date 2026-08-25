from __future__ import annotations

from types import SimpleNamespace

import pytest
from natsume_contracts.task import HostCommandSurface, build_host_command

from host_brain.app.internal_agent_routes import (
    execute_work_order,
)
from host_brain.app.internal_task_runtime import InternalAgentCommandService
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3


class _IntentEngine:
    def evaluate_intent(self, **kwargs):
        intent = kwargs["raw_intent"]
        return {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "reason_code": None,
            "tokens": {},
            "ts_ms": intent["ts_ms"],
        }


class _Runtime:
    def __init__(self) -> None:
        self.orders = []
        self.command = None
        self.work_order = None

    def existing_command(self, *, request_id: str):
        if self.command is None or self.command.request_id != request_id:
            return None
        return {"command": self.command, "work_order": self.work_order}

    async def execute_stream(self, **kwargs):
        self.orders.append(kwargs["work_order"])
        self.work_order = WorkOrderV3.from_v2_mapping(kwargs["work_order"].to_dict())
        authority = kwargs["command_authority"]
        self.command = build_host_command(
            surface=HostCommandSurface.INTERNAL,
            request_id=kwargs["request_id"],
            session_id=kwargs["session_id"],
            principal_id=authority["principal_id"],
            ingress_claim_ref=authority["claim_ref"],
            ingress_audit_ref=authority["audit_ref"],
            governor_grant_ref=kwargs["governor_grant_ref"],
            input_ref="internal-input://unit/route",
            input_sha256="sha256:" + "a" * 64,
            root_work_order_id=kwargs["work_order"].work_order_id,
            graph_id="graph-internal-route",
            graph_revision=1,
            route_type="internal_agent",
            target_id="agent_runtime",
            business_scope="internal-agent-route",
            operation_fingerprint="sha256:" + "b" * 64,
            created_at_ms=kwargs["ts_ms"],
        )
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "result": {
                "status": "EXECUTED",
                "result_text": "governed route result",
            },
            "execution_receipt": {
                "command_id": "host-command://internal/test",
                "executor": "canonical",
                "status": "ACCEPTED",
                "receipt_ref": "agent-execution://internal/route",
            },
            "internal_agent_effect_status": "COMMITTED",
            "internal_agent_result_receipt_ref": "agent-execution://internal/route",
            "replayed": False,
        }

    async def exact_budget_rejection_claim_result_ref(self, **_kwargs):  # noqa: ANN003, ANN201
        return None

    async def exact_nonacceptance_checkpoint_claim_result_ref(self, **_kwargs):  # noqa: ANN003, ANN201
        return None

    async def exact_accepted_result_receipt(  # noqa: ANN201
        self,
        *,
        command_id: str,
        expected_receipt_ref: str,
    ):
        assert command_id
        return expected_receipt_ref


class _ClaimStore:
    def __init__(self) -> None:
        self.completed = []

    def complete_claim(self, **kwargs) -> None:
        self.completed.append(kwargs)


def _request(runtime: _Runtime):
    claim_store = _ClaimStore()
    service = InternalAgentCommandService(
        runtime=runtime,
        intent_engine=_IntentEngine(),
        claim_result_store=claim_store,
    )
    authority = SimpleNamespace(
        admissible=True,
        request_id="request-internal-route",
        principal_id="principal-internal-route",
        claim_ref="ingress-claim://internal/route",
        audit_ref="audit://secure-ingress/internal/route",
        body_sha256="a" * 64,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            request_started_ms=1_800_000_000_000,
            internal_agent_command_service=service,
            internal_agent_authority=authority,
        )
    )
    request.claim_store = claim_store
    return request


@pytest.mark.asyncio
async def test_execute_route_uses_governed_service_and_ignores_background_escape() -> None:
    runtime = _Runtime()
    request = _request(runtime)
    response = await execute_work_order(
        request,
        payload={
            "session_id": "session-internal-route",
            "work_order": {
                "work_order_id": "work-order-internal-route",
                "domain": "code",
                "run_mode": "inspect",
                "objective": "inspect the governed route",
                "workspace": {"strategy": "temp_workspace"},
                "allowed_actions": ["read_files"],
                "forbidden_actions": ["write_files"],
                "acceptance_criteria": ["result exists"],
            },
        },
        background=True,
    )

    assert response["accepted"] is True
    assert response["background"] is False
    assert response["requested_background"] is True
    assert response["result"]["result_text"] == "governed route result"
    assert response["execution_receipt"]["executor"] == "canonical"
    assert len(runtime.orders) == 1
    assert request.claim_store.completed == [
        {
            "claim_ref": "ingress-claim://internal/route",
            "result_ref": "agent-execution://internal/route",
            "at_ms": 1_800_000_000_000,
        }
    ]
