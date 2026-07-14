from __future__ import annotations

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace

import pytest
from natsume_contracts.identity import CredentialProofKind, PrincipalKind

from host_brain.composition import scheduler as composition
from host_brain.scheduler.command_ingress import SchedulerClockSigningContext
from host_brain.secure_ingress import (
    IngressSurface,
    build_hmac_sha256_signature,
    build_surface_request,
)


def _context(*, suffix: str, body_sha256: str) -> SchedulerClockSigningContext:
    return SchedulerClockSigningContext(
        task_id=f"task.{suffix}",
        definition_revision=1,
        scheduled_at_epoch_ms=1_800_000_000_000,
        received_at_epoch_ms=1_800_000_000_100,
        request_id=f"scheduler.clock.request.{suffix}",
        audience="scheduler",
        surface="scheduler.admin",
        action="scheduler:admin",
        required_scopes=("scheduler:admin",),
        normalized_path="/internal/scheduler/clock",
        body_sha256=body_sha256,
    )


def _request(identity, *, suffix: str):
    return build_surface_request(
        surface=IngressSurface.SCHEDULER_ADMIN,
        identity=identity,
        credential=None,
        method="POST",
        normalized_path="/internal/scheduler/clock",
        idempotency_key=f"idem.clock.{suffix}",
        nonce=f"nonce.clock.{suffix}",
        raw_body=f'{{"suffix":"{suffix}"}}'.encode(),
        received_at_epoch_ms=1_800_000_000_100,
        transport_key="principal:clock",
        correlation_id=f"clock.{suffix}",
    )


def test_clock_credential_provider_is_stateless_across_interleaved_requests() -> None:
    secret = "scheduler-clock-secret-for-unit-tests"
    provider = composition.HmacSchedulerClockCredentialProvider(
        composition.SchedulerClockIdentityConfig(
            principal_id="service.scheduler.clock.test",
            tenant_id="tenant.scheduler.test",
            key_id="key.scheduler.clock.test",
            secret=secret,
        )
    )
    first_identity = provider.identity_for(
        context=_context(suffix="first", body_sha256="a" * 64)
    )
    first_request = _request(first_identity, suffix="first")
    second_identity = provider.identity_for(
        context=_context(suffix="second", body_sha256="b" * 64)
    )
    second_request = _request(second_identity, suffix="second")

    # Deliberately issue the first proof after a second identity was created.
    first_proof = provider.issue_proof(request=first_request)
    second_proof = provider.issue_proof(request=second_request)

    assert first_proof.metadata == first_identity.credential_proof
    assert second_proof.metadata == second_identity.credential_proof
    assert first_proof.raw_proof == build_hmac_sha256_signature(first_request, secret)
    assert second_proof.raw_proof == build_hmac_sha256_signature(second_request, secret)


def test_clock_identity_is_read_only_validated_against_registered_service() -> None:
    config = composition.SchedulerClockIdentityConfig(
        principal_id="service.scheduler.clock.test",
        tenant_id="tenant.scheduler.test",
        key_id="key.scheduler.clock.test",
        secret="scheduler-clock-secret-for-unit-tests",
    )
    authority = SimpleNamespace(
        enabled=True,
        kind=PrincipalKind.SERVICE,
        tenant_id=config.tenant_id,
        credential_kind=CredentialProofKind.HMAC_SHA256,
        algorithm="hmac-sha256",
        granted_scopes=("scheduler:admin",),
        allowed_audiences=("scheduler",),
        allowed_actions=("scheduler:admin",),
    )

    class Store:
        def __init__(self, value):
            self.value = value
            self.calls = []

        def resolve(self, **kwargs):
            self.calls.append(kwargs)
            return self.value

    store = Store(authority)
    composition._validate_registered_clock_identity(
        authority_store=store,
        config=config,
        at_ms=1_800_000_000_000,
    )
    assert store.calls == [
        {
            "principal_id": config.principal_id,
            "key_id": config.key_id,
            "at_ms": 1_800_000_000_000,
        }
    ]

    with pytest.raises(composition.SchedulerCompositionError) as rejected:
        composition._validate_registered_clock_identity(
            authority_store=Store(None),
            config=config,
            at_ms=1_800_000_000_000,
        )
    assert rejected.value.reason_code == composition.SCHEDULER_CLOCK_IDENTITY_INVALID


def test_seed_cron_defaults_to_builtin_utc_without_tzdata(
    monkeypatch,
) -> None:
    def reject_zoneinfo(_name: str):
        raise composition.ZoneInfoNotFoundError("tzdata unavailable")

    monkeypatch.setattr(composition, "ZoneInfo", reject_zoneinfo)
    now = int(datetime(2026, 7, 12, 22, 0, tzinfo=timezone.utc).timestamp() * 1_000)
    task_id, definition, next_at = composition.scheduler_seed_payload(
        {
            "task_id": "task.utc.cron",
            "definition": {
                "task_id": "task.utc.cron",
                "trigger_mode": "cron",
                "trigger_config": {"hour": 23, "minute": 0},
                "intent_template": {"skill_name": "noop", "args": {}},
                "priority": "LOW",
                "enabled": True,
                "retry_policy": {"max_attempts": 1, "retry_backoff_ms": 0},
                "resource_profile": {},
                "worker_key": "scheduler_production",
            },
        },
        now_ms=now,
    )
    assert task_id == "task.utc.cron"
    assert definition["worker_key"] == "scheduler_production"
    assert next_at == int(
        datetime(2026, 7, 12, 23, 0, tzinfo=timezone.utc).timestamp() * 1_000
    )


def test_scheduler_composition_has_no_app_or_sqlite_write() -> None:

    source = inspect.getsource(composition)
    assert "host_brain.app" not in source
    assert "sqlite3" not in source
    assert "INSERT INTO secure_ingress" not in source
