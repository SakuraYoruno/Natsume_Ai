from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from host_brain.channels.mateengine import (
    EVENT_MOB_PROXIMITY,
    NATSUME_TEXT_PREFIX,
    SUPPORTED_SIGNAL_EVENTS,
    SUPPORTED_MATEENGINE_ACTIONS,
    build_mateengine_signal_payload,
    list_mateengine_pose_presets,
)
from host_brain.channels.embodiment import queue_mateengine_command
from natsume_contracts.edge.v1 import new_request_id


router = APIRouter(prefix="/v1/mateengine", tags=["mateengine"])


class MateEngineSignalRequest(BaseModel):
    event_type: str = Field(default=EVENT_MOB_PROXIMITY)
    name: str | None = None
    event_id: str | None = None
    phase: str | None = None
    distance: float | None = None
    biome: str | None = None


class MateEngineSignalResponse(BaseModel):
    ok: bool
    status: str
    host: str
    port: int
    payload: dict[str, Any]
    bytes_sent: int = 0
    error: str | None = None


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
            "Host authors versioned commands; Edge exclusively owns MateEngine transport.",
            "Every command carries command_id, authority/grant refs, deadline and idempotency key.",
        ],
    }


@router.get("/health")
async def mateengine_health() -> dict[str, Any]:
    return queue_mateengine_command(
        action="health", payload={}, request_id=new_request_id()
    ).as_dict()


@router.post("/signal", response_model=MateEngineSignalResponse)
async def mateengine_signal(request: MateEngineSignalRequest) -> MateEngineSignalResponse:
    payload = build_mateengine_signal_payload(
        event_type=request.event_type,
        name=request.name,
        event_id=request.event_id,
        phase=request.phase,
        distance=request.distance,
        biome=request.biome,
    )
    receipt = queue_mateengine_command(
        action="signal", payload={"signal": payload}, request_id=new_request_id()
    )
    event = dict(receipt.event or {})
    return MateEngineSignalResponse(
        ok=receipt.success, status=receipt.status.lower(),
        host=str(event.get("host") or "127.0.0.1"),
        port=int(event.get("port") or 32145), payload=payload,
        bytes_sent=int(event.get("bytes_sent") or 0),
        error=None if receipt.success else receipt.detail,
    )


@router.post("/say", response_model=MateEngineSignalResponse)
async def mateengine_say(request: MateEngineSayRequest) -> MateEngineSignalResponse:
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
    receipt = queue_mateengine_command(
        action="signal", payload={"signal": signal}, request_id=new_request_id()
    )
    event = dict(receipt.event or {})
    return MateEngineSignalResponse(
        ok=receipt.success, status=receipt.status.lower(),
        host=str(event.get("host") or "127.0.0.1"),
        port=int(event.get("port") or 32145), payload=signal,
        bytes_sent=int(event.get("bytes_sent") or 0),
        error=None if receipt.success else receipt.detail,
    )


@router.post("/action")
async def mateengine_action(request: MateEngineActionRequest) -> dict[str, Any]:
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
    return queue_mateengine_command(
        action="command",
        payload={"command": command},
        request_id=request.request_id,
    ).as_dict()
