"""Helpers for compiling degraded runtime reports into Codex self-heal proposals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from host_brain.core.ids import now_ms

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAX_CONTEXT_CHARS = 12_000
_MAX_PRIORITY_EXCERPT_CHARS = 6_500
_UNHEALTHY_STATUSES = {"DEGRADED", "DOWN", "FAILED", "UNAVAILABLE", "REJECTED"}
_HEALTHY_STATUSES = {"UP", "OK", "READY", "HEALTHY", "SKIPPED"}
_EXTERNAL_EDGE_RPC_BLOCK_REASON = "EXTERNAL_EDGE_RPC_OUTAGE"


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(value)


def _is_unhealthy_status(value: Any) -> bool:
    status = str(value or "").strip().upper()
    return bool(status) and status not in _HEALTHY_STATUSES


def _pick_reason_code(report: Mapping[str, Any]) -> str | None:
    reason_code = str(report.get("reason_code") or "").strip()
    if reason_code:
        return reason_code
    summary = report.get("summary")
    if isinstance(summary, Mapping):
        nested = str(summary.get("reason_code") or "").strip()
        if nested:
            return nested
    graph_runtime = report.get("graph_runtime")
    if isinstance(graph_runtime, Mapping):
        nested = str(graph_runtime.get("reason_code") or "").strip()
        if nested:
            return nested
    startup_report = report.get("startup_report")
    if isinstance(startup_report, Mapping):
        nested = str(startup_report.get("reason_code") or "").strip()
        if nested:
            return nested
    return None


def _append_component_candidates(
    rows: Sequence[Any] | None,
    sink: list[str],
) -> None:
    if not isinstance(rows, Sequence):
        return
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = row.get("status")
        available = row.get("available")
        reason_code = str(row.get("reason_code") or "").strip()
        unavailable = bool(reason_code) or available is False or _is_unhealthy_status(status)
        if not unavailable:
            continue
        for key in (
            "service_id",
            "dependency_id",
            "component",
            "scope",
            "source_service_id",
            "target_service_id",
            "scenario_id",
            "node_id",
        ):
            value = str(row.get(key) or "").strip()
            if value:
                sink.append(value)
                break


def _collect_health_components(report: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    _append_component_candidates(report.get("services"), items)
    return list(dict.fromkeys(items))


def _collect_readiness_components(report: Mapping[str, Any]) -> list[str]:
    items: list[str] = []
    startup_report = report.get("startup_report")
    if isinstance(startup_report, Mapping):
        services = startup_report.get("services")
        if isinstance(services, Sequence):
            _append_component_candidates(services, items)
    services = report.get("services")
    if isinstance(services, Mapping):
        rows = services.get("rows")
        if isinstance(rows, Sequence):
            _append_component_candidates(rows, items)
    _append_component_candidates(report.get("dependency_matrix"), items)
    graph_runtime = report.get("graph_runtime")
    if isinstance(graph_runtime, Mapping) and _is_unhealthy_status(graph_runtime.get("status")):
        graph_reason = str(graph_runtime.get("reason_code") or "graph_runtime").strip()
        items.append(graph_reason or "graph_runtime")
    return list(dict.fromkeys(items))


def _suggest_priority_paths(
    *,
    source: str,
    reason_code: str | None,
    components: Sequence[str],
) -> list[str]:
    suggestions: list[str] = []
    normalized_reason = str(reason_code or "").strip().upper()
    normalized_components = [str(item).strip().lower() for item in components if str(item).strip()]

    if source == "readiness":
        suggestions.extend(
            [
                "host_brain/app/startup.py",
                "host_brain/app/internal_codex_routes.py",
                "host_brain/app/codex_self_heal.py",
                "host_brain/app/health_routes.py",
            ]
        )
    elif source == "health":
        suggestions.extend(
            [
                "host_brain/app/health_routes.py",
                "host_brain/app/codex_self_heal.py",
                "host_brain/app/internal_codex_routes.py",
                "host_brain/app/startup.py",
            ]
        )

    if normalized_reason == "SERVICE_UNAVAILABLE_EDGE_RPC" or any(item.startswith("edge.rpc.") for item in normalized_components):
        suggestions.extend(
            [
                "host_brain/channels/edge_rpc_client.py",
                "host_brain/app/startup.py",
            ]
        )
    return list(dict.fromkeys(suggestions))


def _build_priority_patterns(
    *,
    source: str,
    reason_code: str | None,
    components: Sequence[str],
) -> list[str]:
    patterns: list[str] = []
    if reason_code:
        patterns.append(str(reason_code).strip().lower())
    for component in components[:8]:
        value = str(component).strip().lower()
        if not value:
            continue
        patterns.append(value)
        if "." in value:
            patterns.append(value.split(".")[-1])
    if source == "readiness":
        patterns.extend(
            [
                "readiness",
                "probe_unavailable",
                "required_unavailable",
                "graph_runtime",
                "build_live_readiness_report",
                "build_codex_self_heal_proposal_from_readiness_report",
            ]
        )
    patterns.extend(
        [
            "self_heal",
            "codex_self_heal",
            "edge_rpc",
            "collect_dependency_probes",
            "monitor_status",
            "auto_state",
            "_auto_dispatch_self_heal_once",
        ]
    )
    return list(dict.fromkeys(item for item in patterns if item))


def _build_priority_symbol_hints(
    *,
    source: str,
    reason_code: str | None,
    components: Sequence[str],
) -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {
        "host_brain/app/codex_self_heal.py": [
            "build_codex_self_heal_proposal_from_readiness_report",
        ],
        "host_brain/app/health_routes.py": [
            "build_live_readiness_report",
        ],
        "host_brain/app/startup.py": [
            "collect_dependency_probes",
            "build_edge_dependency_probe_statuses",
        ],
        "host_brain/app/internal_codex_routes.py": [
            "_auto_dispatch_self_heal_once",
            "get_self_heal_monitor_status",
        ],
    }
    if source == "readiness":
        hints.setdefault("host_brain/app/health_routes.py", []).extend(["collect_dependency_probes"])
    normalized_reason = str(reason_code or "").strip().upper()
    normalized_components = [str(item).strip().lower() for item in components if str(item).strip()]
    if normalized_reason == "SERVICE_UNAVAILABLE_EDGE_RPC" or any(
        item.startswith("edge.rpc.") for item in normalized_components
    ):
        hints.setdefault("host_brain/app/startup.py", []).extend(
            [
                "_EDGE_RPC_DEPENDENCY_SERVICE_IDS",
                "_build_edge_component_probe_status",
            ]
        )
        hints.setdefault("host_brain/channels/edge_rpc_client.py", []).append("Edge HTTP")
    return {path: list(dict.fromkeys(symbols)) for path, symbols in hints.items() if symbols}


def _extract_symbol_windows(
    *,
    relative_path: str,
    lines: Sequence[str],
    symbols: Sequence[str],
    remaining_chars: int,
) -> tuple[list[str], set[int]]:
    fragments: list[str] = []
    covered_lines: set[int] = set()

    def _find_symbol_start(symbol: str) -> int | None:
        symbol_text = str(symbol or "").strip()
        if not symbol_text:
            return None
        match_prefixes = (
            f"def {symbol_text}(",
            f"async def {symbol_text}(",
            f"class {symbol_text}(",
            f"{symbol_text} =",
        )
        lowered_symbol = symbol_text.lower()
        for index, line in enumerate(lines[:1600]):
            stripped = str(line).lstrip()
            if any(stripped.startswith(prefix) for prefix in match_prefixes):
                return index
            if lowered_symbol and lowered_symbol in str(line).lower():
                return index
        return None

    for symbol in symbols:
        start_index = _find_symbol_start(symbol)
        if start_index is None:
            continue
        window_start = max(0, start_index - 1)
        window_end = min(len(lines), start_index + 18)
        excerpt_lines = [f"--- {relative_path}:{window_start + 1}-{window_end} ---"]
        for line_number in range(window_start, window_end):
            excerpt_lines.append(f"{line_number + 1}: {lines[line_number]}")
        excerpt = "\n".join(excerpt_lines).strip()
        if not excerpt:
            continue
        excerpt = excerpt[:remaining_chars].strip()
        if not excerpt:
            break
        fragments.append(excerpt)
        covered_lines.update(range(window_start, window_end))
        remaining_chars -= len(excerpt) + 2
        if remaining_chars <= 0:
            break
    return fragments, covered_lines


def _build_priority_file_excerpts(
    *,
    source: str,
    reason_code: str | None,
    components: Sequence[str],
    priority_paths: Sequence[str],
) -> str:
    patterns = _build_priority_patterns(
        source=source,
        reason_code=reason_code,
        components=components,
    )
    symbol_hints = _build_priority_symbol_hints(
        source=source,
        reason_code=reason_code,
        components=components,
    )
    if not patterns or not priority_paths:
        return ""

    fragments: list[str] = []
    remaining_chars = _MAX_PRIORITY_EXCERPT_CHARS
    for relative_path in priority_paths:
        file_path = _REPO_ROOT / relative_path
        if not file_path.exists() or not file_path.is_file():
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        symbol_fragments, covered_lines = _extract_symbol_windows(
            relative_path=relative_path,
            lines=lines,
            symbols=symbol_hints.get(relative_path) or (),
            remaining_chars=remaining_chars,
        )
        for fragment in symbol_fragments:
            if not fragment:
                continue
            fragments.append(fragment)
            remaining_chars -= len(fragment) + 2
            if remaining_chars <= 0:
                break
        if remaining_chars <= 0:
            break
        if symbol_fragments:
            continue
        matches = [
            index
            for index, line in enumerate(lines[:1200])
            if index not in covered_lines and any(pattern in str(line).lower() for pattern in patterns)
        ]
        if not matches:
            continue

        excerpt_lines: list[str] = []
        covered: list[tuple[int, int]] = []
        for match_index in matches[:2]:
            start = max(0, match_index - 3)
            end = min(len(lines), match_index + 4)
            if covered and start <= covered[-1][1]:
                start = covered[-1][1]
            covered.append((start, end))
        for start, end in covered:
            excerpt_lines.append(f"--- {relative_path}:{start + 1}-{end} ---")
            for line_number in range(start, end):
                excerpt_lines.append(f"{line_number + 1}: {lines[line_number]}")
        excerpt = "\n".join(excerpt_lines).strip()
        if not excerpt:
            continue
        excerpt = excerpt[:remaining_chars].strip()
        if not excerpt:
            break
        fragments.append(excerpt)
        remaining_chars -= len(excerpt) + 2
        if remaining_chars <= 0:
            break
    return "\n\n".join(fragment for fragment in fragments if fragment).strip()


def _build_context(
    *,
    source: str,
    status_text: str,
    summary_text: str,
    reason_code: str | None,
    components: Sequence[str],
    report: Mapping[str, Any],
) -> str:
    sections = [
        f"Self-heal source: {source}",
        f"Source status: {status_text}",
        f"Problem statement: {summary_text}",
    ]
    if reason_code:
        sections.append(f"Primary reason code: {reason_code}")
    if components:
        sections.append(f"Affected components: {', '.join(components[:8])}")
    sections.append(
        "Investigation hints: Treat unreachable external dependencies as a valid root cause. "
        "Prefer runtime files under host_brain/app, host_brain/integrations, host_brain/channels, and scripts/runtime before docs or tests."
    )
    if source == "readiness":
        sections.append(
            "Runtime note: `collect_dependency_probes()` now lives in `host_brain/app/startup.py`; "
            "readiness and health routes compile proposals from that startup probe layer."
        )
        sections.append(
            "Compatibility note: `host_brain.app.dependencies.collect_dependency_probes` is currently "
            "a re-export shim of `host_brain.app.startup.collect_dependency_probes`, so imports through "
            "`dependencies.py` are compatibility aliases unless the implementation diverges."
        )
    priority_paths = _suggest_priority_paths(
        source=source,
        reason_code=reason_code,
        components=components,
    )
    if priority_paths:
        sections.append(f"Priority files: {', '.join(priority_paths)}")
    priority_excerpts = _build_priority_file_excerpts(
        source=source,
        reason_code=reason_code,
        components=components,
        priority_paths=priority_paths,
    )
    if priority_excerpts:
        sections.append("Priority file excerpts:\n" + priority_excerpts)
    sections.extend(
        [
            "Source report snapshot:",
            _compact_json(report),
        ]
    )
    return "\n\n".join(part for part in sections if part).strip()[:_MAX_CONTEXT_CHARS]


def _build_intent_draft(
    *,
    source: str,
    request_id: str,
    ts_ms: int,
    summary_text: str,
    reason_code: str | None,
    components: Sequence[str],
    report: Mapping[str, Any],
    include_dependency_probes: bool,
) -> dict[str, Any]:
    evidence_refs = [
        f"runtime:{source}",
        f"request:{request_id}",
        "runtime_repo:auto",
    ]
    if reason_code:
        evidence_refs.append(f"reason_code:{reason_code}")
    evidence_refs.extend(f"component:{value}" for value in components[:8])
    summary = report.get("summary")
    summary_status = summary.get("overall_status") if isinstance(summary, Mapping) else None
    args = {
        "task": summary_text,
        "problem_statement": summary_text,
        "mode": "analysis",
        "run_mode": "self_heal",
        "workspace_strategy": "runtime_repo",
        "include_dependency_probes": bool(include_dependency_probes),
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
        "text": summary_text,
        "user_message": summary_text,
        "context": _build_context(
            source=source,
            status_text=str(report.get("readiness_status") or summary_status or "").strip(),
            summary_text=summary_text,
            reason_code=reason_code,
            components=components,
            report=report,
        ),
    }
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent::codex-self-heal::{request_id}",
        "ts_ms": ts_ms,
        "seq": 1,
        "type": "task",
        "action": "codex_codegen",
        "target": "host",
        "route": "skill",
        "target_id": "codex_codegen",
        "route_source": f"runtime:{source}",
        "route_reason": reason_code or f"{source}_degraded",
        "route_confidence": 0.98,
        "reason_code": reason_code,
        "args": args,
    }


def _build_summary_text(
    *,
    source: str,
    status_text: str,
    reason_code: str | None,
    components: Sequence[str],
) -> str:
    source_text = "health report" if source == "health" else "readiness report"
    base = f"Self-heal the runtime because the {source_text} is {status_text}"
    if reason_code:
        base = f"{base} ({reason_code})"
    if components:
        return f"{base}. Prioritize these components: {', '.join(components[:5])}."
    return f"{base}. Inspect the degraded runtime path and apply the smallest safe fix."


def _iter_report_rows(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    services = report.get("services")
    if isinstance(services, Sequence) and not isinstance(services, (str, bytes, bytearray)):
        rows.extend(item for item in services if isinstance(item, Mapping))
    elif isinstance(services, Mapping):
        service_rows = services.get("rows")
        if isinstance(service_rows, Sequence) and not isinstance(service_rows, (str, bytes, bytearray)):
            rows.extend(item for item in service_rows if isinstance(item, Mapping))
    dependency_matrix = report.get("dependency_matrix")
    if isinstance(dependency_matrix, Sequence) and not isinstance(dependency_matrix, (str, bytes, bytearray)):
        rows.extend(item for item in dependency_matrix if isinstance(item, Mapping))
    return rows


def _is_external_edge_rpc_outage(
    *,
    reason_code: str | None,
    components: Sequence[str],
    report: Mapping[str, Any],
) -> bool:
    normalized_reason = str(reason_code or "").strip().upper()
    normalized_components = [str(item).strip().lower() for item in components if str(item).strip()]
    if not normalized_components:
        return False
    actionable_components = [
        item
        for item in normalized_components
        if item != "graph_runtime" and "." in item
    ]
    if not actionable_components:
        return False
    if not all(item.startswith("edge.rpc.") for item in actionable_components):
        return False

    if normalized_reason == "SERVICE_UNAVAILABLE_EDGE_RPC":
        return True

    unreachable_markers = (
        "edge rpc unreachable",
        "connecttimeout",
        "connecterror",
        "connection refused",
        "readtimeout",
    )
    for row in _iter_report_rows(report):
        identifier = str(
            row.get("service_id")
            or row.get("dependency_id")
            or row.get("component_id")
            or row.get("component")
            or ""
        ).strip().lower()
        if identifier and not identifier.startswith("edge.rpc."):
            continue
        detail = str(row.get("detail") or "").strip().lower()
        if any(marker in detail for marker in unreachable_markers):
            return True
    return False


def _build_proposal(
    *,
    source: str,
    status_text: str,
    reason_code: str | None,
    request_id: str,
    components: Sequence[str],
    report: Mapping[str, Any],
    include_dependency_probes: bool,
) -> dict[str, Any]:
    triggered = _is_unhealthy_status(status_text)
    ts_ms = int(report.get("ts_ms") or now_ms())
    severity = "none"
    if triggered:
        severity = "high" if status_text in {"DOWN", "FAILED", "REJECTED"} else "medium"
    dispatch_blocked_reason = None
    dispatch_allowed = bool(triggered)
    recommended_action = None
    if triggered and _is_external_edge_rpc_outage(
        reason_code=reason_code,
        components=components,
        report=report,
    ):
        dispatch_allowed = False
        dispatch_blocked_reason = _EXTERNAL_EDGE_RPC_BLOCK_REASON
        recommended_action = (
            "Treat this as an external Edge RPC outage first. Restore the remote edge endpoint, "
            "or disable edge RPC probing when running host-only drills."
        )
    summary_text = _build_summary_text(
        source=source,
        status_text=status_text,
        reason_code=reason_code,
        components=components,
    )
    proposal = {
        "triggered": triggered,
        "source": source,
        "status": status_text,
        "severity": severity,
        "request_id": request_id,
        "reason_code": reason_code,
        "dispatch_allowed": dispatch_allowed,
        "dispatch_blocked_reason": dispatch_blocked_reason,
        "recommended_action": recommended_action,
        "summary": (
            (
                f"The {source} report is {status_text} because the upstream Edge RPC dependency is unreachable. "
                "Keep this self-heal proposal in observe-only mode for host-side runtime. "
                "Restore the remote edge endpoint, or disable edge RPC probing for host-only runs."
            )
            if dispatch_blocked_reason == _EXTERNAL_EDGE_RPC_BLOCK_REASON
            else (
                summary_text
                if triggered
                else f"The {source} report is healthy; no self-heal proposal is needed."
            )
        ),
        "affected_components": list(components[:8]),
        "evidence_refs": list(
            dict.fromkeys(
                [f"runtime:{source}", f"request:{request_id}"]
                + ([f"reason_code:{reason_code}"] if reason_code else [])
                + [f"component:{value}" for value in components[:8]]
            )
        ),
        "ts_ms": ts_ms,
        "intent_draft": None,
    }
    if triggered:
        proposal["intent_draft"] = _build_intent_draft(
            source=source,
            request_id=request_id,
            ts_ms=ts_ms,
            summary_text=summary_text,
            reason_code=reason_code,
            components=components,
            report=report,
            include_dependency_probes=include_dependency_probes,
        )
    return proposal


def build_codex_self_heal_proposal_from_health_report(
    report: Mapping[str, Any],
    *,
    include_dependency_probes: bool = True,
) -> dict[str, Any]:
    summary = report.get("summary")
    status_text = str(
        (summary.get("overall_status") if isinstance(summary, Mapping) else None)
        or report.get("status")
        or "UNKNOWN"
    ).strip().upper()
    request_id = str(report.get("request_id") or "").strip() or "runtime-health"
    reason_code = _pick_reason_code(report)
    components = _collect_health_components(report)
    return _build_proposal(
        source="health",
        status_text=status_text,
        reason_code=reason_code,
        request_id=request_id,
        components=components,
        report=report,
        include_dependency_probes=include_dependency_probes,
    )


def build_codex_self_heal_proposal_from_readiness_report(
    report: Mapping[str, Any],
    *,
    include_dependency_probes: bool = True,
) -> dict[str, Any]:
    status_text = str(report.get("readiness_status") or report.get("status") or "UNKNOWN").strip().upper()
    request_id = str(report.get("request_id") or "").strip() or "runtime-readiness"
    reason_code = _pick_reason_code(report)
    components = _collect_readiness_components(report)
    return _build_proposal(
        source="readiness",
        status_text=status_text,
        reason_code=reason_code,
        request_id=request_id,
        components=components,
        report=report,
        include_dependency_probes=include_dependency_probes,
    )


__all__ = [
    "build_codex_self_heal_proposal_from_health_report",
    "build_codex_self_heal_proposal_from_readiness_report",
]
