from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from host_brain.app import (
    asgi,
    asgi_authority_adapters,
    host_runtime_context_wiring,
    tool_runtime_composition,
)
from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.app.emotion_context import HostEmotionRuntime
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.tribe_composition import TribeRuntimeAgentPorts
from host_brain.composition.host_application import HostApplicationPorts


def _host_lifecycle_ports(**overrides: object) -> HostLifecycleApplicationPorts:
    """Build the same typed Host bundle that the ASGI composition consumes."""
    provider = lambda: object()
    factory = lambda **_kwargs: object()
    emotion_runtime = HostEmotionRuntime()
    values: dict[str, object] = {
        "memory_guard": provider,
        "state_arbiter": provider,
        "distillation_engine": provider,
        "semantic_recaller": provider,
        "episodic_store": provider,
        "skill_registry": provider,
        "graph_runtime": provider,
        "routing_matrix": provider,
        "tool_catalog": provider,
        "capability_broker": provider,
        "model_attempt_service": provider,
        "mode_transition_service_factory": factory,
        "activity_monitor_factory": factory,
        "resource_manager_factory": factory,
        "ledger": provider,
        "life_memory_store": provider,
        "bond_update_queue": provider,
        "life_snapshot_reader": provider,
        "silence_engine": provider,
        "state_write_arbiter": provider,
        "emotion_timeline": provider,
        "emotion_context_runtime": lambda: emotion_runtime,
        "bond_stage_engine": provider,
        "shared_memory_index": provider,
        "milestone_tracker": provider,
        "prompt_registry": provider,
        "user_emotion_profile": emotion_runtime.user_emotion_profile,
        "intent_engine": provider,
        "multimodal_observer": provider,
        "multimodal_processor": provider,
        "conversation_history": provider,
        "conversation_turn_store": provider,
        "life_memory_service": provider,
        "model_route_catalog": provider,
        "session_topic_ledger_store": provider,
        "session_tactic_ledger_store": provider,
        "tts_context_router": provider,
        "meta_learning_loop": provider,
        "bond_neo4j_client": provider,
    }
    values.update(overrides)
    return HostLifecycleApplicationPorts(**values)  # type: ignore[arg-type]


def _host_application_ports(
    *,
    lifecycle: HostLifecycleApplicationPorts,
    **overrides: object,
) -> HostApplicationPorts:
    provider = lambda: object()
    values: dict[str, object] = {
        "memory_worth_evaluator": provider,
        "security_immune_agent": provider,
        "token_service": provider,
        "bond_dynamics_writer": provider,
        "capability_forge_service": provider,
        "capability_gap_telemetry": provider,
        "capability_scorecard_provider": provider,
        "begin_authority_runtime": lambda: None,
        "close_authority_runtime": lambda: None,
        "begin_bond_runtime": lambda: None,
        "close_bond_runtime": lambda: None,
        "begin_governance_runtime": lambda: None,
        "close_governance_runtime": lambda: None,
        "publish_memory_service_container": lambda _value: None,
        "withdraw_memory_service_container": lambda _value: None,
    }
    values.update(overrides)
    return HostApplicationPorts(lifecycle=lifecycle, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mode", "activity"),
    (
        ("normal", "NORMAL"),
        ("focus", "FOCUS"),
        ("gaming", "GAMING"),
        ("sleep", "SLEEP"),
        ("maintenance", "MAINTENANCE"),
        ("locked", "MAINTENANCE"),
    ),
)
def test_runtime_environment_observation_uses_app_local_mode_authority(
    mode: str,
    activity: str,
) -> None:
    provider = host_runtime_context_wiring.runtime_environment_observation_provider(
        SimpleNamespace(get_current_status=lambda: {"sys_mode": mode})
    )

    observed = provider(now_ms=1_900_000_000_000)

    assert observed["now_ms"] == 1_900_000_000_000
    assert observed["activity_state"] == activity
    assert observed["mode_identity"] == mode


def test_runtime_environment_observation_fails_without_app_local_authority() -> None:
    with pytest.raises(
        RuntimeError,
        match="HOST_RUNTIME_CONTEXT_MODE_AUTHORITY_UNAVAILABLE",
    ):
        host_runtime_context_wiring.runtime_environment_observation_provider(None)


def test_scheduler_clock_secret_is_added_only_under_explicit_key(monkeypatch) -> None:
    monkeypatch.setattr(
        asgi_authority_adapters,
        "get_input_settings",
        lambda: SimpleNamespace(
            secure_ingress_hmac_secrets_json='{"external.key":"external-secret"}',
            edge_gateway_hmac_key_id="",
            edge_gateway_hmac_secret="",
        ),
    )
    monkeypatch.setattr(
        asgi_authority_adapters,
        "get_scheduler_settings",
        lambda: SimpleNamespace(
            clock_key_id="scheduler.clock.key",
            clock_hmac_secret=SimpleNamespace(
                get_secret_value=lambda: "scheduler-clock-secret"
            ),
        ),
    )

    assert asgi._command_ingress_hmac_secrets() == {
        "external.key": "external-secret",
        "scheduler.clock.key": "scheduler-clock-secret",
    }


def test_disabled_graph_cancellation_operator_has_no_startup_dependency() -> None:
    asgi._assert_graph_cancellation_operator_startup(
        enabled=False,
        operation_reconciliation_enabled=False,
        effect_reconciliation_enabled=False,
        terminal_reconciliation_enabled=False,
        command_ingress=None,
        security=None,
    )


@pytest.mark.parametrize("mode", ("OFF", "SHADOW", None))
def test_graph_cancellation_operator_requires_enforced_ingress(mode) -> None:
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_SECURE_INGRESS_ENFORCE",
    ):
        asgi._assert_graph_cancellation_operator_startup(
            enabled=True,
            operation_reconciliation_enabled=True,
            effect_reconciliation_enabled=True,
            terminal_reconciliation_enabled=True,
            command_ingress=SimpleNamespace(mode=mode),
            security=None,
        )


@pytest.mark.parametrize(
    "composition",
    (
        None,
        SimpleNamespace(enabled=False, execution_enabled=True, ingress=object()),
        SimpleNamespace(enabled=True, execution_enabled=False, ingress=object()),
        SimpleNamespace(
            enabled=True,
            execution_enabled=True,
            ingress=SimpleNamespace(execute=None),
        ),
    ),
)
def test_graph_cancellation_operator_requires_executable_gateway(
    composition,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE",
    ):
        asgi._assert_graph_cancellation_operator_startup(
            enabled=True,
            operation_reconciliation_enabled=True,
            effect_reconciliation_enabled=True,
            terminal_reconciliation_enabled=True,
            command_ingress=SimpleNamespace(mode="ENFORCE"),
            security=SimpleNamespace(obs_command_composition=composition),
        )


def test_graph_cancellation_operator_accepts_complete_startup_authority() -> None:
    asgi._assert_graph_cancellation_operator_startup(
        enabled=True,
        operation_reconciliation_enabled=True,
        effect_reconciliation_enabled=True,
        terminal_reconciliation_enabled=True,
        command_ingress=SimpleNamespace(mode=SimpleNamespace(value="ENFORCE")),
        security=SimpleNamespace(
            obs_command_composition=SimpleNamespace(
                enabled=True,
                execution_enabled=True,
                ingress=SimpleNamespace(execute=lambda *_args, **_kwargs: None),
            )
        ),
    )


def test_graph_cancellation_operator_requires_operation_reconciliation() -> None:
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_OPERATION_RECONCILIATION",
    ):
        asgi._assert_graph_cancellation_operator_startup(
            enabled=True,
            operation_reconciliation_enabled=False,
            effect_reconciliation_enabled=True,
            terminal_reconciliation_enabled=True,
            command_ingress=None,
            security=None,
        )


def test_graph_cancellation_operator_requires_effect_reconciliation() -> None:
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_EFFECT_RECONCILIATION",
    ):
        asgi._assert_graph_cancellation_operator_startup(
            enabled=True,
            operation_reconciliation_enabled=True,
            effect_reconciliation_enabled=False,
            terminal_reconciliation_enabled=True,
            command_ingress=None,
            security=None,
        )


def test_graph_cancellation_operator_requires_terminal_reconciliation() -> None:
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_TERMINAL_RECONCILIATION",
    ):
        asgi._assert_graph_cancellation_operator_startup(
            enabled=True,
            operation_reconciliation_enabled=True,
            effect_reconciliation_enabled=True,
            terminal_reconciliation_enabled=False,
            command_ingress=None,
            security=None,
        )


def test_operation_reconciliation_operator_requires_secure_gateway() -> None:
    asgi._assert_task_operation_reconciliation_operator_startup(
        enabled=False,
        command_ingress=None,
        security=None,
    )
    with pytest.raises(
        RuntimeError,
        match=(
            "TASK_OPERATION_RECONCILIATION_OPERATOR_"
            "REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
    ):
        asgi._assert_task_operation_reconciliation_operator_startup(
            enabled=True,
            command_ingress=SimpleNamespace(mode="SHADOW"),
            security=None,
        )
    with pytest.raises(
        RuntimeError,
        match="TASK_OPERATION_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE",
    ):
        asgi._assert_task_operation_reconciliation_operator_startup(
            enabled=True,
            command_ingress=SimpleNamespace(mode="ENFORCE"),
            security=None,
        )


def test_effect_reconciliation_operator_requires_secure_gateway() -> None:
    asgi._assert_task_effect_reconciliation_operator_startup(
        enabled=False,
        command_ingress=None,
        security=None,
    )
    with pytest.raises(
        RuntimeError,
        match=(
            "TASK_EFFECT_RECONCILIATION_OPERATOR_"
            "REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
    ):
        asgi._assert_task_effect_reconciliation_operator_startup(
            enabled=True,
            command_ingress=SimpleNamespace(mode="SHADOW"),
            security=None,
        )
    with pytest.raises(
        RuntimeError,
        match="TASK_EFFECT_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE",
    ):
        asgi._assert_task_effect_reconciliation_operator_startup(
            enabled=True,
            command_ingress=SimpleNamespace(mode="ENFORCE"),
            security=None,
        )


def test_terminal_reconciliation_operator_requires_secure_gateway() -> None:
    asgi._assert_task_terminal_reconciliation_operator_startup(
        enabled=False,
        command_ingress=None,
        security=None,
    )
    with pytest.raises(
        RuntimeError,
        match=(
            "TASK_TERMINAL_RECONCILIATION_OPERATOR_"
            "REQUIRES_SECURE_INGRESS_ENFORCE"
        ),
    ):
        asgi._assert_task_terminal_reconciliation_operator_startup(
            enabled=True,
            command_ingress=SimpleNamespace(mode="SHADOW"),
            security=None,
        )
    with pytest.raises(
        RuntimeError,
        match="TASK_TERMINAL_RECONCILIATION_OPERATOR_COMMAND_GATEWAY_UNAVAILABLE",
    ):
        asgi._assert_task_terminal_reconciliation_operator_startup(
            enabled=True,
            command_ingress=SimpleNamespace(mode="ENFORCE"),
            security=None,
        )
    asgi._assert_task_terminal_reconciliation_operator_startup(
        enabled=True,
        command_ingress=SimpleNamespace(mode="ENFORCE"),
        security=SimpleNamespace(
            obs_command_composition=SimpleNamespace(
                enabled=True,
                execution_enabled=True,
                ingress=SimpleNamespace(execute=lambda: None),
            )
        ),
    )


def test_cancellation_production_gate_checks_only_when_cancellation_is_enabled() -> None:
    asgi._assert_graph_cancellation_reconciliation_production_ready(
        enabled=False,
        task_store=None,
    )
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_EFFECT_PRODUCTION_GATE_UNAVAILABLE",
    ):
        asgi._assert_graph_cancellation_reconciliation_production_ready(
            enabled=True,
            task_store=object(),
        )
    with pytest.raises(
        RuntimeError,
        match=(
            "TASK_GRAPH_CANCELLATION_TERMINAL_RECONCILIATION_"
            "PRODUCTION_GATE_UNAVAILABLE"
        ),
    ):
        asgi._assert_graph_cancellation_reconciliation_production_ready(
            enabled=True,
            task_store=SimpleNamespace(
                assert_effect_reconciliation_production_ready=lambda: None,
            ),
        )

    calls: list[str] = []
    store = SimpleNamespace(
        assert_effect_reconciliation_production_ready=(
            lambda: calls.append("effect-production-ready")
        ),
        assert_terminal_reconciliation_production_ready=(
            lambda: calls.append("terminal-production-ready")
        ),
    )
    asgi._assert_graph_cancellation_reconciliation_production_ready(
        enabled=True,
        task_store=store,
    )
    assert calls == ["effect-production-ready", "terminal-production-ready"]


def test_asgi_builds_publishes_injects_and_closes_security_container(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    events: list[str] = []
    ledger = SimpleNamespace(
        canonical_db_path="D:/runtime/canonical.sqlite3",
        log_voice_event=lambda *_args, **_kwargs: None,
        trace_request=lambda _request_id: [],
    )
    token_service = object()
    capability_broker = object()
    model_attempt_service = object()
    prompt_registry = object()
    intent_engine = object()
    resource_gate_calls: list[dict[str, object]] = []

    def assess_background_task(**kwargs):
        resource_gate_calls.append(dict(kwargs))
        return {"allowed": True, "source": "app-local"}

    resource_manager = SimpleNamespace(
        assess_background_task=assess_background_task,
    )
    tool_catalog = object()
    governed_tool_application = object()
    canonical_executor = object()
    workspace_runtime = object()
    obs_ingress = SimpleNamespace(execute=lambda *_args, **_kwargs: None)
    outcome_lookup = lambda _receipt_ref: None
    pentest_composition = object()
    signed_ingress = object()
    skill_registry = object()
    query_projection_store = object()
    mode_transition_service = SimpleNamespace(
        get_current_status=lambda: {"sys_mode": "gaming"}
    )
    activity_monitor = SimpleNamespace(_gaming_interval=30, _idle_interval=60)
    host_lifecycle = SimpleNamespace(
        ports=_host_lifecycle_ports(
            memory_guard=lambda: object(),
            state_arbiter=lambda: object(),
            distillation_engine=lambda: object(),
            semantic_recaller=lambda: object(),
            episodic_store=lambda: object(),
            skill_registry=lambda: skill_registry,
            tool_catalog=lambda: tool_catalog,
            capability_broker=lambda: capability_broker,
            model_attempt_service=lambda: model_attempt_service,
            ledger=lambda: ledger,
            life_snapshot_reader=lambda: object(),
            prompt_registry=lambda: prompt_registry,
            intent_engine=lambda: intent_engine,
            bond_stage_engine=lambda: object(),
        ),
        get_hard_state=lambda: SimpleNamespace(sys_mode="gaming"),
        get_endocrine_state=lambda **_kwargs: {},
        set_sys_mode=lambda _mode: {},
        get_activity_monitor=lambda **_kwargs: activity_monitor,
        get_resource_manager=lambda **_kwargs: resource_manager,
        get_mode_transition_service=lambda: mode_transition_service,
        start_activity_monitor=lambda **_kwargs: (
            events.append("activity-monitor-started") or activity_monitor
        ),
        stop_activity_monitor=lambda **_kwargs: events.append(
            "activity-monitor-stopped"
        ),
        stop_resource_manager=lambda **_kwargs: None,
        notify_user_interaction=lambda: None,
    )
    class EmbodimentRuntime:
        async def establish_transport_authority(self) -> None:
            events.append("embodiment-transport-fenced")

        async def run_authority_heartbeat(self) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("embodiment-heartbeat-stopped")
                raise

        def close(self) -> None:
            events.append("embodiment-runtime-closed")

    embodiment_runtime = EmbodimentRuntime()

    class ObsCommandComposition:
        enabled = True
        execution_enabled = True
        ingress = obs_ingress
        outcome_journal = SimpleNamespace(lookup=outcome_lookup)

        def recover_deferred_review_admissions(self, **kwargs):  # noqa: ANN003
            assert kwargs == {
                "command_repository": canonical_command_repository,
                "task_store": canonical_task_store,
                "review_store": task_runtime_container.review_store,
                "review_continuation_store": (
                    task_runtime_container.review_continuation_store
                ),
            }
            return ()

        async def aclose(self) -> None:
            assert application.state.task_runtime_container is task_runtime_container
            assert captured["obs_command_ingress_provider"]() is obs_ingress
            events.append("obs-command-admission-drained")

    class Container:
        obs_command_composition = ObsCommandComposition()
        pentest_secure_run_composition = pentest_composition
        signed_skill_provider_intake_composition = SimpleNamespace(
            enabled=True,
            ingress=signed_ingress,
        )

        async def aclose(self) -> None:
            assert captured["obs_command_ingress_provider"]() is obs_ingress
            assert (
                captured["pentest_secure_run_composition_provider"]()
                is pentest_composition
            )
            assert (
                captured["signed_skill_provider_intake_provider"]()
                is signed_ingress
            )
            events.append("container-closed")

    container = Container()

    class GovernedRuntime:
        async def recover_startup_graphs(self) -> tuple[object, ...]:
            events.append("host-startup-graphs-recovered")
            return ()

    governed_runtime = GovernedRuntime()
    acceptance_effect_dispatcher = object()
    acceptance_effect_runtime = object()
    scheduler_worker_registry = object()
    scheduler_operation_handler = object()
    scheduler_definition_store = object()
    scheduler_result_store = object()

    class OperatorRuntime:
        async def activate(self) -> None:
            events.append("operator-runtime-activated")

        async def close_admission(self) -> None:
            events.append("operator-runtime-admission-closed")

        async def drain(self) -> None:
            events.append("operator-runtime-drained")

        async def aclose(self) -> None:
            events.append("operator-runtime-closed")

    operator_runtime = OperatorRuntime()
    class ReviewCommands:
        def __init__(self) -> None:
            self.wake = None

        def bind_wake_continuation(self, wake):  # noqa: ANN001
            self.wake = wake

    review_commands = ReviewCommands()
    internal_runtime = object()
    scheduler_runtime = object()
    intent_admission_verifier = object()
    runtime_group = SimpleNamespace(wake_review_continuation=lambda: None)
    internal_ingress = object()
    scheduler_ingress = object()
    scheduler_service = object()
    internal_service = object()
    command_evaluator = object()
    class CommandStore:
        @staticmethod
        def resolve_committed_authority_by_audit(*, audit_ref):  # noqa: ANN001
            del audit_ref
            return None

    command_store = CommandStore()

    class CommandIngressContainer:
        evaluator = command_evaluator
        store = command_store
        mode = "ENFORCE"

        async def aclose(self) -> None:
            events.append("command-ingress-closed")

    command_ingress_container = CommandIngressContainer()

    class MemoryContainer:
        def close(self) -> None:
            events.append("memory-closed")

    memory_container = MemoryContainer()

    class WorkspaceContainer:
        runtime = workspace_runtime
        cleanup_continuation_report = ()

        def start(self) -> None:
            events.append("workspace-started")

        def close(self) -> None:
            assert "task-runtime-closed" in events
            events.append("workspace-closed")

    workspace_container = WorkspaceContainer()

    class SchedulerContainer:
        runtime = scheduler_runtime
        command_ingress = scheduler_ingress
        command_service = scheduler_service

        async def activate_clock(self) -> None:
            events.append("scheduler-clock-activated")

        async def stop_clock(self) -> None:
            events.append("scheduler-clock-stopped")

    scheduler_container = SchedulerContainer()

    class CanonApplication:
        def sync_startup(self) -> None:
            events.append("canon-synced")

    canon_application = CanonApplication()
    canon_container = SimpleNamespace(application=canon_application)

    class CanonicalCommandRepository:
        @staticmethod
        def get_by_graph(*_args, **_kwargs):  # noqa: ANN001, ANN202
            raise AssertionError(
                "composition-only fixture must not query a canonical command"
            )

    canonical_command_repository = CanonicalCommandRepository()

    class CanonicalTaskStore:
        @staticmethod
        def _unreachable(*_args, **_kwargs):  # noqa: ANN001, ANN202
            raise AssertionError(
                "composition-only fixture must not query canonical task state"
            )

        get_graph = _unreachable
        get_node = _unreachable
        get_attempt = _unreachable
        get_active_lease = _unreachable
        get_acceptance_effect_intent = _unreachable
        get_node_artifact = _unreachable

    canonical_task_store = CanonicalTaskStore()

    class OperationReconciliationService:
        bound_owner_id = None

        def bind_execution_owner(self, owner_id: str) -> None:
            assert owner_id == "test-canonical-owner::runtime::test-boot"
            self.bound_owner_id = owner_id

    _operation_reconciliation_service = OperationReconciliationService()

    class TaskRuntimeContainer:
        bound_runtime = None
        canonical_db_path = ledger.canonical_db_path
        agent_result_repository = object()
        review_store = SimpleNamespace(
            create_review=lambda *_args, **_kwargs: {},
            approve_review=lambda *_args, **_kwargs: {},
            reject_review=lambda *_args, **_kwargs: {},
            resume_review=lambda *_args, **_kwargs: {},
        )
        review_continuation_store = object()
        command_repository = canonical_command_repository
        scheduler_definition_repository = scheduler_definition_store
        scheduler_result_repository = scheduler_result_store
        task_store = canonical_task_store
        recovery_id = "test-recovery"
        operation_reconciliation_service = _operation_reconciliation_service
        effect_reconciliation_service = object()

        @staticmethod
        def execution_owner_id(logical_owner_id: str) -> str:
            assert logical_owner_id == "test-canonical-owner"
            return "test-canonical-owner::runtime::test-boot"

        def bind_runtime(self, runtime) -> None:
            self.bound_runtime = runtime
            events.append("governed-runtime-bound")

        async def activate_runtime(self) -> None:
            assert self.bound_runtime is runtime_group
            events.append("governed-runtime-activated")

        async def aclose(self) -> None:
            assert application.state.task_runtime_container is self
            assert application.state.task_runtime_container_provider() is self
            assert events[-2:] == [
                "obs-command-admission-drained",
                "scheduler-clock-stopped",
            ]
            events.append("task-runtime-closed")

    task_runtime_container = TaskRuntimeContainer()

    class RuntimeGuardian:
        @staticmethod
        def snapshot():
            return SimpleNamespace(revision=1, snapshot_digest="sha256:" + "a" * 64)

        @staticmethod
        def observe_startup_batch(*_args, **_kwargs):
            events.append("guardian-startup-observed")

        @staticmethod
        def read_degradation_state():
            return {
                "schema": "natsume.runtime-guardian-degradation.v1",
                "authority": "RUNTIME_GUARDIAN",
                "status": "READY",
                "active_dependencies": [],
                "recovery_pending_dependencies": [],
            }

        @staticmethod
        def observe_service_batch(*_args, **_kwargs):
            return None

        @staticmethod
        def evaluate_capability(**_kwargs):
            return {
                "allowed": True,
                "allowed_by_degradation": True,
                "decision": "UNCHANGED",
                "durability_status": "READY",
                "guardian_authority": {
                    "authority": "RUNTIME_GUARDIAN",
                    "authority_status": "ACTIVE",
                    "durable": True,
                    "aggregate_ref": "runtime-guardian://sha256/" + "a" * 64,
                    "guardian_revision": 1,
                    "fencing_token": 1,
                },
            }

    runtime_guardian = RuntimeGuardian()
    runtime_context_composition = SimpleNamespace(
        guardian=runtime_guardian,
        intent_authority_port=object(),
        intent_arbiter=object(),
        event_store=object(),
    )
    runtime_context_application = SimpleNamespace(
        composition=runtime_context_composition,
        require_frozen=lambda value: value,
    )

    @asynccontextmanager
    async def base_lifespan():
        events.append("base-started")
        try:
            yield
        finally:
            events.append("base-closed")

    def fake_app_factory(**kwargs):
        captured.update(kwargs)
        return FastAPI()

    def fake_build_container(**kwargs):
        route_ports = kwargs.pop("route_ports")
        assert isinstance(route_ports, HostRoutePorts)
        captured["route_ports"] = route_ports
        provider = kwargs.pop("degradation_capability_port_provider")
        assert callable(provider)
        assert provider() is None
        assert kwargs == {
            "ledger": ledger,
            "canonical_db_path": ledger.canonical_db_path,
            "token_service": token_service,
            "defer_obs_review_recovery": True,
        }
        events.append("container-built")
        return container

    def fake_build_task_runtime(**kwargs):
        assert kwargs.pop("authenticated_outcome_lookup") is outcome_lookup
        assert kwargs == {
            "ledger": ledger,
            "canonical_db_path": ledger.canonical_db_path,
            "reconciliation_public_keys": {"reconciler-v1": b"k" * 32},
            "feature_canary_public_keys": {},
            "terminal_reconciliation_auto_enabled": False,
        }
        events.append("task-runtime-built")
        return task_runtime_container

    def fake_build_acceptance_effect_dispatcher(**kwargs):
        assert kwargs["task_store"] is canonical_task_store
        assert kwargs["owner_id"] == (
            "test-canonical-owner::runtime::test-boot::acceptance-effects"
        )
        assert "host.sys_mode_transition" in kwargs["handlers"]
        assert (
            kwargs["handlers"]["scheduler.operation.execute"]
            is scheduler_operation_handler
        )
        captured["effect_action_preview_service"] = kwargs[
            "action_preview_service"
        ]
        return acceptance_effect_dispatcher

    def fake_build_canon(**kwargs):
        assert kwargs["ledger"] is ledger
        assert kwargs["canonical_db_path"] == ledger.canonical_db_path
        assert kwargs["degradation_capability_port"] is runtime_guardian
        events.append("canon-built")
        return canon_container

    def fake_build_governed_runtime(**kwargs):
        assert kwargs["task_runtime_container"] is task_runtime_container
        assert kwargs["ledger"] is ledger
        assert kwargs["skill_registry"] is skill_registry
        assert kwargs["token_service"] is token_service
        assert kwargs["capability_broker"] is capability_broker
        assert kwargs["resource_manager"] is resource_manager
        assert kwargs["model_attempt_service"] is model_attempt_service
        assert callable(kwargs["production_authority_verifier"])
        captured["production_authority_verifier"] = kwargs[
            "production_authority_verifier"
        ]
        captured["cancellation_authority_verifier"] = kwargs[
            "cancellation_authority_verifier"
        ]
        captured["action_preview_service"] = kwargs["action_preview_service"]
        assert captured["effect_action_preview_service"] is kwargs[
            "action_preview_service"
        ]
        assert kwargs["acceptance_effect_dispatcher"] is (
            acceptance_effect_dispatcher
        )
        assert kwargs["bounded_fanout_enabled"] is False
        assert kwargs["fanout_event_buffer_size"] == 32
        assert isinstance(kwargs["runtime_router"], asgi.CanonicalRuntimeRouter)
        assert kwargs["owner_id"] == "test-canonical-owner::runtime::test-boot"
        assert kwargs["runtime_context_application"] is runtime_context_application
        assert kwargs["intent_admission_verifier"] is intent_admission_verifier
        assert kwargs["degradation_capability_port"] is runtime_guardian
        events.append("governed-runtime-built")
        return governed_runtime

    def fake_build_internal_runtime(**kwargs):
        assert kwargs["task_runtime_container"] is task_runtime_container
        assert kwargs["ledger"] is ledger
        assert kwargs["token_service"] is token_service
        assert kwargs["result_repository"] is task_runtime_container.agent_result_repository
        assert isinstance(kwargs["runtime_router"], asgi.CanonicalRuntimeRouter)
        assert kwargs["owner_id"] == "test-canonical-owner::runtime::test-boot"
        assert kwargs["canonical_executor"] is canonical_executor
        assert kwargs["cancellation_authority_verifier"] is captured[
            "cancellation_authority_verifier"
        ]
        assert kwargs["runtime_context_application"] is runtime_context_application
        assert kwargs["action_preview_service"] is captured[
            "action_preview_service"
        ]
        assert kwargs["acceptance_effect_dispatcher"] is (
            acceptance_effect_dispatcher
        )
        events.append("internal-runtime-built")
        return internal_runtime

    def fake_build_operator_runtime(**kwargs):
        assert kwargs["task_runtime_container"] is task_runtime_container
        assert kwargs["ledger"] is ledger
        assert kwargs["token_service"] is token_service
        assert kwargs["intent_engine"] is intent_engine
        assert kwargs["runtime_context_application"] is runtime_context_application
        assert kwargs["action_preview_service"] is captured[
            "action_preview_service"
        ]
        assert kwargs["acceptance_effect_dispatcher"] is (
            acceptance_effect_dispatcher
        )
        assert kwargs["mode_transition_service_provider"] is (
            host_lifecycle.get_mode_transition_service
        )
        assert kwargs["cancellation_authority_verifier"] is captured[
            "cancellation_authority_verifier"
        ]
        assert kwargs["secure_ingress_authority_resolver"] is command_store
        assert kwargs["owner_id"] == (
            "test-canonical-owner::runtime::test-boot::operator"
        )
        events.append("operator-runtime-built")
        return operator_runtime

    def fake_build_operator_composition(**kwargs):
        captured["action_preview_service"] = kwargs["action_preview_service"]
        captured["cancellation_authority_verifier"] = kwargs[
            "cancellation_authority_verifier"
        ]
        captured["agent_runtime_ports"] = kwargs.pop("agent_runtime_ports")
        assert captured["agent_runtime_ports"] is (
            application.state.agent_runtime_application_ports
        )
        assert isinstance(
            kwargs["internal_agent_artifact_store"],
            asgi.AgentArtifactStore,
        )
        captured["internal_agent_artifact_store"] = kwargs[
            "internal_agent_artifact_store"
        ]
        assert kwargs["internal_agent_workspace_runtime"] is workspace_runtime
        assert callable(kwargs["life_receipt_reader_provider"])
        assert kwargs["life_projection_dispatcher"] is (
            life_projection_dispatcher
        )
        assert callable(kwargs["scheduler_command_service_provider"])
        assert kwargs == {
            "task_runtime_container": task_runtime_container,
            "ledger": ledger,
            "token_service": token_service,
            "intent_engine": intent_engine,
            "runtime_context_application": runtime_context_application,
            "action_preview_service": captured["action_preview_service"],
            "mode_transition_service_provider": (
                host_lifecycle.get_mode_transition_service
            ),
            "cancellation_authority_verifier": captured[
                "cancellation_authority_verifier"
            ],
            "secure_ingress_authority_resolver": command_store,
            "runtime_owner_id": "test-canonical-owner::runtime::test-boot",
            "scheduler_operation_handler": scheduler_operation_handler,
            "internal_agent_artifact_store": kwargs[
                "internal_agent_artifact_store"
            ],
            "internal_agent_workspace_runtime": workspace_runtime,
            "life_receipt_reader_provider": kwargs[
                "life_receipt_reader_provider"
            ],
            "life_projection_dispatcher": life_projection_dispatcher,
            "scheduler_command_service_provider": kwargs[
                "scheduler_command_service_provider"
            ],
            "embodiment_runtime_provider": kwargs[
                "embodiment_runtime_provider"
            ],
        }
        fake_build_acceptance_effect_dispatcher(
            task_store=canonical_task_store,
            handlers={
                "host.sys_mode_transition": object(),
                "scheduler.operation.execute": scheduler_operation_handler,
            },
            owner_id=(
                "test-canonical-owner::runtime::test-boot::acceptance-effects"
            ),
            action_preview_service=captured["action_preview_service"],
        )
        fake_build_operator_runtime(
            task_runtime_container=task_runtime_container,
            ledger=ledger,
            token_service=token_service,
            intent_engine=intent_engine,
            runtime_context_application=runtime_context_application,
            action_preview_service=captured["action_preview_service"],
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
            mode_transition_service_provider=(
                host_lifecycle.get_mode_transition_service
            ),
            cancellation_authority_verifier=captured[
                "cancellation_authority_verifier"
            ],
            secure_ingress_authority_resolver=command_store,
            owner_id="test-canonical-owner::runtime::test-boot::operator",
        )
        return SimpleNamespace(
            runtime=operator_runtime,
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
            acceptance_effect_runtime=acceptance_effect_runtime,
            review_command_service=review_commands,
        )

    def fake_build_command_ingress(**kwargs):
        assert kwargs["canonical_db_path"] == ledger.canonical_db_path
        events.append("command-ingress-built")
        return command_ingress_container

    def fake_build_memory(**kwargs):
        assert kwargs["ledger"] is ledger
        assert kwargs["canonical_db_path"] == ledger.canonical_db_path
        events.append("memory-built")
        return memory_container

    def fake_compose_workspace(
        application,
        task_runtime_arg,
        runtime_settings_arg,
        runtime_owner_arg,
    ):
        assert task_runtime_arg is task_runtime_container
        assert runtime_settings_arg.agent_workspace_managed_root == (
            "D:/runtime/agent-workspaces"
        )
        assert runtime_settings_arg.agent_workspace_lease_ms == 120_000
        assert runtime_settings_arg.agent_workspace_heartbeat_ms == 30_000
        assert runtime_settings_arg.agent_workspace_cleanup_poll_ms == 5_000
        assert (
            runtime_settings_arg.agent_workspace_hmac_secret.get_secret_value()
            == "w" * 32
        )
        assert runtime_owner_arg == "test-canonical-owner::runtime::test-boot"
        events.append("workspace-built")
        workspace_container.start()
        application.state.agent_workspace_container = workspace_container
        return workspace_container

    def fake_runtime_group(runtimes, **kwargs):
        assert runtimes == {
            "host": governed_runtime,
            "internal_agent": internal_runtime,
            "scheduler": scheduler_runtime,
            "operator": operator_runtime,
        }
        assert kwargs == {
            "review_store": task_runtime_container.review_continuation_store,
            "review_decision_store": task_runtime_container.review_store,
            "command_repository": task_runtime_container.command_repository,
            "claim_result_store": command_store,
            "operator_outcome_finalizer": obs_ingress,
            "review_continuation_worker_id": (
                "test-recovery::review-continuation"
            ),
            "graph_cancellation_operator_enabled": False,
            "graph_cancellation_state_reader": task_runtime_container.task_store,
            "acceptance_effect_runtime": acceptance_effect_runtime,
            "operation_reacquisition_owner_id": (
                "test-canonical-owner::runtime::test-boot"
            ),
        }
        return runtime_group

    def fake_build_scheduler(**kwargs):
        assert kwargs["task_runtime_container"] is task_runtime_container
        assert kwargs["ledger"] is ledger
        assert kwargs["evaluator"] is command_evaluator
        assert kwargs["claim_result_store"] is command_store
        assert kwargs["owner_id"] == "test-canonical-owner::runtime::test-boot"
        assert kwargs["cancellation_authority_verifier"] is captured[
            "cancellation_authority_verifier"
        ]
        assert kwargs["runtime_context_application"] is runtime_context_application
        assert kwargs["worker_registry"] is scheduler_worker_registry
        assert kwargs["acceptance_effect_dispatcher"] is (
            acceptance_effect_dispatcher
        )
        assert kwargs["current_mode"]() == "gaming"
        gate_result = kwargs["resource_gate"](
            SimpleNamespace(
                resource_profile={},
                task_id="test-scheduler-task",
                priority="LOW",
            ),
            "gaming",
        )
        assert gate_result == {"allowed": True, "source": "app-local"}
        events.append("scheduler-runtime-built")
        return scheduler_container

    action_preview_service = object()
    life_projection_dispatcher = object()
    life_outbox_dispatcher = object()

    def fake_start_runtime_context_services(**kwargs):
        assert kwargs["task_runtime"] is task_runtime_container
        assert kwargs["ledger"] is ledger
        assert kwargs["embodiment_runtime"] is embodiment_runtime
        assert kwargs["host_lifecycle"] is host_lifecycle
        environment = (
            host_runtime_context_wiring.runtime_environment_observation_provider(
                host_lifecycle.get_mode_transition_service()
            )(now_ms=1_900_000_000_000)
        )
        assert environment["activity_state"] == "GAMING"
        host_lifecycle.start_activity_monitor(ledger=ledger)
        events.append("runtime-context-built")
        events.append("guardian-startup-observed")
        return runtime_context_application, action_preview_service

    async def fake_guardian_heartbeat(*_args, **kwargs):
        assert kwargs["resource_manager_provider"]() is resource_manager
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("guardian-heartbeat-stopped")
            raise

    security_immune_agent = object()
    host_application_ports = _host_application_ports(
        lifecycle=host_lifecycle.ports,
        memory_worth_evaluator=object,
        security_immune_agent=lambda: security_immune_agent,
        token_service=lambda: token_service,
        bond_dynamics_writer=lambda: object(),
        capability_forge_service=lambda: object(),
        capability_gap_telemetry=lambda: object(),
        capability_scorecard_provider=lambda: object(),
        publish_memory_service_container=lambda value: events.append(
            "memory-published" if value is memory_container else "memory-publish-mismatch"
        ),
        withdraw_memory_service_container=lambda value: events.append(
            "memory-unpublished" if value is memory_container else "memory-withdraw-mismatch"
        ),
    )

    def build_host_lifecycle_from_explicit_ports(**kwargs):
        assert kwargs == {
            "initial_sys_mode": "normal",
            "ports": host_application_ports.lifecycle,
            "require_durable_hard_state": True,
        }
        return host_lifecycle

    monkeypatch.setattr(asgi, "create_host_fastapi_app", fake_app_factory)
    monkeypatch.setattr(
        asgi,
        "build_host_lifecycle_container",
        build_host_lifecycle_from_explicit_ports,
    )
    monkeypatch.setattr(
        asgi,
        "runtime_lifespan",
        lambda **kwargs: (
            kwargs["host_lifecycle"] is host_lifecycle
            and kwargs["defer_activity_monitor"] is True
            and kwargs["defer_resource_manager"] is True
            and base_lifespan()
        ),
    )
    monkeypatch.setattr(
        asgi,
        "QueryProjectionStore",
        lambda db_path: query_projection_store
        if db_path == ledger.canonical_db_path
        else pytest.fail("wrong query projection path"),
    )
    monkeypatch.setattr(
        asgi,
        "_compose_life_command_authority",
        lambda current: (object(), object())
        if current is ledger
        else pytest.fail("wrong Life ledger"),
    )
    monkeypatch.setattr(
        asgi,
        "_compose_life_dispatchers",
        lambda current, *, embodiment_runtime, action_preview_service, agent_runtime_ports: (
            life_projection_dispatcher,
            life_outbox_dispatcher,
        )
        if (
            current is ledger
            and embodiment_runtime is not None
            and action_preview_service is not None
            and agent_runtime_ports is not None
        )
        else pytest.fail("wrong Life dispatcher ledger"),
    )
    from host_brain.channels import embodiment as embodiment_module
    from host_brain.persistence import embodiment_outbox_repository as embodiment_repo_module

    monkeypatch.setattr(
        embodiment_repo_module,
        "EmbodimentOutboxRepository",
        lambda db_path: SimpleNamespace(canonical_db_path=db_path),
    )
    monkeypatch.setattr(
        embodiment_module,
        "HostEmbodimentRuntime",
        lambda **_kwargs: embodiment_runtime,
    )
    monkeypatch.setattr(
        embodiment_module,
        "build_embodiment_receipt_audit_observer",
        lambda _writer: object(),
    )
    from host_brain.app.observability_runtime import service_health_heartbeat

    monkeypatch.setattr(
        service_health_heartbeat,
        "heartbeat_loop",
        fake_guardian_heartbeat,
    )
    monkeypatch.setattr(
        asgi,
        "_start_host_runtime_context_services",
        fake_start_runtime_context_services,
    )
    monkeypatch.setattr(
        asgi,
        "_bind_host_hard_state_runtime",
        lambda **_kwargs: events.append("hard-state-journal-bound") or {},
    )
    monkeypatch.setattr(
        tool_runtime_composition,
        "DurableIntentAdmissionVerifier",
        lambda **kwargs: intent_admission_verifier
        if kwargs
        == {
            "authority_port": runtime_context_composition.intent_authority_port,
            "intent_arbiter": runtime_context_composition.intent_arbiter,
            "event_store": runtime_context_composition.event_store,
            "review_store": task_runtime_container.review_store,
        }
        else pytest.fail("wrong Intent admission composition"),
    )
    monkeypatch.setattr(
        tool_runtime_composition,
        "ApplicationModelAttemptServicePort",
        lambda provider: model_attempt_service
        if provider is host_lifecycle.ports.model_attempt_service
        else pytest.fail("wrong app-local model attempt provider"),
    )
    monkeypatch.setattr(
        asgi,
        "AgentRuntimeExecutor",
        lambda **kwargs: canonical_executor
        if kwargs == {
            "model_attempt_service": model_attempt_service,
            "prompt_registry": prompt_registry,
            "workspace_runtime": workspace_runtime,
            "artifact_store": captured["internal_agent_artifact_store"],
        }
        else pytest.fail("wrong canonical agent executor ports"),
    )
    monkeypatch.setattr(
        asgi,
        "get_core_settings",
        lambda: SimpleNamespace(
            sys_mode="normal",
            task_runtime_canonical_owner="test-canonical-owner",
            task_graph_cancellation_operator_enabled=False,
            task_operation_reconciliation_operator_enabled=True,
            task_effect_reconciliation_operator_enabled=True,
            task_terminal_reconciliation_operator_enabled=False,
            task_terminal_reconciliation_auto_enabled=False,
            task_runtime_bounded_fanout_enabled=False,
            task_runtime_atomic_graph_success_enabled=True,
            task_runtime_fanout_event_buffer_size=32,
            task_runtime_canary_public_keys=lambda: {},
            task_reconciliation_public_keys=lambda: {"reconciler-v1": b"k" * 32},
            agent_workspace_managed_root="D:/runtime/agent-workspaces",
            agent_workspace_lease_ms=120_000,
            agent_workspace_heartbeat_ms=30_000,
            agent_workspace_cleanup_poll_ms=5_000,
            agent_workspace_hmac_secret=SimpleNamespace(
                get_secret_value=lambda: "w" * 32
            ),
        ),
    )
    monkeypatch.setattr(
        tool_runtime_composition,
        "SecureIngressHttpAuthorityAdapter",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        tool_runtime_composition,
        "GovernedToolHttpApplication",
        lambda **_kwargs: governed_tool_application,
    )
    monkeypatch.setattr(
        asgi,
        "get_input_settings",
        lambda: SimpleNamespace(
            secure_ingress_mode="ENFORCE",
            secure_ingress_hmac_secrets_json="{}",
            edge_gateway_hmac_key_id="",
            edge_gateway_hmac_secret="",
        ),
    )
    monkeypatch.setattr(
        asgi,
        "get_scheduler_settings",
        lambda: SimpleNamespace(
            clock_key_id="",
            clock_hmac_secret=SimpleNamespace(get_secret_value=lambda: ""),
            clock_enabled=False,
            background_tasks_enabled=True,
            clock_poll_interval_seconds=1.0,
            clock_batch_limit=64,
        ),
    )
    monkeypatch.setattr(asgi, "build_scheduler_seed_configs", lambda **_kwargs: [])
    monkeypatch.setattr(
        asgi,
        "SchedulerWorkerRegistry",
        lambda: scheduler_worker_registry,
    )
    monkeypatch.setattr(
        asgi,
        "SchedulerAcceptedOperationHandler",
        lambda **kwargs: scheduler_operation_handler
        if kwargs
        == {
            "worker_registry": scheduler_worker_registry,
            "definition_repository": scheduler_definition_store,
            "occurrence_repository": scheduler_definition_store,
            "command_repository": canonical_command_repository,
            "result_repository": scheduler_result_store,
        }
        else pytest.fail("wrong Scheduler accepted-operation authority ports"),
    )
    monkeypatch.setattr(
        asgi,
        "build_process_security_service_container",
        fake_build_container,
    )
    monkeypatch.setattr(
        asgi,
        "build_process_task_runtime_container",
        fake_build_task_runtime,
    )
    monkeypatch.setattr(asgi, "build_process_canon_container", fake_build_canon)
    monkeypatch.setattr(
        tool_runtime_composition,
        "build_governed_host_runtime",
        fake_build_governed_runtime,
    )
    monkeypatch.setattr(
        asgi,
        "build_process_command_ingress_container",
        fake_build_command_ingress,
    )
    monkeypatch.setattr(asgi, "build_process_memory_container", fake_build_memory)
    monkeypatch.setattr(
        asgi.workspace,
        "compose_agent_workspace_application_scope",
        fake_compose_workspace,
    )
    monkeypatch.setattr(
        asgi,
        "build_governed_internal_runtime",
        fake_build_internal_runtime,
    )
    monkeypatch.setattr(
        asgi,
        "build_governed_operator_composition",
        fake_build_operator_composition,
    )
    monkeypatch.setattr(asgi, "GovernedRuntimeGroup", fake_runtime_group)
    monkeypatch.setattr(
        asgi,
        "build_process_scheduler_container",
        fake_build_scheduler,
    )
    monkeypatch.setattr(
        asgi,
        "InternalAgentIngress",
        lambda *, evaluator: internal_ingress
        if evaluator is command_evaluator
        else pytest.fail("wrong evaluator"),
    )
    monkeypatch.setattr(
        asgi,
        "InternalAgentCommandService",
        lambda *, runtime, intent_engine, claim_result_store: internal_service
        if runtime is internal_runtime and claim_result_store is command_store
        else pytest.fail("wrong internal runtime"),
    )

    application = asgi.create_app(host_application_ports=host_application_ports)
    lifespan = captured["lifespan"]
    assert application.state.task_runtime_container is None
    assert application.state.task_runtime_container_provider() is None
    assert application.state.host_lifecycle_container is host_lifecycle
    assert (
        application.state.lifecycle_runtime_ports.get_activity_monitor()
        is activity_monitor
    )

    async def scenario() -> None:
        async with lifespan(application):
            assert application.state.security_service_container is container
            assert (
                application.state.task_runtime_container
                is task_runtime_container
            )
            assert (
                application.state.task_runtime_container_provider()
                is task_runtime_container
            )
            assert application.state.governed_host_runtime is governed_runtime
            assert application.state.governed_internal_runtime is internal_runtime
            assert application.state.governed_scheduler_runtime is scheduler_runtime
            assert application.state.governed_operator_runtime is operator_runtime
            assert application.state.task_review_command_service is review_commands
            assert review_commands.wake is runtime_group.wake_review_continuation
            assert application.state.memory_service_container is memory_container
            assert application.state.agent_workspace_container is workspace_container
            assert application.state.canon_container is canon_container
            assert application.state.embodiment_runtime is embodiment_runtime
            assert (
                application.state.runtime_context_composition
                is runtime_context_composition
            )
            assert (
                application.state.task_operation_reconciliation_command_service
                is task_runtime_container.operation_reconciliation_service
            )
            assert (
                application.state.task_effect_reconciliation_command_service
                is task_runtime_container.effect_reconciliation_service
            )
            assert captured["canon_service_provider"]() is canon_application
            assert (
                captured["governed_command_application_provider"]()
                is governed_runtime
            )
            assert (
                captured["route_ports"].governed_tool_application_provider()
                is governed_tool_application
            )
            assert (
                captured["route_ports"].security_immune_agent_provider()
                is security_immune_agent
            )
            assert captured["obs_command_ingress_provider"]() is obs_ingress
            assert (
                captured["pentest_secure_run_composition_provider"]()
                is pentest_composition
            )
            assert (
                captured["signed_skill_provider_intake_provider"]()
                is signed_ingress
            )
            assert captured["internal_agent_ingress_provider"]() is internal_ingress
            assert (
                captured["internal_agent_command_service_provider"]()
                is internal_service
            )
            assert captured["scheduler_command_ingress_provider"]() is scheduler_ingress
            assert captured["scheduler_command_service_provider"]() is scheduler_service
            events.append("serving")
        assert application.state.security_service_container is None
        assert application.state.task_runtime_container is None
        assert application.state.task_runtime_container_provider() is None
        assert application.state.governed_host_runtime is None
        assert application.state.governed_internal_runtime is None
        assert application.state.governed_scheduler_runtime is None
        assert application.state.governed_operator_runtime is None
        assert application.state.runtime_context_composition is None
        assert application.state.agent_workspace_container is None
        assert application.state.task_operation_reconciliation_command_service is None
        assert application.state.task_effect_reconciliation_command_service is None
        assert captured["governed_command_application_provider"]() is None
        assert captured["route_ports"].governed_tool_application_provider() is None

    asyncio.run(scenario())

    assert resource_gate_calls == [
        {
            "task_id": "test-scheduler-task",
            "priority": "LOW",
            "task_type": "host_scheduler",
            "uses_llm": False,
            "uses_gpu": False,
            "reason": "governed_scheduler",
        }
    ]

    assert events == [
        "base-started",
        "container-built",
        "task-runtime-built",
        "embodiment-transport-fenced",
        "hard-state-journal-bound",
        "activity-monitor-started",
        "runtime-context-built",
        "guardian-startup-observed",
        "canon-built",
        "canon-synced",
        "command-ingress-built",
        "memory-built",
        "memory-published",
        "workspace-built",
        "workspace-started",
        "operator-runtime-built",
        "governed-runtime-built",
        "internal-runtime-built",
        "scheduler-runtime-built",
        "governed-runtime-bound",
        "governed-runtime-activated",
        "host-startup-graphs-recovered",
        "scheduler-clock-activated",
        "serving",
        "guardian-heartbeat-stopped",
        "obs-command-admission-drained",
        "scheduler-clock-stopped",
        "task-runtime-closed",
        "workspace-closed",
        "activity-monitor-stopped",
        "embodiment-heartbeat-stopped",
        "embodiment-runtime-closed",
        "memory-unpublished",
        "memory-closed",
        "command-ingress-closed",
        "container-closed",
        "base-closed",
    ]


def test_asgi_task_runtime_startup_failure_is_fail_closed_and_releases_security(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    events: list[str] = []
    ledger = SimpleNamespace(canonical_db_path="D:/runtime/canonical.sqlite3")

    class SecurityContainer:
        obs_command_composition = None
        pentest_secure_run_composition = None
        signed_skill_provider_intake_composition = None

        async def aclose(self) -> None:
            events.append("security-closed")

    @asynccontextmanager
    async def base_lifespan():
        events.append("base-started")
        try:
            yield
        finally:
            events.append("base-closed")

    def fake_app_factory(**kwargs):
        captured.update(kwargs)
        return FastAPI()

    def build_security(**_kwargs):
        events.append("security-built")
        return SecurityContainer()

    def fail_task_runtime(**_kwargs):
        events.append("task-runtime-build-failed")
        raise RuntimeError("canonical migration rejected")

    host_application_ports = _host_application_ports(
        lifecycle=_host_lifecycle_ports(
            ledger=lambda: ledger,
            life_snapshot_reader=lambda: object(),
            intent_engine=lambda: object(),
            prompt_registry=lambda: object(),
            bond_stage_engine=lambda: object(),
        ),
        token_service=object,
    )
    monkeypatch.setattr(asgi, "create_host_fastapi_app", fake_app_factory)
    monkeypatch.setattr(
        asgi,
        "runtime_lifespan",
        lambda **_kwargs: base_lifespan(),
    )
    monkeypatch.setattr(
        asgi,
        "_compose_life_command_authority",
        lambda current: (object(), object())
        if current is ledger
        else pytest.fail("wrong Life ledger"),
    )
    monkeypatch.setattr(
        asgi,
        "_compose_life_dispatchers",
        lambda current, **_kwargs: (object(), object())
        if current is ledger
        else pytest.fail("wrong Life dispatcher ledger"),
    )
    monkeypatch.setattr(
        asgi,
        "build_process_security_service_container",
        build_security,
    )
    monkeypatch.setattr(
        asgi,
        "build_process_task_runtime_container",
        fail_task_runtime,
    )

    application = asgi.create_app(host_application_ports=host_application_ports)
    lifespan = captured["lifespan"]

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="canonical migration rejected"):
            async with lifespan(application):
                events.append("serving")
        assert application.state.security_service_container is None
        assert application.state.task_runtime_container is None
        assert application.state.task_runtime_container_provider() is None

    asyncio.run(scenario())

    assert events == [
        "base-started",
        "security-built",
        "task-runtime-build-failed",
        "security-closed",
        "base-closed",
    ]


def test_host_obs_semantic_prewarm_failure_preserves_governance_sources(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    calls: list[str] = []
    governance_snapshot: dict[str, object] = {}
    ledger = SimpleNamespace(canonical_db_path=":memory:")

    class StopAfterGovernanceRead(RuntimeError):
        pass

    class Scorecards:
        def list_all(self):
            calls.append("scorecards-read")
            return []

    @asynccontextmanager
    async def base_lifespan():
        yield

    def fake_app_factory(**kwargs):
        captured.update(kwargs)
        return FastAPI()

    def fail_semantic_recaller():
        calls.append("semantic")
        raise RuntimeError("semantic source unavailable")

    def get_scorecards():
        calls.append("scorecards-prewarmed")
        return Scorecards()

    provider = lambda: object()
    host_lifecycle = SimpleNamespace(
        ports=_host_lifecycle_ports(
            memory_guard=provider,
            state_arbiter=provider,
            distillation_engine=provider,
            semantic_recaller=fail_semantic_recaller,
            episodic_store=provider,
            skill_registry=provider,
            tool_catalog=provider,
            capability_broker=provider,
            model_attempt_service=provider,
            ledger=lambda: ledger,
            life_snapshot_reader=provider,
            intent_engine=provider,
            prompt_registry=provider,
            bond_stage_engine=provider,
        ),
        get_hard_state=lambda: SimpleNamespace(sys_mode="normal"),
        get_endocrine_state=lambda **_kwargs: {},
        set_sys_mode=lambda _mode: {},
        get_activity_monitor=lambda **_kwargs: None,
        get_resource_manager=lambda **_kwargs: None,
        get_mode_transition_service=lambda: None,
        stop_activity_monitor=lambda **_kwargs: None,
        stop_resource_manager=lambda **_kwargs: None,
        notify_user_interaction=lambda: None,
    )

    def read_governance_then_stop(current_ledger):
        assert current_ledger is ledger
        ports = captured["host_obs_read_ports"]
        snapshot = ports.governance_source_reader()
        governance_snapshot.update(snapshot.governance)
        raise StopAfterGovernanceRead

    monkeypatch.setenv("PENTEST_WEB_LAB_MODE", "true")
    monkeypatch.setenv("PENTEST_ALLOW_EXPLOIT", "1")
    host_application_ports = _host_application_ports(
        lifecycle=host_lifecycle.ports,
        token_service=object,
        capability_gap_telemetry=object,
        capability_forge_service=object,
        capability_scorecard_provider=get_scorecards,
        publish_memory_service_container=lambda _value: None,
        withdraw_memory_service_container=lambda _value: None,
    )
    monkeypatch.setattr(asgi, "create_host_fastapi_app", fake_app_factory)
    monkeypatch.setattr(
        asgi,
        "build_host_lifecycle_container",
        lambda **_kwargs: host_lifecycle,
    )
    monkeypatch.setattr(
        asgi,
        "runtime_lifespan",
        lambda **_kwargs: base_lifespan(),
    )
    # This test stops at the Life-authority prewarm boundary; it is not a
    # Task Runtime migration test.  Keep the unit fixture independent from
    # the production-only durable-path requirement introduced by D8.
    class _TaskRuntimeFixture:
        canonical_db_path = ledger.canonical_db_path

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        asgi,
        "build_process_task_runtime_container",
        lambda **_kwargs: _TaskRuntimeFixture(),
    )
    monkeypatch.setattr(
        asgi,
        "build_process_security_service_container",
        lambda **_kwargs: None,
    )
    from host_brain.app import tribe_composition

    monkeypatch.setattr(
        tribe_composition,
        "build_production_tribe_runtime_scope",
        lambda: SimpleNamespace(
            state_registry=SimpleNamespace(snapshot=lambda: {}),
        ),
    )
    monkeypatch.setattr(
        tribe_composition,
        "build_production_tribe_runtime_agent_ports",
        lambda **_kwargs: TribeRuntimeAgentPorts(
            get_thalamus_agent=object,
            get_ans_agent=object,
            get_immune_agent=object,
        ),
    )
    monkeypatch.setattr(
        asgi,
        "_compose_life_command_authority",
        read_governance_then_stop,
    )
    monkeypatch.setattr(asgi, "_reset_life_command_authority", lambda: None)
    monkeypatch.setattr(
        asgi.workspace,
        "quiesce_agent_workspace_application_scope",
        lambda *_args: None,
    )

    application = asgi.create_app(host_application_ports=host_application_ports)
    lifespan = captured["lifespan"]

    async def scenario() -> None:
        with pytest.raises(StopAfterGovernanceRead):
            async with lifespan(application):
                pytest.fail("lifespan should stop after reading governance")

    asyncio.run(scenario())

    assert calls == [
        "semantic",
        "scorecards-prewarmed",
        "scorecards-read",
    ]
    assert governance_snapshot["capabilities"] == {
        "status": "AVAILABLE",
        "count": 0,
        "items": [],
        "automatic_upgrade_available": False,
        "read_only": True,
    }
    # Before the fenced RuntimeGuardian is composed, observability must stay
    # fail-closed instead of consulting the retired module-global authority.
    assert governance_snapshot["degradation"]["status"] == "UNAVAILABLE"
    assert governance_snapshot["degradation"]["high_risk_latched"] is True
    pentest = governance_snapshot["domain_isolation"]["pentest"]
    assert pentest["lab_mode"] is True
    assert pentest["allow_exploit_requested"] is True
    assert pentest["allow_exploit_effective"] is True
    assert pentest["high_risk_entrypoints_disabled"] is False
