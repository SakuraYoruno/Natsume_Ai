"""Canonical Runtime Event authority for World observation source evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Protocol

from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    runtime_event_payload_digest,
    validate_runtime_event_record_envelope,
)

from host_brain.contracts.runtime_context import (
    WorldFactExpiryV1,
    WorldFactV2,
    WorldObservationBatchV1,
)


_SHA256_DIGEST_PATTERN = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")
_WORLD_SOURCE_REF_PATTERN = re.compile(
    r"^world-observation://source/([0-9a-f]{64})$"
)
_RUNTIME_EVENT_REF_PATTERN = re.compile(
    r"^runtimeevent://sha256/[0-9a-f]{64}$"
)
_AUTHORITY_KINDS = frozenset({"BOND", "EMOTION", "ENVIRONMENT"})
_AUTHORITY_EVENT_RULES = {
    "BOND": (
        "BOND_STATE_PROJECTED",
        "BOND_STATE",
        frozenset({"SYSTEM", "TRIBE_EVENT"}),
        ("bond://", "tribe://"),
    ),
    "EMOTION": (
        "EMOTION_STATE_PROJECTED",
        "EMOTION_STATE",
        frozenset({"SYSTEM", "BIO_OPS", "TRIBE_EVENT"}),
        ("emotion://", "bio-ops://", "tribe://"),
    ),
    "ENVIRONMENT": (
        "ENVIRONMENT_STATE_OBSERVED",
        "ENVIRONMENT_STATE",
        frozenset({"SYSTEM"}),
        ("environment://",),
    ),
}


@dataclass(frozen=True, slots=True)
class WorldObservationAuthorityProjectionV1:
    """Typed pointer to one independently persisted upstream observation.

    The World writer may bind this pointer, but cannot manufacture acceptance:
    the verifier reopens the referenced RuntimeEvent and reconstructs its
    canonical event/dedupe identity before accepting the World transition.
    """

    authority_kind: str
    event_ref: str
    payload_digest: str
    event_type: str
    source_kind: str
    source_ref: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    payload_ref: str
    evidence_refs: tuple[str, ...]
    redaction_level: str
    ts_ms: int
    fact_semantic_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        kind = str(self.authority_kind or "").strip().upper()
        event_ref = _exact_text(self.event_ref)
        payload_digest = _normalize_digest(self.payload_digest)
        fact_digests = tuple(sorted(set(self.fact_semantic_digests)))
        if (
            kind not in _AUTHORITY_KINDS
            or event_ref is None
            or _RUNTIME_EVENT_REF_PATTERN.fullmatch(event_ref) is None
            or payload_digest is None
            or not fact_digests
            or any(_normalize_digest(item) is None for item in fact_digests)
            or isinstance(self.aggregate_version, bool)
            or not isinstance(self.aggregate_version, int)
            or self.aggregate_version < 0
            or isinstance(self.ts_ms, bool)
            or not isinstance(self.ts_ms, int)
            or self.ts_ms < 0
        ):
            raise ValueError("invalid World observation authority projection")
        object.__setattr__(self, "authority_kind", kind)
        object.__setattr__(self, "event_ref", event_ref)
        object.__setattr__(self, "payload_digest", payload_digest)
        object.__setattr__(
            self,
            "fact_semantic_digests",
            tuple(_normalize_digest(item) for item in fact_digests),
        )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "WorldObservationAuthorityProjectionV1":
        required = {
            "schema",
            "authority_kind",
            "event_ref",
            "payload_digest",
            "event_type",
            "source_kind",
            "source_ref",
            "aggregate_type",
            "aggregate_id",
            "aggregate_version",
            "payload_ref",
            "evidence_refs",
            "redaction_level",
            "ts_ms",
            "fact_semantic_digests",
        }
        if set(value) != required or value.get("schema") != (
            "natsume.world-observation-authority-projection.v1"
        ):
            raise ValueError("invalid World authority projection schema")
        evidence = value.get("evidence_refs")
        fact_digests = value.get("fact_semantic_digests")
        if not isinstance(evidence, (list, tuple)) or not isinstance(
            fact_digests, (list, tuple)
        ):
            raise ValueError("invalid World authority projection references")
        aggregate_version = value.get("aggregate_version")
        ts_ms = value.get("ts_ms")
        if (
            isinstance(aggregate_version, bool)
            or not isinstance(aggregate_version, int)
            or isinstance(ts_ms, bool)
            or not isinstance(ts_ms, int)
        ):
            raise ValueError("invalid World authority projection chronology")
        return cls(
            authority_kind=str(value["authority_kind"]),
            event_ref=str(value["event_ref"]),
            payload_digest=str(value["payload_digest"]),
            event_type=str(value["event_type"]),
            source_kind=str(value["source_kind"]),
            source_ref=str(value["source_ref"]),
            aggregate_type=str(value["aggregate_type"]),
            aggregate_id=str(value["aggregate_id"]),
            aggregate_version=aggregate_version,
            payload_ref=str(value["payload_ref"]),
            evidence_refs=tuple(str(item) for item in evidence),
            redaction_level=str(value["redaction_level"]),
            ts_ms=ts_ms,
            fact_semantic_digests=tuple(str(item) for item in fact_digests),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.world-observation-authority-projection.v1",
            "authority_kind": self.authority_kind,
            "event_ref": self.event_ref,
            "payload_digest": self.payload_digest,
            "event_type": self.event_type,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_version": self.aggregate_version,
            "payload_ref": self.payload_ref,
            "evidence_refs": list(self.evidence_refs),
            "redaction_level": self.redaction_level,
            "ts_ms": self.ts_ms,
            "fact_semantic_digests": list(self.fact_semantic_digests),
        }


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _semantic_fact(fact: WorldFactV2) -> dict[str, Any]:
    payload = fact.to_dict()
    # The source RuntimeEvent identity is itself the fact's source_ref.  It is
    # deliberately removed from the pre-event semantic identity to avoid a
    # circular hash, then checked exactly against event_id below.
    payload.pop("source_ref", None)
    return payload


def world_fact_semantic_digest(fact: WorldFactV2) -> str:
    """Digest one fact before its World RuntimeEvent id is assigned."""

    return _canonical_digest(_semantic_fact(fact))


def world_observation_source_identity_digest(
    *,
    workspace_id: str,
    subject_id: str,
    observed_at_ms: int,
    upserts: Sequence[WorldFactV2] = (),
    expiries: Sequence[WorldFactExpiryV1] = (),
    authority_projections: Sequence[
        WorldObservationAuthorityProjectionV1
    ] = (),
) -> str:
    """Hash the transition semantics before the RuntimeEvent id exists."""

    return _canonical_digest(
        {
            "schema": "natsume.world-observation-source-identity.v1",
            "workspace_id": workspace_id,
            "subject_id": subject_id,
            "observed_at_ms": observed_at_ms,
            "upserts": sorted(
                (_semantic_fact(item) for item in upserts),
                key=_canonical_digest,
            ),
            "expiries": sorted(
                (item.to_dict() for item in expiries),
                key=_canonical_digest,
            ),
            "authority_projections": sorted(
                (item.to_dict() for item in authority_projections),
                key=_canonical_digest,
            ),
        }
    )


def world_observation_transition_digest(
    *,
    workspace_id: str,
    subject_id: str,
    observed_at_ms: int,
    upserts: Sequence[WorldFactV2] = (),
    expiries: Sequence[WorldFactExpiryV1] = (),
    authority_projections: Sequence[
        WorldObservationAuthorityProjectionV1
    ] = (),
) -> str:
    """Bind the exact typed journal transition to its source event."""

    return _canonical_digest(
        {
            "schema": "natsume.world-observation-transition.v1",
            "workspace_id": workspace_id,
            "subject_id": subject_id,
            "observed_at_ms": observed_at_ms,
            "upserts": sorted(
                (item.to_dict() for item in upserts),
                key=_canonical_digest,
            ),
            "expiries": sorted(
                (item.to_dict() for item in expiries),
                key=_canonical_digest,
            ),
            "authority_projections": sorted(
                (item.to_dict() for item in authority_projections),
                key=_canonical_digest,
            ),
        }
    )


def world_observation_source_payload(
    *,
    workspace_id: str,
    subject_id: str,
    observed_at_ms: int,
    upserts: Sequence[WorldFactV2] = (),
    expiries: Sequence[WorldFactExpiryV1] = (),
    authority_projections: Sequence[
        WorldObservationAuthorityProjectionV1
    ] = (),
) -> dict[str, Any]:
    """Return the secret-free payload independently checked on every read."""

    return {
        "schema": "natsume.world-observation-source.v1",
        "workspace_id": workspace_id,
        "subject_id": subject_id,
        "observed_at_ms": observed_at_ms,
        "transition_digest": world_observation_transition_digest(
            workspace_id=workspace_id,
            subject_id=subject_id,
            observed_at_ms=observed_at_ms,
            upserts=upserts,
            expiries=expiries,
            authority_projections=authority_projections,
        ),
        "upsert_count": len(tuple(upserts)),
        "expiry_count": len(tuple(expiries)),
        "authority_projections": sorted(
            (item.to_dict() for item in authority_projections),
            key=_canonical_digest,
        ),
    }


class RuntimeEventReader(Protocol):
    """Narrow read port required by World source verification."""

    def get_event_by_id(self, *, event_id: str) -> Mapping[str, Any] | None: ...

    def get_events_by_ids(
        self,
        *,
        event_ids: Sequence[str],
    ) -> Mapping[str, Mapping[str, Any]]: ...


def _exact_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _normalize_digest(value: Any) -> str | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    match = _SHA256_DIGEST_PATTERN.fullmatch(value)
    return match.group(1) if match is not None else None


class RuntimeEventWorldSourceVerifier:
    """Verify that a World transition cites one exact canonical Runtime Event.

    Repository failures deliberately propagate so the journal can distinguish
    unavailable authority from a normal evidence mismatch.
    """

    def __init__(self, event_reader: RuntimeEventReader) -> None:
        if not callable(getattr(event_reader, "get_event_by_id", None)):
            raise TypeError("event_reader must expose get_event_by_id")
        self._event_reader = event_reader

    def _verify_authority_projections(
        self,
        *,
        projections: Sequence[WorldObservationAuthorityProjectionV1],
        world_record: Mapping[str, Any],
        world_event_id: str,
        observed_at_ms: int,
        upserts: Sequence[WorldFactV2],
    ) -> bool:
        semantic_facts = {
            world_fact_semantic_digest(item): item for item in upserts
        }
        claimed_fact_digests: set[str] = set()
        kind_prefixes = {
            "BOND": ("relationship.",),
            "EMOTION": ("user.",),
            "ENVIRONMENT": ("environment.",),
        }
        world_cursor = int(world_record.get("cursor") or 0)
        world_created = int(world_record.get("created_ts_ms") or -1)
        for projection in projections:
            if projection.event_ref == world_event_id:
                return False
            reopened = self._event_reader.get_event_by_id(
                event_id=projection.event_ref
            )
            if reopened is None:
                return False
            try:
                source_payload = reopened.get("payload")
                if not isinstance(source_payload, Mapping):
                    return False
                expected = RuntimeEvent.create(
                    event_type=projection.event_type,
                    source_kind=projection.source_kind,
                    source_ref=projection.source_ref,
                    aggregate_type=projection.aggregate_type,
                    aggregate_id=projection.aggregate_id,
                    aggregate_version=projection.aggregate_version,
                    payload_ref=projection.payload_ref,
                    payload=source_payload,
                    evidence_refs=projection.evidence_refs,
                    redaction_level=projection.redaction_level,
                    ts_ms=projection.ts_ms,
                )
                validate_runtime_event_record_envelope(
                    reopened,
                    expected=expected,
                )
                source_cursor = int(reopened["cursor"])
                source_published = int(reopened["published_ts_ms"])
            except Exception:
                return False
            if (
                expected.event_id != projection.event_ref
                or _normalize_digest(reopened.get("payload_digest"))
                != projection.payload_digest
                or projection.ts_ms > observed_at_ms
                or source_cursor >= world_cursor
                or source_published > world_created
                or projection.event_type == "WORLD_OBSERVATION_CAPTURED"
                or projection.aggregate_type == "WORLD_OBSERVATION_SOURCE"
                or projection.source_ref.startswith("world-observation://")
            ):
                return False
            (
                required_event_type,
                required_aggregate_type,
                allowed_source_kinds,
                allowed_source_prefixes,
            ) = _AUTHORITY_EVENT_RULES[projection.authority_kind]
            if (
                projection.event_type != required_event_type
                or projection.aggregate_type != required_aggregate_type
                or projection.source_kind not in allowed_source_kinds
                or not projection.source_ref.startswith(
                    allowed_source_prefixes
                )
            ):
                return False
            allowed_prefixes = kind_prefixes[projection.authority_kind]
            for digest in projection.fact_semantic_digests:
                fact = semantic_facts.get(digest)
                if (
                    fact is None
                    or digest in claimed_fact_digests
                    or not fact.key.startswith(allowed_prefixes)
                ):
                    return False
                claimed_fact_digests.add(digest)
        return True

    def __call__(
        self,
        workspace_id: str,
        subject_id: str,
        source_ref: str,
        source_digest: str,
        observed_at_ms: int,
        upserts: Sequence[WorldFactV2],
        expiries: Sequence[WorldFactExpiryV1],
    ) -> bool:
        workspace = _exact_text(workspace_id)
        subject = _exact_text(subject_id)
        event_id = _exact_text(source_ref)
        expected_digest = _normalize_digest(source_digest)
        if (
            workspace is None
            or subject is None
            or event_id is None
            or expected_digest is None
        ):
            return False

        record = self._event_reader.get_event_by_id(event_id=event_id)
        if record is None:
            return False
        return self._verify_record(
            workspace=workspace,
            subject=subject,
            event_id=event_id,
            expected_digest=expected_digest,
            observed_at_ms=observed_at_ms,
            upserts=upserts,
            expiries=expiries,
            record=record,
        )

    def verify_batch(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        batches: Sequence[WorldObservationBatchV1],
    ) -> bool:
        """Fully verify one journal page after one bounded event read."""

        page = tuple(batches)
        if any(not isinstance(batch, WorldObservationBatchV1) for batch in page):
            return False
        batch_reader = getattr(self._event_reader, "get_events_by_ids", None)
        if not callable(batch_reader):
            return all(
                self(
                    workspace_id,
                    subject_id,
                    batch.source_event_ref,
                    batch.source_event_digest,
                    batch.observed_at_ms,
                    batch.upserts,
                    batch.expiries,
                )
                for batch in page
            )
        records = batch_reader(
            event_ids=tuple(batch.source_event_ref for batch in page),
        )
        if not isinstance(records, Mapping) or len(records) != len(page):
            return False
        for batch in page:
            workspace = _exact_text(workspace_id)
            subject = _exact_text(subject_id)
            event_id = _exact_text(batch.source_event_ref)
            expected_digest = _normalize_digest(batch.source_event_digest)
            record = records.get(str(event_id or ""))
            if (
                workspace is None
                or subject is None
                or event_id is None
                or expected_digest is None
                or not isinstance(record, Mapping)
                or not self._verify_record(
                    workspace=workspace,
                    subject=subject,
                    event_id=event_id,
                    expected_digest=expected_digest,
                    observed_at_ms=batch.observed_at_ms,
                    upserts=batch.upserts,
                    expiries=batch.expiries,
                    record=record,
                )
            ):
                return False
        return True

    def _verify_record(
        self,
        *,
        workspace: str,
        subject: str,
        event_id: str,
        expected_digest: str,
        observed_at_ms: int,
        upserts: Sequence[WorldFactV2],
        expiries: Sequence[WorldFactExpiryV1],
        record: Mapping[str, Any],
    ) -> bool:
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            return False
        if (
            not isinstance(payload.get("workspace_id"), str)
            or payload.get("workspace_id") != workspace
            or not isinstance(payload.get("subject_id"), str)
            or payload.get("subject_id") != subject
        ):
            return False

        try:
            raw_projections = payload.get("authority_projections")
            if not isinstance(raw_projections, (list, tuple)):
                return False
            projections = tuple(
                WorldObservationAuthorityProjectionV1.from_dict(item)
                for item in raw_projections
                if isinstance(item, Mapping)
            )
            if len(projections) != len(raw_projections):
                return False
            expected_payload = world_observation_source_payload(
                workspace_id=workspace,
                subject_id=subject,
                observed_at_ms=observed_at_ms,
                upserts=tuple(upserts),
                expiries=tuple(expiries),
                authority_projections=projections,
            )
            source_identity = world_observation_source_identity_digest(
                workspace_id=workspace,
                subject_id=subject,
                observed_at_ms=observed_at_ms,
                upserts=tuple(upserts),
                expiries=tuple(expiries),
                authority_projections=projections,
            )
        except (TypeError, ValueError):
            return False
        source_ref_expected = f"world-observation://source/{source_identity}"
        source_match = _WORLD_SOURCE_REF_PATTERN.fullmatch(source_ref_expected)
        if dict(payload) != expected_payload or source_match is None:
            return False
        try:
            expected_event = RuntimeEvent.create(
                event_type="WORLD_OBSERVATION_CAPTURED",
                source_kind="SYSTEM",
                source_ref=source_ref_expected,
                aggregate_type="WORLD_OBSERVATION_SOURCE",
                aggregate_id="world-scope-"
                + hashlib.sha256(
                    f"{workspace}\x00{subject}".encode("utf-8")
                ).hexdigest(),
                aggregate_version=observed_at_ms,
                payload_ref="world-observation://transition/"
                + expected_payload["transition_digest"],
                payload=expected_payload,
                evidence_refs=tuple(
                    sorted(item.event_ref for item in projections)
                ),
                redaction_level="INTERNAL_REDACTED",
                ts_ms=observed_at_ms,
            )
            validate_runtime_event_record_envelope(
                record,
                expected=expected_event,
            )
        except Exception:
            return False
        if expected_event.event_id != event_id:
            return False
        if any(item.source_ref != event_id for item in upserts):
            return False
        if not self._verify_authority_projections(
            projections=projections,
            world_record=record,
            world_event_id=event_id,
            observed_at_ms=observed_at_ms,
            upserts=upserts,
        ):
            return False

        durable_digest = _normalize_digest(record.get("payload_digest"))
        recomputed_digest = _normalize_digest(runtime_event_payload_digest(payload))
        return (
            durable_digest is not None
            and recomputed_digest is not None
            and expected_digest == durable_digest == recomputed_digest
        )


__all__ = [
    "RuntimeEventReader",
    "RuntimeEventWorldSourceVerifier",
    "WorldObservationAuthorityProjectionV1",
    "world_observation_source_identity_digest",
    "world_observation_source_payload",
    "world_observation_transition_digest",
    "world_fact_semantic_digest",
]
