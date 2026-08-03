from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from host_brain.app import (
    asgi,
    asgi_authority_adapters,
    host_life_scheduler_wiring,
    host_runtime_shutdown,
    http_hooks,
    internal_runtime_composition,
)
from host_brain.app.asgi_runtime_helpers import build_scheduler_seed_configs
from host_brain.app.host_runtime_context_wiring import (
    ServiceCatalogGuardianObservation,
)
from host_brain.scheduler import scheduling_center
from host_brain.tribe.tribe_state import TribeStateRegistry
from host_brain.runtime.context_contracts import SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2


def _life_tick_runtime_context() -> tuple[dict, object]:
    scene = SceneDecisionV2(
        workspace_id="tenant:life-tick-test",
        subject_id="person-1",
        scene_id="scene.casual-chat",
        revision=1,
        world_snapshot_digest="1" * 64,
        context_digest="2" * 64,
        guardian_snapshot_digest="3" * 64,
        constraints=(),
    )
    frozen = {
        "runtime_context_schema": "natsume.runtime-context.request.v1",
        "scene_decision_v2": scene.to_dict(),
        "scene_identity_v2": SceneIdentityV2.from_decision(scene).to_dict(),
    }
    authority = SimpleNamespace(
        require_frozen=lambda value: SimpleNamespace(
            to_dict=lambda: dict(frozen)
        )
        if dict(value).get("scene_identity_v2") == frozen["scene_identity_v2"]
        else (_ for _ in ()).throw(ValueError("scene drift"))
    )
    return {"frozen_input_pack": dict(frozen)}, authority


def test_production_startup_has_one_clock_and_no_private_life_or_tribe_loop() -> None:
    lifespan_source = inspect.getsource(http_hooks.runtime_lifespan)
    asgi_source = Path(asgi.__file__).read_text(encoding="utf-8")
    activation_source = inspect.getsource(
        internal_runtime_composition.activate_internal_runtime
    )
    shutdown_source = inspect.getsource(
        host_runtime_shutdown.shutdown_host_runtime_authority
    )

    assert activation_source.count("await scheduler.activate_clock()") == 1
    assert "start_life_continuity_loop" not in lifespan_source
    assert ".start_periodic_save(" not in lifespan_source
    assert ".load_snapshot(" not in lifespan_source
    assert "host_brain.runtime.life_continuity" not in shutdown_source
    assert "host_brain.tribe.tribe_state" not in shutdown_source
    assert asgi_source.index(
        "life_command_authority = _compose_life_command_authority(ledger)"
    ) < asgi_source.index("life_dispatchers = _compose_life_dispatchers(")
    assert shutdown_source.index("scheduler.stop_clock") < shutdown_source.index(
        '("life_command_authority", reset_life_command_authority)'
    ) < shutdown_source.rindex("on_dependencies_released,")
    assert asgi_source.index(
        "life_command_authority = life_dispatchers = None"
    ) < asgi_source.index("on_dependencies_released=withdraw_closed_process_references")


def test_default_tribe_registry_cannot_start_a_periodic_thread(monkeypatch) -> None:
    def unexpected_thread(*_args, **_kwargs):
        raise AssertionError("default Tribe registry must stay transient")

    monkeypatch.setattr("host_brain.tribe.tribe_state.threading.Thread", unexpected_thread)
    registry = TribeStateRegistry()

    registry.start_periodic_save()

    assert getattr(registry, "_periodic_thread", None) is None


def test_scheduler_seed_has_exactly_one_life_tick_before_repository_registration(
    monkeypatch,
) -> None:
    settings = SimpleNamespace(
        seed_builtin_definitions=True,
        background_tasks_enabled=True,
        distillation_interval_seconds=300,
        distillation_enabled=False,
        agent_immune_interval_seconds=300,
        agent_immune_enabled=False,
    )
    life_tick = scheduling_center.build_life_tick_task_config()
    monkeypatch.setattr(asgi, "get_scheduler_settings", lambda: settings)
    monkeypatch.setattr(
        scheduling_center,
        "build_personality_scheduler_seed_task_configs",
        lambda: [life_tick],
    )
    configs = build_scheduler_seed_configs(
        settings=settings,
        security_immune_agent_provider=lambda: (
            _ for _ in ()
        ).throw(RuntimeError("disabled in unit test")),
        logger=asgi.logger,
    )

    assert [config.task_id for config in configs].count("life_tick") == 1
    assert "life_io_dispatch_periodic" not in [config.task_id for config in configs]
    assert "embodiment_outbox_periodic" not in [config.task_id for config in configs]


def test_life_tick_handler_only_reads_version_and_submits_one_typed_command(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        asgi_authority_adapters,
        "now_ms",
        lambda: 1_900_000_000_123,
    )
    class Reader:
        def get_receipt(self, command_id: str):
            assert command_id == "life_tick::scheduleroccurrence://sha256/abc"
            return None

        def get_snapshot(self, person_id: str):
            assert person_id == "person-1"
            return SimpleNamespace(version=7, state={"person_id": person_id, "version": 7})

    class Service:
        def __init__(self, reader: Reader) -> None:
            self.reader = reader
            self.commands = []

        def submit_next(self, person_id: str, command_factory):
            command = command_factory(self.reader.get_snapshot(person_id))
            self.commands.append(command)
            return {"status": "APPLIED", "command_id": command.command_id}

    occurrence = SimpleNamespace(
        occurrence_id="scheduleroccurrence://sha256/abc",
        task_id="life_tick",
        definition_revision=3,
        scheduled_at_ms=1_900_000_000_000,
        payload={"person_id": "person-1"},
        principal_id="scheduler-clock",
    )
    definition = SimpleNamespace(
        intent_template={"skill_name": "life_tick", "args": {"person_id": "default"}}
    )
    command = SimpleNamespace(
        command_id="host-command-1",
        request_id="scheduler-request-1",
        principal_id="scheduler-clock",
        governor_grant_ref="governor-decision://sha256/" + "a" * 64,
    )
    reader = Reader()
    service = Service(reader)
    execution_context, runtime_context_application = _life_tick_runtime_context()

    result = asgi._submit_life_tick_command(
        occurrence=occurrence,
        definition=definition,
        command=command,
        execution_context=execution_context,
        runtime_context_application=runtime_context_application,
        life_command_service=service,
        life_snapshot_reader=reader,
    )

    assert result["status"] == "SUCCEEDED"
    assert len(service.commands) == 1
    life_command = service.commands[0]
    assert life_command.command_type == "tick"
    assert life_command.person_id == "person-1"
    assert life_command.expected_version == 7
    assert life_command.created_at_ms == 1_900_000_000_123
    assert life_command.payload["scheduled_at_ms"] == occurrence.scheduled_at_ms
    assert life_command.causation_id == occurrence.occurrence_id
    assert life_command.payload["source"] == "scheduler_clock"
    assert life_command.payload["occurrence_payload"]["delivery_authorization"] == {
        "request_id": "scheduler-request-1",
        "governor_grant_ref": "governor-decision://sha256/" + "a" * 64,
        "authority_kind": "scheduler_host_command",
        "authority_principal_id": "scheduler-clock",
    }

    handler_source = inspect.getsource(
        host_life_scheduler_wiring.submit_life_tick_command
    )
    assert "LifeCommand(" in handler_source
    assert handler_source.count("life_command_service.submit_next(") == 1
    assert handler_source.index("get_receipt(") < handler_source.index("submit_next(")
    assert "life_continuity" not in handler_source
    assert "life_organs" not in handler_source


def test_life_tick_crash_gap_replays_receipt_without_a_second_command() -> None:
    receipt = {
        "status": "APPLIED",
        "command_id": "life_tick::scheduleroccurrence://sha256/crash-gap",
        "state_version": 8,
    }

    class Reader:
        def __init__(self) -> None:
            self.receipt = None

        def get_receipt(self, command_id: str):
            assert command_id == receipt["command_id"]
            return self.receipt

        def get_snapshot(self, person_id: str):
            assert self.receipt is None, "replay must not derive a new expected_version"
            assert person_id == "person-1"
            return SimpleNamespace(version=7, state={"person_id": person_id, "version": 7})

    class Service:
        def __init__(self, reader: Reader) -> None:
            self.reader = reader
            self.submit_count = 0

        def submit_next(self, person_id: str, command_factory):
            self.submit_count += 1
            command_factory(self.reader.get_snapshot(person_id))
            self.reader.receipt = dict(receipt)
            return dict(receipt)

    occurrence = SimpleNamespace(
        occurrence_id="scheduleroccurrence://sha256/crash-gap",
        task_id="life_tick",
        definition_revision=1,
        scheduled_at_ms=1_900_000_000_000,
        payload={"person_id": "person-1"},
        principal_id="scheduler-clock",
    )
    definition = SimpleNamespace(
        intent_template={"skill_name": "life_tick", "args": {"person_id": "default"}}
    )
    command = SimpleNamespace(command_id="host-command-1", request_id="request-1")
    reader = Reader()
    service = Service(reader)
    execution_context, runtime_context_application = _life_tick_runtime_context()

    first = asgi._submit_life_tick_command(
        occurrence=occurrence,
        definition=definition,
        command=command,
        execution_context=execution_context,
        runtime_context_application=runtime_context_application,
        life_command_service=service,
        life_snapshot_reader=reader,
    )
    replayed = asgi._submit_life_tick_command(
        occurrence=occurrence,
        definition=definition,
        command=command,
        execution_context=execution_context,
        runtime_context_application=runtime_context_application,
        life_command_service=service,
        life_snapshot_reader=reader,
    )

    assert first["status"] == "SUCCEEDED"
    assert replayed["status"] == "SUCCEEDED"
    assert replayed["replayed"] is True
    assert service.submit_count == 1


def test_life_tick_outer_receipt_reconciliation_is_read_only() -> None:
    receipt = {
        "status": "APPLIED",
        "command_id": "life_tick::scheduleroccurrence://sha256/outer-gap",
        "state_version": 9,
    }

    class Reader:
        def get_receipt(self, command_id: str):
            assert command_id == receipt["command_id"]
            return dict(receipt)

        def get_snapshot(self, _person_id: str):
            raise AssertionError("read-only reconciliation must not read a snapshot")

    result = host_life_scheduler_wiring.reconcile_life_tick_command(
        occurrence=SimpleNamespace(
            occurrence_id="scheduleroccurrence://sha256/outer-gap"
        ),
        definition=object(),
        command=object(),
        execution_context={},
        life_snapshot_reader=Reader(),
    )

    assert result["status"] == "SUCCEEDED"
    assert result["replayed"] is True
    assert result["command_id"] == receipt["command_id"]


def test_life_command_authority_adopts_legacy_projections_before_return(
    monkeypatch,
) -> None:
    from host_brain.composition.life import ProcessLifeContainer

    events: list[object] = []
    ledger = object()
    repository = object()
    service = object()
    reader = object()
    container = ProcessLifeContainer(
        repository=repository,  # type: ignore[arg-type]
        command_service=service,  # type: ignore[arg-type]
        snapshot_reader=reader,  # type: ignore[arg-type]
    )
    provider = SimpleNamespace(install=lambda current: events.append(("install", current)))

    def build_container(*, ledger: object, adopted_at_ms: int) -> ProcessLifeContainer:
        events.append(("build", ledger, adopted_at_ms))
        return container

    monkeypatch.setattr(
        asgi_authority_adapters,
        "build_process_life_container",
        build_container,
    )
    monkeypatch.setattr(
        asgi_authority_adapters,
        "PROCESS_LIFE_PROVIDER",
        provider,
    )
    monkeypatch.setattr(
        asgi_authority_adapters,
        "now_ms",
        lambda: 1_900_000_000_123,
    )

    resolved_service, resolved_reader = asgi._compose_life_command_authority(ledger)

    assert resolved_service is service
    assert resolved_reader is reader
    assert events == [
        ("build", ledger, 1_900_000_000_123),
        ("install", container),
    ]


def test_life_command_authority_shutdown_resets_shared_app_providers(
    monkeypatch,
) -> None:
    calls = []
    provider = SimpleNamespace(clear=lambda: calls.append("clear"))
    monkeypatch.setattr(
        asgi_authority_adapters,
        "PROCESS_LIFE_PROVIDER",
        provider,
    )

    asgi._reset_life_command_authority()

    assert calls == ["clear"]


def test_life_periodic_side_effect_definitions_are_retired() -> None:
    task_ids = scheduling_center.scheduler_seed_task_ids()

    assert task_ids.count("life_tick") == 1
    assert {
        "proactive_talk_periodic",
        "inner_world_numeric_tick",
        "inner_world_monologue_tick",
        "silence_tick",
        "body_sense_tick",
        "emotion_decay_tick",
    }.isdisjoint(task_ids)


def test_life_io_is_a_lightweight_app_local_projection_drain() -> None:
    source = Path(asgi.__file__).read_text(encoding="utf-8")
    handler = source.split("async def life_io_scheduler_handler", 1)[1].split(
        "def scheduler_current_mode", 1
    )[0]

    assert 'name="host-life-io-dispatch-pump"' in source
    assert 'name="host-embodiment-io-dispatch-pump"' in source
    assert '"life_io_dispatch_periodic": "life_io"' not in source
    assert '"embodiment_outbox_periodic": "embodiment_io"' not in source
    assert "projection_dispatcher.dispatch_pending_async" in handler
    assert "outbox_dispatcher.dispatch_pending_async" in handler
    assert "LifeCommand(" not in handler
    assert "submit_next(" not in handler
    assert "build_life_tick_payload" not in handler


def test_scheduler_clock_is_not_disabled_with_optional_background_work() -> None:
    source = Path(asgi.__file__).read_text(encoding="utf-8")

    assert "scheduler_clock_enabled = bool(scheduler_settings.clock_enabled)" in source
    assert (
        "scheduler_settings.clock_enabled\n"
        "                    and scheduler_settings.background_tasks_enabled"
    ) not in source


def test_catalog_candidate_opens_scheduler_only_after_started_receipt() -> None:
    events: list[str] = []

    class Store:
        def terminal_receipt(self, operation_id: str) -> dict[str, str]:
            assert operation_id == "catalog-operation-1"
            return {"event_type": "ACTIVATION_STARTED"}

    observation = ServiceCatalogGuardianObservation(
        store=Store(),
        observer=SimpleNamespace(
            observe_operation=lambda operation_id: events.append(
                f"observed:{operation_id}"
            )
        ),
        operation_id="catalog-operation-1",
        logger=SimpleNamespace(error=lambda *_args, **_kwargs: None),
    )

    async def activate_clock() -> None:
        events.append("clock-activated")

    import asyncio

    asyncio.run(
        observation.observe_terminal_receipt(
            on_activation_started=activate_clock,
        )
    )

    assert events == ["observed:catalog-operation-1", "clock-activated"]


def test_catalog_candidate_does_not_open_scheduler_after_rollback() -> None:
    events: list[str] = []

    class Store:
        def terminal_receipt(self, operation_id: str) -> dict[str, str]:
            assert operation_id == "catalog-operation-2"
            return {"event_type": "ACTIVATION_ROLLED_BACK"}

    observation = ServiceCatalogGuardianObservation(
        store=Store(),
        observer=SimpleNamespace(
            observe_operation=lambda operation_id: events.append(
                f"observed:{operation_id}"
            )
        ),
        operation_id="catalog-operation-2",
        logger=SimpleNamespace(error=lambda *_args, **_kwargs: None),
    )

    async def activate_clock() -> None:
        events.append("clock-activated")

    import asyncio

    asyncio.run(
        observation.observe_terminal_receipt(
            on_activation_started=activate_clock,
        )
    )

    assert events == ["observed:catalog-operation-2"]
