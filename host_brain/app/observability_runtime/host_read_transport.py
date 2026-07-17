"""Fail-closed Host-to-OBS JSON read transport.

The transport is deliberately small and synchronous.  Its configuration is
captured when an instance is created; request execution never consults process
globals and the object keeps no mutable response cache.  Operational failures
are converted to stable, redacted reason codes so projection jobs can degrade
without exposing internal topology or credentials.
"""

from __future__ import annotations

import json
import math
import os
import socket
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

HOST_READ_ALLOWED_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/internal/tribe_state/all",
        "/scheduler/health",
        "/internal/obs-sources/mode",
        "/internal/obs-sources/capability-forge",
        "/internal/obs-sources/runtime",
    }
)

_PUBLIC_READY = "READY"
_PUBLIC_UNAVAILABLE = "UNAVAILABLE"


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
            token=str(source.get("HOST_INTERNAL_TRIBE_TOKEN") or ""),
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
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                status = _response_status(response)
                if status in {401, 403}:
                    return _failure(HOST_READ_AUTH)
                if status is not None and not 200 <= status < 300:
                    return _failure(HOST_READ_HTTP)
                raw = response.read()
        except urllib.error.HTTPError as exc:
            return _failure(
                HOST_READ_AUTH if int(getattr(exc, "code", 0) or 0) in {401, 403} else HOST_READ_HTTP
            )
        except (socket.timeout, TimeoutError):
            return _failure(HOST_READ_TIMEOUT)
        except urllib.error.URLError as exc:
            if _is_timeout_reason(getattr(exc, "reason", None)):
                return _failure(HOST_READ_TIMEOUT)
            return _failure(HOST_READ_HTTP)
        except Exception:
            # urllib exceptions can include the full URL in their message.
            # They must never cross this application boundary unchanged.
            return _failure(HOST_READ_HTTP)

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
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and (port is None or 1 <= port <= 65535)
    )


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
