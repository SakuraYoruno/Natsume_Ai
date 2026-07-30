from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.governed_task_adapter import _build_contracts
from host_brain.app.direct_chat_scope import bind_direct_chat_scope
from host_brain.app.emotion_context import (
    HostEmotionRuntime,
    build_chat_emotion_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.schemas import ChatRequest
from host_brain.digital_life.affect_fusion import AffectiveScope
from host_brain.app.host_runtime_context_wiring import (
    runtime_environment_observation_provider,
)
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.long_term_goal_repository import (
    SQLiteLongTermGoalAuthority,
    derive_long_term_goal_scope,
)
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
)
from host_brain.runtime.context_application import (
    RuntimeContextApplicationError,
    RuntimeContextApplicationService,
)
from host_brain.runtime.conversation_scope import SecureGatewayConversationScopeV1
from host_brain.runtime.environment_state import build_environment_snapshot
from host_brain.runtime.intent_authority import IntentPolicyIdentityV1
from host_brain.runtime.intent_admission import DurableIntentAdmissionVerifier
from host_brain.runtime.intent_arbiter import IntentArbiter
from host_brain.runtime.production_intent_authority import ProductionIntentAuthorityPort
from host_brain.runtime.production_composition import (
    RuntimeContextComposition,
    build_runtime_context_composition,
)
from host_brain.runtime.scene_resolver import SceneResolverError
from host_brain.runtime.world_source_verifier import RuntimeEventWorldSourceVerifier
from host_brain.runtime.scene_consumer_projection import project_scene_decision_v2
from host_brain.lifecycle_runtime.proactive_dispatch import (
    _require_proactive_runtime_context,
)
from natsume_contracts.task import HostCommandSurface


_LOCAL_NOW = datetime.now().astimezone()
_LOCAL_DAY_START = _LOCAL_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
_DAY_START_MS = int(_LOCAL_DAY_START.timestamp() * 1000)
_LEASE_START_MS = int((_LOCAL_DAY_START - timedelta(days=1)).timestamp() * 1000)
_LEASE_DURATION_MS = int(timedelta(days=4).total_seconds() * 1000)


class _UnusedIntentAuthorityPort:
    def resolve_intent_authority(self, **_kwargs: Any) -> None:
        raise AssertionError("freezing World/Scene must not fabricate Intent authority")


@dataclass(frozen=True)
class _Harness:
    store: RuntimeEventStore
    journal: SQLiteWorldObservationJournal
    composition: RuntimeContextComposition
    service: RuntimeContextApplicationService
    runtime_scope_id: str
    authority_provider: Any
    clock: list[int]
    environment_activity: list[str]


def _at_local_hour(hour: int) -> int:
    return int(_LOCAL_DAY_START.replace(hour=hour).timestamp() * 1000)


def _build_harness(
    tmp_path: Path,
    *,
    name: str = "context-application",
    clock_ms: int | None = None,
    environment_activity: str = "WORKING",
    environment_observation_provider: Any | None = None,
) -> _Harness:
    db_path = tmp_path / f"{name}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne03-{name}",
        ts_ms=_DAY_START_MS,
    )
    assert migrated.success is True, migrated.as_dict()
    current_ms = [_at_local_hour(14) if clock_ms is None else int(clock_ms)]
    activity = [environment_activity]
    store = RuntimeEventStore(db_path, clock=lambda: current_ms[0])
    runtime_scope_id = f"runtime-scope-{name}"
    owner_id = f"runtime-owner-{name}"
    repository = EmbodimentOutboxRepository(
        db_path,
        runtime_scope_id=runtime_scope_id,
    )
    lease = repository.acquire_runtime_lease(
        worker_id=owner_id,
        lease_ms=_LEASE_DURATION_MS,
        now_ms=_LEASE_START_MS,
    )
    assert lease is not None

    def authority_provider() -> dict[str, Any] | None:
        current = repository.validate_runtime_lease(
            worker_id=owner_id,
            lease_id=str(lease["lease_id"]),
            fencing_token=int(lease["fencing_token"]),
            now_ms=int(datetime.now().astimezone().timestamp() * 1000),
        )
        return (
            {**current, "runtime_scope_id": runtime_scope_id}
            if current is not None
            else None
        )

    journal = SQLiteWorldObservationJournal(
        db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=authority_provider,
        source_event_verifier=RuntimeEventWorldSourceVerifier(store),
        clock=lambda: current_ms[0],
    )
    effective_environment_provider = environment_observation_provider or (
        lambda *, now_ms: {
            **build_environment_snapshot(now_ms=now_ms),
            "activity_state": activity[0],
        }
    )
    composition = build_runtime_context_composition(
        event_store=store,
        observation_port=journal,
        long_term_goal_authority=SQLiteLongTermGoalAuthority(
            db_path,
            runtime_scope_id=runtime_scope_id,
            runtime_authority_provider=authority_provider,
        ),
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=authority_provider,
        intent_authority_port=_UnusedIntentAuthorityPort(),
        intent_policy_identity=IntentPolicyIdentityV1(
            policy_ref="policy://intent-arbiter/context-application-v1",
            policy_digest="1" * 64,
        ),
        environment_observation_provider=effective_environment_provider,
    )
    return _Harness(
        store=store,
        journal=journal,
        composition=composition,
        service=RuntimeContextApplicationService(composition),
        runtime_scope_id=runtime_scope_id,
        authority_provider=authority_provider,
        clock=current_ms,
        environment_activity=activity,
    )


def _request(**values: Any) -> SimpleNamespace:
    defaults = {
        "message": "执行请求",
        "workspace_id": "request-body-must-not-be-trusted",
        "scene_type": "caller-selected-scene-must-not-be-trusted",
        "multimodal_observations": {},
    }
    defaults.update(values)
    return SimpleNamespace(**defaults)


def _governor(route: str, target_id: str = "") -> dict[str, Any]:
    return {
        "request_id": f"request-{route}-scene",
        "intent_id": f"intent-{route}-scene",
        "decision": "APPROVE",
        "reason_code": "APPROVE",
        "risk_level": 1,
        "tokens": {"approval_token_ref": "approval::must-not-be-persisted"},
        "approved_action_binding": {
            "binding_ref": "governed-action://sha256/" + "a" * 64,
            "route": route,
            "target_id": target_id,
        },
        "ts_ms": _at_local_hour(14),
    }


def _freeze(
    service: RuntimeContextApplicationService,
    *,
    person_id: str = "person-ne03",
    ts_ms: int | None = None,
    request_body: Any | None = None,
    intent_draft: dict[str, Any] | None = None,
    command_authority: dict[str, Any] | None = None,
    emotion_snapshot: dict[str, Any] | None = None,
    emotion_context: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
):
    return service.freeze_request(
        request_body=request_body or _request(),
        person_id=person_id,
        ts_ms=_at_local_hour(14) if ts_ms is None else ts_ms,
        intent_draft=intent_draft or {"route": "chat", "task": "普通聊天"},
        governor_result={"risk_level": 1},
        command_authority=command_authority or {"principal_id": person_id},
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
    )


def test_direct_chat_scope_cannot_select_life_or_runtime_context_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local UI path shares one server-derived scope before any Life write."""

    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "local-context-owner")
    harness = _build_harness(tmp_path, name="direct-chat-local-scope")
    request = ChatRequest(
        message="我今天有点低落。",
        person_id="caller-selected-person",
        workspace_id="caller-selected-workspace",
        session_id="direct-chat-session",
        emotion_context={"workspace_id": "nested-caller-workspace"},
    )
    scope = bind_direct_chat_scope(
        request,
        session_id=str(request.session_id or ""),
    )

    affect_scope = AffectiveScope.from_request(request)
    frozen = _freeze(
        harness.service,
        person_id=str(request.person_id),
        request_body=request,
        command_authority=scope.as_command_authority(),
    )

    assert affect_scope.workspace_id == "personal:local-context-owner"
    assert affect_scope.relationship_subject_id == "local-context-owner"
    assert frozen.payload["runtime_context_principal_id"] == "local-context-owner"
    assert frozen.payload["workspace_id"] == "personal:local-context-owner"
    assert frozen.payload["subject_id"] == "local-context-owner"
    assert "caller-selected-person" not in frozen.payload.values()
    assert "caller-selected-workspace" not in frozen.payload.values()


def test_signed_gateway_scope_is_reused_exactly_by_affect_and_runtime_context(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="gateway-conversation-scope")
    scope = SecureGatewayConversationScopeV1.derive(
        principal_id="service.edge.gateway",
        tenant_id="tenant-emotion-scope",
        platform="qq",
        from_id="transport-user-private-id",
        conversation_id="transport-conversation-private-id",
    )
    request = ChatRequest(
        message="我今天有些低落。",
        workspace_id=scope.workspace_id,
        person_id=scope.relationship_subject_id,
        session_id=scope.session_id,
    )

    affect_scope = AffectiveScope.from_request(request)
    frozen = _freeze(
        harness.service,
        person_id=scope.relationship_subject_id,
        request_body=request,
        command_authority={
            "principal_id": "service.edge.gateway",
            "tenant_id": "tenant-emotion-scope",
            **scope.as_command_authority(),
        },
    )

    assert affect_scope.workspace_id == scope.workspace_id
    assert affect_scope.relationship_subject_id == scope.relationship_subject_id
    assert frozen.payload["runtime_context_principal_id"] == scope.principal_id
    assert frozen.payload["workspace_id"] == affect_scope.workspace_id
    assert frozen.payload["subject_id"] == affect_scope.relationship_subject_id
    assert "transport-user-private-id" not in repr(frozen.to_dict())
    assert "transport-conversation-private-id" not in repr(frozen.to_dict())

    with pytest.raises(RuntimeContextApplicationError, match="subject drifted"):
        _freeze(
            harness.service,
            person_id="caller-selected-other-subject",
            request_body=request,
            command_authority={
                "principal_id": "service.edge.gateway",
                "tenant_id": "tenant-emotion-scope",
                **scope.as_command_authority(),
            },
        )


def test_freeze_writes_real_world_source_and_does_not_append_same_semantics(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path)

    first = _freeze(
        harness.service,
        intent_draft={"route": "skill", "task": "执行架构重构"},
        command_authority={
            "principal_id": "principal-ne03",
            "tenant_id": "trusted-tenant",
        },
    )
    repeated = _freeze(
        harness.service,
        intent_draft={"route": "skill", "task": "执行架构重构"},
        command_authority={
            "principal_id": "principal-ne03",
            "tenant_id": "trusted-tenant",
        },
    )

    assert first.to_dict() == repeated.to_dict()
    assert first.payload["world_identity_v2"]["revision"] == 1
    assert next(
        item["value"]
        for item in first.payload["world_snapshot_v2"]["facts"]
        if item["key"] == "environment.activity_state"
    ) == "WORKING"
    assert first.scene_identity["scene_id"] == "scene.architecture-refactor"
    history = harness.composition.scene_resolver.transition_history(
        workspace_id="tenant:trusted-tenant",
        subject_id="person-ne03",
    )
    assert len(history) == 1
    assert history[0]["to_digest"] == first.scene_identity["decision_digest"]


def test_long_term_goal_is_frozen_from_owner_and_revocation_expires_world_fact(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="long-term-goal-context")
    authority = harness.composition.long_term_goal_authority
    workspace, subject = derive_long_term_goal_scope(
        principal_id="person-ne03",
        tenant_id=None,
    )
    activated = authority.apply_accepted_transition(
        authority={
            "allowed": True,
            "operator_command_id": "long_term_goal.activate",
            "audit_ref": "secure-ingress://audit/goal-activate",
            "principal_id": "person-ne03",
            "tenant_id": "",
        },
        command_id="long_term_goal.activate",
        operation_id="operator-state-control://goal/context-activate",
        workspace_id=workspace,
        subject_id=subject,
        goal_id="safe-evolution",
        expected_version=0,
        constraints=(
            {
                "code": "GOAL_SAFE_EVOLUTION_REVIEW",
                "disposition": "REVIEW",
                "reason": "长期演化变更必须复核",
            },
        ),
        observed_at_ms=harness.clock[0],
    )
    frozen = _freeze(harness.service).to_dict()
    assert frozen["intent_constraints_v1"] == {
        "schema": "natsume.intent-constraints.v1",
        "long_term_goal_refs": [activated.goal_ref],
        "long_term_goal_constraints": [
            activated.constraints[0].to_dict()
        ],
    }

    harness.clock[0] += 1
    authority.apply_accepted_transition(
        authority={
            "allowed": True,
            "operator_command_id": "long_term_goal.revoke",
            "audit_ref": "secure-ingress://audit/goal-revoke",
            "principal_id": "person-ne03",
            "tenant_id": "",
        },
        command_id="long_term_goal.revoke",
        operation_id="operator-state-control://goal/context-revoke",
        workspace_id=workspace,
        subject_id=subject,
        goal_id="safe-evolution",
        expected_version=1,
        observed_at_ms=harness.clock[0],
    )
    after = _freeze(
        harness.service,
        ts_ms=harness.clock[0],
    ).to_dict()
    assert after["intent_constraints_v1"] == {
        "schema": "natsume.intent-constraints.v1",
        "long_term_goal_refs": [],
        "long_term_goal_constraints": [],
    }
    head = harness.journal.read_observation_head(
        workspace_id=workspace,
        subject_id=subject,
    )
    batches = harness.journal.list_observation_batches(
        workspace_id=workspace,
        subject_id=subject,
        after_revision=0,
        through_revision=head.revision,
        limit=100,
    )
    assert any(
        expiry.reason_code == "SOURCE_REVOKED"
        for batch in batches
        for expiry in batch.expiries
    )


def test_require_frozen_survives_restart_and_rejects_tamper_and_cross_workspace(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="context-restart")
    frozen = _freeze(
        harness.service,
        intent_draft={"route": "plan", "task": "研究部署方案"},
        command_authority={"principal_id": "principal-restart"},
    ).to_dict()

    restarted = RuntimeContextApplicationService(
        build_runtime_context_composition(
            event_store=RuntimeEventStore(
                harness.store.db_path,
                clock=lambda: _at_local_hour(14),
            ),
            observation_port=SQLiteWorldObservationJournal(
                harness.store.db_path,
                runtime_scope_id=harness.runtime_scope_id,
                runtime_authority_provider=harness.authority_provider,
                source_event_verifier=RuntimeEventWorldSourceVerifier(
                    RuntimeEventStore(harness.store.db_path)
                ),
                clock=lambda: _at_local_hour(14),
            ),
            long_term_goal_authority=(
                harness.composition.long_term_goal_authority
            ),
            runtime_scope_id=harness.runtime_scope_id,
            runtime_authority_provider=harness.authority_provider,
            intent_authority_port=_UnusedIntentAuthorityPort(),
            intent_policy_identity=IntentPolicyIdentityV1(
                policy_ref="policy://intent-arbiter/context-application-v1",
                policy_digest="1" * 64,
            ),
        )
    )
    assert restarted.require_frozen(frozen).to_dict() == frozen

    identity_tamper = deepcopy(frozen)
    identity_tamper["scene_identity_v2"]["decision_digest"] = "0" * 64
    with pytest.raises(RuntimeContextApplicationError, match="identity drifted"):
        restarted.require_frozen(identity_tamper)

    canonical_tamper = deepcopy(frozen)
    canonical_tamper["scene_identity_v2"]["scene_id"] = "scene.debugging"
    canonical_tamper["scene_decision_v2"]["scene_id"] = "scene.debugging"
    with pytest.raises(SceneResolverError, match="absent or digest-drifted"):
        restarted.require_frozen(canonical_tamper)

    cross_workspace = deepcopy(frozen)
    for section in (
        cross_workspace,
        cross_workspace["world_identity_v2"],
        cross_workspace["world_snapshot_v2"],
        cross_workspace["scene_resolution_input_v1"],
        cross_workspace["scene_identity_v2"],
        cross_workspace["scene_decision_v2"],
    ):
        section["workspace_id"] = "tenant:cross-workspace"
    with pytest.raises(SceneResolverError, match="absent or digest-drifted"):
        restarted.require_frozen(cross_workspace)


def test_workspace_is_derived_from_server_authority_not_request_body(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="server-workspace")
    frozen = _freeze(
        harness.service,
        request_body=_request(
            workspace_id="tenant:attacker-controlled",
            multimodal_observations={
                "workspace_id": "tenant:attacker-controlled-observation"
            },
        ),
        intent_draft={
            "route": "chat",
            "task": "普通聊天",
            "workspace_id": "tenant:attacker-controlled-intent",
        },
        command_authority={
            "principal_id": "principal-server-owned",
            "tenant_id": "trusted-server-tenant",
        },
    ).to_dict()

    assert frozen["workspace_id"] == "tenant:trusted-server-tenant"
    assert frozen["scene_identity_v2"]["workspace_id"] == frozen["workspace_id"]
    assert frozen["world_identity_v2"]["workspace_id"] == frozen["workspace_id"]
    assert "attacker-controlled" not in repr(frozen)


def test_chat_and_skill_work_orders_freeze_the_exact_same_scene_identity(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="work-order-scene")
    frozen = _freeze(
        harness.service,
        intent_draft={"route": "skill", "task": "执行架构重构"},
        command_authority={"principal_id": "principal-work-order"},
    ).to_dict()

    chat = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-chat-scene",
        session_id="session-scene",
        principal_id="principal-work-order",
        ts_ms=_at_local_hour(14),
        intent_draft={
            "route": "chat",
            "action": "exec_intent",
            "target": "host",
            "task": "回答冻结请求",
        },
        governor_result=_governor("chat"),
        sanitized_message="回答冻结请求",
        person_id="person-ne03",
        frozen_context=frozen,
    )
    _command, skill_order, _graph = _build_contracts(
        request_body=_request(),
        request_id="request-skill-scene",
        session_id="session-scene",
        ts_ms=_at_local_hour(14),
        intent_draft={
            "route": "skill",
            "target_id": "memory.search",
            "action": "exec_intent",
            "target": "host",
            "task": "执行冻结技能请求",
        },
        governor_result=_governor("skill", "memory.search"),
        person_id="person-ne03",
        sanitized_message="执行冻结技能请求",
        emotion_context=None,
        render_target_lang="zh",
        command_authority=None,
        frozen_context=frozen,
    )

    for key in (
        "world_snapshot_v2",
        "world_identity_v2",
        "scene_resolution_input_v1",
        "scene_decision_v2",
        "scene_identity_v2",
    ):
        assert chat.work_order.input_pack[key] == frozen[key]
        assert skill_order.input_pack[key] == frozen[key]
    assert chat.work_order.input_pack["scene_identity_v2"] == (
        skill_order.input_pack["scene_identity_v2"]
    )


def test_surface_proactive_and_task_admission_reopen_one_durable_scene_identity(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="three-scene-consumers")
    frozen = _freeze(
        harness.service,
        intent_draft={"route": "chat", "task": "讨论架构重构方案"},
        command_authority={
            "principal_id": "principal-three-consumers",
            "tenant_id": "workspace-three-consumers",
        },
    ).to_dict()
    contracts = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-three-consumers",
        session_id="session-three-consumers",
        principal_id="principal-three-consumers",
        ts_ms=_at_local_hour(14),
        intent_draft={
            "route": "chat",
            "action": "exec_intent",
            "target": "host",
            "task": "讨论架构重构方案",
        },
        governor_result=_governor("chat"),
        sanitized_message="讨论架构重构方案",
        person_id="person-ne03",
        frozen_context=frozen,
    )
    admitted_pack = dict(contracts.work_order.input_pack)
    expected = dict(admitted_pack["scene_identity_v2"])

    surface = project_scene_decision_v2(
        admitted_pack["scene_decision_v2"],
        expected_identity=expected,
    )
    proactive_frozen, proactive = _require_proactive_runtime_context(
        execution_context={
            "work_order_id": "work-order-three-consumers",
            "graph_id": "graph-three-consumers",
            "graph_revision": 1,
            "node_id": "node-three-consumers",
            "attempt_id": "attempt-three-consumers",
            "lease_id": "lease-three-consumers",
            "fencing_token": 1,
            "node_grant_ref": "governor-grant://node/three-consumers",
            "verified_node_grant": {
                "graph_id": "graph-three-consumers",
                "graph_revision": 1,
                "node_id": "node-three-consumers",
                "grant_ref": "governor-grant://node/three-consumers",
            },
            "frozen_input_pack": admitted_pack,
        },
        runtime_context_application=harness.service,
    )

    class _CanonicalCommandRepository:
        @staticmethod
        def get_by_graph(**_query: Any) -> None:
            return None

    class _GovernorLedger:
        @staticmethod
        def trace_request(_request_id: str) -> list[dict[str, Any]]:
            return []

    policy = IntentPolicyIdentityV1(
        policy_ref="policy://intent-arbiter/three-consumers-v1",
        policy_digest="3" * 64,
    )
    authority = ProductionIntentAuthorityPort(
        command_repository=_CanonicalCommandRepository(),
        scene_resolver=harness.composition.scene_resolver,
        governor_ledger=_GovernorLedger(),
        policy_identity=policy,
        long_term_goal_authority=(
            harness.composition.long_term_goal_authority
        ),
        clock=lambda: harness.clock[0],
    )
    admission = DurableIntentAdmissionVerifier(
        authority_port=authority,
        intent_arbiter=IntentArbiter(
            authority_port=authority,
            policy_identity=policy,
            clock=lambda: harness.clock[0],
        ),
        event_store=harness.store,
        clock=lambda: harness.clock[0],
    )
    admitted_scene = admission._require_scene(  # noqa: SLF001
        {
            "workspace_id": expected["workspace_id"],
            "subject_id": expected["subject_id"],
            "scene_id": expected["scene_id"],
            "revision": expected["revision"],
            "decision_digest": expected["decision_digest"],
        }
    )

    assert surface.identity.to_dict() == expected
    assert proactive_frozen["scene_identity_v2"] == expected
    assert proactive.identity.to_dict() == expected
    assert {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": admitted_scene.workspace_id,
        "subject_id": admitted_scene.subject_id,
        "scene_id": admitted_scene.scene_id,
        "revision": admitted_scene.revision,
        "decision_digest": admitted_scene.decision_digest,
    } == expected


@pytest.mark.parametrize(
    ("case_id", "expected_scene", "hour", "intent", "trusted_activity", "emotion"),
    (
        (
            "architecture",
            "scene.architecture-refactor",
            14,
            {"route": "skill", "task": "执行架构重构"},
            "WORKING",
            None,
        ),
        (
            "debugging",
            "scene.debugging",
            14,
            {"route": "skill", "task": "调试故障"},
            "WORKING",
            None,
        ),
        (
            "research",
            "scene.research",
            14,
            {"route": "plan", "task": "研究资料"},
            "WORKING",
            None,
        ),
        (
            "casual-chat",
            "scene.casual-chat",
            14,
            {"route": "chat", "task": "普通聊天"},
            "WORKING",
            None,
        ),
        (
            "late-night",
            "scene.late-night",
            1,
            {"route": "chat", "task": "普通聊天"},
            "WORKING",
            None,
        ),
        (
            "frustrated",
            "scene.user-frustrated",
            14,
            {"route": "chat", "task": "普通聊天"},
            "WORKING",
            {"emotion_context": {"emotion_tag": "ANGRY"}},
        ),
        (
            "game-low-interruption",
            "scene.game-low-interruption",
            14,
            {"route": "chat", "task": "普通聊天"},
            "GAMING",
            None,
        ),
        (
            "release-maintenance",
            "scene.release-maintenance",
            14,
            {"route": "skill", "task": "执行发布维护"},
            "WORKING",
            None,
        ),
    ),
)
def test_context_application_resolves_eight_required_scene_inputs(
    tmp_path: Path,
    case_id: str,
    expected_scene: str,
    hour: int,
    intent: dict[str, Any],
    trusted_activity: str,
    emotion: dict[str, Any] | None,
) -> None:
    production_mode = "gaming" if trusted_activity == "GAMING" else "focus"
    environment_provider = runtime_environment_observation_provider(
        SimpleNamespace(
            get_current_status=lambda: {"sys_mode": production_mode},
        )
    )
    harness = _build_harness(
        tmp_path,
        name=f"scene-{case_id}",
        clock_ms=_at_local_hour(hour),
        environment_activity=trusted_activity,
        environment_observation_provider=environment_provider,
    )

    frozen = _freeze(
        harness.service,
        person_id=f"person-{case_id}",
        ts_ms=_at_local_hour(hour),
        request_body=_request(
            multimodal_observations={
                "activity_state": "GAMING"
                if trusted_activity != "GAMING"
                else "WORKING",
                "scene_id": "scene.caller-forged",
                "constraints": ["caller-forged"],
            }
        ),
        intent_draft=intent,
        command_authority={"principal_id": f"principal-{case_id}"},
        emotion_snapshot=emotion,
    )

    assert frozen.scene_identity["scene_id"] == expected_scene
    assert frozen.payload["scene_decision_v2"]["scene_id"] == expected_scene
    assert frozen.payload["scene_resolution_input_v1"]["workspace_id"] == (
        frozen.payload["workspace_id"]
    )
    assert frozen.payload["scene_resolution_input_v1"]["activity_state"] == (
        "GAMING" if production_mode == "gaming" else "FOCUS"
    )


@pytest.mark.parametrize(
    ("emotion_snapshot", "emotion_context", "expected_state", "expected_scene"),
    (
        (
            {
                "version": "chat_emotion_snapshot.v1",
                "emotion_context": {"emotion_tag": "IRRITATED"},
            },
            {"emotion_tag": "HAPPY"},
            "FRUSTRATED",
            "scene.user-frustrated",
        ),
        (None, {"emotion_tag": "HURT"}, "SAD", "scene.casual-chat"),
        (None, {"emotion_tag": "unknown-caller-label"}, "NEUTRAL", "scene.casual-chat"),
    ),
)
def test_production_emotion_shapes_use_canonical_tag_and_alias_authority(
    tmp_path: Path,
    emotion_snapshot: dict[str, Any] | None,
    emotion_context: dict[str, Any] | None,
    expected_state: str,
    expected_scene: str,
) -> None:
    harness = _build_harness(tmp_path, name=f"emotion-shape-{expected_state}")

    frozen = _freeze(
        harness.service,
        emotion_snapshot=emotion_snapshot,
        emotion_context=emotion_context,
    ).to_dict()

    assert frozen["scene_resolution_input_v1"]["user_state"] == expected_state
    assert frozen["scene_decision_v2"]["scene_id"] == expected_scene


def test_real_chat_emotion_chain_drives_world_and_scene_projection(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="real-chat-emotion-chain")
    request = _request(
        message="这个怎么又卡住了，真的很烦",
    )
    emotion_context = resolve_chat_emotion_context(
        request_body=request,
        runtime=HostEmotionRuntime(),
    )
    emotion_snapshot = build_chat_emotion_snapshot(
        emotion_context=emotion_context,
        source="chat_runtime_input",
        ts_ms=harness.clock[0],
    )

    frozen = _freeze(
        harness.service,
        request_body=request,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
    ).to_dict()

    assert emotion_snapshot["emotion_context"]["emotion_tag"] == "FRUSTRATED"
    assert frozen["scene_resolution_input_v1"]["user_state"] == "FRUSTRATED"
    assert frozen["scene_decision_v2"]["scene_id"] == "scene.user-frustrated"


@pytest.mark.parametrize(
    ("stage", "expected_relationship"),
    (
        ("FRIEND", "NORMAL"),
        ("CLOSE_FRIEND", "TRUSTED"),
        ("CONFIDANT", "TRUSTED"),
        ("SOULMATE", "TRUSTED"),
        ("亲密恋人", "TRUSTED"),
        ("unknown-stage", "NORMAL"),
    ),
)
def test_production_bond_summary_stage_drives_relationship_projection(
    tmp_path: Path,
    stage: str,
    expected_relationship: str,
) -> None:
    # Runtime authority ids are canonical ASCII identifiers; the fixture name
    # must not accidentally turn a localized Bond label into authority data.
    harness = _build_harness(
        tmp_path,
        name=f"bond-stage-{expected_relationship.lower()}-{len(stage)}",
    )

    frozen = _freeze(
        harness.service,
        bond_summary={
            "stage": stage,
            "trend": "STABLE",
            "capability_hint": "canonical-server-summary",
        },
    ).to_dict()

    assert frozen["scene_resolution_input_v1"]["relationship_state"] == (
        expected_relationship
    )


def test_context_rejects_untyped_bond_and_emotion_source_claims(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="untyped-source-authority")

    with pytest.raises(
        RuntimeContextApplicationError,
        match="BOND source authority lacks a typed projection",
    ):
        _freeze(
            harness.service,
            bond_summary={
                "stage": "CLOSE_FRIEND",
                "source_ref": "bond://projection/person-ne03/1",
                "source_digest": "1" * 64,
            },
        )
    with pytest.raises(
        RuntimeContextApplicationError,
        match="EMOTION source authority lacks a typed projection",
    ):
        _freeze(
            harness.service,
            emotion_snapshot={
                "emotion_context": {"emotion_tag": "FRUSTRATED"},
                "source_ref": "emotion://projection/person-ne03/1",
                "source_digest": "2" * 64,
            },
        )


def test_identical_active_world_semantics_do_not_append_or_renew_ttl(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="world-no-read-renewal")
    first = _freeze(harness.service).to_dict()
    first_head = harness.journal.read_observation_head(
        workspace_id=first["workspace_id"],
        subject_id=first["subject_id"],
    )
    first_expiries = {
        item["key"]: item["expires_at_ms"]
        for item in first["world_snapshot_v2"]["facts"]
    }

    harness.clock[0] += 60_000
    repeated = _freeze(
        harness.service,
        ts_ms=harness.clock[0],
    ).to_dict()
    repeated_head = harness.journal.read_observation_head(
        workspace_id=first["workspace_id"],
        subject_id=first["subject_id"],
    )

    assert first_head.revision == repeated_head.revision == 1
    assert first["world_snapshot_v2"] == repeated["world_snapshot_v2"]
    assert {
        item["key"]: item["expires_at_ms"]
        for item in repeated["world_snapshot_v2"]["facts"]
    } == first_expiries
    assert len(
        harness.composition.scene_resolver.transition_history(
            workspace_id=first["workspace_id"],
            subject_id=first["subject_id"],
        )
    ) == 1


def test_world_state_can_return_to_an_older_still_active_semantic_value(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="world-semantic-return")
    neutral = _freeze(
        harness.service,
        emotion_context={"emotion_tag": "NEUTRAL"},
    ).to_dict()

    harness.clock[0] += 60_000
    frustrated = _freeze(
        harness.service,
        ts_ms=harness.clock[0],
        emotion_context={"emotion_tag": "IRRITATED"},
    ).to_dict()

    harness.clock[0] += 60_000
    neutral_again = _freeze(
        harness.service,
        ts_ms=harness.clock[0],
        emotion_context={"emotion_tag": "NEUTRAL"},
    ).to_dict()

    assert neutral["scene_decision_v2"]["scene_id"] == "scene.casual-chat"
    assert frustrated["scene_resolution_input_v1"]["user_state"] == "FRUSTRATED"
    assert frustrated["scene_decision_v2"]["scene_id"] == "scene.user-frustrated"
    assert neutral_again["scene_resolution_input_v1"]["user_state"] == "NEUTRAL"
    assert neutral_again["scene_decision_v2"]["scene_id"] == "scene.casual-chat"
    assert neutral_again["world_identity_v2"]["revision"] == 3

    latest_neutral = max(
        (
            item
            for item in neutral_again["world_snapshot_v2"]["facts"]
            if item["key"] == "user.affect_state" and item["value"] == "NEUTRAL"
        ),
        key=lambda item: item["observed_at_ms"],
    )
    assert latest_neutral["observed_at_ms"] == harness.clock[0]


def test_world_ttl_expiry_is_explicit_then_same_request_adds_fresh_source(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="world-production-expiry")
    first = _freeze(harness.service).to_dict()
    workspace = first["workspace_id"]
    subject = first["subject_id"]
    initial_environment_digests = {
        item["key"]: next(
            fact.fact_digest
            for fact in harness.composition.world_resolver.resolve(
                workspace_id=workspace,
                subject_id=subject,
                now_ms=harness.clock[0],
            ).facts
            if fact.key == item["key"]
        )
        for item in first["world_snapshot_v2"]["facts"]
        if item["key"].startswith("environment.")
    }

    harness.clock[0] += 5 * 60_000
    refreshed = _freeze(
        harness.service,
        ts_ms=harness.clock[0],
    ).to_dict()
    head = harness.journal.read_observation_head(
        workspace_id=workspace,
        subject_id=subject,
    )
    batches = harness.journal.list_observation_batches(
        workspace_id=workspace,
        subject_id=subject,
        after_revision=0,
        through_revision=head.revision,
        limit=1000,
    )

    assert head.revision == refreshed["world_identity_v2"]["revision"] == 3
    assert {item.fact_digest for item in batches[1].expiries} == set(
        initial_environment_digests.values()
    )
    assert {item.key for item in batches[2].upserts} == {
        "environment.activity_state",
        "environment.day_phase",
        "environment.local_hour",
    }
    assert all(
        item.source_ref == batches[2].source_event_ref
        for item in batches[2].upserts
    )
    # Reopening is a read and therefore cannot create a fourth revision.
    harness.composition.world_resolver.resolve(
        workspace_id=workspace,
        subject_id=subject,
        now_ms=harness.clock[0],
    )
    assert harness.journal.read_observation_head(
        workspace_id=workspace,
        subject_id=subject,
    ).revision == 3


def test_world_source_is_redacted_scoped_and_caller_scene_activity_are_ignored(
    tmp_path: Path,
) -> None:
    harness = _build_harness(
        tmp_path,
        name="world-production-scope",
        environment_activity="WORKING",
    )
    forged = _request(
        message="private-body-must-never-enter-world",
        workspace_id="tenant:attacker",
        scene_id="scene.game-low-interruption",
        constraints=["FORGED_EXPANSION"],
        multimodal_observations={
            "workspace_id": "tenant:attacker",
            "activity_state": "GAMING",
            "private_body": "never-persist-this",
        },
    )
    tenant_a = _freeze(
        harness.service,
        person_id="person-scope",
        request_body=forged,
        command_authority={
            "principal_id": "principal-scope",
            "tenant_id": "authority-a",
        },
    ).to_dict()
    tenant_b = _freeze(
        harness.service,
        person_id="person-scope",
        request_body=forged,
        command_authority={
            "principal_id": "principal-scope",
            "tenant_id": "authority-b",
        },
    ).to_dict()

    assert tenant_a["workspace_id"] == "tenant:authority-a"
    assert tenant_b["workspace_id"] == "tenant:authority-b"
    assert tenant_a["scene_resolution_input_v1"]["activity_state"] == "WORKING"
    assert tenant_a["scene_decision_v2"]["scene_id"] == "scene.casual-chat"
    assert tenant_a["world_snapshot_v2"]["snapshot_digest"] != (
        tenant_b["world_snapshot_v2"]["snapshot_digest"]
    )
    events = harness.store.list_events(
        event_type="WORLD_OBSERVATION_CAPTURED",
        limit=100,
    )
    serialized = repr([item["payload"] for item in events])
    assert "private-body-must-never-enter-world" not in serialized
    assert "never-persist-this" not in serialized
    assert "FORGED_EXPANSION" not in serialized
    assert "tenant:attacker" not in serialized
    assert {item["payload"]["workspace_id"] for item in events} == {
        "tenant:authority-a",
        "tenant:authority-b",
    }


def test_environment_authority_drift_fails_closed_instead_of_fabricating_normal(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="environment-authority-drift")
    harness.environment_activity[0] = "unsupported-production-mode"

    with pytest.raises(
        RuntimeContextApplicationError,
        match="canonical World observation",
    ):
        _freeze(harness.service)

    assert harness.journal.read_observation_head(
        workspace_id="personal:person-ne03",
        subject_id="person-ne03",
    ).revision == 0


def test_frozen_world_reopen_fails_closed_when_runtime_event_source_is_tampered(
    tmp_path: Path,
) -> None:
    harness = _build_harness(tmp_path, name="world-source-tamper")
    frozen = _freeze(harness.service).to_dict()
    head = harness.journal.read_observation_head(
        workspace_id=frozen["workspace_id"],
        subject_id=frozen["subject_id"],
    )
    source = harness.journal.list_observation_batches(
        workspace_id=frozen["workspace_id"],
        subject_id=frozen["subject_id"],
        after_revision=0,
        through_revision=head.revision,
        limit=1000,
    )[0].source_event_ref

    with sqlite3.connect(str(harness.store.db_path)) as conn:
        conn.execute("DROP TRIGGER trg_runtime_event_journal_no_update")
        conn.execute(
            "UPDATE runtime_event_timeline SET payload_digest = ? WHERE event_id = ?",
            ("sha256:" + "0" * 64, source),
        )
        conn.commit()

    with pytest.raises(
        RuntimeContextApplicationError,
        match="source evidence is unavailable or drifted",
    ):
        harness.service.require_frozen(frozen)
    with pytest.raises(
        RuntimeContextApplicationError,
        match="canonical World observation",
    ):
        _freeze(harness.service, ts_ms=harness.clock[0])
