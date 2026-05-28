#!/usr/bin/env python
"""Audit LifeMemory episode/evidence coverage from the runtime SQLite ledger."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
GATEWAY_SESSION_PREFIXES = ("qq_", "wechat_", "wx_", "wecom_", "feishu_")
USER_FACING_ACTIONS = {
    "qq_reply_sent",
    "wechat_reply_sent",
    "gateway_reply_sent",
    "channel_message_sent",
    "lightnovel_recommendation_delivery",
}
SYSTEM_ACTION_PREFIXES = (
    "emotion_decay",
    "silence",
    "inner_world_numeric",
    "body_sense",
    "resource",
    "health",
)


ENTRYPOINT_MATRIX = [
    {
        "entrypoint": "surface_chat",
        "code_path": "host_brain/app/agent_runtime_shared.py -> persist_life_chat_episode",
        "episode_kind": "chat_episode",
        "required_evidence": ["user_text", "assistant_reply"],
        "identity": "person_id + session_id required",
    },
    {
        "entrypoint": "gateway_input_chat",
        "code_path": "host_brain/app/input_routes.py -> ChatRequest -> persist_life_chat_episode",
        "episode_kind": "chat_episode",
        "required_evidence": ["user_text", "assistant_reply", "gateway_message"],
        "identity": "person_id + gateway session required",
    },
    {
        "entrypoint": "gateway_reply_action",
        "code_path": "host_brain/channels/edge_rpc_client.py -> persist_life_action_event_memory",
        "episode_kind": "action_episode",
        "required_evidence": ["tool_result + gateway://message anchor"],
        "identity": "person_id required for gateway context",
    },
    {
        "entrypoint": "codex_lightnovel_delivery",
        "code_path": "host_brain/codex_runtime/production_executor.py -> persist_life_action_event_memory",
        "episode_kind": "action_episode",
        "required_evidence": ["tool_result + gateway card anchors"],
        "identity": "person_id required after gateway backfill",
    },
    {
        "entrypoint": "scheduler_background",
        "code_path": "host_brain/scheduler/scheduler.py -> persist_background_event_memory",
        "episode_kind": "background_episode",
        "required_evidence": ["scheduler_event"],
        "identity": "optional unless user-scoped task",
    },
    {
        "entrypoint": "inner_world",
        "code_path": "host_brain/app/observability_persist.py -> persist_background_event_memory",
        "episode_kind": "inner_episode",
        "required_evidence": ["inner_thought"],
        "identity": "person_id required for user-linked thought",
    },
    {
        "entrypoint": "perception_input",
        "code_path": "LifeMemoryStore.write_background_episode(event_source=vision/audio/asr/perception)",
        "episode_kind": "perception_episode",
        "required_evidence": ["vision_observation or audio_asr"],
        "identity": "person/session optional unless tied to a user turn",
    },
    {
        "entrypoint": "generic_tool_action",
        "code_path": "LifeMemoryStore.write_background_episode(event_source=skill/mcp/sandbox/tool/browser)",
        "episode_kind": "action_episode",
        "required_evidence": ["tool_result"],
        "identity": "optional unless user-scoped action",
    },
    {
        "entrypoint": "audit_anchor",
        "code_path": "host_brain/audit/sqlite_ledger.py -> _log_life_audit_anchor",
        "episode_kind": "background/action/perception anchor",
        "required_evidence": ["source-specific audit evidence"],
        "identity": "best-effort; audit-only anchors may omit person/session",
    },
]


@dataclass(frozen=True)
class EpisodeIssue:
    issue_kind: str
    severity: str
    episode_id: str
    episode_kind: str
    action: str
    person_id: str
    session_id: str
    detail: str


@dataclass(frozen=True)
class CollectionIssue:
    issue_kind: str
    severity: str
    collection_id: str
    collection_kind: str
    domain: str
    scope_key: str
    detail: str


@dataclass(frozen=True)
class EntryPointIssue:
    issue_kind: str
    severity: str
    entrypoint: str
    detail: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit LifeMemory coverage.")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--out", default="")
    parser.add_argument("--fail-on", choices=("none", "critical", "warn"), default="none")
    parser.add_argument("--sample-limit", type=int, default=25)
    parser.add_argument("--include-synthetic", action="store_true", help="Include e2e/probe episodes in issue counts.")
    parser.add_argument(
        "--require-entrypoint",
        action="append",
        default=[],
        help="Require an inferred entrypoint to be present; may be passed multiple times.",
    )
    return parser.parse_args()


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _resolve_db_path(explicit: str) -> Path:
    raw = str(explicit or os.environ.get("Natsume_AUDIT_DB") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return _resolve_path(raw)


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _is_gateway_context(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    session_id = _clean(row.get("session_id"))
    payload = _json_loads(metadata.get("payload"))
    conversation_id = _clean(
        metadata.get("conversation_id")
        or metadata.get("reply_target")
        or payload.get("conversation_id")
        or payload.get("reply_target")
    )
    platform = _clean(metadata.get("platform") or metadata.get("reply_channel") or payload.get("platform") or payload.get("reply_channel")).lower()
    source = _clean(payload.get("source") or payload.get("input_source")).lower()
    if platform in {"qq", "wechat", "gateway", "wecom", "feishu"}:
        return True
    if source == "gateway":
        return True
    return session_id.startswith(GATEWAY_SESSION_PREFIXES) or conversation_id.startswith(GATEWAY_SESSION_PREFIXES)


def _is_system_like(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    action = _clean(row.get("action")).lower()
    if any(action.startswith(prefix) for prefix in SYSTEM_ACTION_PREFIXES):
        return True
    if bool(metadata.get("anchor_only")):
        return True
    source = _clean(metadata.get("event_source") or metadata.get("audit_anchor_source")).lower()
    return source in {"avatar", "voice", "scheduler"} and action not in USER_FACING_ACTIONS


def _requires_person(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    kind = _clean(row.get("episode_kind"))
    action = _clean(row.get("action")).lower()
    if kind == "chat_episode":
        return True
    if _is_gateway_context(row, metadata):
        return True
    if action in USER_FACING_ACTIONS:
        return True
    if kind == "inner_episode" and not _is_system_like(row, metadata):
        return True
    return False


def _requires_session(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    kind = _clean(row.get("episode_kind"))
    action = _clean(row.get("action")).lower()
    if kind == "chat_episode":
        return True
    if _is_gateway_context(row, metadata) or action in USER_FACING_ACTIONS:
        return True
    return False


def _metadata_evidence_refs(metadata: Mapping[str, Any]) -> list[str]:
    refs = metadata.get("evidence_refs")
    if isinstance(refs, list):
        return [_clean(item) for item in refs if _clean(item)]
    return []


def _has_gateway_evidence(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    refs = _metadata_evidence_refs(metadata)
    source_refs = str(row.get("evidence_source_refs") or "")
    return any(ref.startswith("gateway://message/") for ref in refs) or "gateway://message/" in source_refs


def _is_synthetic(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    person_id = _clean(row.get("person_id")).lower()
    session_id = _clean(row.get("session_id")).lower()
    if bool(metadata.get("e2e_probe") or metadata.get("synthetic_probe") or metadata.get("test_probe")):
        return True
    markers = ("surface-memory-recall-e2e", "pytest", "test-", "probe")
    return any(marker in person_id or marker in session_id for marker in markers)


def _summarize_counter(counter: Counter[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {"episode_kind": kind, "action": action, "count": count}
        for (kind, action), count in sorted(counter.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    ]


def _infer_entrypoints(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> list[str]:
    kind = _clean(row.get("episode_kind"))
    action = _clean(row.get("action")).lower()
    source = _clean(metadata.get("event_source") or metadata.get("audit_anchor_source")).lower()
    evidence_source_refs = str(row.get("evidence_source_refs") or "")
    refs = _metadata_evidence_refs(metadata)
    entrypoints: list[str] = []

    def add(entrypoint: str) -> None:
        if entrypoint and entrypoint not in entrypoints:
            entrypoints.append(entrypoint)

    if kind == "chat_episode":
        add("surface_chat")
        if _is_gateway_context(row, metadata) or any(ref.startswith("gateway://message/") for ref in refs) or "gateway://message/" in evidence_source_refs:
            add("gateway_input_chat")
    if kind == "action_episode" and (_is_gateway_context(row, metadata) or action in USER_FACING_ACTIONS):
        if any(ref.startswith("gateway://message/") for ref in refs) or "gateway://message/" in evidence_source_refs:
            add("gateway_reply_action")
    if action == "lightnovel_recommendation_delivery":
        add("codex_lightnovel_delivery")
    if source == "scheduler" or "scheduler_event" in str(row.get("evidence_kinds") or ""):
        add("scheduler_background")
    if kind == "inner_episode" or source in {"inner_world", "inner_stream", "inner_activity"}:
        add("inner_world")
    if kind == "perception_episode" or source in {"vision", "screen_ocr", "camera", "camera_recognition", "perception", "voice", "audio", "asr"}:
        add("perception_input")
    if kind == "action_episode" and (
        source in {"skill", "mcp", "sandbox", "tool", "browser"}
        or action in {"skill", "mcp", "sandbox", "tool", "browser"}
        or any(prefix in evidence_source_refs for prefix in ("skill://exec/", "mcp://exec/", "sandbox://exec/"))
    ):
        add("generic_tool_action")
    if bool(metadata.get("anchor_only")):
        add("audit_anchor")
    return entrypoints or ["unclassified"]


def _fetch_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            ep.episode_id,
            ep.episode_kind,
            ep.person_id,
            ep.session_id,
            ep.action,
            ep.result,
            ep.truth_state,
            ep.retention_state,
            ep.status,
            ep.created_ts_ms,
            ep.last_touched_ts_ms,
            ep.summary,
            ep.metadata,
            COUNT(ev.evidence_id) AS evidence_count,
            GROUP_CONCAT(DISTINCT ev.evidence_kind) AS evidence_kinds,
            GROUP_CONCAT(DISTINCT ev.source_ref) AS evidence_source_refs
        FROM life_episode ep
        LEFT JOIN life_evidence ev ON ev.episode_id = ep.episode_id
        GROUP BY ep.episode_id
        ORDER BY ep.created_ts_ms DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_collection_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "life_memory_collection"):
        return []
    rows = conn.execute(
        """
        SELECT
            c.collection_id,
            c.collection_kind,
            c.domain,
            c.scope_key,
            c.person_id,
            c.session_id,
            c.title,
            c.summary,
            c.item_count,
            c.retention_state,
            c.memory_strength,
            c.created_ts_ms,
            c.updated_ts_ms,
            c.metadata,
            COUNT(i.collection_item_id) AS actual_item_count,
            SUM(CASE WHEN i.collection_item_id IS NOT NULL AND ep.episode_id IS NULL THEN 1 ELSE 0 END) AS missing_episode_item_count
        FROM life_memory_collection c
        LEFT JOIN life_memory_collection_item i ON i.collection_id = c.collection_id
        LEFT JOIN life_episode ep ON ep.episode_id = i.episode_id
        GROUP BY c.collection_id
        ORDER BY c.updated_ts_ms DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (str(table_name),),
    ).fetchone()
    return row is not None


def build_coverage_report(
    conn: sqlite3.Connection,
    *,
    sample_limit: int = 25,
    include_synthetic: bool = False,
    required_entrypoints: Sequence[str] | None = None,
) -> dict[str, Any]:
    rows = _fetch_rows(conn)
    collection_rows = _fetch_collection_rows(conn)
    issues: list[EpisodeIssue] = []
    collection_issues: list[CollectionIssue] = []
    entrypoint_issues: list[EntryPointIssue] = []
    by_kind_action: Counter[tuple[str, str]] = Counter()
    collection_by_kind_domain: Counter[tuple[str, str]] = Counter()
    entrypoint_counter: Counter[str] = Counter()
    evidence_kind_counter: Counter[str] = Counter()
    required_identity_counts = {"person": 0, "session": 0}
    gateway_count = 0
    anchor_only_count = 0
    synthetic_count = 0
    production_count = 0

    for row in rows:
        metadata = _json_loads(row.get("metadata"))
        kind = _clean(row.get("episode_kind"))
        action = _clean(row.get("action"))
        episode_id = _clean(row.get("episode_id"))
        person_id = _clean(row.get("person_id"))
        session_id = _clean(row.get("session_id"))
        by_kind_action[(kind, action)] += 1
        if _is_gateway_context(row, metadata):
            gateway_count += 1
        if bool(metadata.get("anchor_only")):
            anchor_only_count += 1
        for evidence_kind in str(row.get("evidence_kinds") or "").split(","):
            evidence_kind = evidence_kind.strip()
            if evidence_kind:
                evidence_kind_counter[evidence_kind] += 1

        synthetic = _is_synthetic(row, metadata)
        if synthetic:
            synthetic_count += 1
        else:
            production_count += 1
        if synthetic and not include_synthetic:
            continue

        for entrypoint in _infer_entrypoints(row, metadata):
            entrypoint_counter[entrypoint] += 1

        if not _clean(row.get("truth_state")):
            issues.append(EpisodeIssue("missing_truth_state", "critical", episode_id, kind, action, person_id, session_id, "truth_state is required"))
        if not _clean(row.get("retention_state")):
            issues.append(EpisodeIssue("missing_retention_state", "critical", episode_id, kind, action, person_id, session_id, "retention_state is required"))
        if int(row.get("evidence_count") or 0) <= 0:
            issues.append(EpisodeIssue("missing_evidence", "critical", episode_id, kind, action, person_id, session_id, "episode has no life_evidence rows"))
        if _requires_person(row, metadata):
            required_identity_counts["person"] += 1
            if not person_id:
                issues.append(EpisodeIssue("missing_required_person_id", "critical", episode_id, kind, action, person_id, session_id, "user-facing episode lacks person_id"))
        if _requires_session(row, metadata):
            required_identity_counts["session"] += 1
            if not session_id:
                issues.append(EpisodeIssue("missing_required_session_id", "critical", episode_id, kind, action, person_id, session_id, "user-facing episode lacks session_id"))
        if _is_gateway_context(row, metadata) and not _has_gateway_evidence(row, metadata):
            issues.append(EpisodeIssue("missing_gateway_evidence_anchor", "warn", episode_id, kind, action, person_id, session_id, "gateway context lacks gateway://message evidence anchor"))
        if kind == "perception_episode" and int(row.get("evidence_count") or 0) <= 0:
            issues.append(EpisodeIssue("perception_without_evidence", "critical", episode_id, kind, action, person_id, session_id, "perception episode must bind perception/audio evidence"))

    collection_item_total = 0
    for row in collection_rows:
        metadata = _json_loads(row.get("metadata"))
        collection_id = _clean(row.get("collection_id"))
        collection_kind = _clean(row.get("collection_kind"))
        domain = _clean(row.get("domain"))
        scope_key = _clean(row.get("scope_key"))
        declared_count = int(row.get("item_count") or 0)
        actual_count = int(row.get("actual_item_count") or 0)
        missing_episode_count = int(row.get("missing_episode_item_count") or 0)
        collection_item_total += actual_count
        collection_by_kind_domain[(collection_kind, domain)] += 1
        synthetic = bool(metadata.get("e2e_probe") or metadata.get("test_probe") or metadata.get("synthetic_probe"))
        if synthetic and not include_synthetic:
            continue
        if _clean(row.get("retention_state")).upper() == "GONE":
            continue
        if not collection_kind:
            collection_issues.append(CollectionIssue("missing_collection_kind", "critical", collection_id, collection_kind, domain, scope_key, "collection_kind is required"))
        if not domain:
            collection_issues.append(CollectionIssue("missing_collection_domain", "warn", collection_id, collection_kind, domain, scope_key, "domain helps route collection recall"))
        if not scope_key:
            collection_issues.append(CollectionIssue("missing_collection_scope", "critical", collection_id, collection_kind, domain, scope_key, "scope_key is required"))
        if actual_count <= 0:
            collection_issues.append(CollectionIssue("empty_collection", "warn", collection_id, collection_kind, domain, scope_key, "collection has no item anchors"))
        if declared_count != actual_count:
            collection_issues.append(
                CollectionIssue(
                    "collection_item_count_mismatch",
                    "warn",
                    collection_id,
                    collection_kind,
                    domain,
                    scope_key,
                    f"declared item_count={declared_count}, actual items={actual_count}",
                )
            )
        if missing_episode_count > 0:
            collection_issues.append(
                CollectionIssue(
                    "collection_item_missing_episode",
                    "critical",
                    collection_id,
                    collection_kind,
                    domain,
                    scope_key,
                    f"{missing_episode_count} collection items point at missing episodes",
                )
            )

    issue_counts = Counter(issue.issue_kind for issue in issues)
    collection_issue_counts = Counter(issue.issue_kind for issue in collection_issues)
    required_entrypoint_list = list(dict.fromkeys(_clean(item) for item in list(required_entrypoints or []) if _clean(item)))
    for entrypoint in required_entrypoint_list:
        if entrypoint_counter.get(entrypoint, 0) <= 0:
            entrypoint_issues.append(
                EntryPointIssue(
                    "missing_required_entrypoint",
                    "critical",
                    entrypoint,
                    f"required entrypoint {entrypoint!r} has no inferred life_episode coverage",
                )
            )
    entrypoint_issue_counts = Counter(issue.issue_kind for issue in entrypoint_issues)
    severity_counts = Counter(issue.severity for issue in issues)
    collection_severity_counts = Counter(issue.severity for issue in collection_issues)
    entrypoint_severity_counts = Counter(issue.severity for issue in entrypoint_issues)
    report = {
        "schema": "Natsume.life_memory_coverage.v1",
        "generated_ts_ms": int(time.time() * 1000),
        "summary": {
            "episode_count": len(rows),
            "production_episode_count": production_count,
            "synthetic_episode_count": synthetic_count,
            "synthetic_included": bool(include_synthetic),
            "gateway_episode_count": gateway_count,
            "anchor_only_count": anchor_only_count,
            "required_person_episode_count": required_identity_counts["person"],
            "required_session_episode_count": required_identity_counts["session"],
            "issue_count": len(issues),
            "critical_issue_count": severity_counts.get("critical", 0),
            "warn_issue_count": severity_counts.get("warn", 0),
            "collection_count": len(collection_rows),
            "collection_item_count": collection_item_total,
            "collection_issue_count": len(collection_issues),
            "collection_critical_issue_count": collection_severity_counts.get("critical", 0),
            "collection_warn_issue_count": collection_severity_counts.get("warn", 0),
            "observed_entrypoint_count": len(entrypoint_counter),
            "required_entrypoint_count": len(required_entrypoint_list),
            "entrypoint_issue_count": len(entrypoint_issues),
            "entrypoint_critical_issue_count": entrypoint_severity_counts.get("critical", 0),
            "entrypoint_warn_issue_count": entrypoint_severity_counts.get("warn", 0),
        },
        "entrypoint_matrix": ENTRYPOINT_MATRIX,
        "entrypoint_counts": [
            {"entrypoint": entrypoint, "count": count}
            for entrypoint, count in sorted(entrypoint_counter.items(), key=lambda item: (-item[1], item[0]))
        ],
        "required_entrypoints": required_entrypoint_list,
        "by_kind_action": _summarize_counter(by_kind_action),
        "collection_by_kind_domain": [
            {"collection_kind": kind, "domain": domain, "count": count}
            for (kind, domain), count in sorted(collection_by_kind_domain.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
        ],
        "evidence_kinds": [{"evidence_kind": key, "count": count} for key, count in evidence_kind_counter.most_common()],
        "issue_counts": dict(sorted(issue_counts.items())),
        "collection_issue_counts": dict(sorted(collection_issue_counts.items())),
        "entrypoint_issue_counts": dict(sorted(entrypoint_issue_counts.items())),
        "issues": [asdict(issue) for issue in issues[: max(1, int(sample_limit))]],
        "collection_issues": [asdict(issue) for issue in collection_issues[: max(1, int(sample_limit))]],
        "entrypoint_issues": [asdict(issue) for issue in entrypoint_issues[: max(1, int(sample_limit))]],
    }
    return report


def _render_markdown(report: Mapping[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    lines = [
        "# Life Memory Coverage",
        "",
        f"- Episodes: {summary.get('episode_count', 0)}",
        f"- Production episodes: {summary.get('production_episode_count', 0)}",
        f"- Synthetic episodes: {summary.get('synthetic_episode_count', 0)} "
        f"(included={str(summary.get('synthetic_included', False)).lower()})",
        f"- Gateway episodes: {summary.get('gateway_episode_count', 0)}",
        f"- Required person episodes: {summary.get('required_person_episode_count', 0)}",
        f"- Required session episodes: {summary.get('required_session_episode_count', 0)}",
        f"- Issues: {summary.get('issue_count', 0)} "
        f"(critical={summary.get('critical_issue_count', 0)}, warn={summary.get('warn_issue_count', 0)})",
        f"- Collections: {summary.get('collection_count', 0)} "
        f"(items={summary.get('collection_item_count', 0)}, issues={summary.get('collection_issue_count', 0)})",
        f"- Observed entrypoints: {summary.get('observed_entrypoint_count', 0)} "
        f"(required={summary.get('required_entrypoint_count', 0)}, issues={summary.get('entrypoint_issue_count', 0)})",
        "",
        "## Entrypoints",
        "",
        "| Entrypoint | Episode | Evidence | Identity |",
        "|---|---|---|---|",
    ]
    for item in report.get("entrypoint_matrix") or []:
        lines.append(
            "| {entrypoint} | {episode_kind} | {evidence} | {identity} |".format(
                entrypoint=item.get("entrypoint"),
                episode_kind=item.get("episode_kind"),
                evidence=", ".join(item.get("required_evidence") or []),
                identity=item.get("identity"),
            )
        )
    lines.extend(["", "## Observed Entrypoints", ""])
    for item in report.get("entrypoint_counts") or []:
        lines.append(f"- {item.get('entrypoint')}: {item.get('count')}")
    lines.extend(["", "## Issue Counts", ""])
    for key, count in dict(report.get("issue_counts") or {}).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Collection Issue Counts", ""])
    for key, count in dict(report.get("collection_issue_counts") or {}).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## EntryPoint Issue Counts", ""])
    for key, count in dict(report.get("entrypoint_issue_counts") or {}).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Issue Samples", ""])
    for issue in report.get("issues") or []:
        lines.append(
            f"- [{issue.get('severity')}] {issue.get('issue_kind')} "
            f"{issue.get('episode_id')} {issue.get('episode_kind')}/{issue.get('action')}: {issue.get('detail')}"
        )
    lines.extend(["", "## Collection Issue Samples", ""])
    for issue in report.get("collection_issues") or []:
        lines.append(
            f"- [{issue.get('severity')}] {issue.get('issue_kind')} "
            f"{issue.get('collection_id')} {issue.get('collection_kind')}/{issue.get('domain')}/{issue.get('scope_key')}: {issue.get('detail')}"
        )
    lines.extend(["", "## EntryPoint Issue Samples", ""])
    for issue in report.get("entrypoint_issues") or []:
        lines.append(
            f"- [{issue.get('severity')}] {issue.get('issue_kind')} "
            f"{issue.get('entrypoint')}: {issue.get('detail')}"
        )
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    args = _parse_args()
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    db_path = _resolve_db_path(str(args.db_path or ""))
    if not db_path.exists():
        print(json.dumps({"status": "FAILED", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}, ensure_ascii=False))
        return 2
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        report = build_coverage_report(
            conn,
            sample_limit=max(1, int(args.sample_limit or 25)),
            include_synthetic=bool(args.include_synthetic),
            required_entrypoints=list(args.require_entrypoint or []),
        )
    finally:
        conn.close()
    output = _render_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out_path = _resolve_path(str(args.out))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
    else:
        print(output)
    critical = int((report.get("summary") or {}).get("critical_issue_count") or 0)
    critical += int((report.get("summary") or {}).get("collection_critical_issue_count") or 0)
    critical += int((report.get("summary") or {}).get("entrypoint_critical_issue_count") or 0)
    total = int((report.get("summary") or {}).get("issue_count") or 0)
    total += int((report.get("summary") or {}).get("collection_issue_count") or 0)
    total += int((report.get("summary") or {}).get("entrypoint_issue_count") or 0)
    if args.fail_on == "critical" and critical > 0:
        return 3
    if args.fail_on == "warn" and total > 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
