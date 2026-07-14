"""Authenticated, fail-closed health evidence for the MateEngine bridge."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import http.client
import json
import math
import re
import secrets
import time
from typing import Any

from natsume_contracts.bridge_auth import build_http_auth_headers

from .models import EndpointKind, EndpointSpec, Transport
from .probes import HealthProbeResult


MATE_BRIDGE_OWNER_ID = "host.mateengine.bridge"
MATE_BRIDGE_HTTP_ENDPOINT_ID = "host.mateengine.bridge.http"
MATE_BRIDGE_UDP_ENDPOINT_ID = "host.mateengine.bridge.udp"
MATE_BRIDGE_HTTP_PROTOCOL_ID = "http"
MATE_BRIDGE_UDP_PROTOCOL_ID = "mateengine-udp"
MATE_BRIDGE_PROTOCOL_VERSION = "1"
MATE_BRIDGE_HEALTH_PATH = "/health"
MATE_BRIDGE_HEALTH_SCOPE = "bridge:mateengine:read"

MATE_BRIDGE_HEALTH_CONFIG_INVALID = "MATE_BRIDGE_HEALTH_CONFIG_INVALID"
MATE_BRIDGE_HEALTH_ENDPOINT_INVALID = "MATE_BRIDGE_HEALTH_ENDPOINT_INVALID"
MATE_BRIDGE_HEALTH_REQUEST_FAILED = "MATE_BRIDGE_HEALTH_REQUEST_FAILED"
MATE_BRIDGE_HEALTH_RESPONSE_INVALID = "MATE_BRIDGE_HEALTH_RESPONSE_INVALID"
MATE_BRIDGE_HEALTH_HTTP_IDENTITY_MISMATCH = (
    "MATE_BRIDGE_HEALTH_HTTP_IDENTITY_MISMATCH"
)
MATE_BRIDGE_HEALTH_UDP_IDENTITY_MISMATCH = (
    "MATE_BRIDGE_HEALTH_UDP_IDENTITY_MISMATCH"
)
MATE_BRIDGE_HEALTH_OK = "MATE_BRIDGE_HEALTH_OK"

_IDENTIFIER_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}\Z")
_HOST_RE = re.compile(r"\A[A-Za-z0-9.*:_-]{1,255}\Z")
_MIN_SECRET_BYTES = 32
_MAX_SECRET_BYTES = 4096
_MAX_RESPONSE_LIMIT = 1024 * 1024

ConnectionFactory = Callable[[str, int, float], Any]


class MateBridgeHealthProbeConfigurationError(ValueError):
    reason_code = MATE_BRIDGE_HEALTH_CONFIG_INVALID

    def __init__(self) -> None:
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class MateBridgeHealthProbeConfig:
    """Explicit HTTP target and bounded client policy for one worker."""

    http_host: str
    http_port: int
    key_id: str
    timeout_seconds: float = 1.0
    max_response_bytes: int = 65_536

    def __post_init__(self) -> None:
        try:
            host = _normalize_host(self.http_host)
            key_id = _identifier(self.key_id)
            port = self.http_port
            timeout = self.timeout_seconds
            response_limit = self.max_response_bytes
            if type(port) is not int or not (1 <= port <= 65_535):
                raise ValueError
            if (
                type(timeout) not in {int, float}
                or not math.isfinite(float(timeout))
                or not (0.0 < float(timeout) <= 30.0)
            ):
                raise ValueError
            if (
                type(response_limit) is not int
                or not (1 <= response_limit <= _MAX_RESPONSE_LIMIT)
            ):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise MateBridgeHealthProbeConfigurationError() from exc
        object.__setattr__(self, "http_host", host)
        object.__setattr__(self, "key_id", key_id)
        object.__setattr__(self, "timeout_seconds", float(timeout))


def build_mate_bridge_health_url(host: str, port: int) -> str:
    """Build the exact HTTP health target used by an indirect UDP claim."""

    try:
        normalized_host = _normalize_host(host)
        if type(port) is not int or not (1 <= port <= 65_535):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise MateBridgeHealthProbeConfigurationError() from exc
    url_host = (
        f"[{normalized_host}]" if ":" in normalized_host else normalized_host
    )
    return f"http://{url_host}:{port}{MATE_BRIDGE_HEALTH_PATH}"


class MateBridgeHealthProbe:
    """Issue a signed GET and translate only exact identity into health."""

    __slots__ = (
        "_clock_ms",
        "_config",
        "_connection_factory",
        "_nonce_factory",
        "_principal",
        "_secret",
    )

    def __init__(
        self,
        *,
        config: MateBridgeHealthProbeConfig,
        secret: bytes,
        principal: str,
        clock_ms: Callable[[], int] | None = None,
        nonce_factory: Callable[[], str] | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        try:
            if type(config) is not MateBridgeHealthProbeConfig:
                raise TypeError
            if (
                type(secret) is not bytes
                or not (_MIN_SECRET_BYTES <= len(secret) <= _MAX_SECRET_BYTES)
            ):
                raise ValueError
            normalized_principal = _identifier(principal)
            if clock_ms is not None and not callable(clock_ms):
                raise TypeError
            if nonce_factory is not None and not callable(nonce_factory):
                raise TypeError
            if connection_factory is not None and not callable(connection_factory):
                raise TypeError
        except (TypeError, ValueError) as exc:
            raise MateBridgeHealthProbeConfigurationError() from exc
        self._config = config
        self._secret = bytes(secret)
        self._principal = normalized_principal
        self._clock_ms = clock_ms or _system_clock_ms
        self._nonce_factory = nonce_factory or _new_nonce
        self._connection_factory = connection_factory or _http_connection

    @property
    def config(self) -> MateBridgeHealthProbeConfig:
        return self._config

    @property
    def principal(self) -> str:
        return self._principal

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        endpoint_id = str(getattr(endpoint, "endpoint_id", "") or "")
        endpoint_kind = _validate_mate_endpoint(endpoint, self._config)
        if endpoint_kind is None:
            return _unhealthy(endpoint_id, MATE_BRIDGE_HEALTH_ENDPOINT_INVALID)

        try:
            status_code, payload = self._request_health()
        except Exception:
            return _unhealthy(endpoint_id, MATE_BRIDGE_HEALTH_REQUEST_FAILED)
        if payload is None:
            return _unhealthy(
                endpoint_id,
                MATE_BRIDGE_HEALTH_RESPONSE_INVALID,
                status_code=status_code,
            )
        if endpoint_kind == "http":
            return _http_result(endpoint, status_code, payload)
        return _udp_result(endpoint, status_code, payload)

    def _request_health(self) -> tuple[int, dict[str, Any] | None]:
        timestamp_ms = self._clock_ms()
        nonce = self._nonce_factory()
        if type(timestamp_ms) is not int or timestamp_ms < 0:
            raise ValueError("invalid clock")
        if type(nonce) is not str or _IDENTIFIER_RE.fullmatch(nonce) is None:
            raise ValueError("invalid nonce")
        headers = build_http_auth_headers(
            secret=self._secret,
            key_id=self._config.key_id,
            method="GET",
            path=MATE_BRIDGE_HEALTH_PATH,
            timestamp_ms=timestamp_ms,
            nonce=nonce,
            principal=self._principal,
            scope=MATE_BRIDGE_HEALTH_SCOPE,
            raw_body=b"",
        )
        headers["Accept"] = "application/json"
        connection = self._connection_factory(
            self._config.http_host,
            self._config.http_port,
            self._config.timeout_seconds,
        )
        try:
            connection.request(
                "GET",
                MATE_BRIDGE_HEALTH_PATH,
                body=None,
                headers=headers,
            )
            response = connection.getresponse()
            status = getattr(response, "status", None)
            if type(status) is not int:
                raise ValueError("invalid status")
            raw = response.read(self._config.max_response_bytes + 1)
            if type(raw) is not bytes or len(raw) > self._config.max_response_bytes:
                return status, None
        finally:
            connection.close()
        try:
            decoded = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeError, ValueError, TypeError):
            return status, None
        return status, decoded if type(decoded) is dict else None


def _validate_mate_endpoint(
    endpoint: EndpointSpec,
    config: MateBridgeHealthProbeConfig,
) -> str | None:
    if (
        type(endpoint) is not EndpointSpec
        or endpoint.kind is not EndpointKind.LISTENER
        or endpoint.owner_id != MATE_BRIDGE_OWNER_ID
        or endpoint.enabled is not True
        or len(endpoint.protocols) != 1
    ):
        return None
    protocol = endpoint.protocols[0]
    if (
        endpoint.health.expected_owner_id != endpoint.owner_id
        or endpoint.health.expected_protocol != protocol.name
        or endpoint.health.expected_protocol_version != protocol.version
        or protocol.version != MATE_BRIDGE_PROTOCOL_VERSION
    ):
        return None
    if endpoint.endpoint_id == MATE_BRIDGE_HTTP_ENDPOINT_ID:
        if (
            endpoint.transport is not Transport.TCP
            or protocol.name != MATE_BRIDGE_HTTP_PROTOCOL_ID
            or endpoint.port != config.http_port
            or _canonical_host(endpoint.bind_host)
            != _canonical_host(config.http_host)
            or endpoint.health.kind != "http"
            or endpoint.health.path != MATE_BRIDGE_HEALTH_PATH
        ):
            return None
        return "http"
    if endpoint.endpoint_id == MATE_BRIDGE_UDP_ENDPOINT_ID:
        expected_path = (
            build_mate_bridge_health_url(config.http_host, config.http_port)
            + "#bridge_udp"
        )
        if (
            endpoint.transport is not Transport.UDP
            or protocol.name != MATE_BRIDGE_UDP_PROTOCOL_ID
            or endpoint.health.kind != "indirect"
            or endpoint.health.path != expected_path
        ):
            return None
        return "udp"
    return None


def _http_result(
    endpoint: EndpointSpec,
    status_code: int,
    payload: Mapping[str, Any],
) -> HealthProbeResult:
    owner = _exact_text(payload.get("owner_id"))
    protocol = _exact_text(payload.get("protocol_id"))
    version = _exact_text(payload.get("protocol_version"))
    healthy = bool(
        status_code == 200
        and payload.get("status") == "ok"
        and owner == endpoint.owner_id
        and protocol == endpoint.protocols[0].name
        and version == endpoint.protocols[0].version
    )
    return HealthProbeResult(
        endpoint_id=endpoint.endpoint_id,
        healthy=healthy,
        status_code=status_code,
        owner_id=owner,
        protocol_id=protocol,
        protocol_version=version,
        detail=(
            MATE_BRIDGE_HEALTH_OK
            if healthy
            else MATE_BRIDGE_HEALTH_HTTP_IDENTITY_MISMATCH
        ),
    )


def _udp_result(
    endpoint: EndpointSpec,
    status_code: int,
    payload: Mapping[str, Any],
) -> HealthProbeResult:
    owner = _exact_text(payload.get("owner_id"))
    udp = payload.get("udp_ingress")
    udp_payload = udp if type(udp) is dict else {}
    protocol = _exact_text(udp_payload.get("protocol_id"))
    version = _exact_text(udp_payload.get("protocol_version"))
    port = udp_payload.get("port")
    host_matches = (
        _canonical_host(_exact_text(udp_payload.get("host")) or "")
        == _canonical_host(endpoint.bind_host)
    )
    healthy = bool(
        status_code == 200
        and payload.get("status") == "ok"
        and owner == endpoint.owner_id
        and udp_payload.get("ready") is True
        and host_matches
        and type(port) is int
        and port == endpoint.port
        and protocol == endpoint.protocols[0].name
        and version == endpoint.protocols[0].version
    )
    return HealthProbeResult(
        endpoint_id=endpoint.endpoint_id,
        healthy=healthy,
        status_code=status_code,
        owner_id=owner,
        protocol_id=protocol,
        protocol_version=version,
        detail=(
            MATE_BRIDGE_HEALTH_OK
            if healthy
            else MATE_BRIDGE_HEALTH_UDP_IDENTITY_MISMATCH
        ),
    )


def _unhealthy(
    endpoint_id: str,
    reason_code: str,
    *,
    status_code: int | None = None,
) -> HealthProbeResult:
    return HealthProbeResult(
        endpoint_id=endpoint_id,
        healthy=False,
        status_code=status_code,
        detail=reason_code,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _exact_text(value: Any) -> str | None:
    return value if type(value) is str and value else None


def _identifier(value: Any) -> str:
    text = str(value or "").strip()
    if _IDENTIFIER_RE.fullmatch(text) is None:
        raise ValueError("invalid identifier")
    return text


def _normalize_host(value: Any) -> str:
    host = str(value or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if _HOST_RE.fullmatch(host) is None or any(
        character in host for character in "/?#@"
    ):
        raise ValueError("invalid host")
    return host.lower()


def _canonical_host(value: Any) -> str:
    try:
        return _normalize_host(value)
    except ValueError:
        return ""


def _system_clock_ms() -> int:
    return int(time.time() * 1000)


def _new_nonce() -> str:
    return secrets.token_urlsafe(24)


def _http_connection(host: str, port: int, timeout: float) -> Any:
    return http.client.HTTPConnection(host, port, timeout=timeout)


__all__ = [
    "MATE_BRIDGE_HEALTH_CONFIG_INVALID",
    "MATE_BRIDGE_HEALTH_ENDPOINT_INVALID",
    "MATE_BRIDGE_HEALTH_HTTP_IDENTITY_MISMATCH",
    "MATE_BRIDGE_HEALTH_OK",
    "MATE_BRIDGE_HEALTH_PATH",
    "MATE_BRIDGE_HEALTH_REQUEST_FAILED",
    "MATE_BRIDGE_HEALTH_RESPONSE_INVALID",
    "MATE_BRIDGE_HEALTH_SCOPE",
    "MATE_BRIDGE_HEALTH_UDP_IDENTITY_MISMATCH",
    "MATE_BRIDGE_HTTP_ENDPOINT_ID",
    "MATE_BRIDGE_HTTP_PROTOCOL_ID",
    "MATE_BRIDGE_OWNER_ID",
    "MATE_BRIDGE_PROTOCOL_VERSION",
    "MATE_BRIDGE_UDP_ENDPOINT_ID",
    "MATE_BRIDGE_UDP_PROTOCOL_ID",
    "MateBridgeHealthProbe",
    "MateBridgeHealthProbeConfig",
    "MateBridgeHealthProbeConfigurationError",
    "build_mate_bridge_health_url",
]
