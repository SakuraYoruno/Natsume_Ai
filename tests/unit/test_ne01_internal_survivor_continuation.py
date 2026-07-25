from __future__ import annotations

import asyncio
from typing import Any

import pytest

from host_brain.agent_runtime.accepted_effect_runtime import (
    InternalAcceptedEffectRuntime,
)
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.internal_task_runtime import GovernedInternalRuntime


class _RuntimeContext:
    def freeze_request(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def require_frozen(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}


class _TaskStore:
    def get_attempt(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def get_graph(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def get_node(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def get_lease(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    def get_operation_reservation(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {}

    def resolve_internal_agent_checkpoint_continuation_lineage(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {}


class _ArtifactStore:
    def inventory_governed_seals(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> tuple[Any, ...]:
        return ()

    def read_projection_recovery(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {}


class _Dispatcher:
    def dispatch_graph(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()


class _Results:
    def get_by_command(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _CheckpointContinuation:
    def __init__(self) -> None:
        self.calls = 0
        self.status = "HOLD"
        self._report: tuple[dict[str, Any], ...] = ()

    @property
    def report(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(item) for item in self._report)

    async def recover(
        self,
        *,
        page_limit: int = 100,
        scan_limit: int = 1_000,
    ) -> tuple[dict[str, Any], ...]:
        assert page_limit == 100
        assert scan_limit == 1_000
        self.calls += 1
        self._report = (
            {
                "stage_ref": "artifact-stage://internal-survivor",
                "status": self.status,
                "reason_code": None,
            },
        )
        return self.report


@pytest.mark.asyncio
async def test_internal_runtime_periodically_rechecks_held_checkpoint() -> None:
    runtime = GovernedInternalRuntime(
        application=object(),
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=object(),
        governor_grants=object(),
        runtime_context_application=_RuntimeContext(),
        accepted_effect_runtime=InternalAcceptedEffectRuntime(
            dispatcher=_Dispatcher(),
            result_repository=_Results(),
        ),
        startup_task_store=_TaskStore(),
        startup_artifact_store=_ArtifactStore(),
        survivor_interval_ms=60_000,
    )
    checkpoint = _CheckpointContinuation()
    runtime._startup_checkpoint_continuation = checkpoint  # noqa: SLF001

    await runtime.activate()
    assert checkpoint.calls == 1
    assert runtime.startup_checkpoint_report[0]["status"] == "HOLD"
    assert runtime.survivor_continuation_health["running"] is True

    checkpoint.status = "COMPLETED"
    runtime._wake_terminal_continuation()  # noqa: SLF001

    async def wait_for_periodic_recheck() -> None:
        while checkpoint.calls < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_periodic_recheck(), timeout=1.0)
    assert runtime.startup_checkpoint_report[0]["status"] == "COMPLETED"
    assert runtime.survivor_continuation_health["completed_cycles"] >= 2
    await runtime.aclose()
    assert runtime.survivor_continuation_health["running"] is False
