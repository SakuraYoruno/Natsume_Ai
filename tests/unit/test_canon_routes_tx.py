"""Canon route tests for the explicit application-service boundary.

The retired module-level WorldCanonStore/ledger locator had its own locks,
fallback database and transaction semantics.  Canon now owns those concerns
inside one migrated repository/application composition, so route tests bind
that boundary directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest
from fastapi import HTTPException

from host_brain.app import canon_routes
from host_brain.canon.application import CanonApplicationService
from host_brain.memory.canon_apply_service import CanonApplyCommand, CanonApplyResult
from tests.fakes.canon_runtime import compose_canon_test_runtime


@dataclass
class RecordingApplyPort:
    commands: list[CanonApplyCommand] = field(default_factory=list)
    error: Exception | None = None

    def apply(self, command: CanonApplyCommand) -> CanonApplyResult:
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return CanonApplyResult(
            apply_id=command.apply_id,
            operation=command.operation,
            subject_type=command.subject_type,
            subject_id=command.subject_id,
            target_doc_id=command.target_doc_id,
            state="COMMITTED",
            before_version=command.base_version,
            after_version=command.subject_version,
            after_hash=command.subject_hash,
        )

    def recover_pending(self, *, limit: int = 100) -> list[CanonApplyResult]:
        del limit
        return []

    def get_journal(self, apply_id: str):
        del apply_id
        return None


@pytest.fixture
def route_runtime(tmp_path):
    with compose_canon_test_runtime(
        tmp_path,
        request_id="canon-route-explicit-boundary",
    ) as runtime:
        apply_port = RecordingApplyPort()
        service = CanonApplicationService(
            repository=runtime.container.repository,
            world_root=tmp_path / "world",
            apply_service=apply_port,
        )
        canon_routes.set_canon_service_provider(lambda: service)
        try:
            yield runtime, service, apply_port
        finally:
            canon_routes.set_canon_service_provider(None)


def _submit(service: CanonApplicationService, proposal_id: str = "p-route") -> dict:
    return service.submit_proposal(
        proposal_id=proposal_id,
        target_doc_id="doc-route",
        proposed_title="Explicit route proposal",
        proposed_tags=["route"],
        proposed_body="Reviewable Canon proposal body.",
        proposer_node_id="canon.route.test",
        summary="explicit service boundary",
        trace_id="trace-submit",
        ts_ms=1_800_000_000_000,
    )


def _approve_request(proposal: dict) -> canon_routes.CanonProposalApproveRequest:
    return canon_routes.CanonProposalApproveRequest(
        apply_id="apply-route",
        subject_version=int(proposal["proposed_version"]),
        subject_hash=str(proposal["proposed_hash"]),
        target_doc_id=str(proposal["target_id"]),
        base_version=int(proposal["base_version"]),
        approval_token_ref="canon::test-token-reference",
        actor_id="canon-owner",
        session_id="canon-session",
        trace_id="canon-trace",
        evidence_refs=["audit://canon/review"],
    )


def test_route_module_has_no_store_or_ledger_locator() -> None:
    assert hasattr(canon_routes, "set_canon_service_provider")
    assert not hasattr(canon_routes, "_canon_store")
    assert not hasattr(canon_routes, "_canon_store_lock")
    assert not hasattr(canon_routes, "_ledger_provider")
    assert not hasattr(canon_routes, "_get_ledger_from_app")


def test_route_provider_fails_closed_when_not_composed() -> None:
    canon_routes.set_canon_service_provider(None)
    with pytest.raises(RuntimeError, match="not configured"):
        canon_routes._get_canon_service()

    canon_routes.set_canon_service_provider(lambda: object())
    try:
        with pytest.raises(RuntimeError, match="invalid service"):
            canon_routes._get_canon_service()
    finally:
        canon_routes.set_canon_service_provider(None)


def test_approve_routes_one_typed_command_through_application(route_runtime) -> None:
    _runtime, service, apply_port = route_runtime
    proposal = _submit(service)

    response = asyncio.run(
        canon_routes.approve_proposal(proposal["proposal_id"], _approve_request(proposal))
    )

    assert response.status == "APPLIED"
    assert len(apply_port.commands) == 1
    command = apply_port.commands[0]
    assert command.subject_id == proposal["proposal_id"]
    assert command.subject_hash == proposal["proposed_hash"]
    assert command.evidence_refs == ("audit://canon/review",)


def test_approve_unexpected_failure_is_mapped_to_500(route_runtime) -> None:
    _runtime, service, apply_port = route_runtime
    proposal = _submit(service, "p-route-failure")
    apply_port.error = RuntimeError("apply port unavailable")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            canon_routes.approve_proposal(
                proposal["proposal_id"],
                _approve_request(proposal),
            )
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.headers["X-Reason-Code"] == "CANON_SYNC_FAILED"


def test_reject_commits_through_canon_repository_journal(route_runtime) -> None:
    runtime, service, _apply_port = route_runtime
    proposal = _submit(service, "p-route-reject")
    before = runtime.container.repository.journal_count()

    response = asyncio.run(
        canon_routes.reject_proposal(
            proposal["proposal_id"],
            canon_routes.CanonProposalRejectRequest(
                reason="insufficient evidence",
                rejector_node_id="canon-reviewer",
            ),
        )
    )

    assert response.status == "rejected"
    assert runtime.container.repository.journal_count() == before + 1
    assert service.get_proposal(proposal["proposal_id"])["status"] == "rejected"
