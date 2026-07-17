"""Read-only Security Immune evidence source for the standalone Obs process.

The Host process owns the Security Immune agent and writes its ``EvidenceStore``
files.  Obs is only allowed to read those files.  This module therefore has no
dependency on the agent, its providers, policy, or any writer-capable service.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SECURITY_IMMUNE_EVIDENCE_MISSING = "OBS_SECURITY_IMMUNE_EVIDENCE_MISSING"
SECURITY_IMMUNE_EVIDENCE_CORRUPT = "OBS_SECURITY_IMMUNE_EVIDENCE_CORRUPT"
SECURITY_IMMUNE_EVIDENCE_UNREADABLE = "OBS_SECURITY_IMMUNE_EVIDENCE_UNREADABLE"
SECURITY_IMMUNE_EVIDENCE_STALE = "OBS_SECURITY_IMMUNE_EVIDENCE_STALE"
SECURITY_IMMUNE_EVIDENCE_SOURCE_SCOPE = "security-immune-evidence-store"
DEFAULT_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS = 10 * 60_000
SECURITY_IMMUNE_EVIDENCE_MAX_FUTURE_SKEW_MS = 60_000

_RISK_LEVELS = ("none", "low", "medium", "high", "critical")
_MODES = {"observe", "hunt", "contain", "wartime", "forensic"}
_PUBLIC_PROVIDER_SOURCES = {
    "defender",
    "local_probe",
    "osquery",
    "persisted",
    "provider_fusion",
    "wazuh",
}
_PATH_OR_SECRET = re.compile(
    r"(?:[A-Za-z]:[\\/]|(?:^|\s)/(?:[^\s/]+/)+|file://|\.\.[\\/]"
    r"|(?:api[-_ ]?key|authorization|bearer|password|secret|token)\s*[:=])",
    re.IGNORECASE,
)


class SecurityImmuneSnapshotSourceError(RuntimeError):
    """Base class whose public representation never contains a filesystem path."""

    reason_code = SECURITY_IMMUNE_EVIDENCE_UNREADABLE
    public_detail = "security immune evidence is unreadable"

    def __init__(self, source_component: str) -> None:
        self.source_component = source_component
        self.source_scope = SECURITY_IMMUNE_EVIDENCE_SOURCE_SCOPE
        super().__init__(self.public_detail)

    def as_public_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "Natsume.operator.projection_source_unavailable.v1",
            "available": False,
            "reason_code": self.reason_code,
            "source_component": self.source_component,
            "source_scope": self.source_scope,
            "detail": self.public_detail,
        }


class SecurityImmuneSnapshotMissingError(SecurityImmuneSnapshotSourceError):
    reason_code = SECURITY_IMMUNE_EVIDENCE_MISSING
    public_detail = "security immune evidence is missing"


class SecurityImmuneSnapshotCorruptError(SecurityImmuneSnapshotSourceError):
    reason_code = SECURITY_IMMUNE_EVIDENCE_CORRUPT
    public_detail = "security immune evidence is corrupt"


class SecurityImmuneSnapshotUnreadableError(SecurityImmuneSnapshotSourceError):
    reason_code = SECURITY_IMMUNE_EVIDENCE_UNREADABLE
    public_detail = "security immune evidence is unreadable"


class SecurityImmuneSnapshotStaleError(SecurityImmuneSnapshotSourceError):
    reason_code = SECURITY_IMMUNE_EVIDENCE_STALE
    public_detail = "security immune evidence is stale"


@dataclass(frozen=True, slots=True)
class SecurityImmuneSnapshotRead:
    """Privacy-safe payload with separate source cursor and evidence time."""

    payload: dict[str, Any]
    source_cursor: int
    freshness_ts_ms: int


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _public_text(value: Any, *, default: str = "", limit: int = 300) -> str:
    if not isinstance(value, str):
        return default
    text = value.strip()
    if not text:
        return default
    if _PATH_OR_SECRET.search(text):
        return "[redacted]"
    return text[:limit]


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _is_json_int(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_mapping_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, Mapping) for item in value)


def _has_public_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_state_schema(raw: Mapping[str, Any]) -> int:
    """Validate the minimum schema emitted by ``SecuritySnapshot.as_dict``."""

    required = {
        "enabled",
        "mode",
        "assets",
        "incidents",
        "last_run_ts_ms",
        "run_count",
        "generated_ts_ms",
    }
    if not required.issubset(raw):
        raise ValueError("security evidence state is missing required fields")
    if not isinstance(raw.get("enabled"), bool):
        raise ValueError("security evidence enabled must be boolean")
    if str(raw.get("mode") or "").strip().lower() not in _MODES:
        raise ValueError("security evidence mode is invalid")
    if not _is_mapping_list(raw.get("assets")):
        raise ValueError("security evidence assets must be object rows")
    if not _is_mapping_list(raw.get("incidents")):
        raise ValueError("security evidence incidents must be object rows")
    if not _is_json_int(raw.get("run_count")):
        raise ValueError("security evidence run_count is invalid")
    generated_ts_ms = raw.get("generated_ts_ms")
    if not _is_json_int(generated_ts_ms, minimum=1):
        raise ValueError("security evidence generated_ts_ms is invalid")
    last_run_ts_ms = raw.get("last_run_ts_ms")
    if last_run_ts_ms is not None and not _is_json_int(last_run_ts_ms, minimum=1):
        raise ValueError("security evidence last_run_ts_ms is invalid")
    if int(raw["run_count"]) > 0 and last_run_ts_ms is None:
        raise ValueError("security evidence run_count requires last_run_ts_ms")
    if last_run_ts_ms is not None and int(last_run_ts_ms) > int(generated_ts_ms):
        raise ValueError("security evidence timestamp order is invalid")

    for asset in _rows(raw.get("assets")):
        if not _has_public_identity(asset.get("asset_id")):
            raise ValueError("security evidence asset identity is invalid")
        if not _is_json_int(asset.get("observed_ts_ms"), minimum=1):
            raise ValueError("security evidence asset timestamp is invalid")
        if not _is_mapping_list(asset.get("signals", [])):
            raise ValueError("security evidence asset signals are invalid")
    for incident in _rows(raw.get("incidents")):
        if not _has_public_identity(incident.get("incident_id")) or not _has_public_identity(
            incident.get("asset_id")
        ):
            raise ValueError("security evidence incident identity is invalid")
        if str(incident.get("risk") or "").strip().lower() not in _RISK_LEVELS:
            raise ValueError("security evidence incident risk is invalid")
        if not _is_json_int(incident.get("updated_ts_ms"), minimum=1):
            raise ValueError("security evidence incident timestamp is invalid")
        for field in ("signals", "recommended_actions", "action_results"):
            if not _is_mapping_list(incident.get(field, [])):
                raise ValueError("security evidence incident rows are invalid")
    if "provider_statuses" in raw and not _is_mapping_list(raw.get("provider_statuses")):
        raise ValueError("security evidence provider_statuses are invalid")
    return int(generated_ts_ms)


def _validate_event_schema(event: Mapping[str, Any]) -> None:
    if not _has_public_identity(event.get("event_type")):
        raise ValueError("security evidence event identity is invalid")
    if not _is_json_int(event.get("ts_ms"), minimum=1):
        raise ValueError("security evidence event timestamp is invalid")


def _public_signal(raw: Mapping[str, Any]) -> dict[str, Any]:
    # ``detail`` and ``evidence`` may contain command lines, local paths, or
    # credentials collected by endpoint providers.  The public projection only
    # needs the typed signal identity and severity.
    return {
        "signal_id": _public_text(raw.get("signal_id"), default="unknown", limit=128),
        "title": _public_text(raw.get("title"), default="Security signal", limit=200),
        "severity": max(0, min(100, _int(raw.get("severity")))),
        "category": _public_text(raw.get("category"), default="unknown", limit=80),
        "detail": "[redacted]" if raw.get("detail") else "",
        "evidence": {},
        "ts_ms": max(0, _int(raw.get("ts_ms"))),
    }


def _public_asset(raw: Mapping[str, Any]) -> dict[str, Any]:
    source = _public_text(raw.get("source"), default="unknown", limit=80).lower()
    if source not in _PUBLIC_PROVIDER_SOURCES:
        source = "other"
    signals = [_public_signal(item) for item in _rows(raw.get("signals"))]
    addresses = [
        item[:80]
        for item in list(raw.get("ip_addresses") or [])
        if isinstance(item, str) and not _PATH_OR_SECRET.search(item)
    ]
    return {
        "asset_id": _public_text(raw.get("asset_id"), default="unknown", limit=160),
        "hostname": _public_text(raw.get("hostname"), default="unknown", limit=160),
        "os_family": _public_text(raw.get("os_family"), default="unknown", limit=80),
        "ip_addresses": addresses[:32],
        "tags": [],
        "tag_count": len(list(raw.get("tags") or [])),
        "source": source,
        "observed_ts_ms": max(0, _int(raw.get("observed_ts_ms"))),
        "signals": signals,
        "metadata": {},
    }


def _public_action(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_id": _public_text(raw.get("action_id"), default="unknown", limit=128),
        "title": _public_text(raw.get("title"), default="Security action", limit=200),
        "status": _public_text(raw.get("status"), default="unknown", limit=80),
        "reason": "[redacted]" if raw.get("reason") else "",
        "reversible": bool(raw.get("reversible", True)),
        "params": {},
    }


def _public_action_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action_id": _public_text(raw.get("action_id"), default="unknown", limit=128),
        "status": _public_text(raw.get("status"), default="unknown", limit=80),
        "detail": "[redacted]" if raw.get("detail") else "",
        "started_ts_ms": max(0, _int(raw.get("started_ts_ms"))),
        "completed_ts_ms": max(0, _int(raw.get("completed_ts_ms"))),
        "params": {},
    }


def _public_incident(raw: Mapping[str, Any]) -> dict[str, Any]:
    risk = _public_text(raw.get("risk"), default="none", limit=20).lower()
    if risk not in _RISK_LEVELS:
        risk = "none"
    confidence = raw.get("confidence")
    try:
        public_confidence = max(0.0, min(1.0, float(confidence or 0.0)))
    except (TypeError, ValueError, OverflowError):
        public_confidence = 0.0
    return {
        "incident_id": _public_text(raw.get("incident_id"), default="unknown", limit=160),
        "asset_id": _public_text(raw.get("asset_id"), default="unknown", limit=160),
        "risk": risk,
        "verdict": _public_text(raw.get("verdict"), default="unknown", limit=40),
        "confidence": round(public_confidence, 4),
        "title": _public_text(raw.get("title"), default="Security incident", limit=200),
        "summary": "[redacted]" if raw.get("summary") else "",
        "signals": [_public_signal(item) for item in _rows(raw.get("signals"))],
        "recommended_actions": [
            _public_action(item) for item in _rows(raw.get("recommended_actions"))
        ],
        "action_results": [
            _public_action_result(item) for item in _rows(raw.get("action_results"))
        ],
        "created_ts_ms": max(0, _int(raw.get("created_ts_ms"))),
        "updated_ts_ms": max(0, _int(raw.get("updated_ts_ms"))),
        "status": _public_text(raw.get("status"), default="open", limit=40),
    }


def _public_provider_status(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider_id": _public_text(raw.get("provider_id"), default="unknown", limit=80),
        "status": _public_text(raw.get("status"), default="unknown", limit=40),
        "asset_count": max(0, _int(raw.get("asset_count"))),
        "signal_count": max(0, _int(raw.get("signal_count"))),
        "ts_ms": max(0, _int(raw.get("ts_ms"))),
        "detail": "[redacted]" if raw.get("detail") else "",
        "metadata": {},
    }


def _public_event(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_type": _public_text(raw.get("event_type"), default="UNKNOWN", limit=100),
        "asset_count": max(0, _int(raw.get("asset_count"))),
        "signal_count": max(0, _int(raw.get("signal_count"))),
        "incident_count": max(0, _int(raw.get("incident_count"))),
        "action_result_count": max(0, _int(raw.get("action_result_count"))),
        "executed_action_count": max(0, _int(raw.get("executed_action_count"))),
        "ts_ms": max(0, _int(raw.get("ts_ms"))),
    }


def _public_snapshot(
    raw: Mapping[str, Any],
    events: list[Mapping[str, Any]],
    *,
    events_present: bool,
) -> dict[str, Any]:
    assets = [_public_asset(item) for item in _rows(raw.get("assets"))]
    incidents = [_public_incident(item) for item in _rows(raw.get("incidents"))]
    risk_counts = {risk: 0 for risk in _RISK_LEVELS}
    for incident in incidents:
        risk = str(incident["risk"])
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
    mode = _public_text(raw.get("mode"), default="observe", limit=20).lower()
    if mode not in _MODES:
        mode = "observe"
    return {
        "schema_version": "Natsume.security_immune.runtime.v1",
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "asset_count": len(assets),
        "incident_count": len(incidents),
        "risk_counts": risk_counts,
        "assets": assets,
        "incidents": incidents,
        "last_run_ts_ms": max(0, _int(raw.get("last_run_ts_ms"))),
        "run_count": max(0, _int(raw.get("run_count"))),
        "last_error": "SECURITY_IMMUNE_RUN_FAILED" if raw.get("last_error") else None,
        "generated_ts_ms": max(0, _int(raw.get("generated_ts_ms"))),
        "provider_statuses": [
            _public_provider_status(item) for item in _rows(raw.get("provider_statuses"))
        ],
        "recent_events": [_public_event(item) for item in events],
        "evidence_source": {
            "schema_version": "Natsume.security_immune.evidence_source.v1",
            "status": "CURRENT",
            "freshness_ts_ms": max(0, _int(raw.get("generated_ts_ms"))),
            "event_count": len(events),
            "recent_events_status": "CURRENT" if events_present else "NOT_PRESENT",
        },
    }


class SecurityImmuneSnapshotReader:
    """Read a Host-owned ``EvidenceStore`` without importing Host runtime code."""

    __slots__ = (
        "_clock_ms",
        "_events_file",
        "_event_limit",
        "_max_age_ms",
        "_max_future_skew_ms",
        "_state_file",
    )

    def __init__(
        self,
        state_dir: str | Path,
        *,
        event_limit: int = 50,
        max_age_ms: int = DEFAULT_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS,
        max_future_skew_ms: int = SECURITY_IMMUNE_EVIDENCE_MAX_FUTURE_SKEW_MS,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        root = Path(state_dir).expanduser().resolve()
        maximum_age = int(max_age_ms)
        maximum_future_skew = int(max_future_skew_ms)
        if maximum_age <= 0:
            raise ValueError("max_age_ms must be positive")
        if maximum_future_skew < 0:
            raise ValueError("max_future_skew_ms must not be negative")
        self._state_file = root / "state.json"
        self._events_file = root / "events.jsonl"
        self._event_limit = max(1, min(1000, int(event_limit)))
        self._max_age_ms = maximum_age
        self._max_future_skew_ms = maximum_future_skew
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    @staticmethod
    def _read_required_text(path: Path, *, component: str) -> str:
        try:
            if not path.is_file():
                raise SecurityImmuneSnapshotMissingError(component)
            return path.read_text(encoding="utf-8")
        except SecurityImmuneSnapshotSourceError:
            raise
        except UnicodeDecodeError as exc:
            raise SecurityImmuneSnapshotCorruptError(component) from exc
        except OSError as exc:
            raise SecurityImmuneSnapshotUnreadableError(component) from exc

    @staticmethod
    def _read_optional_events_text(path: Path) -> tuple[str, bool]:
        try:
            return path.read_text(encoding="utf-8"), True
        except FileNotFoundError:
            # EvidenceStore.load_recent_events() defines a missing events file
            # as an empty event history.  The durable state snapshot remains a
            # complete, usable source in that case.
            return "", False
        except UnicodeDecodeError as exc:
            raise SecurityImmuneSnapshotCorruptError("events") from exc
        except OSError as exc:
            raise SecurityImmuneSnapshotUnreadableError("events") from exc

    def read(self) -> SecurityImmuneSnapshotRead:
        state_text = self._read_required_text(self._state_file, component="state")
        try:
            raw_state = json.loads(state_text)
        except json.JSONDecodeError as exc:
            raise SecurityImmuneSnapshotCorruptError("state") from exc
        if not isinstance(raw_state, Mapping):
            raise SecurityImmuneSnapshotCorruptError("state")
        try:
            evidence_ts_ms = _validate_state_schema(raw_state)
            now_ms = max(0, int(self._clock_ms()))
        except (TypeError, ValueError, OverflowError) as exc:
            raise SecurityImmuneSnapshotCorruptError("state") from exc
        if evidence_ts_ms > now_ms + self._max_future_skew_ms:
            raise SecurityImmuneSnapshotCorruptError("state")
        if now_ms - evidence_ts_ms > self._max_age_ms:
            raise SecurityImmuneSnapshotStaleError("state")

        events_text, events_present = self._read_optional_events_text(self._events_file)
        events: list[Mapping[str, Any]] = []
        for line in events_text.splitlines()[-self._event_limit :]:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SecurityImmuneSnapshotCorruptError("events") from exc
            if not isinstance(item, Mapping):
                raise SecurityImmuneSnapshotCorruptError("events")
            try:
                _validate_event_schema(item)
            except ValueError as exc:
                raise SecurityImmuneSnapshotCorruptError("events") from exc
            events.append(item)

        try:
            state_file_cursor = int(self._state_file.stat().st_mtime_ns // 1_000_000)
        except FileNotFoundError as exc:
            raise SecurityImmuneSnapshotMissingError("state") from exc
        except OSError as exc:
            raise SecurityImmuneSnapshotUnreadableError("metadata") from exc
        events_file_cursor = 0
        if events_present:
            try:
                events_file_cursor = int(
                    self._events_file.stat().st_mtime_ns // 1_000_000
                )
            except FileNotFoundError:
                # We already have a valid in-memory event snapshot.  A
                # concurrent rotation/removal does not invalidate the required
                # state file and the event timestamps still advance the cursor.
                events_file_cursor = 0
            except OSError as exc:
                raise SecurityImmuneSnapshotUnreadableError("metadata") from exc
        file_cursor = max(state_file_cursor, events_file_cursor)
        try:
            payload = _public_snapshot(
                raw_state,
                events,
                events_present=events_present,
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise SecurityImmuneSnapshotCorruptError("state") from exc
        observed_cursor = max(
            _int(payload.get("generated_ts_ms")),
            _int(payload.get("last_run_ts_ms")),
            max(
                (_int(item.get("observed_ts_ms")) for item in payload["assets"]),
                default=0,
            ),
            max(
                (_int(item.get("updated_ts_ms")) for item in payload["incidents"]),
                default=0,
            ),
            max(
                (_int(item.get("ts_ms")) for item in payload["recent_events"]),
                default=0,
            ),
        )
        return SecurityImmuneSnapshotRead(
            payload=payload,
            source_cursor=max(0, file_cursor, observed_cursor),
            freshness_ts_ms=evidence_ts_ms,
        )

    def __call__(self) -> tuple[dict[str, Any], int]:
        """Projection-callback adapter used by composition wiring."""

        result = self.read()
        return result.payload, result.source_cursor


__all__ = [
    "DEFAULT_SECURITY_IMMUNE_EVIDENCE_MAX_AGE_MS",
    "SECURITY_IMMUNE_EVIDENCE_CORRUPT",
    "SECURITY_IMMUNE_EVIDENCE_MISSING",
    "SECURITY_IMMUNE_EVIDENCE_SOURCE_SCOPE",
    "SECURITY_IMMUNE_EVIDENCE_STALE",
    "SECURITY_IMMUNE_EVIDENCE_UNREADABLE",
    "SecurityImmuneSnapshotCorruptError",
    "SecurityImmuneSnapshotMissingError",
    "SecurityImmuneSnapshotRead",
    "SecurityImmuneSnapshotReader",
    "SecurityImmuneSnapshotSourceError",
    "SecurityImmuneSnapshotStaleError",
    "SecurityImmuneSnapshotUnreadableError",
]
