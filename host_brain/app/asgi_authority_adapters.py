"""Host ASGI 组合根使用的小型应用 authority 适配器。"""

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
from host_brain.app.settings.scheduler import get_scheduler_settings
from host_brain.input_runtime.settings import get_input_settings
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
    """以 Host ledger 构造并安装本进程的 Life command/snapshot 成对 authority。"""
    return compose_life_command_authority(
        ledger,
        container_builder=build_process_life_container,
        provider=PROCESS_LIFE_PROVIDER,
        clock_ms=now_ms,
    )


def reset_life_command_authority_for_host() -> None:
    """在 Host 生命周期结束时撤销已安装 Life container，防止旧 authority 跨 epoch 存活。"""
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
    """把 scheduler tick 委托给规范 Life 接线；保留冻结执行上下文和时钟来源。"""
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
    """从 Host 配置解析命令入口密钥映射；调用方不得记录返回的 secret。"""
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
