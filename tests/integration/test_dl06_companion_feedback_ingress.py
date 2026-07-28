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
from host_brain.digital_life.companion_feedback_ingress import (
    COMPANION_FEEDBACK_BODY_INVALID,
    COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED,
    COMPANION_FEEDBACK_SCOPE_BINDING_DENIED,
    CompanionFeedbackIngress,
    CompanionFeedbackIngressError,
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


NOW = 1_903_000_000_000
DAY_MS = 86_400_000
WORKSPACE_ID = "workspace-feedback-control"
SUBJECT_ID = "person-feedback-control"
SESSION_ID = "session-feedback-control"
KEY_ID = "companion.feedback.user.hmac.v1"
SECRET = "companion-feedback-user-secret-at-least-32-bytes"
PATH = "/v1/digital-life/relationship/response-feedback"


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
                json.dumps(["companion:feedback:write"]),
                json.dumps(["host"]),
                json.dumps(["companion:feedback:write"]),
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
                "test-secret-ref://companion/feedback/user",
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
    CompanionFeedbackIngress,
    object,
]:
    database = tmp_path / "dl06-companion-feedback-ingress.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl06-companion-feedback-ingress",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    _seed_user_principal(database, principal_kind=principal_kind)
    clock = _Clock(NOW)
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=clock)
    reader = LifeSnapshotReader(repository)
    profile = CompanionProfileLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    relationship = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        companion_profile_lifecycle_service_provider=lambda: profile,
        clock_ms=clock,
    )
    command_ingress = build_process_command_ingress_container(
        canonical_db_path=database,
        mode="ENFORCE",
        hmac_secrets={KEY_ID: SECRET},
    )
    ingress = CompanionFeedbackIngress(
        evaluator=command_ingress.evaluator,
        complete_claim=command_ingress.store.complete_claim,
        relationship_lifecycle_service_provider=lambda: relationship,
        clock_ms=clock,
    )
    return database, clock, reader, relationship, profile, ingress, command_ingress


def _accepted_response(*, ordinal: int, created_at_ms: int) -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": f"feedback-control-foreground-{ordinal}",
        "command_id": f"feedback-control-foreground-command-{ordinal}",
        "created_at_ms": created_at_ms,
    }


def _open_response(
    *,
    relationship: RelationshipLifecycleService,
    clock: _Clock,
    ordinal: int,
) -> str:
    receipt = _accepted_response(ordinal=ordinal, created_at_ms=clock.value)
    relationship.provision_accepted_response(
        workspace_id=WORKSPACE_ID,
        relationship_subject_id=SUBJECT_ID,
        session_id=f"session-feedback-{ordinal}",
        request_id=f"feedback-control-response-{ordinal}",
        response_receipt=receipt,
        response_text="我在，先陪你把这一段安静地走完。",
    )
    return f"life-event://{receipt['event_id']}"


def _seed_strong_history(
    *,
    relationship: RelationshipLifecycleService,
    clock: _Clock,
) -> list[str]:
    result: list[str] = []
    for ordinal in range(5):
        response_ref = _open_response(
            relationship=relationship,
            clock=clock,
            ordinal=ordinal,
        )
        clock.value += 1_000
        feedback = relationship.record_explicit_response_feedback(
            workspace_id=WORKSPACE_ID,
            relationship_subject_id=SUBJECT_ID,
            session_id=f"session-feedback-{ordinal}",
            request_id=f"feedback-control-history-{ordinal}",
            response_receipt_ref=response_ref,
            feedback="HELPFUL",
            source_ref=f"secure-ingress-audit://seed-feedback-{ordinal}",
        )
        assert feedback.aftereffect is not None
        assert feedback.aftereffect.helpfulness == 0.95
        result.append(feedback.aftereffect.aftereffect_id)
        clock.value += DAY_MS
    return result


def _body(
    *,
    response_receipt_ref: str,
    workspace_id: str = WORKSPACE_ID,
    subject_id: str = SUBJECT_ID,
    feedback: str = "HELPFUL",
) -> bytes:
    return json.dumps(
        {
            "schema_version": "companion_response_feedback_request.v1",
            "workspace_id": workspace_id,
            "relationship_subject_id": subject_id,
            "session_id": SESSION_ID,
            "response_receipt_ref": response_receipt_ref,
            "feedback": feedback,
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
    request_id: str = "companion.feedback.request.1",
    idempotency_key: str = "companion.feedback.idempotency.1",
    nonce: str = "companion.feedback.nonce.1",
) -> object:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="companion.feedback.proof.1",
        issued_at_epoch_ms=issued_at_ms,
        expires_at_epoch_ms=issued_at_ms + 300_000,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=principal_id,
            kind=principal_kind,
            tenant_id=tenant_id,
            granted_scopes=("companion:feedback:write",),
        ),
        credential_proof=metadata,
        audience="host",
        request_id=request_id,
        requested_scopes=("companion:feedback:write",),
    )
    unsigned = build_surface_request(
        surface=IngressSurface.COMPANION_FEEDBACK,
        identity=identity,
        credential=CredentialProof(metadata=metadata, raw_proof="0" * 64),
        method="POST",
        normalized_path=PATH,
        idempotency_key=idempotency_key,
        nonce=nonce,
        raw_body=raw_body,
        received_at_epoch_ms=issued_at_ms,
        transport_key=f"principal:{principal_id}",
        correlation_id="companion.feedback.correlation.1",
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
                (b"x-natsume-correlation-id", b"companion.feedback.correlation.1"),
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


def test_signed_feedback_is_receipt_bound_replay_safe_and_auto_activates_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import host_brain.app.digital_life_routes as routes

    database, clock, reader, relationship, profile, ingress, command_ingress = _runtime(tmp_path)
    _seed_strong_history(relationship=relationship, clock=clock)
    scope = RelationshipScope.from_values(
        workspace_id=WORKSPACE_ID,
        relationship_subject_id=SUBJECT_ID,
        session_id=SESSION_ID,
    )
    before_control = reader.get_snapshot(scope.aggregate_id).state["companion_profile"]
    assert before_control["canary_profile"] is None
    assert before_control["active_profile"]["is_baseline"] is True
    started = profile.start_canary(
        workspace_id=WORKSPACE_ID,
        relationship_subject_id=SUBJECT_ID,
        session_id=SESSION_ID,
        request_id="feedback-control-profile-start",
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
        evidence_aftereffect_ids=None,
        source_control_ref="companion-profile-request://feedback-history-canary",
    )
    assert started.state.canary_profile is not None
    monkeypatch.setattr(routes.time, "time", lambda: clock.value / 1_000)
    app = FastAPI()
    app.include_router(
        create_digital_life_router(
            HostRoutePorts(companion_feedback_ingress_provider=lambda: ingress)
        )
    )
    final_request: object | None = None
    final_body: bytes | None = None
    try:
        with TestClient(app) as client:
            for offset in range(5):
                response_ref = _open_response(
                    relationship=relationship,
                    clock=clock,
                    ordinal=99 + offset,
                )
                clock.value += 1_000
                raw_body = _body(response_receipt_ref=response_ref)
                request = _signed_request(
                    raw_body,
                    issued_at_ms=clock.value,
                    request_id=f"companion.feedback.request.canary.{offset}",
                    idempotency_key=f"companion.feedback.idempotency.canary.{offset}",
                    nonce=f"companion.feedback.nonce.canary.{offset}",
                )
                first = client.post(
                    PATH,
                    content=raw_body,
                    headers=_http_headers(request),
                )
                assert first.status_code == 200, first.text
                assert first.json()["feedback"] == "HELPFUL"
                assert first.json()["aftereffect_outcome"] == "HELPFUL"
                assert first.json()["replayed"] is False
                if offset == 0:
                    duplicate = client.post(
                        PATH,
                        content=raw_body,
                        headers=_http_headers(request),
                    )
                    assert duplicate.status_code == 200, duplicate.text
                    assert duplicate.json()["replayed"] is True
                    assert duplicate.json()["aftereffect_id"] == first.json()["aftereffect_id"]
                final_request = request
                final_body = raw_body
                clock.value += 1_000

        state = reader.get_snapshot(scope.aggregate_id).state["companion_profile"]
        assert state["canary_profile"] is None
        assert state["active_profile"]["is_baseline"] is False
        assert state["canary_accepted_interaction_ids"] == []
        events = reader.list_events(scope.aggregate_id, limit=100)
        assert [event.event_type for event in events].count(
            "relational_aftereffect_recorded"
        ) == 10
        assert [event.event_type for event in events].count(
            "companion_profile_canary_interaction_recorded"
        ) == 5
        assert [event.event_type for event in events].count(
            "companion_profile_activated"
        ) == 1

        command_ingress.close()
        restarted_container = build_process_command_ingress_container(
            canonical_db_path=database,
            mode="ENFORCE",
            hmac_secrets={KEY_ID: SECRET},
        )
        restarted = CompanionFeedbackIngress(
            evaluator=restarted_container.evaluator,
            complete_claim=restarted_container.store.complete_claim,
            relationship_lifecycle_service_provider=lambda: relationship,
            clock_ms=clock,
        )
        try:
            assert final_request is not None
            assert final_body is not None
            replay = restarted.submit(
                request=final_request,
                raw_body=final_body,
                received_at_ms=clock.value,
            )
        finally:
            restarted_container.close()
        assert replay.replayed is True
        assert [
            event.event_type for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_canary_interaction_recorded") == 5
        assert [
            event.event_type for event in reader.list_events(scope.aggregate_id, limit=100)
        ].count("companion_profile_activated") == 1
    finally:
        if not getattr(command_ingress, "_closed", True):
            command_ingress.close()


def test_feedback_rejects_cross_scope_service_inactive_receipt_and_malformed_body(
    tmp_path: Path,
) -> None:
    _database, clock, _reader, relationship, _profile, ingress, command_ingress = _runtime(tmp_path)
    response_ref = _open_response(
        relationship=relationship,
        clock=clock,
        ordinal=1,
    )
    raw_body = _body(response_receipt_ref=response_ref)
    try:
        with pytest.raises(
            CompanionFeedbackIngressError,
            match=COMPANION_FEEDBACK_SCOPE_BINDING_DENIED,
        ):
            ingress.submit(
                request=_signed_request(
                    _body(response_receipt_ref=response_ref, subject_id="other-person"),
                    issued_at_ms=clock.value,
                    request_id="companion.feedback.request.cross",
                    idempotency_key="companion.feedback.idempotency.cross",
                    nonce="companion.feedback.nonce.cross",
                ),
                raw_body=_body(response_receipt_ref=response_ref, subject_id="other-person"),
                received_at_ms=clock.value,
            )
        inactive = _body(response_receipt_ref="life-event://not-an-open-response")
        with pytest.raises(
            CompanionFeedbackIngressError,
            match="RELATIONSHIP_RESPONSE_RECEIPT_NOT_ACTIVE",
        ):
            ingress.submit(
                request=_signed_request(
                    inactive,
                    issued_at_ms=clock.value,
                    request_id="companion.feedback.request.inactive",
                    idempotency_key="companion.feedback.idempotency.inactive",
                    nonce="companion.feedback.nonce.inactive",
                ),
                raw_body=inactive,
                received_at_ms=clock.value,
            )
        malformed = (
            b'{"schema_version":"companion_response_feedback_request.v1",'
            b'"workspace_id":"workspace-feedback-control",'
            b'"relationship_subject_id":"person-feedback-control",'
            b'"session_id":"session-feedback-control",'
            b'"response_receipt_ref":"life-event://x",'
            b'"feedback":"HELPFUL","rating":5}'
        )
        with pytest.raises(
            CompanionFeedbackIngressError,
            match=COMPANION_FEEDBACK_BODY_INVALID,
        ):
            ingress.submit(
                request=_signed_request(
                    malformed,
                    issued_at_ms=clock.value,
                    request_id="companion.feedback.request.malformed",
                    idempotency_key="companion.feedback.idempotency.malformed",
                    nonce="companion.feedback.nonce.malformed",
                ),
                raw_body=malformed,
                received_at_ms=clock.value,
            )
    finally:
        command_ingress.close()

    (
        _service_database,
        service_clock,
        _service_reader,
        service_relationship,
        _service_profile,
        service_ingress,
        service_command_ingress,
    ) = _runtime(tmp_path / "service-principal", principal_kind="service")
    service_response_ref = _open_response(
        relationship=service_relationship,
        clock=service_clock,
        ordinal=2,
    )
    service_body = _body(response_receipt_ref=service_response_ref)
    try:
        with pytest.raises(
            CompanionFeedbackIngressError,
            match=COMPANION_FEEDBACK_PRINCIPAL_KIND_DENIED,
        ):
            service_ingress.submit(
                request=_signed_request(
                    service_body,
                    principal_kind=PrincipalKind.SERVICE,
                    issued_at_ms=service_clock.value,
                    request_id="companion.feedback.request.service",
                    idempotency_key="companion.feedback.idempotency.service",
                    nonce="companion.feedback.nonce.service",
                ),
                raw_body=service_body,
                received_at_ms=service_clock.value,
            )
    finally:
        service_command_ingress.close()
