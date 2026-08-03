"""Life-body aggregate observability routes for monitor-side digital life views."""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_core import (
    _env_int,
    _get_obs_route_cached_response,
    _now_ms,
)
from host_brain.app.obs_routes_stats_capability_skill import (
    build_prompt_builder_stats_from_view,
)
from host_brain.app.obs_routes_summary import (
    _build_digital_life_overview_snapshot_async,
)
from host_brain.app.obs_routes_trace import (
    _get_trace_builders,
)
from host_brain.app.observability_runtime.projection_runtime import (
    TRACE_FEED_INDEX_KEY,
    TRACE_FEED_KIND,
    projection_meta,
)
from host_brain.app.observability_runtime.health_device_projection import (
    ORGAN_TRIBE_KEY,
    ORGAN_TRIBE_KIND,
    ORGAN_TRIBE_MAX_AGE_MS,
)
from host_brain.app.observability_runtime.process_state_projection import (
    LIFE_BODY_KIND,
    LIFE_BODY_MAX_AGE_MS,
    RUNTIME_MAX_AGE_MS,
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
    read_process_projection,
    window_key,
)
from host_brain.persistence.life_body_query_repository import (
    LifeBodyQueryRepository,
    decode_life_body_row,
)
from host_brain.observability.projection_store import QueryProjectionStore

logger = logging.getLogger("Natsume.obs")

router = APIRouter(prefix="/obs", tags=["observability"])

_LIFE_BODY_ROUTE_CACHE_TTL_MS = _env_int("HOST_OBS_LIFE_BODY_ROUTE_CACHE_TTL_MS", 12_000, minimum=0)
_LIFE_TIMELINE_REQUEST_FALLBACK_CACHE_TTL_MS = _env_int(
    "HOST_OBS_LIFE_TIMELINE_REQUEST_FALLBACK_CACHE_TTL_MS",
    15_000,
    minimum=0,
)
LIFE_BODY_PROJECTION_WINDOW_MINUTES = 360
_LIFE_BODY_RECENT_SESSIONS_LIMIT = _env_int(
    "HOST_OBS_LIFE_BODY_RECENT_SESSIONS_LIMIT",
    3,
    minimum=1,
)
_LIFE_BODY_RECENT_SESSIONS_WINDOW_MINUTES = _env_int(
    "HOST_OBS_LIFE_BODY_RECENT_SESSIONS_WINDOW_MINUTES",
    360,
    minimum=1,
)
_LIFE_LINEAGE_MEMORY_LIMIT = _env_int(
    "HOST_OBS_LIFE_LINEAGE_MEMORY_LIMIT",
    2,
    minimum=1,
)
_LIFE_LINEAGE_BOND_LIMIT = _env_int(
    "HOST_OBS_LIFE_LINEAGE_BOND_LIMIT",
    2,
    minimum=1,
)
_LIFE_LINEAGE_TICK_LIMIT = _env_int(
    "HOST_OBS_LIFE_LINEAGE_TICK_LIMIT",
    2,
    minimum=1,
)
_LIFE_LINEAGE_IMMUNE_LIMIT = _env_int(
    "HOST_OBS_LIFE_LINEAGE_IMMUNE_LIMIT",
    3,
    minimum=1,
)
_LIFE_LINEAGE_IMMUNE_SCAN_LIMIT = _env_int(
    "HOST_OBS_LIFE_LINEAGE_IMMUNE_SCAN_LIMIT",
    48,
    minimum=1,
)

STATUS_RANK = {"critical": 0, "warning": 1, "stable": 2, "pending": 3}
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
TIMELINE_EVENT_PRIORITY = {
    "immune": 0,
    "request": 1,
    "pathology": 2,
    "execution": 3,
    "tick": 4,
    "bond": 5,
    "emotion": 6,
    "memory": 7,
    "self": 8,
    "snapshot": 9,
}

HOTSPOT_ORGAN_MAP = {
    "prefrontal_governor": "nervous",
    "language_feedback": "nervous",
    "somatic_progress": "motor",
    "immune_realign": "immune",
    "motor_execution": "motor",
    "closed_loop_integration": "motor",
}

ORGAN_META: dict[str, dict[str, str]] = {
    "nervous": {
        "name": "神经系统",
        "path": "/trace",
        "drilldownLabel": "进入 Trace",
        "fallbackImpact": "会影响主链路的连续性、可解释性和请求回放效率。",
    },
    "limbic": {
        "name": "边缘系统",
        "path": "/emotion",
        "drilldownLabel": "查看情绪与关系",
        "fallbackImpact": "会直接改变回复姿态、关系温度与陪伴感。",
    },
    "memory": {
        "name": "记忆系统",
        "path": "/memory",
        "drilldownLabel": "查看记忆详情",
        "fallbackImpact": "会影响回忆注入、设定延续和长期陪伴一致性。",
    },
    "endocrine": {
        "name": "内分泌系统",
        "path": "/automation",
        "drilldownLabel": "查看节律详情",
        "fallbackImpact": "会影响后台推进、疲劳恢复和内在世界演化。",
    },
    "immune": {
        "name": "免疫系统",
        "path": "/alerts",
        "drilldownLabel": "查看免疫告警",
        "fallbackImpact": "会影响门控放行、风险收束和叙事护栏。",
    },
    "motor": {
        "name": "运动系统",
        "path": "/gateway",
        "drilldownLabel": "查看执行闭环",
        "fallbackImpact": "会影响 ACK、进度触达和用户对行动结果的体感。",
    },
    "self": {
        "name": "自我系统",
        "path": "/self-model",
        "drilldownLabel": "查看自我模型",
        "fallbackImpact": "会影响身份一致性、成长叙事和长期人格稳定度。",
    },
}

MODE_META = {
    "normal": {"label": "正常"},
    "focus": {"label": "专注"},
    "gaming": {"label": "游戏"},
    "sleep": {"label": "睡眠"},
    "maintenance": {"label": "维护"},
    "locked": {"label": "锁定"},
}
STAGE_LABELS = {
    "FRIEND": "关系好的兄妹",
    "CLOSE": "亲近",
    "CLOSE_FRIEND": "亲近兄妹",
    "COMPANION": "陪伴",
    "CONFIDANT": "亲密恋人",
    "INTIMATE": "亲密恋人",
    "SOULMATE": "灵魂伴侣",
}
REPLY_POSTURE_LABELS = {
    "balanced": "平衡",
    "focused": "专注",
    "warm_reconnect": "温柔重连",
    "brief_guarded": "克制防守",
    "withdrawn": "收敛",
    "warm": "温柔",
    "playful": "轻快",
    "guarded": "谨慎",
    "brief": "简短",
}
MOTIVE_LABELS = {
    "reach_out": "主动靠近",
    "care": "照顾安抚",
    "withdraw": "抽离退避",
    "continuity": "维持联系",
    "self_protect": "自我保护",
    "none": "暂无主导动机",
}
RISK_LEVEL_LABELS = {"none": "无", "low": "低", "medium": "中", "high": "高", "critical": "严重"}
ABSENCE_BAND_LABELS = {
    "recent": "近期在线",
    "6h_plus": "离线 6 小时以上",
    "24h_plus": "离线 24 小时以上",
    "72h_plus": "离线 72 小时以上",
    "7d_plus": "离线 7 天以上",
}
EMOTION_TAG_LABELS = {
    "NEUTRAL": "平静",
    "HAPPY": "开心",
    "JOY": "喜悦",
    "EXCITED": "兴奋",
    "SAD": "难过",
    "SADNESS": "难过",
    "ANGRY": "愤怒",
    "ANGER": "愤怒",
    "FEAR": "害怕",
    "SURPRISE": "惊讶",
    "SURPRISED": "惊讶",
    "CONFUSED": "困惑",
    "CALM": "平静",
    "LOVE": "喜欢",
    "CURIOUS": "好奇",
    "SHY": "害羞",
    "BORED": "无聊",
    "DISGUST": "排斥",
}
TREND_LABELS = {
    "RISING": "上升",
    "IMPROVING": "改善",
    "WARMING": "回暖",
    "WARMING_UP": "回暖",
    "STABLE": "稳定",
    "STEADY": "稳定",
    "MIXED": "波动",
    "SWINGING": "波动",
    "FLUCTUATING": "波动",
    "DECLINING": "下降",
    "FALLING": "下降",
    "COOLING": "降温",
}
ALERT_TITLES = {
    "RUNTIME_MODE_DEGRADED": "生命体存在态轴降级",
    "RUNTIME_SNAPSHOT_DEGRADED": "运行快照存在缺口",
    "RUNTIME_SLOT_NOT_LIVE": "关键槽位未在线",
    "REFLECTION_GUARD_ACTIVE": "自我反思护栏生效",
    "TOPIC_RESISTANCE_ACTIVE": "当前存在话题抗拒",
    "WITHDRAWN_REPLY_POLICY": "回复策略偏收敛",
    "PROMPT_INJECTION_DETECTED": "检测到提示注入",
    "PROMPT_HIGH_RISK_BUILD": "检测到高风险 Prompt 构建",
    "BACKGROUND_RUNTIME_NOT_ADVANCING": "后台推进异常",
    "BACKGROUND_CORE_TICK_MISSING": "后台核心任务缺失",
    "BACKGROUND_PERSONA_MISMATCH": "后台人格对齐异常",
    "INTEROCEPTION_OVERLOAD": "内感受负载过高",
    "VESTIBULAR_INSTABILITY": "前庭平衡不稳",
    "MOTOR_DISPATCH_DEGRADED": "动作分发降级",
    "HOMEOSTASIS_WARNING": "稳态风险上升",
    "FORGE_RUNTIME_CRITICAL_PROPOSALS": "Forge 关键提案堆积",
    "FORGE_RUNTIME_UNHEALTHY_PROPOSALS": "Forge 提案健康度下降",
}


class LifeBodyResponse(BaseModel):
    window_minutes: int
    timestamp: int
    runtimeSnapshot: Optional[Dict[str, Any]] = None
    promptStats: Optional[Dict[str, Any]] = None
    vtsData: Optional[Dict[str, Any]] = None
    memoryData: Optional[Dict[str, Any]] = None
    servicesData: Optional[Dict[str, Any]] = None
    subsystemsData: Optional[Dict[str, Any]] = None
    organTribeData: Optional[Dict[str, Any]] = None
    capabilityGapData: Optional[Dict[str, Any]] = None
    capabilityGapProjection: Dict[str, Any] = Field(default_factory=dict)
    humanoidLoopData: Optional[Dict[str, Any]] = None
    humanoidLoopTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopAlertsData: Optional[Dict[str, Any]] = None
    humanoidLoopRemediationData: Optional[Dict[str, Any]] = None
    humanoidLoopDiagnosticsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotsData: Optional[Dict[str, Any]] = None
    humanoidLoopHotspotTrendData: Optional[Dict[str, Any]] = None
    humanoidLoopSourceCorrelationData: Optional[Dict[str, Any]] = None
    runtimeAlerts: List[Dict[str, Any]] = Field(default_factory=list)
    nonLiveSlots: List[List[str]] = Field(default_factory=list)
    growthSignals: Dict[str, Any] = Field(default_factory=dict)
    derived: Dict[str, Any] = Field(default_factory=dict)
    model: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


class LifeTimelineResponse(BaseModel):
    window_minutes: int
    timestamp: int
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    filtered_total: int = 0
    primaryPathology: Optional[Dict[str, Any]] = None
    primaryOrgan: Optional[Dict[str, Any]] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


class PathologiesResponse(BaseModel):
    window_minutes: int
    timestamp: int
    items: List[Dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    filtered_total: int = 0
    primaryPathology: Optional[Dict[str, Any]] = None
    organSystems: List[Dict[str, Any]] = Field(default_factory=list)
    focusedItem: Optional[Dict[str, Any]] = None
    focusedOrgan: Optional[Dict[str, Any]] = None
    relatedEvents: List[Dict[str, Any]] = Field(default_factory=list)
    filters: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)


def _clone_payload(payload: Any) -> Any:
    return copy.deepcopy(payload)


def _model_dump_compat(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _has_text(value: Any) -> bool:
    text = _as_text(value)
    return bool(text) and text not in {"—", "--", "待判定"}


def _compact_text(value: Any, limit: int = 180) -> str:
    text = " ".join(_as_text(value).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_trace_intent(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("route", "action", "type", "reason_code", "id"):
            text = _as_text(value.get(key))
            if text:
                return text
        return ""
    text = _as_text(value)
    if text.startswith("{") and text.endswith("}"):
        return ""
    return text


def _format_label(mapping: dict[str, str], value: Any, default: str = "—", *, upper: bool = False) -> str:
    text = _as_text(value)
    if not text:
        return default
    key = text.upper() if upper else text.lower()
    return mapping.get(key, mapping.get(text, text))


def _format_stage(value: Any) -> str:
    return _format_label(STAGE_LABELS, value, upper=True)


def _format_emotion_tag(value: Any) -> str:
    return _format_label(EMOTION_TAG_LABELS, value, upper=True)


def _format_dominant_motive(value: Any) -> str:
    return _format_label(MOTIVE_LABELS, value)


def _format_reply_posture(value: Any) -> str:
    return _format_label(REPLY_POSTURE_LABELS, value)


def _format_risk_level(value: Any) -> str:
    return _format_label(RISK_LEVEL_LABELS, value)


def _format_absence_band(value: Any) -> str:
    return _format_label(ABSENCE_BAND_LABELS, value)


def _format_trend(value: Any) -> str:
    return _format_label(TREND_LABELS, value, default="—", upper=True)


def _format_mode_label(value: Any) -> str:
    key = _as_text(value).lower()
    return _as_text(MODE_META.get(key, {}).get("label")) or "待连接"


def _format_alert_title(reason_code: str) -> str:
    key = _as_text(reason_code).upper()
    return ALERT_TITLES.get(key, key or "运行告警")


def _format_percent(value: Any, digits: int = 1) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "--"
    numeric = max(0.0, min(100.0, numeric))
    return f"{numeric:.{digits}f}%"


def _format_window_label(window_minutes: int) -> str:
    if window_minutes <= 60:
        return "1h"
    if window_minutes <= 360:
        return "6h"
    if window_minutes <= 1440:
        return "24h"
    if window_minutes <= 10080:
        return "7d"
    return f"{window_minutes}m"


def _build_pathology_detail_path(pathology_id: Any) -> str:
    from urllib.parse import quote

    return f"/pathologies/{quote(_as_text(pathology_id))}"


def _build_trace_request_path(request_id: Any) -> str:
    from urllib.parse import quote

    return f"/trace/{quote(_as_text(request_id))}"


def _build_immune_replay_path(request_id: Any) -> str:
    from urllib.parse import urlencode

    rid = _as_text(request_id)
    query = urlencode({"immune_replay_request_id": rid}) if rid else ""
    return f"/automation{('?' + query) if query else ''}"


def _build_life_timeline_path(
    *,
    organ_id: Any = "",
    event_type: Any = "",
    request_id: Any = "",
    session_id: Any = "",
    tick_id: Any = "",
) -> str:
    from urllib.parse import urlencode

    params = {}
    if _has_text(organ_id):
        params["organ"] = _as_text(organ_id)
    if _has_text(event_type):
        params["type"] = _as_text(event_type)
    if _has_text(request_id):
        params["request_id"] = _as_text(request_id)
    if _has_text(session_id):
        params["session_id"] = _as_text(session_id)
    if _has_text(tick_id):
        params["tick_id"] = _as_text(tick_id)
    query = urlencode(params)
    return f"/life-timeline{('?' + query) if query else ''}"


def _short_id(value: Any, length: int = 14) -> str:
    text = _as_text(value)
    if not text:
        return "—"
    return text if len(text) <= length else text[:length]


def _extract_chat_ref_id(value: Any, ref_kind: str) -> str:
    prefix = f"chat://{_as_text(ref_kind).strip('/')}/"
    candidates = value if isinstance(value, (list, tuple, set)) else [value]
    for item in candidates:
        text = _as_text(item)
        if text.startswith(prefix):
            return text[len(prefix):]
    return ""


def _extract_session_id_from_detail(value: Any) -> str:
    text = _as_text(value)
    marker = "[session="
    start = text.find(marker)
    if start < 0:
        return ""
    end = text.find("]", start + len(marker))
    if end < 0:
        return ""
    return text[start + len(marker):end].strip()


def _extract_representative_ref_id(value: Any, ref_type: str) -> str:
    candidates = value if isinstance(value, (list, tuple, set)) else [value]
    for item in candidates:
        item_dict = _as_dict(item)
        if _as_text(item_dict.get("type")) == _as_text(ref_type):
            return _as_text(item_dict.get("id"))
    return ""


def _extract_request_id_from_event(item: dict[str, Any]) -> str:
    return (
        _as_text(item.get("requestId"))
        or _as_text(item.get("sourceRequestId"))
        or _extract_representative_ref_id(item.get("representativeRefs"), "request")
    )


def _extract_session_id_from_event(item: dict[str, Any]) -> str:
    return (
        _as_text(item.get("sessionId"))
        or _as_text(item.get("sourceSessionId"))
        or _extract_representative_ref_id(item.get("representativeRefs"), "session")
    )


def _extract_tick_id_from_event(item: dict[str, Any]) -> str:
    return (
        _as_text(item.get("tickId"))
        or _as_text(item.get("sourceTickId"))
        or _extract_representative_ref_id(item.get("representativeRefs"), "tick")
    )


def _fetch_recent_ledger_rows(
    ledger: Any,
    table: str,
    *,
    window_minutes: int,
    limit: int,
) -> list[dict[str, Any]]:
    now_ms = _now_ms()
    start_ms = now_ms - max(1, int(window_minutes or 0)) * 60 * 1000
    try:
        rows = LifeBodyQueryRepository.from_ledger(ledger).read_generic_window(
            table,
            start_ms=start_ms,
            end_ms=now_ms,
            limit=max(1, int(limit)),
        )
        return [_as_dict(decode_life_body_row(table, row)) for row in rows]
    except Exception as exc:
        logger.debug("life-body ledger projection degraded: table=%s err=%s", table, exc)
        return []


def _fetch_recent_immune_rows(
    ledger: Any,
    *,
    window_minutes: int,
    limit: int,
    scan_limit: int,
) -> list[dict[str, Any]]:
    now_ms = _now_ms()
    start_ms = now_ms - max(1, int(window_minutes or 0)) * 60 * 1000
    desired = max(1, int(limit))
    row_limit = max(desired, int(scan_limit or 0) or desired)
    try:
        rows = LifeBodyQueryRepository.from_ledger(ledger).read_event_type_window(
            "voice_event_log",
            start_ms=start_ms,
            end_ms=now_ms,
            exact_event_type="IMMUNE_CHAT_GATE",
            like_event_type="INPUT_%",
            scan_limit=row_limit,
        )
    except Exception as exc:
        logger.debug(
            "life-body immune lineage degraded: table=voice_event_log err=%s",
            exc,
        )
        return []

    immune_rows: list[dict[str, Any]] = []
    for row in rows:
        row_dict = _as_dict(decode_life_body_row("voice_event_log", row))
        if _as_dict(_extract_immune_gate_payload(row_dict)):
            immune_rows.append(row_dict)
        if len(immune_rows) >= desired:
            break
    return immune_rows


def _normalize_string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        items = value
    elif value is None:
        items = []
    else:
        items = [value]
    normalized: list[str] = []
    for item in items:
        text = _as_text(item)
        if not text:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def _extract_immune_gate_payload(row: dict[str, Any]) -> dict[str, Any]:
    event_type = _as_text(row.get("event_type")).upper()
    payload_dict = _as_dict(row.get("payload"))
    source_payload: dict[str, Any] = {}
    if event_type == "IMMUNE_CHAT_GATE":
        source_payload = payload_dict
    elif event_type.startswith("INPUT_"):
        source_payload = _as_dict(payload_dict.get("immune_gate"))
    if not source_payload:
        return {}

    decision = _as_text(source_payload.get("decision")).upper()
    reason_code = _as_text(row.get("reason_code")).upper()
    if decision not in {"ALLOW", "REPORT", "BLOCK"}:
        decision = "BLOCK" if "IMMUNE_BLOCKED" in reason_code else "ALLOW"

    risk_level = _as_text(source_payload.get("risk_level")).lower() or None
    score: float | None = None
    try:
        if source_payload.get("score") is not None:
            score = float(source_payload.get("score"))
    except (TypeError, ValueError):
        score = None

    reason_codes = _normalize_string_list(source_payload.get("reason_codes"), limit=8)
    if reason_code and reason_code.startswith("IMMUNE_") and reason_code not in reason_codes:
        reason_codes.append(reason_code)
    matched_rules = _normalize_string_list(source_payload.get("matched_rules"), limit=8)
    input_preview = ""
    for candidate in (
        payload_dict.get("input_preview"),
        payload_dict.get("text_preview"),
        payload_dict.get("raw_text"),
        source_payload.get("input_preview"),
    ):
        input_preview = _compact_text(candidate, 56)
        if input_preview:
            break

    return {
        "eventType": event_type,
        "decision": decision or "ALLOW",
        "riskLevel": risk_level,
        "score": score,
        "reasonCodes": reason_codes[:8],
        "matchedRules": matched_rules[:8],
        "inputPreview": input_preview or None,
        "detail": _compact_text(row.get("detail"), 120) or None,
        "stage": _as_text(row.get("stage")) or None,
        "direction": _as_text(row.get("direction")) or None,
    }


def _summarize_success_rate_samples(items: list[dict[str, Any]]) -> dict[str, Any]:
    sampled: list[dict[str, Any]] = []
    for item in items:
        try:
            total = int(item.get("total") or 0)
            rate = item.get("success_rate")
            if total > 0 and rate is not None:
                sampled.append(item)
        except Exception:
            continue
    if not sampled:
        return {"averageRate": None, "sampledCount": 0, "configuredCount": len(items)}
    total_rate = sum(float(item.get("success_rate") or 0) for item in sampled)
    return {
        "averageRate": round(total_rate / len(sampled)),
        "sampledCount": len(sampled),
        "configuredCount": len(items),
    }


def _get_recent_sessions_projection(
    window_minutes: int,
    *,
    projection_store: Any,
) -> dict[str, Any]:
    from host_brain.app.obs_routes_sessions import build_sessions_projection_response

    capped_window = max(1, min(int(window_minutes or 0) or 1, _LIFE_BODY_RECENT_SESSIONS_WINDOW_MINUTES))
    try:
        payload = _model_dump_compat(
            build_sessions_projection_response(
                projection_store,
                page=1,
                page_size=_LIFE_BODY_RECENT_SESSIONS_LIMIT,
                status=None,
                window_minutes=capped_window,
                keyword=None,
            )
        )
        payload_dict = _as_dict(payload)
        return {
            "items": [_as_dict(item) for item in _as_list(payload_dict.get("items"))],
            "total": int(payload_dict.get("total") or 0),
            "windowMinutes": capped_window,
        }
    except Exception as exc:
        logger.debug("life-body recent sessions degraded: %s", exc)
        return {
            "items": [],
            "total": 0,
            "windowMinutes": capped_window,
        }


def _get_trace_feed_projection(projection_store: Any) -> dict[str, Any]:
    """Read the canonical durable trace-feed projection only."""
    try:
        document = projection_store.get(TRACE_FEED_KIND, TRACE_FEED_INDEX_KEY)
        meta = projection_meta(
            TRACE_FEED_KIND,
            TRACE_FEED_INDEX_KEY,
            document,
            max_age_ms=15_000,
        )
        if document is None or not meta.get("available"):
            return {
                "items": [],
                "meta": {
                    "available": False,
                    "reason": str(meta.get("reason_code") or "trace_feed_projection_unavailable"),
                    "projection": meta,
                },
            }
        snapshot_dict = dict(document.payload)
        return {
            "items": [_as_dict(item) for item in _as_list(snapshot_dict.get("items"))],
            "timestamp": int(snapshot_dict.get("timestamp") or 0),
            "meta": {
                "available": True,
                "projection": meta,
            },
        }
    except Exception as exc:
        logger.debug("life-body warmed trace feed degraded: %s", exc)
        return {
            "items": [],
            "meta": {
                "available": False,
                "reason": type(exc).__name__,
            },
        }


def _build_memory_lineage_events(memory_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    title_map = {
        "WRITE": "记忆写入已写账",
        "READ": "记忆读取已写账",
        "RECALL": "记忆召回已写账",
        "RETRIEVE": "记忆召回已写账",
        "DISTILL": "记忆蒸馏已写账",
        "MERGE": "记忆合并已写账",
        "DELETE": "记忆回收已写账",
    }
    for row in memory_rows:
        row_dict = _as_dict(row)
        timestamp = int(row_dict.get("ts_ms") or 0)
        if timestamp <= 0:
            continue
        request_id = _as_text(row_dict.get("request_id")) or _extract_chat_ref_id(row_dict.get("evidence_refs"), "request")
        session_id = (
            _extract_chat_ref_id(row_dict.get("evidence_refs"), "session")
            or _extract_session_id_from_detail(row_dict.get("detail"))
        )
        op_kind = _as_text(row_dict.get("op_kind")).upper() or "MEMORY"
        status = _as_text(row_dict.get("status")).upper()
        reason_code = _as_text(row_dict.get("reason_code")).upper()
        memory_id = _as_text(row_dict.get("memory_id") or row_dict.get("target_memory_id"))
        severity = "critical" if status in {"FAILED", "ERROR"} else "warning" if status and status not in {"OK", "SUCCESS"} else "info"
        title = title_map.get(op_kind, "记忆操作已写账")
        if severity == "critical":
            title = title.replace("已写账", "异常")
        elif severity == "warning":
            title = title.replace("已写账", "待确认")
        refs = []
        if request_id:
            refs.append(
                {
                    "type": "request",
                    "id": request_id,
                    "to": _build_trace_request_path(request_id),
                    "label": op_kind.lower(),
                    "subtitle": _short_id(request_id),
                    "tags": [tag for tag in [status or None, reason_code or None] if tag],
                }
            )
        if session_id:
            refs.append(
                {
                    "type": "session",
                    "id": session_id,
                    "to": _build_life_timeline_path(event_type="memory", session_id=session_id),
                    "label": "session",
                    "subtitle": _short_id(session_id),
                    "tags": [tag for tag in [op_kind or None] if tag],
                }
            )
        detail_parts = [
            op_kind,
            status,
            reason_code,
            f"session {_short_id(session_id)}" if session_id else "",
            f"memory {_short_id(memory_id)}" if memory_id else "",
            _compact_text(row_dict.get("detail"), 88),
        ]
        event_token = memory_id or request_id or f"{op_kind.lower()}-{timestamp}"
        events.append(
            {
                "id": f"memory-{event_token}",
                "eventId": event_token,
                "type": "memory",
                "typeLabel": "记忆",
                "organId": "memory",
                "organLabel": ORGAN_META["memory"]["name"],
                "severity": severity,
                "timestamp": timestamp,
                "requestId": request_id or None,
                "sessionId": session_id or None,
                "sourceRequestId": request_id or None,
                "sourceSessionId": session_id or None,
                "memoryId": memory_id or None,
                "representativeRefs": refs,
                "title": title,
                "detail": " · ".join(part for part in detail_parts if part) or "最新记忆链路已经有真实账本事件可追。",
                "to": _build_life_timeline_path(event_type="memory", request_id=request_id, session_id=session_id),
                "linkLabel": "查看记忆事件链",
                "secondaryTo": "/memory",
                "secondaryLinkLabel": "查看记忆系统",
            }
        )
    return events


def _build_bond_lineage_events(bond_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    title_map = {
        "STAGE_TRANSITION": "关系阶段已写账",
        "INTERACTION_MINIMAL": "关系互动已写账",
        "INTERACTION": "关系互动已写账",
        "BOND_MEMORY": "关系记忆已写账",
    }
    for row in bond_rows:
        row_dict = _as_dict(row)
        timestamp = int(row_dict.get("ts_ms") or 0)
        if timestamp <= 0:
            continue
        payload = _as_dict(row_dict.get("payload"))
        request_id = _as_text(row_dict.get("request_id")) or _extract_chat_ref_id(row_dict.get("evidence_refs"), "request")
        session_id = _as_text(row_dict.get("session_id")) or _extract_chat_ref_id(row_dict.get("evidence_refs"), "session")
        event_id = _as_text(row_dict.get("event_id")) or f"bond-{request_id or session_id or timestamp}"
        update_kind = _as_text(row_dict.get("update_kind")).upper() or "BOND"
        status = _as_text(row_dict.get("status")).upper()
        reason_code = _as_text(row_dict.get("reason_code")).upper()
        from_stage = _format_stage(payload.get("from_stage"))
        to_stage = _format_stage(payload.get("to_stage"))
        transition_status = _as_text(payload.get("transition_status")).upper()
        severity = "critical" if status in {"FAILED", "ERROR"} else "warning" if transition_status and transition_status not in {"STABLE", "PROMOTED", "SUCCESS"} else "info"
        title = title_map.get(update_kind, "关系事件已写账")
        if severity == "critical":
            title = title.replace("已写账", "异常")
        elif severity == "warning":
            title = title.replace("已写账", "波动")
        refs = []
        if request_id:
            refs.append(
                {
                    "type": "request",
                    "id": request_id,
                    "to": _build_trace_request_path(request_id),
                    "label": update_kind.lower(),
                    "subtitle": _short_id(request_id),
                    "tags": [tag for tag in [status or None, transition_status or None] if tag],
                }
            )
        if session_id:
            refs.append(
                {
                    "type": "session",
                    "id": session_id,
                    "to": _build_life_timeline_path(event_type="bond", session_id=session_id),
                    "label": "session",
                    "subtitle": _short_id(session_id),
                    "tags": [tag for tag in [update_kind or None] if tag],
                }
            )
        detail_parts = [
            update_kind,
            status,
            reason_code,
            f"{from_stage} → {to_stage}" if from_stage != "—" and to_stage != "—" else "",
            transition_status,
            _as_text(row_dict.get("capability_hint")),
            _compact_text(row_dict.get("detail"), 88),
        ]
        events.append(
            {
                "id": f"bond-{event_id}",
                "eventId": event_id,
                "type": "bond",
                "typeLabel": "关系",
                "organId": "limbic",
                "organLabel": ORGAN_META["limbic"]["name"],
                "severity": severity,
                "timestamp": timestamp,
                "requestId": request_id or None,
                "sessionId": session_id or None,
                "sourceRequestId": request_id or None,
                "sourceSessionId": session_id or None,
                "representativeRefs": refs,
                "title": title,
                "detail": " · ".join(part for part in detail_parts if part) or "最新关系链路已经有真实账本事件可追。",
                "to": _build_life_timeline_path(event_type="bond", request_id=request_id, session_id=session_id),
                "linkLabel": "查看关系事件链",
                "secondaryTo": "/bond",
                "secondaryLinkLabel": "查看关系系统",
            }
        )
    return events


def _build_scheduler_tick_events(scheduler_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in scheduler_rows:
        row_dict = _as_dict(row)
        timestamp = int(row_dict.get("ts_ms") or row_dict.get("trigger_ts") or 0)
        if timestamp <= 0:
            continue
        task_id = _as_text(row_dict.get("task_id"))
        tick_id = _as_text(row_dict.get("event_id")) or f"{task_id or 'tick'}-{timestamp}"
        status = _as_text(row_dict.get("status")).upper()
        reason_code = _as_text(row_dict.get("reason_code")).upper()
        mode_at_trigger = _as_text(row_dict.get("mode_at_trigger"))
        error_detail = _compact_text(row_dict.get("error_detail"), 88)
        severity = "critical" if status in {"FAILED", "ERROR"} else "warning" if status in {"BLOCKED", "SKIPPED", "DEGRADED"} or reason_code else "info"
        title = "节律 tick 已写账" if severity == "info" else "节律 tick 波动" if severity == "warning" else "节律 tick 异常"
        refs = [
            {
                "type": "tick",
                "id": tick_id,
                "to": _build_life_timeline_path(event_type="tick", tick_id=tick_id),
                "label": task_id or "tick",
                "subtitle": _short_id(tick_id),
                "tags": [tag for tag in [status or None, reason_code or None] if tag],
            }
        ]
        events.append(
            {
                "id": f"tick-{tick_id}",
                "eventId": tick_id,
                "tickId": tick_id,
                "sourceTickId": tick_id,
                "type": "tick",
                "typeLabel": "节律",
                "organId": "endocrine",
                "organLabel": ORGAN_META["endocrine"]["name"],
                "severity": severity,
                "timestamp": timestamp,
                "representativeRefs": refs,
                "title": title,
                "detail": " · ".join(
                    part
                    for part in [
                        task_id,
                        status,
                        reason_code,
                        f"mode {mode_at_trigger}" if mode_at_trigger else "",
                        error_detail,
                    ]
                    if part
                ) or "已捕获到可追的节律 tick 事件。",
                "to": _build_life_timeline_path(event_type="tick", tick_id=tick_id),
                "linkLabel": "查看 tick 事件链",
                "secondaryTo": "/automation",
                "secondaryLinkLabel": "查看节律系统",
            }
        )
    return events


def _build_immune_lineage_events(immune_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in immune_rows:
        row_dict = _as_dict(row)
        immune_payload = _extract_immune_gate_payload(row_dict)
        if not immune_payload:
            continue
        timestamp = int(row_dict.get("ts_ms") or 0)
        if timestamp <= 0:
            continue
        request_id = _as_text(row_dict.get("request_id"))
        session_id = _as_text(row_dict.get("session_id"))
        decision = _as_text(immune_payload.get("decision")).upper() or "ALLOW"
        risk_level = _as_text(immune_payload.get("riskLevel")).lower()
        reason_codes = _normalize_string_list(immune_payload.get("reasonCodes"), limit=8)
        matched_rules = _normalize_string_list(immune_payload.get("matchedRules"), limit=8)
        stage = _as_text(immune_payload.get("stage"))
        direction = _as_text(immune_payload.get("direction"))
        input_preview = _compact_text(immune_payload.get("inputPreview"), 56)
        detail = _compact_text(immune_payload.get("detail"), 88)
        severity = (
            "critical"
            if decision == "BLOCK"
            else "warning"
            if decision == "REPORT" or risk_level in {"high", "critical"}
            else "info"
        )
        title = (
            "免疫门拦截高风险输入"
            if decision == "BLOCK"
            else "免疫门上报高风险输入"
            if decision == "REPORT"
            else "免疫门放行当前输入"
        )
        refs: list[dict[str, Any]] = []
        if request_id:
            refs.append(
                {
                    "type": "request",
                    "id": request_id,
                    "to": _build_trace_request_path(request_id),
                    "label": decision,
                    "subtitle": _short_id(request_id),
                    "tags": [tag for tag in [risk_level or None] + reason_codes[:2] if tag],
                }
            )
        if session_id:
            refs.append(
                {
                    "type": "session",
                    "id": session_id,
                    "to": _build_life_timeline_path(event_type="immune", session_id=session_id),
                    "label": "session",
                    "subtitle": _short_id(session_id),
                    "tags": [tag for tag in [direction or None, stage or None] if tag],
                }
            )
        score_text = ""
        score_value = immune_payload.get("score")
        if score_value is not None:
            try:
                score_text = f"score {float(score_value):.2f}"
            except (TypeError, ValueError):
                score_text = ""
        events.append(
            {
                "id": f"immune-{request_id or session_id or timestamp}",
                "requestId": request_id or None,
                "sessionId": session_id or None,
                "sourceRequestId": request_id or None,
                "sourceSessionId": session_id or None,
                "type": "immune",
                "typeLabel": "免疫",
                "organId": "immune",
                "organLabel": ORGAN_META["immune"]["name"],
                "severity": severity,
                "timestamp": timestamp,
                "decision": decision,
                "riskLevel": risk_level or None,
                "matchedRules": matched_rules[:4],
                "representativeRefs": refs,
                "title": title,
                "detail": " · ".join(
                    part
                    for part in [
                        decision,
                        f"risk {risk_level}" if risk_level else "",
                        score_text,
                        reason_codes[0] if reason_codes else "",
                        input_preview,
                        detail,
                    ]
                    if part
                ) or "已捕获到免疫门的真实决策事件。",
                "to": _build_immune_replay_path(request_id) if request_id else "/alerts",
                "linkLabel": "查看 immune 回放" if request_id else "查看免疫告警",
                "secondaryTo": _build_trace_request_path(request_id) if request_id else "/alerts",
                "secondaryLinkLabel": "查看请求链路" if request_id else "查看免疫详情",
            }
        )
    return events


def _session_has_guard_signal(session: dict[str, Any]) -> bool:
    status = _as_text(session.get("status")).upper()
    decision = _as_text(session.get("decision")).upper()
    reason_code = _as_text(session.get("reason_code")).upper()
    if status in {"REJECTED", "ERROR"}:
        return True
    if decision and decision not in {"APPROVE", "ALLOW", "PASS", "OK"}:
        return True
    return bool(reason_code)


def _build_recent_session_events(
    recent_sessions: list[dict[str, Any]],
    *,
    trace_feed_items: list[dict[str, Any]],
    include_immune_fallback: bool = True,
) -> list[dict[str, Any]]:
    if not recent_sessions:
        return []

    trace_by_request_id = {}
    for item in trace_feed_items:
        item_dict = _as_dict(item)
        session_dict = _as_dict(item_dict.get("session"))
        trace_dict = _as_dict(item_dict.get("trace"))
        request_id = _as_text(session_dict.get("request_id") or trace_dict.get("request_id") or _as_dict(trace_dict.get("summary")).get("request_id"))
        if request_id:
            trace_by_request_id[request_id] = item_dict

    events: list[dict[str, Any]] = []
    latest = _as_dict(recent_sessions[0])
    request_id = _as_text(latest.get("request_id"))
    latest_session_id = _as_text(latest.get("session_id"))
    timestamp = int(latest.get("ts_ms") or 0)
    status = _as_text(latest.get("status")).upper()
    decision = _as_text(latest.get("decision")).upper()
    reason_code = _as_text(latest.get("reason_code")).upper()
    preview = _compact_text(latest.get("input_preview"), 56)
    latest_trace_item = _as_dict(trace_by_request_id.get(request_id))
    latest_trace = _as_dict(latest_trace_item.get("trace"))
    latest_summary = _as_dict(latest_trace.get("summary"))
    latest_trace_status = _as_text(latest_trace.get("status")).upper()
    latest_trace_ts = int(latest_trace.get("ts_ms") or 0)
    latest_trace_node_count = int(latest_trace.get("node_count") or 0)
    latest_trace_intent = _format_trace_intent(latest_summary.get("intent"))
    latest_trace_route = _as_text(latest_summary.get("routing_selected"))
    latest_trace_bond_stage = _as_text(latest_summary.get("bond_stage"))
    latest_trace_reason = _as_text(latest_summary.get("reason_code")).upper() or reason_code
    latest_trace_decision = _as_text(latest_summary.get("decision")).upper() or decision
    latest_failure_event = _as_text(latest_summary.get("failure_event"))
    request_ref = {
        "type": "request",
        "id": request_id,
        "to": _build_trace_request_path(request_id),
        "label": "request",
        "subtitle": _short_id(request_id),
        "tags": [tag for tag in [status or None, latest_trace_decision or decision or None, latest_trace_route or None] if tag],
    }
    if request_id and timestamp > 0:
        request_severity = "critical" if status == "ERROR" or latest_failure_event else "warning" if status == "REJECTED" else "info"
        request_title = "活跃请求正在推进" if latest_trace_status in {"RUNNING", "PENDING"} else "最新请求已进入主链" if status == "OK" else "最新请求未完全收束"
        request_detail_parts = []
        if latest_trace_intent:
            request_detail_parts.append(f"intent {latest_trace_intent}")
        if latest_trace_decision:
            request_detail_parts.append(latest_trace_decision)
        if latest_trace_reason:
            request_detail_parts.append(latest_trace_reason)
        if latest_trace_bond_stage:
            request_detail_parts.append(f"bond {latest_trace_bond_stage}")
        if latest_trace_node_count > 0:
            request_detail_parts.append(f"{latest_trace_node_count} nodes")
        if latest_failure_event:
            request_detail_parts.append(f"failure {latest_failure_event}")
        if preview:
            request_detail_parts.append(preview)
        events.append(
            {
                "id": f"request-{request_id}",
                "type": "request",
                "typeLabel": "请求",
                "organId": "nervous",
                "organLabel": ORGAN_META["nervous"]["name"],
                "severity": request_severity,
                "timestamp": latest_trace_ts or timestamp,
                "requestId": request_id,
                "sessionId": latest_session_id or None,
                "sourceRequestId": request_id,
                "sourceSessionId": latest_session_id or None,
                "representativeRefs": [request_ref],
                "title": request_title,
                "detail": " · ".join(request_detail_parts) or "最新用户请求已可回放。",
                "to": _build_trace_request_path(request_id),
                "linkLabel": "查看请求链路",
                "secondaryTo": "/sessions",
                "secondaryLinkLabel": "查看会话流",
            }
        )

    guarded = None
    if include_immune_fallback:
        guarded = next((_as_dict(item) for item in recent_sessions if _session_has_guard_signal(_as_dict(item))), None)
    if include_immune_fallback and not guarded:
        for item in trace_feed_items:
            item_dict = _as_dict(item)
            trace_dict = _as_dict(item_dict.get("trace"))
            summary = _as_dict(trace_dict.get("summary"))
            decision_text = _as_text(summary.get("decision")).upper()
            reason_text = _as_text(summary.get("reason_code")).upper()
            governor_passed = summary.get("governor_passed")
            failure_event = _as_text(summary.get("failure_event"))
            if (decision_text and decision_text not in {"APPROVE", "ALLOW", "PASS", "OK"}) or reason_text or governor_passed is False or failure_event:
                guarded = _as_dict(item_dict.get("session")) or {"request_id": summary.get("request_id"), "ts_ms": trace_dict.get("ts_ms")}
                break
    guarded_request_id = _as_text((guarded or {}).get("request_id"))
    guarded_session_id = _as_text((guarded or {}).get("session_id"))
    guarded_timestamp = int((guarded or {}).get("ts_ms") or 0)
    if include_immune_fallback and guarded and guarded_request_id and guarded_timestamp > 0:
        guarded_status = _as_text(guarded.get("status")).upper()
        guarded_decision = _as_text(guarded.get("decision")).upper()
        guarded_reason = _as_text(guarded.get("reason_code")).upper()
        guarded_preview = _compact_text(guarded.get("input_preview"), 56)
        guarded_trace_item = _as_dict(trace_by_request_id.get(guarded_request_id))
        guarded_trace = _as_dict(guarded_trace_item.get("trace"))
        guarded_summary = _as_dict(guarded_trace.get("summary"))
        guarded_trace_reason = _as_text(guarded_summary.get("reason_code")).upper() or guarded_reason
        guarded_trace_decision = _as_text(guarded_summary.get("decision")).upper() or guarded_decision or guarded_status
        guarded_failure_event = _as_text(guarded_summary.get("failure_event"))
        events.append(
            {
                "id": f"immune-{guarded_request_id}",
                "type": "immune",
                "typeLabel": "免疫",
                "organId": "immune",
                "organLabel": ORGAN_META["immune"]["name"],
                "severity": "critical" if guarded_status == "ERROR" or guarded_failure_event else "warning",
                "timestamp": int(guarded_trace.get("ts_ms") or guarded_timestamp),
                "requestId": guarded_request_id,
                "sessionId": guarded_session_id or None,
                "sourceRequestId": guarded_request_id,
                "sourceSessionId": guarded_session_id or None,
                "representativeRefs": [
                    {
                        "type": "request",
                        "id": guarded_request_id,
                        "to": _build_trace_request_path(guarded_request_id),
                        "label": guarded_trace_reason or guarded_trace_decision or guarded_status or "immune",
                        "subtitle": _short_id(guarded_request_id),
                        "tags": [tag for tag in [guarded_status or None, guarded_trace_reason or None] if tag],
                    }
                ],
                "title": "最近请求触发免疫门控",
                "detail": " · ".join(
                    part
                    for part in [
                        guarded_trace_decision or guarded_status,
                        guarded_trace_reason,
                        f"failure {guarded_failure_event}" if guarded_failure_event else "",
                        guarded_preview,
                    ]
                    if part
                ) or "最近请求已触发门控或降级，需要沿 immune 回放继续确认。",
                "to": _build_immune_replay_path(guarded_request_id),
                "linkLabel": "查看 immune 回放",
                "secondaryTo": _build_trace_request_path(guarded_request_id),
                "secondaryLinkLabel": "查看请求链路",
            }
        )

    return events


def _build_active_trace_events(
    trace_feed_items: list[dict[str, Any]],
    *,
    excluded_request_ids: set[str],
) -> list[dict[str, Any]]:
    for item in trace_feed_items:
        item_dict = _as_dict(item)
        session_dict = _as_dict(item_dict.get("session"))
        trace_dict = _as_dict(item_dict.get("trace"))
        summary = _as_dict(trace_dict.get("summary"))
        request_id = _as_text(summary.get("request_id") or session_dict.get("request_id") or trace_dict.get("request_id"))
        session_id = _as_text(summary.get("session_id") or trace_dict.get("session_id") or session_dict.get("session_id"))
        if not request_id or request_id in excluded_request_ids:
            continue
        timestamp = int(trace_dict.get("ts_ms") or session_dict.get("ts_ms") or 0)
        if timestamp <= 0:
            continue
        trace_status = _as_text(trace_dict.get("status")).upper() or _as_text(session_dict.get("status")).upper()
        decision = _as_text(summary.get("decision")).upper()
        reason_code = _as_text(summary.get("reason_code")).upper()
        failure_event = _as_text(summary.get("failure_event"))
        intent = _format_trace_intent(summary.get("intent"))
        route = _as_text(summary.get("routing_selected"))
        bond_stage = _as_text(summary.get("bond_stage"))
        node_count = int(trace_dict.get("node_count") or 0)
        preview = _compact_text(summary.get("input_text") or session_dict.get("input_preview"), 56)
        severity = "critical" if failure_event or trace_status == "ERROR" else "warning" if trace_status in {"PENDING", "RUNNING"} and decision and decision not in {"APPROVE", "ALLOW", "PASS", "OK"} else "info"
        detail_parts = []
        if intent:
            detail_parts.append(f"intent {intent}")
        if decision:
            detail_parts.append(decision)
        if reason_code:
            detail_parts.append(reason_code)
        if route:
            detail_parts.append(f"route {route}")
        if bond_stage:
            detail_parts.append(f"bond {bond_stage}")
        if node_count > 0:
            detail_parts.append(f"{node_count} nodes")
        if failure_event:
            detail_parts.append(f"failure {failure_event}")
        if preview:
            detail_parts.append(preview)
        return [
            {
                "id": f"active-trace-{request_id}",
                "type": "request",
                "typeLabel": "请求",
                "organId": "nervous",
                "organLabel": ORGAN_META["nervous"]["name"],
                "severity": severity,
                "timestamp": timestamp,
                "requestId": request_id,
                "sessionId": session_id or None,
                "sourceRequestId": request_id,
                "sourceSessionId": session_id or None,
                "representativeRefs": [
                    {
                        "type": "request",
                        "id": request_id,
                        "to": _build_trace_request_path(request_id),
                        "label": route or "active-trace",
                        "subtitle": _short_id(request_id),
                        "tags": [tag for tag in [trace_status or None, decision or None] if tag],
                    }
                ],
                "title": "活跃链路仍在推进" if trace_status in {"RUNNING", "PENDING"} else "活跃链路刚刚收束",
                "detail": " · ".join(detail_parts) or "当前活跃请求已经可以进入 Trace 继续回放。",
                "to": _build_trace_request_path(request_id),
                "linkLabel": "查看活跃 Trace",
                "secondaryTo": "/trace",
                "secondaryLinkLabel": "查看活跃链路",
            }
        ]
    return []


def _build_targeted_request_event(
    ledger: Any,
    request_id: str,
) -> dict[str, Any] | None:
    normalized_request_id = _as_text(request_id)
    if not normalized_request_id:
        return None

    def _builder():
        (_, _build_trace_response_lightweight, *_) = _get_trace_builders()
        trace_payload = _model_dump_compat(
            _build_trace_response_lightweight(
                ledger,
                normalized_request_id,
                reason="LIFE_TIMELINE_REQUEST_FALLBACK",
                message="targeted request fallback for life timeline deep-link",
            )
        )
        return _as_dict(trace_payload)

    try:
        trace_payload = _as_dict(
            _get_obs_route_cached_response(
                cache_name="life_timeline_request_fallback",
                cache_key={"request_id": normalized_request_id},
                ttl_ms=_LIFE_TIMELINE_REQUEST_FALLBACK_CACHE_TTL_MS,
                builder=_builder,
            )
        )
    except Exception as exc:
        logger.debug("life timeline targeted request fallback degraded: %s", exc)
        return None

    summary = _as_dict(trace_payload.get("summary"))
    trace_status = _as_text(trace_payload.get("status")).upper()
    decision = _as_text(summary.get("decision")).upper()
    reason_code = _as_text(summary.get("reason_code")).upper()
    failure_event = _as_text(summary.get("failure_event"))
    intent = _format_trace_intent(summary.get("intent"))
    route = _as_text(summary.get("routing_selected"))
    bond_stage = _as_text(summary.get("bond_stage"))
    node_count = int(trace_payload.get("node_count") or 0)
    preview = _compact_text(summary.get("input_text"), 56)
    severity = "critical" if failure_event or trace_status == "ERROR" else "warning" if decision and decision not in {"APPROVE", "ALLOW", "PASS", "OK"} else "info"
    detail = " · ".join(
        part
        for part in [
            f"intent {intent}" if intent else "",
            decision,
            reason_code,
            f"route {route}" if route else "",
            f"bond {bond_stage}" if bond_stage else "",
            f"{node_count} nodes" if node_count > 0 else "",
            f"failure {failure_event}" if failure_event else "",
            preview,
        ]
        if part
    ) or "指定请求已经可回放。"
    return {
        "id": f"request-fallback-{normalized_request_id}",
        "eventId": normalized_request_id,
        "type": "request",
        "typeLabel": "请求",
        "organId": "nervous",
        "organLabel": ORGAN_META["nervous"]["name"],
        "severity": severity,
        "timestamp": int(trace_payload.get("ts_ms") or trace_payload.get("timestamp") or _now_ms()),
        "requestId": normalized_request_id,
        "sessionId": _as_text(trace_payload.get("session_id") or summary.get("session_id")) or None,
        "sourceRequestId": normalized_request_id,
        "sourceSessionId": _as_text(trace_payload.get("session_id") or summary.get("session_id")) or None,
        "representativeRefs": [
            {
                "type": "request",
                "id": normalized_request_id,
                "to": _build_trace_request_path(normalized_request_id),
                "label": route or "trace",
                "subtitle": _short_id(normalized_request_id),
                "tags": [tag for tag in [trace_status or None, decision or None] if tag],
            }
        ],
        "title": "指定请求回放",
        "detail": detail,
        "to": _build_trace_request_path(normalized_request_id),
        "linkLabel": "查看请求链路",
        "secondaryTo": "/trace",
        "secondaryLinkLabel": "进入 Trace",
    }


def _get_non_live_slots(snapshot: dict[str, Any] | None) -> list[list[str]]:
    slot_status = _as_dict((snapshot or {}).get("slot_status"))
    return [[_as_text(slot), _as_text(status)] for slot, status in slot_status.items() if _as_text(status) != "live"]


def _get_alert_organ_id(alert: dict[str, Any]) -> str:
    code = _as_text(alert.get("reason_code")).upper()
    if code.startswith("BACKGROUND_") or code in {"INTEROCEPTION_OVERLOAD", "VESTIBULAR_INSTABILITY", "HOMEOSTASIS_WARNING"}:
        return "endocrine"
    if code in {"TOPIC_RESISTANCE_ACTIVE", "WITHDRAWN_REPLY_POLICY"}:
        return "limbic"
    if code in {"PROMPT_INJECTION_DETECTED", "PROMPT_HIGH_RISK_BUILD"}:
        return "immune"
    if code == "REFLECTION_GUARD_ACTIVE" or code.startswith("FORGE_RUNTIME_"):
        return "self"
    if code == "MOTOR_DISPATCH_DEGRADED":
        return "motor"
    return "nervous"


def _build_runtime_alerts(
    snapshot: dict[str, Any] | None,
    *,
    prompt_stats: dict[str, Any] | None,
    capability_gap_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not snapshot:
        return []

    alerts: list[dict[str, Any]] = []
    ts_ms = int(snapshot.get("timestamp") or _now_ms())
    sys_mode_status = _as_dict(snapshot.get("sys_mode_status"))
    reflection_guard = _as_dict(_as_dict(snapshot.get("self_reflection_pulse")).get("narrative_guardrails"))
    silence_state = _as_dict(snapshot.get("silence_state"))
    render_policy = _as_dict(snapshot.get("render_policy"))
    runtime_summary = _as_dict(snapshot.get("runtime_summary"))
    degraded_reasons = _as_list(snapshot.get("degraded_reasons"))
    non_live_slots = _get_non_live_slots(snapshot)

    def push(reason_code: str, detail: str, *, severity: str, source: str, to: str) -> None:
        alerts.append(
            {
                "reason_code": reason_code,
                "title": _format_alert_title(reason_code),
                "detail": _compact_text(detail, 180),
                "ts_ms": ts_ms,
                "severity": severity,
                "source": source,
                "to": to,
            }
        )

    if bool(sys_mode_status.get("degraded")) or _has_text(sys_mode_status.get("degraded_reason")):
        degraded_reason = _as_text(sys_mode_status.get("degraded_reason")) or "生命体存在态轴当前处于降级状态。"
        push("RUNTIME_MODE_DEGRADED", f"生命体存在态轴当前处于降级状态：{degraded_reason}", severity="warning", source="runtime", to="/runtime-mode")

    if degraded_reasons:
        push(
            "RUNTIME_SNAPSHOT_DEGRADED",
            "；".join(_as_text(item) for item in degraded_reasons if _as_text(item)) or "运行快照中仍有关键状态未补齐。",
            severity="warning",
            source="runtime",
            to="/digital-life",
        )

    if non_live_slots:
        slots_text = "、".join(_as_text(slot) for slot, _ in non_live_slots[:6])
        push("RUNTIME_SLOT_NOT_LIVE", f"共有 {len(non_live_slots)} 个关键槽位未在线：{slots_text}", severity="warning", source="runtime", to="/digital-life")

    risk_level = _as_text(reflection_guard.get("risk_level")).lower()
    if risk_level and risk_level not in {"low", "none"}:
        tags = [str(tag) for tag in _as_list(reflection_guard.get("risk_tags")) if _as_text(tag)]
        push(
            "REFLECTION_GUARD_ACTIVE",
            f"当前自我反思护栏风险为{_format_risk_level(risk_level)}" + (f"，涉及 {'、'.join(tags)}" if tags else "。"),
            severity="critical" if risk_level in {"high", "critical"} else "warning",
            source="runtime",
            to="/self-model",
        )

    if bool(silence_state.get("topic_resistance_active")):
        resistant_topic = _as_text(silence_state.get("resistant_topic")) or "该话题"
        push("TOPIC_RESISTANCE_ACTIVE", f"当前对“{resistant_topic}”存在明显抗拒，回复会更偏谨慎。", severity="warning", source="runtime", to="/render-policy")

    if bool(render_policy.get("should_be_withdrawn")):
        posture = _format_reply_posture(_as_dict(runtime_summary.get("reply_posture")).get("style"))
        push("WITHDRAWN_REPLY_POLICY", f"当前策略更偏短答与收敛，回复姿态为{posture}。", severity="info", source="runtime", to="/render-policy")

    injection_count = int((prompt_stats or {}).get("injection_detected_count") or 0)
    if injection_count > 0:
        push("PROMPT_INJECTION_DETECTED", f"当前时间窗内检测到 {injection_count} 次提示注入风险。", severity="critical", source="prompt", to="/prompt-config")

    high_risk_count = int((prompt_stats or {}).get("high_risk_count") or 0)
    if high_risk_count > 0:
        push("PROMPT_HIGH_RISK_BUILD", f"当前时间窗内存在 {high_risk_count} 次高风险 Prompt 构建。", severity="warning", source="prompt", to="/prompt-config")

    background_runtime = _as_dict(snapshot.get("background_runtime"))
    if background_runtime:
        scheduler_status = _as_text(background_runtime.get("scheduler_status")).upper() or "UNKNOWN"
        tick_health = _as_text(background_runtime.get("tick_health")).lower() or "unknown"
        is_advancing = bool(background_runtime.get("is_advancing"))
        inner_tick_ago = background_runtime.get("seconds_since_inner_world_tick")
        missing_ticks = [str(item) for item in _as_list(background_runtime.get("core_ticks_missing")) if _as_text(item)]
        if not is_advancing:
            push(
                "BACKGROUND_RUNTIME_NOT_ADVANCING",
                f"后台推进未达预期：tick={tick_health}，scheduler={scheduler_status}，inner_world_tick 距今 {inner_tick_ago if inner_tick_ago is not None else 'unknown'}s。",
                severity="critical" if tick_health == "stalled" else "warning",
                source="runtime",
                to="/inner-world",
            )
        if missing_ticks:
            push("BACKGROUND_CORE_TICK_MISSING", f"后台核心任务缺失：{'、'.join(missing_ticks)}。", severity="warning", source="runtime", to="/automation")
        if background_runtime.get("persona_user_alignment_ok") is False:
            push("BACKGROUND_PERSONA_MISMATCH", "内在世界 user_id 与当前人格 user_id 未对齐，后台推进可能写入错误用户。", severity="critical", source="runtime", to="/inner-world")

    interoception_state = _as_dict(snapshot.get("interoception_state"))
    try:
        interoceptive_index = float(interoception_state.get("interoceptive_index") or 0)
        recovery_urgency = float(interoception_state.get("recovery_urgency") or 0)
    except Exception:
        interoceptive_index = 0.0
        recovery_urgency = 0.0
    if interoceptive_index >= 0.82 or recovery_urgency >= 0.78:
        push("INTEROCEPTION_OVERLOAD", f"内感受负载升高：interoceptive_index={interoceptive_index:.3f}，recovery_urgency={recovery_urgency:.3f}。", severity="critical" if interoceptive_index >= 0.88 or recovery_urgency >= 0.85 else "warning", source="runtime", to="/overview")

    vestibular_state = _as_dict(snapshot.get("vestibular_state"))
    try:
        vestibular_jitter = float(vestibular_state.get("jitter_index") or 0)
    except Exception:
        vestibular_jitter = 0.0
    vestibular_stability = _as_text(vestibular_state.get("stability_state")).lower()
    if vestibular_stability in {"critical", "unstable"} or vestibular_jitter >= 0.72:
        push("VESTIBULAR_INSTABILITY", f"前庭稳定性异常：state={vestibular_stability or 'unknown'}，jitter={vestibular_jitter:.3f}。", severity="critical" if vestibular_stability == "critical" or vestibular_jitter >= 0.82 else "warning", source="runtime", to="/overview")

    motor_cortex_state = _as_dict(snapshot.get("motor_cortex_state"))
    if _has_text(motor_cortex_state.get("last_plan_id")) and motor_cortex_state.get("last_dispatch_ok") is False:
        push("MOTOR_DISPATCH_DEGRADED", f"动作分发失败：plan={_as_text(motor_cortex_state.get('last_plan_id'))}，reason={_as_text(motor_cortex_state.get('last_dispatch_reason')) or 'unknown'}。", severity="warning", source="runtime", to="/overview")

    homeostasis_report = _as_dict(snapshot.get("homeostasis_report"))
    homeostasis_status = _as_text(homeostasis_report.get("status")).lower()
    if homeostasis_status in {"warning", "critical"}:
        minute_window = _as_dict(homeostasis_report.get("minute_window"))
        hour_window = _as_dict(homeostasis_report.get("hour_window"))
        push("HOMEOSTASIS_WARNING", f"生命体稳态告警：status={homeostasis_status}，minute_load={minute_window.get('avg_cognitive_load_index', 'n/a')}，hour_stress={hour_window.get('avg_stress_index', 'n/a')}。", severity="critical" if homeostasis_status == "critical" else "warning", source="runtime", to="/overview")

    forge_runtime_summary = _as_dict(_as_dict((capability_gap_data or {}).get("forge_runtime_quality")).get("summary"))
    critical_proposals = int(forge_runtime_summary.get("critical_proposals") or 0)
    unhealthy_proposals = int(forge_runtime_summary.get("unhealthy_proposals") or 0)
    tracked_proposals = int(forge_runtime_summary.get("tracked_proposals") or 0)
    try:
        avg_health_score = float(forge_runtime_summary.get("avg_health_score") or 0)
    except Exception:
        avg_health_score = 0.0
    if critical_proposals > 0:
        push("FORGE_RUNTIME_CRITICAL_PROPOSALS", f"Forge runtime has {critical_proposals} critical proposals (unhealthy={unhealthy_proposals}, tracked={tracked_proposals}, avg_health={avg_health_score:.1f}).", severity="critical", source="forge-runtime", to="/alerts?reason_code=FORGE_RUNTIME_CRITICAL_PROPOSALS")
    elif unhealthy_proposals > 0:
        push("FORGE_RUNTIME_UNHEALTHY_PROPOSALS", f"Forge runtime has {unhealthy_proposals} unhealthy proposals (tracked={tracked_proposals}, avg_health={avg_health_score:.1f}).", severity="warning", source="forge-runtime", to="/alerts?reason_code=FORGE_RUNTIME_UNHEALTHY_PROPOSALS")

    return sorted(alerts, key=lambda item: (SEVERITY_RANK.get(_as_text(item.get('severity')).lower(), 99), -int(item.get('ts_ms') or 0)))


def _build_growth_signals(runtime_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = runtime_snapshot or {}
    self_model = _as_dict(snapshot.get("self_model_snapshot"))
    self_reflection = _as_dict(snapshot.get("self_reflection_pulse"))
    growth_awareness = _as_dict(self_reflection.get("growth_awareness"))
    growth_summary = _as_dict(self_reflection.get("growth_summary"))
    emergent_growth_note = _as_text(self_reflection.get("emergent_growth_note") or snapshot.get("emergent_growth_note"))
    growth_note = _as_text(self_reflection.get("growth_note"))
    self_awareness = _as_text(self_reflection.get("self_awareness"))
    weekly_summary = _as_text(growth_summary.get("weekly_summary"))
    daily_summary = _as_text(growth_summary.get("daily_summary"))
    latest_growth_note = _as_text(growth_summary.get("latest_growth_note"))
    last_pattern_summary = _as_text(growth_summary.get("last_pattern_summary"))
    awareness_items = [
        item for item in [
            {"key": "recent_change", "label": "最近变化", "value": _as_text(growth_awareness.get("recent_change"))},
            {"key": "trait_felt", "label": "性格体感", "value": _as_text(growth_awareness.get("trait_felt"))},
            {"key": "milestone_felt", "label": "里程碑体感", "value": _as_text(growth_awareness.get("milestone_felt"))},
            {"key": "desire", "label": "内在愿望", "value": _as_text(growth_awareness.get("desire"))},
        ] if item["value"]
    ]
    active_themes = [_as_text(item) for item in _as_list(growth_summary.get("active_themes")) if _as_text(item)]
    milestones = [_as_text(item) for item in _as_list(self_model.get("growth_milestones")) if _as_text(item)]
    cycle_count = 0
    for candidate in (growth_summary.get("recent_cycle_count"), growth_summary.get("total_growth_cycles")):
        try:
            cycle_count = max(cycle_count, int(candidate or 0))
        except Exception:
            pass
    primary_summary = emergent_growth_note or weekly_summary or daily_summary or latest_growth_note or growth_note or self_awareness
    insight_sections = [
        item for item in [
            {"key": "emergent", "label": "涌现成长感知", "value": emergent_growth_note},
            {"key": "weekly", "label": "周成长摘要", "value": weekly_summary},
            {"key": "daily", "label": "日成长摘要", "value": daily_summary},
            {"key": "pattern", "label": "最近模式总结", "value": last_pattern_summary},
        ] if item["value"]
    ]
    has_any_growth = bool(primary_summary or awareness_items or active_themes or milestones or cycle_count > 0 or last_pattern_summary)
    return {
        "selfModel": self_model,
        "selfReflection": self_reflection,
        "growthAwareness": growth_awareness,
        "growthSummary": growth_summary,
        "awarenessItems": awareness_items,
        "activeThemes": active_themes,
        "milestones": milestones,
        "cycleCount": cycle_count,
        "emergentGrowthNote": emergent_growth_note,
        "latestGrowthNote": latest_growth_note,
        "lastPatternSummary": last_pattern_summary,
        "primarySummary": primary_summary,
        "insightSections": insight_sections,
        "hasAnyGrowth": has_any_growth,
        "overviewSummary": _compact_text(primary_summary or last_pattern_summary or (milestones[0] if milestones else ""), 96),
    }


def _normalize_hotspot_pathology(hotspot: dict[str, Any], *, window_label: str) -> dict[str, Any]:
    organ_id = HOTSPOT_ORGAN_MAP.get(_as_text(hotspot.get("organ_system")).lower(), "motor")
    meta = ORGAN_META[organ_id]
    representative_refs = []
    for item in _as_list(hotspot.get("representative_requests")):
        item_dict = _as_dict(item)
        request_id = _as_text(item_dict.get("request_id"))
        if not request_id:
            continue
        representative_refs.append(
            {
                "type": "request",
                "id": request_id,
                "to": _as_text(item_dict.get("target_path")) or f"/trace/{request_id}",
                "label": _as_text(hotspot.get("root_cause_code") or hotspot.get("organ_label") or "request"),
                "subtitle": _short_id(request_id),
                "tags": [tag for tag in [_as_text(hotspot.get("organ_label")), _as_text(item_dict.get("status"))] if tag],
            }
        )
    hotspot_id = _as_text(hotspot.get("hotspot_id") or hotspot.get("root_cause_code"))
    return {
        "id": f"hotspot-{hotspot_id}",
        "organId": organ_id,
        "organLabel": _as_text(hotspot.get("organ_label")) or meta["name"],
        "severity": _as_text(hotspot.get("severity")).lower() or "warning",
        "title": _as_text(hotspot.get("root_cause_code")) or "Gateway hotspot",
        "symptom": _as_text(hotspot.get("narrative") or hotspot.get("operator_focus")) or "当前窗口已形成稳定病灶热区。",
        "durationLabel": f"近 {window_label}",
        "impactLabel": _as_text(hotspot.get("operator_focus")) or meta["fallbackImpact"],
        "detailPath": _build_pathology_detail_path(f"hotspot-{hotspot_id}"),
        "recommendedPath": _as_text(hotspot.get("recommended_view_path")) or meta["path"],
        "recommendedLabel": _as_text(hotspot.get("recommended_view_label")) or meta["drilldownLabel"],
        "representativeRefs": representative_refs,
        "source": "gateway hotspot",
    }


def _normalize_alert_pathology(alert: dict[str, Any], *, window_label: str) -> dict[str, Any]:
    organ_id = _get_alert_organ_id(alert)
    meta = ORGAN_META[organ_id]
    token = _as_text(alert.get("reason_code") or alert.get("title"))
    source = _as_text(alert.get("source")) or "runtime"
    return {
        "id": f"alert-{token}",
        "organId": organ_id,
        "organLabel": meta["name"],
        "severity": _as_text(alert.get("severity")).lower() or "info",
        "title": _as_text(alert.get("title")) or token or "运行告警",
        "symptom": _as_text(alert.get("detail")) or "当前快照出现异常信号。",
        "durationLabel": "当前快照" if source == "runtime" else f"近 {window_label}",
        "impactLabel": meta["fallbackImpact"],
        "detailPath": _build_pathology_detail_path(f"alert-{token}"),
        "recommendedPath": _as_text(alert.get("to")) or meta["path"],
        "recommendedLabel": meta["drilldownLabel"],
        "representativeRefs": [],
        "source": source,
    }


def _build_pathologies(
    *,
    window_minutes: int,
    runtime_alerts: list[dict[str, Any]],
    humanoid_loop_hotspots_data: dict[str, Any] | None,
    capability_gap_data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    window_label = _format_window_label(window_minutes)
    hotspot_pathologies = [_normalize_hotspot_pathology(_as_dict(item), window_label=window_label) for item in _as_list((humanoid_loop_hotspots_data or {}).get("hotspots"))[:3]]
    runtime_pathologies = [_normalize_alert_pathology(item, window_label=window_label) for item in runtime_alerts if _as_text(item.get("severity")).lower() in {"critical", "warning"}][:4]
    forge_summary = _as_dict(_as_dict((capability_gap_data or {}).get("forge_runtime_quality")).get("summary"))
    forge_critical = int(forge_summary.get("critical_proposals") or 0)
    forge_unhealthy = int(forge_summary.get("unhealthy_proposals") or 0)
    capability_pathology = []
    if forge_critical > 0 or forge_unhealthy > 0:
        capability_pathology = [{
            "id": "forge-runtime-health",
            "organId": "self",
            "organLabel": ORGAN_META["self"]["name"],
            "severity": "critical" if forge_critical > 0 else "warning",
            "title": "Forge 关键提案堆积" if forge_critical > 0 else "Forge 提案处于不健康区",
            "symptom": f"当前有 {forge_critical} 条关键提案未收束，继续拖延会影响自我系统演化。" if forge_critical > 0 else f"当前有 {forge_unhealthy} 条提案处于不健康区，需要继续检查 forge runtime 质量。",
            "durationLabel": f"近 {window_label}",
            "impactLabel": ORGAN_META["self"]["fallbackImpact"],
            "detailPath": _build_pathology_detail_path("forge-runtime-health"),
            "recommendedPath": "/insights/global",
            "recommendedLabel": "查看 Forge 详情",
            "representativeRefs": [],
            "source": "forge runtime",
        }]
    items = hotspot_pathologies + runtime_pathologies + capability_pathology
    items.sort(key=lambda item: (SEVERITY_RANK.get(_as_text(item.get("severity")).lower(), 99), -(len(_as_list(item.get("representativeRefs"))))))
    return items[:5]


def _build_timeline_events(
    *,
    window_minutes: int,
    overview_updated_at: int | None,
    runtime_snapshot: dict[str, Any] | None,
    runtime_updated_at: int | None,
    current_emotion: str,
    current_bond_stage: str,
    bond_trend: str,
    has_memory_data: bool,
    has_memory_samples: bool,
    avg_memory_success_rate: int | None,
    sampled_memory_subsystem_count: int,
    configured_memory_subsystem_count: int,
    humanoid_loop_data: dict[str, Any] | None,
    humanoid_loop_hotspots_data: dict[str, Any] | None,
    recent_sessions: list[dict[str, Any]],
    trace_feed_items: list[dict[str, Any]],
    pathologies: list[dict[str, Any]],
    growth_signals: dict[str, Any],
    memory_lineage_rows: list[dict[str, Any]],
    bond_lineage_rows: list[dict[str, Any]],
    scheduler_tick_rows: list[dict[str, Any]],
    immune_lineage_rows: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    has_runtime_snapshot = bool(runtime_snapshot)
    runtime_ts = int((runtime_snapshot or {}).get("timestamp") or runtime_updated_at or 0)
    gateway_ts = int((humanoid_loop_hotspots_data or {}).get("timestamp") or overview_updated_at or runtime_ts or 0)
    memory_ts = int(overview_updated_at or runtime_ts or 0)
    background_runtime = _as_dict((runtime_snapshot or {}).get("background_runtime"))
    memory_lineage_events = _build_memory_lineage_events(memory_lineage_rows)
    bond_lineage_events = _build_bond_lineage_events(bond_lineage_rows)
    scheduler_tick_events = _build_scheduler_tick_events(scheduler_tick_rows)
    immune_lineage_events = _build_immune_lineage_events(immune_lineage_rows)

    if has_runtime_snapshot and runtime_ts > 0:
        events.append({"id": "runtime-refresh", "type": "snapshot", "typeLabel": "快照", "organId": "nervous", "organLabel": ORGAN_META["nervous"]["name"], "severity": "info", "timestamp": runtime_ts, "title": "生命体快照刷新", "detail": _as_text(_as_dict((runtime_snapshot or {}).get("runtime_summary")).get("headline")) or "运行快照已更新，可继续沿器官系统与病灶排查。", "to": "/digital-life", "linkLabel": "查看总览"})
    if has_runtime_snapshot and current_emotion and current_emotion != "—":
        events.append({"id": "emotion-state", "type": "emotion", "typeLabel": "情绪", "organId": "limbic", "organLabel": ORGAN_META["limbic"]["name"], "severity": "info", "timestamp": runtime_ts, "title": "情绪波形采样", "detail": f"当前情绪 {current_emotion}，已回流到驾驶舱的生命体征判断。", "to": "/emotion", "linkLabel": "查看情绪"})
    if not bond_lineage_events and has_runtime_snapshot and current_bond_stage and current_bond_stage != "—":
        events.append({"id": "bond-state", "type": "bond", "typeLabel": "关系", "organId": "limbic", "organLabel": ORGAN_META["limbic"]["name"], "severity": "info", "timestamp": runtime_ts, "title": "关系温度更新", "detail": f"{current_bond_stage} · 趋势 {bond_trend}" if bond_trend and bond_trend != "—" else f"{current_bond_stage} · 等待趋势聚合", "to": "/bond", "linkLabel": "查看关系"})
    if not memory_lineage_events and has_memory_data:
        events.append({"id": "memory-window", "type": "memory", "typeLabel": "记忆", "organId": "memory", "organLabel": ORGAN_META["memory"]["name"], "severity": "info" if has_memory_samples else "warning", "timestamp": memory_ts, "title": "记忆窗口已采样" if has_memory_samples else "记忆系统待采样", "detail": f"平均成功率 {avg_memory_success_rate}% · {sampled_memory_subsystem_count}/{configured_memory_subsystem_count} 个子系统有样本" if has_memory_samples else "当前窗口暂无足够样本，记忆系统仍需更多实际流量。", "to": "/memory", "linkLabel": "查看记忆"})
    if not scheduler_tick_events and background_runtime:
        is_advancing = bool(background_runtime.get("is_advancing"))
        events.append({"id": "rhythm", "type": "tick", "typeLabel": "节律", "organId": "endocrine", "organLabel": ORGAN_META["endocrine"]["name"], "severity": "info" if is_advancing else "warning", "timestamp": runtime_ts, "title": "后台推进继续前进" if is_advancing else "后台推进暂停", "detail": f"scheduler {_as_text(background_runtime.get('scheduler_status')).upper() or '--'} · tick {_as_text(background_runtime.get('tick_health')).lower() or '--'}", "to": "/automation", "linkLabel": "查看节律"})
    total_gateway_requests = int((humanoid_loop_data or {}).get("total_gateway_requests") or 0)
    if total_gateway_requests > 0:
        closed_loop_rate = float((humanoid_loop_data or {}).get("closed_loop_rate") or 0)
        events.append({"id": "gateway-loop", "type": "execution", "typeLabel": "执行", "organId": "motor", "organLabel": ORGAN_META["motor"]["name"], "severity": "warning" if closed_loop_rate < 70 else "info", "timestamp": gateway_ts, "title": "执行闭环更新", "detail": f"闭环 {_format_percent((humanoid_loop_data or {}).get('closed_loop_rate'))} · ACK {_format_percent((humanoid_loop_data or {}).get('ack_touch_rate'))} · 偏航恢复 {_format_percent((humanoid_loop_data or {}).get('deviation_recovery_rate'))}", "to": "/gateway", "linkLabel": "查看闭环"})
    events.extend(immune_lineage_events)
    events.extend(memory_lineage_events)
    events.extend(bond_lineage_events)
    events.extend(scheduler_tick_events)
    recent_session_events = _build_recent_session_events(
        recent_sessions,
        trace_feed_items=trace_feed_items,
        include_immune_fallback=not immune_lineage_events,
    )
    events.extend(recent_session_events)
    request_ids_in_events = {
        _as_text(item.get("requestId"))
        for item in recent_session_events
        if _as_text(item.get("requestId"))
    }
    events.extend(_build_active_trace_events(trace_feed_items, excluded_request_ids=request_ids_in_events))
    if pathologies:
        top_pathology = pathologies[0]
        events.append({"id": "top-pathology", "type": "pathology", "typeLabel": "病灶", "organId": top_pathology.get("organId"), "organLabel": top_pathology.get("organLabel"), "severity": top_pathology.get("severity"), "pathologyId": top_pathology.get("id"), "representativeRefs": _clone_payload(top_pathology.get("representativeRefs") or [])[:2], "timestamp": gateway_ts or runtime_ts, "title": "主病灶升温", "detail": f"{_as_text(top_pathology.get('organLabel'))} · {_as_text(top_pathology.get('title'))}", "to": _as_text(top_pathology.get("detailPath")) or _build_pathology_detail_path(top_pathology.get("id")), "linkLabel": "查看病灶详情", "secondaryTo": _as_text(top_pathology.get("recommendedPath")), "secondaryLinkLabel": _as_text(top_pathology.get("recommendedLabel"))})
    if bool(growth_signals.get("hasAnyGrowth")):
        events.append({"id": "self-growth", "type": "self", "typeLabel": "自我", "organId": "self", "organLabel": ORGAN_META["self"]["name"], "severity": "info", "timestamp": runtime_ts or memory_ts, "title": "自我系统继续成形", "detail": _as_text(growth_signals.get("primarySummary") or growth_signals.get("overviewSummary")) or "已形成新的成长样本。", "to": "/self-model", "linkLabel": "查看自我模型"})
    items = [item for item in events if int(item.get("timestamp") or 0) > 0]
    items.sort(
        key=lambda item: (
            TIMELINE_EVENT_PRIORITY.get(_as_text(item.get("type")).lower(), 99),
            -int(item.get("timestamp") or 0),
        )
    )
    if limit is None or limit <= 0:
        return items
    return items[:limit]


def _pick_status(*, has_data: bool, critical: bool, warning: bool) -> str:
    if not has_data:
        return "pending"
    if critical:
        return "critical"
    if warning:
        return "warning"
    return "stable"


def _build_life_state(
    *,
    has_runtime_snapshot: bool,
    mode_label: str,
    runtime_alerts: list[dict[str, Any]],
    background_runtime: dict[str, Any],
    humanoid_loop_alerts_data: dict[str, Any] | None,
) -> dict[str, Any]:
    if not has_runtime_snapshot:
        return {"label": "待连接", "tone": "pending", "summary": "当前没有接到生命体运行快照，主体视图会以中性占位展示。"}
    critical_count = sum(1 for item in runtime_alerts if _as_text(item.get("severity")) == "critical")
    warning_count = sum(1 for item in runtime_alerts if _as_text(item.get("severity")) == "warning")
    gateway_severity = _as_text((humanoid_loop_alerts_data or {}).get("severity")).lower()
    not_advancing = bool(background_runtime) and background_runtime.get("is_advancing") is False
    if critical_count > 0 or gateway_severity == "critical":
        return {"label": "失衡", "tone": "critical", "summary": "当前存在高风险病灶，已经开始影响生命体的回复、节律或执行闭环。"}
    if not_advancing or warning_count > 0 or gateway_severity == "warning":
        return {"label": "紧张", "tone": "warning", "summary": "整体仍在线，但部分器官系统已经出现结构性波动，建议优先查看病灶面板。"}
    if mode_label == "睡眠":
        return {"label": "离线演化", "tone": "stable", "summary": "当前以离线推进为主，外显交互收缩，但内在生命流仍在继续演化。"}
    return {"label": "稳定", "tone": "stable", "summary": "生命流当前可以稳定穿过关系、情绪、记忆与执行系统，主体状态清晰可读。"}


def _build_diagnostic_guidance(
    *,
    life_state: dict[str, Any],
    primary_organ: dict[str, Any] | None,
    primary_pathology: dict[str, Any] | None,
    timeline_events: list[dict[str, Any]],
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if primary_pathology:
        actions.append({"id": "pathology", "tone": _as_text(primary_pathology.get("severity")) or "warning", "eyebrow": "主病灶", "title": f"{_as_text(primary_pathology.get('organLabel'))} · {_as_text(primary_pathology.get('title'))}", "detail": _as_text(primary_pathology.get("symptom")), "impact": _as_text(primary_pathology.get("impactLabel")), "to": _as_text(primary_pathology.get("detailPath")) or _build_pathology_detail_path(primary_pathology.get("id")), "label": "进入病灶详情", "secondaryTo": _build_life_timeline_path(organ_id=primary_pathology.get("organId"), event_type="pathology"), "secondaryLabel": "查看病灶事件链"})
    if primary_organ:
        impacts = _as_list(primary_organ.get("downstreamImpacts"))
        actions.append({"id": "organ", "tone": _as_text(primary_organ.get("status")) or "pending", "eyebrow": "首要器官", "title": _as_text(primary_organ.get("name")), "detail": _as_text(primary_organ.get("summary")), "impact": _as_text(impacts[0] if impacts else "继续沿对应器官页判断当前行为影响。"), "to": _as_text(primary_organ.get("drilldownPath")) or "/digital-life", "label": _as_text(primary_organ.get("drilldownLabel")) or "查看器官详情", "secondaryTo": _build_life_timeline_path(organ_id=primary_organ.get("id")), "secondaryLabel": "查看相关生命事件"})
    lead_event = timeline_events[0] if timeline_events else None
    if lead_event:
        actions.append({"id": "timeline", "tone": _as_text(lead_event.get("severity")) or "info", "eyebrow": "最近事件", "title": _as_text(lead_event.get("title")), "detail": _as_text(lead_event.get("detail")), "impact": f"所属器官 {_as_text(lead_event.get('organLabel')) or '未分类'}，适合沿统一事件链回看状态是如何升温的。", "to": _as_text(lead_event.get("to")) or _build_life_timeline_path(organ_id=lead_event.get("organId"), event_type=lead_event.get("type"), request_id=lead_event.get("requestId")), "label": _as_text(lead_event.get("linkLabel")) or "查看事件详情", "secondaryTo": _build_life_timeline_path(organ_id=lead_event.get("organId"), event_type=lead_event.get("type"), request_id=lead_event.get("requestId")), "secondaryLabel": "进入生命时间轴"})
    if not actions:
        actions.append({"id": "recovery", "tone": _as_text(life_state.get("tone")) or "pending", "eyebrow": "观测恢复", "title": "先恢复生命体快照", "detail": "当前还没有足够的运行快照、病灶或事件样本，因此驾驶舱无法给出可靠的首要器官与主病灶判断。", "impact": "先恢复 runtime / memory / gateway 观测后，首页的器官排序、病灶优先级和事件链建议才会重新变得可信。", "to": "/life-timeline", "label": "查看生命时间轴", "secondaryTo": "/runtime-mode", "secondaryLabel": "查看存在态"})
    if primary_pathology:
        summary = f"当前最该先处理的是 {_as_text(primary_pathology.get('organLabel'))} 的病灶，然后回到对应器官和生命事件链确认影响范围。"
    elif primary_organ:
        summary = f"当前最值得先看的器官是 {_as_text(primary_organ.get('name'))}，再结合最近事件链判断它是否已经开始影响整体主体状态。"
    elif _as_text(life_state.get("tone")) == "stable":
        summary = "当前生命体没有形成稳定病灶，建议先沿时间轴观察最近事件是否开始聚成异常模式。"
    else:
        summary = "当前观测样本仍不足，建议先恢复运行快照，再回到时间轴与病灶中心继续诊断。"
    return {"summary": summary, "actions": actions[:3]}


def _build_life_body_payload(
    window_minutes: int,
    *,
    ledger: Any,
    projection_store: Any,
) -> dict[str, Any]:
    from host_brain.app.observability_runtime.projection_ledger_view import (
        ProjectionLedgerView,
    )
    overview_payload = asyncio.run(
        _build_digital_life_overview_snapshot_async(
            window_minutes,
            projection_view=ProjectionLedgerView(projection_store),
            projection_store=projection_store,
        )
    )
    runtime_document, runtime_projection = read_process_projection(
        projection_store,
        RUNTIME_SNAPSHOT_KIND,
        RUNTIME_SNAPSHOT_KEY,
        max_age_ms=RUNTIME_MAX_AGE_MS,
    )
    runtime_payload = dict(runtime_document or {})
    if runtime_document is not None:
        runtime_payload["projection"] = runtime_projection
    organ_tribe_document, organ_tribe_projection = read_process_projection(
        projection_store,
        ORGAN_TRIBE_KIND,
        ORGAN_TRIBE_KEY,
        max_age_ms=ORGAN_TRIBE_MAX_AGE_MS,
    )
    organ_tribe_data = dict(organ_tribe_document or {})
    if organ_tribe_document is not None:
        organ_tribe_data["projection"] = organ_tribe_projection
    prompt_stats = _model_dump_compat(
        build_prompt_builder_stats_from_view(
            ProjectionLedgerView(projection_store),
            window_minutes=window_minutes,
        )
    )
    memory_data = _as_dict(overview_payload.get("memoryData"))
    runtime_snapshot = _as_dict(runtime_payload)
    vts_data = _as_dict(overview_payload.get("vtsData"))
    services_data = _as_dict(overview_payload.get("servicesData"))
    subsystems_data = _as_dict(overview_payload.get("subsystemsData"))
    capability_gap_data = _as_dict(overview_payload.get("capabilityGapData"))
    capability_gap_projection = _as_dict(
        overview_payload.get("capabilityGapProjection")
    )
    capability_gap_available = bool(
        capability_gap_projection.get(
            "available",
            overview_payload.get("capabilityGapData") is not None,
        )
    )
    humanoid_loop_data = _as_dict(overview_payload.get("humanoidLoopData"))
    humanoid_loop_alerts_data = _as_dict(overview_payload.get("humanoidLoopAlertsData"))
    humanoid_loop_hotspots_data = _as_dict(overview_payload.get("humanoidLoopHotspotsData"))
    emotion_context = _as_dict(runtime_snapshot.get("emotion_context"))
    bond_state = _as_dict(runtime_snapshot.get("bond_state"))
    motive_state = _as_dict(runtime_snapshot.get("motive_state"))
    offline_state = _as_dict(runtime_snapshot.get("offline_state"))
    memory_subsystems = [_as_dict(item) for item in _as_list(memory_data.get("subsystems"))]
    memory_summary = _summarize_success_rate_samples(memory_subsystems)
    top_emotions = _as_list(vts_data.get("top_emotions"))
    top_emotion_item = _as_dict(top_emotions[0]) if top_emotions else {}
    current_emotion = _format_emotion_tag(emotion_context.get("emotion_tag") or top_emotion_item.get("emotion_tag") or top_emotion_item.get("tag"))
    current_bond_stage = _format_stage(bond_state.get("current_stage"))
    bond_trend = _format_trend(bond_state.get("trend"))
    dominant_motive = _format_dominant_motive(motive_state.get("dominant_motive"))
    reply_posture = _format_reply_posture(_as_dict(_as_dict(runtime_snapshot.get("runtime_summary")).get("reply_posture")).get("style"))
    risk_level = _format_risk_level(_as_dict(_as_dict(runtime_snapshot.get("self_reflection_pulse")).get("narrative_guardrails")).get("risk_level"))
    absence_band = _format_absence_band(offline_state.get("absence_band"))
    non_live_slots = _get_non_live_slots(runtime_snapshot)
    growth_signals = _build_growth_signals(runtime_snapshot)
    recent_sessions_projection = _get_recent_sessions_projection(
        window_minutes,
        projection_store=projection_store,
    )
    recent_sessions = [_as_dict(item) for item in _as_list(recent_sessions_projection.get("items"))]
    trace_feed_projection = _get_trace_feed_projection(projection_store)
    trace_feed_items = [_as_dict(item) for item in _as_list(trace_feed_projection.get("items"))]
    memory_lineage_rows = _fetch_recent_ledger_rows(
        ledger,
        "memory_ops_log",
        window_minutes=window_minutes,
        limit=_LIFE_LINEAGE_MEMORY_LIMIT,
    )
    bond_lineage_rows = _fetch_recent_ledger_rows(
        ledger,
        "bond_update_log",
        window_minutes=window_minutes,
        limit=_LIFE_LINEAGE_BOND_LIMIT,
    )
    scheduler_tick_rows = _fetch_recent_ledger_rows(
        ledger,
        "scheduler_event_log",
        window_minutes=window_minutes,
        limit=_LIFE_LINEAGE_TICK_LIMIT,
    )
    immune_lineage_rows = _fetch_recent_immune_rows(
        ledger,
        window_minutes=window_minutes,
        limit=_LIFE_LINEAGE_IMMUNE_LIMIT,
        scan_limit=_LIFE_LINEAGE_IMMUNE_SCAN_LIMIT,
    )
    runtime_alerts = _build_runtime_alerts(runtime_snapshot, prompt_stats=prompt_stats, capability_gap_data=capability_gap_data)
    pathologies = _build_pathologies(window_minutes=window_minutes, runtime_alerts=runtime_alerts, humanoid_loop_hotspots_data=humanoid_loop_hotspots_data, capability_gap_data=capability_gap_data)
    runtime_updated_at = int(runtime_snapshot.get("timestamp") or 0) or None
    overview_updated_at = max(int(overview_payload.get("timestamp") or 0), int(runtime_updated_at or 0)) or None
    timeline_event_pool = _build_timeline_events(window_minutes=window_minutes, overview_updated_at=overview_updated_at, runtime_snapshot=runtime_snapshot, runtime_updated_at=runtime_updated_at, current_emotion=current_emotion, current_bond_stage=current_bond_stage, bond_trend=bond_trend, has_memory_data=bool(memory_data), has_memory_samples=memory_summary["averageRate"] is not None, avg_memory_success_rate=memory_summary["averageRate"], sampled_memory_subsystem_count=memory_summary["sampledCount"], configured_memory_subsystem_count=memory_summary["configuredCount"], humanoid_loop_data=humanoid_loop_data, humanoid_loop_hotspots_data=humanoid_loop_hotspots_data, recent_sessions=recent_sessions, trace_feed_items=trace_feed_items, pathologies=pathologies, growth_signals=growth_signals, memory_lineage_rows=memory_lineage_rows, bond_lineage_rows=bond_lineage_rows, scheduler_tick_rows=scheduler_tick_rows, immune_lineage_rows=immune_lineage_rows, limit=None)
    timeline_events = timeline_event_pool[:6]
    active_subsystem_count = sum(1 for item in _as_list(subsystems_data.get("subsystems")) if int(_as_dict(item).get("total") or 0) > 0)
    organ_systems_preview = [
        {"id": "nervous", "name": ORGAN_META["nervous"]["name"], "status": _pick_status(has_data=bool(runtime_snapshot) or bool(services_data), critical=len(non_live_slots) >= 4 or int(services_data.get("down") or 0) > 0, warning=len(non_live_slots) > 0 or int(services_data.get("degraded") or 0) > 0), "summary": f"当前意识链处于{_format_mode_label(runtime_snapshot.get('sys_mode'))}，回复姿态 {reply_posture or '--'}。" if runtime_snapshot else "等待运行快照恢复后，再判断意识链连续性。", "drilldownPath": ORGAN_META["nervous"]["path"], "drilldownLabel": ORGAN_META["nervous"]["drilldownLabel"], "downstreamImpacts": ["主链路可解释性下降，建议继续沿 Trace 回放。" if non_live_slots else "当前主链路可以直接下钻到 Trace 与 Session。"]},
        {"id": "limbic", "name": ORGAN_META["limbic"]["name"], "status": _pick_status(has_data=bool(runtime_snapshot) or _has_text(current_emotion) or _has_text(current_bond_stage), critical=any(_as_text(item.get("reason_code")) == "TOPIC_RESISTANCE_ACTIVE" for item in runtime_alerts), warning=reply_posture in {"收敛", "谨慎"} or not _has_text(current_emotion)), "summary": f"当前情绪 {current_emotion or '--'}，关系阶段 {current_bond_stage or '--'}。" if _has_text(current_emotion) or _has_text(current_bond_stage) else "等待情绪与关系样本聚合。", "drilldownPath": "/bond", "drilldownLabel": ORGAN_META["limbic"]["drilldownLabel"], "downstreamImpacts": [f"当前回复姿态 {reply_posture or '--'}，会直接影响陪伴感与亲密度表达。"]},
        {"id": "memory", "name": ORGAN_META["memory"]["name"], "status": _pick_status(has_data=bool(memory_data), critical=False, warning=memory_summary["averageRate"] is None or (memory_summary["averageRate"] is not None and memory_summary["averageRate"] < 70)), "summary": f"平均成功率 {memory_summary['averageRate']}% · {memory_summary['sampledCount']}/{memory_summary['configuredCount']} 个子系统有样本。" if memory_summary["averageRate"] is not None else "记忆统计已接入，但当前窗口暂无足够样本。", "drilldownPath": ORGAN_META["memory"]["path"], "drilldownLabel": ORGAN_META["memory"]["drilldownLabel"], "downstreamImpacts": [ORGAN_META["memory"]["fallbackImpact"]]},
        {"id": "endocrine", "name": ORGAN_META["endocrine"]["name"], "status": _pick_status(has_data=bool(_as_dict(runtime_snapshot.get("background_runtime"))), critical=any(_as_text(item.get("reason_code")) == "BACKGROUND_RUNTIME_NOT_ADVANCING" and _as_text(item.get("severity")) == "critical" for item in runtime_alerts), warning=any(_as_text(item.get("reason_code")).startswith("BACKGROUND_") for item in runtime_alerts)), "summary": "后台推进已接入。" if _as_dict(runtime_snapshot.get("background_runtime")) else "等待后台推进快照。", "drilldownPath": ORGAN_META["endocrine"]["path"], "drilldownLabel": ORGAN_META["endocrine"]["drilldownLabel"], "downstreamImpacts": [ORGAN_META["endocrine"]["fallbackImpact"]]},
        {"id": "motor", "name": ORGAN_META["motor"]["name"], "status": _pick_status(has_data=bool(humanoid_loop_data), critical=_as_text(humanoid_loop_alerts_data.get("severity")) == "critical", warning=(_as_text(humanoid_loop_alerts_data.get("severity")) == "warning" or (humanoid_loop_data and float(humanoid_loop_data.get("closed_loop_rate") or 0) < 70))), "summary": f"闭环 {_format_percent(humanoid_loop_data.get('closed_loop_rate'))} · ACK {_format_percent(humanoid_loop_data.get('ack_touch_rate'))}。" if humanoid_loop_data else "等待执行闭环样本。", "drilldownPath": ORGAN_META["motor"]["path"], "drilldownLabel": ORGAN_META["motor"]["drilldownLabel"], "downstreamImpacts": [ORGAN_META["motor"]["fallbackImpact"]]},
        {"id": "immune", "name": ORGAN_META["immune"]["name"], "status": _pick_status(has_data=bool(runtime_snapshot) or bool(prompt_stats), critical=any(_as_text(item.get("severity")) == "critical" and _get_alert_organ_id(item) == "immune" for item in runtime_alerts), warning=any(_get_alert_organ_id(item) == "immune" for item in runtime_alerts)), "summary": f"当前叙事护栏风险 {risk_level or '--'}。" if runtime_snapshot else "等待免疫与提示构建样本。", "drilldownPath": ORGAN_META["immune"]["path"], "drilldownLabel": ORGAN_META["immune"]["drilldownLabel"], "downstreamImpacts": [ORGAN_META["immune"]["fallbackImpact"]]},
        {"id": "self", "name": ORGAN_META["self"]["name"], "status": _pick_status(has_data=bool(runtime_snapshot) or bool(capability_gap_data), critical=any(_as_text(item.get("severity")) == "critical" and _get_alert_organ_id(item) == "self" for item in runtime_alerts), warning=bool(growth_signals.get("hasAnyGrowth")) is False), "summary": _as_text(growth_signals.get("overviewSummary")) or "自我系统在线，等待更多成长样本聚合。", "drilldownPath": ORGAN_META["self"]["path"], "drilldownLabel": ORGAN_META["self"]["drilldownLabel"], "downstreamImpacts": [ORGAN_META["self"]["fallbackImpact"]]},
    ]
    organ_systems_preview.sort(key=lambda item: STATUS_RANK.get(_as_text(item.get("status")), 99))
    primary_organ = next((item for item in organ_systems_preview if _as_text(item.get("status")) != "pending"), None)
    primary_pathology = pathologies[0] if pathologies else None
    life_state = _build_life_state(has_runtime_snapshot=bool(runtime_snapshot), mode_label=_format_mode_label(runtime_snapshot.get("sys_mode")), runtime_alerts=runtime_alerts, background_runtime=_as_dict(runtime_snapshot.get("background_runtime")), humanoid_loop_alerts_data=humanoid_loop_alerts_data)
    diagnostic_guidance = _build_diagnostic_guidance(life_state=life_state, primary_organ=primary_organ, primary_pathology=primary_pathology, timeline_events=timeline_events)
    payload = LifeBodyResponse(window_minutes=window_minutes, timestamp=_now_ms(), runtimeSnapshot=runtime_snapshot, promptStats=prompt_stats, vtsData=vts_data, memoryData=memory_data, servicesData=services_data, subsystemsData=subsystems_data, organTribeData=(organ_tribe_data or None), capabilityGapData=(capability_gap_data if capability_gap_available else None), capabilityGapProjection=capability_gap_projection, humanoidLoopData=humanoid_loop_data, humanoidLoopTrendData=_as_dict(overview_payload.get("humanoidLoopTrendData")), humanoidLoopAlertsData=humanoid_loop_alerts_data, humanoidLoopRemediationData=_as_dict(overview_payload.get("humanoidLoopRemediationData")), humanoidLoopDiagnosticsData=_as_dict(overview_payload.get("humanoidLoopDiagnosticsData")), humanoidLoopHotspotsData=humanoid_loop_hotspots_data, humanoidLoopHotspotTrendData=_as_dict(overview_payload.get("humanoidLoopHotspotTrendData")), humanoidLoopSourceCorrelationData=_as_dict(overview_payload.get("humanoidLoopSourceCorrelationData")), runtimeAlerts=runtime_alerts, nonLiveSlots=non_live_slots, growthSignals=growth_signals, derived={"hasRuntimeSnapshot": bool(runtime_snapshot), "hasMemoryData": bool(memory_data), "hasMemorySamples": memory_summary["averageRate"] is not None, "avgMemorySuccessRate": memory_summary["averageRate"], "sampledMemorySubsystemCount": memory_summary["sampledCount"], "configuredMemorySubsystemCount": memory_summary["configuredCount"], "hasCapabilityGapData": capability_gap_available, "overviewUpdatedAt": overview_updated_at, "runtimeUpdatedAt": runtime_updated_at, "currentBondStage": current_bond_stage, "currentEmotion": current_emotion, "dominantMotive": dominant_motive, "replyPosture": reply_posture, "riskLevel": risk_level, "absenceBand": absence_band, "bondTrend": bond_trend, "activeSubsystemCount": active_subsystem_count, "recentSessionCount": len(recent_sessions), "recentSessionWindowMinutes": int(recent_sessions_projection.get("windowMinutes") or 0), "activeTraceFeedCount": len(trace_feed_items), "memoryLineageCount": len(memory_lineage_rows), "bondLineageCount": len(bond_lineage_rows), "tickLineageCount": len(scheduler_tick_rows), "immuneLineageCount": len(immune_lineage_rows)}, model={"organSystems": organ_systems_preview, "lifeState": life_state, "pathologies": pathologies, "primaryPathology": primary_pathology, "timelineEvents": timeline_events, "timelineEventPool": timeline_event_pool, "primaryOrgan": primary_organ, "diagnosticGuidance": diagnostic_guidance}, meta={"aggregateSource": "obs-sidecar-life-body", "projectionMode": "derived-from-runtime-and-overview-snapshots-plus-bounded-ledger-lineage", "lifeBodyRouteCacheTtlMs": _LIFE_BODY_ROUTE_CACHE_TTL_MS, "promptStatsCacheTtlMs": 0, "projectionWindowMinutes": LIFE_BODY_PROJECTION_WINDOW_MINUTES, "recentSessionsLimit": _LIFE_BODY_RECENT_SESSIONS_LIMIT, "recentSessionsWindowMinutes": int(recent_sessions_projection.get("windowMinutes") or 0), "traceFeedMeta": _as_dict(trace_feed_projection.get("meta")), "lineageLedger": {"memoryRows": len(memory_lineage_rows), "bondRows": len(bond_lineage_rows), "tickRows": len(scheduler_tick_rows), "immuneRows": len(immune_lineage_rows), "memoryLimit": _LIFE_LINEAGE_MEMORY_LIMIT, "bondLimit": _LIFE_LINEAGE_BOND_LIMIT, "tickLimit": _LIFE_LINEAGE_TICK_LIMIT, "immuneLimit": _LIFE_LINEAGE_IMMUNE_LIMIT, "immuneScanLimit": _LIFE_LINEAGE_IMMUNE_SCAN_LIMIT}})
    return payload.model_dump()


def _get_life_body_snapshot(
    store: QueryProjectionStore,
    window_minutes: int,
) -> dict[str, Any]:
    payload, projection = read_process_projection(
        store,
        LIFE_BODY_KIND,
        window_key(window_minutes),
        max_age_ms=LIFE_BODY_MAX_AGE_MS,
    )
    response = _clone_payload(payload) if payload is not None else {
        "window_minutes": int(window_minutes),
        "timestamp": _now_ms(),
    }
    meta = _as_dict(response.get("meta"))
    meta["projection"] = projection
    response["meta"] = meta
    return response


@router.get("/life-body", response_model=LifeBodyResponse)
def get_life_body(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080, description="聚合时间窗口（分钟）"),
):
    store = require_obs_query_projection_store(request)
    return LifeBodyResponse(**_get_life_body_snapshot(store, window_minutes))


@router.get("/life-timeline", response_model=LifeTimelineResponse)
def get_life_timeline(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080, description="聚合时间窗口（分钟）"),
    event_type: str = Query("", alias="type", description="事件类型过滤"),
    organ: str = Query("", description="器官系统过滤"),
    request_id: str = Query("", description="request 过滤（第一版为 representative ref 级 best-effort 过滤）"),
    session_id: str = Query("", description="session 过滤"),
    tick_id: str = Query("", description="tick / scheduler 事件过滤"),
):
    store = require_obs_query_projection_store(request)
    payload = _get_life_body_snapshot(store, window_minutes)
    model = _as_dict(payload.get("model"))
    items = _clone_payload(_as_list(model.get("timelineEventPool") or model.get("timelineEvents")))
    normalized_type = event_type.strip() if isinstance(event_type, str) else ""
    normalized_organ = organ.strip() if isinstance(organ, str) else ""
    normalized_request_id = request_id.strip() if isinstance(request_id, str) else ""
    normalized_session_id = session_id.strip() if isinstance(session_id, str) else ""
    normalized_tick_id = tick_id.strip() if isinstance(tick_id, str) else ""
    filtered = []
    for item in items:
        item_dict = _as_dict(item)
        if normalized_type and _as_text(item_dict.get("type")) != normalized_type:
            continue
        if normalized_organ and _as_text(item_dict.get("organId")) != normalized_organ:
            continue
        if normalized_request_id and _extract_request_id_from_event(item_dict) != normalized_request_id:
            continue
        if normalized_session_id and _extract_session_id_from_event(item_dict) != normalized_session_id:
            continue
        if normalized_tick_id and _extract_tick_id_from_event(item_dict) != normalized_tick_id:
            continue
        filtered.append(item_dict)
    return LifeTimelineResponse(window_minutes=window_minutes, timestamp=_now_ms(), items=filtered, total=len(items), filtered_total=len(filtered), primaryPathology=_as_dict(model.get("primaryPathology")) or None, primaryOrgan=_as_dict(model.get("primaryOrgan")) or None, filters={"type": normalized_type or None, "organ": normalized_organ or None, "request_id": normalized_request_id or None, "session_id": normalized_session_id or None, "tick_id": normalized_tick_id or None}, meta={"aggregateSource": "obs-sidecar-life-timeline", "coverage": "derived request / bond / memory / tick / immune projection from life-body aggregate; memory / bond prefer bounded ledger lineage, immune prefers bounded voice_event_log lineage, tick prefers scheduler_event_log when available", "lifeBodyMeta": _as_dict(payload.get("meta"))})


@router.get("/pathologies", response_model=PathologiesResponse)
def get_pathologies(
    request: Request,
    window_minutes: int = Query(360, ge=1, le=10080, description="聚合时间窗口（分钟）"),
    organ: str = Query("", description="器官系统过滤"),
    severity: str = Query("", description="病灶级别过滤"),
    pathology_id: str = Query("", description="聚焦单个病灶"),
):
    store = require_obs_query_projection_store(request)
    payload = _get_life_body_snapshot(store, window_minutes)
    model = _as_dict(payload.get("model"))
    items = [_as_dict(item) for item in _as_list(model.get("pathologies"))]
    related_events = [_as_dict(item) for item in _as_list(model.get("timelineEventPool") or model.get("timelineEvents"))]
    normalized_organ = _as_text(organ)
    normalized_severity = _as_text(severity).lower()
    normalized_pathology_id = _as_text(pathology_id)
    filtered = []
    for item in items:
        if normalized_organ and _as_text(item.get("organId")) != normalized_organ:
            continue
        if normalized_severity and _as_text(item.get("severity")).lower() != normalized_severity:
            continue
        if normalized_pathology_id and _as_text(item.get("id")) != normalized_pathology_id:
            continue
        filtered.append(item)
    focused_item = next((item for item in filtered if _as_text(item.get("id")) == normalized_pathology_id), None) if normalized_pathology_id else None
    if focused_item:
        focused_related_events = []
        for event in related_events:
            if _as_text(event.get("pathologyId")) == _as_text(focused_item.get("id")) or _as_text(event.get("organId")) == _as_text(focused_item.get("organId")):
                focused_related_events.append(event)
        related_events = focused_related_events
    else:
        related_events = []
    organ_systems = [_as_dict(item) for item in _as_list(model.get("organSystems"))]
    focused_organ = None
    if focused_item:
        focused_organ = next((item for item in organ_systems if _as_text(item.get("id")) == _as_text(focused_item.get("organId"))), None)
    return PathologiesResponse(window_minutes=window_minutes, timestamp=_now_ms(), items=filtered, total=len(items), filtered_total=len(filtered), primaryPathology=_as_dict(model.get("primaryPathology")) or None, organSystems=organ_systems, focusedItem=focused_item, focusedOrgan=focused_organ, relatedEvents=related_events, filters={"organ": normalized_organ or None, "severity": normalized_severity or None, "pathology_id": normalized_pathology_id or None}, meta={"aggregateSource": "obs-sidecar-pathologies", "coverage": "derived pathology projection from runtime alerts, gateway hotspots, and forge runtime quality", "lifeBodyMeta": _as_dict(payload.get("meta"))})
