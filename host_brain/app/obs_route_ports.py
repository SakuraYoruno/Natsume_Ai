"""Application-local dependencies consumed by standalone Obs HTTP routes."""

from __future__ import annotations

import concurrent.futures
import copy
from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time
from typing import Any

from fastapi import HTTPException, status
from starlette.requests import HTTPConnection


class ObsRouteResponseCache:
    """One app-owned response cache and single-flight registry."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, Any]] = {}
        self._inflight: dict[str, concurrent.futures.Future[Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _clone(value: Any) -> Any:
        model_copy = getattr(value, "model_copy", None)
        if callable(model_copy):
            return model_copy(deep=True)
        legacy_copy = getattr(value, "copy", None)
        if callable(legacy_copy) and value.__class__.__module__.startswith("pydantic"):
            return legacy_copy(deep=True)
        return copy.deepcopy(value)

    def get_or_build(
        self,
        *,
        cache_name: str,
        cache_key: Any,
        ttl_ms: int,
        wait_timeout_s: float,
        builder: Callable[[], Any],
    ) -> Any:
        ttl = max(0, int(ttl_ms))
        slot = f"{str(cache_name).strip()}::{repr(cache_key)}"
        now = int(time.time() * 1000)
        owner = False

        with self._lock:
            cached = self._entries.get(slot)
            if (
                ttl > 0
                and isinstance(cached, dict)
                and int(cached.get("expires_at_ms") or 0) > now
            ):
                return self._clone(cached.get("value"))
            future = self._inflight.get(slot)
            if future is None:
                future = concurrent.futures.Future()
                self._inflight[slot] = future
                owner = True

        if not owner:
            value = future.result(timeout=max(0.0, float(wait_timeout_s)))
            return self._clone(value)

        try:
            value = builder()
            cloned = self._clone(value)
            if ttl > 0:
                with self._lock:
                    self._entries[slot] = {
                        "expires_at_ms": int(time.time() * 1000) + ttl,
                        "value": cloned,
                    }
            future.set_result(cloned)
            return value
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._lock:
                if self._inflight.get(slot) is future:
                    self._inflight.pop(slot, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._inflight.clear()


@dataclass(frozen=True, slots=True)
class ObsRoutePorts:
    """Narrow provider bundle owned by one Obs FastAPI application."""

    query_projection_store_provider: Callable[[], Any]
    life_action_context_provider: Callable[..., Any] | None = None
    approval_token_validator: Callable[..., Any] | None = None
    response_cache: ObsRouteResponseCache = field(
        default_factory=ObsRouteResponseCache,
    )

    def __post_init__(self) -> None:
        if not callable(self.query_projection_store_provider):
            raise TypeError("query_projection_store_provider must be callable")
        for field_name in (
            "life_action_context_provider",
            "approval_token_validator",
        ):
            provider = getattr(self, field_name)
            if provider is not None and not callable(provider):
                raise TypeError(f"{field_name} must be callable or None")
        if not isinstance(self.response_cache, ObsRouteResponseCache):
            raise TypeError("response_cache must be ObsRouteResponseCache")

    def require_query_projection_store(self) -> Any:
        store = self.query_projection_store_provider()
        if store is None or not callable(getattr(store, "get", None)):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OBS_QUERY_PROJECTION_STORE_UNAVAILABLE",
            )
        return store

def require_obs_route_ports(connection: HTTPConnection) -> ObsRoutePorts:
    ports = getattr(connection.app.state, "obs_route_ports", None)
    if not isinstance(ports, ObsRoutePorts):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OBS_ROUTE_PORTS_NOT_READY",
        )
    return ports


def require_obs_query_projection_store(connection: HTTPConnection) -> Any:
    return require_obs_route_ports(connection).require_query_projection_store()


def require_obs_projection_ledger_view(connection: HTTPConnection) -> Any:
    """Build a read-only ledger facade from this Obs application's store."""

    from host_brain.app.observability_runtime.projection_ledger_view import (
        ProjectionLedgerView,
    )

    return ProjectionLedgerView(
        require_obs_query_projection_store(connection),
        fail_closed=True,
    )


__all__ = [
    "ObsRoutePorts",
    "ObsRouteResponseCache",
    "require_obs_projection_ledger_view",
    "require_obs_query_projection_store",
    "require_obs_route_ports",
]
