"""System route handlers extracted from fastapi_app."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from fastapi import HTTPException, status

from host_brain.app.config import build_host_runtime_statuses, load_host_config
from host_brain.app.dependencies import collect_dependency_probes_sync
from host_brain.app.main import (
    build_env_readiness_report,
    build_request_observability_report,
    build_service_health_report,
    build_startup_report,
    emit_runtime_bootstrap_logs,
)
from host_brain.app.schemas import (
    EdgeEventsRequest,
    EdgeEventsResponse,
    HealthResponse,
    ObservabilityRequest,
    ObservabilityResponse,
    ReadinessResponse,
)
from host_brain.core.errors import (
    CANON_SYNC_FAILED,
    RPC_EVENT_BATCH_PARTIAL_FAIL,
    RPC_EVENT_SCHEMA_INVALID,
    STARTUP_MCP_DEGRADED,
)
from host_brain.core.ids import new_request_id
from host_brain.mcp.tool_registry import MCPToolRegistry
from host_brain.memory.canon_sync import detect_world_version_drift
from host_brain.bond.user_identity import resolve_bond_user_id
from host_brain.observability.routes import build_obs_metrics_response

ALLOWED_EDGE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "ASR_CHUNK",
        "ASR_FINAL",
        "ASR_PARTIAL",
        "ASR_RESULT",
        "EMOTION_DETECTED",
        "USER_REACTION",
        "WECHAT_MSG_RECEIVED",
        "USER_INPUT_FINAL",
        "SPEAK",
        "BARGE_IN",
        "TTS_PLAY",
        "TTS_SYNTH",
        "TTS_STARTED",
        "TTS_DONE",
        "TTS_QUEUED",
        "TTS_SYNTH_READY",
        "TTS_SYNTH_FAILED",
        "TTS_PLAYBACK_STARTED",
        "TTS_PLAYBACK_FINISHED",
        "TTS_PLAYBACK_INTERRUPTED",
        "VTS_EXPRESSION",
        "VTS_EXPRESSION_SET",
        "FALLBACK_PRESENCE",
        "FALLBACK_ASR_COMPILE",
        "FALLBACK_RETRIEVAL_HINTS",
        "FALLBACK_CONTEXT_COMPRESS",
        "VOICE_RESOURCE_GATE",
        "EDGE_HEALTH_PING",
    }
)

ALLOWED_EDGE_EVENT_SOURCES: frozenset[str] = frozenset({"edge", "edge_client", "host_edge_bridge"})

_EDGE_ASR_BRIDGE_EVENT_TYPES: frozenset[str] = frozenset(
    {"ASR_CHUNK", "ASR_FINAL", "ASR_RESULT", "USER_INPUT_FINAL"}
)
_EDGE_ASR_BRIDGE_FINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"ASR_FINAL", "ASR_RESULT", "USER_INPUT_FINAL"}
)
_EDGE_ASR_BRIDGE_PLACEHOLDER_TEXTS: frozenset[str] = frozenset({"[ASR_VAD_NO_SPEECH]"})
_EDGE_ASR_BRIDGE_RECENT: dict[str, int] = {}
_EDGE_ASR_BRIDGE_RECENT_LOCK = threading.Lock()
# 同 session_id 下最近一次 bridge 的文本和时间戳（用于文本相似度去重）
_EDGE_ASR_BRIDGE_LAST_TEXT: dict[str, tuple[str, int]] = {}  # session_id -> (text, ts_ms)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_float_env(name: str, *, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _parse_int_env(name: str, *, default: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _log_if_available(logger: Any, level: str, message: str, *args: Any) -> None:
    log_fn = getattr(logger, level, None)
    if callable(log_fn):
        log_fn(message, *args)


def _extract_edge_asr_bridge_candidate(
    *,
    raw: Mapping[str, Any],
    normalized_event_type: str,
) -> dict[str, Any] | None:
    if not _parse_bool_env("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_ENABLED", default=True):
        return None
    if normalized_event_type not in _EDGE_ASR_BRIDGE_EVENT_TYPES:
        return None

    payload_raw = raw.get("payload")
    payload = dict(payload_raw) if isinstance(payload_raw, Mapping) else {}
    text = str(
        payload.get("text")
        or raw.get("text")
        or payload.get("content")
        or raw.get("content")
        or ""
    ).strip()
    if not text:
        return None
    normalized_text = text.strip().upper()
    if normalized_text in _EDGE_ASR_BRIDGE_PLACEHOLDER_TEXTS:
        return None

    reason_code = str(payload.get("reason_code") or raw.get("reason_code") or "").strip().upper()
    if reason_code == "ASR_VAD_NO_SPEECH":
        return None

    confidence = _coerce_float(payload.get("confidence"))
    if confidence is None:
        confidence = _coerce_float(raw.get("confidence"))

    is_final = _coerce_bool(
        payload.get("is_final", payload.get("final", raw.get("is_final", raw.get("final")))),
        default=(normalized_event_type in _EDGE_ASR_BRIDGE_FINAL_EVENT_TYPES),
    )
    if not is_final:
        return None

    request_id = str(raw.get("request_id") or "").strip()
    session_id = str(raw.get("session_id") or "").strip()
    if not request_id or not session_id:
        return None

    seq_raw = payload.get("seq", raw.get("seq"))
    try:
        seq_value = int(seq_raw) if seq_raw is not None else None
    except (TypeError, ValueError):
        seq_value = None

    ts_raw = raw.get("ts_ms")
    try:
        ts_ms = int(ts_raw) if ts_raw is not None else int(time.time() * 1000)
    except (TypeError, ValueError):
        ts_ms = int(time.time() * 1000)

    bridge_payload = dict(payload)
    bridge_payload["session_id"] = session_id
    bridge_payload["text"] = text
    bridge_payload["is_final"] = is_final
    bridge_payload["final"] = is_final
    bridge_payload["edge_event_type"] = normalized_event_type
    bridge_payload["edge_source"] = str(raw.get("source") or "edge").strip().lower() or "edge"
    bridge_payload["edge_request_id"] = request_id
    bridge_payload["edge_ts_ms"] = ts_ms
    if confidence is not None:
        bridge_payload["confidence"] = confidence
    if seq_value is not None:
        bridge_payload["seq"] = seq_value
    if "emotion_tag" not in bridge_payload and raw.get("emotion_tag") is not None:
        bridge_payload["emotion_tag"] = raw.get("emotion_tag")
    if "emotion_intensity" not in bridge_payload and raw.get("emotion_intensity") is not None:
        bridge_payload["emotion_intensity"] = raw.get("emotion_intensity")

    return {
        "request_id": request_id,
        "session_id": session_id,
        "text": text,
        "ts_ms": ts_ms,
        "event_type": normalized_event_type,
        "payload": bridge_payload,
    }


def _schedule_edge_asr_bridge(*, candidate: Mapping[str, Any], logger: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log_if_available(
            logger,
            "debug",
            "edge event ASR bridge skipped: no running loop request_id=%s",
            candidate.get("request_id"),
        )
        return

    request_id = str(candidate.get("request_id") or "").strip()
    session_id = str(candidate.get("session_id") or "").strip()
    text = str(candidate.get("text") or "").strip()
    payload = candidate.get("payload")
    ts_ms_raw = candidate.get("ts_ms")
    if not request_id or not session_id or not text or not isinstance(payload, Mapping):
        return
    try:
        ts_ms = int(ts_ms_raw) if ts_ms_raw is not None else int(time.time() * 1000)
    except (TypeError, ValueError):
        ts_ms = int(time.time() * 1000)

    dedup_window_ms = max(
        0,
        _parse_int_env("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", default=15000),
    )
    if dedup_window_ms > 0:
        dedup_key = f"{session_id}::{text.casefold()}"
        with _EDGE_ASR_BRIDGE_RECENT_LOCK:
            cutoff = ts_ms - dedup_window_ms
            stale_keys = [key for key, seen_at in _EDGE_ASR_BRIDGE_RECENT.items() if seen_at < cutoff]
            for stale_key in stale_keys:
                _EDGE_ASR_BRIDGE_RECENT.pop(stale_key, None)
            # 精确文本去重
            last_seen = _EDGE_ASR_BRIDGE_RECENT.get(dedup_key)
            if last_seen is not None and (ts_ms - last_seen) <= dedup_window_ms:
                _log_if_available(
                    logger,
                    "debug",
                    "edge event ASR bridge deduped (exact) request_id=%s session_id=%s",
                    request_id,
                    session_id,
                )
                return
            # 同 session_id 文本相似度去重：如果上一次 bridge 的文本与本次相同或为子串关系，
            # 且时间差在窗口内，视为重复（防止 ASR 反复识别同一段语音产生噪声链路）
            last_entry = _EDGE_ASR_BRIDGE_LAST_TEXT.get(session_id)
            if last_entry is not None:
                last_text, last_ts = last_entry
                if (ts_ms - last_ts) <= dedup_window_ms:
                    cur = text.casefold()
                    prev = last_text.casefold()
                    if cur == prev or cur in prev or prev in cur:
                        _log_if_available(
                            logger,
                            "debug",
                            "edge event ASR bridge deduped (similar) request_id=%s session_id=%s text=%.40s prev=%.40s",
                            request_id,
                            session_id,
                            text,
                            last_text,
                        )
                        return
            _EDGE_ASR_BRIDGE_RECENT[dedup_key] = ts_ms
            _EDGE_ASR_BRIDGE_LAST_TEXT[session_id] = (text, ts_ms)

    async def _dispatch() -> None:
        try:
            from host_brain.app.input_routes import _dispatch_to_graph

            await _dispatch_to_graph(
                request_id=request_id,
                session_id=session_id,
                source="ASR",
                text=text,
                payload=dict(payload),
                ts_ms=ts_ms,
            )
        except Exception as exc:
            _log_if_available(
                logger,
                "warning",
                "edge event ASR bridge failed request_id=%s: %s",
                request_id,
                exc,
            )

    loop.create_task(_dispatch())


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            loaded = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def _resolve_node_inventory_payload() -> tuple[list[dict[str, Any]], int | None, str | None]:
    configured = str(os.environ.get("HOST_NODE_INVENTORY_PATH") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path("configs/deploy/node_inventory_9.json"),
            Path("tests/fixtures/services/good/node_inventory_9.json"),
        ]
    )
    for path in candidates:
        payload = _load_json_file(path)
        if payload is None:
            continue
        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list):
            continue
        nodes: list[dict[str, Any]] = [dict(row) for row in raw_nodes if isinstance(row, dict)]
        expected = payload.get("expected_node_count")
        expected_count: int | None = None
        if isinstance(expected, int):
            expected_count = int(expected)
        elif expected is not None:
            try:
                expected_count = int(str(expected).strip())
            except (TypeError, ValueError):
                expected_count = None
        return nodes, expected_count, str(path)
    return [], None, None


def health_check_response(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> HealthResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        host_result = load_host_config()
        runtime_statuses: dict[str, Any] = {}
        if host_result.success and host_result.config is not None:
            runtime_statuses = build_host_runtime_statuses(host_result.config)
        try:
            runtime_statuses.update(collect_dependency_probes_sync())
        except Exception as edge_exc:
            logger.warning("Edge transport probe failed (degraded): %s", edge_exc)
        health_report = build_service_health_report(
            runtime_statuses=runtime_statuses,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        return HealthResponse(
            status=health_report.get("status", "OK"),
            version="0.1.0",
            timestamp=ts_ms,
            details=health_report,
        )
    except Exception as exc:
        logger.error("Health check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service health check failed",
        )


def environment_readiness_response(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> ReadinessResponse:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        node_inventory, expected_node_count, inventory_path = _resolve_node_inventory_payload()
        dependency_probes = collect_dependency_probes_sync()
        readiness_report = build_env_readiness_report(
            env=str(os.environ.get("Natsume_ENV") or "dev"),
            node_inventory=node_inventory,
            expected_node_count=expected_node_count,
            dependency_probes=dependency_probes,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        if inventory_path:
            readiness_report["node_inventory_path"] = inventory_path
        readiness_status = readiness_report.get("readiness_status", "FAILED")
        return ReadinessResponse(
            ready=(readiness_status == "READY"),
            status=readiness_status,
            reason_code=readiness_report.get("reason_code"),
            timestamp=ts_ms,
            summary=readiness_report,
        )
    except Exception as exc:
        logger.error("Readiness check failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Environment readiness check failed",
        )


def obs_metrics_payload(
    *,
    collector: Any,
    request_id: str | None,
) -> dict[str, Any]:
    ts_ms = int(time.time() * 1000)
    payload = build_obs_metrics_response(
        collector=collector,
        request_id=request_id,
        ts_ms=ts_ms,
    )
    payload["timestamp"] = ts_ms
    return payload


def mcp_health_check_response() -> HealthResponse:
    ts_ms = int(time.time() * 1000)
    db_path = str(os.environ.get("HOST_MCP_REGISTRY_DB") or "host_brain/data/mcp_registry.sqlite3").strip()
    status_text = "OK"
    reason_code = None
    detail = "mcp registry reachable"
    tool_count = 0
    registry: MCPToolRegistry | None = None
    try:
        registry = MCPToolRegistry(db_path=db_path, check_same_thread=False)
        tool_count = len(registry.list_all())
        if tool_count == 0:
            status_text = "DEGRADED"
            reason_code = STARTUP_MCP_DEGRADED
            detail = "mcp registry reachable but no tools registered"
    except Exception as exc:
        status_text = "DEGRADED"
        reason_code = STARTUP_MCP_DEGRADED
        detail = str(exc)
    finally:
        if registry is not None:
            registry.close()
    return HealthResponse(
        status=status_text,
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "db_path": db_path,
            "tool_count": tool_count,
            "reason_code": reason_code,
            "detail": detail,
        },
    )


def canon_sync_health_check_response(*, get_ledger: Callable[[], Any]) -> HealthResponse:
    ts_ms = int(time.time() * 1000)
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip()
    status_text = "OK"
    reason_code = None
    detail = "canon sync healthy"
    drift_count = 0
    drift_sample: list[dict[str, Any]] = []
    try:
        drifts = detect_world_version_drift(world_root=world_root, ledger=get_ledger())
        drift_count = len(drifts)
        if drift_count > 0:
            status_text = "DEGRADED"
            reason_code = CANON_SYNC_FAILED
            detail = "world canon version drift detected"
            drift_sample = [dict(row) for row in drifts[:5]]
    except Exception as exc:
        status_text = "DEGRADED"
        reason_code = CANON_SYNC_FAILED
        detail = str(exc)
    return HealthResponse(
        status=status_text,
        version="0.1.0",
        timestamp=ts_ms,
        details={
            "world_root": world_root,
            "drift_count": drift_count,
            "drift_sample": drift_sample,
            "reason_code": reason_code,
            "detail": detail,
        },
    )


def request_trace_response(
    *,
    request_body: ObservabilityRequest,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> ObservabilityResponse:
    ts_ms = int(time.time() * 1000)
    try:
        trace_report = build_request_observability_report(
            request_id=request_body.request_id,
            ledger=get_ledger(),
        )
        return ObservabilityResponse(
            request_id=request_body.request_id,
            trace_status=trace_report.get("trace_status", "UNKNOWN"),
            timeline_total=trace_report.get("timeline_total", 0),
            sources=trace_report.get("sources", {}),
            timeline=trace_report.get("timeline", []),
            timestamp=ts_ms,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error("Request trace failed", extra={"request_id": request_body.request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve request trace",
        )


def startup_report_payload(
    *,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> dict[str, Any]:
    request_id = new_request_id()
    ts_ms = int(time.time() * 1000)
    try:
        dependency_probes = collect_dependency_probes_sync()
        report = build_startup_report(
            runtime_statuses={},
            dependency_probes=dependency_probes,
            request_id=request_id,
            ledger=get_ledger(),
            ts_ms=ts_ms,
        )
        emit_runtime_bootstrap_logs(startup_report=report)
        return {
            "request_id": request_id,
            "timestamp": ts_ms,
            "report": report,
        }
    except Exception as exc:
        logger.error("Startup report failed", extra={"request_id": request_id, "error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate startup report",
        )


def receive_edge_events_response(
    *,
    request_body: EdgeEventsRequest,
    get_ledger: Callable[[], Any],
    logger: Any,
) -> EdgeEventsResponse:
    ts_ms = int(time.time() * 1000)
    raw_events = request_body.events
    if not raw_events:
        return EdgeEventsResponse(
            success=True, total=0, accepted=0, rejected=0,
            detail="empty batch", timestamp=ts_ms,
        )

    required_fields = {"event_type", "ts_ms", "request_id", "session_id"}
    accepted_count = 0
    rejected_indices: list[int] = []
    ledger = get_ledger()

    # 收集情感相关事件，用于后续 Bond 更新和 VTS 驱动
    # 修复问题B：Edge 事件上报链路打通——情感事件触发 Bond 更新和 VTS 驱动
    _emotion_events: list[dict[str, Any]] = []
    bridge_candidates: list[dict[str, Any]] = []

    for idx, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            rejected_indices.append(idx)
            continue
        missing = required_fields - set(raw.keys())
        event_type = str(raw.get("event_type") or "").strip()
        request_id = str(raw.get("request_id") or "").strip()
        session_id = str(raw.get("session_id") or "").strip()
        source = str(raw.get("source") or "edge").strip().lower()
        ts_val = raw.get("ts_ms")
        if missing or not event_type or not request_id or not session_id or not isinstance(ts_val, (int, float)):
            rejected_indices.append(idx)
            continue
        if source not in ALLOWED_EDGE_EVENT_SOURCES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown edge event source: {source}",
            )
        normalized_event_type = event_type.upper()
        if normalized_event_type not in ALLOWED_EDGE_EVENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown edge event_type: {event_type}",
            )
        try:
            ledger.log_voice_event({
                "request_id": request_id,
                "session_id": session_id,
                "event_type": normalized_event_type,
                "direction": str(raw.get("direction") or "EDGE_TO_HOST"),
                "seq": raw.get("seq") if isinstance(raw.get("seq"), int) else None,
                "status": str(raw.get("status") or "RECEIVED"),
                "reason_code": str(raw.get("reason_code") or "").strip() or None,
                "stage": str(raw.get("stage") or "").strip() or None,
                "detail": str(raw.get("detail") or "").strip() or None,
                "payload": raw.get("payload") or {},
                "ts_ms": int(ts_val),
            })
            accepted_count += 1

            bridge_candidate = _extract_edge_asr_bridge_candidate(
                raw=raw,
                normalized_event_type=normalized_event_type,
            )
            if bridge_candidate is not None:
                bridge_candidates.append(bridge_candidate)

            # 收集情感相关事件（ASR_FINAL / EMOTION_DETECTED / USER_REACTION）
            if normalized_event_type in {
                "ASR_FINAL", "EMOTION_DETECTED", "USER_REACTION",
                "WECHAT_MSG_RECEIVED", "USER_INPUT_FINAL",
            }:
                payload_inner = raw.get("payload") or {}
                emotion_tag = str(
                    (payload_inner.get("emotion_tag") if isinstance(payload_inner, dict) else None)
                    or raw.get("emotion_tag")
                    or "NEUTRAL"
                ).strip().upper() or "NEUTRAL"
                try:
                    emotion_intensity = float(
                        (payload_inner.get("emotion_intensity") if isinstance(payload_inner, dict) else None)
                        or raw.get("emotion_intensity")
                        or 0.5
                    )
                except (TypeError, ValueError):
                    emotion_intensity = 0.5
                emotion_intensity = max(0.0, min(1.0, emotion_intensity))
                text = str(
                    (payload_inner.get("text") if isinstance(payload_inner, dict) else None)
                    or raw.get("text")
                    or ""
                ).strip()
                _emotion_events.append({
                    "request_id": request_id,
                    "session_id": session_id,
                    "event_type": normalized_event_type,
                    "emotion_tag": emotion_tag,
                    "emotion_intensity": emotion_intensity,
                    "text": text,
                    "ts_ms": int(ts_val),
                })
        except Exception as exc:
            logger.warning("Failed to write edge event %d: %s", idx, exc)
            rejected_indices.append(idx)

    # 情感事件后处理：Bond 更新 + VTS 驱动（fire-and-forget，不阻塞响应）
    if _emotion_events:
        try:
            import asyncio as _asyncio
            from host_brain.app.dependencies import (
                get_bond_stage_engine,
                get_bond_update_queue,
            )
            from host_brain.bond.bond_dynamics import BondDynamicsWriter

            for ev in _emotion_events:
                _ev_request_id = ev["request_id"]
                _ev_session_id = ev["session_id"]
                _ev_emotion_tag = ev["emotion_tag"]
                _ev_emotion_intensity = ev["emotion_intensity"]
                _ev_text = ev["text"]
                _ev_ts_ms = ev["ts_ms"]

                # Bond 更新
                try:
                    _update_kind = BondDynamicsWriter.classify_interaction(
                        text=_ev_text,
                        emotion_intensity=_ev_emotion_intensity,
                    )
                    _bond_payload = {
                        "request_id": _ev_request_id,
                        "intent_id": _ev_request_id,
                        "session_id": _ev_session_id,
                        "seq": 1,
                        "user_id": resolve_bond_user_id(session_id=_ev_session_id),
                        "topic_id": _ev_text[:64],
                        "capability_hint": "edge_event_bond",
                        "update_kind": _update_kind,
                        "evidence_refs": [_ev_request_id],
                        "emotion_tag": _ev_emotion_tag,
                        "emotion_intensity": _ev_emotion_intensity,
                        "ts_ms": _ev_ts_ms,
                    }
                    _bond_queue = get_bond_update_queue()
                    _queue_result = _bond_queue.enqueue(_bond_payload)
                    if str(_queue_result.get("status") or "") != "QUEUED":
                        _bond_writer = BondDynamicsWriter(
                            ledger=ledger,
                            stage_engine=get_bond_stage_engine(),
                        )
                        _bond_writer.apply_update(**_bond_payload)
                except Exception as bond_exc:
                    logger.debug(
                        "edge event bond update skipped: %s (request_id=%s)",
                        bond_exc, _ev_request_id,
                    )

                # VTS 情感驱动（仅当 HOST_STREAM_VTS_EMOTION_ENABLED=true）
                _vts_enabled = str(os.environ.get("HOST_STREAM_VTS_EMOTION_ENABLED") or "true").strip().lower()
                if _vts_enabled in {"1", "true", "yes", "on"}:
                    try:
                        from host_brain.app.chat_stream_handler import _drive_stream_vts_emotion  # noqa: PLC0415

                        async def _fire_vts(req_id: str, etag: str, intensity: float, ev_ts: int) -> None:
                            try:
                                await _drive_stream_vts_emotion(
                                    request_id=req_id,
                                    emotion_context={
                                        "emotion_tag": etag,
                                        "emotion_intensity": intensity,
                                    },
                                    ts_ms=ev_ts,
                                )
                            except Exception as vts_exc:
                                logger.debug(
                                    "edge event VTS drive skipped: %s (request_id=%s)",
                                    vts_exc, req_id,
                                )

                        try:
                            loop = _asyncio.get_running_loop()
                            if loop.is_running():
                                _asyncio.ensure_future(
                                    _fire_vts(
                                        _ev_request_id,
                                        _ev_emotion_tag,
                                        _ev_emotion_intensity,
                                        _ev_ts_ms,
                                    )
                                )
                        except RuntimeError:
                            pass  # 无事件循环时跳过 VTS 驱动
                    except ImportError:
                        pass
        except Exception as post_exc:
            logger.warning("edge event post-processing failed: %s", post_exc)

    for candidate in bridge_candidates:
        _schedule_edge_asr_bridge(candidate=candidate, logger=logger)

    rejected_count = len(rejected_indices)
    total = len(raw_events)
    if rejected_count == 0:
        reason_code = None
        detail = f"all {total} events accepted"
    elif accepted_count == 0:
        reason_code = RPC_EVENT_SCHEMA_INVALID
        detail = f"all {total} events rejected"
    else:
        reason_code = RPC_EVENT_BATCH_PARTIAL_FAIL
        detail = f"accepted {accepted_count}, rejected {rejected_count} of {total}"
    return EdgeEventsResponse(
        success=(rejected_count == 0),
        total=total,
        accepted=accepted_count,
        rejected=rejected_count,
        reason_code=reason_code,
        detail=detail,
        rejected_indices=rejected_indices,
        timestamp=ts_ms,
    )
