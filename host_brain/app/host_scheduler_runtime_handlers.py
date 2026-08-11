"""App-local Scheduler handlers and infrastructure pump lifecycle for Host."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any

from host_brain.app import host_life_scheduler_wiring as scheduler_result_wiring
from host_brain.app.asgi_authority_adapters import (
    reconcile_life_tick_command_for_host,
    submit_life_tick_command_for_host,
)
from host_brain.app.startup import collect_dependency_probes
from host_brain.lifecycle_runtime.proactive_dispatch import (
    run_proactive_intent_dispatch,
)


class HostSchedulerRuntimeHandlers:
    """Own Scheduler call adapters without owning durable Scheduler truth."""

    def __init__(
        self,
        *,
        application: Any,
        application_ports: Any,
        host_services: Any,
        host_lifecycle: Any,
        lifecycle_runtime_ports: Any,
        ledger: Any,
        ledger_provider: Callable[[], Any],
        life_command_authority_provider: Callable[[Any], tuple[Any, Any]],
        life_snapshot_reader_provider: Callable[[], Any],
        life_dispatchers: tuple[Any, Any] | None,
        embodiment_runtime: Any | None,
        runtime_context_application: Any,
        runtime_context_composition: Any,
        action_preview_service: Any,
        logger: Any,
    ) -> None:
        self._application = application
        self._application_ports = application_ports
        self._host_services = host_services
        self._host_lifecycle = host_lifecycle
        self._lifecycle_runtime_ports = lifecycle_runtime_ports
        self._ledger = ledger
        self._ledger_provider = ledger_provider
        self._life_command_authority_provider = life_command_authority_provider
        self._life_snapshot_reader_provider = life_snapshot_reader_provider
        self._life_dispatchers = life_dispatchers
        self._embodiment_runtime = embodiment_runtime
        self._runtime_context_application = runtime_context_application
        self._action_preview_service = action_preview_service
        self._logger = logger
        self._infrastructure_tasks: tuple[asyncio.Task[None], ...] = ()
        try:
            from host_brain.app.vision.proactive_push_client import (  # noqa: PLC0415
                build_proactive_push_client,
            )

            self._proactive_client = build_proactive_push_client()
            self._proactive_push_detail = ""
        except Exception as exc:
            self._proactive_client = None
            self._proactive_push_detail = str(exc)[:300]
            logger.warning("SCHEDULER_PROACTIVE_PUSH_DEGRADED:%s", exc)
        self.agent_immune = (
            scheduler_result_wiring.build_agent_immune_scheduler_handler(
                degradation_state_provider=(
                    runtime_context_composition.guardian.read_degradation_state
                    if runtime_context_composition is not None
                    else None
                ),
                ledger_provider=lambda: self._ledger,
                dependency_probe_provider=collect_dependency_probes,
                workspace_cleanup_health_provider_resolver=lambda: getattr(
                    self._application.state,
                    "health_workspace_cleanup_provider",
                    None,
                ),
            )
        )

    @property
    def retired_task_ids(self) -> tuple[str, ...]:
        return scheduler_result_wiring.RETIRED_LEGACY_SCHEDULER_TASK_IDS

    @property
    def task_handlers(self) -> Mapping[str, Callable[..., Any]]:
        return {
            "life_command": self.life_tick,
            "life_io": self.life_io,
            "embodiment_io": self.embodiment_io,
            "distillation_engine": self.distillation,
            "security_immune": self.security_immune,
            "agent_immune": self.agent_immune,
        }

    @property
    def task_reconcilers(self) -> Mapping[str, Callable[..., Any]]:
        return {"life_command": self.reconcile_life_tick}

    def _push_proactive(
        self,
        message: dict[str, Any],
        *,
        preview_proof: Any,
    ) -> dict[str, Any]:
        if self._proactive_client is None:
            return {
                "status": "SKIPPED",
                "success": False,
                "detail": self._proactive_push_detail,
            }
        return dict(
            self._proactive_client.push(
                message,
                preview_proof=preview_proof,
            )
            or {}
        )

    async def proactive(
        self,
        *,
        occurrence: Any,
        definition: Any,
        command: Any,
        execution_context: Any,
    ) -> dict[str, Any]:
        intent = dict(definition.intent_template)
        args = dict(intent.get("args") or {})
        args.update(dict(occurrence.payload or {}))
        intent["args"] = args
        intent["request_id"] = command.request_id
        result = await asyncio.to_thread(
            run_proactive_intent_dispatch,
            intent,
            ports=self._lifecycle_runtime_ports,
            logger=self._logger,
            get_ledger=self._ledger_provider,
            life_snapshot_reader=self._life_snapshot_reader_provider,
            retention_policy=None,
            ui_pusher=self._push_proactive,
            execution_context=execution_context,
            runtime_context_application=self._runtime_context_application,
            action_preview_service=self._action_preview_service,
        )
        return scheduler_result_wiring.proactive_scheduler_handler_result(result)

    async def distillation(self, **_: Any) -> dict[str, Any]:
        report = await self._host_services.distillation_engine().run_batch()
        return scheduler_result_wiring.distillation_scheduler_handler_result(report)

    async def security_immune(self, **_: Any) -> dict[str, Any]:
        result = await self._application_ports.security_immune_agent().run_once(
            reason="scheduled"
        )
        return scheduler_result_wiring.security_immune_scheduler_handler_result(
            result
        )

    async def life_tick(
        self,
        *,
        occurrence: Any,
        definition: Any,
        command: Any,
        execution_context: Any,
    ) -> dict[str, Any]:
        life_command_service, life_snapshot_reader = (
            self._life_command_authority_provider(self._ledger)
        )
        return await asyncio.to_thread(
            submit_life_tick_command_for_host,
            occurrence=occurrence,
            definition=definition,
            command=command,
            execution_context=execution_context,
            runtime_context_application=self._runtime_context_application,
            life_command_service=life_command_service,
            life_snapshot_reader=life_snapshot_reader,
        )

    async def reconcile_life_tick(
        self,
        *,
        occurrence: Any,
        definition: Any,
        command: Any,
        execution_context: Any,
    ) -> dict[str, Any]:
        _life_service, life_snapshot_reader = self._life_command_authority_provider(
            self._ledger
        )
        return await asyncio.to_thread(
            reconcile_life_tick_command_for_host,
            occurrence=occurrence,
            definition=definition,
            command=command,
            execution_context=execution_context,
            life_snapshot_reader=life_snapshot_reader,
        )

    async def life_io(self, **_: Any) -> dict[str, Any]:
        if self._life_dispatchers is None:
            return {
                "status": "FAILED",
                "reason_code": "LIFE_DISPATCHERS_UNAVAILABLE",
            }
        projection_dispatcher, outbox_dispatcher = self._life_dispatchers
        projection = await projection_dispatcher.dispatch_pending_async(limit=100)
        delivery = await outbox_dispatcher.dispatch_pending_async(limit=100)
        failed = int(projection.failed) + int(delivery.failed)
        return {
            "status": "SUCCEEDED" if failed == 0 else "DEGRADED",
            "reason_code": (
                None if failed == 0 else "LIFE_IO_DISPATCH_PARTIAL_FAILURE"
            ),
            "projection": projection.as_dict(),
            "delivery": delivery.as_dict(),
        }

    async def embodiment_io(self, **_: Any) -> dict[str, Any]:
        runtime = self._embodiment_runtime
        if runtime is None:
            return {
                "status": "FAILED",
                "reason_code": "EMBODIMENT_DISPATCHER_UNAVAILABLE",
            }
        dispatch_error: Exception | None = None
        try:
            batch = await runtime.dispatch_pending_async(limit=100)
            dispatch = batch.as_dict()
        except Exception as exc:
            dispatch_error = exc
            dispatch = {
                "status": "DEGRADED",
                "reason_code": "EMBODIMENT_DISPATCH_UNAVAILABLE",
            }
        reconciliation = await runtime.reconcile_in_doubt_async(limit=100)
        result = {
            "status": (
                "SUCCEEDED"
                if dispatch.get("status") == "SUCCEEDED"
                and reconciliation.as_dict().get("status") == "SUCCEEDED"
                else "DEGRADED"
            ),
            "dispatch": dispatch,
            "reconciliation": reconciliation.as_dict(),
        }
        if dispatch_error is not None:
            result["reason_code"] = "EMBODIMENT_DISPATCH_UNAVAILABLE"
        return result

    def current_mode(self) -> str:
        return str(self._host_lifecycle.get_hard_state().sys_mode)

    def resource_gate(self, definition: Any, mode: str) -> Any:
        del mode
        manager = self._host_lifecycle.get_resource_manager(
            create_if_missing=False
        )
        if manager is None or not callable(
            getattr(manager, "assess_background_task", None)
        ):
            return True
        profile = dict(definition.resource_profile)
        return manager.assess_background_task(
            task_id=definition.task_id,
            priority=definition.priority,
            task_type=str(profile.get("resource_class") or "host_scheduler"),
            uses_llm=bool(profile.get("uses_llm", False)),
            uses_gpu=bool(profile.get("uses_gpu", False)),
            reason="governed_scheduler",
        )

    async def _run_infrastructure_pump(
        self,
        *,
        name: str,
        handler: Callable[..., Any],
        interval_seconds: float,
    ) -> None:
        while True:
            try:
                await handler()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.error(
                    "HOST_INFRASTRUCTURE_DISPATCH_PUMP_FAILED:%s",
                    name,
                    exc_info=True,
                )
            await asyncio.sleep(max(0.25, interval_seconds))

    async def activate_promoted_background_runtime(self, scheduler: Any) -> None:
        await scheduler.activate_clock()
        if self._infrastructure_tasks:
            return
        self._infrastructure_tasks = (
            asyncio.create_task(
                self._run_infrastructure_pump(
                    name="life-io",
                    handler=self.life_io,
                    interval_seconds=1.0,
                ),
                name="host-life-io-dispatch-pump",
            ),
            asyncio.create_task(
                self._run_infrastructure_pump(
                    name="embodiment-io",
                    handler=self.embodiment_io,
                    interval_seconds=0.5,
                ),
                name="host-embodiment-io-dispatch-pump",
            ),
        )

    async def aclose(self) -> None:
        tasks, self._infrastructure_tasks = self._infrastructure_tasks, ()
        for index, task in enumerate(tasks, start=1):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                self._logger.error(
                    "HOST_OWNED_TASK_CLOSE_FAILED:infrastructure-dispatch-%s",
                    index,
                    exc_info=True,
                )


__all__ = ["HostSchedulerRuntimeHandlers"]
