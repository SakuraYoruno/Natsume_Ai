from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from host_brain.channels.mateengine import (
    EVENT_MOB_PROXIMITY,
    SUPPORTED_SIGNAL_EVENTS,
    SUPPORTED_MATEENGINE_ACTIONS,
    get_mateengine_bridge_health,
    list_mateengine_pose_presets,
    send_mateengine_action,
    send_mateengine_say,
    send_mateengine_signal,
)


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
        "transport": "udp",
        "signal_port": 32145,
        "bridge_http": "http://127.0.0.1:18770",
        "events": sorted(SUPPORTED_SIGNAL_EVENTS),
        "actions": sorted(SUPPORTED_MATEENGINE_ACTIONS),
        "pose_presets": list_mateengine_pose_presets(),
        "notes": [
            "Host desktop pet control targets the local MateEngine model, not the Edge VTS model.",
            "High-level actions are translated to MateEngine bridge /command calls on 127.0.0.1:18770.",
            "Free-text say(text) still uses the NATSUME_TEXT:: MateEngine patch when the bridge forwards it.",
        ],
    }


@router.get("/health")
async def mateengine_health() -> dict[str, Any]:
    return get_mateengine_bridge_health().to_dict()


@router.post("/signal", response_model=MateEngineSignalResponse)
async def mateengine_signal(request: MateEngineSignalRequest) -> MateEngineSignalResponse:
    result = send_mateengine_signal(
        event_type=request.event_type,
        name=request.name,
        event_id=request.event_id,
        phase=request.phase,
        distance=request.distance,
        biome=request.biome,
    )
    return MateEngineSignalResponse(**result.to_dict())


@router.post("/say", response_model=MateEngineSignalResponse)
async def mateengine_say(request: MateEngineSayRequest) -> MateEngineSignalResponse:
    result = send_mateengine_say(
        text=request.text,
        emotion=request.emotion,
        ttl=request.ttl,
    )
    return MateEngineSignalResponse(**result.to_dict())


@router.post("/action")
async def mateengine_action(request: MateEngineActionRequest) -> dict[str, Any]:
    return send_mateengine_action(
        action=request.action,
        state=request.state,
        touch_type=request.touch_type,
        emotion_tag=request.emotion_tag,
        expression=request.expression,
        preset=request.preset,
        text=request.text,
        enabled=request.enabled,
        ttl=request.ttl,
        duration=request.duration,
        intensity=request.intensity,
        scale=request.scale,
        position_x=request.position_x,
        position_y=request.position_y,
        request_id=request.request_id,
    ).to_dict()
