"""Strict Host-owned read endpoints consumed by the standalone Obs process."""

from __future__ import annotations

import hmac
import hashlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from fastapi import APIRouter, Header, HTTPException, Response, status

from natsume_contracts.operator import (
    HostCapabilityForgeSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostModeSourceSnapshot,
    HostRuntimeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
)
from host_brain.observability.projection_store import public_projection_payload
from host_brain.app.observability_runtime.host_read_credentials import (
    resolve_host_obs_read_token,
)


HOST_OBS_READ_AUTH_NOT_CONFIGURED = "HOST_OBS_READ_AUTH_NOT_CONFIGURED"
HOST_OBS_READ_UNAUTHORIZED = "HOST_OBS_READ_UNAUTHORIZED"
HOST_OBS_MODE_SOURCE_UNAVAILABLE = "HOST_OBS_MODE_SOURCE_UNAVAILABLE"
HOST_OBS_CAPABILITY_SOURCE_UNAVAILABLE = "HOST_OBS_CAPABILITY_SOURCE_UNAVAILABLE"
HOST_OBS_GOVERNANCE_SOURCE_UNAVAILABLE = "HOST_OBS_GOVERNANCE_SOURCE_UNAVAILABLE"
HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE = "HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE"
HOST_OBS_SEMANTIC_RECALL_SOURCE_UNAVAILABLE = (
    "HOST_OBS_SEMANTIC_RECALL_SOURCE_UNAVAILABLE"
)
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_PUBLIC_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{2,99}$")


class HostObsReadSourceUnavailable(RuntimeError):
    """Typed Host source failure safe to expose as a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        candidate = str(reason_code or "").strip().upper()
        self.reason_code = (
            candidate
            if _PUBLIC_REASON_CODE.fullmatch(candidate)
            else "HOST_OBS_READ_SOURCE_UNAVAILABLE"
        )
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class HostObsReadPorts:
    """One Host application's immutable providers and dedicated credential."""

    mode_source_reader: Callable[[], HostModeSourceSnapshot]
    capability_forge_source_reader: Callable[[], HostCapabilityForgeSourceSnapshot]
    semantic_recall_source_reader: Callable[[], HostSemanticRecallSourceSnapshot]
    governance_source_reader: Callable[[], HostGovernanceSourceSnapshot]
    runtime_source_reader: Callable[[], HostRuntimeSourceSnapshot]
    token_digest: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        for field_name in (
            "mode_source_reader",
            "capability_forge_source_reader",
            "semantic_recall_source_reader",
            "governance_source_reader",
            "runtime_source_reader",
        ):
            if not callable(getattr(self, field_name)):
                raise TypeError(f"{field_name} must be callable")
        if not isinstance(self.token_digest, bytes):
            raise TypeError("token_digest must be bytes")

    @classmethod
    def from_environment(
        cls,
        *,
        mode_source_reader: Callable[[], HostModeSourceSnapshot],
        capability_forge_source_reader: Callable[[], HostCapabilityForgeSourceSnapshot],
        semantic_recall_source_reader: Callable[[], HostSemanticRecallSourceSnapshot],
        governance_source_reader: Callable[[], HostGovernanceSourceSnapshot],
        runtime_source_reader: Callable[[], HostRuntimeSourceSnapshot],
        environ: Mapping[str, str] | None = None,
    ) -> "HostObsReadPorts":
        source = os.environ if environ is None else environ
        return cls(
            mode_source_reader=mode_source_reader,
            capability_forge_source_reader=capability_forge_source_reader,
            semantic_recall_source_reader=semantic_recall_source_reader,
            governance_source_reader=governance_source_reader,
            runtime_source_reader=runtime_source_reader,
            token_digest=_token_digest(resolve_host_obs_read_token(source)),
        )

    @classmethod
    def from_token(
        cls,
        *,
        mode_source_reader: Callable[[], HostModeSourceSnapshot],
        capability_forge_source_reader: Callable[[], HostCapabilityForgeSourceSnapshot],
        semantic_recall_source_reader: Callable[[], HostSemanticRecallSourceSnapshot],
        governance_source_reader: Callable[[], HostGovernanceSourceSnapshot],
        runtime_source_reader: Callable[[], HostRuntimeSourceSnapshot],
        token: str,
    ) -> "HostObsReadPorts":
        return cls(
            mode_source_reader=mode_source_reader,
            capability_forge_source_reader=capability_forge_source_reader,
            semantic_recall_source_reader=semantic_recall_source_reader,
            governance_source_reader=governance_source_reader,
            runtime_source_reader=runtime_source_reader,
            token_digest=_token_digest(token),
        )


_Snapshot = TypeVar(
    "_Snapshot",
    HostModeSourceSnapshot,
    HostCapabilityForgeSourceSnapshot,
    HostSemanticRecallSourceSnapshot,
    HostGovernanceSourceSnapshot,
    HostRuntimeSourceSnapshot,
)


def _token_digest(value: str) -> bytes:
    token = str(value or "").strip()
    return hashlib.sha256(token.encode("utf-8")).digest() if token else b""


def _authorize(expected_digest: bytes, provided: str | None) -> None:
    if not expected_digest:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=HOST_OBS_READ_AUTH_NOT_CONFIGURED,
            headers=_NO_STORE_HEADERS,
        )
    candidate = str(provided or "").strip()
    if not candidate or not hmac.compare_digest(
        _token_digest(candidate),
        expected_digest,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=HOST_OBS_READ_UNAUTHORIZED,
            headers=_NO_STORE_HEADERS,
        )


def _read_snapshot(
    reader: Callable[[], _Snapshot],
    expected_type: type[_Snapshot],
    unavailable_reason: str,
) -> dict[str, Any]:
    try:
        snapshot = reader()
    except HostObsReadSourceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.reason_code,
            headers=_NO_STORE_HEADERS,
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=unavailable_reason,
            headers=_NO_STORE_HEADERS,
        ) from None
    if not isinstance(snapshot, expected_type):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=unavailable_reason,
            headers=_NO_STORE_HEADERS,
        )
    try:
        public_payload = public_projection_payload(snapshot.as_dict())
        return expected_type.from_mapping(public_payload).as_dict()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=unavailable_reason,
            headers=_NO_STORE_HEADERS,
        ) from None


def create_host_obs_read_router(ports: HostObsReadPorts) -> APIRouter:
    """Bind five read-only routes to one immutable Host application graph."""

    if not isinstance(ports, HostObsReadPorts):
        raise TypeError("ports must be HostObsReadPorts")
    router = APIRouter(prefix="/internal/obs-sources", tags=["internal-obs-read"])

    @router.get("/mode", include_in_schema=False)
    def read_mode_source(
        response: Response,
        x_internal_token: str | None = Header(
            default=None,
            alias="X-Internal-Token",
            convert_underscores=False,
        ),
    ) -> dict[str, Any]:
        _authorize(ports.token_digest, x_internal_token)
        response.headers["Cache-Control"] = "no-store"
        return _read_snapshot(
            ports.mode_source_reader,
            HostModeSourceSnapshot,
            HOST_OBS_MODE_SOURCE_UNAVAILABLE,
        )

    @router.get("/capability-forge", include_in_schema=False)
    def read_capability_forge_source(
        response: Response,
        x_internal_token: str | None = Header(
            default=None,
            alias="X-Internal-Token",
            convert_underscores=False,
        ),
    ) -> dict[str, Any]:
        _authorize(ports.token_digest, x_internal_token)
        response.headers["Cache-Control"] = "no-store"
        return _read_snapshot(
            ports.capability_forge_source_reader,
            HostCapabilityForgeSourceSnapshot,
            HOST_OBS_CAPABILITY_SOURCE_UNAVAILABLE,
        )

    @router.get("/semantic-recall", include_in_schema=False)
    def read_semantic_recall_source(
        response: Response,
        x_internal_token: str | None = Header(
            default=None,
            alias="X-Internal-Token",
            convert_underscores=False,
        ),
    ) -> dict[str, Any]:
        _authorize(ports.token_digest, x_internal_token)
        response.headers["Cache-Control"] = "no-store"
        return _read_snapshot(
            ports.semantic_recall_source_reader,
            HostSemanticRecallSourceSnapshot,
            HOST_OBS_SEMANTIC_RECALL_SOURCE_UNAVAILABLE,
        )

    @router.get("/governance", include_in_schema=False)
    def read_governance_source(
        response: Response,
        x_internal_token: str | None = Header(
            default=None,
            alias="X-Internal-Token",
            convert_underscores=False,
        ),
    ) -> dict[str, Any]:
        _authorize(ports.token_digest, x_internal_token)
        response.headers["Cache-Control"] = "no-store"
        return _read_snapshot(
            ports.governance_source_reader,
            HostGovernanceSourceSnapshot,
            HOST_OBS_GOVERNANCE_SOURCE_UNAVAILABLE,
        )

    @router.get("/runtime", include_in_schema=False)
    def read_runtime_source(
        response: Response,
        x_internal_token: str | None = Header(
            default=None,
            alias="X-Internal-Token",
            convert_underscores=False,
        ),
    ) -> dict[str, Any]:
        _authorize(ports.token_digest, x_internal_token)
        response.headers["Cache-Control"] = "no-store"
        return _read_snapshot(
            ports.runtime_source_reader,
            HostRuntimeSourceSnapshot,
            HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE,
        )

    return router


__all__ = [
    "HOST_OBS_CAPABILITY_SOURCE_UNAVAILABLE",
    "HOST_OBS_GOVERNANCE_SOURCE_UNAVAILABLE",
    "HOST_OBS_MODE_SOURCE_UNAVAILABLE",
    "HOST_OBS_READ_AUTH_NOT_CONFIGURED",
    "HOST_OBS_READ_UNAUTHORIZED",
    "HOST_OBS_RUNTIME_SOURCE_UNAVAILABLE",
    "HOST_OBS_SEMANTIC_RECALL_SOURCE_UNAVAILABLE",
    "HostObsReadPorts",
    "HostObsReadSourceUnavailable",
    "create_host_obs_read_router",
]
