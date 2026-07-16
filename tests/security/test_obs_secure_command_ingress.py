from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)
from host_brain.observability.command_ports import (
    DEFAULT_OBS_COMMAND_SPECS,
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandEnvelope,
    ObsCommandExecutionResult,
    ObsCommandPortRegistry,
)
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeState,
    OutcomeJournalEvent,
    SideEffectCertainty,
)
from host_brain.observability.command_router import ObsCommandRouter
from host_brain.observability.secure_command_ingress import (
    ObsCommandIngressOutcome,
    ObsCommandIngressRequest,
    SecureObsCommandIngress,
    build_obs_command_secure_request,
)
from host_brain.secure_ingress import (
    ClaimDecision,
    ClaimStatus,
    CredentialVerifierRegistry,
    HmacSha256CredentialVerifier,
    IngressReasonCode,
    PrincipalAuthority,
    RateDecision,
    SecureIngressAdapter,
    SecureIngressMode,
    SecureIngressService,
    build_hmac_sha256_signature,
)


NOW = 1_800_000_000_000
SECRET = "task0489-obs-unit-secret-not-production"
PRINCIPAL_ID = "service.obs.operator.test"
TENANT_ID = "tenant.obs.test"
KEY_ID = "obs.command.key.test"
COMMAND_ID = "sys_mode.switch"


class _PrincipalStore:
    def __init__(self, authority: PrincipalAuthority, *, fail: bool = False) -> None:
        self.authority = authority
        self.fail = fail

    def resolve(self, **_kwargs: Any) -> PrincipalAuthority | None:
        if self.fail:
            raise RuntimeError("authority unavailable")
        return self.authority


class _RateLimiter:
    def __init__(self) -> None:
        self.attempt = RateDecision(True, 1, 100, 60_000, "unit", lane="ATTEMPT")
        self.authority = RateDecision(True, 1, 20, 60_000, "unit")
        self.attempt_calls = 0
        self.authority_calls = 0

    def consume_attempt(self, **_kwargs: Any) -> RateDecision:
        self.attempt_calls += 1
        return self.attempt

    def consume(self, **_kwargs: Any) -> RateDecision:
        self.authority_calls += 1
        return self.authority


class _EmergencyAudit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def append(self, event: Any) -> str:
        self.events.append(event)
        return f"emergency-audit://obs/{len(self.events)}"


class _StateStore:
    def __init__(self) -> None:
        self.claims: dict[str, dict[str, Any]] = {}
        self.nonces: set[tuple[str, str]] = set()
        self.by_idempotency: dict[tuple[str, str, str], str] = {}
        self.audits: list[Any] = []
        self.completed: list[tuple[str, str, int | None]] = []
        self.fail_claim = False
        self.fail_allow_audit = False
        self.fail_complete = False

    def try_claim(self, **kwargs: Any) -> ClaimDecision:
        if self.fail_claim:
            raise RuntimeError("claim store unavailable")
        idem_key = (
            str(kwargs["principal_id"]),
            str(kwargs["surface"]),
            str(kwargs["idempotency_key"]),
        )
        existing_ref = self.by_idempotency.get(idem_key)
        if existing_ref is not None:
            existing = self.claims[existing_ref]
            same = all(
                existing[key] == kwargs[key]
                for key in ("action", "required_scopes", "body_sha256")
            )
            return ClaimDecision(
                ClaimStatus.DUPLICATE_SAME if same else ClaimStatus.IDEMPOTENCY_CONFLICT,
                existing_ref,
                existing.get("result_ref"),
            )
        nonce_key = (str(kwargs["principal_id"]), str(kwargs["nonce"]))
        if nonce_key in self.nonces:
            return ClaimDecision(ClaimStatus.REPLAY)
        claim_ref = f"claim://obs/{len(self.claims) + 1}"
        self.claims[claim_ref] = {**kwargs, "result_ref": None, "committed": False}
        self.by_idempotency[idem_key] = claim_ref
        self.nonces.add(nonce_key)
        return ClaimDecision(ClaimStatus.NEW, claim_ref)

    def commit_with_audit(self, *, claim_ref: str, event: Any) -> str:
        if self.fail_allow_audit:
            raise RuntimeError("primary audit unavailable")
        self.audits.append(event)
        self.claims[claim_ref]["committed"] = True
        return f"secure-ingress-audit://obs/{len(self.audits)}"

    def append(self, event: Any) -> str:
        self.audits.append(event)
        return f"secure-ingress-audit://obs/{len(self.audits)}"

    def abort_claim(self, *, claim_ref: str) -> None:
        self.claims[claim_ref]["aborted"] = True

    def complete_claim(
        self,
        *,
        claim_ref: str,
        result_ref: str,
        at_ms: int | None = None,
    ) -> None:
        if self.fail_complete:
            raise RuntimeError("claim result store unavailable")
        if not self.claims[claim_ref]["committed"]:
            raise RuntimeError("claim not committed")
        self.claims[claim_ref]["result_ref"] = result_ref
        self.completed.append((claim_ref, result_ref, at_ms))


class _RecordingPort:
    def __init__(self) -> None:
        self.envelopes: list[ObsCommandEnvelope] = []

    def execute(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        self.envelopes.append(envelope)
        return ObsCommandExecutionResult(
            success=True,
            result_ref=f"obs-command-result://unit/{len(self.envelopes)}",
            result={"status": "APPLIED"},
            side_effects_executed=True,
        )


class _InvalidResultPort(_RecordingPort):
    def execute(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        self.envelopes.append(envelope)
        return ObsCommandExecutionResult(
            success=True,
            result_ref="not-a-result-ref",
            result={},
            side_effects_executed=False,
        )


class _RejectedPort(_RecordingPort):
    def execute(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        self.envelopes.append(envelope)
        return ObsCommandExecutionResult(
            success=False,
            result_ref="obs-command-result://unit/rejected",
            result={"status": "REJECTED"},
            side_effects_executed=True,
        )


class _ExplodingPort(_RecordingPort):
    def execute(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        self.envelopes.append(envelope)
        raise RuntimeError("domain crashed after dispatch")


class _SpoofingExplodingPort(_RecordingPort):
    def execute(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        self.envelopes.append(envelope)

        class DependencyError(RuntimeError):
            reason_code = "DEPENDENCY_CLAIMS_NOT_EXECUTED"
            side_effects_may_have_run = False

        raise DependencyError("untrusted dependency exception")


@dataclass
class _System:
    authority: PrincipalAuthority
    rate: _RateLimiter
    state: _StateStore
    port: _RecordingPort
    registry: ObsCommandPortRegistry
    service: SecureIngressService
    ingress: SecureObsCommandIngress
    journal: ObsCommandOutcomeJournal | None
    journal_owner: Any = None


def _all_scopes() -> tuple[str, ...]:
    return tuple(sorted({
        scope
        for spec in DEFAULT_OBS_COMMAND_SPECS
        for scope in spec.required_scopes
    }))


_DEFAULT_JOURNAL = object()


def _system(
    *,
    mode: SecureIngressMode = SecureIngressMode.ENFORCE,
    bind_port: bool = True,
    principal_fail: bool = False,
    claim_result_available: bool = True,
    outcome_journal: Any = _DEFAULT_JOURNAL,
    state_store: _StateStore | None = None,
    port: _RecordingPort | None = None,
) -> _System:
    authority = PrincipalAuthority(
        principal_id=PRINCIPAL_ID,
        kind=PrincipalKind.SERVICE,
        tenant_id=TENANT_ID,
        key_id=KEY_ID,
        credential_kind=CredentialProofKind.HMAC_SHA256,
        algorithm="hmac-sha256",
        granted_scopes=_all_scopes(),
        allowed_audiences=("obs",),
        allowed_actions=("obs:command:*",),
        version=1,
    )
    rate = _RateLimiter()
    state = state_store or _StateStore()
    port = port or _RecordingPort()
    registry = ObsCommandPortRegistry(
        ports=({COMMAND_ID: port} if bind_port else {}),
    )
    service = SecureIngressService(
        principal_store=_PrincipalStore(authority, fail=principal_fail),
        credential_verifier=CredentialVerifierRegistry({
            CredentialProofKind.HMAC_SHA256: HmacSha256CredentialVerifier(
                secret_resolver=lambda _authority: SECRET,
            )
        }),
        attempt_limiter=rate,
        rate_limiter=rate,
        state_store=state,
        emergency_audit_sink=_EmergencyAudit(),
    )
    adapter = SecureIngressAdapter(service=service, mode=mode)
    journal_owner = None
    if outcome_journal is _DEFAULT_JOURNAL:
        journal_owner = TemporaryDirectory(prefix="natsume-obs-outcome-")
        outcome_journal = ObsCommandOutcomeJournal(
            Path(journal_owner.name) / "outcomes.jsonl",
            clock_ms=lambda: NOW + 1,
        )
    ingress = SecureObsCommandIngress(
        ingress_adapter=adapter,
        ingress_service=service,
        claim_result_store=(state if claim_result_available else None),
        outcome_journal=outcome_journal,
        registry=registry,
        router=ObsCommandRouter(registry),
        clock_ms=lambda: NOW + 1,
    )
    return _System(
        authority=authority,
        rate=rate,
        state=state,
        port=port,
        registry=registry,
        service=service,
        ingress=ingress,
        journal=outcome_journal,
        journal_owner=journal_owner,
    )


def _raw_body(
    *,
    command_id: str = COMMAND_ID,
    payload: dict[str, Any] | None = None,
) -> bytes:
    return json.dumps(
        {
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
            "command_id": command_id,
            "payload": payload or {
                "target_mode": "focus",
                "reason": "unit test",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _identity(scopes: tuple[str, ...], *, request_id: str = "obs.request.1") -> IngressIdentityEnvelope:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=KEY_ID,
        algorithm="hmac-sha256",
        proof_id="obs.proof.1",
        issued_at_epoch_ms=NOW,
        expires_at_epoch_ms=NOW + 60_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=PRINCIPAL_ID,
            kind=PrincipalKind.SERVICE,
            tenant_id=TENANT_ID,
            granted_scopes=scopes,
        ),
        credential_proof=metadata,
        audience="obs",
        request_id=request_id,
        requested_scopes=scopes,
    )


def _signed_request(
    system: _System,
    *,
    raw_body: bytes | None = None,
    command_id: str = COMMAND_ID,
    scopes: tuple[str, ...] | None = None,
    idempotency_key: str = "obs.idem.1",
    nonce: str = "obs.nonce.1",
) -> ObsCommandIngressRequest:
    body = raw_body or _raw_body(command_id=command_id)
    spec = system.registry.resolve_spec(command_id)
    assert spec is not None
    requested = scopes or spec.required_scopes
    identity = _identity(requested)
    unsigned = ObsCommandIngressRequest(
        command_id=command_id,
        raw_body=body,
        identity=identity,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof="0" * 64,
        ),
        idempotency_key=idempotency_key,
        nonce=nonce,
        received_at_epoch_ms=NOW,
        transport_key=f"principal:{PRINCIPAL_ID}",
        normalized_path=f"/v1/host/obs-commands/{command_id}",
        correlation_id="obs.correlation.1",
    )
    secure_request = build_obs_command_secure_request(unsigned, spec)
    signature = build_hmac_sha256_signature(secure_request, SECRET)
    return replace(
        unsigned,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof=signature,
        ),
    )


def _execute(system: _System, request: ObsCommandIngressRequest):  # noqa: ANN202
    return asyncio.run(system.ingress.execute(request))


def test_manifest_has_all_active_families_and_no_implicit_runtime_adapters() -> None:
    registry = ObsCommandPortRegistry()

    manifest = registry.manifest()

    assert len(manifest) == 34
    assert len({item["command_id"] for item in manifest}) == 34
    assert "wechat.channel.reset" not in {item["command_id"] for item in manifest}
    assert all(item["action"].startswith("obs:command:") for item in manifest)
    assert all(len(item["required_scopes"]) >= 2 for item in manifest)
    assert registry.bound_command_ids == ()


def test_retired_wechat_command_is_not_allowed_without_journal_or_domain() -> None:
    system = _system()
    retired_id = "wechat.channel.reset"
    request = replace(
        _signed_request(
            system,
            idempotency_key="obs.idem.retired.wechat",
            nonce="obs.nonce.retired.wechat",
        ),
        command_id=retired_id,
        normalized_path=f"/v1/host/obs-commands/{retired_id}",
        raw_body=_raw_body(command_id=retired_id, payload={"reason": "retired"}),
    )

    outcome = _execute(system, request)

    assert outcome.reason_code == "OBS_COMMAND_NOT_ALLOWED"
    assert outcome.allowed is False
    assert outcome.executed is False
    assert system.journal is not None
    assert system.journal.records() == ()
    assert system.state.claims == {}
    assert system.port.envelopes == []


def test_valid_command_completes_claim_and_binds_manifest_action_scope() -> None:
    system = _system()

    outcome = _execute(system, _signed_request(system))

    assert outcome.allowed is True
    assert outcome.executed is True
    assert outcome.reason_code == "OBS_COMMAND_COMPLETED"
    assert len(system.port.envelopes) == 1
    envelope = system.port.envelopes[0]
    spec = system.registry.resolve_spec(COMMAND_ID)
    assert spec is not None
    assert envelope.action == spec.action
    assert envelope.required_scopes == spec.required_scopes
    assert system.state.completed == [
        (outcome.claim_ref, outcome.result_ref, NOW + 1)
    ]
    assert outcome.result_ref is not None
    assert outcome.result_ref.startswith("obs-command-outcome://")
    assert outcome.terminal_state == ObsCommandOutcomeState.SUCCEEDED.value
    assert outcome.certainty == SideEffectCertainty.EXECUTED.value
    assert system.state.audits[0].action == spec.action
    assert system.state.audits[0].required_scopes == tuple(sorted(spec.required_scopes))


@pytest.mark.parametrize("missing", ["identity", "credential"])
def test_missing_identity_or_credential_never_reaches_domain(missing: str) -> None:
    system = _system()
    request = _signed_request(system)
    request = replace(
        request,
        identity=(None if missing == "identity" else request.identity),
        credential=(None if missing == "credential" else request.credential),
    )

    outcome = _execute(system, request)

    assert outcome.allowed is False
    assert outcome.executed is False
    assert outcome.reason_code in {
        IngressReasonCode.PRINCIPAL_INVALID.value,
        IngressReasonCode.CREDENTIAL_MISSING.value,
    }
    assert system.port.envelopes == []


def test_raw_body_tamper_invalidates_signature_before_domain_execution() -> None:
    system = _system()
    signed = _signed_request(system)
    tampered = replace(
        signed,
        raw_body=_raw_body(payload={"target_mode": "locked", "reason": "tampered"}),
    )

    outcome = _execute(system, tampered)

    assert outcome.reason_code == IngressReasonCode.CREDENTIAL_INVALID.value
    assert outcome.executed is False
    assert system.port.envelopes == []


def test_invalid_json_is_not_parsed_until_exact_raw_body_authentication_succeeds() -> None:
    system = _system()
    signed = _signed_request(system)
    invalid_json = (
        b'{"schema_version":"natsume.obs-command.v1",'
        b'"command_id":"sys_mode.switch","command_id":"sys_mode.switch",'
        b'"payload":{"target_mode":"focus"}}'
    )

    outcome = _execute(system, replace(signed, raw_body=invalid_json))

    assert outcome.reason_code == IngressReasonCode.CREDENTIAL_INVALID.value
    assert system.state.claims == {}
    assert system.port.envelopes == []


def test_command_domain_scope_is_enforced_in_addition_to_obs_write() -> None:
    system = _system()
    request = _signed_request(system, scopes=("obs:write",))

    outcome = _execute(system, request)

    assert outcome.reason_code == IngressReasonCode.SCOPE_DENIED.value
    assert outcome.executed is False
    assert system.port.envelopes == []


def test_unauthenticated_and_rate_denials_never_grow_outcome_journal() -> None:
    unauthenticated = _system()
    assert unauthenticated.journal is not None
    for index in range(16):
        request = _signed_request(
            unauthenticated,
            raw_body=_raw_body(payload={
                "target_mode": "focus",
                "reason": f"rotated-unauthenticated-{index}",
            }),
            idempotency_key=f"obs.idem.unauthenticated.{index}",
            nonce=f"obs.nonce.unauthenticated.{index}",
        )
        assert request.identity is not None and request.credential is not None
        forged_identity = replace(
            request.identity,
            principal=replace(
                request.identity.principal,
                principal_id=f"service.obs.unauthenticated.{index}",
            ),
            request_id=f"obs.request.unauthenticated.{index}",
        )
        forged = replace(
            request,
            identity=forged_identity,
            credential=CredentialProof(
                metadata=forged_identity.credential_proof,
                raw_proof=request.credential.raw_proof,
            ),
        )
        outcome = _execute(unauthenticated, forged)
        assert outcome.reason_code == IngressReasonCode.PRINCIPAL_INVALID.value
    assert unauthenticated.journal.records() == ()
    assert unauthenticated.port.envelopes == []

    rate_limited = _system()
    rate_limited.rate.authority = RateDecision(False, 21, 20, 60_000, "unit")
    assert rate_limited.journal is not None
    for index in range(16):
        outcome = _execute(
            rate_limited,
            _signed_request(
                rate_limited,
                raw_body=_raw_body(payload={
                    "target_mode": "focus",
                    "reason": f"rotated-rate-limited-{index}",
                }),
                idempotency_key=f"obs.idem.rate-limited.{index}",
                nonce=f"obs.nonce.rate-limited.{index}",
            ),
        )
        assert outcome.reason_code == IngressReasonCode.RATE_LIMITED.value
    assert rate_limited.journal.records() == ()
    assert rate_limited.state.claims == {}
    assert rate_limited.port.envelopes == []


def test_duplicate_returns_completed_result_without_second_execution() -> None:
    system = _system()
    first = _execute(system, _signed_request(system))
    duplicate = _execute(
        system,
        _signed_request(system, nonce="obs.nonce.2"),
    )

    assert first.allowed is True
    assert duplicate.duplicate is True
    assert duplicate.executed is False
    assert duplicate.result_ref == first.result_ref
    assert duplicate.terminal_state == ObsCommandOutcomeState.SUCCEEDED.value
    assert duplicate.result is None
    assert len(system.port.envelopes) == 1


def test_new_central_claim_state_replays_durable_success_without_execution() -> None:
    first_system = _system()
    first = _execute(first_system, _signed_request(first_system))
    restarted = _system(outcome_journal=first_system.journal)

    replay = _execute(restarted, _signed_request(restarted))

    assert first.terminal_state == ObsCommandOutcomeState.SUCCEEDED.value
    assert replay.allowed is True
    assert replay.duplicate is True
    assert replay.executed is False
    assert replay.result_ref == first.result_ref
    assert restarted.state.completed == [
        (replay.claim_ref, replay.result_ref, NOW + 1)
    ]
    assert restarted.port.envelopes == []


def test_complete_failure_then_restart_replays_journal_and_never_reexecutes() -> None:
    first_system = _system()
    first_system.state.fail_complete = True
    failed_commit = _execute(first_system, _signed_request(first_system))
    assert failed_commit.reason_code == "OBS_COMMAND_RESULT_COMMIT_FAILED"
    assert failed_commit.terminal_state == ObsCommandOutcomeState.SUCCEEDED.value
    assert len(first_system.port.envelopes) == 1

    restarted = _system(outcome_journal=first_system.journal)
    replay = _execute(restarted, _signed_request(restarted))

    assert replay.allowed is True
    assert replay.duplicate is True
    assert replay.executed is False
    assert replay.result_ref == failed_commit.result_ref
    assert restarted.state.completed == [
        (replay.claim_ref, replay.result_ref, NOW + 1)
    ]
    assert restarted.port.envelopes == []


def test_secure_duplicate_without_receipt_converges_uncertain_without_execution() -> None:
    system = _system()
    first_request = _signed_request(system)
    spec = system.registry.resolve_spec(COMMAND_ID)
    assert spec is not None
    first_decision = system.service.decide(
        build_obs_command_secure_request(first_request, spec)
    )
    assert first_decision.allowed is True

    outcome = _execute(
        system,
        _signed_request(system, nonce="obs.nonce.receipt-missing"),
    )

    assert outcome.reason_code == "OBS_COMMAND_CLAIM_RECEIPT_RECOVERED_UNCERTAIN"
    assert outcome.allowed is False
    assert outcome.duplicate is False
    assert outcome.terminal_state == ObsCommandOutcomeState.UNCERTAIN.value
    assert outcome.certainty == SideEffectCertainty.MAY_HAVE_EXECUTED.value
    assert outcome.result_ref is not None
    assert system.state.completed == [
        (first_decision.claim_ref, outcome.result_ref, NOW + 1)
    ]
    assert system.port.envelopes == []

    replay = _execute(
        system,
        _signed_request(system, nonce="obs.nonce.receipt-replay"),
    )
    assert replay.reason_code == outcome.reason_code
    assert replay.duplicate is True
    assert replay.result_ref == outcome.result_ref
    assert system.port.envelopes == []


def test_nonce_replay_and_idempotency_conflict_never_execute_again() -> None:
    replay_system = _system()
    _execute(replay_system, _signed_request(replay_system))
    replay = _execute(
        replay_system,
        _signed_request(
            replay_system,
            idempotency_key="obs.idem.2",
            nonce="obs.nonce.1",
        ),
    )
    assert replay.reason_code == IngressReasonCode.REPLAY_DETECTED.value
    assert len(replay_system.port.envelopes) == 1

    conflict_system = _system()
    _execute(conflict_system, _signed_request(conflict_system))
    changed_body = _raw_body(payload={"target_mode": "sleep", "reason": "changed"})
    conflict = _execute(
        conflict_system,
        _signed_request(
            conflict_system,
            raw_body=changed_body,
            nonce="obs.nonce.2",
        ),
    )
    assert conflict.reason_code == IngressReasonCode.IDEMPOTENCY_CONFLICT.value
    assert len(conflict_system.port.envelopes) == 1


def test_shadow_audits_and_claims_but_never_executes_or_completes_result() -> None:
    system = _system(mode=SecureIngressMode.SHADOW)

    outcome = _execute(system, _signed_request(system))

    assert outcome.reason_code == "OBS_COMMAND_SHADOW_EVALUATED"
    assert outcome.terminal_state == ObsCommandOutcomeState.SHADOW_EVALUATED.value
    assert outcome.executed is False
    assert system.port.envelopes == []
    assert system.state.audits
    assert system.state.completed == []


def test_shadow_authenticated_parse_rejection_has_stable_auth_lane_receipt() -> None:
    system = _system(mode=SecureIngressMode.SHADOW)
    duplicate_body = (
        b'{"schema_version":"natsume.obs-command.v1",'
        b'"command_id":"sys_mode.switch","command_id":"sys_mode.switch",'
        b'"payload":{"target_mode":"focus"}}'
    )

    first = _execute(
        system,
        _signed_request(system, raw_body=duplicate_body),
    )
    replay = _execute(
        system,
        _signed_request(
            system,
            raw_body=duplicate_body,
            nonce="obs.nonce.shadow.parse-replay",
        ),
    )

    assert first.reason_code == "OBS_COMMAND_DUPLICATE_FIELD"
    assert first.terminal_state == ObsCommandOutcomeState.REJECTED.value
    assert first.certainty == SideEffectCertainty.NOT_EXECUTED.value
    assert replay.reason_code == first.reason_code
    assert replay.duplicate is True
    assert replay.result_ref == first.result_ref
    assert system.journal is not None
    records = system.journal.records()
    assert len(records) == 1
    assert records[0].event_type is OutcomeJournalEvent.AUTHENTICATED_REJECTION
    assert system.state.completed == []
    assert system.port.envelopes == []


def test_shadow_receipt_promotes_under_fresh_enforce_state_once() -> None:
    shadow = _system(mode=SecureIngressMode.SHADOW)
    shadow_outcome = _execute(shadow, _signed_request(shadow))
    assert shadow_outcome.terminal_state == ObsCommandOutcomeState.SHADOW_EVALUATED.value

    enforce = _system(
        mode=SecureIngressMode.ENFORCE,
        outcome_journal=shadow.journal,
    )
    promoted = _execute(enforce, _signed_request(enforce))

    assert promoted.allowed is True
    assert promoted.terminal_state == ObsCommandOutcomeState.SUCCEEDED.value
    assert len(enforce.port.envelopes) == 1
    assert enforce.journal is not None
    assert enforce.journal.records()[-2].reason_code == "OBS_COMMAND_SHADOW_PROMOTED"
    assert enforce.journal.records()[-1].state is ObsCommandOutcomeState.SUCCEEDED


def test_authenticated_payload_rejection_is_durable_and_duplicate_replays_rejection() -> None:
    system = _system()
    duplicate_body = (
        b'{"schema_version":"natsume.obs-command.v1",'
        b'"command_id":"sys_mode.switch","command_id":"sys_mode.switch",'
        b'"payload":{"target_mode":"focus"}}'
    )
    first = _execute(
        system,
        _signed_request(system, raw_body=duplicate_body),
    )
    replay = _execute(
        system,
        _signed_request(
            system,
            raw_body=duplicate_body,
            nonce="obs.nonce.rejected-replay",
        ),
    )

    assert first.reason_code == "OBS_COMMAND_DUPLICATE_FIELD"
    assert first.terminal_state == ObsCommandOutcomeState.REJECTED.value
    assert replay.reason_code == first.reason_code
    assert replay.terminal_state == ObsCommandOutcomeState.REJECTED.value
    assert replay.duplicate is True
    assert replay.executed is False
    assert replay.result is None
    assert system.port.envelopes == []


def test_invalid_domain_result_is_uncertain_even_when_port_claims_no_side_effect() -> None:
    port = _InvalidResultPort()
    system = _system(port=port)

    first = _execute(system, _signed_request(system))
    replay = _execute(
        system,
        _signed_request(system, nonce="obs.nonce.invalid-result-replay"),
    )

    assert first.reason_code == "OBS_COMMAND_RESULT_INVALID"
    assert first.terminal_state == ObsCommandOutcomeState.UNCERTAIN.value
    assert first.certainty == SideEffectCertainty.MAY_HAVE_EXECUTED.value
    assert replay.terminal_state == ObsCommandOutcomeState.UNCERTAIN.value
    assert replay.duplicate is True
    assert replay.executed is False
    assert len(port.envelopes) == 1


def test_explicit_domain_rejection_with_side_effect_is_terminal_rejected() -> None:
    port = _RejectedPort()
    system = _system(port=port)

    first = _execute(system, _signed_request(system))
    replay = _execute(
        system,
        _signed_request(system, nonce="obs.nonce.domain-rejected-replay"),
    )

    assert first.reason_code == "OBS_COMMAND_DOMAIN_REJECTED"
    assert first.terminal_state == ObsCommandOutcomeState.REJECTED.value
    assert first.certainty == SideEffectCertainty.EXECUTED.value
    assert first.executed is True
    assert replay.reason_code == first.reason_code
    assert replay.terminal_state == ObsCommandOutcomeState.REJECTED.value
    assert replay.duplicate is True
    assert replay.executed is False
    assert len(port.envelopes) == 1


def test_domain_exception_replays_uncertain_after_fresh_claim_state() -> None:
    exploding = _ExplodingPort()
    first_system = _system(port=exploding)
    first = _execute(first_system, _signed_request(first_system))

    assert first.reason_code == "OBS_COMMAND_EXECUTION_UNCERTAIN"
    assert first.terminal_state == ObsCommandOutcomeState.UNCERTAIN.value
    assert first.certainty == SideEffectCertainty.MAY_HAVE_EXECUTED.value
    assert len(exploding.envelopes) == 1

    fresh_port = _RecordingPort()
    fresh_state = _system(
        outcome_journal=first_system.journal,
        port=fresh_port,
    )
    replay = _execute(fresh_state, _signed_request(fresh_state))

    assert replay.reason_code == first.reason_code
    assert replay.terminal_state == ObsCommandOutcomeState.UNCERTAIN.value
    assert replay.duplicate is True
    assert replay.executed is False
    assert fresh_port.envelopes == []


def test_dependency_exception_cannot_spoof_definite_non_execution() -> None:
    port = _SpoofingExplodingPort()
    system = _system(port=port)

    outcome = _execute(system, _signed_request(system))

    assert outcome.reason_code == "OBS_COMMAND_EXECUTION_UNCERTAIN"
    assert outcome.terminal_state == ObsCommandOutcomeState.UNCERTAIN.value
    assert outcome.certainty == SideEffectCertainty.MAY_HAVE_EXECUTED.value
    assert len(port.envelopes) == 1


def test_all_caller_authority_fields_including_owner_organ_are_overwritten() -> None:
    system = _system()
    raw_body = _raw_body(payload={
        "target_mode": "focus",
        "reason": "authority binding",
        "principal_id": "attacker",
        "tenant_id": "attacker-tenant",
        "proposer": "attacker",
        "approver": "attacker",
        "operator": "attacker",
        "owner": "attacker",
        "owner_organ": "attacker-organ",
        "source": "monitor",
    })

    outcome = _execute(system, _signed_request(system, raw_body=raw_body))

    assert outcome.allowed is True
    envelope = system.port.envelopes[0]
    assert envelope.authority.principal_id == PRINCIPAL_ID
    assert envelope.authority.tenant_id == TENANT_ID
    for field in ("principal_id", "proposer", "approver", "operator", "owner", "owner_organ"):
        assert envelope.payload[field] == PRINCIPAL_ID
    assert envelope.payload["tenant_id"] == TENANT_ID


def test_rate_authority_store_audit_and_claim_result_fail_closed() -> None:
    rate_system = _system()
    rate_system.rate.authority = RateDecision(False, 21, 20, 60_000, "unit")
    rate_outcome = _execute(rate_system, _signed_request(rate_system))
    assert rate_outcome.reason_code == IngressReasonCode.RATE_LIMITED.value
    assert rate_system.port.envelopes == []

    authority_system = _system(principal_fail=True)
    authority_outcome = _execute(authority_system, _signed_request(authority_system))
    assert authority_outcome.reason_code == IngressReasonCode.INTERNAL_ERROR.value
    assert authority_system.port.envelopes == []

    claim_system = _system()
    claim_system.state.fail_claim = True
    claim_outcome = _execute(claim_system, _signed_request(claim_system))
    assert claim_outcome.reason_code == IngressReasonCode.INTERNAL_ERROR.value
    assert claim_system.port.envelopes == []

    audit_system = _system()
    audit_system.state.fail_allow_audit = True
    audit_outcome = _execute(audit_system, _signed_request(audit_system))
    assert audit_outcome.reason_code == IngressReasonCode.AUDIT_UNAVAILABLE.value
    assert audit_system.port.envelopes == []

    completion_system = _system()
    completion_system.state.fail_complete = True
    completion_outcome = _execute(completion_system, _signed_request(completion_system))
    assert completion_outcome.reason_code == "OBS_COMMAND_RESULT_COMMIT_FAILED"
    assert completion_outcome.executed is True


def test_admission_close_rejects_new_requests_and_async_drain_waits(
    monkeypatch,
) -> None:
    system = _system()

    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked(_request: ObsCommandIngressRequest) -> ObsCommandIngressOutcome:
            started.set()
            await release.wait()
            return ObsCommandIngressOutcome(
                allowed=False,
                executed=False,
                duplicate=False,
                reason_code="OBS_COMMAND_BLOCKED_TEST_DONE",
            )

        monkeypatch.setattr(system.ingress, "_execute_admitted", blocked)
        admitted = asyncio.create_task(
            system.ingress.execute(_signed_request(system))
        )
        await started.wait()
        assert system.ingress.in_flight == 1

        system.ingress.close_admission()
        drain = asyncio.create_task(system.ingress.close_admission_and_drain())
        await asyncio.sleep(0)
        rejected = await system.ingress.execute(
            _signed_request(system, nonce="obs.nonce.closed")
        )
        assert rejected.reason_code == "OBS_COMMAND_INGRESS_CLOSED"
        assert rejected.executed is False
        assert system.ingress.in_flight == 1
        assert drain.done() is False

        release.set()
        await admitted
        await asyncio.wait_for(drain, timeout=1.0)
        assert system.ingress.in_flight == 0
        rejected_again = await system.ingress.execute(
            _signed_request(system, nonce="obs.nonce.closed-again")
        )
        assert rejected_again.reason_code == "OBS_COMMAND_INGRESS_CLOSED"

    asyncio.run(scenario())


def test_admission_in_flight_is_released_when_execute_raises(monkeypatch) -> None:
    system = _system()

    async def explode(_request: ObsCommandIngressRequest) -> ObsCommandIngressOutcome:
        raise RuntimeError("execute exploded")

    monkeypatch.setattr(system.ingress, "_execute_admitted", explode)
    with pytest.raises(RuntimeError, match="execute exploded"):
        _execute(system, _signed_request(system))
    assert system.ingress.in_flight == 0


def test_unbound_domain_port_and_missing_claim_store_have_no_legacy_fallback() -> None:
    unbound = _system(bind_port=False)
    unbound_outcome = _execute(unbound, _signed_request(unbound))
    assert unbound_outcome.reason_code == "OBS_COMMAND_DOMAIN_PORT_UNAVAILABLE"
    assert unbound_outcome.executed is False
    assert unbound_outcome.result_ref is not None
    assert len(unbound.state.completed) == 1
    assert unbound.port.envelopes == []
    duplicate = _execute(
        unbound,
        _signed_request(unbound, nonce="obs.nonce.unbound.duplicate"),
    )
    assert duplicate.duplicate is True
    assert duplicate.result_ref == unbound_outcome.result_ref

    missing_store = _system(claim_result_available=False)
    missing_store_outcome = _execute(missing_store, _signed_request(missing_store))
    assert missing_store_outcome.reason_code == "OBS_COMMAND_SECURE_DEPENDENCY_UNAVAILABLE"
    assert missing_store_outcome.executed is False
    assert missing_store.port.envelopes == []

    missing_journal = _system(outcome_journal=None)
    missing_journal_outcome = _execute(
        missing_journal,
        _signed_request(missing_journal),
    )
    assert missing_journal_outcome.reason_code == "OBS_COMMAND_OUTCOME_JOURNAL_UNAVAILABLE"
    assert missing_journal_outcome.executed is False
    assert missing_journal.port.envelopes == []


def test_unknown_duplicate_and_nonfinite_envelopes_are_audited_rejections() -> None:
    system = _system()
    mismatched_body = _execute(
        system,
        _signed_request(
            system,
            raw_body=_raw_body(command_id="legacy.sysmode.write"),
        ),
    )
    assert mismatched_body.reason_code == "OBS_COMMAND_PATH_BODY_MISMATCH"

    unknown_path = _execute(
        system,
        replace(
            _signed_request(system, idempotency_key="obs.idem.unknown"),
            command_id="legacy.sysmode.write",
            normalized_path="/v1/host/obs-commands/legacy.sysmode.write",
        ),
    )
    assert unknown_path.reason_code == "OBS_COMMAND_NOT_ALLOWED"

    duplicate_body = (
        b'{"schema_version":"natsume.obs-command.v1",'
        b'"command_id":"sys_mode.switch","command_id":"sys_mode.switch",'
        b'"payload":{"target_mode":"focus"}}'
    )
    duplicate = _execute(
        system,
        _signed_request(
            system,
            raw_body=duplicate_body,
            idempotency_key="obs.idem.duplicate",
            nonce="obs.nonce.duplicate",
        ),
    )
    assert duplicate.reason_code == "OBS_COMMAND_DUPLICATE_FIELD"

    nonfinite_body = (
        b'{"schema_version":"natsume.obs-command.v1",'
        b'"command_id":"sys_mode.switch",'
        b'"payload":{"target_mode":"focus","operator":NaN}}'
    )
    nonfinite = _execute(
        system,
        _signed_request(
            system,
            raw_body=nonfinite_body,
            idempotency_key="obs.idem.nonfinite",
            nonce="obs.nonce.nonfinite",
        ),
    )
    assert nonfinite.reason_code == "OBS_COMMAND_NONFINITE_VALUE"
    assert system.port.envelopes == []
    assert len(system.state.audits) == 4
