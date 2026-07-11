"""Feature-gated, read-only governance snapshot route."""

from __future__ import annotations

import inspect
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from fastapi import Depends, HTTPException

from host_brain.app.obs_routes_core import router
from host_brain.app.settings.core import get_core_settings
from host_brain.observability.governance_view import build_governance_view


SnapshotProvider = Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
IdentityProvider = Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
_snapshot_provider: SnapshotProvider | None = None
_identity_provider: IdentityProvider | None = None


def set_governance_snapshot_provider(provider: SnapshotProvider | None) -> None:
    global _snapshot_provider
    _snapshot_provider = provider


def set_governance_identity_provider(provider: IdentityProvider | None) -> None:
    """Install internal, already-safe identity metadata; no browser input exists."""
    global _identity_provider
    _identity_provider = provider


def _require_governance_views_enabled() -> None:
    if not get_core_settings().nle_governance_views_enabled:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "NLE_GOVERNANCE_VIEWS_DISABLED",
                "detail": "set HOST_NLE_GOVERNANCE_VIEWS_ENABLED=true to expose governance projections",
            },
        )


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


async def _default_snapshot() -> dict[str, Any]:
    from host_brain.app.dependencies import get_capability_scorecard_provider
    from host_brain.observability.degradation_state import get_degradation_state

    try:
        capabilities = get_capability_scorecard_provider().list_all()
    except Exception:
        capabilities = None
    try:
        degradation = get_degradation_state()
    except Exception:
        degradation = None
    identity = None
    if _identity_provider is not None:
        identity = dict(await _resolve(_identity_provider()) or {})
    return build_governance_view(
        capability_scorecards=capabilities,
        degradation_state=degradation,
        identity=identity,
        pentest_environment={
            "lab_mode": _truthy_env("PENTEST_WEB_LAB_MODE"),
            "allow_exploit": _truthy_env("PENTEST_ALLOW_EXPLOIT"),
            "domain_mode": os.environ.get("HOST_PENTEST_KNOWLEDGE_MODE", "domain"),
        },
        generated_at_ms=int(time.time() * 1000),
    )


async def _get_governance_snapshot() -> dict[str, Any]:
    # Gate before selecting or constructing any production provider.
    _require_governance_views_enabled()
    provider = _snapshot_provider or _default_snapshot
    try:
        return dict(await _resolve(provider()) or {})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "GOVERNANCE_READ_MODEL_UNAVAILABLE", "detail": str(exc)[:400]},
        ) from exc


@router.get("/governance/snapshot")
async def get_governance_snapshot(
    snapshot: dict[str, Any] = Depends(_get_governance_snapshot),
) -> dict[str, Any]:
    return snapshot


__all__ = [
    "get_governance_snapshot",
    "set_governance_identity_provider",
    "set_governance_snapshot_provider",
]
