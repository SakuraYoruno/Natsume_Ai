from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentityContractError,
    IdentityReasonCode,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
    ScopeMatchMode,
    match_scopes,
    parse_identity_envelope,
)


def _wire_payload() -> dict[str, object]:
    return {
        "schema_version": "natsume.identity.v1",
        "principal": {
            "principal_id": "service:edge-gateway",
            "kind": "service",
            "tenant_id": "tenant:local-test",
            "granted_scopes": ["gateway:read", "pentest:*"],
        },
        "credential_proof": {
            "kind": "hmac-sha256",
            "key_id": "test-key-2026-07",
            "algorithm": "HMAC-SHA256",
            "proof_id": "proof:req-0487",
            "issued_at_epoch_ms": 1783700000000,
            "expires_at_epoch_ms": 1783700060000,
        },
        "audience": "host-brain:secure-ingress",
        "request_id": "req:task-0487",
        "requested_scopes": ["pentest:run", "gateway:read"],
    }


def test_canonical_round_trip_is_deterministic_and_immutable() -> None:
    envelope = IngressIdentityEnvelope.from_mapping(_wire_payload())

    assert envelope.schema_version is IdentitySchemaVersion.V1
    assert envelope.principal.kind is PrincipalKind.SERVICE
    assert envelope.credential_proof.kind is CredentialProofKind.HMAC_SHA256
    assert envelope.requested_scopes == ("gateway:read", "pentest:run")
    assert envelope == IngressIdentityEnvelope.from_canonical_json(envelope.to_canonical_json())
    assert envelope.to_canonical_json() == parse_identity_envelope(
        envelope.to_canonical_json().encode("utf-8")
    ).to_canonical_json()
    assert envelope.to_canonical_json() == json.dumps(
        envelope.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )

    with pytest.raises(FrozenInstanceError):
        envelope.request_id = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        envelope.principal.tenant_id = "mutated"  # type: ignore[misc]


def test_runtime_raw_proof_is_explicit_but_never_serialized_or_logged() -> None:
    envelope = IngressIdentityEnvelope.from_mapping(_wire_payload())
    secret = "Aa0123456789bcde" * 4
    runtime_proof = CredentialProof(metadata=envelope.credential_proof, raw_proof=secret)

    assert runtime_proof.raw_proof == secret
    assert secret not in repr(runtime_proof)
    assert secret not in envelope.to_canonical_json()
    audit_json = json.dumps(runtime_proof.audit_dict(), sort_keys=True)
    assert secret not in audit_json
    assert runtime_proof.audit_dict()["raw_proof"] == "[REDACTED]"
    assert runtime_proof.audit_dict()["metadata"] == envelope.credential_proof.to_dict()


@pytest.mark.parametrize(
    ("field_path", "reason_code"),
    [
        ("schema_version", IdentityReasonCode.FIELD_MISSING.value),
        ("principal", IdentityReasonCode.FIELD_MISSING.value),
        ("credential_proof", IdentityReasonCode.FIELD_MISSING.value),
        ("audience", IdentityReasonCode.FIELD_MISSING.value),
        ("request_id", IdentityReasonCode.FIELD_MISSING.value),
        ("requested_scopes", IdentityReasonCode.FIELD_MISSING.value),
    ],
)
def test_every_missing_envelope_field_fails_closed_with_stable_code(
    field_path: str, reason_code: str
) -> None:
    payload = _wire_payload()
    payload.pop(field_path)

    with pytest.raises(IdentityContractError) as caught:
        IngressIdentityEnvelope.from_mapping(payload)

    assert caught.value.reason_code == reason_code
    assert caught.value.field_path == f"identity_envelope.{field_path}"


@pytest.mark.parametrize(
    ("container", "missing_field", "expected_path"),
    [
        ("principal", "principal_id", "principal.principal_id"),
        ("principal", "kind", "principal.kind"),
        ("principal", "tenant_id", "principal.tenant_id"),
        ("principal", "granted_scopes", "principal.granted_scopes"),
        ("credential_proof", "kind", "credential_proof.kind"),
        ("credential_proof", "key_id", "credential_proof.key_id"),
        ("credential_proof", "algorithm", "credential_proof.algorithm"),
        ("credential_proof", "proof_id", "credential_proof.proof_id"),
        (
            "credential_proof",
            "issued_at_epoch_ms",
            "credential_proof.issued_at_epoch_ms",
        ),
    ],
)
def test_every_missing_nested_identity_field_fails_closed(
    container: str, missing_field: str, expected_path: str
) -> None:
    payload = _wire_payload()
    nested = dict(payload[container])  # type: ignore[arg-type]
    nested.pop(missing_field)
    payload[container] = nested

    with pytest.raises(IdentityContractError) as caught:
        IngressIdentityEnvelope.from_mapping(payload)

    assert caught.value.reason_code == IdentityReasonCode.FIELD_MISSING.value
    assert caught.value.field_path == expected_path


@pytest.mark.parametrize("secret_field", ["raw_token", "shared_secret", "signature", "authorization"])
def test_secret_material_is_rejected_from_auditable_metadata(secret_field: str) -> None:
    payload = _wire_payload()
    credential = dict(payload["credential_proof"])  # type: ignore[arg-type]
    credential[secret_field] = "must-not-be-accepted"
    payload["credential_proof"] = credential

    with pytest.raises(IdentityContractError) as caught:
        IngressIdentityEnvelope.from_mapping(payload)

    assert caught.value.reason_code == IdentityReasonCode.SECRET_MATERIAL_FORBIDDEN.value
    assert caught.value.field_path == f"credential_proof.{secret_field}"


def test_missing_or_empty_runtime_proof_has_stable_reason_code() -> None:
    metadata = IngressIdentityEnvelope.from_mapping(_wire_payload()).credential_proof

    with pytest.raises(IdentityContractError) as caught:
        CredentialProof(metadata=metadata, raw_proof="")

    assert caught.value.reason_code == IdentityReasonCode.CREDENTIAL_PROOF_MISSING.value
    assert caught.value.field_path == "credential.raw_proof"


@pytest.mark.parametrize(
    "valid_proof",
    [
        "0123456789abcdef" * 4,
        "0123456789ABCDEF" * 4,
        b"aBcDeF0123456789" * 4,
    ],
)
def test_hmac_sha256_proof_accepts_only_64_ascii_hex_characters(
    valid_proof: str | bytes,
) -> None:
    metadata = IngressIdentityEnvelope.from_mapping(_wire_payload()).credential_proof

    proof = CredentialProof(metadata=metadata, raw_proof=valid_proof)

    assert proof.raw_proof == valid_proof


@pytest.mark.parametrize(
    "invalid_proof",
    [
        "a" * 63,
        "a" * 65,
        "g" * 64,
        ("a" * 63) + "é",
        b"a" * 63,
        b"a" * 65,
        (b"a" * 63) + b"g",
        (b"a" * 63) + b"\x00",
    ],
)
def test_hmac_sha256_bad_hex_or_length_fails_closed(invalid_proof: str | bytes) -> None:
    metadata = IngressIdentityEnvelope.from_mapping(_wire_payload()).credential_proof

    with pytest.raises(IdentityContractError) as caught:
        CredentialProof(metadata=metadata, raw_proof=invalid_proof)

    assert caught.value.reason_code in {
        IdentityReasonCode.CREDENTIAL_PROOF_INVALID.value,
        IdentityReasonCode.CREDENTIAL_PROOF_TOO_LARGE.value,
    }
    assert caught.value.field_path == "credential.raw_proof"


def _proof_metadata(kind: CredentialProofKind) -> CredentialProofMetadata:
    return CredentialProofMetadata(
        kind=kind,
        key_id="test-key-boundary",
        algorithm="TEST",
        proof_id=f"proof:{kind.value}",
        issued_at_epoch_ms=1783700000000,
    )


@pytest.mark.parametrize(
    ("kind", "max_bytes"),
    [
        (CredentialProofKind.BEARER_OPAQUE, 4096),
        (CredentialProofKind.JWT, 8192),
        (CredentialProofKind.API_KEY, 1024),
        (CredentialProofKind.MTLS_CERTIFICATE, 16384),
    ],
)
def test_non_hmac_proof_has_explicit_byte_limit(
    kind: CredentialProofKind,
    max_bytes: int,
) -> None:
    metadata = _proof_metadata(kind)

    assert CredentialProof(metadata=metadata, raw_proof=b"a" * max_bytes).raw_proof == b"a" * max_bytes
    with pytest.raises(IdentityContractError) as caught:
        CredentialProof(metadata=metadata, raw_proof=b"a" * (max_bytes + 1))

    assert caught.value.reason_code == IdentityReasonCode.CREDENTIAL_PROOF_TOO_LARGE.value
    assert caught.value.field_path == "credential.raw_proof"


@pytest.mark.parametrize(
    "kind",
    [
        CredentialProofKind.BEARER_OPAQUE,
        CredentialProofKind.JWT,
        CredentialProofKind.API_KEY,
        CredentialProofKind.MTLS_CERTIFICATE,
    ],
)
@pytest.mark.parametrize("invalid_proof", ["", "abc\ndef", b"abc\x00def", b"abc\x7fdef"])
def test_non_hmac_proof_rejects_empty_and_control_characters(
    kind: CredentialProofKind,
    invalid_proof: str | bytes,
) -> None:
    with pytest.raises(IdentityContractError) as caught:
        CredentialProof(metadata=_proof_metadata(kind), raw_proof=invalid_proof)

    expected = (
        IdentityReasonCode.CREDENTIAL_PROOF_MISSING.value
        if not invalid_proof
        else IdentityReasonCode.CREDENTIAL_PROOF_INVALID.value
    )
    assert caught.value.reason_code == expected
    assert caught.value.field_path == "credential.raw_proof"


def test_scope_matching_supports_exact_and_final_wildcard_without_escalation() -> None:
    envelope = IngressIdentityEnvelope.from_mapping(_wire_payload())
    allowed = envelope.evaluate_scopes()
    assert allowed.allowed is True
    assert allowed.reason_code is None
    assert allowed.matched_scopes == ("gateway:read", "pentest:run")

    denied = match_scopes(
        envelope.principal.granted_scopes,
        ("gateway:write", "pentest:run"),
    )
    assert denied.allowed is False
    assert denied.reason_code == IdentityReasonCode.SCOPE_DENIED.value
    assert denied.unmatched_scopes == ("gateway:write",)

    any_result = match_scopes(
        envelope.principal.granted_scopes,
        ("gateway:write", "pentest:run"),
        mode=ScopeMatchMode.ANY,
    )
    assert any_result.allowed is True
    assert any_result.matched_scopes == ("pentest:run",)

    assert match_scopes(("*",), ("admin:dangerous",)).allowed is True


@pytest.mark.parametrize("invalid_scope", ["", "Pentest:run", "pentest:*:run", "pentest: run"])
def test_invalid_scope_fails_closed(invalid_scope: str) -> None:
    with pytest.raises(IdentityContractError) as caught:
        match_scopes(("pentest:*",), (invalid_scope,))

    assert caught.value.reason_code == IdentityReasonCode.SCOPE_INVALID.value


def test_empty_required_scope_set_does_not_implicitly_authorize() -> None:
    with pytest.raises(IdentityContractError) as caught:
        match_scopes(("*",), ())

    assert caught.value.reason_code == IdentityReasonCode.SCOPE_INVALID.value


def test_unknown_version_malformed_json_and_duplicate_keys_have_stable_codes() -> None:
    payload = _wire_payload()
    payload["schema_version"] = "natsume.identity.v2"
    with pytest.raises(IdentityContractError) as unsupported:
        IngressIdentityEnvelope.from_mapping(payload)
    assert unsupported.value.reason_code == IdentityReasonCode.VERSION_UNSUPPORTED.value

    with pytest.raises(IdentityContractError) as malformed:
        parse_identity_envelope("{")
    assert malformed.value.reason_code == IdentityReasonCode.PAYLOAD_INVALID.value

    duplicate = '{"schema_version":"natsume.identity.v1","schema_version":"natsume.identity.v1"}'
    with pytest.raises(IdentityContractError) as duplicated:
        parse_identity_envelope(duplicate)
    assert duplicated.value.reason_code == IdentityReasonCode.FIELD_UNEXPECTED.value


def test_contract_has_no_host_edge_framework_or_storage_dependency() -> None:
    import natsume_contracts.identity.v1 as identity_contract

    source_names = set(identity_contract.__dict__)
    assert "host_brain" not in source_names
    assert "edge_client" not in source_names
    assert "fastapi" not in source_names
    assert "sqlite3" not in source_names
