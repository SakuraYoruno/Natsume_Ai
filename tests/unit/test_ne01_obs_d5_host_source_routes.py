from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi import Response
from fastapi.testclient import TestClient
import pytest

from natsume_contracts.operator import (
    HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
    HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
    HOST_MODE_SOURCE_SCHEMA_VERSION,
    HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
    HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
    HostCapabilityForgeSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostModeSourceSnapshot,
    HostObsSourceContractError,
    HostRuntimeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
)
from host_brain.app.host_obs_read_routes import (
    HOST_OBS_READ_AUTH_NOT_CONFIGURED,
    HOST_OBS_READ_UNAUTHORIZED,
    HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE,
    HostObsReadPorts,
    HostObsReadSourceUnavailable,
    create_host_obs_read_router,
)
from host_brain.app import app_factory
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.app.runtime_metrics import RuntimeMetrics


NOW = 1_750_000_000_000


@pytest.mark.parametrize(
    "field_name",
    ("observed_at_ms", "evidence_ts_ms", "source_revision", "nested_timestamp"),
)
@pytest.mark.parametrize("invalid", (True, 1.5, "1"))
def test_host_source_contract_rejects_non_integer_numeric_coercion(
    field_name: str,
    invalid: object,
) -> None:
    payload = {
        "schema_version": HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
        "observed_at_ms": NOW,
        "evidence_ts_ms": NOW - 1,
        "source_instance_id": "host-test-epoch",
        "source_revision": 1,
        "stats": {"status": "MOCK_READY", "timestamp": NOW - 1},
    }
    if field_name == "nested_timestamp":
        payload["stats"]["timestamp"] = invalid
    else:
        payload[field_name] = invalid

    with pytest.raises(HostObsSourceContractError):
        HostSemanticRecallSourceSnapshot.from_mapping(payload)


@pytest.mark.parametrize("invalid", (True, 12345678, 12345678.0))
def test_host_source_contract_requires_a_string_source_instance_id(
    invalid: object,
) -> None:
    payload = {
        "schema_version": HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
        "observed_at_ms": NOW,
        "evidence_ts_ms": NOW - 1,
        "source_instance_id": invalid,
        "source_revision": 1,
        "stats": {"status": "MOCK_READY", "timestamp": NOW - 1},
    }

    with pytest.raises(HostObsSourceContractError):
        HostSemanticRecallSourceSnapshot.from_mapping(payload)


def _mode() -> HostModeSourceSnapshot:
    return HostModeSourceSnapshot.from_mapping(
        {
            "schema_version": HOST_MODE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 1,
            "source_instance_id": "host-test-epoch",
            "source_revision": 1,
            "status": {"sys_mode": "focus", "ts_ms": NOW - 1},
            "history": [],
        }
    )


def _capability() -> HostCapabilityForgeSourceSnapshot:
    return HostCapabilityForgeSourceSnapshot.from_mapping(
        {
            "schema_version": HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 2,
            "source_instance_id": "host-test-epoch",
            "source_revision": 1,
            "gap": {"timestamp": NOW - 2},
            "proposals": [],
            "runtime_stats": {},
            "runtime_tuning": {},
            "runtime_quality": {},
            "semantic_recall": {"timestamp": NOW - 2},
            "governance": {"generated_at_ms": NOW - 2},
        }
    )


def _runtime() -> HostRuntimeSourceSnapshot:
    return HostRuntimeSourceSnapshot.from_mapping(
        {
            "schema_version": HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 3,
            "source_instance_id": "host-test-epoch",
            "source_revision": 1,
            "organ_tribe": {"agents": [], "active_agents": 0},
            "scheduler": {"available": True, "status": "OK"},
        }
    )


def _semantic() -> HostSemanticRecallSourceSnapshot:
    return HostSemanticRecallSourceSnapshot.from_mapping(
        {
            "schema_version": HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 4,
            "source_instance_id": "host-test-epoch",
            "source_revision": 1,
            "stats": {"status": "MOCK_READY", "timestamp": NOW - 4},
        }
    )


def _governance() -> HostGovernanceSourceSnapshot:
    return HostGovernanceSourceSnapshot.from_mapping(
        {
            "schema_version": HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
            "observed_at_ms": NOW,
            "evidence_ts_ms": NOW - 5,
            "source_instance_id": "host-test-epoch",
            "source_revision": 1,
            "governance": {
                "schema_version": "Natsume.governance.snapshot.v1",
                "generated_at_ms": NOW - 5,
                "read_only": True,
            },
        }
    )


def _app(ports: HostObsReadPorts) -> FastAPI:
    app = FastAPI()
    app.include_router(create_host_obs_read_router(ports))
    return app


def _ports(token: str = "dedicated-obs-token") -> HostObsReadPorts:
    return HostObsReadPorts.from_token(
        mode_source_reader=_mode,
        capability_forge_source_reader=_capability,
        semantic_recall_source_reader=_semantic,
        governance_source_reader=_governance,
        runtime_source_reader=_runtime,
        token=token,
    )


def test_dedicated_auth_fails_closed_when_missing_or_wrong() -> None:
    missing = TestClient(_app(_ports(token=""))).get("/internal/obs-sources/mode")
    assert missing.status_code == 503
    assert missing.json()["detail"] == HOST_OBS_READ_AUTH_NOT_CONFIGURED
    assert missing.headers["cache-control"] == "no-store"

    wrong = TestClient(_app(_ports())).get(
        "/internal/obs-sources/mode",
        headers={"X-Internal-Token": "tribe-or-wrong-token"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == HOST_OBS_READ_UNAUTHORIZED
    assert wrong.headers["cache-control"] == "no-store"


def test_five_versioned_sources_are_private_no_store_routes() -> None:
    client = TestClient(_app(_ports()))
    expected = {
        "/internal/obs-sources/mode": HOST_MODE_SOURCE_SCHEMA_VERSION,
        "/internal/obs-sources/capability-forge": (
            HOST_CAPABILITY_FORGE_SOURCE_SCHEMA_VERSION
        ),
        "/internal/obs-sources/governance": HOST_GOVERNANCE_SOURCE_SCHEMA_VERSION,
        "/internal/obs-sources/runtime": HOST_RUNTIME_SOURCE_SCHEMA_VERSION,
        "/internal/obs-sources/semantic-recall": (
            HOST_SEMANTIC_RECALL_SOURCE_SCHEMA_VERSION
        ),
    }

    for path, schema_version in expected.items():
        response = client.get(
            path,
            headers={"X-Internal-Token": "dedicated-obs-token"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["schema_version"] == schema_version
        assert response.json()["observed_at_ms"] == NOW
        assert response.json()["evidence_ts_ms"] > 0

    assert all(path not in client.get("/openapi.json").json()["paths"] for path in expected)


def test_source_failure_is_typed_and_private_exception_is_not_reflected() -> None:
    private = "private-runtime-host-and-secret"

    def unavailable() -> HostRuntimeSourceSnapshot:
        raise HostObsReadSourceUnavailable(HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE)

    ports = HostObsReadPorts.from_token(
        mode_source_reader=_mode,
        capability_forge_source_reader=_capability,
        semantic_recall_source_reader=_semantic,
        governance_source_reader=_governance,
        runtime_source_reader=unavailable,
        token="dedicated-obs-token",
    )
    response = TestClient(_app(ports)).get(
        "/internal/obs-sources/runtime",
        headers={"X-Internal-Token": "dedicated-obs-token"},
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    rendered = response.text
    assert HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE in rendered
    assert private not in rendered


def test_environment_binding_uses_only_dedicated_obs_token() -> None:
    ports = HostObsReadPorts.from_environment(
        mode_source_reader=_mode,
        capability_forge_source_reader=_capability,
        semantic_recall_source_reader=_semantic,
        governance_source_reader=_governance,
        runtime_source_reader=_runtime,
        environ={
            "HOST_OBS_READ_TOKEN": "dedicated",
            "HOST_INTERNAL_TRIBE_TOKEN": "must-not-authorize",
        },
    )
    client = TestClient(_app(ports))

    rejected = client.get(
        "/internal/obs-sources/runtime",
        headers={"X-Internal-Token": "must-not-authorize"},
    )
    accepted = client.get(
        "/internal/obs-sources/runtime",
        headers={"X-Internal-Token": "dedicated"},
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200


def test_host_app_factory_forwards_the_read_ports_to_the_router_registry(
    monkeypatch,
) -> None:
    monkeypatch.setattr(app_factory, "configure_http_middleware", lambda _app: None)
    app = app_factory.create_host_fastapi_app(
        lifespan=None,
        metrics=RuntimeMetrics(),
        logger=logging.getLogger("test.ne01.obs-d5.host-factory"),
        get_ledger=lambda: None,
        get_tool_broker=lambda: None,
        get_tool_catalog=lambda: None,
        get_bond_engine=lambda: None,
        chat_endpoint=lambda _body, _request: Response(),
        edge_events_endpoint=lambda _body: None,
        root_endpoint=lambda: Response(),
        host_obs_read_ports=_ports(),
        obs_route_ports=ObsRoutePorts(
            query_projection_store_provider=lambda: type(
                "EmptyProjectionStore",
                (),
                {"get": lambda *_args, **_kwargs: None},
            )(),
        ),
        input_runtime_ports=build_input_runtime_application_ports(),
    )

    with TestClient(app) as client:
        response = client.get(
            "/internal/obs-sources/mode",
            headers={"X-Internal-Token": "dedicated-obs-token"},
        )

    assert response.status_code == 200
    assert response.json()["schema_version"] == HOST_MODE_SOURCE_SCHEMA_VERSION
    assert [route.path for route in app.routes].count(
        "/internal/obs-sources/mode"
    ) == 1
