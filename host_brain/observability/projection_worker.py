"""Versioned projection worker with durable rebuild and reconciliation."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from natsume_contracts.operator.v1 import ProjectionDegradedState, ProjectionDocument

from .projection_store import QueryProjectionStore, digest_json


@dataclass(frozen=True, slots=True)
class ProjectionSource:
    key: str
    payload: Mapping[str, Any]
    cursor: int
    digest: str | None = None

    def source_digest(self) -> str:
        return str(self.digest or digest_json(self.payload))


class QueryProjectionWorker:
    """Serializes refreshes per kind/key and preserves the last good read."""

    def __init__(
        self,
        store: QueryProjectionStore,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._locks_guard = threading.Lock()
        self._locks: dict[tuple[str, str], threading.Lock] = {}

    def _lock_for(self, kind: str, key: str) -> threading.Lock:
        identity = (str(kind), str(key))
        with self._locks_guard:
            return self._locks.setdefault(identity, threading.Lock())

    def get_or_refresh(
        self,
        kind: str,
        key: str,
        loader: Callable[[], ProjectionSource],
        *,
        max_age_ms: int,
        source_ref: str = "projection-worker",
    ) -> ProjectionDocument:
        current = self.store.get(kind, key)
        now = max(0, int(self._clock()))
        if (
            current is not None
            and now - current.freshness_ts_ms <= max(0, int(max_age_ms))
        ):
            return current
        with self._lock_for(kind, key):
            current = self.store.get(kind, key)
            now = max(0, int(self._clock()))
            if (
                current is not None
                and now - current.freshness_ts_ms <= max(0, int(max_age_ms))
            ):
                return current
            try:
                source = loader()
                if source.key != key:
                    raise ValueError("projection loader returned a mismatched document key")
                return self.store.upsert(
                    kind,
                    key,
                    source.payload,
                    source_cursor=source.cursor,
                    source_digest=source.source_digest(),
                    source_ref=source_ref,
                    freshness_ts_ms=now,
                )
            except Exception as exc:
                if current is None:
                    raise
                stale = self.store.mark_degraded(
                    kind,
                    key,
                    state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
                    detail=f"{type(exc).__name__}: {exc}",
                )
                assert stale is not None
                return stale

    def rebuild(
        self,
        kind: str,
        loader: Callable[[], Iterable[ProjectionSource]],
        *,
        source_ref: str = "projection-worker-rebuild",
    ) -> dict[str, Any]:
        sources = list(loader())
        return self.store.rebuild(
            kind,
            (
                (source.key, source.payload, source.cursor, source.source_digest())
                for source in sources
            ),
            source_ref=source_ref,
        )

    def reconcile(
        self,
        kind: str,
        loader: Callable[[], Iterable[ProjectionSource]],
        *,
        source_ref: str = "projection-worker-reconcile",
    ) -> dict[str, Any]:
        sources = list(loader())
        return self.store.reconcile(
            kind,
            (
                (source.key, source.payload, source.cursor, source.source_digest())
                for source in sources
            ),
            source_ref=source_ref,
        )


__all__ = ["ProjectionSource", "QueryProjectionWorker"]
