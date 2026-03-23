"""Unified Host input route for ASR / WeChat events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from host_brain.core.ids import new_request_id

_logger = logging.getLogger("Natsume.input_routes")

router = APIRouter(prefix="/v1/host", tags=["input"])


class InputPayload(BaseModel):
    """Ingress payload reported by Edge."""

    source: str = Field(..., description="Input source: ASR or WECHAT")

    session_id: str | None = Field(None, description="[ASR] Session ID")
    text: str | None = Field(None, description="[ASR] Transcript")
    confidence: float | None = Field(None, description="[ASR] Confidence 0~1")
    is_final: bool | None = Field(None, description="[ASR] Final segment")
    seq: int | None = Field(None, description="[ASR] Sequence")
    alternates: list[dict[str, Any]] | None = Field(None, description="[ASR] N-best candidates")
    evidence_ref: str | None = Field(None, description="[ASR] Evidence ref")

    from_wxid: str | None = Field(None, description="[WECHAT] Sender")
    to_wxid: str | None = Field(None, description="[WECHAT] Receiver")
    content: str | None = Field(None, description="[WECHAT] Message content")
    msg_type: str | None = Field(None, description="[WECHAT] Message type")
    msg_id: str | None = Field(None, description="[WECHAT] Message ID")
    room_wxid: str | None = Field(None, description="[WECHAT] Room ID")
    raw_extra: dict[str, Any] | None = Field(None, description="[WECHAT] Extra fields")

    ts_ms: int | None = Field(None, description="Event timestamp in ms")


class InputResponse(BaseModel):
    """Ingress response."""

    success: bool
    request_id: str
    source: str
    status: str
    detail: str
    ts_ms: int


@dataclass
class _WeChatQueuedInput:
    request_id: str
    session_id: str
    text: str
    payload: dict[str, Any]
    ts_ms: int


@dataclass
class _WeChatLaneState:
    queue: list[_WeChatQueuedInput] = field(default_factory=list)
    running: bool = False
    task: asyncio.Task[None] | None = None
    last_active_mono: float = field(default_factory=time.monotonic)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(
    name: str,
    *,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = int(default)
    return max(min_value, min(max_value, parsed))


class _WeChatLaneManager:
    def __init__(self) -> None:
        self._lanes: dict[str, _WeChatLaneState] = {}
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        request_id: str,
        session_id: str,
        text: str,
        payload: dict[str, Any],
        ts_ms: int,
        ledger: Any,
    ) -> None:
        queued = _WeChatQueuedInput(
            request_id=str(request_id),
            session_id=str(session_id),
            text=str(text),
            payload=dict(payload),
            ts_ms=int(ts_ms),
        )
        async with self._lock:
            lane = self._lanes.get(session_id)
            if lane is None:
                lane = _WeChatLaneState()
                self._lanes[session_id] = lane
            lane.queue.append(queued)
            lane.last_active_mono = time.monotonic()
            queued_size = len(lane.queue)
            _log_lane_event(
                ledger=ledger,
                request_id=request_id,
                session_id=session_id,
                event_type="queue.lane.enqueue",
                detail=f"queued={queued_size} request_id={request_id}",
                payload={"queued_size": queued_size},
                ts_ms=int(time.time() * 1000),
            )
            if lane.task is None or lane.task.done():
                lane.task = asyncio.create_task(self._drain(session_id=session_id))

    async def _drain(self, *, session_id: str) -> None:
        debounce_ms = _parse_int_env(
            "HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS",
            default=1200,
            min_value=100,
            max_value=10000,
        )
        collect_cap = _parse_int_env(
            "HOST_INPUT_WECHAT_COLLECT_CAP",
            default=3,
            min_value=1,
            max_value=10,
        )
        debounce_s = float(debounce_ms) / 1000.0

        while True:
            await asyncio.sleep(debounce_s)

            async with self._lock:
                lane = self._lanes.get(session_id)
                if lane is None:
                    return
                if lane.running:
                    return
                if not lane.queue:
                    lane.task = None
                    self._maybe_cleanup_lane(session_id=session_id)
                    return

                lane.running = True
                batch = lane.queue[:collect_cap]
                lane.queue = lane.queue[collect_cap:]
                lane.last_active_mono = time.monotonic()
                pending_size = len(lane.queue)

            merged = self._merge_batch(batch=batch)
            if merged is None:
                async with self._lock:
                    lane = self._lanes.get(session_id)
                    if lane is None:
                        return
                    lane.running = False
                    if not lane.queue:
                        lane.task = None
                        self._maybe_cleanup_lane(session_id=session_id)
                        return
                continue

            _log_lane_event(
                ledger=merged["ledger"],
                request_id=merged["request_id"],
                session_id=session_id,
                event_type="queue.lane.dequeue",
                detail=(
                    f"batch={merged['merged_count']} pending={pending_size} "
                    f"request_id={merged['request_id']}"
                ),
                payload={
                    "batch_size": merged["merged_count"],
                    "pending_size": pending_size,
                    "request_ids": merged["request_ids"],
                },
                ts_ms=int(time.time() * 1000),
            )
            _log_lane_event(
                ledger=merged["ledger"],
                request_id=merged["request_id"],
                session_id=session_id,
                event_type="session.state",
                detail="state=running",
                payload={"state": "running"},
                ts_ms=int(time.time() * 1000),
            )
            try:
                await _dispatch_to_graph_worker(
                    request_id=merged["request_id"],
                    session_id=session_id,
                    source="WECHAT",
                    text=merged["text"],
                    payload=merged["payload"],
                    ts_ms=merged["ts_ms"],
                    ledger=merged["ledger"],
                    dispatch_mode="lane_collect",
                )
            finally:
                async with self._lock:
                    lane = self._lanes.get(session_id)
                    if lane is None:
                        return
                    lane.running = False
                    lane.last_active_mono = time.monotonic()
                    has_pending = bool(lane.queue)
                    if not has_pending:
                        lane.task = None
                        self._maybe_cleanup_lane(session_id=session_id)
                _log_lane_event(
                    ledger=merged["ledger"],
                    request_id=merged["request_id"],
                    session_id=session_id,
                    event_type="session.state",
                    detail=("state=idle" if not has_pending else "state=followup_pending"),
                    payload={"state": ("idle" if not has_pending else "followup_pending")},
                    ts_ms=int(time.time() * 1000),
                )
                if not has_pending:
                    return

    def _merge_batch(self, *, batch: list[_WeChatQueuedInput]) -> dict[str, Any] | None:
        if not batch:
            return None
        latest = batch[-1]
        merged_text_parts: list[str] = []
        request_ids: list[str] = []
        for row in batch:
            text_value = str(row.text or "").strip()
            if not text_value:
                continue
            request_ids.append(str(row.request_id))
            if merged_text_parts and merged_text_parts[-1] == text_value:
                continue
            merged_text_parts.append(text_value)
        merged_text = "\n".join(merged_text_parts).strip() or str(latest.text or "").strip()
        merged_payload = dict(latest.payload)
        merged_payload["content"] = merged_text
        merged_payload["ts_ms"] = int(latest.ts_ms)
        raw_extra = dict(merged_payload.get("raw_extra") or {})
        raw_extra["lane_mode"] = "collect"
        raw_extra["lane_merged_count"] = len(batch)
        if request_ids:
            raw_extra["lane_request_ids"] = request_ids[:10]
        merged_payload["raw_extra"] = raw_extra
        return {
            "request_id": str(latest.request_id),
            "request_ids": request_ids,
            "text": merged_text,
            "payload": merged_payload,
            "ts_ms": int(latest.ts_ms),
            "merged_count": len(batch),
            "ledger": _get_ledger_safe(),
        }

    def _maybe_cleanup_lane(self, *, session_id: str) -> None:
        lane = self._lanes.get(session_id)
        if lane is None:
            return
        if lane.running:
            return
        if lane.queue:
            return
        if lane.task is not None and not lane.task.done():
            return
        self._lanes.pop(session_id, None)


_WECHAT_LANE_MANAGER = _WeChatLaneManager()


@router.post("/input", response_model=InputResponse)
async def receive_input(body: InputPayload, request: Request) -> InputResponse:
    _ = request
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    source = str(body.source or "").upper().strip()

    _logger.info("input_routes: received edge input source=%s request_id=%s", source, request_id)

    if source == "ASR":
        if not body.text or not body.session_id:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="ASR input missing required fields: text, session_id",
                ts_ms=ts_ms,
            )
        text = str(body.text).strip()
        session_id = str(body.session_id).strip()
    elif source == "WECHAT":
        if not body.content or not body.from_wxid:
            return InputResponse(
                success=False,
                request_id=request_id,
                source=source,
                status="REJECTED",
                detail="WECHAT input missing required fields: content, from_wxid",
                ts_ms=ts_ms,
            )
        text = str(body.content).strip()
        session_id = str(body.from_wxid).strip()
    else:
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="REJECTED",
            detail=f"unsupported source: {source}, only ASR/WECHAT are accepted",
            ts_ms=ts_ms,
        )

    try:
        await _dispatch_to_graph(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=body.model_dump(exclude_none=True),
            ts_ms=ts_ms,
        )
        return InputResponse(
            success=True,
            request_id=request_id,
            source=source,
            status="ACCEPTED",
            detail="input accepted for processing",
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.error("input_routes: dispatch failed source=%s request_id=%s: %s", source, request_id, exc)
        return InputResponse(
            success=False,
            request_id=request_id,
            source=source,
            status="ERROR",
            detail=f"dispatch failed: {exc}",
            ts_ms=ts_ms,
        )


def _get_ledger_safe():
    try:
        from host_brain.app.dependencies import get_ledger

        return get_ledger()
    except Exception:
        return None


def _log_input_event(
    *,
    ledger,
    request_id: str,
    session_id: str,
    source: str,
    phase: str,
    status: str,
    detail: str = "",
    reason_code: str | None = None,
    latency_ms: int | None = None,
    ts_ms: int,
) -> None:
    if ledger is None:
        return
    try:
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": f"INPUT_{phase}",
                "direction": "EDGE_TO_HOST",
                "status": status,
                "reason_code": reason_code,
                "stage": f"input_dispatch.{phase.lower()}",
                "detail": detail[:400] if detail else None,
                "payload": {
                    "source": source,
                    "phase": phase,
                    "latency_ms": latency_ms,
                },
                "ts_ms": ts_ms,
            }
        )
    except Exception as log_exc:
        _logger.debug("input_routes: ledger log failed (non-blocking): %s", log_exc)


def _log_lane_event(
    *,
    ledger,
    request_id: str,
    session_id: str,
    event_type: str,
    detail: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> None:
    normalized = str(event_type or "").strip().replace(".", "_").upper() or "QUEUE_EVENT"
    _logger.info("input_routes lane: event=%s session_id=%s detail=%s", event_type, session_id, detail)
    if ledger is None:
        return
    try:
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": normalized,
                "direction": "EDGE_TO_HOST",
                "status": "OK",
                "reason_code": None,
                "stage": f"input_dispatch.{str(event_type or 'queue').replace('.', '_')}",
                "detail": str(detail)[:400],
                "payload": dict(payload),
                "ts_ms": int(ts_ms),
            }
        )
    except Exception as log_exc:
        _logger.debug("input_routes: lane event log failed (non-blocking): %s", log_exc)


async def _dispatch_to_graph(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
) -> None:
    ledger = _get_ledger_safe()

    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        phase="ACCEPTED",
        status="ACCEPTED",
        detail=f"source={source} text_len={len(text)}",
        ts_ms=ts_ms,
    )

    lane_enabled = (
        source == "WECHAT"
        and _parse_bool_env("HOST_INPUT_WECHAT_SESSION_LANE_ENABLED", default=False)
    )
    if lane_enabled:
        await _WECHAT_LANE_MANAGER.enqueue(
            request_id=request_id,
            session_id=session_id,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
            ledger=ledger,
        )
        return

    asyncio.create_task(
        _dispatch_to_graph_worker(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
            ledger=ledger,
            dispatch_mode="fire_and_forget",
        )
    )


async def _dispatch_to_graph_worker(
    *,
    request_id: str,
    session_id: str,
    source: str,
    text: str,
    payload: dict[str, Any],
    ts_ms: int,
    ledger: Any,
    dispatch_mode: str,
) -> None:
    dispatch_ts = int(time.time() * 1000)
    _log_input_event(
        ledger=ledger,
        request_id=request_id,
        session_id=session_id,
        source=source,
        phase="DISPATCHED",
        status="DISPATCHED",
        detail=f"dispatch_mode={dispatch_mode} source={source}",
        ts_ms=dispatch_ts,
    )
    try:
        from host_brain.app.graph.main_chain import run_main_chain_from_input

        await run_main_chain_from_input(
            request_id=request_id,
            session_id=session_id,
            source=source,
            text=text,
            payload=payload,
            ts_ms=ts_ms,
        )
        complete_ts = int(time.time() * 1000)
        latency_ms = complete_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            phase="COMPLETED",
            status="OK",
            detail=f"main_chain completed source={source} latency_ms={latency_ms}",
            latency_ms=latency_ms,
            ts_ms=complete_ts,
        )
        _logger.info(
            "input_routes: graph completed request_id=%s source=%s latency_ms=%d mode=%s",
            request_id,
            source,
            latency_ms,
            dispatch_mode,
        )
    except ImportError:
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            phase="NOT_WIRED",
            status="DEGRADED",
            reason_code="INPUT_MAIN_CHAIN_NOT_WIRED",
            detail="run_main_chain_from_input not implemented",
            ts_ms=int(time.time() * 1000),
        )
        _logger.info(
            "input_routes: main_chain.run_main_chain_from_input not implemented, request_id=%s source=%s text=%.60s",
            request_id,
            source,
            text,
        )
    except Exception as exc:
        fail_ts = int(time.time() * 1000)
        latency_ms = fail_ts - dispatch_ts
        _log_input_event(
            ledger=ledger,
            request_id=request_id,
            session_id=session_id,
            source=source,
            phase="FAILED",
            status="FAILED",
            reason_code="INPUT_GRAPH_ERROR",
            detail=f"{type(exc).__name__}: {str(exc)[:200]}",
            latency_ms=latency_ms,
            ts_ms=fail_ts,
        )
        _logger.error("input_routes: graph failed request_id=%s: %s", request_id, exc)
