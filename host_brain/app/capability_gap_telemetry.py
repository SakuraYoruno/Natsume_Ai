"""In-memory telemetry for capability gap detection and runtime synthesis."""

from __future__ import annotations

import threading
from collections import Counter
from typing import Any

from host_brain.core.ids import now_ms as _now_ms


def _safe_ratio(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return float(num) / float(den)


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized = [
        str(tag or "").strip().lower()
        for tag in list(tags or [])
        if str(tag or "").strip()
    ]
    return list(dict.fromkeys(normalized))


def _build_candidate_key(
    *,
    reason_code: str,
    suggested_family: str,
    missing_capability_tags: list[str],
) -> str:
    key_parts = [
        str(reason_code or "UNKNOWN").strip().upper() or "UNKNOWN",
        str(suggested_family or "unknown").strip().lower() or "unknown",
        ",".join(sorted(missing_capability_tags)),
    ]
    return "|".join(key_parts)


class CapabilityGapTelemetry:
    def __init__(self, *, max_recent_events: int = 300) -> None:
        self._lock = threading.RLock()
        self._max_recent_events = max(50, int(max_recent_events))
        self._detected_total = 0
        self._executed_total = 0
        self._success_total = 0
        self._failed_total = 0
        self._by_reason: Counter[str] = Counter()
        self._by_family: Counter[str] = Counter()
        self._by_executor: Counter[str] = Counter()
        self._recent_events: list[dict[str, Any]] = []
        self._runtime_capabilities: dict[str, dict[str, Any]] = {}
        self._runtime_to_candidate: dict[str, str] = {}
        self._candidate_stats: dict[str, dict[str, Any]] = {}

    def _append_event(self, event: dict[str, Any]) -> None:
        self._recent_events.append(dict(event))
        overflow = len(self._recent_events) - self._max_recent_events
        if overflow > 0:
            del self._recent_events[:overflow]

    def reset_for_tests(self) -> None:
        with self._lock:
            self._detected_total = 0
            self._executed_total = 0
            self._success_total = 0
            self._failed_total = 0
            self._by_reason.clear()
            self._by_family.clear()
            self._by_executor.clear()
            self._recent_events.clear()
            self._runtime_capabilities.clear()
            self._runtime_to_candidate.clear()
            self._candidate_stats.clear()

    def _get_or_create_candidate(
        self,
        *,
        candidate_key: str,
        reason_code: str,
        suggested_family: str,
        missing_capability_tags: list[str],
        ts_ms: int,
    ) -> dict[str, Any]:
        existing = self._candidate_stats.get(candidate_key)
        if existing is not None:
            return existing
        created = {
            "candidate_key": candidate_key,
            "reason_code": reason_code,
            "suggested_family": suggested_family,
            "missing_capability_tags": list(missing_capability_tags),
            "detected_count": 0,
            "executed_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "first_detected_ts_ms": ts_ms,
            "last_detected_ts_ms": ts_ms,
            "last_execution_ts_ms": None,
            "executor_routes": Counter(),
            "executor_targets": Counter(),
            "unique_sessions": set(),
            "unique_requests": set(),
        }
        self._candidate_stats[candidate_key] = created
        return created

    def record_detected(
        self,
        *,
        request_id: str,
        session_id: str,
        runtime_capability_id: str,
        reason_code: str,
        suggested_family: str | None,
        missing_capability_tags: list[str] | tuple[str, ...] | None = None,
        confidence: float | None = None,
        executor_route: str | None = None,
        executor_target: str | None = None,
    ) -> None:
        ts_ms = _now_ms()
        request = str(request_id or "").strip()
        session = str(session_id or "").strip()
        normalized_reason = str(reason_code or "UNKNOWN").strip().upper() or "UNKNOWN"
        family = str(suggested_family or "unknown").strip().lower() or "unknown"
        cap_id = str(runtime_capability_id or "").strip()
        target = str(executor_target or "").strip()
        route = str(executor_route or "").strip().lower()
        tags = _normalize_tags(missing_capability_tags)
        candidate_key = _build_candidate_key(
            reason_code=normalized_reason,
            suggested_family=family,
            missing_capability_tags=tags,
        )

        with self._lock:
            self._detected_total += 1
            self._by_reason[normalized_reason] += 1
            self._by_family[family] += 1
            if target:
                self._by_executor[target] += 1

            candidate = self._get_or_create_candidate(
                candidate_key=candidate_key,
                reason_code=normalized_reason,
                suggested_family=family,
                missing_capability_tags=tags,
                ts_ms=ts_ms,
            )
            candidate["detected_count"] = int(candidate.get("detected_count") or 0) + 1
            candidate["last_detected_ts_ms"] = ts_ms
            if route:
                candidate["executor_routes"][route] += 1
            if target:
                candidate["executor_targets"][target] += 1
            if session:
                candidate["unique_sessions"].add(session)
            if request:
                candidate["unique_requests"].add(request)

            if cap_id:
                self._runtime_to_candidate[cap_id] = candidate_key
                self._runtime_capabilities[cap_id] = {
                    "runtime_capability_id": cap_id,
                    "request_id": request,
                    "session_id": session,
                    "candidate_key": candidate_key,
                    "reason_code": normalized_reason,
                    "suggested_family": family,
                    "missing_capability_tags": list(tags),
                    "confidence": float(confidence or 0.0),
                    "executor_route": route,
                    "executor_target": target,
                    "detected_ts_ms": ts_ms,
                    "last_status": "DETECTED",
                    "last_update_ts_ms": ts_ms,
                }
            self._append_event(
                {
                    "ts_ms": ts_ms,
                    "event_type": "GAP_DETECTED",
                    "request_id": request,
                    "session_id": session,
                    "runtime_capability_id": cap_id,
                    "candidate_key": candidate_key,
                    "reason_code": normalized_reason,
                    "suggested_family": family,
                    "missing_capability_tags": list(tags),
                    "confidence": float(confidence or 0.0),
                    "executor_route": route or None,
                    "executor_target": target or None,
                }
            )

    def record_execution(
        self,
        *,
        runtime_capability_id: str,
        request_id: str,
        success: bool,
        executor_route: str,
        executor_target: str,
        latency_ms: int | float | None = None,
        error_code: str | None = None,
    ) -> None:
        ts_ms = _now_ms()
        cap_id = str(runtime_capability_id or "").strip()
        request = str(request_id or "").strip()
        target = str(executor_target or "").strip()
        route = str(executor_route or "").strip().lower()
        normalized_error = str(error_code or "").strip().upper() or None

        with self._lock:
            self._executed_total += 1
            if success:
                self._success_total += 1
                status = "SUCCESS"
            else:
                self._failed_total += 1
                status = "FAILED"
            if target:
                self._by_executor[target] += 1

            if cap_id:
                existing = dict(self._runtime_capabilities.get(cap_id) or {})
                existing.update(
                    {
                        "runtime_capability_id": cap_id,
                        "request_id": request or existing.get("request_id"),
                        "executor_route": route or existing.get("executor_route"),
                        "executor_target": target or existing.get("executor_target"),
                        "last_status": status,
                        "last_error_code": normalized_error,
                        "last_latency_ms": int(latency_ms or 0),
                        "last_update_ts_ms": ts_ms,
                    }
                )
                self._runtime_capabilities[cap_id] = existing

                candidate_key = self._runtime_to_candidate.get(cap_id)
                if candidate_key:
                    candidate = self._candidate_stats.get(candidate_key)
                    if candidate is not None:
                        candidate["executed_count"] = int(candidate.get("executed_count") or 0) + 1
                        if success:
                            candidate["success_count"] = int(candidate.get("success_count") or 0) + 1
                        else:
                            candidate["failed_count"] = int(candidate.get("failed_count") or 0) + 1
                        candidate["last_execution_ts_ms"] = ts_ms
                        if route:
                            candidate["executor_routes"][route] += 1
                        if target:
                            candidate["executor_targets"][target] += 1
                        if request:
                            candidate["unique_requests"].add(request)

            self._append_event(
                {
                    "ts_ms": ts_ms,
                    "event_type": "RUNTIME_CAPABILITY_EXECUTION",
                    "runtime_capability_id": cap_id,
                    "request_id": request,
                    "status": status,
                    "success": bool(success),
                    "executor_route": route or None,
                    "executor_target": target or None,
                    "latency_ms": int(latency_ms or 0),
                    "error_code": normalized_error,
                }
            )

    def _build_hotspots(self, *, limit: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for candidate in self._candidate_stats.values():
            detected_count = int(candidate.get("detected_count") or 0)
            executed_count = int(candidate.get("executed_count") or 0)
            failed_count = int(candidate.get("failed_count") or 0)
            success_count = int(candidate.get("success_count") or 0)
            unique_sessions = len(candidate.get("unique_sessions") or set())
            failure_rate = _safe_ratio(failed_count, executed_count)
            items.append(
                {
                    "candidate_key": str(candidate.get("candidate_key") or ""),
                    "reason_code": str(candidate.get("reason_code") or "UNKNOWN"),
                    "suggested_family": str(candidate.get("suggested_family") or "unknown"),
                    "missing_capability_tags": list(candidate.get("missing_capability_tags") or []),
                    "detected_count": detected_count,
                    "executed_count": executed_count,
                    "failed_count": failed_count,
                    "success_count": success_count,
                    "failure_rate": round(failure_rate, 4),
                    "impact_sessions": unique_sessions,
                    "last_detected_ts_ms": candidate.get("last_detected_ts_ms"),
                }
            )
        items.sort(
            key=lambda item: (
                int(item.get("detected_count") or 0),
                int(item.get("impact_sessions") or 0),
                int(item.get("failed_count") or 0),
                int(item.get("executed_count") or 0),
            ),
            reverse=True,
        )
        return items[: max(1, int(limit))]

    def get_candidate_detail(self, candidate_key: str) -> dict[str, Any] | None:
        key = str(candidate_key or "").strip()
        if not key:
            return None
        with self._lock:
            candidate = dict(self._candidate_stats.get(key) or {})
            if not candidate:
                return None
            detected_count = int(candidate.get("detected_count") or 0)
            executed_count = int(candidate.get("executed_count") or 0)
            failed_count = int(candidate.get("failed_count") or 0)
            success_count = int(candidate.get("success_count") or 0)
            unique_sessions = len(candidate.get("unique_sessions") or set())
            unique_requests = len(candidate.get("unique_requests") or set())
            failure_rate = _safe_ratio(failed_count, executed_count)
            success_rate = _safe_ratio(success_count, executed_count)
            return {
                "candidate_key": str(candidate.get("candidate_key") or ""),
                "reason_code": str(candidate.get("reason_code") or "UNKNOWN"),
                "suggested_family": str(candidate.get("suggested_family") or "unknown"),
                "missing_capability_tags": list(candidate.get("missing_capability_tags") or []),
                "detected_count": detected_count,
                "executed_count": executed_count,
                "success_count": success_count,
                "failed_count": failed_count,
                "failure_rate": round(failure_rate, 4),
                "success_rate": round(success_rate, 4),
                "impact_sessions": unique_sessions,
                "impact_requests": unique_requests,
                "first_detected_ts_ms": candidate.get("first_detected_ts_ms"),
                "last_detected_ts_ms": candidate.get("last_detected_ts_ms"),
                "last_execution_ts_ms": candidate.get("last_execution_ts_ms"),
                "top_executor_targets": [
                    {"key": executor_key, "count": int(executor_count)}
                    for executor_key, executor_count in Counter(candidate.get("executor_targets") or {}).most_common(3)
                ],
            }

    def _build_forge_candidates(
        self,
        *,
        limit: int,
        min_detected_count: int,
        min_unique_sessions: int,
        min_failure_rate: float,
        min_failed_count: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        thresholds = {
            "min_detected_count": int(max(1, min_detected_count)),
            "min_unique_sessions": int(max(1, min_unique_sessions)),
            "min_failure_rate": round(max(0.0, min(1.0, float(min_failure_rate))), 4),
            "min_failed_count": int(max(0, min_failed_count)),
        }
        items: list[dict[str, Any]] = []
        for candidate in self._candidate_stats.values():
            detected_count = int(candidate.get("detected_count") or 0)
            executed_count = int(candidate.get("executed_count") or 0)
            failed_count = int(candidate.get("failed_count") or 0)
            success_count = int(candidate.get("success_count") or 0)
            unique_sessions = len(candidate.get("unique_sessions") or set())
            unique_requests = len(candidate.get("unique_requests") or set())
            failure_rate = _safe_ratio(failed_count, executed_count)
            success_rate = _safe_ratio(success_count, executed_count)
            frequency_score = min(1.0, float(detected_count) / 10.0)
            failure_score = max(failure_rate, min(1.0, float(failed_count) / 6.0))
            coverage_score = min(1.0, float(unique_sessions) / 6.0)
            execution_score = min(1.0, float(executed_count) / 8.0)
            priority_score = round(
                (0.4 * frequency_score)
                + (0.3 * failure_score)
                + (0.2 * coverage_score)
                + (0.1 * execution_score),
                4,
            )
            meets_threshold = (
                detected_count >= thresholds["min_detected_count"]
                and unique_sessions >= thresholds["min_unique_sessions"]
                and (
                    failed_count >= thresholds["min_failed_count"]
                    or failure_rate >= thresholds["min_failure_rate"]
                    or (executed_count == 0 and detected_count >= max(4, thresholds["min_detected_count"] * 2))
                )
            )
            candidate_stage = "READY_FOR_FORGE" if meets_threshold else "OBSERVE"
            if candidate_stage != "READY_FOR_FORGE":
                continue
            recommendation = "forge_with_canary"
            if executed_count == 0:
                recommendation = "forge_new_skill_family_entry"
            elif failed_count > 0:
                recommendation = "forge_with_reliability_tests"
            top_executor_targets = [
                {"key": key, "count": int(count)}
                for key, count in Counter(candidate.get("executor_targets") or {}).most_common(3)
            ]
            items.append(
                {
                    "candidate_key": str(candidate.get("candidate_key") or ""),
                    "stage": candidate_stage,
                    "reason_code": str(candidate.get("reason_code") or "UNKNOWN"),
                    "suggested_family": str(candidate.get("suggested_family") or "unknown"),
                    "missing_capability_tags": list(candidate.get("missing_capability_tags") or []),
                    "detected_count": detected_count,
                    "executed_count": executed_count,
                    "success_count": success_count,
                    "failed_count": failed_count,
                    "failure_rate": round(failure_rate, 4),
                    "success_rate": round(success_rate, 4),
                    "impact_sessions": unique_sessions,
                    "impact_requests": unique_requests,
                    "priority_score": priority_score,
                    "recommended_action": recommendation,
                    "top_executor_targets": top_executor_targets,
                    "first_detected_ts_ms": candidate.get("first_detected_ts_ms"),
                    "last_detected_ts_ms": candidate.get("last_detected_ts_ms"),
                    "last_execution_ts_ms": candidate.get("last_execution_ts_ms"),
                }
            )
        items.sort(
            key=lambda item: (
                float(item.get("priority_score") or 0.0),
                int(item.get("detected_count") or 0),
                int(item.get("impact_sessions") or 0),
            ),
            reverse=True,
        )
        return items[: max(1, int(limit))], thresholds

    def snapshot(
        self,
        *,
        limit_recent: int = 20,
        limit_hotspots: int = 8,
        limit_candidates: int = 8,
        min_candidate_detected: int = 3,
        min_candidate_sessions: int = 2,
        min_candidate_failure_rate: float = 0.35,
        min_candidate_failed: int = 2,
    ) -> dict[str, Any]:
        with self._lock:
            total_exec = int(self._executed_total)
            success_rate = float(self._success_total / total_exec) if total_exec > 0 else 0.0
            recent = list(self._recent_events[-max(1, int(limit_recent)):])
            hotspots = self._build_hotspots(limit=max(1, int(limit_hotspots)))
            forge_candidates, candidate_thresholds = self._build_forge_candidates(
                limit=max(1, int(limit_candidates)),
                min_detected_count=int(min_candidate_detected),
                min_unique_sessions=int(min_candidate_sessions),
                min_failure_rate=float(min_candidate_failure_rate),
                min_failed_count=int(min_candidate_failed),
            )
            return {
                "timestamp": _now_ms(),
                "detected_total": int(self._detected_total),
                "executed_total": total_exec,
                "success_total": int(self._success_total),
                "failed_total": int(self._failed_total),
                "success_rate": round(success_rate, 4),
                "active_runtime_capabilities": len(self._runtime_capabilities),
                "by_reason": [
                    {"key": key, "count": int(count)}
                    for key, count in self._by_reason.most_common()
                ],
                "by_family": [
                    {"key": key, "count": int(count)}
                    for key, count in self._by_family.most_common()
                ],
                "by_executor": [
                    {"key": key, "count": int(count)}
                    for key, count in self._by_executor.most_common()
                ],
                "hotspots": hotspots,
                "forge_candidates": forge_candidates,
                "candidate_thresholds": candidate_thresholds,
                "recent_events": list(recent),
            }


__all__ = ["CapabilityGapTelemetry"]
