"""App-bound REST adapters for durable MateEngine embodiment commands.

The Host never treats durable enqueue as a physical-device success.  Each
router is bound to the owning ASGI application's ``HostEmbodimentRuntime`` so
two Host apps in the same process cannot borrow an outbox or an Edge lease.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException

from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.channels.mateengine import (
    SUPPORTED_MATEENGINE_ACTIONS,
    SUPPORTED_SIGNAL_EVENTS,
    list_mateengine_pose_presets,
)


def _require_runtime(route_ports: HostRoutePorts) -> Any:
    provider = route_ports.embodiment_runtime_provider
    runtime = provider() if provider is not None else None
    if runtime is None or not callable(getattr(runtime, "enqueue", None)):
        raise HTTPException(status_code=503, detail="EMBODIMENT_RUNTIME_UNAVAILABLE")
    return runtime


def _read_only_health(route_ports: HostRoutePorts) -> dict[str, Any]:
    """Project Host outbox readiness without creating an embodiment command.

    ``GET /health`` is consumed by the Monitor as a low-risk query.  It must
    never manufacture a durable ``health`` command merely to answer that
    query: physical device state belongs to Edge and can only be established
    by an Edge-owned receipt.  In particular, an isolated Host must be able
    to start while the remote Edge machine is intentionally absent.
    """

    runtime = _require_runtime(route_ports)
    runtime_scope_id = str(getattr(runtime, "runtime_scope_id", "") or "")
    authority_lease = getattr(runtime, "authority_lease", None)
    # Narrow test and adapter runtimes may not expose ``closed``; an app
    # runtime with an ``enqueue`` port is treated as open unless it explicitly
    # reports that it has closed.
    host_outbox_ready = not bool(getattr(runtime, "closed", False))
    return {
        # ``ok`` intentionally answers the remote physical-health question,
        # not whether this Host can write to its outbox.
        "ok": False,
        "status": "REMOTE_EDGE_UNVERIFIED",
        "host_outbox_ready": host_outbox_ready,
        "runtime_scope_id": runtime_scope_id or None,
        "authority_lease_active": isinstance(authority_lease, dict),
        "physical_effect_verified": False,
        "delivery_state": "NOT_DISPATCHED",
        "reason_code": "EMBODIMENT_REMOTE_HEALTH_REQUIRES_EDGE_RECEIPT",
        "notes": [
            "This endpoint is a Host-local read-only projection.",
            "It does not contact Edge or enqueue an embodiment command.",
            "Physical MateEngine health requires an Edge-owned receipt.",
        ],
    }


def _raise_direct_command_retired() -> NoReturn:
    """Reject the pre-TaskGraph MateEngine mutation adapters.

    These historical HTTP routes cannot bind an exact Task/Grant/Preview
    boundary.  Leaving them reachable would either bypass the governed
    embodiment authority or fail only after partially constructing a command.
    The canonical replacement is a governed TaskGraph effect delivered through
    the Host embodiment outbox and settled by an Edge-owned receipt.
    """

    raise HTTPException(
        status_code=410,
        detail={
            "reason_code": "MATEENGINE_DIRECT_COMMAND_RETIRED",
            "replacement_authority": "governed_taskgraph_edge_embodiment",
            "physical_effect_verified": False,
        },
    )


def create_mateengine_router(route_ports: HostRoutePorts) -> APIRouter:
    """Create a router bound to one Host application's embodiment runtime."""

    router = APIRouter(prefix="/v1/mateengine", tags=["mateengine"])

    @router.get("/capabilities")
    async def mateengine_capabilities() -> dict[str, Any]:
        return {
            "transport": "edge_embodiment_command_v1",
            "physical_owner": "edge_client",
            "host_direct_effects": False,
            "events": sorted(SUPPORTED_SIGNAL_EVENTS),
            "actions": sorted(SUPPORTED_MATEENGINE_ACTIONS),
            "pose_presets": list_mateengine_pose_presets(),
            "notes": [
                "Host writes versioned commands to its durable outbox; Edge exclusively owns MateEngine transport.",
                "A PENDING response is only durable admission. Read a durable Edge receipt before claiming an effect.",
                "The historical direct POST routes are retired; a governed TaskGraph is required for every device effect.",
            ],
        }

    @router.get("/health")
    async def mateengine_health() -> dict[str, Any]:
        return _read_only_health(route_ports)

    @router.post("/action")
    async def mateengine_action() -> dict[str, Any]:
        _raise_direct_command_retired()

    @router.post("/signal")
    async def mateengine_signal() -> dict[str, Any]:
        _raise_direct_command_retired()

    @router.post("/say")
    async def mateengine_say() -> dict[str, Any]:
        _raise_direct_command_retired()

    return router


__all__ = ["create_mateengine_router"]
