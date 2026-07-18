"""App-owned readers for authenticated Host observability source contracts."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from natsume_contracts.operator import (
    HostCapabilityForgeSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostModeSourceSnapshot,
    HostObsSourceContractError,
    HostRuntimeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
)

from host_brain.app.observability_runtime.host_read_transport import (
    HostReadTransport,
)
from host_brain.app.observability_runtime.operator_live_sources import (
    OperatorSourceSample,
    OperatorSourceUnavailable,
)
from host_brain.observability.projection_store import (
    PROJECTION_CURSOR_CONFLICT,
    QueryProjectionStore,
    QueryProjectionError,
    digest_json,
    public_projection_payload,
)


HOST_SOURCE_SCOPE = "host-runtime"
HOST_SOURCE_LINEAGE_KIND = "obs_internal_host_source_lineage"
DEFAULT_HOST_SOURCE_MAX_CLOCK_SKEW_MS = 5_000
DEFAULT_HOST_SOURCE_MAX_EVIDENCE_AGE_MS = 120_000

_Snapshot = TypeVar(
    "_Snapshot",
    HostModeSourceSnapshot,
    HostCapabilityForgeSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostRuntimeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
)


@dataclass(slots=True)
class AuthenticatedHostProjectionSources:
    """Read and validate one Host's bounded sources with an app-local cache."""

    transport: HostReadTransport
    cache_ttl_ms: int = 1_000
    lineage_store: QueryProjectionStore | None = field(default=None, repr=False)
    max_clock_skew_ms: int = DEFAULT_HOST_SOURCE_MAX_CLOCK_SKEW_MS
    max_evidence_age_ms: int = DEFAULT_HOST_SOURCE_MAX_EVIDENCE_AGE_MS
    clock_ms: Any = field(
        default=lambda: int(time.time() * 1000),
        repr=False,
    )
    _cache: dict[str, tuple[int, Any]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lineage_cache: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.transport, HostReadTransport):
            raise TypeError("transport must be HostReadTransport")
        self.cache_ttl_ms = max(0, int(self.cache_ttl_ms))
        if self.lineage_store is not None and not isinstance(
            self.lineage_store,
            QueryProjectionStore,
        ):
            raise TypeError("lineage_store must be QueryProjectionStore")
        self.max_clock_skew_ms = max(0, int(self.max_clock_skew_ms))
        self.max_evidence_age_ms = max(1, int(self.max_evidence_age_ms))
        if not callable(self.clock_ms):
            raise TypeError("clock_ms must be callable")

    def mode_status(self) -> OperatorSourceSample:
        snapshot = self._mode_snapshot()
        return OperatorSourceSample(
            payload=dict(snapshot.status),
            evidence_ts_ms=self._freshness_ts_ms(snapshot),
        )

    def mode_history(self) -> OperatorSourceSample:
        snapshot = self._mode_snapshot()
        items = [dict(item) for item in snapshot.history]
        return OperatorSourceSample(
            payload={
                "items": items,
                "total": len(items),
                "timestamp": snapshot.observed_at_ms,
            },
            evidence_ts_ms=self._freshness_ts_ms(snapshot),
        )

    def capability_forge(self) -> tuple[dict[str, Any], int]:
        snapshot = self._capability_snapshot()
        evidence_ts_ms = self._target_freshness_ts_ms(
            snapshot.evidence_ts_ms,
            reason_code="OBS_HOST_CAPABILITY_EVIDENCE_NOT_OBSERVED",
            detail="Host Capability Forge source has no bounded runtime evidence",
        )
        payload = snapshot.as_projection_payload()
        # Capability v1 retains these fields only for wire compatibility.
        # Semantic Recall and Governance jobs consume their own endpoints and
        # contracts, with independent lineage, freshness, and availability.
        payload.pop("semantic_recall", None)
        payload.pop("governance", None)
        return payload, evidence_ts_ms

    def semantic_recall(self) -> tuple[dict[str, Any], int]:
        snapshot = self._read(
            path="/internal/obs-sources/semantic-recall",
            snapshot_type=HostSemanticRecallSourceSnapshot,
            contract_reason="OBS_HOST_SEMANTIC_RECALL_SOURCE_CONTRACT_INVALID",
        )
        if snapshot.evidence_ts_ms <= 0:
            raise OperatorSourceUnavailable(
                "OBS_HOST_SEMANTIC_RECALL_EVIDENCE_NOT_OBSERVED",
                "Host semantic recall source has no bounded runtime evidence",
                source_scope=HOST_SOURCE_SCOPE,
            )
        payload = snapshot.as_projection_payload()
        if payload.get("available") is False:
            raise OperatorSourceUnavailable(
                str(
                    payload.get("reason_code")
                    or "OBS_HOST_SEMANTIC_RECALL_RUNTIME_NOT_READY"
                ),
                "Host semantic recall runtime is unavailable",
                source_scope=HOST_SOURCE_SCOPE,
            )
        return payload, self._freshness_ts_ms(snapshot)

    def governance(self) -> tuple[dict[str, Any], int]:
        snapshot = self._read(
            path="/internal/obs-sources/governance",
            snapshot_type=HostGovernanceSourceSnapshot,
            contract_reason="OBS_HOST_GOVERNANCE_SOURCE_CONTRACT_INVALID",
        )
        if snapshot.evidence_ts_ms <= 0:
            raise OperatorSourceUnavailable(
                "OBS_HOST_GOVERNANCE_EVIDENCE_NOT_OBSERVED",
                "Host governance source has no bounded runtime evidence",
                source_scope=HOST_SOURCE_SCOPE,
            )
        return snapshot.as_projection_payload(), self._freshness_ts_ms(snapshot)

    def organ_tribe(self) -> tuple[dict[str, Any], int]:
        snapshot = self._read(
            path="/internal/obs-sources/runtime",
            snapshot_type=HostRuntimeSourceSnapshot,
            contract_reason="OBS_HOST_RUNTIME_SOURCE_CONTRACT_INVALID",
        )
        if snapshot.evidence_ts_ms <= 0:
            raise OperatorSourceUnavailable(
                "OBS_HOST_ORGAN_EVIDENCE_NOT_OBSERVED",
                "Host Organ-Tribe source has no bounded runtime evidence",
                source_scope=HOST_SOURCE_SCOPE,
            )
        payload = dict(snapshot.organ_tribe)
        payload["host_scheduler"] = dict(snapshot.scheduler)
        payload["timestamp"] = snapshot.observed_at_ms
        payload["evidence_ts_ms"] = snapshot.evidence_ts_ms
        return payload, self._freshness_ts_ms(snapshot)

    def _mode_snapshot(self) -> HostModeSourceSnapshot:
        snapshot = self._read(
            path="/internal/obs-sources/mode",
            snapshot_type=HostModeSourceSnapshot,
            contract_reason="OBS_HOST_MODE_SOURCE_CONTRACT_INVALID",
        )
        if snapshot.evidence_ts_ms <= 0:
            raise OperatorSourceUnavailable(
                "OBS_HOST_MODE_EVIDENCE_NOT_OBSERVED",
                "Host mode source has no bounded runtime evidence",
                source_scope=HOST_SOURCE_SCOPE,
            )
        return snapshot

    def _capability_snapshot(self) -> HostCapabilityForgeSourceSnapshot:
        return self._read(
            path="/internal/obs-sources/capability-forge",
            snapshot_type=HostCapabilityForgeSourceSnapshot,
            contract_reason="OBS_HOST_CAPABILITY_SOURCE_CONTRACT_INVALID",
        )

    def _read(
        self,
        *,
        path: str,
        snapshot_type: type[_Snapshot],
        contract_reason: str,
    ) -> _Snapshot:
        now = max(0, int(self.clock_ms()))
        with self._lock:
            cached = self._cache.get(path)
            if cached is not None and cached[0] > now:
                snapshot = cached[1]
                if isinstance(snapshot, snapshot_type):
                    self._validate_local_clock(snapshot, local_now_ms=now)
                    return snapshot

            result = self.transport.read_json(path)
            if not result.ok:
                raise OperatorSourceUnavailable(
                    f"OBS_HOST_READ_{str(result.reason_code or 'HTTP')}",
                    "Authenticated Host read source is unavailable",
                    source_scope=HOST_SOURCE_SCOPE,
                )
            try:
                public_payload = public_projection_payload(
                    result.require_payload()
                )
                snapshot = snapshot_type.from_mapping(public_payload)
            except HostObsSourceContractError as exc:
                raise OperatorSourceUnavailable(
                    contract_reason,
                    "Authenticated Host source violated its versioned contract",
                    source_scope=HOST_SOURCE_SCOPE,
                ) from exc
            except Exception as exc:
                raise OperatorSourceUnavailable(
                    contract_reason,
                    "Authenticated Host source violated its versioned contract",
                    source_scope=HOST_SOURCE_SCOPE,
                ) from exc
            received_at_ms = max(now, max(0, int(self.clock_ms())))
            self._validate_local_clock(snapshot, local_now_ms=received_at_ms)
            self._accept_lineage(path, snapshot, local_now_ms=received_at_ms)
            self._cache[path] = (now + self.cache_ttl_ms, snapshot)
            return snapshot

    def _validate_local_clock(self, snapshot: Any, *, local_now_ms: int) -> None:
        maximum = local_now_ms + self.max_clock_skew_ms
        if (
            int(snapshot.observed_at_ms) > maximum
            or int(snapshot.evidence_ts_ms) > maximum
        ):
            raise OperatorSourceUnavailable(
                "OBS_HOST_SOURCE_CLOCK_AHEAD",
                "Authenticated Host source clock exceeds the bounded skew",
                source_scope=HOST_SOURCE_SCOPE,
            )
        evidence = int(snapshot.evidence_ts_ms)
        if evidence > 0 and local_now_ms - evidence > self.max_evidence_age_ms:
            raise OperatorSourceUnavailable(
                "OBS_HOST_SOURCE_EVIDENCE_STALE",
                "Authenticated Host source evidence exceeded its maximum age",
                source_scope=HOST_SOURCE_SCOPE,
            )

    def _freshness_ts_ms(self, snapshot: Any) -> int:
        local_now_ms = max(0, int(self.clock_ms()))
        return min(int(snapshot.evidence_ts_ms), local_now_ms)

    def _target_freshness_ts_ms(
        self,
        evidence_ts_ms: int,
        *,
        reason_code: str,
        detail: str,
    ) -> int:
        evidence = int(evidence_ts_ms or 0)
        if evidence <= 0:
            raise OperatorSourceUnavailable(
                reason_code,
                detail,
                source_scope=HOST_SOURCE_SCOPE,
            )
        local_now_ms = max(0, int(self.clock_ms()))
        if evidence > local_now_ms + self.max_clock_skew_ms:
            raise OperatorSourceUnavailable(
                "OBS_HOST_SOURCE_CLOCK_AHEAD",
                "Authenticated Host source clock exceeds the bounded skew",
                source_scope=HOST_SOURCE_SCOPE,
            )
        if local_now_ms - evidence > self.max_evidence_age_ms:
            raise OperatorSourceUnavailable(
                "OBS_HOST_SOURCE_EVIDENCE_STALE",
                "Authenticated Host source evidence exceeded its maximum age",
                source_scope=HOST_SOURCE_SCOPE,
            )
        return min(evidence, local_now_ms)

    def _accept_lineage(
        self,
        path: str,
        snapshot: Any,
        *,
        local_now_ms: int,
    ) -> None:
        incoming = {
            "schema_version": "Natsume.obs.host_source_lineage.v1",
            "path": path,
            "source_instance_id": str(snapshot.source_instance_id),
            "source_revision": int(snapshot.source_revision),
            "observed_at_ms": int(snapshot.observed_at_ms),
            "evidence_ts_ms": int(snapshot.evidence_ts_ms),
            "snapshot_digest": digest_json(snapshot.as_dict()),
            "retired_instance_ids": [],
        }
        try:
            if self.lineage_store is None:
                current = self._lineage_cache.get(path)
                historical_instances = {
                    str(item)
                    for item in list(
                        (current or {}).get("retired_instance_ids") or []
                    )
                    if str(item).strip()
                }
                if current is not None:
                    self._validate_lineage_transition(
                        current,
                        incoming,
                        historical_instances=historical_instances,
                    )
                    current_instance = str(
                        current.get("source_instance_id") or ""
                    )
                    if incoming["source_instance_id"] == current_instance:
                        incoming["retired_instance_ids"] = sorted(
                            historical_instances
                        )
                    else:
                        incoming["retired_instance_ids"] = sorted(
                            {*historical_instances, current_instance}
                        )
                elif incoming["source_instance_id"] in historical_instances:
                    self._raise_instance_replay()
                self._lineage_cache[path] = dict(incoming)
                return

            # Multiple Obs applications can share this SQLite store.  The
            # current read, transition validation, and commit therefore use
            # an optimistic CAS retry rather than relying on this instance's
            # process-local lock.
            for _attempt in range(8):
                current_document = self.lineage_store.get(
                    HOST_SOURCE_LINEAGE_KIND,
                    path,
                )
                historical_instances = self.lineage_store.journal_text_values(
                    HOST_SOURCE_LINEAGE_KIND,
                    path,
                    "source_instance_id",
                )
                current = (
                    dict(current_document.payload)
                    if current_document is not None
                    else None
                )
                if current is None:
                    if incoming["source_instance_id"] in historical_instances:
                        self._raise_instance_replay()
                    candidate = dict(incoming)
                else:
                    self._validate_lineage_transition(
                        current,
                        incoming,
                        historical_instances=historical_instances,
                    )
                    candidate = dict(incoming)
                    if (
                        incoming["source_instance_id"]
                        == str(current.get("source_instance_id") or "")
                    ):
                        # Preserve an old payload's compatibility field while
                        # journal history, not this field, remains authoritative.
                        candidate["retired_instance_ids"] = list(
                            current.get("retired_instance_ids") or []
                        )
                current_cursor = int(
                    getattr(current_document, "source_cursor", 0) or 0
                )
                source_cursor = (
                    current_cursor
                    if current is not None
                    and digest_json(current) == digest_json(candidate)
                    else max(local_now_ms, current_cursor + 1)
                )
                try:
                    self.lineage_store.compare_and_upsert(
                        HOST_SOURCE_LINEAGE_KIND,
                        path,
                        candidate,
                        expected_current=current_document,
                        source_cursor=source_cursor,
                        source_ref=(
                            "obs-app://authenticated-host-source-lineage"
                        ),
                        freshness_ts_ms=min(
                            int(snapshot.evidence_ts_ms),
                            local_now_ms,
                        ),
                    )
                    return
                except QueryProjectionError as exc:
                    if exc.reason_code != PROJECTION_CURSOR_CONFLICT:
                        raise
            raise QueryProjectionError(
                PROJECTION_CURSOR_CONFLICT,
                "Host source lineage transition remained contended",
            )
        except OperatorSourceUnavailable:
            raise
        except Exception as exc:
            raise OperatorSourceUnavailable(
                "OBS_HOST_SOURCE_LINEAGE_UNAVAILABLE",
                "Authenticated Host source lineage could not be verified",
                source_scope=HOST_SOURCE_SCOPE,
            ) from exc

    @staticmethod
    def _validate_lineage_transition(
        current: dict[str, Any],
        incoming: dict[str, Any],
        *,
        historical_instances: frozenset[str] | set[str],
    ) -> None:
        current_instance = str(current.get("source_instance_id") or "")
        incoming_instance = str(incoming.get("source_instance_id") or "")
        if incoming_instance == current_instance:
            current_revision = int(current.get("source_revision") or 0)
            incoming_revision = int(incoming.get("source_revision") or 0)
            if incoming_revision < current_revision:
                raise OperatorSourceUnavailable(
                    "OBS_HOST_SOURCE_REVISION_ROLLBACK",
                    "Authenticated Host source revision regressed",
                    source_scope=HOST_SOURCE_SCOPE,
                )
            if (
                incoming_revision == current_revision
                and str(incoming.get("snapshot_digest") or "")
                != str(current.get("snapshot_digest") or "")
            ):
                raise OperatorSourceUnavailable(
                    "OBS_HOST_SOURCE_REVISION_CONFLICT",
                    "Authenticated Host source revision changed its payload",
                    source_scope=HOST_SOURCE_SCOPE,
                )
            if (
                int(incoming.get("observed_at_ms") or 0)
                < int(current.get("observed_at_ms") or 0)
                or int(incoming.get("evidence_ts_ms") or 0)
                < int(current.get("evidence_ts_ms") or 0)
            ):
                raise OperatorSourceUnavailable(
                    "OBS_HOST_SOURCE_EVIDENCE_ROLLBACK",
                    "Authenticated Host source evidence regressed",
                    source_scope=HOST_SOURCE_SCOPE,
                )
            return
        if incoming_instance in historical_instances:
            AuthenticatedHostProjectionSources._raise_instance_replay()

    @staticmethod
    def _raise_instance_replay() -> None:
        raise OperatorSourceUnavailable(
            "OBS_HOST_SOURCE_INSTANCE_REPLAY",
            "A retired Host source instance was replayed",
            source_scope=HOST_SOURCE_SCOPE,
        )


__all__ = [
    "AuthenticatedHostProjectionSources",
    "DEFAULT_HOST_SOURCE_MAX_CLOCK_SKEW_MS",
    "DEFAULT_HOST_SOURCE_MAX_EVIDENCE_AGE_MS",
    "HOST_SOURCE_SCOPE",
    "HOST_SOURCE_LINEAGE_KIND",
]
