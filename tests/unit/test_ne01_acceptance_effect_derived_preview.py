from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.runtime import action_preview_resolver as resolver_module
from host_brain.runtime.action_preview_authority import (
    ACTION_PREVIEW_BLOCKED,
    ACTION_PREVIEW_DRIFT,
    ActionPreviewAuthorityError,
    SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2,
    require_scheduler_derived_action,
)
from host_brain.runtime.action_preview_resolver import (
    ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA,
    DERIVED_EFFECT_AUTHORITY_SCHEMA,
    ProductionActionPreviewAuthorityResolver,
    SCHEDULER_OPERATION_EFFECT_TYPE,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)


NOW = 1_900_000_000_000


def _scene() -> dict[str, Any]:
    return {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": "workspace-scheduler-effect",
        "subject_id": "subject-scheduler-effect",
        "scene_id": "normal-chat",
        "revision": 2,
        "decision_digest": "sha256:" + "2" * 64,
    }


def _origin_boundary() -> dict[str, Any]:
    return {
        "action_kind": "acceptance-effect.scheduler.operation.execute",
        "action_scope": "acceptance-effect://scheduler/operation",
        "risk_level": 3,
        "action_args": {"operation": {"kind": "life_tick"}},
        "context_pack_digest": "sha256:" + "1" * 64,
        "scene_identity": _scene(),
        "task_graph_id": "graph-scheduler-effect",
        "graph_revision": 2,
        "task_node_id": "node-scheduler-effect",
        "parent_governor_grant_ref": "governor-grant://scheduler/parent",
        "node_grant_ref": "governor-grant://scheduler/node",
        "node_grant": {
            "grant_ref": "governor-grant://scheduler/node",
            "binding_digest": "sha256:" + "3" * 64,
        },
        "authority_context": {
            "schema_version": ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA,
            "effect_id": "acceptance-effect://scheduler/operation",
            "lease_owner_id": "scheduler-effect-owner",
            "fencing_token": 7,
        },
    }


def test_scheduler_v2_helper_reopens_acceptance_effect_before_child_and_terminal() -> None:
    boundary = _origin_boundary()
    origin_event_id = "runtimeevent://scheduler/operation"
    origin_receipt_ref = "action-preview://scheduler/operation"

    class Service:
        def __init__(self) -> None:
            self.required: list[tuple[str, dict[str, Any]]] = []
            self.issued: dict[str, Any] | None = None

        def require_execution(self, *, event_id: str, **kwargs: Any) -> Any:
            self.required.append((event_id, deepcopy(kwargs)))
            if event_id == origin_event_id:
                return SimpleNamespace(
                    receipt_ref=origin_receipt_ref,
                    payload={"authority_kind": "ACCEPTANCE_EFFECT"},
                )
            return SimpleNamespace(
                receipt_ref="action-preview://scheduler/child",
                payload={"authority_kind": "DERIVED_EFFECT"},
            )

        def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
            self.issued = deepcopy(kwargs)
            return {
                "event_id": "runtimeevent://scheduler/child",
                "receipt_ref": "action-preview://scheduler/child",
            }

        @staticmethod
        def now_ms() -> int:
            return NOW

    service = Service()
    context = {
        "schema_version": SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2,
        "origin_authority_kind": "ACCEPTANCE_EFFECT",
        "origin_event_id": origin_event_id,
        "origin_receipt_ref": origin_receipt_ref,
        "origin_boundary": boundary,
    }
    result = require_scheduler_derived_action(
        service=service,
        preview_context=context,
        action_kind="proactive.ui_push",
        action_scope="request-scheduler-child",
        risk_level=3,
        action_args={
            "type": "proactive_talk",
            "content": "exact",
            "event_id": "event-scheduler-child",
            "ts_ms": NOW,
            "request_id": "request-scheduler-child",
        },
        guardian_capability_id="proactive_ui_push",
    )

    assert service.required[0] == (origin_event_id, boundary)
    assert service.required[1][0] == "runtimeevent://scheduler/child"
    assert service.issued is not None
    derived_context = service.issued["authority_context"]
    assert derived_context["schema_version"] == DERIVED_EFFECT_AUTHORITY_SCHEMA
    assert derived_context["origin_boundary"] == boundary
    assert result["_terminal_execution_boundary"]["authority_context"] == derived_context


def test_scheduler_v2_helper_rejects_task_node_origin_kind() -> None:
    boundary = _origin_boundary()

    class Service:
        @staticmethod
        def require_execution(**_kwargs: Any) -> Any:
            return SimpleNamespace(
                receipt_ref="action-preview://scheduler/operation",
                payload={"authority_kind": "TASK_NODE"},
            )

    with pytest.raises(ActionPreviewAuthorityError) as blocked:
        require_scheduler_derived_action(
            service=Service(),
            preview_context={
                "schema_version": SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2,
                "origin_authority_kind": "ACCEPTANCE_EFFECT",
                "origin_event_id": "runtimeevent://scheduler/operation",
                "origin_receipt_ref": "action-preview://scheduler/operation",
                "origin_boundary": boundary,
            },
            action_kind="proactive.ui_push",
            action_scope="request-scheduler-child",
            risk_level=3,
            action_args={"request_id": "request-scheduler-child"},
            guardian_capability_id="proactive_ui_push",
            now_ms=NOW,
        )
    assert blocked.value.reason_code == ACTION_PREVIEW_DRIFT


class _DispatcherStore:
    def __init__(self, effect: dict[str, Any]) -> None:
        self.effect = deepcopy(effect)
        self.failed: dict[str, Any] | None = None

    def list_acceptance_effect_intents(self, **_kwargs: Any) -> list[Any]:
        return []

    def claim_acceptance_effect_intent(self, **_kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.effect)

    @staticmethod
    def get_node(*_args: Any) -> dict[str, Any]:
        return {"status": "ACCEPTED", "node": {"risk_level": 3}}

    def commit_acceptance_effect_intent(
        self,
        _effect_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {**deepcopy(self.effect), **kwargs, "status": "COMMITTED"}

    def fail_acceptance_effect_intent(
        self,
        _effect_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.failed = {**deepcopy(self.effect), **kwargs, "status": "FAILED"}
        return deepcopy(self.failed)

    def reschedule_acceptance_effect_intent(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("typed Preview failures must fail closed")


class _DispatcherPreviewService:
    def __init__(self) -> None:
        self.issued: dict[str, Any] | None = None
        self.required: list[dict[str, Any]] = []

    def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
        self.issued = deepcopy(kwargs)
        return {
            "event_id": "runtimeevent://scheduler/acceptance",
            "receipt_ref": "action-preview://scheduler/acceptance",
            "receipt": {
                "authority_kind": "ACCEPTANCE_EFFECT",
                "expires_at_ms": NOW + 20_000,
            },
        }

    def require_execution(self, **kwargs: Any) -> Any:
        self.required.append(deepcopy(kwargs))
        return SimpleNamespace()


def _dispatcher_effect(*, collision: bool = False) -> dict[str, Any]:
    operation: dict[str, Any] = {"kind": "life_tick"}
    if collision:
        operation["_action_preview_context"] = {"forged": True}
    return {
        "effect_id": "acceptance-effect://scheduler/operation",
        "graph_id": "graph-scheduler-effect",
        "graph_revision": 2,
        "node_id": "node-scheduler-effect",
        "attempt_id": "attempt-scheduler-effect",
        "effect_type": SCHEDULER_OPERATION_EFFECT_TYPE,
        "status": "CLAIMED",
        "status_version": 2,
        "attempt_count": 1,
        "lease_owner": "scheduler-effect-owner",
        "lease_expires_ts_ms": NOW + 60_000,
        "fencing_token": 7,
        "payload": {
            "operation": operation,
            "_action_preview_context": {
                "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
                "context_pack_digest": "sha256:" + "1" * 64,
                "scene_identity": _scene(),
                "task_graph_id": "graph-scheduler-effect",
                "graph_revision": 2,
                "task_node_id": "node-scheduler-effect",
                "parent_governor_grant_ref": "governor-grant://scheduler/parent",
                "node_grant_ref": "governor-grant://scheduler/node",
                "node_grant_digest": "sha256:" + "3" * 64,
            },
        },
    }


@pytest.mark.asyncio
async def test_dispatcher_injects_private_v2_only_after_scheduler_effect_claim() -> None:
    effect = _dispatcher_effect()
    store = _DispatcherStore(effect)
    service = _DispatcherPreviewService()
    captured: dict[str, Any] = {}

    def handler(payload: Any) -> dict[str, Any]:
        captured.update(deepcopy(dict(payload)))
        return {"receipt_ref": "scheduler-operation://receipt/exact"}

    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={SCHEDULER_OPERATION_EFFECT_TYPE: handler},
        owner_id="scheduler-effect-owner",
        action_preview_service=service,
    )
    result = await dispatcher.dispatch_effect(effect["effect_id"])

    assert result is not None and result["status"] == "COMMITTED"
    private = captured["_action_preview_context"]
    reopened_boundary = {
        key: value
        for key, value in service.required[0].items()
        if key != "event_id"
    }
    assert private == {
        "schema_version": SCHEDULER_ACTION_PREVIEW_CONTEXT_SCHEMA_V2,
        "origin_authority_kind": "ACCEPTANCE_EFFECT",
        "origin_event_id": "runtimeevent://scheduler/acceptance",
        "origin_receipt_ref": "action-preview://scheduler/acceptance",
        "origin_boundary": reopened_boundary,
    }
    assert private["origin_boundary"]["risk_level"] == 3
    assert "_action_preview_context" not in private["origin_boundary"]["action_args"]
    assert effect["payload"]["_action_preview_context"]["schema_version"].endswith(
        "action_preview_context.v1"
    )


@pytest.mark.asyncio
async def test_dispatcher_rejects_durable_private_context_collision() -> None:
    effect = _dispatcher_effect(collision=True)
    store = _DispatcherStore(effect)
    service = _DispatcherPreviewService()
    called = False

    def handler(_payload: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"receipt_ref": "scheduler-operation://receipt/forbidden"}

    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={SCHEDULER_OPERATION_EFFECT_TYPE: handler},
        owner_id="scheduler-effect-owner",
        action_preview_service=service,
    )
    result = await dispatcher.dispatch_effect(effect["effect_id"])

    assert result is not None and result["status"] == "FAILED"
    assert result["reason_code"] == "ACTION_PREVIEW_INVALID"
    assert called is False


@pytest.mark.asyncio
async def test_dispatcher_reclaimed_scheduler_claim_is_reconciliation_only() -> None:
    effect = _dispatcher_effect()
    effect["claim_reconciliation_required"] = True
    effect["claim_previous_status"] = "CLAIMED"
    store = _DispatcherStore(effect)
    service = _DispatcherPreviewService()

    class Handler:
        def __init__(self) -> None:
            self.calls = 0
            self.reconciliations = 0

        def __call__(self, _payload: Any) -> dict[str, Any]:
            self.calls += 1
            raise AssertionError("reclaimed unknown effect must not be invoked")

        def reconcile(self, payload: Any) -> dict[str, Any]:
            self.reconciliations += 1
            assert payload["_action_preview_context"]["origin_authority_kind"] == (
                "ACCEPTANCE_EFFECT"
            )
            return {"receipt_ref": "scheduler-operation://receipt/reconciled"}

    handler = Handler()
    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={SCHEDULER_OPERATION_EFFECT_TYPE: handler},
        owner_id="scheduler-effect-owner",
        action_preview_service=service,
    )

    result = await dispatcher.dispatch_effect(effect["effect_id"])

    assert result is not None and result["status"] == "COMMITTED"
    assert handler.calls == 0
    assert handler.reconciliations == 1


def _resolver_harness(monkeypatch: pytest.MonkeyPatch):
    scene = _scene()
    preview_context = {
        "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
        "context_pack_digest": "sha256:" + "1" * 64,
        "scene_identity": scene,
        "task_graph_id": "graph-scheduler-effect",
        "graph_revision": 2,
        "task_node_id": "node-scheduler-effect",
        "parent_governor_grant_ref": "governor-grant://scheduler/parent",
        "node_grant_ref": "governor-grant://scheduler/node",
        "node_grant_digest": "sha256:" + "3" * 64,
    }
    effect = {
        "effect_id": "acceptance-effect://scheduler/operation",
        "graph_id": "graph-scheduler-effect",
        "graph_revision": 2,
        "node_id": "node-scheduler-effect",
        "attempt_id": "attempt-scheduler-effect",
        "commit_scope": "GRAPH",
        "effect_type": SCHEDULER_OPERATION_EFFECT_TYPE,
        "status": "CLAIMED",
        "status_version": 2,
        "attempt_count": 1,
        "lease_owner": "scheduler-effect-owner",
        "lease_expires_ts_ms": NOW + 60_000,
        "fencing_token": 7,
        "payload_sha256": "sha256:" + "4" * 64,
        "payload": {
            "operation": {"kind": "life_tick"},
            "_action_preview_context": preview_context,
        },
    }
    command = SimpleNamespace(
        command_id="host-command://scheduler/operation",
        surface="scheduler",
        route_type="delegate",
        target_id="life_command",
        governor_grant_ref="governor-grant://scheduler/parent",
    )
    work_order = SimpleNamespace(
        input_pack={"scene_identity_v2": scene},
        metadata={
            "planning_lineage": {
                "node_bindings": [
                    {
                        "node_id": "node-scheduler-effect",
                        "worker_route": "delegate",
                        "target_id": "life_command",
                    }
                ]
            }
        },
    )

    class Commands:
        @staticmethod
        def get_by_graph(**_kwargs: Any) -> dict[str, Any]:
            return {
                "command": command,
                "work_order": work_order,
                "command_digest": "sha256:" + "5" * 64,
                "work_order_digest": "sha256:" + "6" * 64,
                "graph_digest": "sha256:" + "7" * 64,
            }

    class Tasks:
        @staticmethod
        def get_graph(*_args: Any) -> dict[str, Any]:
            return {"status": "COMPLETED"}

        @staticmethod
        def get_node(*_args: Any) -> dict[str, Any]:
            return {"status": "ACCEPTED", "node": {"risk_level": 3}}

        @staticmethod
        def get_attempt(_attempt_id: str) -> dict[str, Any]:
            return {
                "graph_id": "graph-scheduler-effect",
                "graph_revision": 2,
                "node_id": "node-scheduler-effect",
                "status": "SUCCEEDED",
            }

        @staticmethod
        def get_active_lease(_attempt_id: str) -> None:
            return None

        @staticmethod
        def get_acceptance_effect_intent(_effect_id: str) -> dict[str, Any]:
            return deepcopy(effect)

        @staticmethod
        def get_node_artifact(*_args: Any) -> dict[str, Any]:
            return {
                "attempt_id": "attempt-scheduler-effect",
                "artifact_ref": "artifact://scheduler/operation",
                "payload_sha256": "sha256:" + "8" * 64,
            }

    monkeypatch.setattr(
        resolver_module,
        "context_pack_digest_from_work_order",
        lambda _work_order: "sha256:" + "1" * 64,
    )
    resolver = ProductionActionPreviewAuthorityResolver(
        command_repository=Commands(),
        task_store=Tasks(),
        node_grant_verifier=SimpleNamespace(verify=lambda **_kwargs: None),
        runtime_context_application=SimpleNamespace(require_frozen=lambda _pack: None),
    )
    values = {
        "authority_context": {
            "schema_version": ACCEPTANCE_EFFECT_AUTHORITY_SCHEMA,
            "effect_id": effect["effect_id"],
            "lease_owner_id": effect["lease_owner"],
            "fencing_token": effect["fencing_token"],
        },
        "action_kind": "acceptance-effect.scheduler.operation.execute",
        "action_scope": effect["effect_id"],
        "action_args": {"operation": {"kind": "life_tick"}},
        "risk_level": 3,
        "guardian_capability_id": "acceptance_effect_scheduler_operation_execute",
        "context_pack_digest": "sha256:" + "1" * 64,
        "scene_identity": scene,
        "task_graph_id": effect["graph_id"],
        "graph_revision": effect["graph_revision"],
        "task_node_id": effect["node_id"],
        "parent_governor_grant_ref": "governor-grant://scheduler/parent",
        "node_grant_ref": "governor-grant://scheduler/node",
        "node_grant": {
            "grant_ref": "governor-grant://scheduler/node",
            "binding_digest": "sha256:" + "3" * 64,
        },
        "now_ms": NOW,
    }
    return resolver, values, effect, command, work_order


def test_resolver_allows_only_canonical_life_command_acceptance_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver, values, effect, command, work_order = _resolver_harness(monkeypatch)
    origin = resolver.resolve(**values)
    assert origin.authority_kind == "ACCEPTANCE_EFFECT"
    assert {
        str(item["action_kind"]) for item in origin.effect_descriptors
    } >= {"proactive.ui_push", "life-delivery.proactive.gateway"}

    origin_boundary = {
        key: deepcopy(value)
        for key, value in values.items()
        if key not in {"guardian_capability_id", "now_ms"}
    }
    origin_receipt = {
        "authority_kind": origin.authority_kind,
        "action_kind": origin.action_kind,
        "action_scope": origin.action_scope,
        "risk_level": origin.risk_level,
        "action_args_digest": resolver_module._digest(origin_boundary["action_args"]),
        "guardian_capability_id": origin.guardian_capability_id,
        "context_pack_digest": origin.context_pack_digest,
        "scene_identity": dict(origin.scene_identity),
        "task_graph_id": origin.task_graph_id,
        "graph_revision": origin.graph_revision,
        "task_node_id": origin.task_node_id,
        "parent_governor_grant_ref": origin.parent_governor_grant_ref,
        "node_grant_ref": origin.node_grant_ref,
        "node_grant_digest": "sha256:" + "3" * 64,
        "authority_binding_digest": origin.binding_digest,
        "expires_at_ms": origin.expires_at_ms,
        "effect_descriptors": [dict(item) for item in origin.effect_descriptors],
        "receipt_digest": "sha256:" + "9" * 64,
    }
    message = {
        "type": "proactive_talk",
        "content": "exact",
        "event_id": "event-scheduler-child",
        "ts_ms": NOW,
        "request_id": "request-scheduler-child",
    }
    derived_values = {
        "authority_context": {
            "schema_version": DERIVED_EFFECT_AUTHORITY_SCHEMA,
            "origin_event_id": "runtimeevent://scheduler/acceptance",
            "origin_receipt_ref": "action-preview://scheduler/acceptance",
            "origin_boundary": origin_boundary,
        },
        "action_kind": "proactive.ui_push",
        "action_scope": message["request_id"],
        "action_args": message,
        "risk_level": 3,
        "guardian_capability_id": "proactive_ui_push",
        "context_pack_digest": origin.context_pack_digest,
        "scene_identity": dict(origin.scene_identity),
        "task_graph_id": origin.task_graph_id,
        "graph_revision": origin.graph_revision,
        "task_node_id": origin.task_node_id,
        "parent_governor_grant_ref": origin.parent_governor_grant_ref,
        "node_grant_ref": origin.node_grant_ref,
        "node_grant": dict(origin.node_grant),
        "origin_receipt": origin_receipt,
        "now_ms": NOW,
    }
    derived = resolver.resolve(**derived_values)
    assert derived.authority_kind == "DERIVED_EFFECT"
    assert derived.binding["origin_authority_kind"] == "ACCEPTANCE_EFFECT"

    tampered = deepcopy(derived_values)
    tampered["authority_context"]["origin_boundary"]["action_args"]["extra"] = True
    with pytest.raises(ActionPreviewAuthorityError) as drifted:
        resolver.resolve(**tampered)
    assert drifted.value.reason_code == ACTION_PREVIEW_DRIFT

    effect["status"] = "COMMITTED"
    with pytest.raises(ActionPreviewAuthorityError) as stale:
        resolver.resolve(**derived_values)
    assert stale.value.reason_code == ACTION_PREVIEW_BLOCKED

    effect["status"] = "CLAIMED"
    effect["fencing_token"] = 8
    with pytest.raises(ActionPreviewAuthorityError) as rotated_fence:
        resolver.resolve(**derived_values)
    assert rotated_fence.value.reason_code == ACTION_PREVIEW_BLOCKED
    effect["fencing_token"] = 7

    nested = deepcopy(derived_values)
    nested["origin_receipt"]["authority_kind"] = "DERIVED_EFFECT"
    with pytest.raises(ActionPreviewAuthorityError) as nested_authority:
        resolver.resolve(**nested)
    assert nested_authority.value.reason_code == ACTION_PREVIEW_BLOCKED

    command.target_id = "scheduler_production"
    work_order.metadata["planning_lineage"]["node_bindings"][0][
        "target_id"
    ] = "scheduler_production"
    no_expansion = resolver.resolve(**values)
    assert no_expansion.effect_descriptors == ()


def test_resolver_allows_only_bound_operator_scheduler_task_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver, values, _effect, command, work_order = _resolver_harness(monkeypatch)
    command.surface = "operator"
    work_order.metadata.update(
        {
            "operator_command_id": "scheduler.task.trigger",
            "command_surface": "operator",
        }
    )
    operator_origin = resolver.resolve(**values)
    assert operator_origin.effect_descriptors

    work_order.metadata["operator_command_id"] = "sys_mode.switch"
    with pytest.raises(ActionPreviewAuthorityError) as forged_operator:
        resolver.resolve(**values)
    assert forged_operator.value.reason_code == ACTION_PREVIEW_BLOCKED

    work_order.metadata["operator_command_id"] = "scheduler.task.trigger"
    work_order.metadata["command_surface"] = "scheduler"
    with pytest.raises(ActionPreviewAuthorityError) as forged_surface:
        resolver.resolve(**values)
    assert forged_surface.value.reason_code == ACTION_PREVIEW_BLOCKED
