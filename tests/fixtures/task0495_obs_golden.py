"""Deterministic, recording-only golden run for the OBS command plane."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Callable, Mapping, MutableMapping, cast

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.observability_runtime import domain_command_ports
from host_brain.app.observability_runtime.command_composition import (
    ObsCommandComposition,
    build_obs_command_composition,
)
from host_brain.observability.command_ports import (
    OBS_COMMAND_ENVELOPE_SCHEMA,
    ObsCommandPortRegistry,
)
from host_brain.observability.secure_command_ingress import (
    ObsCommandIngressOutcome,
    ObsCommandIngressRequest,
    build_obs_command_secure_request,
)
from host_brain.secure_ingress import (
    IngressSurface,
    IngressReasonCode,
    SECURE_INGRESS_SURFACE_POLICIES,
    build_hmac_sha256_signature,
)
from tests.fixtures.m48b_isolation import M48BIsolationHarness
from tests.fixtures.m48b_p0_validation import resolve_m48b_owned_state_root


_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS = _ROOT / "host_brain" / "audit" / "migrations"
_COMMAND_ID = "sys_mode.switch"
_TENANT_ID = "tenant.task0495.obs.golden"
_KEY_ID = "task0495.obs.golden.hmac.v1"
_HANDLER_INSTALL_LOCK = threading.Lock()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def _migrate_database(db_path: Path, *, now_ms: int) -> None:
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_MIGRATIONS,
    ).apply_migrations(
        request_id="task0495.obs.golden.migration",
        ts_ms=now_ms - 1,
    )
    _require(result.success, "TASK0495_OBS_GOLDEN_MIGRATION_FAILED")


def _seed_principal(
    db_path: Path,
    *,
    harness: M48BIsolationHarness,
    action: str,
    scopes: tuple[str, ...],
    now_ms: int,
) -> None:
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO secure_ingress_principal(
                principal_id, principal_kind, tenant_id, status, version,
                granted_scopes_json, allowed_audiences_json,
                allowed_actions_json, valid_from_ms, valid_until_ms,
                updated_at_ms
            ) VALUES (?, 'service', ?, 'ACTIVE', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                harness.principal,
                _TENANT_ID,
                json.dumps(list(scopes)),
                json.dumps(["obs"]),
                json.dumps([action]),
                now_ms - 60_000,
                now_ms + 600_000,
                now_ms,
            ),
        )
        connection.execute(
            """
            INSERT INTO secure_ingress_credential(
                principal_id, key_id, credential_kind, algorithm,
                secret_ref, status, version, valid_from_ms,
                valid_until_ms, updated_at_ms
            ) VALUES (?, ?, 'hmac-sha256', 'hmac-sha256', ?, 'ACTIVE', 1, ?, ?, ?)
            """,
            (
                harness.principal,
                _KEY_ID,
                f"test-secret-ref://{_KEY_ID}",
                now_ms - 60_000,
                now_ms + 600_000,
                now_ms,
            ),
        )


def _identity(
    *,
    harness: M48BIsolationHarness,
    scopes: tuple[str, ...],
    now_ms: int,
) -> IngressIdentityEnvelope:
    metadata = CredentialProofMetadata(
        kind=CredentialProofKind.HMAC_SHA256,
        key_id=_KEY_ID,
        algorithm="hmac-sha256",
        proof_id="task0495.obs.golden.proof",
        issued_at_epoch_ms=now_ms,
        expires_at_epoch_ms=now_ms + 300_000,
    )
    return IngressIdentityEnvelope(
        schema_version=IdentitySchemaVersion.V1,
        principal=Principal(
            principal_id=harness.principal,
            kind=PrincipalKind.SERVICE,
            tenant_id=_TENANT_ID,
            granted_scopes=scopes,
        ),
        credential_proof=metadata,
        audience="obs",
        request_id="task0495.obs.golden.request",
        requested_scopes=scopes,
    )


def _signed_request(
    composition: ObsCommandComposition,
    *,
    harness: M48BIsolationHarness,
    identity: IngressIdentityEnvelope,
    raw_body: bytes,
    idempotency_key: str,
    nonce: str,
    now_ms: int,
) -> ObsCommandIngressRequest:
    spec = composition.registry.resolve_spec(_COMMAND_ID)
    _require(spec is not None, "TASK0495_OBS_GOLDEN_SPEC_MISSING")
    if spec is None:  # Keep the type narrow after the explicit proof above.
        raise AssertionError("TASK0495_OBS_GOLDEN_SPEC_MISSING")
    unsigned = ObsCommandIngressRequest(
        command_id=_COMMAND_ID,
        raw_body=raw_body,
        identity=identity,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof="0" * 64,
        ),
        idempotency_key=idempotency_key,
        nonce=nonce,
        received_at_epoch_ms=now_ms,
        transport_key=harness.channel,
        normalized_path=f"/v1/host/obs-commands/{_COMMAND_ID}",
        correlation_id="task0495.obs.golden.correlation",
    )
    secure_request = build_obs_command_secure_request(unsigned, spec)
    signature = build_hmac_sha256_signature(
        secure_request,
        harness.test_secret,
    )
    return replace(
        unsigned,
        credential=CredentialProof(
            metadata=identity.credential_proof,
            raw_proof=signature,
        ),
    )


def _build_recording_composition(
    *,
    env: Mapping[str, str],
    db_path: Path,
    emergency_audit_path: Path,
    outcome_journal_path: Path,
    handler: Callable[[Mapping[str, Any], str], Any],
) -> ObsCommandComposition:
    handlers = cast(
        MutableMapping[str, Callable[[Mapping[str, Any], str], Any]],
        domain_command_ports._HANDLERS,  # noqa: SLF001 - explicit test seam
    )
    route_handlers = cast(
        MutableMapping[str, Callable[..., Any]],
        domain_command_ports._ROUTE_PORT_HANDLERS,  # noqa: SLF001 - explicit test seam
    )

    def _recording_route_handler(
        payload: Mapping[str, Any],
        request_id: str,
        *,
        route_ports: Any,
    ) -> Any:
        # Production binds sys_mode.switch through the canonical route-port
        # overlay.  The no-real-I/O golden must replace that exact owner too;
        # patching only the legacy handler map no longer reaches execution.
        del route_ports
        return handler(payload, request_id)

    with _HANDLER_INSTALL_LOCK:
        original = handlers[_COMMAND_ID]
        original_route = route_handlers[_COMMAND_ID]
        handlers[_COMMAND_ID] = handler
        route_handlers[_COMMAND_ID] = _recording_route_handler
        try:
            return build_obs_command_composition(
                env=env,
                db_path=db_path,
                emergency_audit_path=emergency_audit_path,
                outcome_journal_path=outcome_journal_path,
            )
        finally:
            handlers[_COMMAND_ID] = original
            route_handlers[_COMMAND_ID] = original_route


def _execute(
    composition: ObsCommandComposition,
    request: ObsCommandIngressRequest,
) -> ObsCommandIngressOutcome:
    return asyncio.run(composition.ingress.execute(request))


def _tamper_proof(request: ObsCommandIngressRequest) -> ObsCommandIngressRequest:
    credential = request.credential
    _require(credential is not None, "TASK0495_OBS_GOLDEN_PROOF_MISSING")
    if credential is None:
        raise AssertionError("TASK0495_OBS_GOLDEN_PROOF_MISSING")
    proof = str(credential.raw_proof)
    _require(bool(proof), "TASK0495_OBS_GOLDEN_PROOF_MISSING")
    replacement = "0" if proof[0] != "0" else "1"
    return replace(
        request,
        credential=CredentialProof(
            metadata=credential.metadata,
            raw_proof=replacement + proof[1:],
        ),
    )


def run_obs_golden(
    *,
    state_root: Path,
    harness: M48BIsolationHarness,
    now_ms: int,
) -> dict[str, object]:
    """Run completed, duplicate, restart, and tamper cases without real effects."""

    harness.assert_safe()
    _require(harness.domain == "obs", "TASK0495_OBS_GOLDEN_DOMAIN_INVALID")
    _require(type(now_ms) is int, "TASK0495_OBS_GOLDEN_TIME_INVALID")
    root = resolve_m48b_owned_state_root(
        harness=harness,
        state_root=state_root,
    )
    db_path = root / "task0495-obs-golden.sqlite3"
    emergency_audit_path = root / "task0495-obs-golden-emergency.jsonl"
    outcome_journal_path = root / "task0495-obs-golden-outcomes.jsonl"

    _migrate_database(db_path, now_ms=now_ms)
    manifest = ObsCommandPortRegistry()
    spec = manifest.resolve_spec(_COMMAND_ID)
    _require(spec is not None, "TASK0495_OBS_GOLDEN_SPEC_MISSING")
    if spec is None:
        raise AssertionError("TASK0495_OBS_GOLDEN_SPEC_MISSING")
    _seed_principal(
        db_path,
        harness=harness,
        action=spec.action,
        scopes=spec.required_scopes,
        now_ms=now_ms,
    )

    effects_before = len(harness.sink.effects)

    def _recording_handler(
        payload: Mapping[str, Any],
        request_id: str,
    ) -> dict[str, object]:
        assert payload.get("operator") == harness.principal
        assert payload.get("owner_organ") == harness.principal
        assert "caller-supplied-authority" not in json.dumps(
            dict(payload),
            sort_keys=True,
        )
        harness.sink.emit(
            "obs.command.golden",
            {
                "command_id": _COMMAND_ID,
                "principal_id": str(payload["operator"]),
                "request_id": request_id,
            },
        )
        return {"success": True, "recording_only": True}

    env = {
        "HOST_SECURE_INGRESS_MODE": "ENFORCE",
        "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps(
            {_KEY_ID: harness.test_secret},
            separators=(",", ":"),
        ),
    }
    identity = _identity(
        harness=harness,
        scopes=spec.required_scopes,
        now_ms=now_ms,
    )
    raw_body = json.dumps(
        {
            "schema_version": OBS_COMMAND_ENVELOPE_SCHEMA,
            "command_id": _COMMAND_ID,
            "payload": {
                "target_mode": "focus",
                "reason": "TASK-0495 recording-only golden",
                "operator": "caller-supplied-authority",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    outcomes: list[ObsCommandIngressOutcome] = []
    first_result_ref: str | None = None
    first_reason_code = ""

    first_composition = _build_recording_composition(
        env=env,
        db_path=db_path,
        emergency_audit_path=emergency_audit_path,
        outcome_journal_path=outcome_journal_path,
        handler=_recording_handler,
    )
    try:
        _require(
            first_composition.execution_enabled,
            "TASK0495_OBS_GOLDEN_COMPOSITION_DISABLED",
        )
        first_request = _signed_request(
            first_composition,
            harness=harness,
            identity=identity,
            raw_body=raw_body,
            idempotency_key="task0495.obs.golden.idempotency",
            nonce="task0495.obs.golden.nonce.first",
            now_ms=now_ms,
        )
        first = _execute(first_composition, first_request)
        outcomes.append(first)
        _require(first.allowed, "TASK0495_OBS_GOLDEN_FIRST_NOT_ALLOWED")
        _require(first.executed, "TASK0495_OBS_GOLDEN_FIRST_NOT_EXECUTED")
        _require(not first.duplicate, "TASK0495_OBS_GOLDEN_FIRST_DUPLICATE")
        _require(
            first.reason_code == "OBS_COMMAND_COMPLETED",
            "TASK0495_OBS_GOLDEN_FIRST_NOT_COMPLETED",
        )
        _require(
            len(harness.sink.effects) == effects_before + 1,
            "TASK0495_OBS_GOLDEN_FIRST_EFFECT_COUNT_INVALID",
        )
        first_result_ref = first.result_ref
        first_reason_code = first.reason_code
        _require(
            first_result_ref is not None,
            "TASK0495_OBS_GOLDEN_RESULT_REF_MISSING",
        )

        duplicate = _execute(
            first_composition,
            _signed_request(
                first_composition,
                harness=harness,
                identity=identity,
                raw_body=raw_body,
                idempotency_key="task0495.obs.golden.idempotency",
                nonce="task0495.obs.golden.nonce.duplicate",
                now_ms=now_ms,
            ),
        )
        outcomes.append(duplicate)
        _require(duplicate.allowed, "TASK0495_OBS_GOLDEN_DUPLICATE_NOT_ALLOWED")
        _require(duplicate.duplicate, "TASK0495_OBS_GOLDEN_DUPLICATE_NOT_REPLAYED")
        _require(not duplicate.executed, "TASK0495_OBS_GOLDEN_DUPLICATE_EXECUTED")
        _require(
            duplicate.result_ref == first_result_ref,
            "TASK0495_OBS_GOLDEN_DUPLICATE_RESULT_DRIFT",
        )
        _require(
            len(harness.sink.effects) == effects_before + 1,
            "TASK0495_OBS_GOLDEN_DUPLICATE_EFFECT",
        )
    finally:
        first_composition.close()

    restarted = _build_recording_composition(
        env=env,
        db_path=db_path,
        emergency_audit_path=emergency_audit_path,
        outcome_journal_path=outcome_journal_path,
        handler=_recording_handler,
    )
    try:
        _require(
            restarted.execution_enabled,
            "TASK0495_OBS_GOLDEN_RESTART_DISABLED",
        )
        restart_duplicate = _execute(
            restarted,
            _signed_request(
                restarted,
                harness=harness,
                identity=identity,
                raw_body=raw_body,
                idempotency_key="task0495.obs.golden.idempotency",
                nonce="task0495.obs.golden.nonce.restart",
                now_ms=now_ms,
            ),
        )
        outcomes.append(restart_duplicate)
        _require(
            restart_duplicate.allowed and restart_duplicate.duplicate,
            "TASK0495_OBS_GOLDEN_RESTART_NOT_DUPLICATE",
        )
        _require(
            not restart_duplicate.executed,
            "TASK0495_OBS_GOLDEN_RESTART_EXECUTED",
        )
        _require(
            restart_duplicate.result_ref == first_result_ref,
            "TASK0495_OBS_GOLDEN_RESTART_RESULT_DRIFT",
        )
        _require(
            len(harness.sink.effects) == effects_before + 1,
            "TASK0495_OBS_GOLDEN_RESTART_EFFECT",
        )

        proof_base = _signed_request(
            restarted,
            harness=harness,
            identity=identity,
            raw_body=raw_body,
            idempotency_key="task0495.obs.golden.tampered-proof",
            nonce="task0495.obs.golden.nonce.tampered-proof",
            now_ms=now_ms,
        )
        tampered_proof = _execute(restarted, _tamper_proof(proof_base))
        outcomes.append(tampered_proof)
        _require(
            not tampered_proof.allowed and not tampered_proof.executed,
            "TASK0495_OBS_GOLDEN_TAMPERED_PROOF_EFFECT",
        )
        _require(
            tampered_proof.reason_code
            == IngressReasonCode.CREDENTIAL_INVALID.value,
            "TASK0495_OBS_GOLDEN_TAMPERED_PROOF_REASON_DRIFT",
        )
        _require(
            len(harness.sink.effects) == effects_before + 1,
            "TASK0495_OBS_GOLDEN_TAMPERED_PROOF_REACHED_HANDLER",
        )

        body_base = _signed_request(
            restarted,
            harness=harness,
            identity=identity,
            raw_body=raw_body,
            idempotency_key="task0495.obs.golden.tampered-body",
            nonce="task0495.obs.golden.nonce.tampered-body",
            now_ms=now_ms,
        )
        tampered_body = raw_body.replace(b'"focus"', b'"locked"', 1)
        _require(
            tampered_body != raw_body,
            "TASK0495_OBS_GOLDEN_BODY_TAMPER_FAILED",
        )
        tampered_body_outcome = _execute(
            restarted,
            replace(body_base, raw_body=tampered_body),
        )
        outcomes.append(tampered_body_outcome)
        _require(
            not tampered_body_outcome.allowed
            and not tampered_body_outcome.executed,
            "TASK0495_OBS_GOLDEN_TAMPERED_BODY_EFFECT",
        )
        _require(
            tampered_body_outcome.reason_code
            == IngressReasonCode.CREDENTIAL_INVALID.value,
            "TASK0495_OBS_GOLDEN_TAMPERED_BODY_REASON_DRIFT",
        )
        _require(
            len(harness.sink.effects) == effects_before + 1,
            "TASK0495_OBS_GOLDEN_TAMPERED_BODY_REACHED_HANDLER",
        )
    finally:
        restarted.close()

    with sqlite3.connect(str(db_path)) as connection:
        surface_id = SECURE_INGRESS_SURFACE_POLICIES[IngressSurface.OBS].surface_id
        claim_rows = connection.execute(
            """
            SELECT claim_ref, claim_status, request_id, result_ref,
                   surface, action, required_scopes_json
            FROM secure_ingress_idempotency_claim
            WHERE principal_id = ? AND surface = ?
            """,
            (harness.principal, surface_id),
        ).fetchall()
        durable_claim_count = len(claim_rows)
        audit_rows = connection.execute(
            """
            SELECT claim_ref, decision, reason_code, stage,
                   side_effects_executed
            FROM secure_ingress_audit
            WHERE principal_id = ? AND surface = ?
            """,
            (harness.principal, surface_id),
        ).fetchall()
        audit_count = len(audit_rows)

    effect_count = len(harness.sink.effects) - effects_before
    terminal_projection = tuple(
        (outcome.terminal_state, outcome.certainty)
        for outcome in outcomes
    )
    expected_terminal_projection = (
        ("SUCCEEDED", "EXECUTED"),
        ("SUCCEEDED", "EXECUTED"),
        ("SUCCEEDED", "EXECUTED"),
        (None, None),
        (None, None),
    )
    # Fail closed on missing, partial, unknown, or drifted terminal metadata.
    # Pre-admission credential rejections intentionally have no outcome-journal
    # terminal metadata; all durable first/replay outcomes must be exact.
    uncertain = terminal_projection != expected_terminal_projection
    _require(effect_count == 1, "TASK0495_OBS_GOLDEN_EFFECT_COUNT_INVALID")
    _require(
        durable_claim_count == 1,
        "TASK0495_OBS_GOLDEN_DURABLE_CLAIM_COUNT_INVALID",
    )
    _require(audit_count == 5, "TASK0495_OBS_GOLDEN_AUDIT_COUNT_INVALID")
    (
        claim_ref,
        claim_status,
        claim_request_id,
        claim_result_ref,
        claim_surface,
        claim_action,
        claim_scopes_json,
    ) = claim_rows[0]
    _require(
        bool(re.fullmatch(r"secure-ingress-claim://[0-9a-f]{32}", claim_ref)),
        "TASK0495_OBS_GOLDEN_CLAIM_REF_INVALID",
    )
    _require(
        claim_status == "COMMITTED",
        "TASK0495_OBS_GOLDEN_CLAIM_NOT_COMMITTED",
    )
    _require(
        claim_request_id == "task0495.obs.golden.request",
        "TASK0495_OBS_GOLDEN_CLAIM_REQUEST_DRIFT",
    )
    _require(
        claim_surface == surface_id and claim_action == spec.action,
        "TASK0495_OBS_GOLDEN_CLAIM_AUTHORITY_DRIFT",
    )
    _require(
        tuple(json.loads(claim_scopes_json))
        == tuple(sorted(set(spec.required_scopes))),
        "TASK0495_OBS_GOLDEN_CLAIM_SCOPE_DRIFT",
    )
    _require(
        claim_result_ref == first_result_ref
        == duplicate.result_ref
        == restart_duplicate.result_ref,
        "TASK0495_OBS_GOLDEN_CLAIM_RESULT_DRIFT",
    )
    _require(
        bool(
            re.fullmatch(
                r"obs-command-outcome://[0-9a-f]{64}",
                str(claim_result_ref),
            )
        ),
        "TASK0495_OBS_GOLDEN_CLAIM_RESULT_REF_INVALID",
    )
    audit_semantics = Counter(tuple(row) for row in audit_rows)
    expected_audit_semantics = Counter(
        {
            (
                claim_ref,
                "ALLOW",
                IngressReasonCode.ALLOWED.value,
                "COMPLETE",
                0,
            ): 1,
            (
                claim_ref,
                "DENY",
                IngressReasonCode.IDEMPOTENCY_DUPLICATE.value,
                "CLAIM",
                0,
            ): 2,
            (
                None,
                "DENY",
                IngressReasonCode.CREDENTIAL_INVALID.value,
                "CREDENTIAL",
                0,
            ): 2,
        }
    )
    _require(
        audit_semantics == expected_audit_semantics,
        "TASK0495_OBS_GOLDEN_AUDIT_SEMANTICS_DRIFT",
    )
    _require(not uncertain, "TASK0495_OBS_GOLDEN_UNCERTAIN")

    return {
        "status": "COMPLETED",
        "reason_code": first_reason_code,
        "effect_count": effect_count,
        "durable_claim_count": durable_claim_count,
        "audit_count": audit_count,
        "uncertain": uncertain,
    }


__all__ = ["run_obs_golden"]
