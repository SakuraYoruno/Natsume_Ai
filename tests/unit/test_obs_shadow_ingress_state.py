from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from host_brain.observability.shadow_ingress_state import (
    ShadowIngressStateError,
    ShadowIngressStateStore,
)
from host_brain.secure_ingress import ClaimDecision, ClaimStatus, IngressAuditEvent


BODY_SHA256 = "a" * 64
SCOPES = ("obs:sys-mode:write", "obs:write")


class _RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[IngressAuditEvent] = []
        self.fail = False

    def append(self, event: IngressAuditEvent) -> str:
        if self.fail:
            raise RuntimeError("durable audit unavailable")
        self.events.append(event)
        return f"secure-ingress-audit://recording/{len(self.events)}"


def _claim(
    store: ShadowIngressStateStore,
    *,
    idempotency_key: str = "idem-1",
    nonce: str = "nonce-1",
    body_sha256: str = BODY_SHA256,
    action: str = "obs:command:sys_mode.switch",
    at_ms: int = 100,
    expires_at_ms: int = 1_000,
) -> ClaimDecision:
    return store.try_claim(
        principal_id="principal-1",
        surface="obs",
        action=action,
        required_scopes=SCOPES,
        idempotency_key=idempotency_key,
        nonce=nonce,
        body_sha256=body_sha256,
        request_id="request-1",
        at_ms=at_ms,
        expires_at_ms=expires_at_ms,
    )


def _allow_event(claim_ref: str, *, ts_ms: int = 100) -> IngressAuditEvent:
    return IngressAuditEvent(
        request_id="request-1",
        correlation_id="correlation-1",
        surface="obs",
        action="obs:command:sys_mode.switch",
        method="POST",
        normalized_path="/v1/host/obs-commands/sys_mode.switch",
        principal_id="principal-1",
        tenant_id="tenant-1",
        key_id="key-1",
        credential_kind="hmac-sha256",
        credential_ref_digest="sha256:credential",
        requested_scopes=SCOPES,
        required_scopes=SCOPES,
        body_sha256=BODY_SHA256,
        idempotency_digest="sha256:idempotency",
        nonce_digest="sha256:nonce",
        transport_key_digest="sha256:transport",
        decision="ALLOW",
        reason_code="SECURE_INGRESS_ALLOWED",
        stage="COMPLETE",
        claim_ref=claim_ref,
        rate_snapshot=None,
        side_effects_executed=False,
        ts_ms=ts_ms,
    )


def test_shadow_claim_and_denial_audits_delegate_without_durable_claim_calls() -> None:
    audit = _RecordingAuditSink()
    store = ShadowIngressStateStore(audit_sink=audit)

    first = _claim(store)
    assert first.status is ClaimStatus.NEW
    assert first.claim_ref is not None
    audit_ref = store.commit_with_audit(
        claim_ref=first.claim_ref,
        event=_allow_event(first.claim_ref),
    )
    assert audit_ref == "secure-ingress-audit://recording/1"

    duplicate = _claim(store, nonce="nonce-2", at_ms=101)
    assert duplicate.status is ClaimStatus.DUPLICATE_SAME
    assert duplicate.claim_ref == first.claim_ref
    assert duplicate.existing_result_ref is None

    deny_event = replace(
        _allow_event(first.claim_ref, ts_ms=101),
        decision="DENY",
        reason_code="SECURE_INGRESS_IDEMPOTENCY_DUPLICATE",
        stage="CLAIM",
    )
    assert store.append(deny_event) == "secure-ingress-audit://recording/2"
    assert audit.events == [_allow_event(first.claim_ref), deny_event]


def test_shadow_claim_detects_idempotency_conflict_and_nonce_replay() -> None:
    store = ShadowIngressStateStore(audit_sink=_RecordingAuditSink())
    first = _claim(store)

    conflict = _claim(store, nonce="nonce-2", body_sha256="b" * 64)
    replay = _claim(store, idempotency_key="idem-2", nonce="nonce-1")

    assert conflict.status is ClaimStatus.IDEMPOTENCY_CONFLICT
    assert conflict.claim_ref == first.claim_ref
    assert replay.status is ClaimStatus.REPLAY
    assert replay.claim_ref == first.claim_ref


def test_shadow_abort_and_expiry_release_idempotency_and_nonce() -> None:
    audit = _RecordingAuditSink()
    store = ShadowIngressStateStore(audit_sink=audit, provisional_ttl_ms=10)
    first = _claim(store)

    after_provisional_expiry = _claim(store, at_ms=110)
    assert after_provisional_expiry.status is ClaimStatus.NEW
    assert after_provisional_expiry.claim_ref != first.claim_ref
    assert after_provisional_expiry.claim_ref is not None
    store.abort_claim(claim_ref=after_provisional_expiry.claim_ref)

    committed = _claim(store, at_ms=111)
    assert committed.claim_ref is not None
    store.commit_with_audit(
        claim_ref=committed.claim_ref,
        event=_allow_event(committed.claim_ref, ts_ms=111),
    )
    assert _claim(store, nonce="nonce-2", at_ms=999).status is ClaimStatus.DUPLICATE_SAME
    after_full_expiry = _claim(store, at_ms=1_000, expires_at_ms=2_000)
    assert after_full_expiry.status is ClaimStatus.NEW
    assert after_full_expiry.claim_ref != committed.claim_ref


def test_shadow_audit_failure_keeps_claim_abortable() -> None:
    audit = _RecordingAuditSink()
    audit.fail = True
    store = ShadowIngressStateStore(audit_sink=audit)
    first = _claim(store)
    assert first.claim_ref is not None

    with pytest.raises(RuntimeError, match="durable audit unavailable"):
        store.commit_with_audit(
            claim_ref=first.claim_ref,
            event=_allow_event(first.claim_ref),
        )
    assert _claim(store, nonce="nonce-2").status is ClaimStatus.DUPLICATE_SAME

    store.abort_claim(claim_ref=first.claim_ref)
    replacement = _claim(store)
    assert replacement.status is ClaimStatus.NEW
    assert replacement.claim_ref != first.claim_ref


def test_shadow_claim_is_thread_safe_for_one_idempotency_key() -> None:
    store = ShadowIngressStateStore(audit_sink=_RecordingAuditSink())

    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = list(executor.map(
            lambda index: _claim(store, nonce=f"nonce-{index}"),
            range(32),
        ))

    assert sum(item.status is ClaimStatus.NEW for item in decisions) == 1
    assert sum(item.status is ClaimStatus.DUPLICATE_SAME for item in decisions) == 31
    assert len({item.claim_ref for item in decisions}) == 1


def test_new_shadow_store_does_not_inherit_a_previous_process_claim() -> None:
    audit = _RecordingAuditSink()
    first_store = ShadowIngressStateStore(audit_sink=audit)
    first = _claim(first_store)
    assert first.claim_ref is not None
    first_store.commit_with_audit(
        claim_ref=first.claim_ref,
        event=_allow_event(first.claim_ref),
    )

    restarted_store = ShadowIngressStateStore(audit_sink=audit)
    restarted = _claim(restarted_store)
    assert restarted.status is ClaimStatus.NEW
    assert restarted.claim_ref != first.claim_ref


def test_shadow_rejects_invalid_claim_and_allow_audit_mismatch() -> None:
    store = ShadowIngressStateStore(audit_sink=_RecordingAuditSink())
    with pytest.raises(ShadowIngressStateError, match="OBS_SHADOW_CLAIM_INVALID"):
        _claim(store, expires_at_ms=100)

    claim = _claim(store)
    assert claim.claim_ref is not None
    with pytest.raises(
        ShadowIngressStateError,
        match="OBS_SHADOW_ALLOW_AUDIT_MISMATCH",
    ):
        store.commit_with_audit(
            claim_ref=claim.claim_ref,
            event=replace(_allow_event(claim.claim_ref), decision="DENY"),
        )
