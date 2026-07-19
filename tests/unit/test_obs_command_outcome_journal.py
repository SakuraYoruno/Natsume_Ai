from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import json
import multiprocessing
from pathlib import Path
from queue import Empty

import pytest

import host_brain.observability.command_outcome_journal as outcome_journal
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeCorruption,
    ObsCommandOutcomeJournal,
    ObsCommandOutcomeJournalError,
    ObsCommandOutcomeKey,
    ObsCommandOutcomeState,
    ObsCommandReviewWaitRecovery,
    ObsCommandOutcomeTransitionError,
    OutcomeBeginDisposition,
    OutcomeJournalEvent,
    SideEffectCertainty,
)


NOW = 1_800_000_000_000


def _key(*, body: bytes = b'{"target_mode":"focus"}') -> ObsCommandOutcomeKey:
    return ObsCommandOutcomeKey.from_values(
        principal_id="principal.raw.must-not-persist",
        tenant_id="tenant.raw.must-not-persist",
        idempotency_key="idempotency.raw.must-not-persist",
        command_id="sys_mode.switch",
        action="obs:command:sys_mode.switch",
        required_scopes=("obs:write", "obs:sys-mode:write"),
        body_sha256=hashlib.sha256(body).hexdigest(),
    )


def _begin(path: Path, key: ObsCommandOutcomeKey):
    return ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW).begin(
        key,
        claim_ref="secure-ingress-claim://unit-claim",
        audit_ref="secure-ingress-audit://unit-audit",
    )


def _process_begin(path: str, start, queue) -> None:  # noqa: ANN001
    try:
        start.wait(10)
        result = _begin(Path(path), _key())
        queue.put(("ok", result.disposition.value))
    except Exception as exc:  # pragma: no cover - surfaced in parent assertion
        queue.put(("error", f"{type(exc).__name__}:{exc}"))


def test_begin_finalize_restart_lookup_and_terminal_immutability(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.jsonl"
    key = _key()
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)

    started = journal.begin(
        key,
        claim_ref="secure-ingress-claim://claim-1",
        audit_ref="secure-ingress-audit://audit-1",
    )
    assert started.disposition is OutcomeBeginDisposition.NEW
    assert started.record.state is ObsCommandOutcomeState.EXECUTING
    assert path.read_bytes().endswith(b"\n")

    completed = journal.finalize(
        started.record.receipt_ref,
        state=ObsCommandOutcomeState.SUCCEEDED,
        reason_code="OBS_COMMAND_COMPLETED",
        side_effect_certainty=SideEffectCertainty.EXECUTED,
        domain_result_ref="obs-command-result://sys-mode/1",
    )
    assert completed.state is ObsCommandOutcomeState.SUCCEEDED
    assert completed.domain_result_ref == "obs-command-result://sys-mode/1"

    restarted = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW + 1)
    assert restarted.lookup(key) == completed
    assert restarted.lookup(completed.receipt_ref) == completed
    duplicate = restarted.begin(
        key,
        claim_ref="secure-ingress-claim://new-secure-claim",
        audit_ref="secure-ingress-audit://new-secure-audit",
    )
    assert duplicate.disposition is OutcomeBeginDisposition.EXISTING_SAME
    assert duplicate.record == completed
    assert len(restarted.records()) == 2

    with pytest.raises(ObsCommandOutcomeTransitionError, match="TERMINAL_IMMUTABLE"):
        restarted.finalize(
            completed.receipt_ref,
            state=ObsCommandOutcomeState.REJECTED,
            reason_code="OBS_COMMAND_DOMAIN_REJECTED",
            side_effect_certainty=SideEffectCertainty.NOT_EXECUTED,
        )


def test_existing_v1_wal_reopens_and_appends_v2_terminal_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-v1.jsonl"
    key = _key()
    started = _begin(path, key)
    v2_record = json.loads(path.read_text(encoding="utf-8"))
    v2_record.pop("continuation_ref")
    v2_record.pop("terminal_result_ref")
    v2_record["schema_version"] = outcome_journal.OUTCOME_JOURNAL_SCHEMA_V1
    unsigned = {key: value for key, value in v2_record.items() if key != "checksum"}
    v2_record["checksum"] = outcome_journal._checksum(unsigned)
    wal_bytes = outcome_journal._canonical_json(v2_record) + b"\n"
    path.write_bytes(wal_bytes)
    anchor_unsigned = {
        "schema_version": outcome_journal._OUTCOME_JOURNAL_ANCHOR_SCHEMA,
        "sequence": 1,
        "wal_size": len(wal_bytes),
        "head_checksum": v2_record["checksum"],
    }
    anchor = {
        **anchor_unsigned,
        "anchor_checksum": outcome_journal._checksum(anchor_unsigned),
    }
    path.with_suffix(path.suffix + ".head").write_bytes(
        outcome_journal._canonical_json(anchor) + b"\n"
    )

    reopened = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW + 1)
    legacy = reopened.lookup(key)
    assert legacy is not None
    assert legacy.schema_version == outcome_journal.OUTCOME_JOURNAL_SCHEMA_V1
    assert legacy.receipt_ref == started.record.receipt_ref

    terminal = reopened.finalize(
        legacy.receipt_ref,
        state=ObsCommandOutcomeState.SUCCEEDED,
        reason_code="OBS_COMMAND_COMPLETED",
        side_effect_certainty=SideEffectCertainty.EXECUTED,
        domain_result_ref="obs-command-result://legacy-v1/accepted",
    )
    assert terminal.schema_version == outcome_journal.OUTCOME_JOURNAL_SCHEMA
    assert [record.schema_version for record in reopened.records()] == [
        outcome_journal.OUTCOME_JOURNAL_SCHEMA_V1,
        outcome_journal.OUTCOME_JOURNAL_SCHEMA,
    ]


def test_rejected_may_record_confirmed_domain_side_effect(tmp_path: Path) -> None:
    path = tmp_path / "executed-rejection.jsonl"
    started = _begin(path, _key())

    rejected = ObsCommandOutcomeJournal(path).finalize(
        started.record.receipt_ref,
        state=ObsCommandOutcomeState.REJECTED,
        reason_code="OBS_COMMAND_DOMAIN_REJECTED_AFTER_EXECUTION",
        side_effect_certainty=SideEffectCertainty.EXECUTED,
        domain_result_ref="obs-command-result://sys-mode/rejected-after-execution",
    )

    assert rejected.state is ObsCommandOutcomeState.REJECTED
    assert rejected.side_effect_certainty is SideEffectCertainty.EXECUTED
    assert ObsCommandOutcomeJournal(path).lookup(_key()) == rejected


def test_authenticated_rejection_atomically_closes_authenticated_lane(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authenticated-rejection.jsonl"
    key = _key()
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)

    rejected = journal.record_authenticated_rejection(
        key,
        claim_ref="secure-ingress-claim://authenticated-rejection",
        audit_ref="secure-ingress-audit://authenticated-rejection",
        reason_code="OBS_COMMAND_PAYLOAD_INVALID",
    )
    duplicate = journal.begin(
        key,
        claim_ref="secure-ingress-claim://authenticated-retry",
        audit_ref="secure-ingress-audit://authenticated-retry",
    )
    conflict = journal.record_authenticated_rejection(
        _key(body=b'{"target_mode":"different"}'),
        claim_ref="secure-ingress-claim://authenticated-conflict",
        audit_ref="secure-ingress-audit://authenticated-conflict",
        reason_code="OBS_COMMAND_PAYLOAD_INVALID",
    )

    assert rejected.disposition is OutcomeBeginDisposition.NEW
    assert rejected.record.event_type.value == "AUTHENTICATED_REJECTION"
    assert rejected.record.state is ObsCommandOutcomeState.REJECTED
    assert rejected.record.side_effect_certainty is SideEffectCertainty.NOT_EXECUTED
    assert duplicate.disposition is OutcomeBeginDisposition.EXISTING_SAME
    assert duplicate.record == rejected.record
    assert conflict.disposition is OutcomeBeginDisposition.CONFLICT
    assert ObsCommandOutcomeJournal(path).lookup(key) == rejected.record


def test_append_refuses_before_crossing_wal_capacity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "capacity.jsonl"
    key = _key()
    started = _begin(path, key)
    journal = ObsCommandOutcomeJournal(path)
    wal_before = path.read_bytes()
    anchor_path = path.with_suffix(path.suffix + ".head")
    anchor_before = anchor_path.read_bytes()
    monkeypatch.setattr(outcome_journal, "_MAX_WAL_BYTES", len(wal_before))

    with pytest.raises(ObsCommandOutcomeJournalError, match="WAL_TOO_LARGE"):
        journal.finalize(
            started.record.receipt_ref,
            state=ObsCommandOutcomeState.REJECTED,
            reason_code="OBS_COMMAND_REJECTED",
            side_effect_certainty=SideEffectCertainty.NOT_EXECUTED,
        )

    assert path.read_bytes() == wal_before
    assert anchor_path.read_bytes() == anchor_before
    assert journal.lookup(key) == started.record


def test_atomic_begin_same_and_conflict_across_threads(tmp_path: Path) -> None:
    path = tmp_path / "threaded.jsonl"
    key = _key()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: _begin(path, key), range(16)))

    dispositions = [item.disposition for item in results]
    assert dispositions.count(OutcomeBeginDisposition.NEW) == 1
    assert dispositions.count(OutcomeBeginDisposition.EXISTING_SAME) == 15
    assert len(ObsCommandOutcomeJournal(path).records()) == 1

    conflict = _begin(path, _key(body=b'{"target_mode":"sleep"}'))
    assert conflict.disposition is OutcomeBeginDisposition.CONFLICT
    assert len(ObsCommandOutcomeJournal(path).records()) == 1


def test_cross_process_lock_allows_exactly_one_new_begin(tmp_path: Path) -> None:
    path = tmp_path / "process.jsonl"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    queue = context.Queue()
    processes = [
        context.Process(target=_process_begin, args=(str(path), start, queue))
        for _ in range(4)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)
        assert process.exitcode == 0
    try:
        outcomes = [queue.get(timeout=5) for _ in processes]
    except Empty as exc:  # pragma: no cover - diagnostic failure
        raise AssertionError("child process did not report begin result") from exc

    assert all(kind == "ok" for kind, _value in outcomes), outcomes
    values = [value for _kind, value in outcomes]
    assert values.count(OutcomeBeginDisposition.NEW.value) == 1
    assert values.count(OutcomeBeginDisposition.EXISTING_SAME.value) == 3
    assert len(ObsCommandOutcomeJournal(path).records()) == 1


def test_crash_recovery_marks_executing_uncertain_and_never_reopens(tmp_path: Path) -> None:
    path = tmp_path / "crash.jsonl"
    key = _key()
    started = _begin(path, key)
    assert started.record.state is ObsCommandOutcomeState.EXECUTING

    restarted = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW + 10)
    recovered = restarted.recover_incomplete()
    assert len(recovered) == 1
    assert recovered[0].state is ObsCommandOutcomeState.UNCERTAIN
    assert recovered[0].side_effect_certainty is SideEffectCertainty.MAY_HAVE_EXECUTED
    assert restarted.recover_incomplete() == ()

    retry = restarted.begin(
        key,
        claim_ref="secure-ingress-claim://retry",
        audit_ref="secure-ingress-audit://retry",
    )
    assert retry.disposition is OutcomeBeginDisposition.EXISTING_SAME
    assert retry.record.state is ObsCommandOutcomeState.UNCERTAIN
    assert len(restarted.records()) == 2


def test_crash_recovery_reopens_exact_durable_review_wait(tmp_path: Path) -> None:
    path = tmp_path / "review-crash.jsonl"
    key = _key()
    started = _begin(path, key)
    restarted = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW + 10)
    assert restarted.recover_incomplete(
        defer_review_command_ids=frozenset({key.command_id})
    ) == ()

    def resolve(record):  # noqa: ANN001
        return ObsCommandReviewWaitRecovery(
            receipt_ref=record.receipt_ref,
            claim_ref=str(record.claim_ref),
            audit_ref=str(record.audit_ref),
            command_id=record.key.command_id,
            action=record.key.action,
            required_scopes=record.key.required_scopes,
            body_sha256=record.key.body_sha256,
            domain_result_ref="obs-command://result/sys-mode/switch/recovered",
            continuation_ref=(
                "governed-review://graph-recovered/1/review-recovered"
                "?checkpoint=checkpoint-recovered"
            ),
        )

    recovered = restarted.recover_incomplete(review_wait_resolver=resolve)

    assert len(recovered) == 1
    assert recovered[0].state is ObsCommandOutcomeState.WAITING_REVIEW
    assert recovered[0].reason_code == "OBS_COMMAND_WAITING_REVIEW_RECOVERED"
    assert recovered[0].side_effect_certainty is SideEffectCertainty.NOT_EXECUTED
    assert restarted.recover_incomplete(review_wait_resolver=resolve) == ()
    assert started.record.receipt_ref == recovered[0].receipt_ref


def test_waiting_review_survives_restart_and_resolves_append_only(
    tmp_path: Path,
) -> None:
    path = tmp_path / "waiting-review.jsonl"
    key = _key()
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)
    started = journal.begin(
        key,
        claim_ref="secure-ingress-claim://waiting-review-claim",
        audit_ref="secure-ingress-audit://waiting-review-audit",
    )
    continuation_ref = (
        "governed-review://graph-operator/1/review-operator"
        "?checkpoint=checkpoint-operator"
    )

    waiting = journal.mark_waiting_review(
        started.record.receipt_ref,
        domain_result_ref="obs-command://result/sys-mode/switch/canonical",
        continuation_ref=continuation_ref,
    )
    assert waiting.state is ObsCommandOutcomeState.WAITING_REVIEW
    assert waiting.event_type is OutcomeJournalEvent.WAIT_REVIEW
    assert waiting.domain_result_ref == (
        "obs-command://result/sys-mode/switch/canonical"
    )
    assert waiting.continuation_ref == continuation_ref
    assert waiting.side_effect_certainty is SideEffectCertainty.NOT_EXECUTED

    restarted = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW + 1)
    assert restarted.recover_incomplete() == ()
    replay = restarted.begin(
        key,
        claim_ref="secure-ingress-claim://waiting-review-claim",
        audit_ref="secure-ingress-audit://waiting-review-audit",
    )
    assert replay.disposition is OutcomeBeginDisposition.EXISTING_SAME
    assert replay.record == waiting
    assert (
        restarted.lookup_by_claim_ref(
            "secure-ingress-claim://waiting-review-claim"
        )
        == waiting
    )

    terminal = restarted.finalize(
        waiting.receipt_ref,
        state=ObsCommandOutcomeState.SUCCEEDED,
        reason_code="OBS_COMMAND_COMPLETED",
        side_effect_certainty=SideEffectCertainty.EXECUTED,
        domain_result_ref="obs-command://result/sys-mode/switch/canonical",
        terminal_result_ref="task-execution-receipt://operator/accepted",
    )
    assert terminal.event_type is OutcomeJournalEvent.RESOLVE_REVIEW
    assert terminal.state is ObsCommandOutcomeState.SUCCEEDED
    assert [record.state for record in restarted.records()] == [
        ObsCommandOutcomeState.EXECUTING,
        ObsCommandOutcomeState.WAITING_REVIEW,
        ObsCommandOutcomeState.SUCCEEDED,
    ]


def test_shadow_requires_explicit_promotion(tmp_path: Path) -> None:
    journal = ObsCommandOutcomeJournal(tmp_path / "shadow.jsonl", clock_ms=lambda: NOW)
    key = _key()
    shadow = journal.begin(
        key,
        claim_ref="secure-ingress-claim://shadow",
        audit_ref="secure-ingress-audit://shadow",
        shadow=True,
    )
    assert shadow.record.state is ObsCommandOutcomeState.SHADOW_EVALUATED

    ordinary = journal.begin(
        key,
        claim_ref="secure-ingress-claim://enforce",
        audit_ref="secure-ingress-audit://enforce",
    )
    assert ordinary.disposition is OutcomeBeginDisposition.EXISTING_SAME
    assert ordinary.record.state is ObsCommandOutcomeState.SHADOW_EVALUATED

    promoted = journal.begin(
        key,
        claim_ref="secure-ingress-claim://enforce",
        audit_ref="secure-ingress-audit://enforce",
        promote_shadow=True,
    )
    assert promoted.disposition is OutcomeBeginDisposition.PROMOTED
    assert promoted.record.state is ObsCommandOutcomeState.EXECUTING


def test_corrupt_checksum_strict_schema_and_truncated_tail_fail_closed(tmp_path: Path) -> None:
    checksum_path = tmp_path / "checksum.jsonl"
    _begin(checksum_path, _key())
    line = json.loads(checksum_path.read_text(encoding="utf-8"))
    line["reason_code"] = "OBS_COMMAND_TAMPERED"
    checksum_path.write_text(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(ObsCommandOutcomeCorruption, match="CHECKSUM"):
        ObsCommandOutcomeJournal(checksum_path)

    schema_path = tmp_path / "schema.jsonl"
    _begin(schema_path, _key())
    line = json.loads(schema_path.read_text(encoding="utf-8"))
    line["unexpected"] = "field"
    schema_path.write_text(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(ObsCommandOutcomeCorruption, match="SCHEMA"):
        ObsCommandOutcomeJournal(schema_path)

    truncated_path = tmp_path / "truncated.jsonl"
    _begin(truncated_path, _key())
    truncated_path.write_bytes(truncated_path.read_bytes()[:-1])
    with pytest.raises(ObsCommandOutcomeCorruption, match="TRUNCATED"):
        ObsCommandOutcomeJournal(truncated_path)


def test_zero_byte_and_complete_record_history_rollback_fail_closed(
    tmp_path: Path,
) -> None:
    zero_path = tmp_path / "zero-byte.jsonl"
    _begin(zero_path, _key())
    zero_path.write_bytes(b"")
    with pytest.raises(ObsCommandOutcomeCorruption, match="TRUNCATED"):
        ObsCommandOutcomeJournal(zero_path)

    rollback_path = tmp_path / "complete-record-rollback.jsonl"
    started = _begin(rollback_path, _key())
    valid_earlier_prefix = rollback_path.read_bytes()
    ObsCommandOutcomeJournal(rollback_path).finalize(
        started.record.receipt_ref,
        state=ObsCommandOutcomeState.SUCCEEDED,
        reason_code="OBS_COMMAND_COMPLETED",
        side_effect_certainty=SideEffectCertainty.EXECUTED,
        domain_result_ref="obs-command-result://sys-mode/rollback-test",
    )
    rollback_path.write_bytes(valid_earlier_prefix)

    with pytest.raises(ObsCommandOutcomeCorruption, match="HISTORY_ROLLBACK"):
        ObsCommandOutcomeJournal(rollback_path)


def test_preauth_rejection_persists_only_digests_and_safe_refs(tmp_path: Path) -> None:
    path = tmp_path / "preauth.jsonl"
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)
    body = b'{"opaque":"raw-body-must-not-persist"}'
    body_sha = hashlib.sha256(body).hexdigest()
    result = journal.record_preauth_rejection(
        principal_claim="principal-raw-secret-value",
        tenant_claim="tenant-raw-secret-value",
        idempotency_claim="idempotency-raw-secret-value",
        command_id="sys_mode.switch",
        action="obs:command:sys_mode.switch",
        required_scopes=("obs:write", "obs:sys-mode:write"),
        body_sha256=body_sha,
        reason_code="OBS_COMMAND_PROOF_MISSING",
        audit_ref="secure-ingress-audit://preauth-1",
    )
    assert result.record.state is ObsCommandOutcomeState.REJECTED
    serialized = path.read_text(encoding="utf-8")
    for forbidden in (
        "principal-raw-secret-value",
        "tenant-raw-secret-value",
        "idempotency-raw-secret-value",
        "raw-body-must-not-persist",
    ):
        assert forbidden not in serialized
    payload = json.loads(serialized)
    assert set(payload["key"]) == {
        "principal_digest", "tenant_digest", "idempotency_digest",
        "command_id", "action", "required_scopes", "body_sha256",
    }
    assert payload["key"]["body_sha256"] == body_sha
    assert payload["key"]["principal_digest"].startswith("sha256:")
    assert "proof" not in {key.lower() for key in payload}


def test_preauth_rejection_cannot_poison_authenticated_idempotency_lane(
    tmp_path: Path,
) -> None:
    path = tmp_path / "preauth-lane.jsonl"
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)
    key = _key()

    rejected = journal.record_preauth_rejection(
        principal_claim="principal.raw.must-not-persist",
        tenant_claim="tenant.raw.must-not-persist",
        idempotency_claim="idempotency.raw.must-not-persist",
        command_id=key.command_id,
        action=key.action,
        required_scopes=key.required_scopes,
        body_sha256=key.body_sha256,
        reason_code="OBS_COMMAND_PROOF_INVALID",
        audit_ref="secure-ingress-audit://bad-proof",
    )
    authenticated = journal.begin(
        key,
        claim_ref="secure-ingress-claim://valid-proof",
        audit_ref="secure-ingress-audit://valid-proof",
    )

    assert rejected.disposition is OutcomeBeginDisposition.NEW
    assert authenticated.disposition is OutcomeBeginDisposition.NEW
    assert rejected.record.key.idempotency_digest != key.idempotency_digest
    assert rejected.record.receipt_ref != authenticated.record.receipt_ref
    assert len(journal.records()) == 2


def test_preauth_digest_domain_cannot_alias_prefixed_authenticated_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "preauth-domain-separation.jsonl"
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)
    authenticated_key = ObsCommandOutcomeKey.from_values(
        principal_id="principal.raw.must-not-persist",
        tenant_id="tenant.raw.must-not-persist",
        idempotency_key="preauth:chosen-value",
        command_id="sys_mode.switch",
        action="obs:command:sys_mode.switch",
        required_scopes=("obs:write", "obs:sys-mode:write"),
        body_sha256=_key().body_sha256,
    )
    rejected = journal.record_preauth_rejection(
        principal_claim="principal.raw.must-not-persist",
        tenant_claim="tenant.raw.must-not-persist",
        idempotency_claim="chosen-value",
        command_id=authenticated_key.command_id,
        action=authenticated_key.action,
        required_scopes=authenticated_key.required_scopes,
        body_sha256=authenticated_key.body_sha256,
        reason_code="OBS_COMMAND_PROOF_INVALID",
        audit_ref="secure-ingress-audit://domain-separated",
    )
    authenticated = journal.begin(
        authenticated_key,
        claim_ref="secure-ingress-claim://domain-separated",
        audit_ref="secure-ingress-audit://domain-separated",
    )

    assert rejected.record.key.digest_domain == "preauth"
    assert authenticated.record.key.digest_domain == "auth"
    assert authenticated.disposition is OutcomeBeginDisposition.NEW
    assert rejected.record.receipt_ref != authenticated.record.receipt_ref


def test_first_wal_append_best_effort_syncs_parent_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "directory-sync" / "outcomes.jsonl"
    synced: list[Path] = []
    monkeypatch.setattr(outcome_journal, "_best_effort_fsync_directory", synced.append)
    journal = ObsCommandOutcomeJournal(path, clock_ms=lambda: NOW)

    started = journal.begin(
        _key(),
        claim_ref="secure-ingress-claim://directory-sync",
        audit_ref="secure-ingress-audit://directory-sync",
    )
    journal.finalize(
        started.record.receipt_ref,
        state=ObsCommandOutcomeState.REJECTED,
        reason_code="OBS_COMMAND_REJECTED",
        side_effect_certainty=SideEffectCertainty.NOT_EXECUTED,
    )

    assert synced == [path.parent]


@pytest.mark.parametrize("error_number", [errno.EIO, errno.ENOSPC])
def test_directory_fsync_hard_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    def fail_open(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise OSError(error_number, "forced directory sync failure")

    monkeypatch.setattr(outcome_journal.os, "open", fail_open)

    with pytest.raises(
        ObsCommandOutcomeJournalError,
        match="DIRECTORY_FSYNC_FAILED",
    ):
        outcome_journal._best_effort_fsync_directory(tmp_path)


def test_directory_fsync_explicitly_unsupported_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsupported = getattr(errno, "ENOTSUP", errno.EINVAL)

    def fail_open(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        raise OSError(unsupported, "directory fsync unsupported")

    monkeypatch.setattr(outcome_journal.os, "open", fail_open)
    outcome_journal._best_effort_fsync_directory(tmp_path)
