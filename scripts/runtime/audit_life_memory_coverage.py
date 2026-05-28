#!/usr/bin/env python
"""Audit LifeMemory episode/evidence coverage from the runtime SQLite ledger."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
import json
import os
import re
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
DEFAULT_REQUIRED_ENTRYPOINTS = tuple(
    str(item.get("entrypoint") or "").strip()
    for item in ENTRYPOINT_MATRIX
    if str(item.get("entrypoint") or "").strip()
)


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
class SemanticIssue:
    issue_kind: str
    severity: str
    semantic_id: str
    source_memory_id: str
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
    parser.add_argument(
        "--no-default-entrypoints",
        action="store_true",
        help="Do not require the standard LifeMemory entrypoint matrix in CLI audits.",
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


def _json_loads_any(value: Any, *, fallback: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def _json_loads(value: Any) -> dict[str, Any]:
    parsed = _json_loads_any(value, fallback={})
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    parsed = _json_loads_any(value, fallback=[])
    if isinstance(parsed, list):
        return list(parsed)
    return []


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


def _requires_gateway_evidence_anchor(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> bool:
    kind = _clean(row.get("episode_kind"))
    action = _clean(row.get("action")).lower()
    source = _clean(metadata.get("event_source") or metadata.get("audit_anchor_source")).lower()
    if kind == "chat_episode" and _is_gateway_context(row, metadata):
        return True
    if action in USER_FACING_ACTIONS:
        return True
    if action == "lightnovel_recommendation_delivery":
        return True
    return source in {"gateway", "channel"} and _is_gateway_context(row, metadata)


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
    action = _clean(row.get("action") or metadata.get("action_kind") or metadata.get("task_id")).lower()
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
    if kind in {"action_episode", "background_episode"} and (_is_gateway_context(row, metadata) or action in USER_FACING_ACTIONS):
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
    if kind in {"action_episode", "background_episode"} and (
        source in {"skill", "mcp", "sandbox", "tool", "browser", "surface_memory_recall", "codex_production"}
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


def _fetch_semantic_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "semantic_memory"):
        return []
    rows = conn.execute(
        """
        SELECT
            semantic_id,
            summary,
            source_memory_ids,
            source_hash,
            distill_strategy,
            confidence,
            tags,
            ts_ms,
            operator_node_id,
            deleted_at
        FROM semantic_memory
        WHERE deleted_at IS NULL
        ORDER BY ts_ms DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (str(table_name),),
    ).fetchone()
    return row is not None


def _semantic_summary_has_noise(summary: Any) -> bool:
    compact = str(summary or "").lower().replace(" ", "")
    if not compact:
        return True
    return any(
        token in compact
        for token in (
            "api_key",
            "apikey",
            "authorization:",
            "cookie",
            "token=",
            "heartbeat",
            "health_check",
            "emotion_decay_tick",
            "inner_world_numeric_tick",
            "silence_tick",
            "metrics test",
            "当前回合摘要：用户提到",
            "发送了text消息",
            "发送了image消息",
            "latency_ms",
        )
    )


def _summary_declared_item_count(summary: Any) -> int | None:
    match = re.search(r"记录\s*(\d+)\s*个条目", str(summary or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _semantic_source_ids(row: Mapping[str, Any]) -> list[str]:
    return [_clean(item) for item in _json_list(row.get("source_memory_ids")) if _clean(item)]


def _audit_semantic_rows(
    *,
    semantic_rows: Sequence[Mapping[str, Any]],
    episode_rows: Sequence[Mapping[str, Any]],
    collection_rows: Sequence[Mapping[str, Any]],
    include_synthetic: bool,
) -> tuple[list[SemanticIssue], Counter[str]]:
    episode_by_id = {_clean(row.get("episode_id")): row for row in episode_rows if _clean(row.get("episode_id"))}
    collection_by_id = {_clean(row.get("collection_id")): row for row in collection_rows if _clean(row.get("collection_id"))}
    issues: list[SemanticIssue] = []
    source_class_counts: Counter[str] = Counter()
    for row in semantic_rows:
        semantic_id = _clean(row.get("semantic_id"))
        source_ids = _semantic_source_ids(row)
        if not source_ids:
            issues.append(SemanticIssue("semantic_missing_sources", "critical", semantic_id, "", "semantic memory has no source_memory_ids"))
        if _semantic_summary_has_noise(row.get("summary")):
            issues.append(SemanticIssue("semantic_summary_noise", "warn", semantic_id, "", "semantic summary looks like raw logs, secrets, or turn transcript noise"))
        life_source_seen = False
        for source_id in source_ids:
            if source_id.startswith("life_episode::"):
                source_class_counts["life_episode"] += 1
                life_source_seen = True
                episode_id = source_id.split("::", 1)[1]
                episode = episode_by_id.get(episode_id)
                if episode is None:
                    issues.append(SemanticIssue("semantic_missing_life_episode_source", "critical", semantic_id, source_id, "life_episode source is missing"))
                    continue
                metadata = _json_loads(episode.get("metadata"))
                if _is_synthetic(episode, metadata) and not include_synthetic:
                    continue
                truth_state = _clean(episode.get("truth_state")).upper()
                status = _clean(episode.get("status")).upper()
                retention_state = _clean(episode.get("retention_state")).upper()
                if truth_state != "VERIFIED":
                    issues.append(SemanticIssue("semantic_untrusted_life_episode_source", "critical", semantic_id, source_id, f"source truth_state={truth_state or '<empty>'}"))
                if status != "ACTIVE":
                    issues.append(SemanticIssue("semantic_inactive_life_episode_source", "critical", semantic_id, source_id, f"source status={status or '<empty>'}"))
                if retention_state == "GONE":
                    issues.append(SemanticIssue("semantic_gone_life_episode_source", "warn", semantic_id, source_id, "semantic source episode is no longer recallable"))
            elif source_id.startswith("life_memory_collection::"):
                source_class_counts["life_memory_collection"] += 1
                life_source_seen = True
                collection_id = source_id.split("::", 1)[1]
                collection = collection_by_id.get(collection_id)
                if collection is None:
                    issues.append(SemanticIssue("semantic_missing_collection_source", "critical", semantic_id, source_id, "life_memory_collection source is missing"))
                    continue
                retention_state = _clean(collection.get("retention_state")).upper()
                if retention_state == "GONE":
                    issues.append(SemanticIssue("semantic_gone_collection_source", "warn", semantic_id, source_id, "semantic source collection is GONE"))
                if int(collection.get("actual_item_count") or collection.get("item_count") or 0) <= 0:
                    issues.append(SemanticIssue("semantic_empty_collection_source", "warn", semantic_id, source_id, "semantic source collection has no items"))
            else:
                source_class_counts["legacy_or_external"] += 1
        if source_ids and not life_source_seen:
            issues.append(SemanticIssue("semantic_legacy_non_life_source", "warn", semantic_id, "", "active semantic memory is not sourced from LifeMemory SSOT"))
    return issues, source_class_counts


def build_coverage_report(
    conn: sqlite3.Connection,
    *,
    sample_limit: int = 25,
    include_synthetic: bool = False,
    required_entrypoints: Sequence[str] | None = None,
) -> dict[str, Any]:
    rows = _fetch_rows(conn)
    collection_rows = _fetch_collection_rows(conn)
    semantic_rows = _fetch_semantic_rows(conn)
    issues: list[EpisodeIssue] = []
    collection_issues: list[CollectionIssue] = []
    semantic_issues: list[SemanticIssue] = []
    entrypoint_issues: list[EntryPointIssue] = []
    by_kind_action: Counter[tuple[str, str]] = Counter()
    collection_by_kind_domain: Counter[tuple[str, str]] = Counter()
    entrypoint_counter: Counter[str] = Counter()
    evidence_kind_counter: Counter[str] = Counter()
    memory_class_counter: Counter[str] = Counter()
    recall_grain_counter: Counter[str] = Counter()
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
        policy = metadata.get("memory_policy") if isinstance(metadata.get("memory_policy"), Mapping) else {}
        memory_class = _clean(metadata.get("memory_class") or (policy or {}).get("memory_class") or "legacy_unclassified")
        recall_grain = _clean(metadata.get("recall_grain") or (policy or {}).get("recall_grain") or "legacy_unclassified")
        memory_class_counter[memory_class] += 1
        recall_grain_counter[recall_grain] += 1
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
        if _requires_gateway_evidence_anchor(row, metadata) and not _has_gateway_evidence(row, metadata):
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
        summary_count = _summary_declared_item_count(row.get("summary"))
        if summary_count is not None and summary_count != actual_count:
            collection_issues.append(
                CollectionIssue(
                    "collection_summary_count_mismatch",
                    "warn",
                    collection_id,
                    collection_kind,
                    domain,
                    scope_key,
                    f"summary count={summary_count}, actual items={actual_count}",
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

    semantic_issues, semantic_source_class_counts = _audit_semantic_rows(
        semantic_rows=semantic_rows,
        episode_rows=rows,
        collection_rows=collection_rows,
        include_synthetic=include_synthetic,
    )
    issue_counts = Counter(issue.issue_kind for issue in issues)
    collection_issue_counts = Counter(issue.issue_kind for issue in collection_issues)
    semantic_issue_counts = Counter(issue.issue_kind for issue in semantic_issues)
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
    semantic_severity_counts = Counter(issue.severity for issue in semantic_issues)
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
            "semantic_memory_count": len(semantic_rows),
            "semantic_issue_count": len(semantic_issues),
            "semantic_critical_issue_count": semantic_severity_counts.get("critical", 0),
            "semantic_warn_issue_count": semantic_severity_counts.get("warn", 0),
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
        "memory_class_counts": [{"memory_class": key, "count": count} for key, count in memory_class_counter.most_common()],
        "recall_grain_counts": [{"recall_grain": key, "count": count} for key, count in recall_grain_counter.most_common()],
        "evidence_kinds": [{"evidence_kind": key, "count": count} for key, count in evidence_kind_counter.most_common()],
        "issue_counts": dict(sorted(issue_counts.items())),
        "collection_issue_counts": dict(sorted(collection_issue_counts.items())),
        "semantic_source_class_counts": dict(sorted(semantic_source_class_counts.items())),
        "semantic_issue_counts": dict(sorted(semantic_issue_counts.items())),
        "entrypoint_issue_counts": dict(sorted(entrypoint_issue_counts.items())),
        "issues": [asdict(issue) for issue in issues[: max(1, int(sample_limit))]],
        "collection_issues": [asdict(issue) for issue in collection_issues[: max(1, int(sample_limit))]],
        "semantic_issues": [asdict(issue) for issue in semantic_issues[: max(1, int(sample_limit))]],
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
        f"- Semantic memories: {summary.get('semantic_memory_count', 0)} "
        f"(issues={summary.get('semantic_issue_count', 0)})",
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
    lines.extend(["", "## Memory Classes", ""])
    for item in report.get("memory_class_counts") or []:
        lines.append(f"- {item.get('memory_class')}: {item.get('count')}")
    lines.extend(["", "## Recall Grains", ""])
    for item in report.get("recall_grain_counts") or []:
        lines.append(f"- {item.get('recall_grain')}: {item.get('count')}")
    lines.extend(["", "## Issue Counts", ""])
    for key, count in dict(report.get("issue_counts") or {}).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Collection Issue Counts", ""])
    for key, count in dict(report.get("collection_issue_counts") or {}).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Semantic Issue Counts", ""])
    for key, count in dict(report.get("semantic_issue_counts") or {}).items():
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
    lines.extend(["", "## Semantic Issue Samples", ""])
    for issue in report.get("semantic_issues") or []:
        lines.append(
            f"- [{issue.get('severity')}] {issue.get('issue_kind')} "
            f"{issue.get('semantic_id')} {issue.get('source_memory_id')}: {issue.get('detail')}"
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
            required_entrypoints=[
                *([] if bool(args.no_default_entrypoints) else list(DEFAULT_REQUIRED_ENTRYPOINTS)),
                *list(args.require_entrypoint or []),
            ],
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
    critical += int((report.get("summary") or {}).get("semantic_critical_issue_count") or 0)
    critical += int((report.get("summary") or {}).get("entrypoint_critical_issue_count") or 0)
    total = int((report.get("summary") or {}).get("issue_count") or 0)
    total += int((report.get("summary") or {}).get("collection_issue_count") or 0)
    total += int((report.get("summary") or {}).get("semantic_issue_count") or 0)
    total += int((report.get("summary") or {}).get("entrypoint_issue_count") or 0)
    if args.fail_on == "critical" and critical > 0:
        return 3
    if args.fail_on == "warn" and total > 0:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
