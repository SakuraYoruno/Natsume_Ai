from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from host_brain.app.governed_operator_runtime import (
    build_governed_operator_composition,
)
from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.app.host_runtime_context_wiring import (
    compose_action_preview_service,
    compose_host_runtime_context,
    runtime_environment_observation_provider,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    build_default_obs_domain_command_registry,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.embodiment import HostEmbodimentRuntime
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.composition.task_runtime import (
    build_process_task_runtime_container,
)
from host_brain.core.mode_transition import ModeTransitionService
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.observability.command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandEnvelope,
    bind_server_authority,
)
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.secure_ingress_repository import (
    SQLiteSecureIngressStore,
)
from host_brain.secure_ingress import ClaimStatus, IngressAuditEvent
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.skills.agent_runtime_registry import (
    build_agent_runtime_skill_registry,
)
from natsume_contracts.task import HostCommandSurface


_PRINCIPAL = "principal.operator.task-control"
_TENANT = "tenant-operator-task-control"


class _UnusedEmbodimentPort:
    async def submit(self, _command: Any) -> Any:
        raise AssertionError("Operator Task control must not invoke Edge")


class _OperatorOutcomeFinalizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def finalize_review_outcome(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


def _runtime_authority_provider(
    embodiment_runtime: HostEmbodimentRuntime,
) -> dict[str, Any]:
    return {
        **embodiment_runtime.renew_authority(),
        "runtime_scope_id": embodiment_runtime.runtime_scope_id,
    }


def _operator_envelope(
    *,
    store: SQLiteSecureIngressStore,
    registry: Any,
    command_id: str,
    payload: dict[str, Any],
    request_id: str,
) -> tuple[ObsCommandEnvelope, bytes]:
    spec = registry.resolve_spec(command_id)
    assert spec is not None
    normalized = spec.payload_schema.validate(payload)
    raw_body = json.dumps(
        {
            "command_id": command_id,
            "payload": payload,
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    body_sha256 = hashlib.sha256(raw_body).hexdigest()
    now = int(time.time() * 1_000)
    claim = store.try_claim(
        principal_id=_PRINCIPAL,
        surface="obs.command",
        action=spec.action,
        required_scopes=spec.required_scopes,
        idempotency_key=f"idempotency:{request_id}",
        nonce=f"nonce:{request_id}",
        body_sha256=body_sha256,
        request_id=request_id,
        at_ms=now,
        expires_at_ms=now + 600_000,
    )
    assert claim.status is ClaimStatus.NEW
    assert claim.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=claim.claim_ref,
        event=IngressAuditEvent(
            request_id=request_id,
            correlation_id=f"correlation:{request_id}",
            surface="obs.command",
            action=spec.action,
            method="POST",
            normalized_path=f"/v1/host/obs-commands/{command_id}",
            principal_id=_PRINCIPAL,
            tenant_id=_TENANT,
            key_id="operator-task-control-key",
            credential_kind="hmac-sha256",
            credential_ref_digest="sha256:" + "1" * 64,
            requested_scopes=spec.required_scopes,
            required_scopes=spec.required_scopes,
            body_sha256=body_sha256,
            idempotency_digest="sha256:" + hashlib.sha256(
                f"idempotency:{request_id}".encode("utf-8")
            ).hexdigest(),
            nonce_digest="sha256:" + hashlib.sha256(
                f"nonce:{request_id}".encode("utf-8")
            ).hexdigest(),
            transport_key_digest="sha256:" + "2" * 64,
            decision="ALLOW",
            reason_code="SECURE_INGRESS_ALLOWED",
            stage="COMPLETE",
            claim_ref=claim.claim_ref,
            rate_snapshot={"backend": "sqlite", "current_count": 1},
            side_effects_executed=False,
            ts_ms=now,
        ),
    )
    authority = ObsCommandAuthority.bind(
        principal_id=_PRINCIPAL,
        tenant_id=_TENANT,
        request_id=request_id,
    )
    return (
        ObsCommandEnvelope(
            schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
            command_id=command_id,
            action=spec.action,
            required_scopes=spec.required_scopes,
            payload=bind_server_authority(
                normalized,
                spec=spec,
                authority=authority,
            ),
            authority=authority,
            claim_ref=str(claim.claim_ref),
            audit_ref=str(audit_ref),
            body_sha256=body_sha256,
            correlation_id=f"correlation:{request_id}",
        ),
        raw_body,
    )


async def _eventually(
    read: Callable[[], Any],
    *,
    accepted: Callable[[Any], bool],
    timeout_seconds: float = 8.0,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last: Any = None
    while asyncio.get_running_loop().time() < deadline:
        last = read()
        if accepted(last):
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(f"durable state did not converge: {last!r}")


@pytest.mark.asyncio
async def test_task_controls_cross_governed_acceptance_and_cancel_exact_graph(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "governed-operator-task-controls.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    embodiment_runtime = None
    hard_state_owner = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        embodiment_runtime = HostEmbodimentRuntime(
            repository=EmbodimentOutboxRepository(db_path),
            worker_id="ne01-operator-task-control-runtime",
            command_port=_UnusedEmbodimentPort(),
            authority_lease_ms=600_000,
        )
        hard_state_owner = HostHardStateOwner.bootstrap(
            initial_sys_mode="normal",
            endocrine_resolver=lambda **_kwargs: {},
            require_durable=True,
        )
        hard_state_owner.bind_durable_journal(
            SQLiteHardStateJournal(
                db_path,
                runtime_scope_id=embodiment_runtime.runtime_scope_id,
                runtime_authority_provider=(
                    lambda: _runtime_authority_provider(
                        embodiment_runtime
                    )
                ),
            ),
            initialization_event_id="operator-task-control-hard-state",
        )
        mode_service = ModeTransitionService(
            get_hard_state=hard_state_owner.get_hard_state,
            set_sys_mode=hard_state_owner.set_sys_mode,
            history_provider=hard_state_owner.durable_history,
            exact_history_provider=hard_state_owner.reopen_sys_mode_transition,
            ledger=ledger,
        )
        runtime_context = compose_host_runtime_context(
            task_runtime=container,
            ledger=ledger,
            embodiment_runtime=embodiment_runtime,
            environment_observation_provider=(
                runtime_environment_observation_provider(mode_service)
            ),
        )
        action_preview = compose_action_preview_service(
            task_runtime=container,
            ledger=ledger,
            runtime_context_application=runtime_context,
        )
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        intent_engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=token_service,
            registry=build_agent_runtime_skill_registry(),
        )
        ingress_store = SQLiteSecureIngressStore(db_path, rate_limit=100)
        composition = build_governed_operator_composition(
            task_runtime_container=container,
            ledger=ledger,
            token_service=token_service,
            intent_engine=intent_engine,
            runtime_context_application=runtime_context,
            action_preview_service=action_preview,
            mode_transition_service_provider=lambda: mode_service,
            cancellation_authority_verifier=(
                SecureIngressGraphCancellationAuthorityVerifier(
                    ingress_store
                )
            ),
            secure_ingress_authority_resolver=ingress_store,
            runtime_owner_id="ne01-operator-task-control",
            embodiment_runtime_provider=lambda: embodiment_runtime,
        )
        finalizer = _OperatorOutcomeFinalizer()
        runtime_group = GovernedRuntimeGroup(
            {"operator": composition.runtime},
            review_store=container.review_continuation_store,
            review_decision_store=container.review_store,
            command_repository=container.command_repository,
            claim_result_store=ingress_store,
            operator_outcome_finalizer=finalizer,
            review_continuation_worker_id=(
                "ne01-operator-task-control-review-worker"
            ),
            graph_cancellation_operator_enabled=True,
            graph_cancellation_state_reader=container.task_store,
            acceptance_effect_runtime=composition.acceptance_effect_runtime,
            operation_reacquisition_owner_id=(
                "ne01-operator-task-control"
            ),
        )
        composition.review_command_service.bind_wake_continuation(
            runtime_group.wake_review_continuation
        )
        container.bind_runtime(runtime_group)
        await container.activate_runtime()
        registry = build_default_obs_domain_command_registry(
            degradation_capability_port_provider=(
                lambda: runtime_context.composition.guardian
            )
        )
        app_state = SimpleNamespace(
            governed_operator_runtime=composition.runtime,
            task_graph_cancellation_operator_enabled=True,
        )

        async def execute(
            command_id: str,
            payload: dict[str, Any],
            request_id: str,
        ) -> tuple[ObsCommandEnvelope, bytes, Any]:
            envelope, raw_body = _operator_envelope(
                store=ingress_store,
                registry=registry,
                command_id=command_id,
                payload=payload,
                request_id=request_id,
            )
            port = registry.resolve_port(command_id)
            assert port is not None
            with bind_operator_command_runtime(
                app_state,
                raw_body=raw_body,
            ):
                result = await port.execute(envelope)
            return envelope, raw_body, result

        target_envelope, _target_body, target_result = await execute(
            "sys_mode.switch",
            {
                "target_mode": "focus",
                "source": "manual",
                "reason": "create one cancellable governed graph",
            },
            "request-ne01-task-control-target",
        )
        target = dict(target_result.result["output"])
        assert target["status"] == "WAITING_REVIEW"
        assert hard_state_owner.get_hard_state().sys_mode == "normal"

        cancel_envelope, cancel_body, cancel_result = await execute(
            "task_graph.cancel",
            {
                "graph_id": target["graph_id"],
                "graph_revision": target["graph_revision"],
                "reason_code": "OPERATOR_REQUESTED",
            },
            "request-ne01-task-control-cancel",
        )
        cancellation = dict(cancel_result.result["output"])
        assert cancellation["status"] == "WAITING_REVIEW"
        assert cancel_result.side_effects_executed is False
        cancel_review = dict(cancellation["reviews"][0])
        assert container.task_store.get_graph_cancellation_for_graph(
            target["graph_id"],
            target["graph_revision"],
        ) is None

        approve_envelope, approve_body, approve_result = await execute(
            "task_review.approve",
            {
                "review_id": cancel_review["review_id"],
                "subject_ref": cancel_review["subject_ref"],
                "subject_version": cancel_review["subject_version"],
                "resume_phase": cancel_review["resume_phase"],
                "checkpoint_id": cancel_review["checkpoint_id"],
            },
            "request-ne01-task-control-approve",
        )
        approved = dict(approve_result.result["output"])
        public_token = str(approved["resume_token_ref"])
        assert public_token.startswith("review-token://")
        assert "review_resume::" not in json.dumps(
            approved,
            sort_keys=True,
        )
        approved_record = container.review_store.get_review(
            cancel_review["review_id"]
        )
        approved_version = approved_record["status_version"]

        approve_port = registry.resolve_port("task_review.approve")
        assert approve_port is not None
        with bind_operator_command_runtime(
            app_state,
            raw_body=approve_body,
        ):
            approve_replay = await approve_port.execute(approve_envelope)
        assert approve_replay.side_effects_executed is False
        assert container.review_store.get_review(
            cancel_review["review_id"]
        )["status_version"] == approved_version

        _resume_envelope, _resume_body, resume_result = await execute(
            "task_review.resume",
            {
                "review_id": cancel_review["review_id"],
                "resume_token_ref": public_token,
                "subject_ref": cancel_review["subject_ref"],
                "subject_version": cancel_review["subject_version"],
                "resume_phase": cancel_review["resume_phase"],
                "checkpoint_id": cancel_review["checkpoint_id"],
            },
            "request-ne01-task-control-resume",
        )
        resumed = dict(resume_result.result["output"])
        assert resumed["review_status"] == "CONSUMED"
        assert resume_result.side_effects_executed is True

        cancellation_intent = await _eventually(
            lambda: container.task_store.get_graph_cancellation_for_graph(
                target["graph_id"],
                target["graph_revision"],
            ),
            accepted=lambda value: (
                isinstance(value, dict)
                and str(value.get("status") or "") == "FINALIZED"
            ),
        )
        assert cancellation_intent["payload"]["request_id"] == (
            cancel_envelope.authority.request_id
        )
        assert cancellation_intent["payload"]["request_identity"] == (
            cancel_envelope.audit_ref
        )
        assert cancellation_intent["payload"]["source"] == "OPERATOR"
        cancelled_graph = container.task_store.get_graph(
            target["graph_id"],
            target["graph_revision"],
        )
        assert cancelled_graph["status"] == "CANCELLED"
        assert hard_state_owner.get_hard_state().sys_mode == "normal"

        cancel_port = registry.resolve_port("task_graph.cancel")
        assert cancel_port is not None
        with bind_operator_command_runtime(app_state, raw_body=cancel_body):
            cancel_replay = await cancel_port.execute(cancel_envelope)
        cancel_replayed = dict(cancel_replay.result["output"])
        assert cancel_replayed["exact_replay"] is True
        assert cancel_replayed["cancellation_status"] == "FINALIZED"
        assert cancel_replay.side_effects_executed is False

        with bind_operator_command_runtime(app_state, raw_body=approve_body):
            approve_after_resume = await approve_port.execute(approve_envelope)
        historical_approval = dict(approve_after_resume.result["output"])
        assert historical_approval["exact_replay"] is True
        assert historical_approval["review_status"] == "APPROVED"
        assert historical_approval["review_status_version"] == 1
        assert historical_approval["resume_token_ref"] == public_token
        assert approve_after_resume.side_effects_executed is False

        _second_envelope, _second_body, second_result = await execute(
            "sys_mode.switch",
            {
                "target_mode": "maintenance",
                "source": "manual",
                "reason": "create one governed rejection",
            },
            "request-ne01-task-control-reject-target",
        )
        second = dict(second_result.result["output"])
        second_review = dict(second["reviews"][0])
        _reject_envelope, _reject_body, reject_result = await execute(
            "task_review.reject",
            {"review_id": second_review["review_id"]},
            "request-ne01-task-control-reject",
        )
        rejected = dict(reject_result.result["output"])
        assert rejected["review_status"] == "REJECTED"
        await _eventually(
            lambda: container.review_store.get_review(
                second_review["review_id"]
            ),
            accepted=lambda value: (
                isinstance(value, dict)
                and value.get("review", {}).get("status") == "REJECTED"
            ),
        )

        for request_id in (
            "request-ne01-task-control-cancel",
            "request-ne01-task-control-approve",
            "request-ne01-task-control-resume",
            "request-ne01-task-control-reject",
        ):
            record = container.command_repository.get_by_request(
                surface=HostCommandSurface.OPERATOR.value,
                request_id=request_id,
            )
            assert record is not None
            work_order_json = json.dumps(
                record["work_order"].to_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
            assert "review_resume::" not in work_order_json
            assert (
                record["work_order"].metadata["operator_command_id"]
                in {
                    "task_graph.cancel",
                    "task_review.approve",
                    "task_review.resume",
                    "task_review.reject",
                }
            )
        assert b"review_resume::" not in db_path.read_bytes()
        assert finalizer.calls
        assert target_envelope.body_sha256
    finally:
        if container is not None:
            await container.aclose()
        if hard_state_owner is not None:
            hard_state_owner.unbind_durable_journal()
        if embodiment_runtime is not None:
            embodiment_runtime.close()
        ledger.close()
