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
from host_brain.digital_life.companion_profile_ingress import (
    COMPANION_PROFILE_BODY_INVALID,
    COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
    COMPANION_PROFILE_SCOPE_BINDING_DENIED,
    CompanionProfileIngress,
    CompanionProfileIngressError,
)
from host_brain.digital_life.companion_profile_lifecycle import (
    CompanionProfileLifecycleService,
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
from natsume_contracts.life import CompanionProfilePreferencesV1


NOW = 1_902_000_000_000
DAY_MS = 86_400_000
WORKSPACE_ID = "workspace-profile-control"
SUBJECT_ID = "person-profile-control"
SESSION_ID = "session-profile-control"
KEY_ID = "companion.profile.user.hmac.v1"
SECRET = "companion-profile-user-secret-at-least-32-bytes"
PATH = "/v1/digital-life/relationship/companion-profile"


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
                json.dumps(["companion:profile:write"]),
                json.dumps(["host"]),
                json.dumps(["companion:profile:write"]),
                NOW - 1_000,
                NOW + 30 * DAY_MS,
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
                "test-secret-ref://companion/profile/user",
                NOW - 1_000,
                NOW + 30 * DAY_MS,
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
    CompanionProfileLifecycleService,
    CompanionProfileIngress,
    object,
]:
    database = tmp_path / "dl06-companion-profile-ingress.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-companion-profile-ingress",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    _seed_user_principal(database, principal_kind=principal_kind)
    clock = _Clock(NOW)
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=clock)
    reader = LifeSnapshotReader(repository)
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    relationship._classify_followup = lambda **_kwargs: (  # type: ignore[method-assign]
        "HELPFUL",
        0.91,
        True,
        False,
    )
    profile = CompanionProfileLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    command_ingress = build_process_command_ingress_container(
        canonical_db_path=database,
        mode="ENFORCE",
        hmac_secrets={KEY_ID: SECRET},
    )
    ingress = CompanionProfileIngress(
        evaluator=command_ingress.evaluator,
        complete_claim=command_ingress.store.complete_claim,
        companion_profile_lifecycle_service_provider=lambda: profile,
        clock_ms=clock,
    )
    return database, clock, reader, relationship, profile, ingress, command_ingress


def _accepted_response(*, ordinal: int, created_at_ms: int) -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": f"profile-control-foreground-{ordinal}",
        "command_id": f"profile-control-foreground-command-{ordinal}",
        "created_at_ms": created_at_ms,
    }


def _request_body(*, session_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id=WORKSPACE_ID,
        person_id=SUBJECT_ID,
        session_id=session_id,
        emotion_context={},
        message="谢谢，这样我感觉被好好接住了。",
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
        mood_state=None,
        emotion_range_hint=None,
    )


def _helpful_aftereffect(
    *, relationship: RelationshipLifecycleService, clock: _Clock, ordinal: int
) -> None:
    relationship.provision_accepted_response(
        workspace_id=WORKSPACE_ID,
        relationship_subject_id=SUBJECT_ID,
        session_id=f"session-evidence-{ordinal}",
        request_id=f"profile-control-response-{ordinal}",
        response_receipt=_accepted_response(ordinal=ordinal, created_at_ms=clock.value),
        response_text="我在，慢一点也没关系。",
    )
    clock.value += 1_000
    aftereffect = relationship.record_followup(
        request_body=_request_body(session_id=f"session-evidence-{ordinal}"),
        request_id=f"profile-control-aftereffect-{ordinal}",
    )
    assert aftereffect.aftereffect is not None
    assert aftereffect.aftereffect.helpfulness == 0.91


def _seed_eligible_history(
    *, relationship: RelationshipLifecycleService, clock: _Clock, count: int = 5
) -> None:
    for ordinal in range(count):
        _helpful_aftereffect(relationship=relationship, clock=clock, ordinal=ordinal)
        clock.value += DAY_MS


def _body(
    *,
    workspace_id: str = WORKSPACE_ID,
    subject_id: str = SUBJECT_ID,
) -> bytes:
    return json.dumps(
        {
            "schema_version": "companion_profile_control_request.v1",
            "workspace_id": workspace_id,
            "relationship_subject_id": subject_id,
            "session_id": SESSION_ID,
            "preferences": CompanionProfilePreferencesV1(
                addressing_style="GENTLE_DIRECT",
                response_length="COMPACT",
                humor_level="LOW",
                followup_rate="NONE",
                comfort_mode="QUIET_FIRST",
                proactive_window="EVENING",
                tts_rate_scale=0.9,
                avatar_motion_intensity=0.35,
            ).as_dict(),
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
    issued_at_ms: int = NOW,
    request_id: str = "companion.profile.request.1",
    idempotency_key: str = "companion.profile.idempotency.1",
    nonce: str = "companion.profile.nonce.1",
) -> object:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="companion.profile.proof.1",
        issued_at_epoch_ms=issued_at_ms,
        expires_at_epoch_ms=issued_at_ms + 300_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=principal_id,
            kind=principal_kind,
            tenant_id=tenant_id,
            granted_scopes=("companion:profile:write",),
        ),
        credential_proof=metadata,
        audience="host",
        request_id=request_id,
        requested_scopes=("companion:profile:write",),
    )
    unsigned = build_surface_request(
        surface=IngressSurface.COMPANION_PROFILE,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path=PATH,
        idempotency_key=idempotency_key,
        nonce=nonce,
        raw_body=raw_body,
        received_at_epoch_ms=issued_at_ms,
        transport_key=f"principal:{principal_id}",
        correlation_id="companion.profile.correlation.1",
    )
    identity_header = base64.urlsafe_b64encode(
        identity.to_canonical_json().encode("utf-8")
    ).rstrip(b"=")
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path=PATH),
        scope={
            "headers": [
                (b"x-natsume-identity", identity_header),
                (
                    b"x-natsume-proof",
                    build_hmac_sha256_signature(unsigned, SECRET).encode("ascii"),
                ),
                (b"x-natsume-idempotency-key", idempotency_key.encode("ascii")),
                (b"x-natsume-nonce", nonce.encode("ascii")),
                (b"x-natsume-correlation-id", b"companion.profile.correlation.1"),
                (b"content-type", b"application/json"),
            ]
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )


def _http_headers(request: object) -> dict[str, str]:
    return {
        bytes(name).decode("ascii"): bytes(value).decode("ascii")
        for name, value in list(getattr(request, "scope")["headers"])
    }


def test_signed_profile_control_starts_replay_safe_canary_and_binds_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import host_brain.app.digital_life_routes as routes

    database, clock, reader, relationship, _profile, ingress, command_ingress = _runtime(tmp_path)
    _seed_eligible_history(relationship=relationship, clock=clock)
    raw_body = _body()
    request = _signed_request(raw_body, issued_at_ms=clock.value)
    monkeypatch.setattr(routes.time, "time", lambda: clock.value / 1_000)
    app = FastAPI()
    app.include_router(
        create_digital_life_router(
            HostRoutePorts(companion_profile_ingress_provider=lambda: ingress)
        )
    )
    try:
        with TestClient(app) as client:
            first = client.post(PATH, content=raw_body, headers=_http_headers(request))
            duplicate = client.post(PATH, content=raw_body, headers=_http_headers(request))
        assert first.status_code == 200, first.text
        assert first.json()["profile_stage"] == "CANARY"
        assert first.json()["replayed"] is False
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["replayed"] is True
        assert duplicate.json()["profile_id"] == first.json()["profile_id"]

        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=SESSION_ID,
        )
        events = reader.list_events(scope.aggregate_id, limit=100)
        assert [event.event_type for event in events].count(
            "companion_profile_canary_started"
        ) == 1
        profile_state = reader.get_snapshot(scope.aggregate_id).state["companion_profile"]
        candidate = profile_state["canary_profile"]
        assert candidate["source_control_ref"].startswith("secure-ingress-audit://")
        projection = reader.list_companion_profile_event_heads(scope.aggregate_id, limit=20)
        assert projection[-1]["source_control_ref"] == candidate["source_control_ref"]

        command_ingress.close()
        restarted_container = build_process_command_ingress_container(
            canonical_db_path=database,
            mode="ENFORCE",
            hmac_secrets={KEY_ID: SECRET},
        )
        restarted = CompanionProfileIngress(
            evaluator=restarted_container.evaluator,
            complete_claim=restarted_container.store.complete_claim,
            companion_profile_lifecycle_service_provider=lambda: _profile,
            clock_ms=clock,
        )
        try:
            replay = restarted.submit(
                request=request,
                raw_body=raw_body,
                received_at_ms=clock.value,
            )
        finally:
            restarted_container.close()
        assert replay.replayed is True
        assert [
            event.event_type for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_canary_started") == 1
    finally:
        if not getattr(command_ingress, "_closed", True):
            command_ingress.close()


def test_profile_control_rejects_cross_scope_service_and_weak_history(tmp_path: Path) -> None:
    _database, clock, reader, relationship, _profile, ingress, command_ingress = _runtime(tmp_path)
    _seed_eligible_history(relationship=relationship, clock=clock, count=1)
    raw_body = _body()
    try:
        with pytest.raises(
            CompanionProfileIngressError,
            match="COMPANION_PROFILE_EVIDENCE_THRESHOLD_NOT_MET",
        ):
            ingress.submit(
                request=_signed_request(raw_body, issued_at_ms=clock.value),
                raw_body=raw_body,
                received_at_ms=clock.value,
            )
        cross_scope = _body(subject_id="other-person")
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_SCOPE_BINDING_DENIED,
        ):
            ingress.submit(
                request=_signed_request(
                    cross_scope,
                    issued_at_ms=clock.value,
                    request_id="companion.profile.request.cross",
                    idempotency_key="companion.profile.idempotency.cross",
                    nonce="companion.profile.nonce.cross",
                ),
                raw_body=cross_scope,
                received_at_ms=clock.value,
            )
        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=SESSION_ID,
        )
        assert [
            event.event_type for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_canary_started") == 0
    finally:
        command_ingress.close()


def test_profile_control_rejects_service_principal(tmp_path: Path) -> None:
    _database, clock, _reader, relationship, _profile, ingress, command_ingress = _runtime(
        tmp_path,
        principal_kind="service",
    )
    _seed_eligible_history(relationship=relationship, clock=clock)
    raw_body = _body()
    try:
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
        ):
            ingress.submit(
                request=_signed_request(
                    raw_body,
                    principal_kind=PrincipalKind.SERVICE,
                    issued_at_ms=clock.value,
                ),
                raw_body=raw_body,
                received_at_ms=clock.value,
            )
    finally:
        command_ingress.close()


def test_profile_control_requires_strict_bounded_wire_shape(tmp_path: Path) -> None:
    _database, clock, _reader, relationship, _profile, ingress, command_ingress = _runtime(tmp_path)
    _seed_eligible_history(relationship=relationship, clock=clock)
    malformed = (
        b'{"schema_version":"companion_profile_control_request.v1",'
        b'"workspace_id":"workspace-profile-control",'
        b'"relationship_subject_id":"person-profile-control",'
        b'"session_id":"session-profile-control",'
        b'"preferences":{"schema_version":"companion_profile_preferences.v1",'
        b'"addressing_style":"DEFAULT","response_length":"BALANCED",'
        b'"humor_level":"LIGHT","followup_rate":"GENTLE",'
        b'"comfort_mode":"ASK_FIRST","proactive_window":"FLEXIBLE",'
        b'"tts_rate_scale":1,"avatar_motion_intensity":0.5,"soul_patch":"no"}}'
    )
    try:
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_BODY_INVALID,
        ):
            ingress.submit(
                request=_signed_request(
                    malformed,
                    issued_at_ms=clock.value,
                    request_id="companion.profile.request.malformed",
                    idempotency_key="companion.profile.idempotency.malformed",
                    nonce="companion.profile.nonce.malformed",
                ),
                raw_body=malformed,
                received_at_ms=clock.value,
            )
    finally:
        command_ingress.close()
