"""Deterministic, privacy-bounded real-trace intake for trusted Lab replay."""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Protocol

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    LabDatasetV1,
    canonical_evolution_lab_digest,
)
from host_brain.contracts.evolution_lab_redaction import (
    REDACTION_RULES_VERSION,
    TrustedRedactedTraceDataset,
    TrustedTraceRedactionError,
    project_trusted_trace_records,
    trusted_redacted_dataset_schema_digest,
    trusted_redaction_identity_digest,
    trusted_redaction_rules_digest,
)
from host_brain.contracts.evolution_lab_trusted import RedactionReceiptV1
from host_brain.core.ids import now_ms
from host_brain.evolution_lab.golden_datasets import (
    BuiltInGoldenDatasetReader,
)


_MAX_RECORDS = 10_000
_MAX_SOURCE_BYTES = 6 * 1024 * 1024


class TrustedRedactedDatasetLedger(Protocol):
    def append_redacted_dataset(
        self,
        *,
        receipt: RedactionReceiptV1,
        manifest: DatasetManifestV1,
        dataset: LabDatasetV1,
    ) -> str: ...


class TrustedRedactedDatasetReader(Protocol):
    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1: ...

    def read_by_redaction_id(
        self,
        redaction_id: str,
    ) -> TrustedRedactedTraceDataset | None: ...

    def read_by_dataset_id(
        self,
        dataset_id: str,
    ) -> TrustedRedactedTraceDataset | None: ...


class TrustedCompositeDatasetReader:
    """Resolve only code-backed synthetic or 0140 redacted datasets."""

    def __init__(
        self,
        *,
        redacted_reader: TrustedRedactedDatasetReader,
    ) -> None:
        if not all(
            callable(getattr(redacted_reader, name, None))
            for name in (
                "read",
                "read_by_redaction_id",
                "read_by_dataset_id",
            )
        ):
            raise TypeError(
                "redacted_reader must implement trusted dataset reads"
            )
        self._synthetic = BuiltInGoldenDatasetReader()
        self._redacted = redacted_reader

    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1:
        if manifest.source_kind == "synthetic":
            return self._synthetic.read(manifest=manifest)
        if manifest.source_kind == "redacted_trace":
            return self._redacted.read(manifest=manifest)
        raise TrustedTraceRedactionError(
            "trusted Lab dataset source kind is unsupported"
        )


class TrustedTraceRedactionService:
    """Remove private bodies before atomically persisting replayable evidence."""

    def __init__(
        self,
        *,
        ledger: TrustedRedactedDatasetLedger,
        reader: TrustedRedactedDatasetReader,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        if ledger is None or not callable(
            getattr(ledger, "append_redacted_dataset", None)
        ):
            raise TypeError("ledger must append trusted redacted datasets")
        if not all(
            callable(getattr(reader, name, None))
            for name in (
                "read",
                "read_by_redaction_id",
                "read_by_dataset_id",
            )
        ):
            raise TypeError(
                "reader must implement trusted redacted dataset reads"
            )
        self._ledger = ledger
        self._reader = reader
        self._clock = clock

    def redact_and_persist(
        self,
        *,
        workspace_id: str,
        records: tuple[Mapping[str, Any], ...],
    ) -> TrustedRedactedTraceDataset:
        workspace = str(workspace_id or "").strip()
        if not workspace or len(workspace) > 160:
            raise TrustedTraceRedactionError("workspace_id is invalid")
        source_records = _json_records(records)
        source_digest = canonical_evolution_lab_digest(
            {
                "schema": "natsume.evolution-lab.real-trace-source.v1",
                "records": source_records,
            }
        )
        identity_digest = trusted_redaction_identity_digest(
            workspace,
            source_digest,
        )
        redaction_id = f"lab-redaction-{identity_digest[:48]}"
        replay = self._reader.read_by_redaction_id(redaction_id)
        if replay is not None:
            _assert_replay(
                replay,
                workspace_id=workspace,
                source_digest=source_digest,
            )
            return replay
        projected, removed = project_trusted_trace_records(
            source_records,
            source_digest=source_digest,
        )
        dataset = LabDatasetV1(
            dataset_id=f"lab-redacted-{identity_digest[:48]}",
            records=projected,
        )
        receipt = RedactionReceiptV1(
            redaction_id=redaction_id,
            workspace_id=workspace,
            source_digest=source_digest,
            rules_version=REDACTION_RULES_VERSION,
            rules_digest=trusted_redaction_rules_digest(),
            output_dataset_digest=dataset.dataset_digest,
            record_count=len(dataset.records),
            removed_field_count=removed,
            created_at_ms=max(0, int(self._clock())),
        )
        manifest = DatasetManifestV1(
            workspace_id=workspace,
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.dataset_digest,
            schema_digest=trusted_redacted_dataset_schema_digest(dataset),
            record_count=len(dataset.records),
            source_kind="redacted_trace",
            redaction_receipt_digest=receipt.receipt_digest,
        )
        try:
            self._ledger.append_redacted_dataset(
                receipt=receipt,
                manifest=manifest,
                dataset=dataset,
            )
        except Exception:
            # A concurrent exact intake may have committed first with a
            # different timestamp.  Only an independently reopened exact
            # source/rules binding can turn that race into replay.
            concurrent = self._reader.read_by_redaction_id(redaction_id)
            if concurrent is None:
                raise
            _assert_replay(
                concurrent,
                workspace_id=workspace,
                source_digest=source_digest,
            )
            return concurrent
        reopened = self._reader.read_by_redaction_id(redaction_id)
        if reopened is None:
            raise TrustedTraceRedactionError(
                "redacted dataset commit could not be reopened"
            )
        _assert_replay(
            reopened,
            workspace_id=workspace,
            source_digest=source_digest,
        )
        return reopened


def _json_records(
    records: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    if (
        not isinstance(records, tuple)
        or not records
        or len(records) > _MAX_RECORDS
        or not all(isinstance(item, Mapping) for item in records)
    ):
        raise TrustedTraceRedactionError(
            f"records must contain 1..{_MAX_RECORDS} mappings"
        )
    try:
        encoded = json.dumps(
            list(records),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        copied = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise TrustedTraceRedactionError(
            "real trace records must be bounded JSON"
        ) from exc
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise TrustedTraceRedactionError(
            "real trace source exceeds the intake limit"
        )
    return tuple(dict(item) for item in copied)


def _assert_replay(
    value: TrustedRedactedTraceDataset,
    *,
    workspace_id: str,
    source_digest: str,
) -> None:
    if (
        value.receipt.workspace_id != workspace_id
        or value.receipt.source_digest != source_digest
        or value.receipt.rules_version != REDACTION_RULES_VERSION
        or value.receipt.rules_digest
        != trusted_redaction_rules_digest()
        or value.manifest.source_kind != "redacted_trace"
    ):
        raise TrustedTraceRedactionError(
            "redacted trace replay identity drift"
        )


__all__ = [
    "REDACTION_RULES_VERSION",
    "TrustedCompositeDatasetReader",
    "TrustedRedactedTraceDataset",
    "TrustedTraceRedactionError",
    "TrustedTraceRedactionService",
]
