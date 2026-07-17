from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.live_source_projection import (
    GATEWAY_STATS_SOURCE_KEY,
    GATEWAY_STATS_SOURCE_KIND,
    PROACTIVE_AUTONOMY_DEFAULT_KEY,
    PROACTIVE_AUTONOMY_KIND,
    SYS_MODE_HISTORY_KEY,
    SYS_MODE_HISTORY_KIND,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
)
from host_brain.app.observability_runtime.operator_live_sources import (
    AppOwnedGatewayHealthReader,
    AppOwnedGatewaySourceReader,
    EDGE_GATEWAY_AUTH_FAILED,
    EDGE_GATEWAY_INVALID_RESPONSE,
    EDGE_GATEWAY_TIMEOUT,
    GatewayHttpResponse,
    OperatorLiveSourcePorts,
    OperatorLiveStateProjectionRefresher,
    OperatorProjectionWriteFailed,
    OperatorSourceUnavailable,
    SYS_MODE_HISTORY_PORT_NOT_INJECTED,
    SYS_MODE_STATUS_PORT_NOT_INJECTED,
)


class _GatewayTransport:
    def __init__(
        self,
        marker: str,
        *,
        status_code: int = 200,
        error: BaseException | None = None,
    ) -> None:
        self.marker = marker
        self.status_code = status_code
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_s: float,
    ) -> GatewayHttpResponse:
        self.calls.append(
            {"url": url, "headers": dict(headers), "timeout_s": timeout_s}
        )
        if self.error is not None:
            raise self.error
        return GatewayHttpResponse(
            status_code=self.status_code,
            payload={
                "status": self.marker,
                "uptime_s": 10,
                "outbound_ready": True,
                "adapters": {"registered_count": 1},
                "transport": {"mode": "durable", "worker_alive": True},
            },
        )


class _ProjectionStore:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], SimpleNamespace] = {}
        self.writes: list[tuple[str, str]] = []

    def get(self, kind: str, key: str) -> SimpleNamespace | None:
        return self.documents.get((kind, key))

    def upsert(
        self,
        kind: str,
        key: str,
        payload: dict[str, Any],
        *,
        source_cursor: int,
        source_digest: str,
        source_ref: str,
        degraded_state: ProjectionDegradedState,
        degraded_detail: str | None,
        freshness_ts_ms: int,
    ) -> None:
        self.writes.append((kind, key))
        self.documents[(kind, key)] = SimpleNamespace(
            kind=kind,
            key=key,
            payload=dict(payload),
            source_cursor=source_cursor,
            source_digest=source_digest,
            source_ref=source_ref,
            degraded_state=degraded_state,
            degraded_detail=degraded_detail,
            freshness_ts_ms=freshness_ts_ms,
        )


class _LifeReader:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.calls: list[str] = []

    def get_snapshot(self, person_id: str) -> dict[str, Any]:
        self.calls.append(person_id)
        return {
            "person_id": person_id,
            "version": 2,
            "state": {
                "inner_weather": {"reach_out_bias": 0.4},
                "self_growth": {"active_projects": []},
                "dialogue_closure": {"marker": self.marker},
            },
            "state_digest": f"sha256:{self.marker}",
            "updated_at_ms": 1_700_000_000_000,
        }


def _gateway_payload(marker: str) -> dict[str, Any]:
    return {
        "generated_at_ms": 1_700_000_000_000,
        "channel_rows": [],
        "voice_rows": [],
        "remote_gateway_health": {
            "base_url": f"http://{marker}",
            "body": {"status": "ok", "adapters": {}},
        },
        "runtime_config": {},
    }


def _status(marker: str) -> dict[str, Any]:
    return {
        "sys_mode": marker,
        "auto_rules": {},
        "idle_ms": 0,
        "idle_minutes": 0.0,
        "last_interaction_ts_ms": 0,
        "is_night_time": False,
        "valid_transitions": [],
        "ts_ms": 1_700_000_000_000,
    }


def _history(marker: str) -> list[dict[str, Any]]:
    return [
        {
            "event_id": marker,
            "from_mode": "normal",
            "to_mode": "focus",
            "source": "unit",
            "reason": marker,
            "ts_ms": 1_700_000_000_000,
            "success": True,
        }
    ]


def test_gateway_health_cache_is_owned_and_isolated_per_reader() -> None:
    first_transport = _GatewayTransport("first")
    second_transport = _GatewayTransport("second")
    first = AppOwnedGatewayHealthReader(
        ("http://first-edge",),
        transport=first_transport,
        auth_headers_provider=lambda: {"X-Internal-Token": "first-secret"},
        clock_ms=lambda: 100,
    )
    second = AppOwnedGatewayHealthReader(
        ("http://second-edge",),
        transport=second_transport,
        auth_headers_provider=lambda: {"X-Internal-Token": "second-secret"},
        clock_ms=lambda: 100,
    )

    assert first.read()["body"]["status"] == "first"
    assert second.read()["body"]["status"] == "second"
    assert first.read()["body"]["status"] == "first"
    assert second.read()["body"]["status"] == "second"

    assert len(first_transport.calls) == 1
    assert len(second_transport.calls) == 1
    assert first_transport.calls[0]["headers"] == {
        "X-Internal-Token": "first-secret"
    }
    assert second_transport.calls[0]["headers"] == {
        "X-Internal-Token": "second-secret"
    }


@pytest.mark.parametrize(
    ("transport", "reason_code"),
    [
        (_GatewayTransport("auth", status_code=401), EDGE_GATEWAY_AUTH_FAILED),
        (_GatewayTransport("timeout", error=TimeoutError()), EDGE_GATEWAY_TIMEOUT),
    ],
)
def test_gateway_auth_and_timeout_are_typed_source_failures(
    transport: _GatewayTransport,
    reason_code: str,
) -> None:
    reader = AppOwnedGatewayHealthReader(
        ("http://edge",),
        transport=transport,
        cache_ttl_ms=0,
    )

    with pytest.raises(OperatorSourceUnavailable) as raised:
        reader.read()

    assert raised.value.reason_code == reason_code
    assert raised.value.source_scope == "edge-runtime"


def test_explicit_live_sources_refresh_without_process_locator(monkeypatch) -> None:
    from host_brain.composition import process_services

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("operator live source touched process_services")

    for name in dir(process_services):
        if name.startswith(("get_", "peek_", "configure_")):
            monkeypatch.setattr(process_services, name, forbidden)

    store = _ProjectionStore()
    life_reader = _LifeReader("local")
    refresher = OperatorLiveStateProjectionRefresher(
        store=store,  # type: ignore[arg-type]
        ports=OperatorLiveSourcePorts(
            gateway_source_reader=lambda: _gateway_payload("local"),
            life_snapshot_reader=life_reader,
            sys_mode_status_reader=lambda: _status("focus"),
            sys_mode_history_reader=lambda: _history("event-local"),
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    result = refresher.refresh()

    assert result["target_count"] == 4
    assert result["healthy_count"] == 4
    assert result["unavailable_count"] == 0
    assert life_reader.calls == ["default"]
    proactive = store.get(PROACTIVE_AUTONOMY_KIND, PROACTIVE_AUTONOMY_DEFAULT_KEY)
    assert proactive is not None
    assert proactive.payload["dialogue_closure"] == {"marker": "local"}


def test_missing_sys_mode_ports_are_independent_typed_documents() -> None:
    store = _ProjectionStore()
    refresher = OperatorLiveStateProjectionRefresher(
        store=store,  # type: ignore[arg-type]
        ports=OperatorLiveSourcePorts(
            gateway_source_reader=lambda: _gateway_payload("healthy"),
            life_snapshot_reader=_LifeReader("healthy"),
            sys_mode_status_reader=None,
            sys_mode_history_reader=None,
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    result = refresher.refresh()

    assert result["healthy_count"] == 2
    assert result["unavailable_count"] == 2
    assert result["write_failed_count"] == 0
    status = store.get(SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY)
    history = store.get(SYS_MODE_HISTORY_KIND, SYS_MODE_HISTORY_KEY)
    assert status is not None and history is not None
    assert status.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
    assert history.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
    assert status.payload["reason_code"] == SYS_MODE_STATUS_PORT_NOT_INJECTED
    assert history.payload["reason_code"] == SYS_MODE_HISTORY_PORT_NOT_INJECTED
    assert (
        store.get(GATEWAY_STATS_SOURCE_KIND, GATEWAY_STATS_SOURCE_KEY).degraded_state
        is ProjectionDegradedState.HEALTHY
    )
    assert (
        store.get(
            PROACTIVE_AUTONOMY_KIND,
            PROACTIVE_AUTONOMY_DEFAULT_KEY,
        ).degraded_state
        is ProjectionDegradedState.HEALTHY
    )


def test_gateway_auth_failure_is_materialized_without_blocking_host_sources() -> None:
    store = _ProjectionStore()
    health_reader = AppOwnedGatewayHealthReader(
        ("http://edge",),
        transport=_GatewayTransport("auth", status_code=403),
        cache_ttl_ms=0,
        clock_ms=lambda: 1_700_000_000_000,
    )
    gateway_reader = AppOwnedGatewaySourceReader(
        health_reader=health_reader,
        activity_reader=lambda _start, _end: {
            "channel_rows": [],
            "voice_rows": [],
        },
        clock_ms=lambda: 1_700_000_000_000,
    )
    refresher = OperatorLiveStateProjectionRefresher(
        store=store,  # type: ignore[arg-type]
        ports=OperatorLiveSourcePorts(
            gateway_source_reader=gateway_reader,
            life_snapshot_reader=_LifeReader("healthy"),
            sys_mode_status_reader=lambda: _status("focus"),
            sys_mode_history_reader=lambda: _history("history-live"),
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    result = refresher.refresh()

    assert result["healthy_count"] == 3
    assert result["unavailable_count"] == 1
    gateway = store.get(GATEWAY_STATS_SOURCE_KIND, GATEWAY_STATS_SOURCE_KEY)
    assert gateway is not None
    assert gateway.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
    assert gateway.payload["reason_code"] == EDGE_GATEWAY_AUTH_FAILED
    assert (
        store.get(SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY).degraded_state
        is ProjectionDegradedState.HEALTHY
    )
    assert (
        store.get(SYS_MODE_HISTORY_KIND, SYS_MODE_HISTORY_KEY).degraded_state
        is ProjectionDegradedState.HEALTHY
    )


def test_one_sys_mode_read_failure_does_not_block_history_or_other_targets() -> None:
    store = _ProjectionStore()

    def broken_status() -> dict[str, Any]:
        raise RuntimeError("status port down")

    refresher = OperatorLiveStateProjectionRefresher(
        store=store,  # type: ignore[arg-type]
        ports=OperatorLiveSourcePorts(
            gateway_source_reader=lambda: _gateway_payload("healthy"),
            life_snapshot_reader=_LifeReader("healthy"),
            sys_mode_status_reader=broken_status,
            sys_mode_history_reader=lambda: _history("history-still-live"),
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    result = refresher.refresh()

    assert result["healthy_count"] == 3
    assert result["unavailable_count"] == 1
    history = store.get(SYS_MODE_HISTORY_KIND, SYS_MODE_HISTORY_KEY)
    assert history is not None
    assert history.payload["items"][0]["event_id"] == "history-still-live"
    assert history.degraded_state is ProjectionDegradedState.HEALTHY
    status = store.get(SYS_MODE_STATUS_KIND, SYS_MODE_STATUS_KEY)
    assert status is not None
    assert status.payload["reason_code"] == "OBS_SYS_MODE_STATUS_READ_FAILED"


def test_missing_canonical_life_snapshot_is_not_materialized_as_healthy() -> None:
    store = _ProjectionStore()
    refresher = OperatorLiveStateProjectionRefresher(
        store=store,  # type: ignore[arg-type]
        ports=OperatorLiveSourcePorts(
            gateway_source_reader=lambda: _gateway_payload("healthy"),
            life_snapshot_reader=lambda _person_id: {
                "person_id": "default",
                "version": 0,
                "state": {},
            },
            sys_mode_status_reader=lambda: _status("focus"),
            sys_mode_history_reader=lambda: _history("history-live"),
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    result = refresher.refresh()

    assert result["healthy_count"] == 3
    assert result["unavailable_count"] == 1
    proactive = store.get(
        PROACTIVE_AUTONOMY_KIND,
        PROACTIVE_AUTONOMY_DEFAULT_KEY,
    )
    assert proactive is not None
    assert proactive.degraded_state is ProjectionDegradedState.SOURCE_UNAVAILABLE
    assert proactive.payload["reason_code"] == "OBS_LIFE_SNAPSHOT_NOT_FOUND"


def test_empty_gateway_health_object_is_typed_invalid_instead_of_live() -> None:
    class _EmptyGatewayTransport:
        def get_json(self, *_args, **_kwargs) -> GatewayHttpResponse:  # noqa: ANN002, ANN003
            return GatewayHttpResponse(status_code=200, payload={})

    reader = AppOwnedGatewayHealthReader(
        ("http://edge.test",),
        transport=_EmptyGatewayTransport(),  # type: ignore[arg-type]
    )

    with pytest.raises(OperatorSourceUnavailable) as captured:
        reader.read()

    assert captured.value.reason_code == EDGE_GATEWAY_INVALID_RESPONSE


def test_any_operator_projection_write_failure_fails_the_whole_job() -> None:
    class _FailingStore(_ProjectionStore):
        def upsert(self, *_args, **_kwargs) -> None:  # noqa: ANN002, ANN003
            raise OSError("projection database unavailable")

    refresher = OperatorLiveStateProjectionRefresher(
        store=_FailingStore(),  # type: ignore[arg-type]
        ports=OperatorLiveSourcePorts(
            gateway_source_reader=lambda: _gateway_payload("healthy"),
            life_snapshot_reader=_LifeReader("healthy"),
            sys_mode_status_reader=lambda: _status("focus"),
            sys_mode_history_reader=lambda: _history("history-live"),
        ),
        clock_ms=lambda: 1_700_000_000_000,
    )

    with pytest.raises(OperatorProjectionWriteFailed) as captured:
        refresher.refresh()

    assert captured.value.targets == (
        "gateway",
        "proactive-autonomy",
        "sys-mode-history",
        "sys-mode-status",
    )
