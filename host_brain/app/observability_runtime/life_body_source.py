"""App-local durable source for the standalone Obs Life Body projection.

The reader joins only already-public query projections owned by the calling
Obs application and bounded, read-only canonical-ledger windows.  It never
imports HTTP routes, Host composition, or process service locators.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from host_brain.app.observability_runtime.process_state_projection import (
    DIGITAL_LIFE_MAX_AGE_MS,
    DIGITAL_LIFE_OVERVIEW_KIND,
    INSIGHTS_SUMMARY_KIND,
    RUNTIME_MAX_AGE_MS,
    RUNTIME_SNAPSHOT_KEY,
    RUNTIME_SNAPSHOT_KIND,
    SUMMARY_MAX_AGE_MS,
    window_key,
)
from host_brain.app.observability_runtime.projection_runtime import (
    TRACE_FEED_INDEX_KEY,
    TRACE_FEED_KIND,
)
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.persistence.life_body_query_repository import (
    LifeBodyQueryRepository,
    LifeBodyQueryRepositoryError,
    decode_life_body_row,
)


LIFE_BODY_SOURCE_SCOPE = "app-local-query-projections-and-canonical-ledger"
LIFE_BODY_ROOT_NOT_READY = "OBS_LIFE_BODY_ROOT_NOT_READY"
LIFE_BODY_ROOT_SOURCE_UNAVAILABLE = "OBS_LIFE_BODY_ROOT_SOURCE_UNAVAILABLE"
LIFE_BODY_ROOT_STALE = "OBS_LIFE_BODY_ROOT_STALE"
LIFE_BODY_ROOT_INVALID = "OBS_LIFE_BODY_ROOT_INVALID"
LIFE_BODY_ROOT_READ_FAILED = "OBS_LIFE_BODY_ROOT_READ_FAILED"
LIFE_BODY_LEDGER_QUERY_UNAVAILABLE = "OBS_LIFE_BODY_LEDGER_QUERY_UNAVAILABLE"
LIFE_BODY_SCHEMA_VERSION = "Natsume.life_body.public.v2"

LIFE_BODY_WINDOW_MINUTES = 360
INSIGHTS_WINDOW_MINUTES = 60
TRACE_FEED_MAX_AGE_MS = 15_000
_MAX_CLOCK_SKEW_MS = 60_000
_HEALTHY = "HEALTHY"
_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:@-]{1,240}$")
_SENSITIVE_KEYS = frozenset(
    {
        "assistant_message",
        "chunk",
        "content",
        "felt_reason",
        "input_data",
        "input_preview",
        "opening_message",
        "output_data",
        "output_preview",
        "output_text",
        "private_body",
        "prompt_text",
        "raw_body",
        "raw_content",
        "response_text",
        "user_message",
    }
)


@dataclass(frozen=True, slots=True)
class LifeBodySourceRead:
    payload: dict[str, Any]
    source_cursor: int


class LifeBodySourceUnavailable(RuntimeError):
    """Typed aggregate failure safe to persist in an operator projection."""

    def __init__(
        self,
        reason_code: str,
        *,
        root_name: str,
        source_scope: str = LIFE_BODY_SOURCE_SCOPE,
    ) -> None:
        self.reason_code = str(reason_code or LIFE_BODY_ROOT_READ_FAILED)
        self.root_name = str(root_name or "unknown")
        self.source_scope = str(source_scope or LIFE_BODY_SOURCE_SCOPE)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class _RootSpec:
    name: str
    kind: str
    key: str
    max_age_ms: int
    expected_window_minutes: int | None = None
    allow_zero_timestamp: bool = False


@dataclass(frozen=True, slots=True)
class _RootRead:
    payload: dict[str, Any]
    source_cursor: int
    freshness_ts_ms: int


_REQUIRED_ROOTS = (
    _RootSpec(
        "runtime-snapshot",
        RUNTIME_SNAPSHOT_KIND,
        RUNTIME_SNAPSHOT_KEY,
        RUNTIME_MAX_AGE_MS,
    ),
    _RootSpec(
        "digital-life-overview",
        DIGITAL_LIFE_OVERVIEW_KIND,
        window_key(LIFE_BODY_WINDOW_MINUTES),
        DIGITAL_LIFE_MAX_AGE_MS,
        expected_window_minutes=LIFE_BODY_WINDOW_MINUTES,
    ),
    _RootSpec(
        "insights-summary",
        INSIGHTS_SUMMARY_KIND,
        window_key(INSIGHTS_WINDOW_MINUTES),
        SUMMARY_MAX_AGE_MS,
        expected_window_minutes=INSIGHTS_WINDOW_MINUTES,
    ),
)
_TRACE_FEED_ROOT = _RootSpec(
    "trace-feed",
    TRACE_FEED_KIND,
    TRACE_FEED_INDEX_KEY,
    TRACE_FEED_MAX_AGE_MS,
    allow_zero_timestamp=True,
)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    return {}


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _state_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().upper()


def _document_value(document: Any, name: str, default: Any = None) -> Any:
    if isinstance(document, Mapping):
        return document.get(name, default)
    return getattr(document, name, default)


def _public_tree(value: Any) -> Any:
    """Defensively remove content-bearing fields from public upstream docs."""

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.strip().lower()
            if normalized in _SENSITIVE_KEYS or "content" in normalized:
                continue
            result[key] = _public_tree(raw_value)
        return result
    if isinstance(value, (list, tuple)):
        return [_public_tree(item) for item in value]
    return value


def _safe_identifier(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _SAFE_TOKEN.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _safe_code(value: Any, *, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip().upper()
    return text if _SAFE_TOKEN.fullmatch(text) else default


def _root_read(
    store: QueryProjectionStore,
    spec: _RootSpec,
    *,
    now_ms: int,
) -> _RootRead:
    try:
        document = store.get(spec.kind, spec.key)
    except Exception as exc:
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_READ_FAILED,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        ) from exc
    if document is None:
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_NOT_READY,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )

    state = _state_name(_document_value(document, "degraded_state"))
    if state == _SOURCE_UNAVAILABLE:
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_SOURCE_UNAVAILABLE,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )
    if state != _HEALTHY:
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_INVALID,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )

    freshness = _non_negative_int(_document_value(document, "freshness_ts_ms"))
    if freshness <= 0 or freshness > now_ms + _MAX_CLOCK_SKEW_MS:
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_INVALID,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )
    if now_ms - freshness > max(0, int(spec.max_age_ms)):
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_STALE,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )

    payload = _mapping(_document_value(document, "payload", {}))
    timestamp = _non_negative_int(payload.get("timestamp"))
    if not payload or (timestamp <= 0 and not spec.allow_zero_timestamp):
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_INVALID,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )
    if (
        spec.expected_window_minutes is not None
        and _non_negative_int(payload.get("window_minutes"))
        != spec.expected_window_minutes
    ):
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_INVALID,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )
    if spec.name == "trace-feed" and not isinstance(payload.get("items"), list):
        raise LifeBodySourceUnavailable(
            LIFE_BODY_ROOT_INVALID,
            root_name=spec.name,
            source_scope="app-local-query-projection",
        )
    return _RootRead(
        payload=_mapping(_public_tree(payload)),
        source_cursor=_non_negative_int(_document_value(document, "source_cursor")),
        freshness_ts_ms=freshness,
    )


def _lineage_rows(
    repository: LifeBodyQueryRepository,
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, list[dict[str, Any]]]:
    try:
        rows = {
            table: [
                decode_life_body_row(table, row)
                for row in repository.read_generic_window(
                    table,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    limit=8,
                )
            ]
            for table in (
                "memory_ops_log",
                "bond_update_log",
                "scheduler_event_log",
            )
        }
        rows["voice_event_log"] = [
            decode_life_body_row("voice_event_log", row)
            for row in repository.read_event_type_window(
                "voice_event_log",
                start_ms=start_ms,
                end_ms=end_ms,
                exact_event_type="SECURITY_IMMUNE",
                like_event_type="%IMMUNE%",
                scan_limit=48,
            )[:8]
        ]
        return rows
    except LifeBodyQueryRepositoryError as exc:
        raise LifeBodySourceUnavailable(
            LIFE_BODY_LEDGER_QUERY_UNAVAILABLE,
            root_name="canonical-ledger",
            source_scope="canonical-ledger-projection",
        ) from exc


_LINEAGE_EVENT_META = {
    "memory_ops_log": ("memory", "memory", "记忆系统", "记忆操作回执"),
    "bond_update_log": ("bond", "limbic", "边缘系统", "关系状态回执"),
    "scheduler_event_log": ("tick", "endocrine", "内分泌系统", "节律任务回执"),
    "voice_event_log": ("immune", "immune", "免疫系统", "免疫事件回执"),
}


def _row_timestamp(row: Mapping[str, Any]) -> int:
    return max(
        _non_negative_int(row.get("ts_ms")),
        _non_negative_int(row.get("created_at_ms")),
        _non_negative_int(row.get("updated_at_ms")),
    )


def _lineage_events(rows_by_table: Mapping[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for table, rows in rows_by_table.items():
        event_type, organ_id, organ_label, title = _LINEAGE_EVENT_META[table]
        for index, row in enumerate(rows):
            timestamp = _row_timestamp(row)
            if timestamp <= 0:
                continue
            request_id = _safe_identifier(row.get("request_id"), prefix="request")
            session_id = _safe_identifier(row.get("session_id"), prefix="session")
            status = _safe_code(
                row.get("status") or row.get("result") or row.get("decision"),
                default="OBSERVED",
            )
            reason_code = _safe_code(row.get("reason_code"), default="")
            stable_id = _safe_identifier(
                row.get("event_id")
                or row.get("operation_id")
                or row.get("task_id")
                or row.get("update_id")
                or f"{table}:{timestamp}:{index}",
                prefix=event_type,
            )
            severity = "warning" if status in {"FAIL", "FAILED", "REJECTED", "BLOCKED"} else "info"
            event = {
                "id": stable_id,
                "type": event_type,
                "typeLabel": title,
                "organId": organ_id,
                "organLabel": organ_label,
                "severity": severity,
                "timestamp": timestamp,
                "title": title,
                "detail": f"已观测到持久化回执，状态 {status}。",
                "to": {
                    "memory": "/memory",
                    "bond": "/bond",
                    "tick": "/automation",
                    "immune": "/alerts",
                }[event_type],
                "linkLabel": "查看公开观测",
                "status": status,
            }
            if request_id:
                event["requestId"] = request_id
            if session_id:
                event["sessionId"] = session_id
            if reason_code:
                event["reasonCode"] = reason_code
            events.append(event)
    return events


def _trace_events(trace_feed: Mapping[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for index, raw in enumerate(list(trace_feed.get("items") or [])[:12]):
        item = _mapping(raw)
        session = _mapping(item.get("session"))
        trace = _mapping(item.get("trace"))
        request_id = _safe_identifier(
            session.get("request_id") or trace.get("request_id"),
            prefix="request",
        )
        if not request_id:
            continue
        session_id = _safe_identifier(
            session.get("session_id") or trace.get("session_id"),
            prefix="session",
        )
        timestamp = max(
            _non_negative_int(session.get("ts_ms")),
            _non_negative_int(trace.get("ts_ms")),
            _non_negative_int(trace.get("timestamp")),
        )
        if timestamp <= 0:
            continue
        status = _safe_code(
            trace.get("status") or session.get("status"),
            default="OBSERVED",
        )
        event = {
            "id": f"trace:{request_id}",
            "type": "request",
            "typeLabel": "请求",
            "organId": "nervous",
            "organLabel": "神经系统",
            "severity": "warning" if status in {"FAIL", "FAILED", "DEGRADED"} else "info",
            "timestamp": timestamp,
            "title": "请求链路回执",
            "detail": f"已观测到公开 Trace 回执，状态 {status}。",
            "to": f"/trace/{request_id}",
            "linkLabel": "查看 Trace",
            "requestId": request_id,
            "status": status,
            "nodeCount": _non_negative_int(trace.get("node_count")),
        }
        if session_id:
            event["sessionId"] = session_id
        events.append(event)
    return events


def _non_live_slots(runtime: Mapping[str, Any]) -> list[list[str]]:
    slots = _mapping(runtime.get("slot_status"))
    return [
        [str(name), str(status)]
        for name, status in sorted(slots.items())
        if str(status or "").strip().lower() not in {"live", "not_applicable"}
    ]


def _runtime_alerts(
    runtime: Mapping[str, Any],
    *,
    observed_ts_ms: int,
) -> list[dict[str, Any]]:
    reasons = [
        _safe_code(reason, default="RUNTIME_SOURCE_DEGRADED")
        for reason in list(runtime.get("degraded_reasons") or [])
    ]
    if runtime.get("degraded") and not reasons:
        reasons = ["RUNTIME_SOURCE_DEGRADED"]
    return [
        {
            "id": f"runtime:{reason.lower()}:{index}",
            "reason_code": reason,
            "severity": "warning",
            "organId": "nervous",
            "title": "运行投影存在降级",
            "detail": "一个公开运行槽位暂不可用。",
            "source": "runtime",
            "to": "/runtime-mode",
            "ts_ms": observed_ts_ms,
        }
        for index, reason in enumerate(reasons)
    ]


def _public_risk_alerts(
    overview: Mapping[str, Any],
    insights: Mapping[str, Any],
    *,
    observed_ts_ms: int,
) -> list[dict[str, Any]]:
    """Translate public aggregate risks into stable server-side semantics."""

    alerts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(
        reason_code: str,
        *,
        severity: str,
        organ_id: str,
        title: str,
        detail: str,
        source: str,
        to: str,
    ) -> None:
        reason = _safe_code(reason_code, default="PUBLIC_PROJECTION_RISK")
        if reason in seen:
            return
        seen.add(reason)
        alerts.append(
            {
                "id": f"public-risk:{reason.lower()}",
                "reason_code": reason,
                "severity": "critical" if severity == "critical" else "warning",
                "organId": organ_id,
                "title": title,
                "detail": detail,
                "source": source,
                "to": to,
                "ts_ms": observed_ts_ms,
            }
        )

    prompt_stats = _mapping(insights.get("promptStats"))
    injection_count = _non_negative_int(
        prompt_stats.get("injection_detected_count")
    )
    if injection_count > 0:
        push(
            "PROMPT_INJECTION_DETECTED",
            severity="critical",
            organ_id="immune",
            title="检测到提示注入",
            detail=f"当前公开时间窗内检测到 {injection_count} 次提示注入风险。",
            source="prompt-security",
            to="/operations/prompt",
        )
    high_risk_count = _non_negative_int(prompt_stats.get("high_risk_count"))
    if high_risk_count > 0:
        push(
            "PROMPT_HIGH_RISK_BUILD",
            severity="warning",
            organ_id="immune",
            title="检测到高风险 Prompt 构建",
            detail=f"当前公开时间窗内存在 {high_risk_count} 次高风险 Prompt 构建。",
            source="prompt-security",
            to="/operations/prompt",
        )

    services = _mapping(overview.get("servicesData"))
    service_down = _non_negative_int(services.get("down"))
    if service_down > 0:
        push(
            "SERVICE_DOWN",
            severity="critical",
            organ_id="nervous",
            title="底座服务离线",
            detail=f"公开服务投影报告 {service_down} 个服务离线。",
            source="service-health",
            to="/observability/overview",
        )
    service_degraded = _non_negative_int(services.get("degraded"))
    if service_degraded > 0:
        push(
            "SERVICE_DEGRADED",
            severity="warning",
            organ_id="nervous",
            title="底座服务降级",
            detail=f"公开服务投影报告 {service_degraded} 个服务降级。",
            source="service-health",
            to="/observability/overview",
        )

    gateway = _mapping(overview.get("humanoidLoopData"))
    gateway_alerts = _mapping(overview.get("humanoidLoopAlertsData"))
    for raw_alert in list(gateway_alerts.get("alerts") or [])[:4]:
        item = _mapping(raw_alert)
        reason = _safe_code(
            item.get("reason_code") or item.get("code"),
            default="GATEWAY_RUNTIME_DEGRADED",
        )
        push(
            reason,
            severity=(
                "critical"
                if str(item.get("severity") or "").lower() == "critical"
                else "warning"
            ),
            organ_id="motor",
            title="Gateway 公开告警",
            detail=f"Gateway 公开投影报告风险 {reason}。",
            source="gateway",
            to="/execution/gateway",
        )
    gateway_severity = str(gateway_alerts.get("severity") or "").lower()
    gateway_status = str(gateway_alerts.get("status") or "").lower()
    if not list(gateway_alerts.get("alerts") or []) and (
        gateway_severity in {"critical", "warning"}
        or gateway_status in {"critical", "warning", "degraded", "down"}
    ):
        critical_gateway = gateway_severity == "critical" or gateway_status in {
            "critical",
            "down",
        }
        push(
            "GATEWAY_RUNTIME_CRITICAL"
            if critical_gateway
            else "GATEWAY_RUNTIME_DEGRADED",
            severity="critical" if critical_gateway else "warning",
            organ_id="motor",
            title="Gateway 执行闭环异常",
            detail="Gateway 公开告警投影报告执行闭环存在风险。",
            source="gateway",
            to="/execution/gateway",
        )
    gateway_samples = _non_negative_int(gateway.get("total_gateway_requests"))
    try:
        closed_loop_rate = float(gateway.get("closed_loop_rate") or 0)
    except (TypeError, ValueError, OverflowError):
        closed_loop_rate = 0.0
    if gateway_samples > 0 and closed_loop_rate < 70:
        push(
            "CLOSED_LOOP_LOW",
            severity="warning",
            organ_id="motor",
            title="Gateway 闭环率偏低",
            detail=f"公开窗口闭环率为 {closed_loop_rate:.1f}%。",
            source="gateway",
            to="/execution/gateway",
        )

    capability = _mapping(overview.get("capabilityGapData"))
    forge_summary = _mapping(
        _mapping(capability.get("forge_runtime_quality")).get("summary")
    )
    forge_critical = _non_negative_int(forge_summary.get("critical_proposals"))
    forge_unhealthy = _non_negative_int(forge_summary.get("unhealthy_proposals"))
    if forge_critical > 0:
        push(
            "FORGE_RUNTIME_CRITICAL_PROPOSALS",
            severity="critical",
            organ_id="self",
            title="Forge 关键提案堆积",
            detail=f"公开能力投影报告 {forge_critical} 条关键提案未收束。",
            source="capability-forge",
            to="/observability/insights/global?focus=forge-risk",
        )
    elif forge_unhealthy > 0:
        push(
            "FORGE_RUNTIME_UNHEALTHY_PROPOSALS",
            severity="warning",
            organ_id="self",
            title="Forge 提案健康度下降",
            detail=f"公开能力投影报告 {forge_unhealthy} 条提案处于不健康区。",
            source="capability-forge",
            to="/observability/insights/global?focus=forge-risk",
        )
    capability_failures = _non_negative_int(capability.get("failed_total"))
    if capability_failures > 0:
        push(
            "CAPABILITY_GAP_FAILURES",
            severity="warning",
            organ_id="self",
            title="能力缺口处理失败",
            detail=f"公开能力投影报告 {capability_failures} 条失败记录。",
            source="capability-gap",
            to="/observability/insights/global",
        )

    severity_order = {"critical": 0, "warning": 1}
    alerts.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity")), 99),
            str(item.get("reason_code")),
        )
    )
    return alerts


_ORGAN_LABELS = {
    "nervous": "神经系统",
    "limbic": "边缘系统",
    "memory": "记忆系统",
    "endocrine": "内分泌系统",
    "immune": "免疫系统",
    "motor": "运动系统",
    "self": "自我系统",
}


def _organ_systems(
    runtime: Mapping[str, Any],
    overview: Mapping[str, Any],
    alerts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    memory = _mapping(overview.get("memoryData"))
    services = _mapping(overview.get("servicesData"))
    gateway = _mapping(overview.get("humanoidLoopData"))
    definitions = (
        ("nervous", "神经系统", "/trace", True),
        ("limbic", "边缘系统", "/emotion", True),
        ("memory", "记忆系统", "/memory", bool(memory)),
        ("endocrine", "内分泌系统", "/automation", bool(_mapping(runtime.get("background_runtime")))),
        ("immune", "免疫系统", "/alerts", True),
        ("motor", "运动系统", "/gateway", bool(gateway)),
        ("self", "自我系统", "/self-model", True),
    )
    alerts_by_organ: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts:
        organ_id = str(alert.get("organId") or "").strip()
        if organ_id:
            alerts_by_organ.setdefault(organ_id, []).append(alert)
    result = []
    for organ_id, name, path, available in definitions:
        organ_alerts = alerts_by_organ.get(organ_id, [])
        critical = any(
            str(item.get("severity") or "").lower() == "critical"
            for item in organ_alerts
        )
        if not available:
            status = "pending"
        elif critical:
            status = "critical"
        elif organ_alerts:
            status = "warning"
        else:
            status = "stable"
        summary = (
            f"公开投影报告 {len(organ_alerts)} 条待处理风险。"
            if organ_alerts
            else "公开持久投影已接入。"
            if available
            else "等待公开持久投影。"
        )
        result.append(
            {
                "id": organ_id,
                "name": name,
                "status": status,
                "summary": summary,
                "drilldownPath": path,
                "drilldownLabel": "查看公开观测",
                "downstreamImpacts": ["该状态仅由公开持久投影计算。"],
            }
        )
    order = {"critical": 0, "warning": 1, "stable": 2, "pending": 3}
    result.sort(key=lambda item: (order[item["status"]], item["id"]))
    return result


def _pathologies(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": f"pathology:{item['id']}",
            "organId": item["organId"],
            "organLabel": _ORGAN_LABELS.get(
                str(item.get("organId") or ""),
                "未分类系统",
            ),
            "severity": item["severity"],
            "title": item["title"],
            "symptom": item["detail"],
            "durationLabel": "当前公开投影",
            "impactLabel": "运行投影完整性下降",
            "detailPath": str(item.get("to") or "/alerts"),
            "recommendedPath": str(item.get("to") or "/alerts"),
            "recommendedLabel": "查看相关公开观测",
            "representativeRefs": [],
        }
        for item in alerts
    ]


def _diagnostic_guidance(
    alerts: list[dict[str, Any]],
    pathologies: list[dict[str, Any]],
    primary_organ: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build public, actionable guidance without importing route helpers."""

    actions: list[dict[str, Any]] = []
    if pathologies:
        pathology = pathologies[0]
        actions.append(
            {
                "id": "primary-pathology",
                "tone": str(pathology.get("severity") or "warning"),
                "eyebrow": "主病灶",
                "title": f"{pathology.get('organLabel') or '未分类系统'} · {pathology.get('title') or '公开风险'}",
                "detail": str(pathology.get("symptom") or ""),
                "impact": str(pathology.get("impactLabel") or "运行投影完整性下降"),
                "to": str(pathology.get("detailPath") or "/alerts"),
                "label": "查看病灶来源",
            }
        )
    if primary_organ is not None:
        actions.append(
            {
                "id": "primary-organ",
                "tone": str(primary_organ.get("status") or "pending"),
                "eyebrow": "首要器官",
                "title": str(primary_organ.get("name") or "公开生命体征"),
                "detail": str(primary_organ.get("summary") or ""),
                "impact": "沿该器官的公开观测继续确认影响范围。",
                "to": str(primary_organ.get("drilldownPath") or "/digital-life"),
                "label": str(primary_organ.get("drilldownLabel") or "查看公开观测"),
            }
        )
    if not actions:
        return {
            "summary": "公开生命体投影当前没有待处理风险，可继续观察时间轴。",
            "actions": [],
        }
    if alerts and str(alerts[0].get("severity") or "").lower() == "critical":
        summary = "存在高风险公开信号；先处理主病灶，再确认相关器官的影响范围。"
    else:
        summary = "存在需要跟进的公开信号；先查看主病灶及其关联器官。"
    return {"summary": summary, "actions": actions[:2]}


@dataclass(slots=True)
class LifeBodySourceReader:
    """Join one Obs application's public durable roots into Life Body."""

    store: QueryProjectionStore
    query_repository: LifeBodyQueryRepository
    window_minutes: int = LIFE_BODY_WINDOW_MINUTES
    clock_ms: Callable[[], int] = field(
        default=lambda: int(time.time() * 1000),
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.store is None or not callable(getattr(self.store, "get", None)):
            raise TypeError("store must provide get")
        if not isinstance(self.query_repository, LifeBodyQueryRepository):
            raise TypeError("query_repository must be LifeBodyQueryRepository")
        if int(self.window_minutes) != LIFE_BODY_WINDOW_MINUTES:
            raise ValueError("Life Body durable source supports window 360 only")
        if not callable(self.clock_ms):
            raise TypeError("clock_ms must be callable")

    def read(self) -> LifeBodySourceRead:
        now = _non_negative_int(self.clock_ms())
        roots = {
            spec.name: _root_read(self.store, spec, now_ms=now)
            for spec in _REQUIRED_ROOTS
        }
        trace_feed_reason: str | None = None
        try:
            roots[_TRACE_FEED_ROOT.name] = _root_read(
                self.store,
                _TRACE_FEED_ROOT,
                now_ms=now,
            )
        except LifeBodySourceUnavailable as exc:
            # Trace is historical diagnostic enrichment, not the authority for
            # whether the Digital Life runtime exists.  A slow trace rebuild
            # must not blank an otherwise healthy self/runtime snapshot.
            trace_feed_reason = exc.reason_code
            roots[_TRACE_FEED_ROOT.name] = _RootRead(
                payload={"items": [], "count": 0, "timestamp": 0},
                source_cursor=0,
                freshness_ts_ms=0,
            )
        runtime = roots["runtime-snapshot"].payload
        overview = roots["digital-life-overview"].payload
        insights = roots["insights-summary"].payload
        trace_feed = roots["trace-feed"].payload
        start_ms = max(0, now - self.window_minutes * 60_000)
        lineage = _lineage_rows(
            self.query_repository,
            start_ms=start_ms,
            end_ms=now,
        )
        alert_observed_ts_ms = max(
            _non_negative_int(runtime.get("timestamp")),
            _non_negative_int(overview.get("timestamp")),
            _non_negative_int(insights.get("timestamp")),
        )
        alerts = _runtime_alerts(
            runtime,
            observed_ts_ms=alert_observed_ts_ms,
        )
        seen_reasons = {
            str(item.get("reason_code") or "")
            for item in alerts
        }
        for alert in _public_risk_alerts(
            overview,
            insights,
            observed_ts_ms=alert_observed_ts_ms,
        ):
            reason = str(alert.get("reason_code") or "")
            if reason and reason in seen_reasons:
                continue
            seen_reasons.add(reason)
            alerts.append(alert)
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        alerts.sort(
            key=lambda item: (
                severity_order.get(str(item.get("severity") or "").lower(), 99),
                str(item.get("reason_code") or ""),
            )
        )
        pathologies = _pathologies(alerts)
        organs = _organ_systems(runtime, overview, alerts)
        timeline_pool = _trace_events(trace_feed) + _lineage_events(lineage)
        timeline_pool.sort(
            key=lambda item: (-_non_negative_int(item.get("timestamp")), str(item.get("id")))
        )
        timeline_events = timeline_pool[:6]
        non_live = _non_live_slots(runtime)
        runtime_updated = _non_negative_int(runtime.get("timestamp"))
        overview_updated = _non_negative_int(overview.get("timestamp"))
        prompt_stats = _mapping(insights.get("promptStats")) or None
        primary_pathology = pathologies[0] if pathologies else None
        primary_organ = next(
            (item for item in organs if item["status"] in {"critical", "warning"}),
            next((item for item in organs if item["status"] != "pending"), None),
        )
        source_timestamp = max(
            _non_negative_int(runtime.get("timestamp")),
            _non_negative_int(overview.get("timestamp")),
            _non_negative_int(insights.get("timestamp")),
            _non_negative_int(trace_feed.get("timestamp")),
            *(
                _row_timestamp(row)
                for rows in lineage.values()
                for row in rows
            ),
        )
        if source_timestamp <= 0:
            source_timestamp = now
        diagnostic_guidance = _diagnostic_guidance(
            alerts,
            pathologies,
            primary_organ,
        )
        life_tone = (
            "critical"
            if any(str(item.get("severity") or "").lower() == "critical" for item in alerts)
            else "warning"
            if alerts
            else "stable"
        )
        payload = {
            "schema_version": LIFE_BODY_SCHEMA_VERSION,
            "window_minutes": self.window_minutes,
            "timestamp": source_timestamp,
            "runtimeSnapshot": runtime,
            "promptStats": prompt_stats,
            "vtsData": _mapping(overview.get("vtsData")) or None,
            "memoryData": _mapping(overview.get("memoryData")) or None,
            "servicesData": _mapping(overview.get("servicesData")) or None,
            "subsystemsData": _mapping(overview.get("subsystemsData")) or None,
            "capabilityGapData": _mapping(overview.get("capabilityGapData")) or None,
            "capabilityGapProjection": _mapping(overview.get("capabilityGapProjection")),
            "humanoidLoopData": _mapping(overview.get("humanoidLoopData")) or None,
            "humanoidLoopTrendData": _mapping(overview.get("humanoidLoopTrendData")) or None,
            "humanoidLoopAlertsData": _mapping(overview.get("humanoidLoopAlertsData")) or None,
            "humanoidLoopRemediationData": _mapping(overview.get("humanoidLoopRemediationData")) or None,
            "humanoidLoopDiagnosticsData": _mapping(overview.get("humanoidLoopDiagnosticsData")) or None,
            "humanoidLoopHotspotsData": _mapping(overview.get("humanoidLoopHotspotsData")) or None,
            "humanoidLoopHotspotTrendData": _mapping(overview.get("humanoidLoopHotspotTrendData")) or None,
            "humanoidLoopSourceCorrelationData": _mapping(overview.get("humanoidLoopSourceCorrelationData")) or None,
            "runtimeAlerts": alerts,
            "nonLiveSlots": non_live,
            "growthSignals": {
                "hasAnyGrowth": bool(
                    runtime.get("self_model_snapshot")
                    or runtime.get("self_reflection_pulse")
                ),
                "source": "runtime-snapshot",
            },
            "derived": {
                "hasRuntimeSnapshot": True,
                "hasMemoryData": bool(_mapping(overview.get("memoryData"))),
                "hasMemorySamples": bool(
                    list(_mapping(overview.get("memoryData")).get("subsystems") or [])
                ),
                "hasCapabilityGapData": bool(_mapping(overview.get("capabilityGapData"))),
                "overviewUpdatedAt": overview_updated,
                "runtimeUpdatedAt": runtime_updated,
                "activeTraceFeedCount": len(list(trace_feed.get("items") or [])),
                "memoryLineageCount": len(lineage["memory_ops_log"]),
                "bondLineageCount": len(lineage["bond_update_log"]),
                "tickLineageCount": len(lineage["scheduler_event_log"]),
                "immuneLineageCount": len(lineage["voice_event_log"]),
                "activeSubsystemCount": sum(
                    1
                    for item in list(
                        _mapping(overview.get("subsystemsData")).get("subsystems")
                        or []
                    )
                    if isinstance(item, Mapping)
                    and _non_negative_int(item.get("total")) > 0
                ),
            },
            "model": {
                "organSystems": organs,
                "lifeState": {
                    "label": (
                        "失衡"
                        if life_tone == "critical"
                        else "紧张"
                        if life_tone == "warning"
                        else "稳定"
                    ),
                    "tone": life_tone,
                    "summary": (
                        "存在高风险公开生命体征。"
                        if life_tone == "critical"
                        else "存在需要跟进的公开生命体征。"
                        if life_tone == "warning"
                        else "公开持久投影状态稳定。"
                    ),
                },
                "pathologies": pathologies,
                "primaryPathology": primary_pathology,
                "timelineEvents": timeline_events,
                "timelineEventPool": timeline_pool,
                "primaryOrgan": primary_organ,
                "diagnosticGuidance": diagnostic_guidance,
            },
            "meta": {
                "aggregateSource": "obs-app-life-body-durable-source",
                "projectionMode": "app-local-materialized-roots-plus-bounded-ledger-lineage",
                "projectionWindowMinutes": self.window_minutes,
                "rootEvidenceTimestamp": {
                    name: _non_negative_int(root.payload.get("timestamp"))
                    for name, root in roots.items()
                },
                "optionalSourceStatus": {
                    "trace-feed": {
                        "available": trace_feed_reason is None,
                        "reason_code": trace_feed_reason,
                    }
                },
                "lineageLedger": {
                    "memoryRows": len(lineage["memory_ops_log"]),
                    "bondRows": len(lineage["bond_update_log"]),
                    "tickRows": len(lineage["scheduler_event_log"]),
                    "immuneRows": len(lineage["voice_event_log"]),
                },
            },
        }
        payload = _mapping(_public_tree(payload))
        cursor_candidates = [
            *(root.source_cursor for root in roots.values()),
            *(
                _row_timestamp(row)
                for rows in lineage.values()
                for row in rows
            ),
        ]
        source_cursor = max(cursor_candidates, default=now)
        return LifeBodySourceRead(payload=payload, source_cursor=source_cursor)

    def __call__(self) -> tuple[dict[str, Any], int]:
        result = self.read()
        return result.payload, result.source_cursor


__all__ = [
    "LIFE_BODY_LEDGER_QUERY_UNAVAILABLE",
    "LIFE_BODY_ROOT_INVALID",
    "LIFE_BODY_ROOT_NOT_READY",
    "LIFE_BODY_ROOT_READ_FAILED",
    "LIFE_BODY_ROOT_SOURCE_UNAVAILABLE",
    "LIFE_BODY_ROOT_STALE",
    "LIFE_BODY_SCHEMA_VERSION",
    "LIFE_BODY_SOURCE_SCOPE",
    "LIFE_BODY_WINDOW_MINUTES",
    "LifeBodySourceRead",
    "LifeBodySourceReader",
    "LifeBodySourceUnavailable",
]
