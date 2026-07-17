"""App-bound REST adapters for durable MateEngine embodiment commands.

The Host never treats durable enqueue as a physical-device success.  Each
router is bound to the owning ASGI application's ``HostEmbodimentRuntime`` so
two Host apps in the same process cannot borrow an outbox or an Edge lease.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.channels.mateengine import (
    EVENT_MOB_PROXIMITY,
    NATSUME_TEXT_PREFIX,
    SUPPORTED_MATEENGINE_ACTIONS,
    SUPPORTED_SIGNAL_EVENTS,
    build_mateengine_signal_payload,
    list_mateengine_pose_presets,
)
from natsume_contracts.edge.v1 import new_request_id


class MateEngineSignalRequest(BaseModel):
    event_type: str = Field(default=EVENT_MOB_PROXIMITY)
    name: str | None = None
    event_id: str | None = None
    phase: str | None = None
    distance: float | None = None
    biome: str | None = None


class MateEngineSignalResponse(BaseModel):
    """A durable enqueue acknowledgement, never an actuator receipt."""

    ok: bool
    status: str
    host: str | None = None
    port: int | None = None
    payload: dict[str, Any]
    bytes_sent: int = 0
    error: str | None = None
    outbox_id: str | None = None
    physical_effect_verified: bool = False


class MateEngineSayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    emotion: str | None = None
    ttl: float | None = Field(default=8.0, ge=0.1, le=30.0)


class MateEngineActionRequest(BaseModel):
    action: str = Field(default="state")
    state: str | None = None
    touch_type: str | None = None
    emotion_tag: str | None = None
    expression: str | None = None
    preset: str | None = None
    text: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    ttl: float | None = Field(default=8.0, ge=0.1, le=60.0)
    duration: float | None = Field(default=None, ge=0.1, le=60.0)
    intensity: float | None = Field(default=None, ge=0.0, le=1.0)
    scale: float | None = Field(default=None, ge=0.1, le=4.0)
    position_x: float | None = Field(default=None, ge=-1.0, le=1.0)
    position_y: float | None = Field(default=None, ge=-1.0, le=1.0)
    request_id: str | None = None


def _require_runtime(route_ports: HostRoutePorts) -> Any:
    provider = route_ports.embodiment_runtime_provider
    runtime = provider() if provider is not None else None
    if runtime is None or not callable(getattr(runtime, "enqueue", None)):
        raise HTTPException(status_code=503, detail="EMBODIMENT_RUNTIME_UNAVAILABLE")
    return runtime


def _pending_ack(ack: Any) -> dict[str, Any]:
    value = dict(ack.as_dict())
    value["physical_effect_verified"] = False
    value["delivery_state"] = "DURABLY_QUEUED"
    return value


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
            ],
        }

    @router.get("/health")
    async def mateengine_health() -> dict[str, Any]:
        from host_brain.channels.embodiment import queue_mateengine_command  # noqa: PLC0415

        return _pending_ack(
            queue_mateengine_command(
                action="health",
                payload={},
                request_id=new_request_id(),
                runtime=_require_runtime(route_ports),
            )
        )

    @router.post("/signal", response_model=MateEngineSignalResponse)
    async def mateengine_signal(request: MateEngineSignalRequest) -> MateEngineSignalResponse:
        from host_brain.channels.embodiment import queue_mateengine_command  # noqa: PLC0415

        payload = build_mateengine_signal_payload(
            event_type=request.event_type,
            name=request.name,
            event_id=request.event_id,
            phase=request.phase,
            distance=request.distance,
            biome=request.biome,
        )
        ack = queue_mateengine_command(
            action="signal",
            payload={"signal": payload},
            request_id=new_request_id(),
            runtime=_require_runtime(route_ports),
        )
        return MateEngineSignalResponse(
            ok=ack.accepted,
            status=ack.status.lower(),
            payload=payload,
            error=None if ack.accepted else ack.detail,
            outbox_id=ack.outbox_id,
        )

    @router.post("/say", response_model=MateEngineSignalResponse)
    async def mateengine_say(request: MateEngineSayRequest) -> MateEngineSignalResponse:
        from host_brain.channels.embodiment import queue_mateengine_command  # noqa: PLC0415

        signal = build_mateengine_signal_payload(
            event_type=EVENT_MOB_PROXIMITY,
            name=NATSUME_TEXT_PREFIX + request.text,
            event_id="natsume_say",
            phase="enter",
            distance=1.0,
        )
        if request.emotion:
            signal["emotion"] = request.emotion
        if request.ttl is not None:
            signal["ttl"] = request.ttl
        ack = queue_mateengine_command(
            action="signal",
            payload={"signal": signal},
            request_id=new_request_id(),
            runtime=_require_runtime(route_ports),
        )
        return MateEngineSignalResponse(
            ok=ack.accepted,
            status=ack.status.lower(),
            payload=signal,
            error=None if ack.accepted else ack.detail,
            outbox_id=ack.outbox_id,
        )

    @router.post("/action")
    async def mateengine_action(request: MateEngineActionRequest) -> dict[str, Any]:
        from host_brain.channels.embodiment import queue_mateengine_command  # noqa: PLC0415

        action = str(request.action or "state").strip().lower()
        if action in {"idle", "reset"} or str(request.state or "").lower() == "idle":
            command: dict[str, Any] = {"type": "reset"}
        elif action in {"state", "thinking", "listening", "speak", "speaking", "emote", "expression"}:
            emotion = str(request.emotion_tag or request.expression or request.preset or "NEUTRAL").upper()
            command = {
                "type": "expression", "preset": emotion, "emotion_tag": emotion,
                "value": request.intensity, "ttl": request.duration or request.ttl,
            }
        elif action in {"say", "show"}:
            command = {"type": "say", "text": request.text or "", "ttl": request.ttl}
        else:
            command = {
                "type": action, "state": request.state, "touch_type": request.touch_type,
                "preset": request.preset, "enabled": request.enabled,
                "duration": request.duration, "value": request.intensity,
                "scale": request.scale, "position_x": request.position_x,
                "position_y": request.position_y,
            }
        command = {key: value for key, value in command.items() if value is not None}
        return _pending_ack(
            queue_mateengine_command(
                action="command",
                payload={"command": command},
                request_id=request.request_id,
                runtime=_require_runtime(route_ports),
            )
        )

    return router


__all__ = ["create_mateengine_router"]
