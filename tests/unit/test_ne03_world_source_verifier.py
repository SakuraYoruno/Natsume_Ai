from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Any

import pytest

from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    runtime_event_payload_digest,
)
from host_brain.contracts.runtime_context import WorldFactV2, WorldObservationBatchV1
from host_brain.runtime.world_source_verifier import (
    RuntimeEventWorldSourceVerifier,
    WorldObservationAuthorityProjectionV1,
    world_fact_semantic_digest,
    world_observation_source_identity_digest,
    world_observation_source_payload,
)


WORKSPACE_ID = "workspace-ne03"
SUBJECT_ID = "person-ne03"
NOW = 1_900_000_000_000


class FakeEventReader:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self.record = record
        self.calls: list[str] = []

    def get_event_by_id(self, *, event_id: str) -> dict[str, Any] | None:
        self.calls.append(event_id)
        return self.record


class MultiEventReader:
    def __init__(self, records: tuple[dict[str, Any], ...]) -> None:
        self.records = {
            str(item["event_id"]): item for item in records
        }

    def get_event_by_id(self, *, event_id: str) -> dict[str, Any] | None:
        return self.records.get(event_id)


class CountingBatchEventReader(MultiEventReader):
    def __init__(self, records: tuple[dict[str, Any], ...]) -> None:
        super().__init__(records)
        self.individual_reads = 0
        self.batch_reads: list[tuple[str, ...]] = []

    def get_event_by_id(self, *, event_id: str) -> dict[str, Any] | None:
        self.individual_reads += 1
        return super().get_event_by_id(event_id=event_id)

    def get_events_by_ids(
        self,
        *,
        event_ids: tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        self.batch_reads.append(tuple(event_ids))
        return {
            event_id: self.records[event_id]
            for event_id in event_ids
            if event_id in self.records
        }


def _evidence(
    *,
    authority_projections: tuple[
        WorldObservationAuthorityProjectionV1, ...
    ] = (),
    cursor: int = 1,
) -> tuple[dict[str, Any], WorldFactV2]:
    pending = WorldFactV2(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        key="runtime.state",
        value="READY",
        source_ref="world-observation://pending/source",
        confidence=1.0,
        observed_at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    identity = world_observation_source_identity_digest(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        observed_at_ms=NOW,
        upserts=(pending,),
        authority_projections=authority_projections,
    )
    source_ref = f"world-observation://source/{identity}"
    draft = RuntimeEvent.create(
        event_type="WORLD_OBSERVATION_CAPTURED",
        source_kind="SYSTEM",
        source_ref=source_ref,
        aggregate_type="WORLD_OBSERVATION_SOURCE",
        aggregate_id="world-scope-"
        + hashlib.sha256(
            f"{WORKSPACE_ID}\x00{SUBJECT_ID}".encode("utf-8")
        ).hexdigest(),
        aggregate_version=NOW,
        payload_ref="world-observation://transition/" + "0" * 64,
        payload={"workspace_id": WORKSPACE_ID, "subject_id": SUBJECT_ID},
        redaction_level="INTERNAL_REDACTED",
        ts_ms=NOW,
    )
    fact = replace(pending, source_ref=str(draft.event_id))
    payload = world_observation_source_payload(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        observed_at_ms=NOW,
        upserts=(fact,),
        authority_projections=authority_projections,
    )
    event = RuntimeEvent.create(
        event_type="WORLD_OBSERVATION_CAPTURED",
        source_kind="SYSTEM",
        source_ref=source_ref,
        aggregate_type="WORLD_OBSERVATION_SOURCE",
        aggregate_id=draft.aggregate_id,
        aggregate_version=NOW,
        payload_ref="world-observation://transition/" + payload["transition_digest"],
        payload=payload,
        dedupe_key=draft.dedupe_key,
        event_id=draft.event_id,
        redaction_level="INTERNAL_REDACTED",
        ts_ms=NOW,
    )
    record = {
        "cursor": cursor,
        **{
            field: getattr(event, field)
            for field in (
                "schema_version",
                "event_type",
                "source_kind",
                "source_ref",
                "aggregate_type",
                "aggregate_id",
                "aggregate_version",
                "payload_ref",
                "dedupe_key",
                "event_id",
                "redaction_level",
                "ts_ms",
            )
        },
        "payload": dict(event.payload),
        "payload_digest": runtime_event_payload_digest(event.payload),
        "evidence_refs": [],
        "created_ts_ms": NOW,
        "published_ts_ms": NOW,
    }
    return record, fact


def _verify(
    reader: FakeEventReader,
    *,
    workspace_id: str = WORKSPACE_ID,
    subject_id: str = SUBJECT_ID,
    source_ref: str | None = None,
    source_digest: str | None = None,
) -> bool:
    record, fact = _evidence()
    if reader.record is None:
        event_id = str(record["event_id"])
        digest = str(record["payload_digest"])
    else:
        event_id = str(reader.record.get("event_id") or record["event_id"])
        digest = str(reader.record.get("payload_digest") or record["payload_digest"])
    return RuntimeEventWorldSourceVerifier(reader)(
        workspace_id,
        subject_id,
        source_ref if source_ref is not None else event_id,
        source_digest if source_digest is not None else digest,
        NOW,
        (fact,),
        (),
    )


def _record(event: RuntimeEvent, *, cursor: int) -> dict[str, Any]:
    return {
        "cursor": cursor,
        "event_id": event.event_id,
        "schema_version": event.schema_version,
        "event_type": event.event_type,
        "source_kind": event.source_kind,
        "source_ref": event.source_ref,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "aggregate_version": event.aggregate_version,
        "payload_ref": event.payload_ref,
        "payload": dict(event.payload),
        "payload_digest": runtime_event_payload_digest(event.payload),
        "evidence_refs": list(event.evidence_refs),
        "dedupe_key": event.dedupe_key,
        "redaction_level": event.redaction_level,
        "ts_ms": event.ts_ms,
        "created_ts_ms": event.ts_ms,
        "published_ts_ms": event.ts_ms,
    }


def test_world_source_verifier_accepts_exact_event_scope_transition_and_digest() -> None:
    record, fact = _evidence()
    reader = FakeEventReader(record)
    verifier = RuntimeEventWorldSourceVerifier(reader)

    assert verifier(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(record["event_id"]),
        str(record["payload_digest"]),
        NOW,
        (fact,),
        (),
    ) is True
    assert verifier(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(record["event_id"]),
        str(record["payload_digest"]).removeprefix("sha256:"),
        NOW,
        (fact,),
        (),
    ) is True


def test_world_source_verifier_batch_reopens_page_with_one_bounded_read() -> None:
    record, fact = _evidence()
    reader = CountingBatchEventReader((record,))
    verifier = RuntimeEventWorldSourceVerifier(reader)
    batch = WorldObservationBatchV1(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        runtime_scope_id="runtime-scope-ne03",
        runtime_owner_id="runtime-owner-ne03",
        runtime_lease_id="runtime-lease-ne03",
        fencing_token=1,
        revision=1,
        previous_batch_digest="",
        source_event_ref=str(record["event_id"]),
        source_event_digest=str(record["payload_digest"]).removeprefix("sha256:"),
        observed_at_ms=NOW,
        upserts=(fact,),
    )

    assert verifier.verify_batch(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        batches=(batch,),
    ) is True
    assert reader.batch_reads == [(str(record["event_id"]),)]
    assert reader.individual_reads == 0


def test_world_source_verifier_rejects_scope_digest_and_transition_drift() -> None:
    record, fact = _evidence()
    assert _verify(FakeEventReader(record), workspace_id="other-workspace") is False
    assert _verify(FakeEventReader(record), subject_id="other-person") is False
    assert _verify(FakeEventReader(record), source_digest="0" * 64) is False
    changed = replace(fact, value="DRIFTED")
    assert RuntimeEventWorldSourceVerifier(FakeEventReader(record))(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(record["event_id"]),
        str(record["payload_digest"]),
        NOW,
        (changed,),
        (),
    ) is False


def test_world_source_verifier_rejects_missing_or_forged_reader_records() -> None:
    record, fact = _evidence()
    assert RuntimeEventWorldSourceVerifier(FakeEventReader(None))(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(record["event_id"]),
        str(record["payload_digest"]),
        NOW,
        (fact,),
        (),
    ) is False

    forged = dict(record)
    forged["payload_digest"] = "sha256:" + "f" * 64
    assert RuntimeEventWorldSourceVerifier(FakeEventReader(forged))(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(record["event_id"]),
        "f" * 64,
        NOW,
        (fact,),
        (),
    ) is False


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schema_version", "Natsume.runtime_event.v0"),
        ("source_kind", "TASK"),
        ("source_ref", "world-observation://source/" + "0" * 64),
        ("dedupe_key", "sha256:" + "0" * 64),
        ("event_id", "runtimeevent://sha256/" + "0" * 64),
        ("evidence_refs", ["evidence://forged/source"]),
        ("redaction_level", "PUBLIC"),
        ("created_ts_ms", NOW + 1),
        ("published_ts_ms", NOW - 1),
    ],
)
def test_world_source_verifier_rejects_complete_envelope_tamper(
    field: str,
    replacement: Any,
) -> None:
    record, fact = _evidence()
    event_id = str(record["event_id"])
    source_digest = str(record["payload_digest"])
    forged = dict(record)
    forged[field] = replacement

    assert RuntimeEventWorldSourceVerifier(FakeEventReader(forged))(
        WORKSPACE_ID,
        SUBJECT_ID,
        event_id,
        source_digest,
        NOW,
        (fact,),
        (),
    ) is False


def test_world_source_verifier_exactly_reopens_typed_bond_projection() -> None:
    pending = WorldFactV2(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        key="relationship.state",
        value="TRUSTED",
        source_ref="world-observation://pending/source",
        confidence=1.0,
        observed_at_ms=NOW,
        expires_at_ms=NOW + 60_000,
    )
    upstream = RuntimeEvent.create(
        event_type="BOND_STATE_PROJECTED",
        source_kind="SYSTEM",
        source_ref="bond://projection/person-ne03/7",
        aggregate_type="BOND_STATE",
        aggregate_id="person-ne03",
        aggregate_version=7,
        payload_ref="bond://state/person-ne03/7",
        payload={"stage": "TRUSTED", "revision": 7},
        evidence_refs=("bond-event://person-ne03/6",),
        redaction_level="INTERNAL_REDACTED",
        ts_ms=NOW - 1,
    )
    upstream_record = _record(upstream, cursor=1)
    projection = WorldObservationAuthorityProjectionV1(
        authority_kind="BOND",
        event_ref=str(upstream.event_id),
        payload_digest=str(upstream_record["payload_digest"]),
        event_type=upstream.event_type,
        source_kind=upstream.source_kind,
        source_ref=upstream.source_ref,
        aggregate_type=upstream.aggregate_type,
        aggregate_id=upstream.aggregate_id,
        aggregate_version=upstream.aggregate_version,
        payload_ref=upstream.payload_ref,
        evidence_refs=upstream.evidence_refs,
        redaction_level=upstream.redaction_level,
        ts_ms=upstream.ts_ms,
        fact_semantic_digests=(world_fact_semantic_digest(pending),),
    )
    # Build the relationship fact while preserving the circularity-free
    # World event construction.
    identity = world_observation_source_identity_digest(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        observed_at_ms=NOW,
        upserts=(pending,),
        authority_projections=(projection,),
    )
    draft = RuntimeEvent.create(
        event_type="WORLD_OBSERVATION_CAPTURED",
        source_kind="SYSTEM",
        source_ref=f"world-observation://source/{identity}",
        aggregate_type="WORLD_OBSERVATION_SOURCE",
        aggregate_id="world-scope-"
        + hashlib.sha256(
            f"{WORKSPACE_ID}\x00{SUBJECT_ID}".encode("utf-8")
        ).hexdigest(),
        aggregate_version=NOW,
        payload_ref="world-observation://transition/" + "0" * 64,
        payload={"workspace_id": WORKSPACE_ID},
        redaction_level="INTERNAL_REDACTED",
        ts_ms=NOW,
    )
    world_fact = replace(pending, source_ref=str(draft.event_id))
    payload = world_observation_source_payload(
        workspace_id=WORKSPACE_ID,
        subject_id=SUBJECT_ID,
        observed_at_ms=NOW,
        upserts=(world_fact,),
        authority_projections=(projection,),
    )
    world = RuntimeEvent.create(
        event_type=draft.event_type,
        source_kind=draft.source_kind,
        source_ref=draft.source_ref,
        aggregate_type=draft.aggregate_type,
        aggregate_id=draft.aggregate_id,
        aggregate_version=draft.aggregate_version,
        payload_ref="world-observation://transition/"
        + payload["transition_digest"],
        payload=payload,
        evidence_refs=(str(upstream.event_id),),
        dedupe_key=draft.dedupe_key,
        event_id=draft.event_id,
        redaction_level=draft.redaction_level,
        ts_ms=NOW,
    )
    world_record = _record(world, cursor=2)
    reader = MultiEventReader((upstream_record, world_record))
    verifier = RuntimeEventWorldSourceVerifier(reader)

    assert verifier(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(world.event_id),
        str(world_record["payload_digest"]),
        NOW,
        (world_fact,),
        (),
    ) is True

    upstream_record["dedupe_key"] = "sha256:" + "0" * 64
    assert verifier(
        WORKSPACE_ID,
        SUBJECT_ID,
        str(world.event_id),
        str(world_record["payload_digest"]),
        NOW,
        (world_fact,),
        (),
    ) is False

def test_world_source_verifier_propagates_authority_unavailability() -> None:
    record, fact = _evidence()

    class UnavailableReader:
        def get_event_by_id(self, *, event_id: str) -> dict[str, Any] | None:
            raise RuntimeError(f"authority unavailable: {event_id}")

    verifier = RuntimeEventWorldSourceVerifier(UnavailableReader())
    with pytest.raises(RuntimeError, match="authority unavailable"):
        verifier(
            WORKSPACE_ID,
            SUBJECT_ID,
            str(record["event_id"]),
            str(record["payload_digest"]),
            NOW,
            (fact,),
            (),
        )
