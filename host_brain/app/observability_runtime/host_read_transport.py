"""Fail-closed Host-to-OBS JSON read transport.

The transport is deliberately small and synchronous.  Its configuration is
captured when an instance is created; request execution never consults process
globals and the object keeps no mutable response cache.  Operational failures
are converted to stable, redacted reason codes so projection jobs can degrade
without exposing internal topology or credentials.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import queue
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final


HOST_READ_CONFIG: Final = "CONFIG"
HOST_READ_AUTH: Final = "AUTH"
HOST_READ_TIMEOUT: Final = "TIMEOUT"
HOST_READ_HTTP: Final = "HTTP"
HOST_READ_INVALID_JSON: Final = "INVALID_JSON"
HOST_READ_INVALID_PAYLOAD: Final = "INVALID_PAYLOAD"
HOST_READ_RESPONSE_TOO_LARGE: Final = "RESPONSE_TOO_LARGE"
HOST_READ_MAX_RESPONSE_BYTES: Final = 1_048_576
_HOST_READ_CHUNK_BYTES: Final = 64 * 1024

HOST_READ_ALLOWED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/internal/obs-sources/mode",
        "/internal/obs-sources/capability-forge",
        "/internal/obs-sources/governance",
        "/internal/obs-sources/runtime",
        "/internal/obs-sources/semantic-recall",
    }
)

_PUBLIC_READY = "READY"
_PUBLIC_UNAVAILABLE = "UNAVAILABLE"
_monotonic = time.monotonic
_RESOLVER_SLOT_LIMIT: Final = 4
_resolver_slots = threading.BoundedSemaphore(_RESOLVER_SLOT_LIMIT)


class _TotalDeadlineExceeded(TimeoutError):
    """Internal marker for one exhausted end-to-end read budget."""


class _DeadlineResponse:
    """Own an origin response and the watchdog enforcing its total deadline."""

    __slots__ = ("_connection", "_expired", "_response", "_timer")

    def __init__(
        self,
        *,
        connection: http.client.HTTPConnection,
        response: http.client.HTTPResponse,
        timer: threading.Timer,
        expired: threading.Event,
    ) -> None:
        self._connection = connection
        self._response = response
        self._timer = timer
        self._expired = expired

    @property
    def status(self) -> int:
        return int(self._response.status)

    def getcode(self) -> int:
        return self.status

    def read(self, size: int = -1) -> bytes:
        try:
            return self._response.read(size)
        except Exception:
            if self._expired.is_set():
                raise _TotalDeadlineExceeded from None
            raise

    def read1(self, size: int = -1) -> bytes:
        try:
            return self._response.read1(size)
        except Exception:
            if self._expired.is_set():
                raise _TotalDeadlineExceeded from None
            raise

    def set_read_timeout(self, timeout: float) -> None:
        response_socket = _response_socket(self._response)
        if response_socket is not None:
            response_socket.settimeout(timeout)

    def close(self) -> None:
        self._timer.cancel()
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self) -> _DeadlineResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _open_no_redirect(request: urllib.request.Request, *, timeout: float):
    """Open one origin request without consulting proxies or following redirects."""

    deadline = _monotonic() + timeout
    parsed = urllib.parse.urlsplit(request.full_url)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError("invalid origin URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    addresses = _resolve_stream_addresses(
        hostname,
        port,
        deadline=deadline,
    )
    if parsed.scheme == "http":
        addresses = tuple(
            address
            for address in addresses
            if _is_loopback_sockaddr(address[4])
        )
        if not addresses:
            raise OSError("HTTP origin did not resolve to a loopback address")
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(
        hostname,
        port=parsed.port,
        timeout=_remaining_seconds(deadline),
    )
    # Keep the configured hostname on the connection so HTTP Host and TLS SNI
    # remain correct, but prevent the stdlib from starting a second unbounded
    # resolver call after the bounded lookup above.
    connection._create_connection = (  # type: ignore[attr-defined]
        lambda _address, _timeout, source_address: _connect_resolved_stream(
            addresses,
            deadline=deadline,
            source_address=source_address,
        )
    )
    expired = threading.Event()
    response_holder: list[http.client.HTTPResponse] = []

    def _expire() -> None:
        expired.set()
        response = response_holder[0] if response_holder else None
        response_socket = _response_socket(response)
        connection_socket = getattr(connection, "sock", None)
        _abort_socket(response_socket)
        if connection_socket is not response_socket:
            _abort_socket(connection_socket)
        connection.close()

    timer = threading.Timer(_remaining_seconds(deadline), _expire)
    timer.daemon = True
    timer.start()
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    try:
        connection.request(
            request.get_method(),
            target,
            body=request.data,
            headers=dict(request.header_items()),
        )
        response = connection.getresponse()
        response_holder.append(response)
        if expired.is_set():
            raise _TotalDeadlineExceeded
        return _DeadlineResponse(
            connection=connection,
            response=response,
            timer=timer,
            expired=expired,
        )
    except Exception:
        timer.cancel()
        response = response_holder[0] if response_holder else None
        if response is not None:
            response.close()
        connection.close()
        if expired.is_set():
            raise _TotalDeadlineExceeded from None
        raise


def _resolve_stream_addresses(
    hostname: str,
    port: int,
    *,
    deadline: float,
) -> tuple[tuple[int, int, int, str, tuple[Any, ...]], ...]:
    """Resolve in a daemon worker so caller latency remains deadline-bounded."""

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        sockaddr: tuple[Any, ...] = (
            (str(literal), port, 0, 0)
            if literal.version == 6
            else (str(literal), port)
        )
        return ((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr),)

    resolver_slots = _resolver_slots
    if not resolver_slots.acquire(timeout=_remaining_seconds(deadline)):
        raise _TotalDeadlineExceeded

    results: queue.Queue[object] = queue.Queue(maxsize=1)

    def _resolve() -> None:
        try:
            try:
                value: object = socket.getaddrinfo(
                    hostname,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                )
            except Exception as exc:  # propagated in the caller, then redacted
                value = exc
            try:
                results.put_nowait(value)
            except queue.Full:
                pass
        finally:
            resolver_slots.release()

    worker = threading.Thread(
        target=_resolve,
        name="host-obs-bounded-resolver",
        daemon=True,
    )
    try:
        worker.start()
    except Exception:
        resolver_slots.release()
        raise
    try:
        value = results.get(timeout=_remaining_seconds(deadline))
    except queue.Empty:
        raise _TotalDeadlineExceeded from None
    if isinstance(value, Exception):
        raise value
    addresses = tuple(value) if isinstance(value, (list, tuple)) else ()
    if not addresses:
        raise OSError("origin resolution returned no stream address")
    return addresses


def _connect_resolved_stream(
    addresses: tuple[tuple[int, int, int, str, tuple[Any, ...]], ...],
    *,
    deadline: float,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    """Connect to one resolved address while consuming the shared deadline."""

    last_error: OSError | None = None
    for family, socktype, proto, _canonname, sockaddr in addresses:
        stream = socket.socket(family, socktype, proto)
        try:
            stream.settimeout(_remaining_seconds(deadline))
            if source_address is not None:
                stream.bind(source_address)
            stream.connect(sockaddr)
            return stream
        except OSError as exc:
            last_error = exc
            stream.close()
    if last_error is not None:
        raise last_error
    raise OSError("origin resolution returned no usable stream address")


class HostReadTransportError(RuntimeError):
    """Redacted exception raised only when a caller requires a failed result."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or HOST_READ_HTTP)
        super().__init__(f"host read unavailable: {self.reason_code}")

    def as_public_payload(self) -> dict[str, str]:
        return {
            "status": _PUBLIC_UNAVAILABLE,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class HostReadResult:
    """One read result with a separately redacted public status projection."""

    ok: bool
    payload: dict[str, Any] | None = field(default=None, repr=False)
    reason_code: str | None = None

    def as_public_payload(self) -> dict[str, str | None]:
        """Return status metadata only; never reflect the response body."""

        return {
            "status": _PUBLIC_READY if self.ok else _PUBLIC_UNAVAILABLE,
            "reason_code": None if self.ok else self.reason_code,
        }

    def require_payload(self) -> dict[str, Any]:
        """Return a shallow copy or raise a redacted, stable exception."""

        if not self.ok or self.payload is None:
            raise HostReadTransportError(self.reason_code or HOST_READ_HTTP)
        return dict(self.payload)


@dataclass(frozen=True, slots=True)
class HostReadTransport:
    """Synchronous, stateless client for a narrow set of Host read endpoints."""

    base_url: str = field(repr=False)
    token: str = field(repr=False)
    timeout_s: float = 1.5
    _configuration_valid: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        base_url = str(self.base_url or "").strip().rstrip("/")
        token = str(self.token or "").strip()
        try:
            timeout_s = float(self.timeout_s)
        except (TypeError, ValueError):
            timeout_s = 0.0

        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "token", token)
        object.__setattr__(self, "timeout_s", timeout_s)
        object.__setattr__(
            self,
            "_configuration_valid",
            _valid_configuration(base_url=base_url, token=token, timeout_s=timeout_s),
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_s: float = 1.5,
    ) -> HostReadTransport:
        """Capture environment configuration once and return a frozen client."""

        source = os.environ if environ is None else environ
        base_url = str(source.get("HOST_OBS_HOST_API_BASE_URL") or "").strip()
        if not base_url:
            port = str(source.get("HOST_APP_PORT") or "18080").strip() or "18080"
            base_url = f"http://127.0.0.1:{port}"
        return cls(
            base_url=base_url,
            token=str(source.get("HOST_OBS_READ_TOKEN") or ""),
            timeout_s=timeout_s,
        )

    def read_json(self, path: str) -> HostReadResult:
        """Read one allowlisted endpoint and return a typed, redacted result."""

        if not self._configuration_valid or path not in HOST_READ_ALLOWED_PATHS:
            return _failure(HOST_READ_CONFIG)

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={
                "Accept": "application/json",
                "X-Internal-Token": self.token,
            },
            method="GET",
        )
        deadline = _monotonic() + self.timeout_s
        try:
            with _open_no_redirect(
                request,
                timeout=_remaining_seconds(deadline),
            ) as response:
                _remaining_seconds(deadline)
                status = _response_status(response)
                if status in {401, 403}:
                    return _failure(HOST_READ_AUTH)
                if status is not None and not 200 <= status < 300:
                    return _failure(HOST_READ_HTTP)
                raw = _read_bounded_body(response, deadline=deadline)
        except urllib.error.HTTPError as exc:
            return _failure(
                HOST_READ_AUTH if int(getattr(exc, "code", 0) or 0) in {401, 403} else HOST_READ_HTTP
            )
        except (_TotalDeadlineExceeded, socket.timeout, TimeoutError):
            return _failure(HOST_READ_TIMEOUT)
        except urllib.error.URLError as exc:
            if _is_timeout_reason(getattr(exc, "reason", None)):
                return _failure(HOST_READ_TIMEOUT)
            return _failure(HOST_READ_HTTP)
        except Exception:
            # urllib exceptions can include the full URL in their message.
            # They must never cross this application boundary unchanged.
            return _failure(
                HOST_READ_TIMEOUT if _deadline_elapsed(deadline) else HOST_READ_HTTP
            )

        if len(raw) > HOST_READ_MAX_RESPONSE_BYTES:
            return _failure(HOST_READ_RESPONSE_TOO_LARGE)
        try:
            decoded = raw.decode("utf-8")
            payload = json.loads(decoded)
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            return _failure(HOST_READ_INVALID_JSON)
        except Exception:
            return _failure(HOST_READ_INVALID_JSON)
        if not isinstance(payload, dict):
            return _failure(HOST_READ_INVALID_PAYLOAD)
        return HostReadResult(ok=True, payload=dict(payload), reason_code=None)

    def __call__(self, path: str) -> HostReadResult:
        """Expose the same synchronous operation as an injectable callable port."""

        return self.read_json(path)


def _failure(reason_code: str) -> HostReadResult:
    return HostReadResult(ok=False, payload=None, reason_code=reason_code)


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - _monotonic()
    if remaining <= 0:
        raise _TotalDeadlineExceeded
    return remaining


def _deadline_elapsed(deadline: float) -> bool:
    return _monotonic() >= deadline


def _read_bounded_body(response: Any, *, deadline: float) -> Any:
    """Read at most 1 MiB + 1 byte while consuming one shared deadline."""

    read1 = getattr(response, "read1", None)
    if not callable(read1):
        remaining = _remaining_seconds(deadline)
        _set_response_timeout(response, remaining)
        raw = response.read(HOST_READ_MAX_RESPONSE_BYTES + 1)
        _remaining_seconds(deadline)
        return raw

    chunks: list[bytes] = []
    total = 0
    while total <= HOST_READ_MAX_RESPONSE_BYTES:
        remaining = _remaining_seconds(deadline)
        _set_response_timeout(response, remaining)
        read_size = min(
            _HOST_READ_CHUNK_BYTES,
            HOST_READ_MAX_RESPONSE_BYTES + 1 - total,
        )
        chunk = read1(read_size)
        _remaining_seconds(deadline)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            try:
                chunk = bytes(chunk)
            except (TypeError, ValueError):
                return chunk
        chunks.append(chunk)
        total += len(chunk)
        if total > HOST_READ_MAX_RESPONSE_BYTES:
            break
    return b"".join(chunks)


def _set_response_timeout(response: Any, timeout: float) -> None:
    setter = getattr(response, "set_read_timeout", None)
    if callable(setter):
        setter(timeout)
        return
    response_socket = _response_socket(response)
    if response_socket is not None:
        response_socket.settimeout(timeout)


def _response_socket(response: Any) -> socket.socket | None:
    if response is None:
        return None
    stream = getattr(response, "fp", None)
    for owner in (stream, getattr(stream, "raw", None)):
        candidate = getattr(owner, "_sock", None)
        if isinstance(candidate, socket.socket):
            return candidate
    return None


def _abort_socket(target: Any) -> None:
    if target is None:
        return
    try:
        target.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        target.close()
    except OSError:
        pass


def _valid_configuration(*, base_url: str, token: str, timeout_s: float) -> bool:
    if not base_url or not token or not math.isfinite(timeout_s) or timeout_s <= 0:
        return False
    if any(character in base_url or character in token for character in ("\r", "\n")):
        return False
    try:
        parsed = urllib.parse.urlsplit(base_url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    hostname = str(parsed.hostname or "").strip().lower()
    return bool(
        parsed.scheme in {"http", "https"}
        and hostname
        and (parsed.scheme == "https" or _is_loopback_host(hostname))
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and (port is None or 1 <= port <= 65535)
    )


def _is_loopback_host(hostname: str) -> bool:
    normalized = str(hostname or "").strip().lower().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _is_loopback_sockaddr(sockaddr: tuple[Any, ...]) -> bool:
    if not sockaddr:
        return False
    candidate = str(sockaddr[0] or "").split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)


def _response_status(response: Any) -> int | None:
    value = getattr(response, "status", None)
    if value is None:
        getcode = getattr(response, "getcode", None)
        value = getcode() if callable(getcode) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_timeout_reason(reason: Any) -> bool:
    return isinstance(reason, (socket.timeout, TimeoutError))
