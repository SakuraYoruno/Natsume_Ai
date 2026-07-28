"""Query-only SQLite reader for trusted sanitized Evolution Lab datasets."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Mapping

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    LabDatasetV1,
)
from host_brain.contracts.evolution_lab_redaction import (
    REDACTION_RULES_VERSION,
    TrustedRedactedTraceDataset,
    TrustedTraceRedactionError,
    trusted_redacted_dataset_schema_digest,
    trusted_redaction_identity_digest,
    trusted_redaction_rules_digest,
)
from host_brain.contracts.evolution_lab_trusted import RedactionReceiptV1


class SQLiteTrustedRedactedDatasetReader:
    """Reopen sanitized datasets; raw traces never enter this repository."""

    def __init__(self, db_path: str | Path) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:":
            raise TrustedTraceRedactionError(
                "redacted dataset reader requires a durable SQLite path"
            )
        self.db_path = Path(raw).expanduser().resolve()
        if not self.db_path.is_file():
            raise TrustedTraceRedactionError(
                "redacted dataset database does not exist"
            )
        self._uri = f"{self.db_path.as_uri()}?mode=ro"
        with self._connection() as conn:
            names = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        if not {
            "evolution_lab_trusted_redaction_receipt",
            "evolution_lab_trusted_redacted_dataset",
        }.issubset(names):
            raise TrustedTraceRedactionError(
                "migration 0140 redacted dataset schema is not ready"
            )

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._uri,
            uri=True,
            timeout=10.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1:
        if (
            not isinstance(manifest, DatasetManifestV1)
            or manifest.source_kind != "redacted_trace"
        ):
            raise TrustedTraceRedactionError(
                "reader requires a redacted_trace DatasetManifestV1"
            )
        reopened = self.read_by_dataset_id(manifest.dataset_id)
        if reopened is None or reopened.manifest != manifest:
            raise TrustedTraceRedactionError(
                "redacted dataset manifest is not authoritative"
            )
        return reopened.dataset

    def read_by_redaction_id(
        self,
        redaction_id: str,
    ) -> TrustedRedactedTraceDataset | None:
        identity = str(redaction_id or "").strip()
        if not identity or len(identity) > 160:
            raise TrustedTraceRedactionError("redaction_id is invalid")
        with self._connection() as conn:
            row = conn.execute(
                f"""
                {_SELECT_BINDINGS}
                WHERE receipt.redaction_id = ?
                """,
                (identity,),
            ).fetchone()
        return self._decode_row(row) if row is not None else None

    def read_by_dataset_id(
        self,
        dataset_id: str,
    ) -> TrustedRedactedTraceDataset | None:
        identity = str(dataset_id or "").strip()
        if not identity or len(identity) > 160:
            raise TrustedTraceRedactionError("dataset_id is invalid")
        with self._connection() as conn:
            row = conn.execute(
                f"""
                {_SELECT_BINDINGS}
                WHERE dataset.dataset_id = ?
                """,
                (identity,),
            ).fetchone()
        return self._decode_row(row) if row is not None else None

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> TrustedRedactedTraceDataset:
        try:
            dataset_payload = json.loads(str(row["dataset_json"]))
            manifest_payload = json.loads(str(row["manifest_json"]))
            receipt_payload = json.loads(str(row["receipt_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise TrustedTraceRedactionError(
                "redacted dataset JSON is corrupt"
            ) from exc
        if (
            not isinstance(dataset_payload, Mapping)
            or not isinstance(manifest_payload, Mapping)
            or not isinstance(receipt_payload, Mapping)
        ):
            raise TrustedTraceRedactionError(
                "redacted dataset payloads must be objects"
            )
        try:
            dataset = LabDatasetV1(
                dataset_id=dataset_payload.get("dataset_id"),
                records=tuple(dataset_payload.get("records", ())),
            )
            receipt = RedactionReceiptV1(
                redaction_id=receipt_payload.get("redaction_id"),
                workspace_id=receipt_payload.get("workspace_id"),
                source_digest=receipt_payload.get("source_digest"),
                rules_version=receipt_payload.get("rules_version"),
                rules_digest=receipt_payload.get("rules_digest"),
                output_dataset_digest=receipt_payload.get(
                    "output_dataset_digest"
                ),
                record_count=receipt_payload.get("record_count"),
                removed_field_count=receipt_payload.get(
                    "removed_field_count"
                ),
                created_at_ms=receipt_payload.get("created_at_ms"),
            )
            manifest = DatasetManifestV1(
                workspace_id=manifest_payload.get("workspace_id"),
                dataset_id=manifest_payload.get("dataset_id"),
                dataset_digest=manifest_payload.get("dataset_digest"),
                schema_digest=manifest_payload.get("schema_digest"),
                record_count=manifest_payload.get("record_count"),
                source_kind=manifest_payload.get("source_kind"),
                redaction_receipt_digest=manifest_payload.get(
                    "redaction_receipt_digest"
                ),
            )
        except (TypeError, ValueError) as exc:
            raise TrustedTraceRedactionError(
                "redacted dataset contract is corrupt"
            ) from exc
        identity_digest = trusted_redaction_identity_digest(
            receipt.workspace_id,
            receipt.source_digest,
        )
        if (
            dataset_payload != dataset.to_dict()
            or receipt_payload != receipt.to_dict()
            or manifest_payload != manifest.to_dict()
            or str(row["dataset_id"]) != dataset.dataset_id
            or str(row["workspace_id"]) != manifest.workspace_id
            or str(row["dataset_digest"]) != dataset.dataset_digest
            or str(row["manifest_digest"]) != manifest.manifest_digest
            or str(row["redaction_receipt_digest"])
            != receipt.receipt_digest
            or int(row["record_count"]) != len(dataset.records)
            or str(row["source_kind"]) != "redacted_trace"
            or int(row["created_at_ms"]) != receipt.created_at_ms
            or str(row["receipt_redaction_id"]) != receipt.redaction_id
            or str(row["receipt_workspace_id"]) != receipt.workspace_id
            or str(row["receipt_source_digest"]) != receipt.source_digest
            or str(row["receipt_output_dataset_digest"])
            != receipt.output_dataset_digest
            or str(row["receipt_index_digest"]) != receipt.receipt_digest
            or int(row["receipt_created_at_ms"]) != receipt.created_at_ms
            or receipt.workspace_id != manifest.workspace_id
            or receipt.output_dataset_digest != dataset.dataset_digest
            or receipt.record_count != len(dataset.records)
            or manifest.dataset_digest != dataset.dataset_digest
            or manifest.schema_digest
            != trusted_redacted_dataset_schema_digest(dataset)
            or manifest.redaction_receipt_digest != receipt.receipt_digest
            or receipt.rules_version != REDACTION_RULES_VERSION
            or receipt.rules_digest != trusted_redaction_rules_digest()
            or receipt.redaction_id
            != f"lab-redaction-{identity_digest[:48]}"
            or dataset.dataset_id != f"lab-redacted-{identity_digest[:48]}"
        ):
            raise TrustedTraceRedactionError(
                "redacted dataset durable binding drift"
            )
        return TrustedRedactedTraceDataset(
            receipt=receipt,
            manifest=manifest,
            dataset=dataset,
        )


_SELECT_BINDINGS = """
SELECT dataset.dataset_json, dataset.manifest_json,
       dataset.dataset_id, dataset.workspace_id,
       dataset.dataset_digest, dataset.manifest_digest,
       dataset.redaction_receipt_digest,
       dataset.record_count, dataset.source_kind,
       dataset.created_at_ms, receipt.receipt_json,
       receipt.redaction_id AS receipt_redaction_id,
       receipt.workspace_id AS receipt_workspace_id,
       receipt.source_digest AS receipt_source_digest,
       receipt.output_dataset_digest AS receipt_output_dataset_digest,
       receipt.receipt_digest AS receipt_index_digest,
       receipt.created_at_ms AS receipt_created_at_ms
FROM evolution_lab_trusted_redacted_dataset AS dataset
JOIN evolution_lab_trusted_redaction_receipt AS receipt
  ON receipt.receipt_digest = dataset.redaction_receipt_digest
"""


__all__ = ["SQLiteTrustedRedactedDatasetReader"]
