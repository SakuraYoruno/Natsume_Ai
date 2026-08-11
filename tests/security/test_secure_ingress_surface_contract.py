from __future__ import annotations

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.secure_ingress import (
    IngressSurface,
    M48B_P0_SURFACE_POLICIES,
    SECURE_INGRESS_SURFACE_POLICIES,
    build_surface_request,
)


def _identity(policy):
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id="key.test.1",
        algorithm="hmac-sha256",
        proof_id="proof.test.1",
        issued_at_epoch_ms=1,
        expires_at_epoch_ms=2,
    )
    identity = IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=f"service.{policy.surface.value}.test",
            kind=PrincipalKind.SERVICE,
            tenant_id="tenant.test",
            granted_scopes=policy.required_scopes,
        ),
        credential_proof=metadata,
        audience=policy.audience,
        request_id=f"request.{policy.surface.value}.test",
        requested_scopes=policy.required_scopes,
    )
    return identity, CredentialProof(metadata=metadata, raw_proof="0" * 64)


def test_all_write_surfaces_have_unique_server_owned_policy() -> None:
    assert M48B_P0_SURFACE_POLICIES is SECURE_INGRESS_SURFACE_POLICIES
    assert set(M48B_P0_SURFACE_POLICIES) == set(IngressSurface)
    policies = tuple(M48B_P0_SURFACE_POLICIES.values())
    assert len({policy.surface_id for policy in policies}) == len(IngressSurface)
    assert len({policy.action for policy in policies}) == len(IngressSurface)
    assert all(policy.required_scopes for policy in policies)
    assert all(policy.default_path.startswith("/") for policy in policies)
    internal = M48B_P0_SURFACE_POLICIES[IngressSurface.INTERNAL_AGENT]
    scheduler = M48B_P0_SURFACE_POLICIES[IngressSurface.SCHEDULER_ADMIN]
    assert (internal.audience, internal.action, internal.required_scopes) == (
        "internal",
        "internal:agent:execute",
        ("internal:agent:execute",),
    )
    assert (scheduler.audience, scheduler.action, scheduler.required_scopes) == (
        "scheduler",
        "scheduler:admin",
        ("scheduler:admin",),
    )


def test_builder_never_accepts_client_owned_action_or_scope() -> None:
    for policy in M48B_P0_SURFACE_POLICIES.values():
        identity, proof = _identity(policy)
        request = build_surface_request(
            surface=policy.surface,
            identity=identity,
            credential=proof,
            method="POST",
            normalized_path=f"/test/{policy.surface.value}",
            idempotency_key=f"idem.{policy.surface.value}.test",
            nonce=f"nonce.{policy.surface.value}.test",
            raw_body=b"{}",
            received_at_epoch_ms=1,
            transport_key="loopback:surface-test",
        )
        assert request.surface == policy.surface_id
        assert request.action == policy.action
        assert request.required_scopes == policy.required_scopes
        assert request.raw_body == b"{}"


def test_builder_rejects_forged_surface_and_wrong_audience() -> None:
    policy = M48B_P0_SURFACE_POLICIES[IngressSurface.HOST]
    identity, proof = _identity(policy)
    kwargs = {
        "identity": identity,
        "credential": proof,
        "method": "POST",
        "normalized_path": "/test/host",
        "idempotency_key": "idem.host.test",
        "nonce": "nonce.host.test",
        "raw_body": b"{}",
        "received_at_epoch_ms": 1,
        "transport_key": "loopback:surface-test",
    }
    import pytest

    with pytest.raises(ValueError, match="SURFACE_INVALID"):
        build_surface_request(surface="host", **kwargs)  # type: ignore[arg-type]

    wrong_identity = IngressIdentityEnvelope(
        schema_version=identity.schema_version,
        principal=identity.principal,
        credential_proof=identity.credential_proof,
        audience="gateway",
        request_id=identity.request_id,
        requested_scopes=identity.requested_scopes,
    )
    with pytest.raises(ValueError, match="AUDIENCE_MISMATCH"):
        build_surface_request(
            surface=IngressSurface.HOST,
            **{**kwargs, "identity": wrong_identity},
        )
