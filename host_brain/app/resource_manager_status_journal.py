"""Host-lease-fenced durable status journal for SystemResourceManager."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from typing import Any

from host_brain.contracts.resource_manager_status import (
    RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
    RESOURCE_MANAGER_STATUS_EVENT_TYPE,
    ResourceManagerStatusHeadV1,
    ResourceManagerStatusJournalError,
)
from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    runtime_event_payload_digest,
    sanitize_runtime_event_payload,
    validate_runtime_event_record_envelope,
)


_SOURCE_KIND = "SYSTEM"
_REDACTION_LEVEL = "INTERNAL_REDACTED"
_PAGE_SIZE = 1000


def _text(value: Any, field_name: str, *, max_length: int = 512) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ResourceManagerStatusJournalError(
            "RESOURCE_MANAGER_STATUS_INVALID",
            f"{field_name} is required",
        )
    if len(normalized) > max_length:
        raise ResourceManagerStatusJournalError(
            "RESOURCE_MANAGER_STATUS_INVALID",
            f"{field_name} exceeds {max_length} chars",
        )
    return normalized


def _integer(
    value: Any,
    field_name: str,
    *,
    minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ResourceManagerStatusJournalError(
            "RESOURCE_MANAGER_STATUS_INVALID",
            f"{field_name} is invalid",
        )
    return value


class RuntimeEventResourceManagerStatusJournal:
    """One exact Runtime Event aggregate for one Host runtime scope."""

    def __init__(
        self,
        *,
        event_store: Any,
        runtime_scope_id: str,
        runtime_authority_provider: Callable[[], Mapping[str, Any]],
    ) -> None:
        if event_store is None:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_STORE_REQUIRED",
                "Runtime Event store is required",
            )
        if not callable(runtime_authority_provider):
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_AUTHORITY_REQUIRED",
                "runtime authority provider is required",
            )
        self._event_store = event_store
        self._runtime_scope_id = _text(runtime_scope_id, "runtime_scope_id")
        self._runtime_authority_provider = runtime_authority_provider
        scope_digest = hashlib.sha256(
            self._runtime_scope_id.encode("utf-8")
        ).hexdigest()
        self._source_ref = f"resource-manager://status/{scope_digest}"
        self._head: ResourceManagerStatusHeadV1 | None = None
        self._loaded = False

    @property
    def runtime_scope_id(self) -> str:
        return self._runtime_scope_id

    def _authority(self, *, observed_at_ms: int) -> dict[str, Any]:
        try:
            raw = self._runtime_authority_provider()
        except Exception as exc:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_AUTHORITY_UNAVAILABLE",
                "runtime authority provider failed",
            ) from exc
        if not isinstance(raw, Mapping):
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_AUTHORITY_UNAVAILABLE",
                "runtime authority is unavailable",
            )
        scope = _text(raw.get("runtime_scope_id"), "runtime_scope_id")
        owner = _text(raw.get("owner_id"), "runtime_owner_id")
        lease = _text(raw.get("lease_id"), "runtime_lease_id")
        fence = _integer(raw.get("fencing_token"), "fencing_token", minimum=1)
        issued = _integer(raw.get("issued_at_ms"), "runtime_issued_at_ms")
        expires = _integer(
            raw.get("expires_at_ms"),
            "runtime_expires_at_ms",
            minimum=1,
        )
        if (
            scope != self._runtime_scope_id
            or expires <= issued
            or issued > observed_at_ms
            or expires <= observed_at_ms
            or raw.get("released_at_ms") is not None
        ):
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_AUTHORITY_STALE",
                "runtime authority is not active for the observation",
            )
        return {
            "runtime_scope_id": scope,
            "runtime_owner_id": owner,
            "runtime_lease_id": lease,
            "fencing_token": fence,
            "runtime_issued_at_ms": issued,
            "runtime_expires_at_ms": expires,
            "runtime_released_at_ms": None,
        }

    def _event(
        self,
        *,
        revision: int,
        reason: str,
        observed_at_ms: int,
        status: Mapping[str, Any],
        authority: Mapping[str, Any],
        previous_payload_digest: str,
    ) -> RuntimeEvent:
        payload = sanitize_runtime_event_payload(
            {
                **dict(authority),
                "status_revision": int(revision),
                "previous_payload_digest": str(previous_payload_digest or ""),
                "reason": _text(reason, "reason", max_length=128),
                "observed_at_ms": int(observed_at_ms),
                "status": dict(status),
            }
        )
        return RuntimeEvent.create(
            event_type=RESOURCE_MANAGER_STATUS_EVENT_TYPE,
            source_kind=_SOURCE_KIND,
            source_ref=self._source_ref,
            aggregate_type=RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
            aggregate_id=self._runtime_scope_id,
            aggregate_version=int(revision),
            payload_ref=(
                f"{self._source_ref}/revision/{int(revision)}"
            ),
            payload=payload,
            evidence_refs=(),
            redaction_level=_REDACTION_LEVEL,
            ts_ms=int(observed_at_ms),
        )

    def _head_from_record(
        self,
        record: Mapping[str, Any],
        *,
        expected_revision: int,
        previous_payload_digest: str,
        previous_authority: tuple[str, str, int] | None,
    ) -> tuple[ResourceManagerStatusHeadV1, tuple[str, str, int]]:
        payload = dict(record.get("payload") or {})
        revision = _integer(
            record.get("aggregate_version"),
            "aggregate_version",
            minimum=1,
        )
        if revision != expected_revision:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_REVISION_GAP",
                "status revision is not contiguous",
            )
        observed = _integer(
            payload.get("observed_at_ms"),
            "observed_at_ms",
            minimum=1,
        )
        if (
            str(record.get("aggregate_type") or "")
            != RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE
            or str(record.get("aggregate_id") or "") != self._runtime_scope_id
            or str(record.get("event_type") or "")
            != RESOURCE_MANAGER_STATUS_EVENT_TYPE
            or _integer(
                payload.get("status_revision"),
                "status_revision",
                minimum=1,
            )
            != revision
            or str(payload.get("runtime_scope_id") or "")
            != self._runtime_scope_id
            or str(payload.get("previous_payload_digest") or "")
            != previous_payload_digest
            or int(record.get("ts_ms") or 0) != observed
            or not isinstance(payload.get("status"), Mapping)
        ):
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_DURABLE_DATA_INVALID",
                "status journal envelope or digest chain is invalid",
            )
        owner = _text(payload.get("runtime_owner_id"), "runtime_owner_id")
        lease = _text(payload.get("runtime_lease_id"), "runtime_lease_id")
        fence = _integer(payload.get("fencing_token"), "fencing_token", minimum=1)
        issued = _integer(payload.get("runtime_issued_at_ms"), "runtime_issued_at_ms")
        expires = _integer(
            payload.get("runtime_expires_at_ms"),
            "runtime_expires_at_ms",
            minimum=1,
        )
        if (
            expires <= issued
            or issued > observed
            or expires <= observed
            or payload.get("runtime_released_at_ms") is not None
        ):
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_AUTHORITY_LINEAGE_INVALID",
                "durable runtime authority is invalid",
            )
        if previous_authority is not None:
            previous_owner, previous_lease, previous_fence = previous_authority
            if (
                fence < previous_fence
                or (
                    fence == previous_fence
                    and (owner != previous_owner or lease != previous_lease)
                )
                or (
                    fence > previous_fence
                    and lease == previous_lease
                )
            ):
                raise ResourceManagerStatusJournalError(
                    "RESOURCE_MANAGER_STATUS_AUTHORITY_LINEAGE_INVALID",
                    "durable runtime fencing lineage is invalid",
                )
        reason = _text(payload.get("reason"), "reason", max_length=128)
        event = self._event(
            revision=revision,
            reason=reason,
            observed_at_ms=observed,
            status=dict(payload["status"]),
            authority={
                "runtime_scope_id": self._runtime_scope_id,
                "runtime_owner_id": owner,
                "runtime_lease_id": lease,
                "fencing_token": fence,
                "runtime_issued_at_ms": issued,
                "runtime_expires_at_ms": expires,
                "runtime_released_at_ms": None,
            },
            previous_payload_digest=previous_payload_digest,
        )
        try:
            validate_runtime_event_record_envelope(record, expected=event)
        except Exception as exc:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_DURABLE_DATA_INVALID",
                "status Runtime Event envelope is invalid",
            ) from exc
        payload_digest = runtime_event_payload_digest(payload)
        if str(record.get("payload_digest") or "") != payload_digest:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_DURABLE_DATA_INVALID",
                "status payload digest is invalid",
            )
        return (
            ResourceManagerStatusHeadV1(
                runtime_scope_id=self._runtime_scope_id,
                revision=revision,
                event_id=str(record["event_id"]),
                payload_digest=payload_digest,
                observed_at_ms=observed,
                payload=payload,
            ),
            (owner, lease, fence),
        )

    def recover_head(self) -> ResourceManagerStatusHeadV1 | None:
        """Replay and validate the full aggregate exactly once per adapter."""

        if self._loaded:
            return self._head
        cursor = 0
        revision = 1
        previous_digest = ""
        previous_authority: tuple[str, str, int] | None = None
        recovered: ResourceManagerStatusHeadV1 | None = None
        try:
            while True:
                page = self._event_store.list_events_for_aggregates(
                    aggregate_refs=(
                        (
                            RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
                            self._runtime_scope_id,
                        ),
                    ),
                    after_cursor=cursor,
                    limit=_PAGE_SIZE,
                )
                if not page:
                    break
                next_cursor = int(page[-1].get("cursor") or 0)
                if next_cursor <= cursor:
                    raise ResourceManagerStatusJournalError(
                        "RESOURCE_MANAGER_STATUS_CURSOR_STALLED",
                        "status journal cursor did not advance",
                    )
                for record in page:
                    recovered, previous_authority = self._head_from_record(
                        record,
                        expected_revision=revision,
                        previous_payload_digest=previous_digest,
                        previous_authority=previous_authority,
                    )
                    previous_digest = recovered.payload_digest
                    revision += 1
                cursor = next_cursor
                if len(page) < _PAGE_SIZE:
                    break
        except ResourceManagerStatusJournalError:
            raise
        except Exception as exc:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_JOURNAL_UNAVAILABLE",
                "status journal replay failed",
            ) from exc
        self._head = recovered
        self._loaded = True
        return recovered

    def _exact_record(
        self,
        *,
        event: RuntimeEvent,
        previous_payload_digest: str,
        previous_authority: tuple[str, str, int] | None,
    ) -> ResourceManagerStatusHeadV1 | None:
        try:
            record = self._event_store.get_event_by_id(event_id=str(event.event_id))
        except Exception as exc:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_JOURNAL_UNAVAILABLE",
                "status exact replay lookup failed",
            ) from exc
        if record is None:
            return None
        head, _ = self._head_from_record(
            record,
            expected_revision=event.aggregate_version,
            previous_payload_digest=previous_payload_digest,
            previous_authority=previous_authority,
        )
        try:
            validate_runtime_event_record_envelope(record, expected=event)
        except Exception as exc:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_REPLAY_CONFLICT",
                "status replay payload drifted",
            ) from exc
        return head

    def append_status(
        self,
        *,
        expected_revision: int,
        reason: str,
        observed_at_ms: int,
        payload: Mapping[str, Any],
    ) -> ResourceManagerStatusHeadV1:
        """Append one status revision or reopen the exact committed event."""

        expected = _integer(
            expected_revision,
            "expected_revision",
            minimum=0,
        )
        observed = _integer(observed_at_ms, "observed_at_ms", minimum=1)
        if not isinstance(payload, Mapping):
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_INVALID",
                "status payload must be a mapping",
            )
        current = self.recover_head()
        current_revision = current.revision if current is not None else 0
        if current is not None and current_revision == expected + 1:
            current_payload = dict(current.payload)
            replay_event = self._event(
                revision=current_revision,
                reason=reason,
                observed_at_ms=observed,
                status=payload,
                authority={
                    "runtime_scope_id": self._runtime_scope_id,
                    "runtime_owner_id": _text(
                        current_payload.get("runtime_owner_id"),
                        "runtime_owner_id",
                    ),
                    "runtime_lease_id": _text(
                        current_payload.get("runtime_lease_id"),
                        "runtime_lease_id",
                    ),
                    "fencing_token": _integer(
                        current_payload.get("fencing_token"),
                        "fencing_token",
                        minimum=1,
                    ),
                    "runtime_issued_at_ms": _integer(
                        current_payload.get("runtime_issued_at_ms"),
                        "runtime_issued_at_ms",
                    ),
                    "runtime_expires_at_ms": _integer(
                        current_payload.get("runtime_expires_at_ms"),
                        "runtime_expires_at_ms",
                        minimum=1,
                    ),
                    "runtime_released_at_ms": None,
                },
                previous_payload_digest=str(
                    current_payload.get("previous_payload_digest") or ""
                ),
            )
            exact = self._exact_record(
                event=replay_event,
                previous_payload_digest=str(
                    current_payload.get("previous_payload_digest") or ""
                ),
                previous_authority=None,
            )
            if exact is None:
                raise ResourceManagerStatusJournalError(
                    "RESOURCE_MANAGER_STATUS_DURABLE_DATA_INVALID",
                    "status head is missing its exact event",
                )
            self._head = exact
            return exact
        if current_revision != expected:
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_REVISION_CONFLICT",
                "status journal head changed",
            )
        previous_digest = current.payload_digest if current is not None else ""
        previous_authority = None
        if current is not None:
            current_payload = dict(current.payload)
            previous_authority = (
                _text(current_payload.get("runtime_owner_id"), "runtime_owner_id"),
                _text(current_payload.get("runtime_lease_id"), "runtime_lease_id"),
                _integer(
                    current_payload.get("fencing_token"),
                    "fencing_token",
                    minimum=1,
                ),
            )
        authority = self._authority(observed_at_ms=observed)
        event = self._event(
            revision=expected + 1,
            reason=reason,
            observed_at_ms=observed,
            status=payload,
            authority=authority,
            previous_payload_digest=previous_digest,
        )
        exact = self._exact_record(
            event=event,
            previous_payload_digest=previous_digest,
            previous_authority=previous_authority,
        )
        if exact is not None:
            self._head = exact
            return exact
        try:
            record = self._event_store.append_event(event)
        except Exception as exc:
            exact = self._exact_record(
                event=event,
                previous_payload_digest=previous_digest,
                previous_authority=previous_authority,
            )
            if exact is not None:
                self._head = exact
                return exact
            raise ResourceManagerStatusJournalError(
                "RESOURCE_MANAGER_STATUS_APPEND_FAILED",
                "status journal append failed",
            ) from exc
        head, _authority = self._head_from_record(
            record,
            expected_revision=event.aggregate_version,
            previous_payload_digest=previous_digest,
            previous_authority=previous_authority,
        )
        self._head = head
        return head


__all__ = ["RuntimeEventResourceManagerStatusJournal"]
