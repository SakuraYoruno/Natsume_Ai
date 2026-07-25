"""Small application adapters used by the Host ASGI composition root."""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.app.host_life_scheduler_wiring import (
    LifeCommandServicePort,
    LifeSnapshotReaderPort,
    compose_life_command_authority,
    reset_life_command_authority,
    submit_life_tick_command,
)
from host_brain.app.host_runtime_startup_guards import (
    command_ingress_hmac_secrets,
)
from host_brain.app.settings import get_scheduler_settings
from host_brain.app.settings.input import get_input_settings
from host_brain.composition.life import (
    PROCESS_LIFE_PROVIDER,
    build_process_life_container,
)
from host_brain.core.ids import now_ms
from host_brain.runtime.context_application import (
    RuntimeContextApplicationService,
)


def compose_life_command_authority_for_host(
    ledger: Any,
) -> tuple[LifeCommandServicePort, LifeSnapshotReaderPort]:
    return compose_life_command_authority(
        ledger,
        container_builder=build_process_life_container,
        provider=PROCESS_LIFE_PROVIDER,
        clock_ms=now_ms,
    )


def reset_life_command_authority_for_host() -> None:
    reset_life_command_authority(provider=PROCESS_LIFE_PROVIDER)


def submit_life_tick_command_for_host(
    *,
    occurrence: Any,
    definition: Any,
    command: Any,
    execution_context: Mapping[str, Any],
    runtime_context_application: RuntimeContextApplicationService,
    life_command_service: LifeCommandServicePort,
    life_snapshot_reader: LifeSnapshotReaderPort,
) -> dict[str, Any]:
    return submit_life_tick_command(
        occurrence=occurrence,
        definition=definition,
        command=command,
        execution_context=execution_context,
        runtime_context_application=runtime_context_application,
        life_command_service=life_command_service,
        life_snapshot_reader=life_snapshot_reader,
        clock_ms=now_ms,
    )


def command_ingress_hmac_secrets_for_host() -> dict[str, str]:
    return command_ingress_hmac_secrets(
        input_settings=get_input_settings(),
        scheduler_settings=get_scheduler_settings(),
    )


__all__ = [
    "command_ingress_hmac_secrets_for_host",
    "compose_life_command_authority_for_host",
    "reset_life_command_authority_for_host",
    "submit_life_tick_command_for_host",
]
