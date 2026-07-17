"""Explicit, app-owned readers for the operator live-state projection.

This module contains no Host service locator and owns no module-level mutable
cache.  One standalone Obs application composes one reader/refresher instance
and may therefore be started beside another Obs application without sharing
Edge health, throttling, or source state.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.app.observability_runtime.live_source_projection import (
    GATEWAY_STATS_SOURCE_KEY,
    GATEWAY_STATS_SOURCE_KIND,
    PROACTIVE_AUTONOMY_DEFAULT_KEY,
    PROACTIVE_AUTONOMY_KIND,
    SYS_MODE_HISTORY_KEY,
    SYS_MODE_HISTORY_KIND,
    SYS_MODE_STATUS_KEY,
    SYS_MODE_STATUS_KIND,
)
from host_brain.observability.projection_store import QueryProjectionStore, digest_json


EDGE_GATEWAY_AUTH_FAILED = "OBS_EDGE_GATEWAY_AUTH_FAILED"
EDGE_GATEWAY_AUTH_CONFIGURATION_FAILED = (
    "OBS_EDGE_GATEWAY_AUTH_CONFIGURATION_FAILED"
)
EDGE_GATEWAY_TIMEOUT = "OBS_EDGE_GATEWAY_TIMEOUT"
EDGE_GATEWAY_UNAVAILABLE = "OBS_EDGE_GATEWAY_UNAVAILABLE"
EDGE_GATEWAY_INVALID_RESPONSE = "OBS_EDGE_GATEWAY_INVALID_RESPONSE"
EDGE_GATEWAY_ENDPOINT_NOT_CONFIGURED = "OBS_EDGE_GATEWAY_ENDPOINT_NOT_CONFIGURED"

GATEWAY_SOURCE_PORT_NOT_INJECTED = "OBS_EDGE_GATEWAY_SOURCE_PORT_NOT_INJECTED"
LIFE_SNAPSHOT_PORT_NOT_INJECTED = "OBS_LIFE_SNAPSHOT_PORT_NOT_INJECTED"
SYS_MODE_STATUS_PORT_NOT_INJECTED = "OBS_SYS_MODE_STATUS_PORT_NOT_INJECTED"
SYS_MODE_HISTORY_PORT_NOT_INJECTED = "OBS_SYS_MODE_HISTORY_PORT_NOT_INJECTED"


class OperatorSourceUnavailable(RuntimeError):
    """Typed read failure which is safe to materialize as source unavailable."""

    def __init__(self, reason_code: str, detail: str, *, source_scope: str) -> None:
        self.reason_code = str(reason_code or "OPERATOR_SOURCE_UNAVAILABLE").strip()
        self.detail = str(detail or self.reason_code).strip()[:500]
        self.source_scope = str(source_scope or "external-runtime").strip()
        super().__init__(f"{self.reason_code}: {self.detail}")


class OperatorProjectionWriteFailed(RuntimeError):
    """Stable job-level failure raised when any target cannot be persisted."""

    reason_code = "OBS_OPERATOR_PROJECTION_WRITE_FAILED"

    def __init__(self, targets: Sequence[str]) -> None:
        self.targets = tuple(sorted({str(target) for target in targets if str(target)}))
        super().__init__(
            f"{self.reason_code}: target_count={len(self.targets)}"
        )


@dataclass(frozen=True, slots=True)
class GatewayHttpResponse:
    status_code: int
    payload: Mapping[str, Any] | None


class GatewayHttpTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> GatewayHttpResponse: ...


class HttpxGatewayHealthTransport:
    """Small synchronous HTTP adapter used only by a projection worker."""

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_s: float,
    ) -> GatewayHttpResponse:
        try:
            import httpx

            response = httpx.get(
                url,
                headers=dict(headers),
                timeout=max(0.1, float(timeout_s)),
                trust_env=False,
            )
        except ImportError as exc:
            raise OperatorSourceUnavailable(
                EDGE_GATEWAY_UNAVAILABLE,
                "Edge gateway HTTP client is unavailable",
                source_scope="edge-runtime",
            ) from exc
        except httpx.TimeoutException as exc:
            raise OperatorSourceUnavailable(
                EDGE_GATEWAY_TIMEOUT,
                "Edge gateway health request timed out",
                source_scope="edge-runtime",
            ) from exc
        except httpx.HTTPError as exc:
            raise OperatorSourceUnavailable(
                EDGE_GATEWAY_UNAVAILABLE,
                "Edge gateway health transport failed",
                source_scope="edge-runtime",
            ) from exc

        try:
            payload = response.json()
        except ValueError:
            payload = None
        return GatewayHttpResponse(
            status_code=int(response.status_code),
            payload=payload if isinstance(payload, Mapping) else None,
        )


def _normalize_base_url(value: str) -> str:
    resolved = str(value or "").strip().rstrip("/")
    if not resolved:
        return ""
    if not resolved.startswith(("http://", "https://")):
        raise ValueError("gateway base URL must use http or https")
    return resolved


def _public_gateway_health(
    *,
    base_url: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only the public health dimensions consumed by operator routes."""

    source = dict(payload)
    adapters = source.get("adapters")
    public_adapters: dict[str, Any] = {}
    if isinstance(adapters, Mapping):
        registered_count = adapters.get("registered_count")
        if registered_count is not None:
            try:
                public_adapters["registered_count"] = max(0, int(registered_count))
            except (TypeError, ValueError):
                pass
        for platform, raw in adapters.items():
            if not isinstance(raw, Mapping):
                continue
            last_outbound = raw.get("last_outbound")
            public_adapters[str(platform)] = {
                "status": str(raw.get("status") or "unknown"),
                "bridge_backend": str(raw.get("bridge_backend") or ""),
                "last_outbound": {
                    "ts_ms": max(0, int(last_outbound.get("ts_ms") or 0)),
                }
                if isinstance(last_outbound, Mapping)
                else {},
            }

    transport = source.get("transport")
    public_transport: dict[str, Any] = {}
    if isinstance(transport, Mapping):
        for name in (
            "mode",
            "intake_enabled",
            "verification_enabled",
            "delivery_enabled",
            "worker_alive",
        ):
            if name in transport:
                public_transport[name] = transport.get(name)

    return {
        "base_url": base_url,
        "body": {
            "status": str(source.get("status") or "unknown"),
            "uptime_s": source.get("uptime_s"),
            "adapters": public_adapters,
            "outbound_ready": bool(source.get("outbound_ready")),
            "transport": public_transport,
        },
    }


def _is_valid_gateway_health(payload: Mapping[str, Any]) -> bool:
    status = payload.get("status")
    if not isinstance(status, str) or not status.strip():
        return False
    return any(
        name in payload
        for name in ("uptime_s", "outbound_ready", "adapters", "transport")
    )


@dataclass(slots=True)
class AppOwnedGatewayHealthReader:
    """Edge health reader whose cache and single-flight lock belong to one app."""

    base_urls: Sequence[str]
    transport: GatewayHttpTransport = field(default_factory=HttpxGatewayHealthTransport)
    auth_headers_provider: Callable[[], Mapping[str, str]] = field(
        default=lambda: {},
    )
    timeout_s: float = 3.0
    cache_ttl_ms: int = 3_000
    clock_ms: Callable[[], int] = field(
        default=lambda: int(time.time() * 1000),
        repr=False,
    )
    _cached: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _cache_expires_at_ms: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        normalized: list[str] = []
        for value in self.base_urls:
            base_url = _normalize_base_url(str(value))
            if base_url and base_url not in normalized:
                normalized.append(base_url)
        self.base_urls = tuple(normalized)
        self.timeout_s = max(0.1, min(float(self.timeout_s), 30.0))
        self.cache_ttl_ms = max(0, int(self.cache_ttl_ms))
        if not callable(self.auth_headers_provider):
            raise TypeError("auth_headers_provider must be callable")
        if not callable(self.clock_ms):
            raise TypeError("clock_ms must be callable")

    def read(self) -> dict[str, Any]:
        now = max(0, int(self.clock_ms()))
        with self._lock:
            if self._cached is not None and self._cache_expires_at_ms > now:
                return copy.deepcopy(self._cached)
            if not self.base_urls:
                raise OperatorSourceUnavailable(
                    EDGE_GATEWAY_ENDPOINT_NOT_CONFIGURED,
                    "No Edge gateway health endpoint was injected",
                    source_scope="edge-runtime",
                )

            try:
                headers = {
                    str(name): str(value)
                    for name, value in dict(
                        self.auth_headers_provider() or {}
                    ).items()
                    if str(name).strip() and str(value).strip()
                }
            except Exception as exc:
                raise OperatorSourceUnavailable(
                    EDGE_GATEWAY_AUTH_CONFIGURATION_FAILED,
                    "Edge gateway health-read authentication is unavailable",
                    source_scope="edge-runtime",
                ) from exc
            failures: list[OperatorSourceUnavailable] = []
            for base_url in self.base_urls:
                try:
                    response = self.transport.get_json(
                        f"{base_url}/v1/gateway/health",
                        headers=headers,
                        timeout_s=self.timeout_s,
                    )
                except OperatorSourceUnavailable as exc:
                    failures.append(exc)
                    continue
                except TimeoutError:
                    failures.append(
                        OperatorSourceUnavailable(
                            EDGE_GATEWAY_TIMEOUT,
                            "Edge gateway health request timed out",
                            source_scope="edge-runtime",
                        )
                    )
                    continue
                except Exception:
                    failures.append(
                        OperatorSourceUnavailable(
                            EDGE_GATEWAY_UNAVAILABLE,
                            "Edge gateway health transport failed",
                            source_scope="edge-runtime",
                        )
                    )
                    continue

                if int(response.status_code) in {401, 403}:
                    failures.append(
                        OperatorSourceUnavailable(
                            EDGE_GATEWAY_AUTH_FAILED,
                            "Edge gateway rejected health-read authentication",
                            source_scope="edge-runtime",
                        )
                    )
                    continue
                if int(response.status_code) != 200:
                    failures.append(
                        OperatorSourceUnavailable(
                            EDGE_GATEWAY_UNAVAILABLE,
                            f"Edge gateway health returned HTTP {int(response.status_code)}",
                            source_scope="edge-runtime",
                        )
                    )
                    continue
                if not isinstance(response.payload, Mapping) or not _is_valid_gateway_health(
                    response.payload
                ):
                    failures.append(
                        OperatorSourceUnavailable(
                            EDGE_GATEWAY_INVALID_RESPONSE,
                            "Edge gateway health returned an invalid payload",
                            source_scope="edge-runtime",
                        )
                    )
                    continue

                snapshot = _public_gateway_health(
                    base_url=base_url,
                    payload=response.payload,
                )
                self._cached = copy.deepcopy(snapshot)
                self._cache_expires_at_ms = now + self.cache_ttl_ms
                return snapshot

            if failures:
                auth_failure = next(
                    (
                        failure
                        for failure in failures
                        if failure.reason_code == EDGE_GATEWAY_AUTH_FAILED
                    ),
                    None,
                )
                raise auth_failure or failures[0]
            raise OperatorSourceUnavailable(
                EDGE_GATEWAY_UNAVAILABLE,
                "Edge gateway health is unavailable",
                source_scope="edge-runtime",
            )


@dataclass(frozen=True, slots=True)
class AppOwnedGatewaySourceReader:
    """Combine explicit durable activity rows with one app-owned Edge reader."""

    health_reader: AppOwnedGatewayHealthReader
    activity_reader: Callable[[int, int], Mapping[str, Any]]
    runtime_config_reader: Callable[[], Mapping[str, Any]] = field(
        default=lambda: {},
    )
    window_minutes: int = 10_080
    clock_ms: Callable[[], int] = field(
        default=lambda: int(time.time() * 1000),
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.health_reader, AppOwnedGatewayHealthReader):
            raise TypeError("health_reader must be AppOwnedGatewayHealthReader")
        if not callable(self.activity_reader):
            raise TypeError("activity_reader must be callable")
        if not callable(self.runtime_config_reader):
            raise TypeError("runtime_config_reader must be callable")
        if not callable(self.clock_ms):
            raise TypeError("clock_ms must be callable")
        object.__setattr__(self, "window_minutes", max(1, int(self.window_minutes)))

    def read(self) -> dict[str, Any]:
        now = max(0, int(self.clock_ms()))
        start_ms = max(0, now - self.window_minutes * 60 * 1000)
        try:
            activity = self.activity_reader(start_ms, now)
        except OperatorSourceUnavailable:
            raise
        except Exception as exc:
            raise OperatorSourceUnavailable(
                "OBS_GATEWAY_ACTIVITY_READ_FAILED",
                "Canonical gateway activity reader failed",
                source_scope="canonical-ledger",
            ) from exc
        if not isinstance(activity, Mapping):
            raise OperatorSourceUnavailable(
                "OBS_GATEWAY_ACTIVITY_INVALID_RESPONSE",
                "Gateway activity reader returned a non-object payload",
                source_scope="canonical-ledger",
            )
        channel_rows = [
            dict(row)
            for row in list(activity.get("channel_rows") or [])
            if isinstance(row, Mapping)
        ]
        voice_rows = [
            _public_gateway_voice_row(row)
            for row in list(activity.get("voice_rows") or [])
            if isinstance(row, Mapping)
        ]
        return {
            "generated_at_ms": now,
            "channel_rows": channel_rows,
            "voice_rows": voice_rows,
            "remote_gateway_health": self.health_reader.read(),
            "runtime_config": dict(self.runtime_config_reader() or {}),
        }


def _public_gateway_voice_row(value: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(value)
    if isinstance(row.get("payload"), Mapping):
        return row
    row["payload"] = {
        name: row.get(name)
        for name in ("platform", "source", "phase", "latency_ms")
        if row.get(name) is not None
    }
    return row


@dataclass(frozen=True, slots=True)
class OperatorLiveSourcePorts:
    """All four source ports needed by the operator-live-state projection."""

    gateway_source_reader: Any | None
    life_snapshot_reader: Any | None
    sys_mode_status_reader: Callable[[], Mapping[str, Any]] | None = None
    sys_mode_history_reader: Callable[[], Any] | None = None
    person_id: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "person_id",
            str(self.person_id or "default").strip() or "default",
        )
        if self.sys_mode_status_reader is not None and not callable(
            self.sys_mode_status_reader
        ):
            raise TypeError("sys_mode_status_reader must be callable")
        if self.sys_mode_history_reader is not None and not callable(
            self.sys_mode_history_reader
        ):
            raise TypeError("sys_mode_history_reader must be callable")


@dataclass(frozen=True, slots=True)
class _Target:
    name: str
    kind: str
    key: str
    source_scope: str
    reader: Callable[[], Mapping[str, Any]]


@dataclass(slots=True)
class OperatorLiveStateProjectionRefresher:
    """Refresh four documents independently; one source cannot abort siblings."""

    store: QueryProjectionStore
    ports: OperatorLiveSourcePorts
    clock_ms: Callable[[], int] = field(
        default=lambda: int(time.time() * 1000),
        repr=False,
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.store is None or not callable(getattr(self.store, "upsert", None)):
            raise TypeError("store must provide upsert")
        if not isinstance(self.ports, OperatorLiveSourcePorts):
            raise TypeError("ports must be OperatorLiveSourcePorts")
        if not callable(self.clock_ms):
            raise TypeError("clock_ms must be callable")

    def refresh(self) -> dict[str, Any]:
        outcomes: dict[str, dict[str, Any]] = {}
        with self._lock:
            for target in self._targets():
                try:
                    payload = target.reader()
                    if not isinstance(payload, Mapping):
                        raise OperatorSourceUnavailable(
                            f"OBS_{target.name.upper().replace('-', '_')}_INVALID_RESPONSE",
                            f"{target.name} reader returned a non-object payload",
                            source_scope=target.source_scope,
                        )
                except OperatorSourceUnavailable as exc:
                    outcomes[target.name] = self._materialize_unavailable(target, exc)
                except Exception as exc:
                    failure = OperatorSourceUnavailable(
                        f"OBS_{target.name.upper().replace('-', '_')}_READ_FAILED",
                        f"{target.name} reader failed ({type(exc).__name__})",
                        source_scope=target.source_scope,
                    )
                    outcomes[target.name] = self._materialize_unavailable(
                        target,
                        failure,
                    )
                else:
                    try:
                        self._upsert_healthy(target, payload)
                        outcomes[target.name] = {
                            "status": "HEALTHY",
                            "reason_code": None,
                        }
                    except Exception as write_exc:
                        outcomes[target.name] = {
                            "status": "WRITE_FAILED",
                            "reason_code": type(write_exc).__name__,
                        }

        result = {
            "target_count": len(outcomes),
            "healthy_count": sum(
                1 for result in outcomes.values() if result["status"] == "HEALTHY"
            ),
            "unavailable_count": sum(
                1
                for result in outcomes.values()
                if result["status"] == "SOURCE_UNAVAILABLE"
            ),
            "write_failed_count": sum(
                1
                for result in outcomes.values()
                if result["status"] == "WRITE_FAILED"
            ),
            "targets": outcomes,
        }
        failed_targets = [
            name
            for name, outcome in outcomes.items()
            if outcome["status"] == "WRITE_FAILED"
        ]
        if failed_targets:
            raise OperatorProjectionWriteFailed(failed_targets)
        return result

    def _materialize_unavailable(
        self,
        target: _Target,
        failure: OperatorSourceUnavailable,
    ) -> dict[str, Any]:
        try:
            changed = self._upsert_unavailable(target, failure)
            return {
                "status": "SOURCE_UNAVAILABLE",
                "reason_code": failure.reason_code,
                "changed": changed,
            }
        except Exception as write_exc:
            return {
                "status": "WRITE_FAILED",
                "reason_code": type(write_exc).__name__,
            }

    def _targets(self) -> tuple[_Target, ...]:
        return (
            _Target(
                "gateway",
                GATEWAY_STATS_SOURCE_KIND,
                GATEWAY_STATS_SOURCE_KEY,
                "edge-runtime",
                self._read_gateway,
            ),
            _Target(
                "sys-mode-status",
                SYS_MODE_STATUS_KIND,
                SYS_MODE_STATUS_KEY,
                "host-runtime",
                self._read_sys_mode_status,
            ),
            _Target(
                "sys-mode-history",
                SYS_MODE_HISTORY_KIND,
                SYS_MODE_HISTORY_KEY,
                "host-runtime",
                self._read_sys_mode_history,
            ),
            _Target(
                "proactive-autonomy",
                PROACTIVE_AUTONOMY_KIND,
                PROACTIVE_AUTONOMY_DEFAULT_KEY,
                "canonical-life-ledger",
                self._read_proactive,
            ),
        )

    def _read_gateway(self) -> Mapping[str, Any]:
        reader = self.ports.gateway_source_reader
        if reader is None:
            raise OperatorSourceUnavailable(
                GATEWAY_SOURCE_PORT_NOT_INJECTED,
                "Edge gateway source reader was not injected",
                source_scope="edge-runtime",
            )
        call = getattr(reader, "read", None)
        if not callable(call) and callable(reader):
            call = reader
        if not callable(call):
            raise OperatorSourceUnavailable(
                GATEWAY_SOURCE_PORT_NOT_INJECTED,
                "Edge gateway source reader is invalid",
                source_scope="edge-runtime",
            )
        return call()

    def _read_sys_mode_status(self) -> Mapping[str, Any]:
        reader = self.ports.sys_mode_status_reader
        if reader is None:
            raise OperatorSourceUnavailable(
                SYS_MODE_STATUS_PORT_NOT_INJECTED,
                "Host sys-mode status reader was not injected",
                source_scope="host-runtime",
            )
        payload = reader()
        if not isinstance(payload, Mapping):
            raise OperatorSourceUnavailable(
                "OBS_SYS_MODE_STATUS_INVALID_RESPONSE",
                "Host sys-mode status reader returned a non-object payload",
                source_scope="host-runtime",
            )
        return dict(payload)

    def _read_sys_mode_history(self) -> Mapping[str, Any]:
        reader = self.ports.sys_mode_history_reader
        if reader is None:
            raise OperatorSourceUnavailable(
                SYS_MODE_HISTORY_PORT_NOT_INJECTED,
                "Host sys-mode history reader was not injected",
                source_scope="host-runtime",
            )
        raw = reader()
        if isinstance(raw, Mapping):
            rows = raw.get("items") or []
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            rows = raw
        else:
            raise OperatorSourceUnavailable(
                "OBS_SYS_MODE_HISTORY_INVALID_RESPONSE",
                "Host sys-mode history reader returned an invalid payload",
                source_scope="host-runtime",
            )
        items = [dict(item) for item in list(rows)[:100] if isinstance(item, Mapping)]
        return {
            "items": items,
            "total": len(items),
            "timestamp": max(0, int(self.clock_ms())),
        }

    def _read_proactive(self) -> Mapping[str, Any]:
        reader = self.ports.life_snapshot_reader
        if reader is None:
            raise OperatorSourceUnavailable(
                LIFE_SNAPSHOT_PORT_NOT_INJECTED,
                "Durable Life snapshot reader was not injected",
                source_scope="canonical-life-ledger",
            )
        if not callable(getattr(reader, "get_snapshot", None)):
            if callable(reader):
                reader = _CallableLifeSnapshotReader(reader)
            else:
                raise OperatorSourceUnavailable(
                    LIFE_SNAPSHOT_PORT_NOT_INJECTED,
                    "Durable Life snapshot reader is invalid",
                    source_scope="canonical-life-ledger",
                )

        from host_brain.app.obs_routes_proactive_autonomy import (
            build_proactive_autonomy_snapshot,
        )

        payload = build_proactive_autonomy_snapshot(
            None,
            self.ports.person_id,
            life_snapshot_reader=reader,
        )
        if payload.get("error"):
            raise OperatorSourceUnavailable(
                "OBS_LIFE_SNAPSHOT_READ_FAILED",
                "Durable Life snapshot reader failed",
                source_scope="canonical-life-ledger",
            )
        if payload.get("available") is not True:
            raise OperatorSourceUnavailable(
                "OBS_LIFE_SNAPSHOT_NOT_FOUND",
                "Canonical Life snapshot is not available",
                source_scope="canonical-life-ledger",
            )
        return payload

    def _next_cursor(self, target: _Target, now: int) -> int:
        current = self.store.get(target.kind, target.key)
        return max(
            now,
            int(getattr(current, "source_cursor", 0) or 0) + 1
            if current is not None
            else now,
        )

    def _upsert_healthy(self, target: _Target, payload: Mapping[str, Any]) -> None:
        now = max(0, int(self.clock_ms()))
        self.store.upsert(
            target.kind,
            target.key,
            dict(payload),
            source_cursor=self._next_cursor(target, now),
            source_digest=digest_json(payload),
            source_ref=f"obs-app://operator-live-state/{target.name}",
            degraded_state=ProjectionDegradedState.HEALTHY,
            degraded_detail=None,
            freshness_ts_ms=now,
        )

    def _upsert_unavailable(
        self,
        target: _Target,
        failure: OperatorSourceUnavailable,
    ) -> bool:
        current = self.store.get(target.kind, target.key)
        if current is not None:
            state = str(
                getattr(
                    getattr(current, "degraded_state", ""),
                    "value",
                    getattr(current, "degraded_state", ""),
                )
                or ""
            ).upper()
            current_payload = dict(getattr(current, "payload", {}) or {})
            if (
                state == ProjectionDegradedState.SOURCE_UNAVAILABLE.value
                and current_payload.get("available") is False
                and current_payload.get("reason_code") == failure.reason_code
                and current_payload.get("source_scope") == failure.source_scope
            ):
                return False

        now = max(0, int(self.clock_ms()))
        payload = {
            "schema_version": "Natsume.operator.projection_source_unavailable.v1",
            "available": False,
            "job_name": "operator-live-state",
            "projection_kind": target.kind,
            "projection_key": target.key,
            "reason_code": failure.reason_code,
            "source_scope": failure.source_scope,
            "timestamp": now,
        }
        self.store.upsert(
            target.kind,
            target.key,
            payload,
            source_cursor=self._next_cursor(target, now),
            source_digest=digest_json(payload),
            source_ref=f"obs-app://operator-live-state/{target.name}/source-unavailable",
            degraded_state=ProjectionDegradedState.SOURCE_UNAVAILABLE,
            degraded_detail=failure.detail,
            freshness_ts_ms=now,
        )
        return True


@dataclass(frozen=True, slots=True)
class _CallableLifeSnapshotReader:
    callback: Callable[[str], Any]

    def get_snapshot(self, person_id: str) -> Any:
        return self.callback(person_id)


def build_operator_live_state_callback(
    *,
    store: QueryProjectionStore,
    ports: OperatorLiveSourcePorts,
    clock_ms: Callable[[], int] | None = None,
) -> Callable[[], dict[str, Any]]:
    """Return one app-owned bound callback suitable for ProjectionJobDaemon."""

    refresher = OperatorLiveStateProjectionRefresher(
        store=store,
        ports=ports,
        **({"clock_ms": clock_ms} if clock_ms is not None else {}),
    )
    return refresher.refresh


__all__ = [
    "AppOwnedGatewayHealthReader",
    "AppOwnedGatewaySourceReader",
    "EDGE_GATEWAY_AUTH_FAILED",
    "EDGE_GATEWAY_AUTH_CONFIGURATION_FAILED",
    "EDGE_GATEWAY_ENDPOINT_NOT_CONFIGURED",
    "EDGE_GATEWAY_INVALID_RESPONSE",
    "EDGE_GATEWAY_TIMEOUT",
    "EDGE_GATEWAY_UNAVAILABLE",
    "GATEWAY_SOURCE_PORT_NOT_INJECTED",
    "GatewayHttpResponse",
    "GatewayHttpTransport",
    "HttpxGatewayHealthTransport",
    "LIFE_SNAPSHOT_PORT_NOT_INJECTED",
    "OperatorLiveSourcePorts",
    "OperatorLiveStateProjectionRefresher",
    "OperatorProjectionWriteFailed",
    "OperatorSourceUnavailable",
    "SYS_MODE_HISTORY_PORT_NOT_INJECTED",
    "SYS_MODE_STATUS_PORT_NOT_INJECTED",
    "build_operator_live_state_callback",
]
