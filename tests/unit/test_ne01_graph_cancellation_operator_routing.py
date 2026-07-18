from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from host_brain.composition import runtime_group as runtime_group_module
from host_brain.composition.runtime_group import (
    GovernedRuntimeGroup,
    GraphCancellationOperatorError,
)
from natsume_contracts.task import HostCommandSurface, build_host_command


def _command(surface: HostCommandSurface):
    return build_host_command(
        surface=surface,
        request_id=f"cancel-routing-{surface.value}",
        session_id="cancel-routing-session",
        principal_id="cancel-routing-principal",
        input_ref="input://cancel-routing",
        input_sha256="sha256:" + "a" * 64,
        root_work_order_id="cancel-routing-work",
        graph_id=f"cancel-routing-graph-{surface.value}",
        graph_revision=7,
        route_type="delegate",
        target_id="cancel-routing-target",
        business_scope="cancel-routing-scope",
        operation_fingerprint="sha256:" + "b" * 64,
        created_at_ms=1_000,
        governor_grant_ref="governor-decision://cancel-routing",
    )


class _Runtime:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cancellations: list[dict[str, Any]] = []
        self.failure: BaseException | None = None

    async def activate(self) -> None:
        return None

    async def close_admission(self) -> None:
        return None

    async def drain(self) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def request_graph_cancellation(
        self,
        record: Mapping[str, Any],
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        invocation = {
            "record": record,
            "authority": authority,
            "source": source,
            "reason_code": reason_code,
        }
        self.cancellations.append(invocation)
        if self.failure is not None:
            raise self.failure
        command = record["command"]
        return {
            "runtime": self.name,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
        }


class _CommandRepository:
    def __init__(self, command: Any) -> None:
        self.command = command
        self.lookups: list[tuple[str, int]] = []

    def get_by_graph(self, *, graph_id: str, graph_revision: int):
        self.lookups.append((graph_id, graph_revision))
        return {
            "command": self.command,
            "work_order": object(),
            "assignment": object(),
            "graph": object(),
        }


class _CancellationStateReader:
    def __init__(self, record: Mapping[str, Any] | None = None) -> None:
        self.record = record
        self.lookups: list[tuple[str, int]] = []

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> Mapping[str, Any] | None:
        self.lookups.append((graph_id, graph_revision))
        return self.record


class _ExplodingCancellationStateReader(_CancellationStateReader):
    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> Mapping[str, Any] | None:
        self.lookups.append((graph_id, graph_revision))
        raise RuntimeError("durable cancellation state unavailable")


class _Worker:
    async def start(self) -> None:
        return None

    def wake(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def join(self) -> None:
        return None


def _group(
    command: Any,
    *,
    enabled: bool,
    state_reader: _CancellationStateReader | None = None,
) -> tuple[
    GovernedRuntimeGroup,
    dict[str, _Runtime],
    _CommandRepository,
    _CancellationStateReader,
]:
    runtimes = {
        name: _Runtime(name) for name in ("host", "internal_agent", "scheduler")
    }
    commands = _CommandRepository(command)
    reader = state_reader or _CancellationStateReader()
    group = GovernedRuntimeGroup(
        runtimes,
        review_store=object(),
        review_decision_store=object(),
        command_repository=commands,
        review_continuation_worker_factory=lambda **_kwargs: _Worker(),
        review_continuation_worker_id="cancel-routing-worker",
        graph_cancellation_operator_enabled=enabled,
        graph_cancellation_state_reader=reader,
    )
    return group, runtimes, commands, reader


def _authority() -> dict[str, Any]:
    return {
        "request_id": "cancel-routing-request",
        "audit_ref": "secure-ingress-audit://cancel-routing/request",
        "claim_ref": "secure-ingress-claim://cancel-routing/request",
        "principal_id": "cancel-routing-principal",
        "required_scopes": ["obs:write", "task-runtime:cancel"],
    }


async def _request(
    group: GovernedRuntimeGroup,
    command: Any,
    *,
    authority: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    return await group.request_graph_cancellation(
        graph_id=command.graph_id,
        graph_revision=command.graph_revision,
        reason_code="operator_abort",
        authority=authority or _authority(),
    )


@pytest.mark.asyncio
async def test_disabled_operator_cancellation_fails_closed_before_lookup() -> None:
    command = _command(HostCommandSurface.CHAT)
    group, runtimes, commands, _reader = _group(command, enabled=False)
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, command)

        assert raised.value.reason_code == "GRAPH_CANCELLATION_OPERATOR_DISABLED"
        assert raised.value.side_effects_may_have_run is False
        assert commands.lookups == []
        assert all(runtime.cancellations == [] for runtime in runtimes.values())
    finally:
        await group.aclose()


@pytest.mark.asyncio
async def test_inactive_operator_cancellation_fails_closed_before_lookup() -> None:
    command = _command(HostCommandSurface.CHAT)
    group, runtimes, commands, _reader = _group(command, enabled=True)

    with pytest.raises(GraphCancellationOperatorError) as raised:
        await _request(group, command)

    assert raised.value.reason_code == "GRAPH_CANCELLATION_RUNTIME_INACTIVE"
    assert raised.value.side_effects_may_have_run is False
    assert commands.lookups == []
    assert all(runtime.cancellations == [] for runtime in runtimes.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("surface", "runtime_name"),
    [
        (HostCommandSurface.CHAT, "host"),
        (HostCommandSurface.INTERNAL, "internal_agent"),
        (HostCommandSurface.SCHEDULER, "scheduler"),
    ],
)
async def test_exact_admission_routes_to_owning_surface_with_server_source(
    surface: HostCommandSurface,
    runtime_name: str,
) -> None:
    command = _command(surface)
    group, runtimes, commands, _reader = _group(command, enabled=True)
    authority = _authority()
    authority_snapshot = dict(authority)
    await group.activate()
    try:
        result = await _request(group, command, authority=authority)

        assert commands.lookups == [(command.graph_id, command.graph_revision)]
        assert result == {
            "runtime": runtime_name,
            "graph_id": command.graph_id,
            "graph_revision": command.graph_revision,
        }
        assert sum(len(runtime.cancellations) for runtime in runtimes.values()) == 1
        invocation = runtimes[runtime_name].cancellations[0]
        assert invocation["record"]["command"] is command
        assert invocation["authority"] == authority_snapshot
        assert authority == authority_snapshot
        assert invocation["source"] == "OPERATOR"
        assert invocation["reason_code"] == "OPERATOR_ABORT"
    finally:
        await group.aclose()


@pytest.mark.asyncio
async def test_exact_lookup_binding_drift_fails_closed_without_dispatch() -> None:
    requested = _command(HostCommandSurface.CHAT)
    wrong_revision = _command(HostCommandSurface.CHAT)
    object.__setattr__(wrong_revision, "graph_revision", requested.graph_revision + 1)
    group, runtimes, commands, _reader = _group(wrong_revision, enabled=True)
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, requested)

        assert commands.lookups == [(requested.graph_id, requested.graph_revision)]
        assert raised.value.reason_code == "GRAPH_CANCELLATION_COMMAND_BINDING_DRIFT"
        assert raised.value.side_effects_may_have_run is False
        assert all(runtime.cancellations == [] for runtime in runtimes.values())
    finally:
        await group.aclose()


@pytest.mark.asyncio
async def test_command_lookup_error_is_definitely_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _command(HostCommandSurface.CHAT)
    group, runtimes, commands, reader = _group(command, enabled=True)

    def fail_lookup(*, graph_id: str, graph_revision: int) -> None:
        commands.lookups.append((graph_id, graph_revision))
        raise RuntimeError("read-only command repository unavailable")

    monkeypatch.setattr(commands, "get_by_graph", fail_lookup)
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, command)

        assert commands.lookups == [(command.graph_id, command.graph_revision)]
        assert raised.value.reason_code == (
            "GRAPH_CANCELLATION_COMMAND_LOOKUP_UNAVAILABLE"
        )
        assert raised.value.side_effects_may_have_run is False
        assert reader.lookups == []
        assert all(runtime.cancellations == [] for runtime in runtimes.values())
    finally:
        await group.aclose()


@pytest.mark.asyncio
async def test_unknown_surface_mapping_fails_closed_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = _command(HostCommandSurface.GATEWAY)
    group, runtimes, commands, _reader = _group(command, enabled=True)
    monkeypatch.delitem(
        runtime_group_module._SURFACE_RUNTIME,
        HostCommandSurface.GATEWAY.value,
    )
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, command)

        assert commands.lookups == [(command.graph_id, command.graph_revision)]
        assert raised.value.reason_code == (
            "GRAPH_CANCELLATION_SURFACE_UNAVAILABLE:gateway"
        )
        assert raised.value.side_effects_may_have_run is False
        assert all(runtime.cancellations == [] for runtime in runtimes.values())
    finally:
        await group.aclose()


@pytest.mark.asyncio
async def test_surface_runtime_error_without_durable_intent_fails_closed() -> None:
    command = _command(HostCommandSurface.INTERNAL)
    reader = _CancellationStateReader(record=None)
    group, runtimes, commands, _reader = _group(
        command,
        enabled=True,
        state_reader=reader,
    )
    runtimes["internal_agent"].failure = RuntimeError("runtime failed")
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, command)

        assert commands.lookups == [(command.graph_id, command.graph_revision)]
        assert reader.lookups == [(command.graph_id, command.graph_revision)]
        assert raised.value.reason_code == "GRAPH_CANCELLATION_REJECTED"
        assert raised.value.side_effects_may_have_run is False
        assert len(runtimes["internal_agent"].cancellations) == 1
        assert runtimes["host"].cancellations == []
        assert runtimes["scheduler"].cancellations == []
    finally:
        await group.aclose()


@pytest.mark.asyncio
async def test_surface_runtime_error_with_exact_durable_intent_is_uncertain() -> None:
    command = _command(HostCommandSurface.SCHEDULER)
    authority = _authority()
    reader = _CancellationStateReader(
        record={
            "intent_ref": "task-graph-cancellation://cancel-routing-intent",
            "payload": {
                "request_id": authority["request_id"],
                "request_identity": authority["audit_ref"],
                "source": "OPERATOR",
                "reason_code": "OPERATOR_ABORT",
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
            },
            "status": "REQUESTED",
        }
    )
    group, runtimes, commands, _reader = _group(
        command,
        enabled=True,
        state_reader=reader,
    )
    runtimes["scheduler"].failure = RuntimeError("response lost after commit")
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, command, authority=authority)

        assert commands.lookups == [(command.graph_id, command.graph_revision)]
        assert reader.lookups == [(command.graph_id, command.graph_revision)]
        assert raised.value.reason_code == (
            "GRAPH_CANCELLATION_EXECUTION_UNCERTAIN"
        )
        assert raised.value.side_effects_may_have_run is True
        assert len(runtimes["scheduler"].cancellations) == 1
        assert runtimes["host"].cancellations == []
        assert runtimes["internal_agent"].cancellations == []
    finally:
        await group.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reader",
    [
        _ExplodingCancellationStateReader(),
        _CancellationStateReader(
            record={
                "intent_ref": "task-graph-cancellation://malformed",
                "payload": {
                    "request_id": "cancel-routing-request",
                    "request_identity": (
                        "secure-ingress-audit://cancel-routing/request"
                    ),
                    "source": "OPERATOR",
                    "reason_code": "OPERATOR_ABORT",
                    "graph_id": "cancel-routing-graph-internal",
                    "graph_revision": "not-an-integer",
                },
            }
        ),
    ],
    ids=("state-read-error", "malformed-state"),
)
async def test_surface_runtime_error_with_unverifiable_durable_state_is_uncertain(
    reader: _CancellationStateReader,
) -> None:
    command = _command(HostCommandSurface.INTERNAL)
    group, runtimes, commands, _reader = _group(
        command,
        enabled=True,
        state_reader=reader,
    )
    runtimes["internal_agent"].failure = RuntimeError("runtime failed")
    await group.activate()
    try:
        with pytest.raises(GraphCancellationOperatorError) as raised:
            await _request(group, command)

        assert commands.lookups == [(command.graph_id, command.graph_revision)]
        assert reader.lookups == [(command.graph_id, command.graph_revision)]
        assert raised.value.reason_code == (
            "GRAPH_CANCELLATION_EXECUTION_UNCERTAIN"
        )
        assert raised.value.side_effects_may_have_run is True
        assert len(runtimes["internal_agent"].cancellations) == 1
    finally:
        await group.aclose()
