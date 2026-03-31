"""obs_routes_platform.py — 平台统计 + WebSocket（从 obs_routes.py 拆分）。

包含：failure-events / gateway/stats / skills-hub/stats / ws/health
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from host_brain.audit.sqlite_ledger import SQLiteLedger

logger = logging.getLogger("Natsume.obs")


from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import asyncio

from .obs_routes_core import router, _get_ledger, _now_ms, _safe_json


# ─────────────────────────────────────────────────────────────────────────────
# Ref #115: GET /obs/failure-events — 失败事件统计（按事件类型分组）
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/failure-events")
async def get_failure_events(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计时间窗口（分钟，最大 7 天）"),
    event_type: Optional[str] = Query(None, description="按事件类型过滤（如 PROMPT_INJECTION / GOVERNOR_BLOCK）"),
    limit: int = Query(50, ge=1, le=200, description="返回条数上限"),
    ledger: "SQLiteLedger" = Depends(_get_ledger),
):
    """
    Ref #115: 失败事件统计端点。

    从 audit_log 中按 audit_tag 聚合失败事件，返回：
    - 各事件类型的发生次数
    - 最近 N 条失败事件详情
    - 时间窗口内的总失败次数
    """
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # 所有已知失败事件类型（对应 FailureEvent 枚举）
    KNOWN_FAILURE_EVENTS = [
        "GOVERNOR_BLOCK", "EVIDENCE_EMPTY", "MODEL_FALLBACK",
        "QA_REWRITE", "EDGE_UNREACHABLE", "VISION_LOW_CONFIDENCE",
        "MODULE_TIMEOUT", "PROMPT_INJECTION",
    ]

    try:
        # 使用 SQLiteLedger 实际存在的 fetch_by_time_range 方法查询 error_log
        # error_log 表的 reason_code 字段对应 FailureEvent 枚举值
        all_rows = ledger.fetch_by_time_range(
            "error_log",
            start_ms=since_ms,
            end_ms=now,
            limit=limit * 3,  # 多取一些，过滤后再截断
        )

        # 按 reason_code 过滤（对应 FailureEvent 枚举值）
        target_codes = set([event_type] if event_type else KNOWN_FAILURE_EVENTS)
        filtered_rows = [
            row for row in all_rows
            if row.get("reason_code", "") in target_codes
        ]

        # 按事件类型聚合
        event_counts: dict = {}
        recent_events = []
        for row in filtered_rows:
            tag = row.get("reason_code", "UNKNOWN")
            event_counts[tag] = event_counts.get(tag, 0) + 1
            if len(recent_events) < limit:
                recent_events.append({
                    "ts_ms": row.get("ts_ms", 0),
                    "event_type": tag,
                    "request_id": row.get("request_id", ""),
                    "stage": row.get("stage", ""),
                    "detail": str(row.get("detail", ""))[:200],
                })

        total_failures = sum(event_counts.values())

        return {
            "window_minutes": window_minutes,
            "total_failures": total_failures,
            "event_counts": event_counts,
            "recent_events": recent_events,
            "timestamp": now,
            "status": "OK",
        }

    except Exception as exc:
        logger.warning("get_failure_events: 查询失败: %s", exc)
        return {
            "window_minutes": window_minutes,
            "total_failures": 0,
            "event_counts": {},
            "recent_events": [],
            "timestamp": now,
            "status": f"DEGRADED: {exc}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Gateway 六平台消息统计 — GET /obs/gateway/stats
# ─────────────────────────────────────────────────────────────────────────────

class GatewayPlatformStats(BaseModel):
    """单个平台的统计数据。"""
    platform: str
    status: str = "UNKNOWN"  # UP / DEGRADED / DOWN / UNKNOWN
    status_detail: Optional[str] = None
    inbound_count: int = 0
    outbound_count: int = 0
    error_count: int = 0
    avg_latency_ms: float = 0.0
    last_event_ts_ms: Optional[int] = None
    bridge_status: Optional[str] = None  # wxautox4 bridge 特有
    call_active: int = 0  # 当前活跃通话数（仅微信）

class GatewayStatsResponse(BaseModel):
    platforms: List[GatewayPlatformStats] = []
    total_inbound: int = 0
    total_outbound: int = 0
    total_errors: int = 0
    humanize_enabled: bool = True
    humanize_typing_speed_cps: int = 12
    window_minutes: int = 60
    timestamp: int = 0


_GATEWAY_REMOTE_HEALTH_CACHE: Dict[str, Any] = {
    "expires_at_ms": 0,
    "value": {},
}


def _normalize_gateway_platform_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"ok", "up", "healthy", "ready", "online", "active"}:
        return "UP"
    if normalized in {"degraded", "warn", "warning", "partial"}:
        return "DEGRADED"
    if normalized in {"error", "down", "failed", "fail", "offline"}:
        return "DOWN"
    return "UNKNOWN"


def _merge_gateway_platform_status(local_status: str, remote_status: str) -> str:
    priority = {
        "UNKNOWN": 0,
        "UP": 1,
        "DEGRADED": 2,
        "DOWN": 3,
    }
    left = str(local_status or "UNKNOWN").strip().upper() or "UNKNOWN"
    right = str(remote_status or "UNKNOWN").strip().upper() or "UNKNOWN"
    return left if priority.get(left, 0) >= priority.get(right, 0) else right


def _coerce_dict_payload(value: Any) -> Dict[str, Any]:
    payload = _safe_json(value)
    return dict(payload) if isinstance(payload, dict) else {}


def _infer_gateway_platform_from_row(*, session_id: Any, payload: Any, detail: Any) -> str | None:
    payload_dict = _coerce_dict_payload(payload)
    platform = str(payload_dict.get("platform") or "").strip().lower()
    if platform in {"wechat", "qq", "telegram", "feishu", "wecom", "line"}:
        return platform

    normalized_session = str(session_id or "").strip().lower()
    if normalized_session.startswith("qq_"):
        return "qq"
    if normalized_session.startswith("wechat_") or normalized_session.startswith("wx_"):
        return "wechat"
    if normalized_session.startswith("telegram_") or normalized_session.startswith("tg_"):
        return "telegram"
    if normalized_session.startswith("feishu_"):
        return "feishu"
    if normalized_session.startswith("wecom_"):
        return "wecom"
    if normalized_session.startswith("line_"):
        return "line"

    detail_text = str(detail or "").strip().lower()
    for candidate in ("wechat", "qq", "telegram", "feishu", "wecom", "line"):
        if f"platform={candidate}" in detail_text:
            return candidate
    return None


async def _fetch_remote_gateway_health() -> Dict[str, Any]:
    cached = _GATEWAY_REMOTE_HEALTH_CACHE.get("value")
    expires_at_ms = int(_GATEWAY_REMOTE_HEALTH_CACHE.get("expires_at_ms") or 0)
    now = _now_ms()
    if expires_at_ms > now and isinstance(cached, dict) and cached:
        return dict(cached)

    stale = dict(cached) if isinstance(cached, dict) else {}
    try:
        import httpx
        from host_brain.channels.edge_rpc_client import _resolve_gateway_base_urls
    except Exception:
        return stale

    timeout_ms_raw = str(os.environ.get("HOST_EDGE_RPC_TIMEOUT_MS") or "3000").strip()
    try:
        timeout_ms = int(timeout_ms_raw)
    except ValueError:
        timeout_ms = 3000
    timeout_s = max(0.5, min(float(timeout_ms) / 1000.0, 5.0))

    for base_url in _resolve_gateway_base_urls():
        url = f"{base_url}/v1/gateway/health"
        try:
            async with httpx.AsyncClient(timeout=timeout_s, trust_env=False) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                continue
            body = resp.json()
            if not isinstance(body, dict):
                continue
            snapshot = {
                "base_url": base_url,
                "body": body,
            }
            _GATEWAY_REMOTE_HEALTH_CACHE["value"] = dict(snapshot)
            _GATEWAY_REMOTE_HEALTH_CACHE["expires_at_ms"] = now + 3_000
            return snapshot
        except Exception as exc:
            logger.debug("get_gateway_stats: gateway health probe failed url=%s error=%s", url, exc)

    return stale


def _resolve_remote_gateway_health_fetcher():
    try:
        import host_brain.app.obs_routes as obs_routes_module
    except Exception:
        return _fetch_remote_gateway_health
    fetcher = getattr(obs_routes_module, "_fetch_remote_gateway_health", None)
    return fetcher if callable(fetcher) else _fetch_remote_gateway_health


@router.get("/gateway/stats", response_model=GatewayStatsResponse)
async def get_gateway_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
    ledger: "SQLiteLedger" = Depends(_get_ledger),
):
    """
    Gateway 六平台消息统计端点。

    聚合各平台的消息收发量、错误数、延迟、在线状态、Bridge 健康和通话状态。
    """
    import os
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    PLATFORMS = ["wechat", "qq", "telegram", "feishu", "wecom", "line"]
    platform_stats: List[GatewayPlatformStats] = []

    total_in = 0
    total_out = 0
    total_err = 0
    remote_gateway_health = await _resolve_remote_gateway_health_fetcher()()
    remote_gateway_body = (
        remote_gateway_health.get("body")
        if isinstance(remote_gateway_health, dict)
        else {}
    )
    remote_adapters = (
        remote_gateway_body.get("adapters")
        if isinstance(remote_gateway_body, dict) and isinstance(remote_gateway_body.get("adapters"), dict)
        else {}
    )
    remote_health_source = str(remote_gateway_health.get("base_url") or "").strip()
    try:
        channel_rows = ledger.fetch_by_time_range(
            "channel_io_log",
            start_ms=since_ms,
            end_ms=now,
            limit=5000,
        )
    except Exception:
        channel_rows = []
    try:
        voice_rows = ledger.fetch_by_time_range(
            "voice_event_log",
            start_ms=since_ms,
            end_ms=now,
            limit=5000,
        )
    except Exception:
        voice_rows = []

    for plat in PLATFORMS:
        try:
            plat_rows = [
                row for row in channel_rows
                if str(row.get("channel") or "").strip().lower() == plat
            ]
            gateway_input_rows = []
            plat_voice_rows = []
            for row in voice_rows:
                payload_dict = _coerce_dict_payload(row.get("payload"))
                inferred_platform = _infer_gateway_platform_from_row(
                    session_id=row.get("session_id"),
                    payload=payload_dict,
                    detail=row.get("detail"),
                )
                if inferred_platform == plat:
                    plat_voice_rows.append(row)
                if inferred_platform != plat:
                    continue
                source_upper = str(payload_dict.get("source") or "").strip().upper()
                if source_upper == "GATEWAY":
                    gateway_input_rows.append((row, payload_dict))

            combined_rows = list(plat_rows) + list(plat_voice_rows)
            channel_inbound = sum(
                1
                for row in plat_rows
                if str(row.get("direction") or "").upper() in ("INBOUND", "INGRESS", "RECEIVE", "RECV")
            )
            channel_outbound = sum(
                1
                for row in plat_rows
                if str(row.get("direction") or "").upper() in ("OUTBOUND", "EGRESS", "SEND", "REPLY")
            )
            channel_errors = sum(
                1
                for row in plat_rows
                if str(row.get("status") or "").upper() in ("ERROR", "FAILED", "REJECTED")
                or "FAIL" in str(row.get("reason_code") or "").upper()
                or "ERROR" in str(row.get("reason_code") or "").upper()
            )
            gateway_inbound_ids = {
                str(row.get("request_id") or "").strip()
                for row, _payload in gateway_input_rows
                if str(row.get("event_type") or "").strip().upper() == "INPUT_ACCEPTED"
                and str(row.get("request_id") or "").strip()
            }
            gateway_completed_ids = {
                str(row.get("request_id") or "").strip()
                for row, _payload in gateway_input_rows
                if str(row.get("event_type") or "").strip().upper() == "INPUT_COMPLETED"
                and str(row.get("status") or "").strip().upper() in {"OK", "COMPLETED", "SENT"}
                and str(row.get("request_id") or "").strip()
            }
            gateway_error_ids = {
                str(row.get("request_id") or "").strip()
                for row, _payload in gateway_input_rows
                if (
                    str(row.get("event_type") or "").strip().upper() == "INPUT_FAILED"
                    or str(row.get("status") or "").strip().upper() in {"ERROR", "FAILED", "REJECTED"}
                    or "FAIL" in str(row.get("reason_code") or "").upper()
                    or "ERROR" in str(row.get("reason_code") or "").upper()
                )
                and str(row.get("request_id") or "").strip()
            }

            inbound = channel_inbound if channel_inbound > 0 else len(gateway_inbound_ids)
            outbound = channel_outbound if channel_outbound > 0 else len(gateway_completed_ids)
            errors = channel_errors if channel_errors > 0 else len(gateway_error_ids)

            last_ts = max((row.get("ts_ms", 0) for row in combined_rows), default=None) if combined_rows else None

            latencies = []
            for row in combined_rows:
                payload = _coerce_dict_payload(row.get("payload"))
                if payload.get("latency_ms") is not None:
                    latencies.append(payload.get("latency_ms"))
            avg_lat = sum(latencies) / len(latencies) if latencies else 0.0

            if not combined_rows:
                status_str = "UNKNOWN"
            elif errors > inbound * 0.5 and inbound > 0:
                status_str = "DOWN"
            elif errors > 0:
                status_str = "DEGRADED"
            else:
                status_str = "UP"

            bridge_status = None
            call_active = 0
            status_detail = None
            if plat == "wechat":
                bridge_backend = os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND", "uia")
                if combined_rows:
                    bridge_status = f"{bridge_backend}:active"
                recent_call_window = now - 5 * 60 * 1000
                call_rows = [
                    row for row in plat_voice_rows
                    if row.get("ts_ms", 0) > recent_call_window and "CALL" in str(row.get("event_type") or "").upper()
                ]
                call_starts = sum(1 for row in call_rows if "VOICE_CALL" in str(row.get("event_type") or "").upper())
                call_ends = sum(1 for row in call_rows if "CANCEL_CALL" in str(row.get("event_type") or "").upper())
                call_active = max(0, call_starts - call_ends)
            remote_adapter = remote_adapters.get(plat) if isinstance(remote_adapters, dict) else None
            if isinstance(remote_adapter, dict):
                remote_status = _normalize_gateway_platform_status(remote_adapter.get("status"))
                status_str = _merge_gateway_platform_status(status_str, remote_status)
                if not combined_rows and remote_status != "UNKNOWN":
                    if remote_health_source:
                        status_detail = f"adapter health via {remote_health_source}"
                    else:
                        status_detail = "adapter health via remote gateway"
                if plat == "wechat" and not bridge_status:
                    remote_bridge_backend = (
                        str(remote_adapter.get("bridge_backend") or os.environ.get("EDGE_WECHAT_BRIDGE_BACKEND", "uia")).strip()
                        or "uia"
                    )
                    remote_bridge_status = str(remote_adapter.get("status") or "unknown").strip().lower() or "unknown"
                    bridge_status = f"{remote_bridge_backend}:{remote_bridge_status}"
                remote_last_outbound = remote_adapter.get("last_outbound")
                if last_ts is None and isinstance(remote_last_outbound, dict):
                    try:
                        last_ts = int(remote_last_outbound.get("ts_ms")) if remote_last_outbound.get("ts_ms") is not None else None
                    except (TypeError, ValueError):
                        last_ts = None
            ps = GatewayPlatformStats(
                platform=plat,
                status=status_str,
                status_detail=status_detail,
                inbound_count=inbound,
                outbound_count=outbound,
                error_count=errors,
                avg_latency_ms=round(avg_lat, 1),
                last_event_ts_ms=last_ts,
                bridge_status=bridge_status,
                call_active=call_active,
            )
            platform_stats.append(ps)
            total_in += inbound
            total_out += outbound
            total_err += errors

        except Exception as exc:
            logger.warning("get_gateway_stats: platform %s error: %s", plat, exc)
            platform_stats.append(GatewayPlatformStats(platform=plat, status=f"ERROR: {exc}"))

    humanize_enabled = os.environ.get("EDGE_HUMANIZE_ENABLED", "true").lower() == "true"
    humanize_cps = int(os.environ.get("EDGE_HUMANIZE_TYPING_SPEED_CPS", "12"))

    return GatewayStatsResponse(
        platforms=platform_stats,
        total_inbound=total_in,
        total_outbound=total_out,
        total_errors=total_err,
        humanize_enabled=humanize_enabled,
        humanize_typing_speed_cps=humanize_cps,
        window_minutes=window_minutes,
        timestamp=now,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Skills Hub 统计 — GET /obs/skills-hub/stats
# ─────────────────────────────────────────────────────────────────────────────

class SkillHubEntry(BaseModel):
    name: str
    version: str = ""
    author: str = ""
    clearance: str = "LOW"
    risk_level: int = 1
    source: str = "local"  # local / github / external
    status: str = "loaded"

class SkillHubStatsResponse(BaseModel):
    hub_enabled: bool = True
    total_skills: int = 0
    loaded_skills: int = 0
    failed_skills: int = 0
    skills: List[SkillHubEntry] = []
    external_dirs_scanned: List[str] = []
    timestamp: int = 0

@router.get("/skills-hub/stats", response_model=SkillHubStatsResponse)
async def get_skills_hub_stats():
    """
    Skills Hub 统计端点。

    返回已加载的外部 Skill 列表、Hub 开关状态、扫描目录等信息。
    """
    import os
    now = _now_ms()

    hub_enabled = os.environ.get("HOST_SKILL_HUB_ENABLED", "true").lower() == "true"
    skills_list: List[SkillHubEntry] = []
    failed_count = 0
    external_dirs: List[str] = []

    if hub_enabled:
        try:
            # 尝试从 SkillRegistry 获取已注册技能
            from host_brain.skills.registry import SkillRegistry
            registry = SkillRegistry.get_instance()
            all_skills = registry.list_all() if hasattr(registry, "list_all") else []
            for sk in all_skills:
                if isinstance(sk, dict):
                    skills_list.append(SkillHubEntry(
                        name=sk.get("name", sk.get("skill_id", "unknown")),
                        version=sk.get("version", ""),
                        author=sk.get("author", ""),
                        clearance=sk.get("clearance", "LOW"),
                        risk_level=sk.get("risk_level", 1),
                        source=sk.get("source", "builtin"),
                        status="loaded",
                    ))
        except Exception as exc:
            logger.warning("get_skills_hub_stats: registry load failed: %s", exc)
            failed_count += 1

        # 扫描外部目录
        scan_external = os.environ.get("HOST_SKILL_SCAN_EXTERNAL", "true").lower() == "true"
        if scan_external:
            home = os.path.expanduser("~")
            for d in [
                os.path.join(home, ".skillhub", "skills"),
                os.path.join(home, ".openclaw", "skills"),
                os.path.join(home, ".codebuddy", "skills"),
            ]:
                if os.path.isdir(d):
                    external_dirs.append(d)
        extra_dirs = os.environ.get("HOST_SKILL_EXTERNAL_DIRS", "").strip()
        if extra_dirs:
            for d in extra_dirs.split(","):
                d = d.strip()
                if d and os.path.isdir(d):
                    external_dirs.append(d)

    return SkillHubStatsResponse(
        hub_enabled=hub_enabled,
        total_skills=len(skills_list) + failed_count,
        loaded_skills=len(skills_list),
        failed_skills=failed_count,
        skills=skills_list,
        external_dirs_scanned=external_dirs,
        timestamp=now,
    )


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket 实时推送 — WS /obs/ws/health
# ─────────────────────────────────────────────────────────────────────────────

from fastapi import WebSocket, WebSocketDisconnect
import asyncio

_ws_clients: List[WebSocket] = []

@router.websocket("/ws/health")
async def ws_health(websocket: WebSocket):
    """
    WebSocket 实时健康推送端点。

    连接后每 10s 推送一次全服务健康快照（与 GET /obs/services/health 格式一致）。
    告警事件（服务 DOWN/DEGRADED 变化）立即推送。
    """
    await websocket.accept()
    _ws_clients.append(websocket)
    logger.info("WebSocket health client connected (total: %d)", len(_ws_clients))

    prev_statuses: Dict[str, str] = {}

    try:
        while True:
            try:
                # 获取最新健康数据（与 GET /obs/services/health 逻辑一致）
                ledger = _get_ledger_instance()
                if ledger:
                    now_ms = _now_ms()
                    since_ms = now_ms - 60 * 60 * 1000  # 默认 1h 窗口
                    probe_registry, _extra = _build_probe_registry(ledger, since_ms)
                    aggregator = ServiceHealthAggregator(probe_registry=probe_registry)
                    health_data = await aggregator.aggregate()
                else:
                    health_data = {"services": [], "total": 0, "up": 0, "down": 0, "degraded": 0, "unknown": 0, "timestamp": _now_ms()}

                services = health_data.get("services", [])
                current_statuses: Dict[str, str] = {}
                alerts = []

                for svc in services:
                    sid = svc.get("service_id", "")
                    st = svc.get("status", "UNKNOWN")
                    current_statuses[sid] = st

                    # 检测状态变化（告警）
                    prev_st = prev_statuses.get(sid)
                    if prev_st and prev_st != st:
                        if st in ("DOWN", "DEGRADED"):
                            alerts.append({
                                "type": "alert",
                                "service_id": sid,
                                "prev_status": prev_st,
                                "new_status": st,
                                "ts_ms": _now_ms(),
                            })

                prev_statuses = current_statuses

                # 推送健康快照
                payload = {
                    "type": "health_snapshot",
                    "data": health_data,
                    "alerts": alerts,
                    "ts_ms": _now_ms(),
                }
                await websocket.send_json(payload)

            except Exception as exc:
                logger.warning("WebSocket health push error: %s", exc)
                try:
                    await websocket.send_json({
                        "type": "error",
                        "detail": str(exc),
                        "ts_ms": _now_ms(),
                    })
                except Exception:
                    break

            await asyncio.sleep(10)  # 每 10 秒推送一次

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if websocket in _ws_clients:
            _ws_clients.remove(websocket)
        logger.info("WebSocket health client disconnected (remaining: %d)", len(_ws_clients))


def _get_ledger_instance():
    """尝试获取 ledger 实例（辅助函数）。"""
    try:
        if _ledger_provider and callable(_ledger_provider):
            return _ledger_provider()
    except Exception:
        pass
    return None
