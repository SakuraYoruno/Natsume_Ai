from __future__ import annotations

import base64
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.digital_life_routes import create_digital_life_router
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.composition.command_ingress import (
    build_process_command_ingress_container,
)
from host_brain.digital_life.companion_permission_ingress import (
    COMPANION_PERMISSION_BODY_INVALID,
    COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED,
    COMPANION_PERMISSION_SCOPE_BINDING_DENIED,
    CompanionPermissionIngress,
    CompanionPermissionIngressError,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.secure_ingress import (
    IngressSurface,
    build_hmac_sha256_signature,
    build_surface_request,
)
from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)


NOW = 1_901_000_000_000
WORKSPACE_ID = "workspace-permission"
SUBJECT_ID = "person-permission"
SESSION_ID = "session-permission"
KEY_ID = "companion.permission.user.hmac.v1"
SECRET = "companion-permission-user-secret-at-least-32-bytes"
PATH = "/v1/digital-life/relationship/proactive-permission"


class _Clock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def _seed_user_principal(
    database: Path,
    *,
    principal_id: str = SUBJECT_ID,
    principal_kind: str = "user",
    tenant_id: str = WORKSPACE_ID,
) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                principal_id,
                principal_kind,
                tenant_id,
                json.dumps(["companion:contact:write"]),
                json.dumps(["host"]),
                json.dumps(["companion:contact:write"]),
                NOW - 1_000,
                NOW + 600_000,
                NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm, secret_ref,
                status, version, valid_from_ms, valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
            """,
            (
                principal_id,
                KEY_ID,
                "test-secret-ref://companion/permission/user",
                NOW - 1_000,
                NOW + 600_000,
                NOW,
            ),
        )


def _runtime(
    tmp_path: Path,
    *,
    principal_kind: str = "user",
) -> tuple[
    Path,
    _Clock,
    LifeSnapshotReader,
    RelationshipLifecycleService,
    CompanionPermissionIngress,
    object,
]:
    database = tmp_path / "dl06-companion-permission.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-companion-permission-unit",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    _seed_user_principal(database, principal_kind=principal_kind)
    clock = _Clock(NOW)
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=clock)
    reader = LifeSnapshotReader(repository)
    lifecycle = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    command_ingress = build_process_command_ingress_container(
        canonical_db_path=database,
        mode="ENFORCE",
        hmac_secrets={KEY_ID: SECRET},
    )
    ingress = CompanionPermissionIngress(
        evaluator=command_ingress.evaluator,
        complete_claim=command_ingress.store.complete_claim,
        relationship_lifecycle_service_provider=lambda: lifecycle,
        clock_ms=clock,
    )
    return database, clock, reader, lifecycle, ingress, command_ingress


def _body(
    *,
    subject_id: str = SUBJECT_ID,
    workspace_id: str = WORKSPACE_ID,
    permission: str = "ALLOWED",
) -> bytes:
    return json.dumps(
        {
            "schema_version": "companion_proactive_permission_request.v1",
            "workspace_id": workspace_id,
            "relationship_subject_id": subject_id,
            "session_id": SESSION_ID,
            "proactive_permission": permission,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _signed_request(
    raw_body: bytes,
    *,
    principal_id: str = SUBJECT_ID,
    principal_kind: PrincipalKind = PrincipalKind.USER,
    tenant_id: str = WORKSPACE_ID,
    request_id: str = "companion.permission.request.1",
    idempotency_key: str = "companion.permission.idempotency.1",
    nonce: str = "companion.permission.nonce.1",
) -> object:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="companion.permission.proof.1",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 300_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=principal_id,
            kind=principal_kind,
            tenant_id=tenant_id,
            granted_scopes=("companion:contact:write",),
        ),
        credential_proof=metadata,
        audience="host",
        request_id=request_id,
        requested_scopes=("companion:contact:write",),
    )
    unsigned = build_surface_request(
        surface=IngressSurface.COMPANION_CONTACT,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path=PATH,
        idempotency_key=idempotency_key,
        nonce=nonce,
        raw_body=raw_body,
        received_at_epoch_ms=NOW,
        transport_key=f"principal:{principal_id}",
        correlation_id="companion.permission.correlation.1",
    )
    identity_header = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=")
    headers = [
        (b"x-natsume-identity", identity_header),
        (b"x-natsume-proof", build_hmac_sha256_signature(unsigned, SECRET).encode("ascii")),
        (b"x-natsume-idempotency-key", idempotency_key.encode("ascii")),
        (b"x-natsume-nonce", nonce.encode("ascii")),
        (b"x-natsume-correlation-id", b"companion.permission.correlation.1"),
        (b"content-type", b"application/json"),
    ]
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path=PATH),
        scope={"headers": headers},
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _http_headers(request: object) -> dict[str, str]:
    return {
        bytes(name).decode("ascii"): bytes(value).decode("ascii")
        for name, value in list(getattr(request, "scope")["headers"])
    }


def test_signed_user_permission_is_scope_bound_replay_safe_and_restart_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import host_brain.app.digital_life_routes as routes

    database, clock, reader, lifecycle, ingress, command_ingress = _runtime(tmp_path)
    raw_body = _body(permission="ALLOWED")
    request = _signed_request(raw_body)
    monkeypatch.setattr(routes.time, "time", lambda: NOW / 1_000)
    app = FastAPI()
    app.include_router(
        create_digital_life_router(
            HostRoutePorts(companion_permission_ingress_provider=lambda: ingress)
        )
    )
    try:
        with TestClient(app) as client:
            first = client.post(PATH, content=raw_body, headers=_http_headers(request))
            duplicate = client.post(PATH, content=raw_body, headers=_http_headers(request))

        assert first.status_code == 200, first.text
        assert first.json()["proactive_permission"] == "ALLOWED"
        assert first.json()["replayed"] is False
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["replayed"] is True

        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=SESSION_ID,
        )
        snapshot = reader.get_snapshot(scope.aggregate_id)
        assert snapshot.state["relationship_v2"]["proactive_permission"] == "ALLOWED"
        events = reader.list_events(scope.aggregate_id, limit=20)
        assert [row.event_type for row in events].count(
            "relationship_proactive_permission_changed"
        ) == 1

        command_ingress.close()
        restarted_container = build_process_command_ingress_container(
            canonical_db_path=database,
            mode="ENFORCE",
            hmac_secrets={KEY_ID: SECRET},
        )
        restarted = CompanionPermissionIngress(
            evaluator=restarted_container.evaluator,
            complete_claim=restarted_container.store.complete_claim,
            relationship_lifecycle_service_provider=lambda: lifecycle,
            clock_ms=clock,
        )
        try:
            replay = restarted.submit(
                request=request,
                raw_body=raw_body,
                received_at_ms=NOW,
            )
        finally:
            restarted_container.close()
        assert replay.replayed is True
        assert [row.event_type for row in reader.list_events(scope.aggregate_id, limit=20)].count(
            "relationship_proactive_permission_changed"
        ) == 1
    finally:
        if not getattr(command_ingress, "_closed", True):
            command_ingress.close()


def test_signed_control_cannot_cross_user_or_workspace_scope(tmp_path: Path) -> None:
    _database, _clock, reader, _lifecycle, ingress, command_ingress = _runtime(tmp_path)
    raw_body = _body(subject_id="other-person")
    request = _signed_request(raw_body)
    try:
        with pytest.raises(
            CompanionPermissionIngressError,
            match=COMPANION_PERMISSION_SCOPE_BINDING_DENIED,
        ):
            ingress.submit(request=request, raw_body=raw_body, received_at_ms=NOW)
        # The same signed duplicate remains a denial and never turns into a
        # writable Life command after a restart/retry boundary.
        with pytest.raises(
            CompanionPermissionIngressError,
            match=COMPANION_PERMISSION_SCOPE_BINDING_DENIED,
        ):
            ingress.submit(request=request, raw_body=raw_body, received_at_ms=NOW)
        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id="other-person",
            session_id=SESSION_ID,
        )
        assert reader.get_snapshot(scope.aggregate_id).version == 0
    finally:
        command_ingress.close()


def test_service_principal_and_ambiguous_payload_are_rejected(
    tmp_path: Path,
) -> None:
    _database, _clock, reader, _lifecycle, ingress, command_ingress = _runtime(
        tmp_path,
        principal_kind="service",
    )
    raw_body = _body()
    service_request = _signed_request(raw_body, principal_kind=PrincipalKind.SERVICE)
    duplicate_key_body = (
        b'{"schema_version":"companion_proactive_permission_request.v1",'
        b'"workspace_id":"workspace-permission",'
        b'"workspace_id":"workspace-permission",'
        b'"relationship_subject_id":"person-permission",'
        b'"session_id":"session-permission",'
        b'"proactive_permission":"UNKNOWN"}'
    )
    malformed_request = _signed_request(
        duplicate_key_body,
        principal_kind=PrincipalKind.SERVICE,
        request_id="companion.permission.request.malformed",
        idempotency_key="companion.permission.idempotency.malformed",
        nonce="companion.permission.nonce.malformed",
    )
    try:
        with pytest.raises(
            CompanionPermissionIngressError,
            match=COMPANION_PERMISSION_PRINCIPAL_KIND_DENIED,
        ):
            ingress.submit(
                request=service_request,
                raw_body=raw_body,
                received_at_ms=NOW,
            )
        with pytest.raises(
            CompanionPermissionIngressError,
            match=COMPANION_PERMISSION_BODY_INVALID,
        ):
            ingress.submit(
                request=malformed_request,
                raw_body=duplicate_key_body,
                received_at_ms=NOW,
            )
        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=SESSION_ID,
        )
        assert reader.get_snapshot(scope.aggregate_id).version == 0
    finally:
        command_ingress.close()


def test_unknown_permission_cannot_be_promoted_to_contact_authority(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "unknown-permission"
    sandbox.mkdir()
    _database, _clock, reader, _lifecycle, ingress, command_ingress = _runtime(sandbox)
    raw_body = _body(permission="UNKNOWN")
    request = _signed_request(
        raw_body,
        request_id="companion.permission.request.unknown",
        idempotency_key="companion.permission.idempotency.unknown",
        nonce="companion.permission.nonce.unknown",
    )
    try:
        with pytest.raises(
            CompanionPermissionIngressError,
            match=COMPANION_PERMISSION_BODY_INVALID,
        ):
            ingress.submit(request=request, raw_body=raw_body, received_at_ms=NOW)
        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=SESSION_ID,
        )
        assert reader.get_snapshot(scope.aggregate_id).version == 0
    finally:
        command_ingress.close()
