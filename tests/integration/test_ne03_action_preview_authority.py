from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.persistence.embodiment_outbox_repository import EmbodimentOutboxRepository
from host_brain.runtime.action_preview_authority import (
    ACTION_PREVIEW_DRIFT,
    ACTION_PREVIEW_EXPIRED,
    ActionPreviewAuthorityError,
    DurableActionPreviewAuthority,
)
from host_brain.runtime.acceptance_effect_execution import (
    AcceptanceEffectExecutionProof,
)
from host_brain.runtime.action_preview_resolver import (
    ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA,
    DERIVED_EFFECT_AUTHORITY_SCHEMA,
    EFFECT_DESCRIPTOR_SCHEMA,
    NODE_AUTHORITY_SCHEMA,
    ResolvedActionPreviewAuthority,
)
from host_brain.runtime.runtime_guardian import RuntimeGuardian
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.runtime.context_contracts import WorldFactV2, WorldSnapshotV2
from host_brain.task_runtime.governed_workers import (
    GovernedFrozenNodeWorker,
    GovernedNodeWorkerError,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import AcceptanceEffectDispatcher
from host_brain.task_runtime.acceptance_effects import compile_graph_acceptance_effects
from natsume_contracts.task import HostCommandSurface, build_host_command


NOW = 1_900_000_000_000


class _LostFirstResponseStore:
    def __init__(self, store: RuntimeEventStore) -> None:
        self._store = store
        self._lost = False

    def append_event(self, event):
        result = self._store.append_event(event)
        if not self._lost:
            self._lost = True
            raise RuntimeError("simulated response loss after commit")
        return result

    def get_event_by_id(self, *, event_id: str):
        return self._store.get_event_by_id(event_id=event_id)

    def list_events(self, **kwargs):
        return self._store.list_events(**kwargs)


class _StaticResolver:
    """Test-only structural resolver; production composition never uses it."""

    def resolve(self, **kwargs):
        context = dict(kwargs["authority_context"])
        kind = {
            ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA: "ACCEPTANCE_EFFECT",
            DERIVED_EFFECT_AUTHORITY_SCHEMA: "DERIVED_EFFECT",
        }.get(context.get("schema_version"), "TASK_NODE")
        origin = kwargs.get("origin_receipt") or {}
        descriptors = ()
        if kind == "TASK_NODE" and kwargs["action_kind"].startswith("scheduler."):
            descriptors = (
                {
                    "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
                    "action_kind": "proactive.ui_push",
                    "scope_mode": "EXACT_RUNTIME_VALUE",
                    "max_risk_level": 3,
                    "guardian_capability_id": "proactive_ui_push",
                    "scope_max_length": 512,
                    "args_max_bytes": 65_536,
                    "forbidden_arg_key_fragments": ["authorization", "credential", "secret", "token"],
                },
                {
                    "schema_version": EFFECT_DESCRIPTOR_SCHEMA,
                    "action_kind": "life-delivery.proactive.gateway",
                    "scope_mode": "EXACT_RUNTIME_VALUE",
                    "max_risk_level": 3,
                    "guardian_capability_id": "life_delivery_proactive_gateway",
                    "scope_max_length": 512,
                    "args_max_bytes": 65_536,
                    "forbidden_arg_key_fragments": ["authorization", "credential", "secret", "token"],
                },
            )
        binding = {
            "schema_version": "Natsume.action_preview.authority_binding.v1",
            "authority_kind": kind,
            "context": context,
            "node_grant_ref": kwargs["node_grant_ref"],
            "origin_receipt_digest": str(origin.get("receipt_digest") or ""),
        }
        return ResolvedActionPreviewAuthority(
            authority_kind=kind,
            action_kind=kwargs["action_kind"],
            action_scope=kwargs["action_scope"],
            risk_level=kwargs["risk_level"],
            guardian_capability_id=kwargs["guardian_capability_id"],
            context_pack_digest=kwargs["context_pack_digest"],
            scene_identity=kwargs["scene_identity"],
            task_graph_id=kwargs["task_graph_id"],
            graph_revision=kwargs["graph_revision"],
            task_node_id=kwargs["task_node_id"],
            parent_governor_grant_ref=kwargs["parent_governor_grant_ref"],
            node_grant_ref=kwargs["node_grant_ref"],
            node_grant=kwargs["node_grant"],
            expires_at_ms=NOW + 60_000,
            binding=binding,
            effect_descriptors=descriptors,
            origin_receipt_ref=str(context.get("origin_receipt_ref") or ""),
        )


def _harness(tmp_path: Path, *, response_loss: bool = False):
    db_path = tmp_path / "preview.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne03-action-preview",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    scope = "runtime-scope-action-preview"
    repository = EmbodimentOutboxRepository(db_path, runtime_scope_id=scope)
    lease = repository.acquire_runtime_lease(
        worker_id="preview-owner",
        lease_ms=600_000,
        now_ms=NOW,
    )
    assert lease is not None

    def authority_provider():
        value = repository.validate_runtime_lease(
            worker_id=str(lease["owner_id"]),
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=NOW,
        )
        assert value is not None
        return {**value, "runtime_scope_id": scope}

    event_store = RuntimeEventStore(db_path, clock=lambda: NOW)
    guardian = RuntimeGuardian(
        event_store=event_store,
        runtime_scope_id=scope,
        runtime_authority_provider=authority_provider,
        clock=lambda: NOW,
    )
    store = _LostFirstResponseStore(event_store) if response_loss else event_store
    preview = DurableActionPreviewAuthority(
        runtime_event_store=store,
        guardian=guardian,
        authority_resolver=_StaticResolver(),
        clock=lambda: NOW,
    )
    return preview, guardian, event_store


def _issue(preview: DurableActionPreviewAuthority):
    return preview.issue(
        action_kind="desktop.execute",
        action_scope="workspace-alpha",
        risk_level=4,
        action_args={"command_digest": "sha256:" + "a" * 64},
        context_pack_digest="sha256:" + "b" * 64,
        scene_identity={
            "schema": "natsume.scene-identity.v2",
            "workspace_id": "workspace-alpha",
            "subject_id": "subject-alpha",
            "scene_id": "release-maintenance",
            "revision": 7,
            "decision_digest": "sha256:" + "c" * 64,
        },
        task_graph_id="graph-alpha",
        graph_revision=3,
        task_node_id="node-alpha",
        parent_governor_grant_ref="governor-grant://parent-alpha",
        node_grant_ref="governor-grant://node-alpha",
        node_grant={
            "grant_ref": "governor-grant://node-alpha",
            "graph_id": "graph-alpha",
            "graph_revision": 3,
            "node_id": "node-alpha",
            "route": "skill",
            "target_id": "desktop.execute",
        },
        guardian_capability_id="desktop_execution",
        authority_context={
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": "skill.execute",
        },
    )


def _require(preview: DurableActionPreviewAuthority, event_id: str, **overrides):
    values = {
        "event_id": event_id,
        "action_kind": "desktop.execute",
        "action_scope": "workspace-alpha",
        "risk_level": 4,
        "action_args": {"command_digest": "sha256:" + "a" * 64},
        "context_pack_digest": "sha256:" + "b" * 64,
        "scene_identity": {
            "schema": "natsume.scene-identity.v2",
            "workspace_id": "workspace-alpha",
            "subject_id": "subject-alpha",
            "scene_id": "release-maintenance",
            "revision": 7,
            "decision_digest": "sha256:" + "c" * 64,
        },
        "task_graph_id": "graph-alpha",
        "graph_revision": 3,
        "task_node_id": "node-alpha",
        "parent_governor_grant_ref": "governor-grant://parent-alpha",
        "node_grant_ref": "governor-grant://node-alpha",
        "node_grant": {
            "grant_ref": "governor-grant://node-alpha",
            "graph_id": "graph-alpha",
            "graph_revision": 3,
            "node_id": "node-alpha",
            "route": "skill",
            "target_id": "desktop.execute",
        },
        "authority_context": {
            "schema_version": NODE_AUTHORITY_SCHEMA,
            "purpose": "skill.execute",
        },
    }
    values.update(overrides)
    return preview.require_execution(**values)


def test_preview_receipt_reopens_exact_action_and_rejects_tamper(
    tmp_path: Path,
) -> None:
    preview, _guardian, _store = _harness(tmp_path)
    issued = _issue(preview)
    receipt = _require(preview, issued["event_id"])
    assert receipt.receipt_ref == issued["receipt_ref"]
    assert receipt.payload["authority_granted"] is False
    assert "command_digest" not in receipt.to_dict()
    assert receipt.payload["expires_at_ms"] - receipt.payload["issued_at_ms"] <= 30_000

    not_yet_active = DurableActionPreviewAuthority(
        runtime_event_store=_store,
        guardian=_guardian,
        authority_resolver=_StaticResolver(),
        clock=lambda: NOW - 1,
    )
    with pytest.raises(ActionPreviewAuthorityError) as future:
        _require(not_yet_active, issued["event_id"])
    assert future.value.reason_code == ACTION_PREVIEW_DRIFT

    with pytest.raises(ActionPreviewAuthorityError) as raised:
        _require(
            preview,
            issued["event_id"],
            action_args={"command_digest": "sha256:" + "d" * 64},
        )
    assert raised.value.reason_code == ACTION_PREVIEW_DRIFT


def test_preview_duplicate_and_response_loss_reopen_one_runtime_event(
    tmp_path: Path,
) -> None:
    preview, guardian, store = _harness(tmp_path, response_loss=True)
    with pytest.raises(RuntimeError, match="response loss"):
        _issue(preview)
    replay = _issue(preview)
    rows = store.list_events(event_type="ActionPreviewReceiptIssued")
    assert len(rows) == 1
    assert rows[0]["event_id"] == replay["event_id"]
    _require(preview, replay["event_id"])
    restarted = DurableActionPreviewAuthority(
        runtime_event_store=RuntimeEventStore(store.db_path, clock=lambda: NOW),
        guardian=guardian,
        authority_resolver=_StaticResolver(),
        clock=lambda: NOW,
    )
    _require(restarted, replay["event_id"])


def test_preview_rejects_guardian_revision_drift_and_expiry(tmp_path: Path) -> None:
    preview, guardian, _store = _harness(tmp_path)
    issued = _issue(preview)
    guardian.record_slo_breach(
        slo_id="host-p95",
        observed_value=101.0,
        threshold=100.0,
        evidence_ref="slo-evidence://action-preview",
        observed_at_ms=NOW,
    )
    with pytest.raises(ActionPreviewAuthorityError) as raised:
        _require(preview, issued["event_id"])
    assert raised.value.reason_code == ACTION_PREVIEW_DRIFT

    expired = DurableActionPreviewAuthority(
        runtime_event_store=_store,
        guardian=guardian,
        authority_resolver=_StaticResolver(),
        clock=lambda: NOW + 120_000,
    )
    with pytest.raises(ActionPreviewAuthorityError) as expired_error:
        _require(expired, issued["event_id"])
    assert expired_error.value.reason_code == ACTION_PREVIEW_EXPIRED


@pytest.mark.asyncio
async def test_real_delegate_requires_durable_preview_before_worker_projection(
    tmp_path: Path,
) -> None:
    _preview, guardian, store = _harness(tmp_path)
    service = ActionPreviewService(
        runtime_event_store=store,
        guardian=guardian,
        authority_resolver=_StaticResolver(),
        clock=lambda: NOW,
    )
    fact = WorldFactV2(
        workspace_id="workspace-delegate",
        subject_id="subject-delegate",
        key="environment.activity",
        value="WORKING",
        source_ref="runtime-observation://delegate",
        confidence=1.0,
        observed_at_ms=NOW - 1_000,
        expires_at_ms=NOW + 60_000,
        revision=1,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    order = WorkOrderV3(
        work_order_id="wo-preview-delegate",
        domain="research",
        run_mode="worker",
        objective="read-only authorized security analysis",
        input_pack={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "workspace_id": fact.workspace_id,
            "subject_id": fact.subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "scene_id": "security-analysis",
                "revision": 1,
                "decision_digest": "sha256:" + "c" * 64,
            },
        },
        metadata={
            "planning_lineage": {
                "graph_id": "graph-preview-delegate",
                "root_work_order_id": "wo-preview-delegate",
                "revision": 1,
                "node_bindings": [
                    {
                        "node_id": "node-preview-delegate",
                        "worker_route": "delegate",
                        "target_id": "pentest_agent",
                        "objective": "read-only authorized security analysis",
                    }
                ],
            }
        },
        root_work_order_id="wo-preview-delegate",
        task_graph_id="graph-preview-delegate",
        task_node_id="node-preview-delegate",
        graph_revision=1,
        budget_policy=BudgetPolicy(
            scope="preview-delegate",
            limits=BudgetAmount(
                token_count=128,
                time_ms=2_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=5_000,
            ),
        ),
        business_idempotency_scope="preview-delegate",
        operation_fingerprint="sha256:" + "d" * 64,
        approval_ref="governor-grant://parent-preview-delegate",
        review_ref="review-waiver://preview-delegate",
    )
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-preview-delegate",
        session_id="session-preview-delegate",
        principal_id="principal-preview-delegate",
        input_ref="input://sha256/" + "e" * 64,
        input_sha256="sha256:" + "e" * 64,
        governor_grant_ref=order.approval_ref,
        root_work_order_id=order.root_work_order_id,
        graph_id=order.task_graph_id,
        graph_revision=1,
        route_type="delegate",
        target_id="pentest_agent",
        business_scope=order.business_idempotency_scope,
        operation_fingerprint=order.operation_fingerprint,
        created_at_ms=NOW - 100,
    )

    class GrantVerifier:
        def verify(self, **kwargs):
            return {
                **kwargs,
                "decision_id": "decision-preview-delegate",
                "intent_id": "intent-preview-delegate",
                "expires_at_ms": NOW + 60_000,
                "nonce": "nonce-preview-delegate",
                "action_kind": "delegate.dispatch",
                "token_subject": "delegate:pentest_agent",
                "review_continuation_authority": None,
            }

    class ProjectionStore:
        def __init__(self) -> None:
            self.rows = {}

        def stage(self, *, projection_id, projection):
            self.rows[projection_id] = dict(projection)
            return dict(projection)

    class Executor:
        def __init__(self) -> None:
            self.called = False

        async def execute_stream(self, _frozen):
            self.called = True
            yield {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": "read-only delegate result",
                "route_type": "delegate",
                "target_id": "pentest_agent",
            }

    projections = ProjectionStore()
    executor = Executor()
    missing_preview_executor = Executor()
    missing_preview_worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=missing_preview_executor,
        node_grants=GrantVerifier(),
        projections=ProjectionStore(),
    )
    worker = GovernedFrozenNodeWorker(
        route_kind="delegate",
        executor=executor,
        node_grants=GrantVerifier(),
        projections=projections,
        action_preview_service=service,
    )
    context = {
        "command_id": command.command_id,
        "work_order_id": order.work_order_id,
        "graph_id": order.task_graph_id,
        "graph_revision": 1,
        "node_id": order.task_node_id,
        "attempt_id": "attempt-preview-delegate",
        "lease_id": "lease-preview-delegate",
        "lease_owner_id": "worker-preview-delegate",
        "fencing_token": 1,
        "reservation_id": "operation-reservation://preview-delegate",
        "business_scope": order.business_idempotency_scope,
        "operation_fingerprint": order.operation_fingerprint,
        "node_risk_level": 3,
        "worker_route": "delegate:pentest_agent",
        "node_input_refs": [],
        "node_input_artifacts": [],
        "node_grant_ref": "governor-grant://child-preview-delegate",
        "budget": {
            "budget_id": "budget://preview-delegate",
            "status": "ACTIVE",
            "status_version": 1,
            "remaining": {
                "token_count": 128,
                "time_ms": 2_000,
                "retry_count": 1,
                "model_cost_micros": 5_000,
            },
        },
    }
    with pytest.raises(GovernedNodeWorkerError, match="ActionPreview authority"):
        _ = [
            event
            async for event in missing_preview_worker.execute_stream(
                command=command,
                work_order=order,
                execution_context=context,
            )
        ]
    assert missing_preview_executor.called is False
    events = [
        event
        async for event in worker.execute_stream(
            command=command,
            work_order=order,
            execution_context=context,
        )
    ]
    assert executor.called is True
    assert len(events) == 1
    audit = events[0]["audit_refs"]
    assert audit["action_preview_receipt_ref"].startswith("action-preview://sha256/")
    assert audit["action_preview_event_id"].startswith("runtimeevent://sha256/")
    assert len(store.list_events(event_type="ActionPreviewReceiptIssued")) == 1
    effects = compile_graph_acceptance_effects(
        command=command,
        work_order=order,
        node_id=order.task_node_id,
        attempt_id=context["attempt_id"],
        worker_commit=events[0],
    )
    assert effects
    for effect in effects:
        preview_context = effect.payload["_action_preview_context"]
        assert preview_context["node_grant_ref"] == context["node_grant_ref"]
        assert preview_context["task_node_id"] == order.task_node_id


def test_action_preview_service_rejects_missing_app_local_guardian(tmp_path: Path) -> None:
    _preview, _guardian, store = _harness(tmp_path)
    with pytest.raises(TypeError, match="app-local RuntimeGuardian"):
        ActionPreviewService(
            runtime_event_store=store,
            guardian=None,
            authority_resolver=_StaticResolver(),
        )


@pytest.mark.asyncio
async def test_accepted_surface_dispatcher_uses_live_effect_proof_without_preview(
    tmp_path: Path,
) -> None:
    _preview, guardian, event_store = _harness(tmp_path)
    service = ActionPreviewService(
        runtime_event_store=event_store,
        guardian=guardian,
        authority_resolver=_StaticResolver(),
        clock=lambda: NOW,
    )
    scene = {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": "workspace-alpha",
        "subject_id": "subject-alpha",
        "scene_id": "normal-chat",
        "revision": 4,
        "decision_digest": "sha256:" + "f" * 64,
    }
    effect = {
        "effect_id": "acceptance-effect://preview/delivery",
        "graph_id": "graph-alpha",
        "graph_revision": 3,
        "node_id": "node-alpha",
        "attempt_id": "attempt-alpha",
        "effect_type": "surface.reply_delivery",
        "status": "CLAIMED",
        "status_version": 2,
        "attempt_count": 1,
        "lease_owner": "effect-worker",
        "lease_expires_ts_ms": NOW + 60_000,
        "fencing_token": 8,
        "created_ts_ms": NOW - 100,
        "updated_ts_ms": NOW - 1,
        "payload_sha256": "sha256:" + "1" * 64,
        "payload": {
            "request_id": "request-alpha",
            "reply_target": "conversation-alpha",
            "narrative": "accepted reply",
            "_action_preview_context": {
                "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
                "context_pack_digest": "sha256:" + "2" * 64,
                "scene_identity": scene,
                "task_graph_id": "graph-alpha",
                "graph_revision": 3,
                "task_node_id": "node-alpha",
                "parent_governor_grant_ref": "governor-grant://parent-alpha",
                "node_grant_ref": "governor-grant://node-alpha",
                "node_grant_digest": "sha256:" + "3" * 64,
            },
        },
    }

    class EffectStore:
        def list_acceptance_effect_intents(self, **_kwargs):
            return []

        def claim_acceptance_effect_intent(self, **_kwargs):
            return dict(effect)

        def commit_acceptance_effect_intent(self, _effect_id, **kwargs):
            return {**effect, **kwargs, "status": "COMMITTED"}

        def fail_acceptance_effect_intent(self, *_args, **_kwargs):
            raise AssertionError("valid preview must not fail")

        def reschedule_acceptance_effect_intent(self, *_args, **_kwargs):
            raise AssertionError("valid preview must not reschedule")

    seen = []

    def handler(payload):
        seen.append(dict(payload))
        assert "_action_preview_context" not in payload
        proof = payload.pop("_acceptance_effect_execution_proof")
        assert isinstance(proof, AcceptanceEffectExecutionProof)
        assert proof.effect_id == effect["effect_id"]
        assert proof.action_args_dict() == payload
        return {"receipt_ref": "gateway-receipt://delivery-alpha"}

    dispatcher = AcceptanceEffectDispatcher(
        task_store=EffectStore(),
        handlers={"surface.reply_delivery": handler},
        owner_id="effect-worker",
        action_preview_service=service,
    )
    committed = await dispatcher.dispatch_effect(effect["effect_id"])
    assert committed is not None and committed["status"] == "COMMITTED"
    assert len(seen) == 1
    rows = event_store.list_events(event_type="ActionPreviewReceiptIssued")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_legacy_acceptance_effect_is_quarantined_before_handler() -> None:
    effect = {
        "effect_id": "acceptance-effect://preview/legacy",
        "graph_id": "graph-legacy",
        "graph_revision": 1,
        "node_id": "node-legacy",
        "attempt_id": "attempt-legacy",
        "effect_type": "surface.reply_delivery",
        "status": "CLAIMED",
        "status_version": 2,
        "attempt_count": 1,
        "lease_owner": "effect-worker",
        "lease_expires_ts_ms": NOW + 60_000,
        "fencing_token": 3,
        "payload": {
            "request_id": "request-legacy",
            "reply_target": "conversation-legacy",
            "narrative": "must not send",
        },
    }
    called = False

    class _Store:
        def list_acceptance_effect_intents(self, **_kwargs):
            return []

        def claim_acceptance_effect_intent(self, **_kwargs):
            return dict(effect)

        def commit_acceptance_effect_intent(self, *_args, **_kwargs):
            raise AssertionError("legacy effect must never commit")

        def fail_acceptance_effect_intent(self, _effect_id, **kwargs):
            return {**effect, **kwargs, "status": "FAILED"}

        def reschedule_acceptance_effect_intent(self, *_args, **_kwargs):
            raise AssertionError("legacy effect must be quarantined, not retried")

    def _handler(_payload):
        nonlocal called
        called = True
        return {"receipt_ref": "forbidden://legacy"}

    dispatcher = AcceptanceEffectDispatcher(
        task_store=_Store(),
        handlers={"surface.reply_delivery": _handler},
        owner_id="effect-worker",
        action_preview_service=None,
    )
    result = await dispatcher.dispatch_effect(effect["effect_id"])
    assert result is not None and result["status"] == "FAILED"
    assert result["reason_code"] == "ACTION_PREVIEW_INVALID"
    assert called is False
