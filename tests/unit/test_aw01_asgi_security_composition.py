from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from host_brain.app import asgi


def test_scheduler_clock_secret_is_added_only_under_explicit_key(monkeypatch) -> None:
    monkeypatch.setattr(
        asgi,
        "get_input_settings",
        lambda: SimpleNamespace(
            secure_ingress_hmac_secrets_json='{"external.key":"external-secret"}',
            edge_gateway_hmac_key_id="",
            edge_gateway_hmac_secret="",
        ),
    )
    monkeypatch.setattr(
        asgi,
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
            command_ingress=SimpleNamespace(mode="ENFORCE"),
            security=SimpleNamespace(obs_command_composition=composition),
        )


def test_graph_cancellation_operator_accepts_complete_startup_authority() -> None:
    asgi._assert_graph_cancellation_operator_startup(
        enabled=True,
        operation_reconciliation_enabled=True,
        effect_reconciliation_enabled=True,
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


def test_cancellation_production_gate_checks_only_when_cancellation_is_enabled() -> None:
    asgi._assert_graph_cancellation_effect_reconciliation_production_ready(
        enabled=False,
        task_store=None,
    )
    with pytest.raises(
        RuntimeError,
        match="TASK_GRAPH_CANCELLATION_EFFECT_PRODUCTION_GATE_UNAVAILABLE",
    ):
        asgi._assert_graph_cancellation_effect_reconciliation_production_ready(
            enabled=True,
            task_store=object(),
        )

    calls: list[str] = []
    store = SimpleNamespace(
        assert_effect_reconciliation_production_ready=(
            lambda: calls.append("production-ready")
        )
    )
    asgi._assert_graph_cancellation_effect_reconciliation_production_ready(
        enabled=True,
        task_store=store,
    )
    assert calls == ["production-ready"]


def test_asgi_builds_publishes_injects_and_closes_security_container(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    events: list[str] = []
    ledger = SimpleNamespace(
        canonical_db_path="D:/runtime/canonical.sqlite3",
        log_voice_event=lambda *_args, **_kwargs: None,
    )
    token_service = object()
    capability_broker = object()
    model_attempt_service = object()
    prompt_registry = object()
    canonical_executor = object()
    obs_ingress = SimpleNamespace(execute=lambda *_args, **_kwargs: None)
    pentest_composition = object()
    signed_ingress = object()
    skill_registry = object()

    class Container:
        obs_command_composition = SimpleNamespace(
            enabled=True,
            execution_enabled=True,
            ingress=obs_ingress,
        )
        pentest_secure_run_composition = pentest_composition
        signed_skill_provider_intake_composition = SimpleNamespace(
            enabled=True,
            ingress=signed_ingress,
        )

        async def aclose(self) -> None:
            assert captured["obs_command_ingress_provider"]() is None
            assert captured["pentest_secure_run_composition_provider"]() is None
            assert captured["signed_skill_provider_intake_provider"]() is None
            events.append("container-closed")

    container = Container()

    governed_runtime = object()
    internal_runtime = object()
    scheduler_runtime = object()
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
        command_repository = object()
        task_store = object()
        recovery_id = "test-recovery"
        operation_reconciliation_service = object()
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
            assert application.state.task_runtime_container is None
            assert application.state.task_runtime_container_provider() is None
            events.append("task-runtime-closed")

    task_runtime_container = TaskRuntimeContainer()

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
        assert kwargs == {
            "ledger": ledger,
            "canonical_db_path": ledger.canonical_db_path,
            "token_service": token_service,
        }
        events.append("container-built")
        return container

    def fake_build_task_runtime(**kwargs):
        assert kwargs == {
            "ledger": ledger,
            "canonical_db_path": ledger.canonical_db_path,
            "reconciliation_public_keys": {"reconciler-v1": b"k" * 32},
        }
        events.append("task-runtime-built")
        return task_runtime_container

    def fake_build_canon(**kwargs):
        assert kwargs["ledger"] is ledger
        assert kwargs["canonical_db_path"] == ledger.canonical_db_path
        events.append("canon-built")
        return canon_container

    def fake_build_governed_runtime(**kwargs):
        assert kwargs["task_runtime_container"] is task_runtime_container
        assert kwargs["ledger"] is ledger
        assert kwargs["skill_registry"] is skill_registry
        assert kwargs["token_service"] is token_service
        assert kwargs["capability_broker"] is capability_broker
        assert kwargs["model_attempt_service"] is model_attempt_service
        assert kwargs["production_authority_verifier"] is asgi.verify_durable_production_authority
        captured["cancellation_authority_verifier"] = kwargs[
            "cancellation_authority_verifier"
        ]
        assert isinstance(kwargs["runtime_router"], asgi.CanonicalRuntimeRouter)
        assert kwargs["owner_id"] == "test-canonical-owner::runtime::test-boot"
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
        events.append("internal-runtime-built")
        return internal_runtime

    def fake_build_command_ingress(**kwargs):
        assert kwargs["canonical_db_path"] == ledger.canonical_db_path
        events.append("command-ingress-built")
        return command_ingress_container

    def fake_build_memory(**kwargs):
        assert kwargs["ledger"] is ledger
        assert kwargs["canonical_db_path"] == ledger.canonical_db_path
        events.append("memory-built")
        return memory_container

    def fake_runtime_group(runtimes, **kwargs):
        assert runtimes == {
            "host": governed_runtime,
            "internal_agent": internal_runtime,
            "scheduler": scheduler_runtime,
        }
        assert kwargs == {
            "review_store": task_runtime_container.review_continuation_store,
            "review_decision_store": task_runtime_container.review_store,
            "command_repository": task_runtime_container.command_repository,
            "claim_result_store": command_store,
            "review_continuation_worker_id": (
                "test-recovery::review-continuation"
            ),
            "graph_cancellation_operator_enabled": False,
            "graph_cancellation_state_reader": task_runtime_container.task_store,
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
        events.append("scheduler-runtime-built")
        return scheduler_container

    monkeypatch.setattr(asgi, "create_host_fastapi_app", fake_app_factory)
    monkeypatch.setattr(
        asgi,
        "runtime_lifespan",
        lambda **_kwargs: base_lifespan(),
    )
    monkeypatch.setattr(asgi, "get_ledger", lambda: ledger)
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
        lambda current: (object(), object())
        if current is ledger
        else pytest.fail("wrong Life dispatcher ledger"),
    )
    monkeypatch.setattr(asgi, "get_token_service", lambda: token_service)
    monkeypatch.setattr(asgi, "get_capability_broker", lambda: capability_broker)
    monkeypatch.setattr(
        asgi,
        "ApplicationModelAttemptServicePort",
        lambda: model_attempt_service,
    )
    monkeypatch.setattr(asgi, "get_model_attempt_service", lambda: model_attempt_service)
    monkeypatch.setattr(asgi, "get_prompt_registry", lambda: prompt_registry)
    monkeypatch.setattr(
        asgi,
        "AgentRuntimeExecutor",
        lambda **kwargs: canonical_executor
        if kwargs == {
            "model_attempt_service": model_attempt_service,
            "prompt_registry": prompt_registry,
        }
        else pytest.fail("wrong canonical agent executor ports"),
    )
    monkeypatch.setattr(asgi, "get_skill_registry", lambda: skill_registry)
    monkeypatch.setattr(
        asgi,
        "get_core_settings",
        lambda: SimpleNamespace(
            task_runtime_canonical_owner="test-canonical-owner",
            task_graph_cancellation_operator_enabled=False,
            task_operation_reconciliation_operator_enabled=True,
            task_effect_reconciliation_operator_enabled=True,
            task_reconciliation_public_keys=lambda: {"reconciler-v1": b"k" * 32},
        ),
    )
    monkeypatch.setattr(asgi, "get_intent_engine", object)
    monkeypatch.setattr(asgi, "get_episodic_store", object)
    monkeypatch.setattr(asgi, "get_memory_guard", object)
    monkeypatch.setattr(asgi, "get_memory_worth_evaluator", object)
    monkeypatch.setattr(
        asgi,
        "set_memory_service_container",
        lambda value: events.append(
            "memory-published" if value is memory_container else "memory-unpublished"
        ),
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
    monkeypatch.setattr(asgi, "_scheduler_seed_configs", lambda: [])
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
        asgi,
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
        asgi,
        "build_governed_internal_runtime",
        fake_build_internal_runtime,
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

    application = asgi.create_app()
    lifespan = captured["lifespan"]
    assert application.state.task_runtime_container is None
    assert application.state.task_runtime_container_provider() is None

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
            assert application.state.memory_service_container is memory_container
            assert application.state.canon_container is canon_container
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
        assert application.state.task_operation_reconciliation_command_service is None
        assert application.state.task_effect_reconciliation_command_service is None
        assert captured["governed_command_application_provider"]() is None

    asyncio.run(scenario())

    assert events == [
        "base-started",
        "container-built",
        "task-runtime-built",
        "canon-built",
        "canon-synced",
        "command-ingress-built",
        "memory-built",
        "memory-published",
        "governed-runtime-built",
        "internal-runtime-built",
        "scheduler-runtime-built",
        "governed-runtime-bound",
        "governed-runtime-activated",
        "scheduler-clock-activated",
        "serving",
        "scheduler-clock-stopped",
        "task-runtime-closed",
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

    monkeypatch.setattr(asgi, "create_host_fastapi_app", fake_app_factory)
    monkeypatch.setattr(
        asgi,
        "runtime_lifespan",
        lambda **_kwargs: base_lifespan(),
    )
    monkeypatch.setattr(asgi, "get_ledger", lambda: ledger)
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
        lambda current: (object(), object())
        if current is ledger
        else pytest.fail("wrong Life dispatcher ledger"),
    )
    monkeypatch.setattr(asgi, "get_token_service", object)
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

    application = asgi.create_app()
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
