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
    COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
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
PROFILE_SCOPE = "companion:profile:write"
ROLLBACK_SCOPE = "companion:profile:rollback"
PATH = "/v1/digital-life/relationship/companion-profile"
ROLLBACK_PATH = "/v1/digital-life/relationship/companion-profile/rollback"


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
    scopes: tuple[str, ...] = (PROFILE_SCOPE,),
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
                json.dumps(list(scopes)),
                json.dumps(["host"]),
                json.dumps(list(scopes)),
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
    scopes: tuple[str, ...] = (PROFILE_SCOPE,),
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
    _seed_user_principal(
        database,
        principal_kind=principal_kind,
        scopes=scopes,
    )
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
) -> str:
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
    return aftereffect.aftereffect.aftereffect_id


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


def _rollback_body(
    *,
    expected_active_profile_id: str,
    workspace_id: str = WORKSPACE_ID,
    subject_id: str = SUBJECT_ID,
) -> bytes:
    return json.dumps(
        {
            "schema_version": "companion_profile_rollback_request.v1",
            "workspace_id": workspace_id,
            "relationship_subject_id": subject_id,
            "session_id": SESSION_ID,
            "expected_active_profile_id": expected_active_profile_id,
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
    surface: IngressSurface = IngressSurface.COMPANION_PROFILE,
    path: str = PATH,
    scopes: tuple[str, ...] = (PROFILE_SCOPE,),
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
            granted_scopes=scopes,
        ),
        credential_proof=metadata,
        audience="host",
        request_id=request_id,
        requested_scopes=scopes,
    )
    unsigned = build_surface_request(
        surface=surface,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path=path,
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
        url=SimpleNamespace(path=path),
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


def _activate_profile_for_rollback(
    *,
    relationship: RelationshipLifecycleService,
    profile: CompanionProfileLifecycleService,
    clock: _Clock,
) -> str:
    """Build one canonical active profile; the public test only rolls it back."""

    _seed_eligible_history(relationship=relationship, clock=clock)
    started = profile.start_canary(
        workspace_id=WORKSPACE_ID,
        relationship_subject_id=SUBJECT_ID,
        session_id=SESSION_ID,
        request_id="profile-rollback-canary-start",
        preferences=CompanionProfilePreferencesV1(
            addressing_style="GENTLE_DIRECT",
            response_length="COMPACT",
            humor_level="LOW",
            followup_rate="NONE",
            comfort_mode="QUIET_FIRST",
            proactive_window="EVENING",
            tts_rate_scale=0.9,
            avatar_motion_intensity=0.35,
        ),
    )
    candidate = started.state.canary_profile
    assert candidate is not None
    recorded = None
    for ordinal in range(5, 10):
        aftereffect_id = _helpful_aftereffect(
            relationship=relationship,
            clock=clock,
            ordinal=ordinal,
        )
        recorded = profile.record_eligible_canary_aftereffect(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=f"session-profile-canary-{ordinal}",
            request_id=f"profile-rollback-canary-observation-{ordinal}",
            aftereffect_id=aftereffect_id,
        )
        assert recorded.receipt is not None
        clock.value += DAY_MS
    assert recorded is not None
    activated = recorded.state
    assert activated.active_profile.profile_id == candidate.profile_id
    assert activated.canary_profile is None
    return candidate.profile_id


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


def test_signed_profile_rollback_is_exact_replay_safe_and_scope_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import host_brain.app.digital_life_routes as routes

    (
        database,
        clock,
        reader,
        relationship,
        profile,
        ingress,
        command_ingress,
    ) = _runtime(tmp_path, scopes=(PROFILE_SCOPE, ROLLBACK_SCOPE))
    active_profile_id = _activate_profile_for_rollback(
        relationship=relationship,
        profile=profile,
        clock=clock,
    )
    raw_body = _rollback_body(expected_active_profile_id=active_profile_id)
    request = _signed_request(
        raw_body,
        surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
        path=ROLLBACK_PATH,
        scopes=(ROLLBACK_SCOPE,),
        issued_at_ms=clock.value,
        request_id="companion.profile.rollback.request.1",
        idempotency_key="companion.profile.rollback.idempotency.1",
        nonce="companion.profile.rollback.nonce.1",
    )
    stale_request = _signed_request(
        raw_body,
        surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
        path=ROLLBACK_PATH,
        scopes=(ROLLBACK_SCOPE,),
        issued_at_ms=clock.value,
        request_id="companion.profile.rollback.request.stale-route",
        idempotency_key="companion.profile.rollback.idempotency.stale-route",
        nonce="companion.profile.rollback.nonce.stale-route",
    )
    monkeypatch.setattr(routes.time, "time", lambda: clock.value / 1_000)
    app = FastAPI()
    app.include_router(
        create_digital_life_router(
            HostRoutePorts(companion_profile_ingress_provider=lambda: ingress)
        )
    )
    try:
        with TestClient(app) as client:
            first = client.post(
                ROLLBACK_PATH,
                content=raw_body,
                headers=_http_headers(request),
            )
            duplicate = client.post(
                ROLLBACK_PATH,
                content=raw_body,
                headers=_http_headers(request),
            )
            stale = client.post(
                ROLLBACK_PATH,
                content=raw_body,
                headers=_http_headers(stale_request),
            )
        assert first.status_code == 200, first.text
        assert first.json()["action"] == "ROLLBACK"
        assert first.json()["profile_stage"] == "ACTIVE"
        assert first.json()["profile_id"] != active_profile_id
        assert first.json()["replayed"] is False
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["replayed"] is True
        assert duplicate.json()["profile_id"] == first.json()["profile_id"]
        assert stale.status_code == 409, stale.text
        assert stale.json()["detail"] == "COMPANION_PROFILE_ROLLBACK_ACTIVE_PROFILE_DRIFT"

        scope = RelationshipScope.from_values(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=SESSION_ID,
        )
        rollback_events = [
            event
            for event in reader.list_events(scope.aggregate_id, limit=100)
            if event.event_type == "companion_profile_rolled_back"
        ]
        assert len(rollback_events) == 1
        command_payload = rollback_events[0].payload["command_payload"]
        assert command_payload["rollback_profile_id"] == active_profile_id
        assert command_payload["source_control_ref"].startswith(
            "secure-ingress-audit://"
        )
        assert "rollback_profile_id" not in json.loads(raw_body)

        command_ingress.close()
        restarted_container = build_process_command_ingress_container(
            canonical_db_path=database,
            mode="ENFORCE",
            hmac_secrets={KEY_ID: SECRET},
        )
        restarted = CompanionProfileIngress(
            evaluator=restarted_container.evaluator,
            complete_claim=restarted_container.store.complete_claim,
            companion_profile_lifecycle_service_provider=lambda: profile,
            clock_ms=clock,
        )
        try:
            replay = restarted.submit_rollback(
                request=request,
                raw_body=raw_body,
                received_at_ms=clock.value,
            )
        finally:
            restarted_container.close()
        assert replay.replayed is True
        assert replay.lifecycle.state.active_profile.profile_id == first.json()["profile_id"]
        assert [
            event.event_type
            for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_rolled_back") == 1
    finally:
        if not getattr(command_ingress, "_closed", True):
            command_ingress.close()


def test_profile_rollback_reopens_after_claim_result_loss(tmp_path: Path) -> None:
    (
        _database,
        clock,
        reader,
        relationship,
        profile,
        ingress,
        command_ingress,
    ) = _runtime(tmp_path, scopes=(PROFILE_SCOPE, ROLLBACK_SCOPE))
    active_profile_id = _activate_profile_for_rollback(
        relationship=relationship,
        profile=profile,
        clock=clock,
    )
    raw_body = _rollback_body(expected_active_profile_id=active_profile_id)
    request = _signed_request(
        raw_body,
        surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
        path=ROLLBACK_PATH,
        scopes=(ROLLBACK_SCOPE,),
        issued_at_ms=clock.value,
        request_id="companion.profile.rollback.request.claim-loss",
        idempotency_key="companion.profile.rollback.idempotency.claim-loss",
        nonce="companion.profile.rollback.nonce.claim-loss",
    )

    def _lose_claim_result(**_kwargs: object) -> None:
        raise RuntimeError("simulated profile rollback response-loss")

    interrupted = CompanionProfileIngress(
        evaluator=command_ingress.evaluator,
        complete_claim=_lose_claim_result,
        companion_profile_lifecycle_service_provider=lambda: profile,
        clock_ms=clock,
    )
    scope = RelationshipScope.from_values(
        workspace_id=WORKSPACE_ID,
        relationship_subject_id=SUBJECT_ID,
        session_id=SESSION_ID,
    )
    try:
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_RESULT_RECORD_UNAVAILABLE,
        ):
            interrupted.submit_rollback(
                request=request,
                raw_body=raw_body,
                received_at_ms=clock.value,
            )
        assert [
            event.event_type
            for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_rolled_back") == 1

        recovered = ingress.submit_rollback(
            request=request,
            raw_body=raw_body,
            received_at_ms=clock.value,
        )
        assert recovered.replayed is True
        assert recovered.lifecycle.receipt is not None
        assert [
            event.event_type
            for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_rolled_back") == 1
    finally:
        command_ingress.close()


def test_profile_rollback_rejects_stale_cross_scope_service_and_target_injection(
    tmp_path: Path,
) -> None:
    service_root = tmp_path / "service"
    service_root.mkdir()
    (
        _database,
        clock,
        _reader,
        relationship,
        profile,
        ingress,
        command_ingress,
    ) = _runtime(tmp_path, scopes=(PROFILE_SCOPE, ROLLBACK_SCOPE))
    active_profile_id = _activate_profile_for_rollback(
        relationship=relationship,
        profile=profile,
        clock=clock,
    )
    raw_body = _rollback_body(expected_active_profile_id=active_profile_id)
    try:
        accepted = ingress.submit_rollback(
            request=_signed_request(
                raw_body,
                surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
                path=ROLLBACK_PATH,
                scopes=(ROLLBACK_SCOPE,),
                issued_at_ms=clock.value,
                request_id="companion.profile.rollback.request.accepted",
                idempotency_key="companion.profile.rollback.idempotency.accepted",
                nonce="companion.profile.rollback.nonce.accepted",
            ),
            raw_body=raw_body,
            received_at_ms=clock.value,
        )
        assert accepted.lifecycle.state.active_profile.profile_id != active_profile_id

        with pytest.raises(
            CompanionProfileIngressError,
            match="COMPANION_PROFILE_ROLLBACK_ACTIVE_PROFILE_DRIFT",
        ):
            ingress.submit_rollback(
                request=_signed_request(
                    raw_body,
                    surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
                    path=ROLLBACK_PATH,
                    scopes=(ROLLBACK_SCOPE,),
                    issued_at_ms=clock.value,
                    request_id="companion.profile.rollback.request.stale",
                    idempotency_key="companion.profile.rollback.idempotency.stale",
                    nonce="companion.profile.rollback.nonce.stale",
                ),
                raw_body=raw_body,
                received_at_ms=clock.value,
            )
        cross_scope = _rollback_body(
            expected_active_profile_id=active_profile_id,
            subject_id="other-person",
        )
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_SCOPE_BINDING_DENIED,
        ):
            ingress.submit_rollback(
                request=_signed_request(
                    cross_scope,
                    surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
                    path=ROLLBACK_PATH,
                    scopes=(ROLLBACK_SCOPE,),
                    issued_at_ms=clock.value,
                    request_id="companion.profile.rollback.request.cross",
                    idempotency_key="companion.profile.rollback.idempotency.cross",
                    nonce="companion.profile.rollback.nonce.cross",
                ),
                raw_body=cross_scope,
                received_at_ms=clock.value,
            )
        malformed = json.loads(raw_body)
        malformed["rollback_profile_id"] = "companion-profile::attacker-selected"
        malformed_body = json.dumps(
            malformed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_BODY_INVALID,
        ):
            ingress.submit_rollback(
                request=_signed_request(
                    malformed_body,
                    surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
                    path=ROLLBACK_PATH,
                    scopes=(ROLLBACK_SCOPE,),
                    issued_at_ms=clock.value,
                    request_id="companion.profile.rollback.request.malformed",
                    idempotency_key="companion.profile.rollback.idempotency.malformed",
                    nonce="companion.profile.rollback.nonce.malformed",
                ),
                raw_body=malformed_body,
                received_at_ms=clock.value,
            )
    finally:
        command_ingress.close()

    (
        _database,
        service_clock,
        _reader,
        _relationship,
        _profile,
        service_ingress,
        service_command_ingress,
    ) = _runtime(
        service_root,
        principal_kind="service",
        scopes=(PROFILE_SCOPE, ROLLBACK_SCOPE),
    )
    try:
        service_body = _rollback_body(
            expected_active_profile_id="companion-profile::" + "0" * 40
        )
        with pytest.raises(
            CompanionProfileIngressError,
            match=COMPANION_PROFILE_PRINCIPAL_KIND_DENIED,
        ):
            service_ingress.submit_rollback(
                request=_signed_request(
                    service_body,
                    principal_kind=PrincipalKind.SERVICE,
                    surface=IngressSurface.COMPANION_PROFILE_ROLLBACK,
                    path=ROLLBACK_PATH,
                    scopes=(ROLLBACK_SCOPE,),
                    issued_at_ms=service_clock.value,
                    request_id="companion.profile.rollback.request.service",
                    idempotency_key="companion.profile.rollback.idempotency.service",
                    nonce="companion.profile.rollback.nonce.service",
                ),
                raw_body=service_body,
                received_at_ms=service_clock.value,
            )
    finally:
        service_command_ingress.close()
