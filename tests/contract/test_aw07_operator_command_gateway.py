from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.observability.command_http import (
    OPERATOR_COMMAND_GATEWAY_VERSION,
    build_operator_command_openapi,
    create_obs_command_http_router,
)
from host_brain.observability.command_ports import DEFAULT_OBS_COMMAND_SPECS
from host_brain.observability.command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandAuthority,
    ObsCommandEnvelope,
    ObsCommandPortExecutionError,
    bind_server_authority,
)
from host_brain.app.observability_runtime.domain_command_ports import (
    ObsDomainCommandError,
    build_default_obs_domain_command_registry,
)
from host_brain.app.observability_runtime import domain_command_ports
from host_brain.observability.operator_command_gateway import (
    bind_operator_command_runtime,
)


def test_operator_openapi_is_generated_from_the_server_command_manifest() -> None:
    document = build_operator_command_openapi()
    assert document["openapi"] == "3.1.0"
    assert document["info"]["x-natsume-contract"] == OPERATOR_COMMAND_GATEWAY_VERSION
    expected = {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
    actual = {
        operation["post"]["x-natsume-command-id"]
        for operation in document["paths"].values()
    }
    assert actual == expected
    for command_id in actual:
        operation = document["paths"][
            f"/v1/host/obs-commands/{command_id}"
        ]["post"]
        assert operation["security"] == [
            {"identity": [], "proof": [], "idempotency": [], "nonce": []}
        ]
        assert operation["x-natsume-required-scopes"][0] == "obs:write"
        envelope = operation["requestBody"]["content"]["application/json"]["schema"]
        assert envelope["properties"]["command_id"]["const"] == command_id
        assert not (
            set(envelope["properties"]["payload"]["properties"])
            & {"principal_id", "operator", "approver", "owner", "tenant_id"}
        )


def test_production_manifest_has_no_placeholder_domain_port() -> None:
    manifest_ids = {spec.command_id for spec in DEFAULT_OBS_COMMAND_SPECS}
    assert set(domain_command_ports._HANDLERS) == manifest_ids
    assert all(
        "retired" not in handler.__name__
        and "placeholder" not in handler.__name__
        and "unavailable" not in handler.__name__
        for handler in domain_command_ports._HANDLERS.values()
    )


def test_operator_openapi_endpoint_is_read_only_and_available_without_command_runtime() -> None:
    app = FastAPI()
    app.include_router(create_obs_command_http_router(ingress_provider=None))
    response = TestClient(app).get(
        "/v1/host/operator-command-gateway/openapi.json"
    )
    assert response.status_code == 200
    assert response.json() == build_operator_command_openapi()


def _envelope(command_id: str, payload: dict[str, object]) -> ObsCommandEnvelope:
    registry = build_default_obs_domain_command_registry()
    spec = registry.resolve_spec(command_id)
    assert spec is not None
    authority = ObsCommandAuthority.bind(
        principal_id="operator.contract",
        tenant_id="tenant.contract",
        request_id="request.contract",
    )
    bound = bind_server_authority(
        spec.payload_schema.validate(payload),
        spec=spec,
        authority=authority,
    )
    return ObsCommandEnvelope(
        schema_version=OBS_COMMAND_ENVELOPE_SCHEMA,
        command_id=command_id,
        action=spec.action,
        required_scopes=spec.required_scopes,
        payload=bound,
        authority=authority,
        claim_ref="claim://operator/contract",
        audit_ref="audit://operator/contract",
        body_sha256="0" * 64,
    )


def test_scheduler_mutation_fails_closed_without_governed_runtime() -> None:
    captured: dict[str, object] = {}

    class _SchedulerService:
        @staticmethod
        def get_definition(task_id: str) -> dict[str, object]:
            assert task_id == "clock.daily"
            return {"head_version": 7}

        @staticmethod
        async def set_enabled(**kwargs):  # noqa: ANN003
            captured.update(kwargs)
            return {"task_id": kwargs["task_id"], "enabled": kwargs["enabled"]}

    registry = build_default_obs_domain_command_registry()
    port = registry.resolve_port("scheduler.task.enable")
    assert port is not None
    state = SimpleNamespace(
        scheduler_container=SimpleNamespace(command_service=_SchedulerService())
    )
    with bind_operator_command_runtime(state):
        with pytest.raises(ObsDomainCommandError) as blocked:
            asyncio.run(port.execute(_envelope(
                "scheduler.task.enable",
                {"task_id": "clock.daily"},
            )))

    assert str(blocked.value) == (
        "OBS_OPERATOR_STATE_CONTROL_RUNTIME_UNAVAILABLE"
    )
    assert captured == {}
