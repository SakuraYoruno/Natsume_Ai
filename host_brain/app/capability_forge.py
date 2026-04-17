"""Capability Forge service for promoting gap hotspots into governed proposals."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from typing import Any, Mapping

from host_brain.app.capability_gap_telemetry import CapabilityGapTelemetry
from host_brain.governor.tokens import TOKEN_TYPE_APPROVAL, TokenService


_FORGE_STATE_STAGE = "CAPABILITY_FORGE_STATE"
_FORGE_EVENT_CREATE = "CAPABILITY_FORGE_EVENT_CREATED"
_FORGE_EVENT_APPROVE = "CAPABILITY_FORGE_EVENT_APPROVED"
_FORGE_EVENT_ROLLBACK = "CAPABILITY_FORGE_EVENT_ROLLED_BACK"
_FORGE_EVENT_RUNTIME = "CAPABILITY_FORGE_EVENT_RUNTIME_RESULT"
_FORGE_REASON_AUTO_ROLLBACK = "CAPABILITY_FORGE_AUTO_ROLLBACK"

_DEFAULT_EXECUTOR_BY_FAMILY = {
    "code": "codex_codegen",
    "translation": "translate_agent",
    "perception": "vision_agent",
    "vision": "vision_agent",
    "system": "repair_agent",
    "reasoning": "intent_agent",
    "social": "chat_agent",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_stage(stage: str | None) -> str:
    normalized = str(stage or "").strip().lower()
    if normalized in {"shadow", "shadow_canary", "proposed"}:
        return "SHADOW"
    if normalized == "canary":
        return "CANARY"
    if normalized == "full":
        return "FULL"
    if normalized == "rolled_back":
        return "ROLLED_BACK"
    return "SHADOW"


def _build_proposal_id(candidate_key: str, ts_ms: int) -> str:
    digest = hashlib.sha1(f"{candidate_key}|{ts_ms}".encode("utf-8")).hexdigest()[:14]
    return f"forge_prop_{digest}"


def _stable_percent(request_id: str, proposal_id: str) -> int:
    digest = hashlib.sha1(f"{request_id}|{proposal_id}".encode("utf-8")).hexdigest()[:8]
    value = int(digest, 16) % 100
    return int(value + 1)


def _p95_int(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(int(max(0, v)) for v in values)
    index = max(0, int(len(ordered) * 0.95) - 1)
    return int(ordered[index])


def _parse_bool_env(name: str, *, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(minimum), min(int(maximum), value))


def _parse_float_env(name: str, *, default: float, minimum: float, maximum: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except Exception:
        return float(default)
    return max(float(minimum), min(float(maximum), value))


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized = [
        str(tag or "").strip().lower()
        for tag in list(tags or [])
        if str(tag or "").strip()
    ]
    return list(dict.fromkeys(normalized))


def _build_candidate_key(*, reason_code: str, suggested_family: str, missing_capability_tags: list[str]) -> str:
    return "|".join(
        [
            str(reason_code or "UNKNOWN").strip().upper() or "UNKNOWN",
            str(suggested_family or "unknown").strip().lower() or "unknown",
            ",".join(sorted(_normalize_tags(missing_capability_tags))),
        ]
    )


class CapabilityForgeService:
    """Forge proposal registry with governor approval-token gate and replay persistence."""

    _TOKEN_SKILL_NAME = "capability_forge"
    _TOKEN_SEQ = 1

    def __init__(
        self,
        *,
        telemetry: CapabilityGapTelemetry,
        token_service: TokenService | None = None,
        ledger: Any | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._token_service = token_service or TokenService()
        self._ledger = ledger
        self._lock = threading.RLock()
        self._loaded_from_ledger = False
        self._proposals: dict[str, dict[str, Any]] = {}
        self._runtime_match_counters: dict[str, int] = {
            "total": 0,
            "applied": 0,
            "full_hit": 0,
            "canary_hit": 0,
            "canary_miss": 0,
            "no_active_proposal": 0,
            "invalid_input": 0,
            "no_match": 0,
        }

    def _audit(self, *, request_id: str, stage: str, reason_code: str, detail: str) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.log_error(
                request_id=request_id,
                stage=stage,
                reason_code=reason_code,
                detail=detail[:1500],
            )
        except Exception:
            return

    def _persist_state_event(self, *, proposal: Mapping[str, Any], event: str, actor: str, reason: str | None = None) -> None:
        if self._ledger is None:
            return
        payload = dict(proposal)
        proposal_id = str(payload.get("proposal_id") or "").strip()
        if not proposal_id:
            return
        now = _now_ms()
        promotion_stage = str(dict(payload.get("promotion") or {}).get("stage") or "PROPOSED").strip().upper()
        status = str(payload.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        try:
            self._ledger.log_capability_forge_proposal(
                {
                    "proposal_id": proposal_id,
                    "event_type": str(event or "").strip().upper(),
                    "status": status,
                    "stage": promotion_stage,
                    "session_id": str(payload.get("session_id") or "").strip(),
                    "candidate_key": str(payload.get("candidate_key") or "").strip(),
                    "reason_code": str(reason or "").strip().upper() or None,
                    "actor": str(actor or "").strip(),
                    "payload": payload,
                    "ts_ms": now,
                }
            )
        except Exception:
            # backward compatibility fallback
            try:
                legacy = {
                    "proposal_id": proposal_id,
                    "event": str(event or "").strip().upper(),
                    "actor": str(actor or "").strip(),
                    "reason": str(reason or "").strip(),
                    "proposal_snapshot": payload,
                    "ts_ms": now,
                }
                self._ledger.log_error(
                    request_id=proposal_id,
                    stage=_FORGE_STATE_STAGE,
                    reason_code=str(event or "").strip().upper(),
                    detail=json.dumps(legacy, ensure_ascii=False),
                )
            except Exception:
                return

    def _ensure_loaded_from_ledger(self) -> None:
        if self._loaded_from_ledger:
            return
        with self._lock:
            if self._loaded_from_ledger:
                return
            if self._ledger is None:
                self._loaded_from_ledger = True
                return
            try:
                rows = list(self._ledger.list_capability_forge_proposals(limit=200000, offset=0))
            except Exception:
                rows = []
            restored: dict[str, dict[str, Any]] = {}
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                snapshot = payload
                proposal_id = str(snapshot.get("proposal_id") or "").strip()
                if not proposal_id:
                    continue
                restored[proposal_id] = dict(snapshot)
            # legacy fallback: recover from prior error_log encoding
            if not restored:
                try:
                    legacy_rows = list(self._ledger.fetch_by_time_range("error_log", limit=200000, offset=0))
                except Exception:
                    legacy_rows = []
                for row in legacy_rows:
                    if not isinstance(row, Mapping):
                        continue
                    if str(row.get("stage") or "").strip().upper() != _FORGE_STATE_STAGE:
                        continue
                    raw_detail = str(row.get("detail") or "").strip()
                    if not raw_detail:
                        continue
                    try:
                        payload = json.loads(raw_detail)
                    except Exception:
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    snapshot = payload.get("proposal_snapshot")
                    if not isinstance(snapshot, Mapping):
                        continue
                    proposal_id = str(snapshot.get("proposal_id") or "").strip()
                    if not proposal_id:
                        continue
                    restored[proposal_id] = dict(snapshot)
            for proposal_id, snapshot in list(restored.items()):
                if not isinstance(snapshot, Mapping):
                    continue
                normalized = dict(snapshot)
                runtime_stats = dict(normalized.get("runtime_stats") or {})
                runtime_stats.setdefault("total", 0)
                runtime_stats.setdefault("success", 0)
                runtime_stats.setdefault("failed", 0)
                runtime_stats.setdefault("consecutive_failures", 0)
                runtime_stats.setdefault("window_total", 0)
                runtime_stats.setdefault("window_failed", 0)
                runtime_stats.setdefault("window_failure_rate", 0.0)
                runtime_stats.setdefault("window_success_rate", 0.0)
                runtime_stats.setdefault("window_p95_latency_ms", 0)
                runtime_stats.setdefault("latency_ema_ms", 0.0)
                runtime_stats.setdefault("stability_score", 0.0)
                runtime_stats.setdefault("health_score", 0.0)
                runtime_stats.setdefault("recent_results", [])
                runtime_stats.setdefault("adaptive_tune_count", 0)
                runtime_stats.setdefault("last_tune_action", "NONE")
                runtime_stats.setdefault("last_tune_reason", None)
                runtime_stats.setdefault("last_tune_ts_ms", None)
                runtime_stats.setdefault("auto_rollback_triggered", False)
                normalized["runtime_stats"] = runtime_stats
                restored[proposal_id] = normalized
            self._proposals = restored
            self._loaded_from_ledger = True

    def _resolve_candidate(self, candidate_key: str | None) -> dict[str, Any] | None:
        key = str(candidate_key or "").strip()
        if key:
            return self._telemetry.get_candidate_detail(key)
        snapshot = self._telemetry.snapshot(
            limit_recent=5,
            limit_hotspots=1,
            limit_candidates=1,
            min_candidate_detected=2,
            min_candidate_sessions=1,
            min_candidate_failure_rate=0.0,
            min_candidate_failed=0,
        )
        forge_candidates = list(snapshot.get("forge_candidates") or [])
        if forge_candidates:
            top = forge_candidates[0]
            detail = self._telemetry.get_candidate_detail(str(top.get("candidate_key") or ""))
            if detail is not None:
                return detail
        hotspots = list(snapshot.get("hotspots") or [])
        if hotspots:
            top = hotspots[0]
            detail = self._telemetry.get_candidate_detail(str(top.get("candidate_key") or ""))
            if detail is not None:
                return detail
        return None

    def resolve_candidate(self, candidate_key: str | None = None) -> dict[str, Any] | None:
        return copy.deepcopy(self._resolve_candidate(candidate_key))

    def _find_latest_candidate_proposal_locked(self, candidate_key: str) -> dict[str, Any] | None:
        key = str(candidate_key or "").strip()
        if not key:
            return None
        latest: dict[str, Any] | None = None
        latest_ts = -1
        for proposal in self._proposals.values():
            if str(proposal.get("candidate_key") or "").strip() != key:
                continue
            ts = int(proposal.get("updated_ts_ms") or proposal.get("created_ts_ms") or 0)
            if ts >= latest_ts:
                latest_ts = ts
                latest = dict(proposal)
        return latest

    def _get_auto_plan_thresholds(self) -> dict[str, Any]:
        return {
            "enabled": _parse_bool_env("HOST_CAPABILITY_FORGE_AUTO_PROPOSAL_ENABLED", default=True),
            "min_detected_count": _parse_int_env(
                "HOST_CAPABILITY_FORGE_AUTO_MIN_DETECTED",
                default=3,
                minimum=1,
                maximum=100,
            ),
            "min_unique_sessions": _parse_int_env(
                "HOST_CAPABILITY_FORGE_AUTO_MIN_SESSIONS",
                default=2,
                minimum=1,
                maximum=100,
            ),
            "min_failure_rate": _parse_float_env(
                "HOST_CAPABILITY_FORGE_AUTO_MIN_FAILURE_RATE",
                default=0.35,
                minimum=0.0,
                maximum=1.0,
            ),
            "min_failed_count": _parse_int_env(
                "HOST_CAPABILITY_FORGE_AUTO_MIN_FAILED",
                default=2,
                minimum=0,
                maximum=100,
            ),
            "dedupe_cooldown_ms": _parse_int_env(
                "HOST_CAPABILITY_FORGE_AUTO_DEDUPE_COOLDOWN_MS",
                default=1800000,
                minimum=60000,
                maximum=86400000,
            ),
            "canary_traffic_percent": _parse_int_env(
                "HOST_CAPABILITY_FORGE_AUTO_CANARY_TRAFFIC_PERCENT",
                default=5,
                minimum=1,
                maximum=100,
            ),
            "auto_approve_enabled": _parse_bool_env(
                "HOST_CAPABILITY_FORGE_AUTO_APPROVE_ENABLED",
                default=False,
            ),
            "auto_approve_stage": _normalize_stage(
                str(os.getenv("HOST_CAPABILITY_FORGE_AUTO_APPROVE_STAGE") or "shadow").strip()
            ),
            "auto_approver": str(os.getenv("HOST_CAPABILITY_FORGE_AUTO_APPROVER") or "auto_forge").strip()
            or "auto_forge",
        }

    def maybe_auto_plan_for_gap(
        self,
        *,
        request_id: str,
        session_id: str,
        reason_code: str,
        suggested_family: str,
        missing_capability_tags: list[str] | tuple[str, ...] | None = None,
        proposer: str = "executive_runtime",
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        normalized_request_id = str(request_id or "").strip()
        thresholds = self._get_auto_plan_thresholds()
        if not bool(thresholds.get("enabled")):
            return {
                "triggered": False,
                "reason": "AUTO_PROPOSAL_DISABLED",
                "request_id": normalized_request_id,
            }

        normalized_reason = str(reason_code or "UNKNOWN").strip().upper() or "UNKNOWN"
        normalized_family = str(suggested_family or "unknown").strip().lower() or "unknown"
        normalized_tags = _normalize_tags(missing_capability_tags)
        candidate_key = _build_candidate_key(
            reason_code=normalized_reason,
            suggested_family=normalized_family,
            missing_capability_tags=normalized_tags,
        )
        candidate = self._telemetry.get_candidate_detail(candidate_key)
        if candidate is None:
            return {
                "triggered": False,
                "reason": "CANDIDATE_NOT_FOUND",
                "request_id": normalized_request_id,
                "candidate_key": candidate_key,
            }

        detected_count = int(candidate.get("detected_count") or 0)
        executed_count = int(candidate.get("executed_count") or 0)
        failed_count = int(candidate.get("failed_count") or 0)
        failure_rate = float(candidate.get("failure_rate") or 0.0)
        unique_sessions = int(candidate.get("impact_sessions") or 0)
        min_detected_count = int(thresholds.get("min_detected_count") or 3)
        min_unique_sessions = int(thresholds.get("min_unique_sessions") or 2)
        min_failure_rate = float(thresholds.get("min_failure_rate") or 0.35)
        min_failed_count = int(thresholds.get("min_failed_count") or 2)

        meets_threshold = (
            detected_count >= min_detected_count
            and unique_sessions >= min_unique_sessions
            and (
                failed_count >= min_failed_count
                or failure_rate >= min_failure_rate
                or (executed_count == 0 and detected_count >= max(4, min_detected_count * 2))
            )
        )
        if not meets_threshold:
            return {
                "triggered": False,
                "reason": "THRESHOLD_NOT_MET",
                "request_id": normalized_request_id,
                "candidate_key": candidate_key,
                "metrics": {
                    "detected_count": detected_count,
                    "executed_count": executed_count,
                    "failed_count": failed_count,
                    "failure_rate": round(failure_rate, 4),
                    "impact_sessions": unique_sessions,
                },
                "thresholds": {
                    "min_detected_count": min_detected_count,
                    "min_unique_sessions": min_unique_sessions,
                    "min_failure_rate": round(min_failure_rate, 4),
                    "min_failed_count": min_failed_count,
                },
            }

        now = _now_ms()
        dedupe_cooldown_ms = int(thresholds.get("dedupe_cooldown_ms") or 1800000)
        with self._lock:
            latest = self._find_latest_candidate_proposal_locked(candidate_key)
        if latest:
            status = str(latest.get("status") or "").strip().upper()
            updated_ts = int(latest.get("updated_ts_ms") or latest.get("created_ts_ms") or 0)
            within_cooldown = updated_ts > 0 and (now - updated_ts) < dedupe_cooldown_ms
            if status in {"PENDING_APPROVAL", "APPROVED"}:
                return {
                    "triggered": False,
                    "reason": "EXISTING_ACTIVE_PROPOSAL",
                    "request_id": normalized_request_id,
                    "candidate_key": candidate_key,
                    "proposal_id": str(latest.get("proposal_id") or ""),
                    "status": status,
                    "within_cooldown": within_cooldown,
                }
            if status == "ROLLED_BACK" and within_cooldown:
                return {
                    "triggered": False,
                    "reason": "RECENT_ROLLBACK_COOLDOWN",
                    "request_id": normalized_request_id,
                    "candidate_key": candidate_key,
                    "proposal_id": str(latest.get("proposal_id") or ""),
                    "status": status,
                    "within_cooldown": True,
                }

        created = self.create_proposal(
            session_id=session_id,
            proposer=proposer,
            candidate_key=candidate_key,
            canary_traffic_percent=int(thresholds.get("canary_traffic_percent") or 5),
        )
        proposal_id = str(created.get("proposal_id") or "")
        result: dict[str, Any] = {
            "triggered": True,
            "reason": "PROPOSAL_CREATED",
            "request_id": normalized_request_id,
            "candidate_key": candidate_key,
            "proposal_id": proposal_id,
            "status": str(created.get("status") or "PENDING_APPROVAL"),
            "auto_approved": False,
        }

        if not bool(thresholds.get("auto_approve_enabled")):
            return result

        gate = dict(created.get("governor_gate") or {})
        token = str(gate.get("approval_token_ref") or "").strip()
        if not token or not proposal_id:
            result["reason"] = "PROPOSAL_CREATED_TOKEN_MISSING"
            return result
        approved = self.approve_and_promote(
            proposal_id=proposal_id,
            approval_token_ref=token,
            approver=str(thresholds.get("auto_approver") or "auto_forge"),
            target_stage=str(thresholds.get("auto_approve_stage") or "SHADOW"),
        )
        result["reason"] = "PROPOSAL_CREATED_AND_APPROVED"
        result["auto_approved"] = True
        result["status"] = str(approved.get("status") or "APPROVED")
        result["stage"] = str(dict(approved.get("promotion") or {}).get("stage") or "")
        return result

    def _resolve_executor_target(self, proposal: Mapping[str, Any]) -> str:
        artifact = dict(proposal.get("artifact") or {})
        direct = str(artifact.get("executor_target") or "").strip()
        if direct:
            return direct
        metrics = dict(proposal.get("candidate_metrics") or {})
        top_targets = list(metrics.get("top_executor_targets") or [])
        if top_targets:
            first = top_targets[0]
            if isinstance(first, Mapping):
                target = str(first.get("key") or "").strip()
                if target:
                    return target
        family = str(artifact.get("skill_family") or metrics.get("suggested_family") or "reasoning").strip().lower()
        return _DEFAULT_EXECUTOR_BY_FAMILY.get(family, "intent_agent")

    def _build_artifact(
        self,
        *,
        proposal_id: str,
        session_id: str,
        proposer: str,
        candidate: Mapping[str, Any],
        approval_token_ref: str,
        canary_traffic_percent: int,
        ts_ms: int,
    ) -> dict[str, Any]:
        family = str(candidate.get("suggested_family") or "unknown").strip().lower() or "unknown"
        tags = [
            str(tag).strip().lower()
            for tag in list(candidate.get("missing_capability_tags") or [])
            if str(tag or "").strip()
        ]
        candidate_key = str(candidate.get("candidate_key") or "")
        top_targets = [
            dict(item)
            for item in list(candidate.get("top_executor_targets") or [])
            if isinstance(item, Mapping)
        ]
        executor_target = ""
        if top_targets:
            executor_target = str(top_targets[0].get("key") or "").strip()
        if not executor_target:
            executor_target = _DEFAULT_EXECUTOR_BY_FAMILY.get(family, "intent_agent")
        return {
            "proposal_id": proposal_id,
            "candidate_key": candidate_key,
            "status": "PENDING_APPROVAL",
            "created_ts_ms": ts_ms,
            "updated_ts_ms": ts_ms,
            "session_id": session_id,
            "proposer": proposer,
            "candidate_metrics": {
                "reason_code": str(candidate.get("reason_code") or "UNKNOWN"),
                "suggested_family": family,
                "missing_capability_tags": tags,
                "detected_count": int(candidate.get("detected_count") or 0),
                "executed_count": int(candidate.get("executed_count") or 0),
                "success_count": int(candidate.get("success_count") or 0),
                "failed_count": int(candidate.get("failed_count") or 0),
                "failure_rate": float(candidate.get("failure_rate") or 0.0),
                "impact_sessions": int(candidate.get("impact_sessions") or 0),
                "impact_requests": int(candidate.get("impact_requests") or 0),
                "top_executor_targets": top_targets,
            },
            "artifact": {
                "skill_family": family,
                "capability_tags": tags,
                "executor_target": executor_target,
                "proposal_summary": (
                    f"Forge skill capability for family={family} from candidate={candidate_key} "
                    f"with reason={str(candidate.get('reason_code') or 'UNKNOWN')}"
                ),
                "implementation_blueprint": [
                    "define skill contract and I/O schema",
                    "implement executor adapter with guarded side effects",
                    "wire skill registry metadata (family/tags/risk/fallback)",
                ],
                "test_blueprint": [
                    "unit tests for deterministic routing and executor output",
                    "integration tests for gateway->intent->executive->skill chain",
                    "rollback validation tests in canary/full stage",
                ],
            },
            "governor_gate": {
                "required": True,
                "approval_token_ref": approval_token_ref,
                "token_skill_name": self._TOKEN_SKILL_NAME,
                "token_seq": self._TOKEN_SEQ,
                "token_consumed": False,
                "issued_ts_ms": ts_ms,
                "expiry_ts_ms": ts_ms + int(getattr(self._token_service, "approval_ttl_ms", 120000)),
            },
            "release_plan": {
                "shadow": {"enabled": True, "state": "PENDING"},
                "canary": {
                    "enabled": True,
                    "state": "PENDING",
                    "traffic_percent": max(1, min(100, int(canary_traffic_percent))),
                },
                "full": {"enabled": True, "state": "PENDING"},
                "rollback": {
                    "enabled": True,
                    "auto_trigger_failure_rate": 0.35,
                    "observe_window_minutes": 30,
                    "observe_min_samples": 5,
                    "auto_trigger_consecutive_failures": 3,
                },
                "adaptive": {
                    "enabled": True,
                    "min_samples": 8,
                    "cooldown_samples": 4,
                    "target_failure_rate": 0.25,
                    "target_health_score": 75.0,
                    "latency_guard_ms": 1500,
                    "weight_success_rate": 0.55,
                    "weight_latency": 0.25,
                    "weight_stability": 0.20,
                    "increase_step_percent": 5,
                    "decrease_step_percent": 10,
                    "max_canary_percent": 50,
                    "min_canary_percent": 1,
                    "last_tuned_total": 0,
                    "last_tuned_ts_ms": None,
                    "last_action": "INIT",
                    "last_reason": None,
                },
            },
            "promotion": {
                "stage": "PROPOSED",
                "history": [
                    {
                        "ts_ms": ts_ms,
                        "event": "PROPOSED",
                        "actor": proposer,
                    }
                ],
            },
            "runtime_stats": {
                "total": 0,
                "success": 0,
                "failed": 0,
                "consecutive_failures": 0,
                "failure_rate": 0.0,
                "window_total": 0,
                "window_failed": 0,
                "window_failure_rate": 0.0,
                "window_success_rate": 0.0,
                "window_p95_latency_ms": 0,
                "latency_ema_ms": 0.0,
                "stability_score": 0.0,
                "health_score": 0.0,
                "recent_results": [],
                "adaptive_tune_count": 0,
                "last_tune_action": "NONE",
                "last_tune_reason": None,
                "last_tune_ts_ms": None,
                "last_error_code": None,
                "last_latency_ms": 0,
                "last_result_ts_ms": None,
                "auto_rollback_triggered": False,
            },
        }

    def create_proposal(
        self,
        *,
        session_id: str,
        proposer: str,
        candidate_key: str | None = None,
        canary_traffic_percent: int = 5,
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        candidate = self._resolve_candidate(candidate_key)
        if candidate is None:
            raise ValueError("CAPABILITY_FORGE_CANDIDATE_NOT_FOUND")
        now = _now_ms()
        proposal_id = _build_proposal_id(str(candidate.get("candidate_key") or ""), now)
        normalized_session = str(session_id or "").strip() or "forge-session"
        normalized_proposer = str(proposer or "").strip() or "capability_forge"
        approval_token_ref = self._token_service.issue_approval_token(
            intent_id=proposal_id,
            skill_name=self._TOKEN_SKILL_NAME,
            session_id=normalized_session,
            seq=self._TOKEN_SEQ,
            now_ts_ms=now,
        )
        artifact = self._build_artifact(
            proposal_id=proposal_id,
            session_id=normalized_session,
            proposer=normalized_proposer,
            candidate=candidate,
            approval_token_ref=approval_token_ref,
            canary_traffic_percent=canary_traffic_percent,
            ts_ms=now,
        )
        with self._lock:
            self._proposals[proposal_id] = artifact
        self._persist_state_event(proposal=artifact, event=_FORGE_EVENT_CREATE, actor=normalized_proposer)
        return copy.deepcopy(artifact)

    def list_proposals(self, *, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure_loaded_from_ledger()
        status_filter = str(status or "").strip().upper()
        with self._lock:
            items = list(self._proposals.values())
        if status_filter:
            items = [item for item in items if str(item.get("status") or "").upper() == status_filter]
        items.sort(key=lambda row: int(row.get("created_ts_ms") or 0), reverse=True)
        return [copy.deepcopy(item) for item in items[: max(1, int(limit))]]

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        self._ensure_loaded_from_ledger()
        key = str(proposal_id or "").strip()
        if not key:
            return None
        with self._lock:
            found = self._proposals.get(key)
        return copy.deepcopy(found) if isinstance(found, Mapping) else None

    def approve_and_promote(
        self,
        *,
        proposal_id: str,
        approval_token_ref: str,
        approver: str,
        target_stage: str = "shadow",
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        key = str(proposal_id or "").strip()
        if not key:
            raise ValueError("CAPABILITY_FORGE_INVALID_PROPOSAL_ID")
        with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                raise KeyError("CAPABILITY_FORGE_PROPOSAL_NOT_FOUND")
            if str(proposal.get("status") or "").upper() == "ROLLED_BACK":
                raise ValueError("CAPABILITY_FORGE_PROPOSAL_ROLLED_BACK")

            gate = dict(proposal.get("governor_gate") or {})
            provided_token = str(approval_token_ref or "").strip()
            expected_token = str(gate.get("approval_token_ref") or "").strip()
            if not expected_token or provided_token != expected_token:
                raise PermissionError("TOKEN_INVALID")
            if bool(gate.get("token_consumed")):
                raise PermissionError("TOKEN_ALREADY_USED")

            validation = self._token_service.validate_bound_token(
                provided_token,
                expected_type=TOKEN_TYPE_APPROVAL,
                intent_id=key,
                skill_name=str(gate.get("token_skill_name") or self._TOKEN_SKILL_NAME),
                session_id=str(proposal.get("session_id") or ""),
                seq=int(gate.get("token_seq") or self._TOKEN_SEQ),
                consume=True,
            )
            if not validation.valid:
                expiry_ts_ms = int(gate.get("expiry_ts_ms") or 0)
                if expiry_ts_ms > 0 and _now_ms() > expiry_ts_ms:
                    raise PermissionError("TOKEN_EXPIRED")
                # token_service 在进程重启后会丢内存态 token；允许基于持久化 token_ref 做一次恢复验证
                if not expected_token or provided_token != expected_token:
                    raise PermissionError(validation.reason_code or "TOKEN_INVALID")

            stage = _normalize_stage(target_stage)
            release_plan = dict(proposal.get("release_plan") or {})
            shadow = dict(release_plan.get("shadow") or {})
            canary = dict(release_plan.get("canary") or {})
            full = dict(release_plan.get("full") or {})

            shadow["state"] = "ACTIVE"
            if stage in {"CANARY", "FULL"}:
                canary["state"] = "ACTIVE"
            elif stage == "SHADOW":
                canary["state"] = "PENDING"
            if stage == "FULL":
                full["state"] = "ACTIVE"
            else:
                full["state"] = "PENDING"
            release_plan.update({"shadow": shadow, "canary": canary, "full": full})

            promotion = dict(proposal.get("promotion") or {})
            history = list(promotion.get("history") or [])
            now = _now_ms()
            history.append({"ts_ms": now, "event": f"APPROVED_{stage}", "actor": str(approver or "approver")})
            promotion["history"] = history
            promotion["stage"] = stage

            gate["token_consumed"] = True
            gate["consumed_ts_ms"] = now
            proposal["status"] = "APPROVED"
            proposal["updated_ts_ms"] = now
            proposal["release_plan"] = release_plan
            proposal["promotion"] = promotion
            proposal["governor_gate"] = gate
            self._proposals[key] = proposal

        self._persist_state_event(proposal=proposal, event=_FORGE_EVENT_APPROVE, actor=str(approver or "approver"))
        return copy.deepcopy(proposal)

    def rollback(
        self,
        *,
        proposal_id: str,
        operator: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        key = str(proposal_id or "").strip()
        if not key:
            raise ValueError("CAPABILITY_FORGE_INVALID_PROPOSAL_ID")
        with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                raise KeyError("CAPABILITY_FORGE_PROPOSAL_NOT_FOUND")
            proposal = self._rollback_locked(
                proposal=proposal,
                operator=str(operator or "operator"),
                reason=reason,
                reason_code="CAPABILITY_FORGE_MANUAL_ROLLBACK",
            )
            self._proposals[key] = proposal

        self._persist_state_event(
            proposal=proposal,
            event=_FORGE_EVENT_ROLLBACK,
            actor=str(operator or "operator"),
            reason=f"CAPABILITY_FORGE_MANUAL_ROLLBACK:{str(reason or '').strip()}",
        )
        return copy.deepcopy(proposal)

    def _maybe_apply_adaptive_tuning_locked(
        self,
        *,
        proposal: dict[str, Any],
        stats: dict[str, Any],
        now_ms: int,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        release_plan = dict(proposal.get("release_plan") or {})
        adaptive_cfg = dict(release_plan.get("adaptive") or {})
        if not bool(adaptive_cfg.get("enabled", True)):
            return proposal, None

        min_samples = max(1, int(adaptive_cfg.get("min_samples") or 8))
        cooldown_samples = max(1, int(adaptive_cfg.get("cooldown_samples") or 4))
        target_failure_rate = max(0.01, min(0.95, float(adaptive_cfg.get("target_failure_rate") or 0.25)))
        target_health_score = max(20.0, min(95.0, float(adaptive_cfg.get("target_health_score") or 75.0)))
        latency_guard_ms = max(100, int(adaptive_cfg.get("latency_guard_ms") or 1500))
        increase_step = max(1, int(adaptive_cfg.get("increase_step_percent") or 5))
        decrease_step = max(1, int(adaptive_cfg.get("decrease_step_percent") or 10))
        max_canary = max(1, min(100, int(adaptive_cfg.get("max_canary_percent") or 50)))
        min_canary = max(1, min(max_canary, int(adaptive_cfg.get("min_canary_percent") or 1)))
        last_tuned_total = int(adaptive_cfg.get("last_tuned_total") or 0)

        window_total = int(stats.get("window_total") or 0)
        window_failure_rate = max(0.0, min(1.0, float(stats.get("window_failure_rate") or 0.0)))
        health_score = max(0.0, min(100.0, float(stats.get("health_score") or 0.0)))
        window_p95_latency_ms = max(0, int(stats.get("window_p95_latency_ms") or 0))
        if window_total < min_samples:
            return proposal, None
        if (window_total - last_tuned_total) < cooldown_samples:
            return proposal, None

        canary_cfg = dict(release_plan.get("canary") or {})
        rollback_cfg = dict(release_plan.get("rollback") or {})
        current_percent = max(1, min(100, int(canary_cfg.get("traffic_percent") or 5)))
        current_threshold = max(0.0, min(1.0, float(rollback_cfg.get("auto_trigger_failure_rate") or 0.35)))
        low_band = max(0.0, target_failure_rate * 0.6)
        high_band = min(1.0, max(target_failure_rate, target_failure_rate * 1.2))
        latency_good = (window_p95_latency_ms <= 0) or (window_p95_latency_ms <= int(latency_guard_ms * 0.85))
        latency_bad = (window_p95_latency_ms > 0) and (window_p95_latency_ms >= int(latency_guard_ms * 1.2))

        new_percent = current_percent
        new_threshold = current_threshold
        action = "HOLD"
        reason = "within_target_band"
        tuned = False
        if (health_score >= (target_health_score + 8.0)) and (window_failure_rate <= low_band) and latency_good:
            new_percent = min(max_canary, current_percent + increase_step)
            new_threshold = min(0.8, max(current_threshold, target_failure_rate + 0.1))
            action = "SCALE_UP"
            reason = "high_health_score"
            tuned = (new_percent != current_percent) or (round(new_threshold, 4) != round(current_threshold, 4))
        elif (health_score <= max(35.0, target_health_score - 12.0)) or (window_failure_rate >= high_band) or latency_bad:
            new_percent = max(min_canary, current_percent - decrease_step)
            new_threshold = max(0.15, min(current_threshold, target_failure_rate + 0.05))
            action = "SCALE_DOWN"
            if latency_bad:
                reason = "latency_guard_exceeded"
            elif window_failure_rate >= high_band:
                reason = "high_failure_rate"
            else:
                reason = "low_health_score"
            tuned = (new_percent != current_percent) or (round(new_threshold, 4) != round(current_threshold, 4))

        adaptive_cfg["last_tuned_total"] = window_total
        adaptive_cfg["last_tuned_ts_ms"] = now_ms
        adaptive_cfg["last_action"] = action
        adaptive_cfg["last_reason"] = reason
        release_plan["adaptive"] = adaptive_cfg

        if tuned:
            canary_cfg["traffic_percent"] = new_percent
            rollback_cfg["auto_trigger_failure_rate"] = round(new_threshold, 4)
            release_plan["canary"] = canary_cfg
            release_plan["rollback"] = rollback_cfg
            proposal["release_plan"] = release_plan
            tune_count = int(stats.get("adaptive_tune_count") or 0) + 1
            stats["adaptive_tune_count"] = tune_count
            stats["last_tune_action"] = action
            stats["last_tune_reason"] = reason
            stats["last_tune_ts_ms"] = now_ms
            proposal["runtime_stats"] = stats
            return proposal, {
                "action": action,
                "reason": reason,
                "window_total": window_total,
                "window_failure_rate": round(window_failure_rate, 4),
                "health_score": round(health_score, 2),
                "window_p95_latency_ms": window_p95_latency_ms,
                "target_health_score": round(target_health_score, 2),
                "latency_guard_ms": latency_guard_ms,
                "from_traffic_percent": current_percent,
                "to_traffic_percent": new_percent,
                "from_failure_threshold": round(current_threshold, 4),
                "to_failure_threshold": round(new_threshold, 4),
            }

        stats["last_tune_action"] = action
        stats["last_tune_reason"] = reason
        stats["last_tune_ts_ms"] = now_ms
        proposal["runtime_stats"] = stats
        proposal["release_plan"] = release_plan
        return proposal, {
            "action": action,
            "reason": reason,
            "window_total": window_total,
            "window_failure_rate": round(window_failure_rate, 4),
            "health_score": round(health_score, 2),
            "window_p95_latency_ms": window_p95_latency_ms,
            "target_health_score": round(target_health_score, 2),
            "latency_guard_ms": latency_guard_ms,
            "from_traffic_percent": current_percent,
            "to_traffic_percent": current_percent,
            "from_failure_threshold": round(current_threshold, 4),
            "to_failure_threshold": round(current_threshold, 4),
        }

    def _rollback_locked(
        self,
        *,
        proposal: dict[str, Any],
        operator: str,
        reason: str | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        release_plan = dict(proposal.get("release_plan") or {})
        for plan_key in ("shadow", "canary", "full"):
            unit = dict(release_plan.get(plan_key) or {})
            unit["state"] = "ROLLED_BACK"
            release_plan[plan_key] = unit
        rollback_cfg = dict(release_plan.get("rollback") or {})
        rollback_cfg["triggered"] = True
        rollback_cfg["triggered_ts_ms"] = _now_ms()
        rollback_cfg["trigger_reason"] = str(reason or "").strip()
        rollback_cfg["trigger_reason_code"] = str(reason_code or "").strip().upper() or None
        release_plan["rollback"] = rollback_cfg

        promotion = dict(proposal.get("promotion") or {})
        history = list(promotion.get("history") or [])
        now = _now_ms()
        history.append(
            {
                "ts_ms": now,
                "event": "ROLLED_BACK",
                "actor": str(operator or "operator"),
                "reason": str(reason or "").strip()[:200],
                "reason_code": str(reason_code or "").strip().upper() or None,
            }
        )
        promotion["history"] = history
        promotion["stage"] = "ROLLED_BACK"

        runtime_stats = dict(proposal.get("runtime_stats") or {})
        if str(reason_code or "").strip().upper() == _FORGE_REASON_AUTO_ROLLBACK:
            runtime_stats["auto_rollback_triggered"] = True

        proposal["status"] = "ROLLED_BACK"
        proposal["updated_ts_ms"] = now
        proposal["release_plan"] = release_plan
        proposal["promotion"] = promotion
        proposal["runtime_stats"] = runtime_stats
        return proposal

    def record_runtime_result(
        self,
        *,
        proposal_id: str,
        success: bool,
        latency_ms: int | float | None = None,
        error_code: str | None = None,
        actor: str = "executive_runtime",
    ) -> dict[str, Any] | None:
        self._ensure_loaded_from_ledger()
        key = str(proposal_id or "").strip()
        if not key:
            return None
        auto_rollback = False
        auto_reason = None
        tune_decision: dict[str, Any] | None = None
        with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                return None
            if str(proposal.get("status") or "").upper() != "APPROVED":
                return copy.deepcopy(proposal)

            promotion = dict(proposal.get("promotion") or {})
            stage = str(promotion.get("stage") or "SHADOW").strip().upper()
            if stage not in {"CANARY", "FULL"}:
                return copy.deepcopy(proposal)

            release_plan = dict(proposal.get("release_plan") or {})
            rollback_cfg = dict(release_plan.get("rollback") or {})
            adaptive_cfg = dict(release_plan.get("adaptive") or {})
            observe_window_minutes = max(1, int(rollback_cfg.get("observe_window_minutes") or 30))
            window_ms = observe_window_minutes * 60 * 1000
            latency_guard_ms = max(100, int(adaptive_cfg.get("latency_guard_ms") or 1500))
            weight_success_rate = max(0.0, float(adaptive_cfg.get("weight_success_rate") or 0.55))
            weight_latency = max(0.0, float(adaptive_cfg.get("weight_latency") or 0.25))
            weight_stability = max(0.0, float(adaptive_cfg.get("weight_stability") or 0.20))
            weight_total = weight_success_rate + weight_latency + weight_stability
            if weight_total <= 0.0:
                weight_success_rate, weight_latency, weight_stability = 0.55, 0.25, 0.20
                weight_total = 1.0
            ws = weight_success_rate / weight_total
            wl = weight_latency / weight_total
            wt = weight_stability / weight_total
            now_ms = _now_ms()
            latency_now = max(0, int(latency_ms or 0))
            stats = dict(proposal.get("runtime_stats") or {})
            total = int(stats.get("total") or 0) + 1
            succ = int(stats.get("success") or 0) + (1 if success else 0)
            failed = int(stats.get("failed") or 0) + (0 if success else 1)
            consecutive = 0 if success else int(stats.get("consecutive_failures") or 0) + 1
            failure_rate = float(failed / total) if total > 0 else 0.0
            recent_results_raw = list(stats.get("recent_results") or [])
            recent_results: list[dict[str, Any]] = []
            cutoff_ms = now_ms - window_ms
            for item in recent_results_raw:
                if not isinstance(item, Mapping):
                    continue
                item_ts = int(item.get("ts_ms") or 0)
                item_success = bool(item.get("success"))
                item_latency = max(0, int(item.get("latency_ms") or 0))
                if item_ts >= cutoff_ms:
                    recent_results.append({"ts_ms": item_ts, "success": item_success, "latency_ms": item_latency})
            recent_results.append({"ts_ms": now_ms, "success": bool(success), "latency_ms": latency_now})
            if len(recent_results) > 512:
                recent_results = recent_results[-512:]
            window_total = len(recent_results)
            window_failed = sum(1 for item in recent_results if not bool(item.get("success")))
            window_failure_rate = float(window_failed / window_total) if window_total > 0 else 0.0
            window_success_rate = max(0.0, min(1.0, 1.0 - window_failure_rate))
            window_latencies = [
                int(item.get("latency_ms") or 0)
                for item in recent_results
                if int(item.get("latency_ms") or 0) > 0
            ]
            window_p95_latency_ms = _p95_int(window_latencies)
            prev_ema = max(0.0, float(stats.get("latency_ema_ms") or 0.0))
            latency_ema_ms = (
                float(latency_now)
                if prev_ema <= 0.0
                else round((prev_ema * 0.75) + (float(latency_now) * 0.25), 2)
            )
            latency_penalty_ratio = max(0.0, (float(window_p95_latency_ms) - float(latency_guard_ms)) / float(latency_guard_ms))
            latency_score = max(0.0, min(100.0, 100.0 - (latency_penalty_ratio * 100.0)))
            stability_score = max(
                0.0,
                min(
                    100.0,
                    100.0
                    - min(70.0, float(consecutive * 18))
                    - min(30.0, float(window_failure_rate * 30.0)),
                ),
            )
            success_score = window_success_rate * 100.0
            health_score = max(
                0.0,
                min(100.0, round((success_score * ws) + (latency_score * wl) + (stability_score * wt), 2)),
            )
            stats.update(
                {
                    "total": total,
                    "success": succ,
                    "failed": failed,
                    "consecutive_failures": consecutive,
                    "failure_rate": round(failure_rate, 4),
                    "window_total": window_total,
                    "window_failed": window_failed,
                    "window_failure_rate": round(window_failure_rate, 4),
                    "window_success_rate": round(window_success_rate, 4),
                    "window_p95_latency_ms": int(window_p95_latency_ms),
                    "latency_ema_ms": float(latency_ema_ms),
                    "stability_score": round(stability_score, 2),
                    "health_score": float(health_score),
                    "recent_results": recent_results,
                    "last_error_code": (str(error_code or "").strip().upper() or None),
                    "last_latency_ms": latency_now,
                    "last_result_ts_ms": now_ms,
                }
            )
            proposal["runtime_stats"] = stats
            proposal["updated_ts_ms"] = now_ms

            if stage == "CANARY":
                proposal, tune_decision = self._maybe_apply_adaptive_tuning_locked(
                    proposal=proposal,
                    stats=stats,
                    now_ms=now_ms,
                )
                rollback_cfg = dict(dict(proposal.get("release_plan") or {}).get("rollback") or {})
                threshold = float(rollback_cfg.get("auto_trigger_failure_rate") or 0.35)
                min_samples = int(rollback_cfg.get("observe_min_samples") or 5)
                max_consecutive = int(rollback_cfg.get("auto_trigger_consecutive_failures") or 3)
                normalized_threshold = max(0.0, min(1.0, threshold))
                trigger_by_rate = (
                    window_total >= max(1, min_samples) and window_failure_rate >= normalized_threshold
                )
                trigger_by_consecutive = consecutive >= max(1, max_consecutive)
                if trigger_by_rate or trigger_by_consecutive:
                    auto_rollback = True
                    auto_reason = (
                        f"auto rollback canary due to {'failure_rate' if trigger_by_rate else 'consecutive_failures'}"
                    )
                    proposal = self._rollback_locked(
                        proposal=proposal,
                        operator="auto_guard",
                        reason=auto_reason,
                        reason_code=_FORGE_REASON_AUTO_ROLLBACK,
                    )

            self._proposals[key] = proposal

        if auto_rollback:
            self._persist_state_event(
                proposal=proposal,
                event=_FORGE_EVENT_ROLLBACK,
                actor="auto_guard",
                reason=f"{_FORGE_REASON_AUTO_ROLLBACK}:{str(auto_reason or '')}",
            )
            self._audit(
                request_id=key,
                stage="CAPABILITY_FORGE_AUTO_GUARD",
                reason_code=_FORGE_REASON_AUTO_ROLLBACK,
                detail=str(auto_reason or "auto rollback triggered"),
            )
        else:
            runtime_reason = "SUCCESS" if success else (str(error_code or "").strip().upper() or "FAILED")
            runtime_health_score = float(dict(proposal.get("runtime_stats") or {}).get("health_score") or 0.0)
            if tune_decision and str(tune_decision.get("action") or "").upper() in {"SCALE_UP", "SCALE_DOWN"}:
                runtime_reason = (
                    f"{runtime_reason}|{str(tune_decision.get('action') or 'HOLD')}|"
                    f"traffic={int(tune_decision.get('to_traffic_percent') or 0)}|"
                    f"threshold={float(tune_decision.get('to_failure_threshold') or 0.0):.3f}|"
                    f"health={runtime_health_score:.2f}"
                )
            self._persist_state_event(
                proposal=proposal,
                event=_FORGE_EVENT_RUNTIME,
                actor=actor,
                reason=runtime_reason,
            )
        return copy.deepcopy(proposal)

    def get_runtime_route_override(
        self,
        *,
        request_id: str,
        suggested_family: str,
        missing_capability_tags: list[str] | tuple[str, ...] | None = None,
        reason_code: str | None = None,
    ) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        req = str(request_id or "").strip()
        family = str(suggested_family or "").strip().lower()
        tags = {
            str(tag or "").strip().lower()
            for tag in list(missing_capability_tags or [])
            if str(tag or "").strip()
        }
        reason = str(reason_code or "").strip().upper()
        with self._lock:
            self._runtime_match_counters["total"] = int(self._runtime_match_counters.get("total") or 0) + 1
        if not req or not family:
            with self._lock:
                self._runtime_match_counters["invalid_input"] = int(self._runtime_match_counters.get("invalid_input") or 0) + 1
            return {"apply": False, "reason": "invalid_input"}

        with self._lock:
            proposals = list(self._proposals.values())
        proposals.sort(key=lambda row: int(row.get("updated_ts_ms") or row.get("created_ts_ms") or 0), reverse=True)
        has_approved = False
        for proposal in proposals:
            if str(proposal.get("status") or "").upper() != "APPROVED":
                continue
            has_approved = True
            artifact = dict(proposal.get("artifact") or {})
            metrics = dict(proposal.get("candidate_metrics") or {})
            p_family = str(artifact.get("skill_family") or metrics.get("suggested_family") or "").strip().lower()
            if p_family != family:
                continue
            p_reason = str(metrics.get("reason_code") or "").strip().upper()
            if reason and p_reason and reason != p_reason:
                continue
            p_tags = {
                str(tag).strip().lower()
                for tag in list(artifact.get("capability_tags") or metrics.get("missing_capability_tags") or [])
                if str(tag or "").strip()
            }
            if p_tags and tags and p_tags.isdisjoint(tags):
                continue
            stage = str(dict(proposal.get("promotion") or {}).get("stage") or "SHADOW").strip().upper()
            if stage == "ROLLED_BACK" or stage == "SHADOW":
                continue
            executor_target = self._resolve_executor_target(proposal)
            if stage == "FULL":
                with self._lock:
                    self._runtime_match_counters["applied"] = int(self._runtime_match_counters.get("applied") or 0) + 1
                    self._runtime_match_counters["full_hit"] = int(self._runtime_match_counters.get("full_hit") or 0) + 1
                return {
                    "apply": True,
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "stage": "FULL",
                    "executor_target": executor_target,
                    "traffic_percent": 100,
                    "bucket": 100,
                }
            if stage == "CANARY":
                canary = dict(dict(proposal.get("release_plan") or {}).get("canary") or {})
                traffic_percent = max(1, min(100, int(canary.get("traffic_percent") or 5)))
                bucket = _stable_percent(req, str(proposal.get("proposal_id") or ""))
                if bucket <= traffic_percent:
                    with self._lock:
                        self._runtime_match_counters["applied"] = int(self._runtime_match_counters.get("applied") or 0) + 1
                        self._runtime_match_counters["canary_hit"] = int(self._runtime_match_counters.get("canary_hit") or 0) + 1
                    return {
                        "apply": True,
                        "proposal_id": str(proposal.get("proposal_id") or ""),
                        "stage": "CANARY",
                        "executor_target": executor_target,
                        "traffic_percent": traffic_percent,
                        "bucket": bucket,
                    }
                with self._lock:
                    self._runtime_match_counters["canary_miss"] = int(self._runtime_match_counters.get("canary_miss") or 0) + 1
                return {
                    "apply": False,
                    "reason": "canary_miss",
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "stage": "CANARY",
                    "traffic_percent": traffic_percent,
                    "bucket": bucket,
                }
        with self._lock:
            if has_approved:
                self._runtime_match_counters["no_match"] = int(self._runtime_match_counters.get("no_match") or 0) + 1
            else:
                self._runtime_match_counters["no_active_proposal"] = int(self._runtime_match_counters.get("no_active_proposal") or 0) + 1
        if has_approved:
            return {"apply": False, "reason": "no_match"}
        return {"apply": False, "reason": "no_active_proposal"}

    def runtime_match_stats(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._runtime_match_counters)
        total = int(counters.get("total") or 0)
        applied = int(counters.get("applied") or 0)
        return {
            "timestamp": _now_ms(),
            "counters": counters,
            "apply_rate": round((float(applied) / float(total)) if total > 0 else 0.0, 4),
        }

    def runtime_tuning_stats(self, *, limit: int = 20) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        with self._lock:
            proposals = list(self._proposals.values())
        proposals.sort(key=lambda row: int(row.get("updated_ts_ms") or row.get("created_ts_ms") or 0), reverse=True)

        items: list[dict[str, Any]] = []
        summary = {
            "total_proposals": 0,
            "approved_proposals": 0,
            "canary_active": 0,
            "adaptive_enabled": 0,
            "auto_rollback_triggered": 0,
            "last_action_scale_up": 0,
            "last_action_scale_down": 0,
            "last_action_hold": 0,
            "health_lt_40": 0,
            "health_lt_60": 0,
        }
        for proposal in proposals:
            summary["total_proposals"] = int(summary["total_proposals"]) + 1
            status = str(proposal.get("status") or "").strip().upper()
            if status == "APPROVED":
                summary["approved_proposals"] = int(summary["approved_proposals"]) + 1

            promotion = dict(proposal.get("promotion") or {})
            stage = str(promotion.get("stage") or "PROPOSED").strip().upper()
            if status == "APPROVED" and stage == "CANARY":
                summary["canary_active"] = int(summary["canary_active"]) + 1

            release_plan = dict(proposal.get("release_plan") or {})
            canary_cfg = dict(release_plan.get("canary") or {})
            rollback_cfg = dict(release_plan.get("rollback") or {})
            adaptive_cfg = dict(release_plan.get("adaptive") or {})
            adaptive_enabled = bool(adaptive_cfg.get("enabled", True))
            if adaptive_enabled:
                summary["adaptive_enabled"] = int(summary["adaptive_enabled"]) + 1

            runtime_stats = dict(proposal.get("runtime_stats") or {})
            if bool(runtime_stats.get("auto_rollback_triggered")):
                summary["auto_rollback_triggered"] = int(summary["auto_rollback_triggered"]) + 1
            health_score = max(0.0, min(100.0, float(runtime_stats.get("health_score") or 0.0)))
            if health_score < 40.0:
                summary["health_lt_40"] = int(summary["health_lt_40"]) + 1
            if health_score < 60.0:
                summary["health_lt_60"] = int(summary["health_lt_60"]) + 1

            last_action = str(
                runtime_stats.get("last_tune_action")
                or adaptive_cfg.get("last_action")
                or "NONE"
            ).strip().upper()
            if last_action == "SCALE_UP":
                summary["last_action_scale_up"] = int(summary["last_action_scale_up"]) + 1
            elif last_action == "SCALE_DOWN":
                summary["last_action_scale_down"] = int(summary["last_action_scale_down"]) + 1
            else:
                summary["last_action_hold"] = int(summary["last_action_hold"]) + 1

            items.append(
                {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "status": status,
                    "stage": stage,
                    "adaptive_enabled": adaptive_enabled,
                    "canary_traffic_percent": int(canary_cfg.get("traffic_percent") or 0),
                    "rollback_failure_threshold": float(rollback_cfg.get("auto_trigger_failure_rate") or 0.0),
                    "window_total": int(runtime_stats.get("window_total") or 0),
                    "window_failure_rate": float(runtime_stats.get("window_failure_rate") or 0.0),
                    "window_p95_latency_ms": int(runtime_stats.get("window_p95_latency_ms") or 0),
                    "latency_ema_ms": float(runtime_stats.get("latency_ema_ms") or 0.0),
                    "stability_score": float(runtime_stats.get("stability_score") or 0.0),
                    "health_score": float(runtime_stats.get("health_score") or 0.0),
                    "total": int(runtime_stats.get("total") or 0),
                    "failed": int(runtime_stats.get("failed") or 0),
                    "adaptive_tune_count": int(runtime_stats.get("adaptive_tune_count") or 0),
                    "last_tune_action": last_action,
                    "last_tune_reason": runtime_stats.get("last_tune_reason"),
                    "last_tune_ts_ms": runtime_stats.get("last_tune_ts_ms"),
                    "updated_ts_ms": int(proposal.get("updated_ts_ms") or 0),
                }
            )
        return {
            "timestamp": _now_ms(),
            "summary": summary,
            "items": items[: max(1, int(limit))],
        }

    def runtime_quality_stats(self, *, limit: int = 20) -> dict[str, Any]:
        self._ensure_loaded_from_ledger()
        with self._lock:
            proposals = list(self._proposals.values())
        proposals.sort(key=lambda row: int(row.get("updated_ts_ms") or row.get("created_ts_ms") or 0), reverse=True)

        tracked = 0
        unhealthy = 0
        critical = 0
        health_sum = 0.0
        failure_sum = 0.0
        latency_sum = 0.0
        latency_count = 0
        items: list[dict[str, Any]] = []
        for proposal in proposals:
            runtime_stats = dict(proposal.get("runtime_stats") or {})
            total = int(runtime_stats.get("total") or 0)
            if total <= 0:
                continue
            tracked += 1
            health_score = max(0.0, min(100.0, float(runtime_stats.get("health_score") or 0.0)))
            window_failure_rate = max(0.0, min(1.0, float(runtime_stats.get("window_failure_rate") or 0.0)))
            window_p95_latency_ms = max(0, int(runtime_stats.get("window_p95_latency_ms") or 0))
            if health_score < 60.0:
                unhealthy += 1
            if health_score < 40.0:
                critical += 1
            health_sum += health_score
            failure_sum += window_failure_rate
            if window_p95_latency_ms > 0:
                latency_sum += float(window_p95_latency_ms)
                latency_count += 1

            promotion = dict(proposal.get("promotion") or {})
            items.append(
                {
                    "proposal_id": str(proposal.get("proposal_id") or ""),
                    "status": str(proposal.get("status") or "").strip().upper(),
                    "stage": str(promotion.get("stage") or "PROPOSED").strip().upper(),
                    "health_score": round(health_score, 2),
                    "window_failure_rate": round(window_failure_rate, 4),
                    "window_p95_latency_ms": window_p95_latency_ms,
                    "stability_score": float(runtime_stats.get("stability_score") or 0.0),
                    "latency_ema_ms": float(runtime_stats.get("latency_ema_ms") or 0.0),
                    "adaptive_tune_count": int(runtime_stats.get("adaptive_tune_count") or 0),
                    "last_tune_action": str(runtime_stats.get("last_tune_action") or "NONE"),
                    "updated_ts_ms": int(proposal.get("updated_ts_ms") or 0),
                }
            )

        avg_health = round((health_sum / tracked), 2) if tracked > 0 else 0.0
        avg_failure_rate = round((failure_sum / tracked), 4) if tracked > 0 else 0.0
        avg_p95_latency = round((latency_sum / latency_count), 2) if latency_count > 0 else 0.0
        return {
            "timestamp": _now_ms(),
            "summary": {
                "tracked_proposals": tracked,
                "unhealthy_proposals": unhealthy,
                "critical_proposals": critical,
                "avg_health_score": avg_health,
                "avg_window_failure_rate": avg_failure_rate,
                "avg_window_p95_latency_ms": avg_p95_latency,
            },
            "items": items[: max(1, int(limit))],
        }


__all__ = ["CapabilityForgeService"]
