from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from host_brain.app.conversation_event_resolver import (
    resolve_conversation_event_action,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.context_preference_repository import PreferenceRepository


ROOT = Path(__file__).resolve().parents[2]
NOW = 1_900_000_000_000


class DurableLedgerReference:
    def __init__(self, db_path: Path) -> None:
        self.canonical_db_path = str(db_path)

    def __getattr__(self, name: str) -> Any:
        if name in {"_conn", "_write_lock", "execute", "connect", "connection"}:
            raise AssertionError("resolver reached through generic ledger persistence")
        raise AttributeError(name)


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "conversation-event-resolver.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-conversation-event-resolver",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _append_event(
    repository: MemoryEventRepository,
    *,
    event_id: str,
    created_at_ms: int,
    person_id: str = "person-1",
    session_id: str = "session-1",
    visibility: str = "system",
    source_kind: str = "browser_automation",
    event_kind: str = "browser_failed",
    refs: dict[str, Any] | None = None,
    causality: dict[str, Any] | None = None,
) -> None:
    repository.append_event(
        {
            "event_id": event_id,
            "person_id": person_id,
            "session_id": session_id,
            "conversation_id": session_id,
            "source_kind": source_kind,
            "event_kind": event_kind,
            "visibility": visibility,
            "role": "assistant",
            "content": f"event::{event_id}",
            "content_summary": f"summary::{event_id}",
            "refs": refs or {},
            "causality": causality or {},
            "created_at_ms": created_at_ms,
        }
    )


def test_resolver_uses_latest_delivery_before_cutoff_and_decodes_items(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = PreferenceRepository(db_path)
    repository.append_delivery(
        {
            "delivery_id": "delivery-old",
            "person_id": "person-1",
            "session_id": "session-1",
            "conversation_id": "session-1",
            "domain": "cross-domain-books",
            "platform": "desktop",
            "request_id": "request-old",
            "job_id": "job-old",
            "recommendations_ref": "recommendations-old",
            "ranked_recommendations_ref": "ranked-old",
            "items": [
                {"rank": 1, "title": "第一本"},
                {"rank": 2, "title": "第二本", "detail_url": "https://example.invalid/2"},
            ],
            "message_payload": {"kind": "recommendation"},
            "created_at_ms": NOW + 10,
        }
    )
    repository.append_delivery(
        {
            "delivery_id": "delivery-after-cutoff",
            "person_id": "person-1",
            "session_id": "session-1",
            "conversation_id": "session-1",
            "domain": "lightnovel",
            "items": [{"rank": 2, "title": "不应命中"}],
            "created_at_ms": NOW + 30,
        }
    )

    result = resolve_conversation_event_action(
        ledger=DurableLedgerReference(db_path),
        person_id="person-1",
        session_id="session-1",
        current_user_message="第二本详情",
        now_ms=NOW + 40,
        before_ms=NOW + 20,
    )

    assert result["status"] == "RESOLVED"
    assert result["action_kind"] == "fetch_item_detail"
    assert result["source_event"]["delivery_id"] == "delivery-old"
    assert result["target_item"]["title"] == "第二本"
    assert result["target_item"]["detail_url"] == "https://example.invalid/2"
    assert result["evidence_refs"] == [
        {
            "delivery_id": "delivery-old",
            "request_id": "request-old",
            "job_id": "job-old",
            "recommendations_ref": "recommendations-old",
            "ranked_recommendations_ref": "ranked-old",
        }
    ]


def test_resolver_preserves_any_identity_visibility_cutoff_and_event_refs(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = MemoryEventRepository(db_path)
    _append_event(
        repository,
        event_id="event-before",
        person_id="another-person",
        session_id="shared-session",
        created_at_ms=NOW + 10,
        refs={"job_id": "job-before", "url": "https://example.invalid/job"},
        causality={"status": "FAILED", "reason_code": "timeout"},
    )
    _append_event(
        repository,
        event_id="event-after",
        session_id="shared-session",
        created_at_ms=NOW + 30,
        refs={"job_id": "job-after"},
        causality={"status": "FAILED", "reason_code": "newer"},
    )
    _append_event(
        repository,
        event_id="event-private",
        session_id="shared-session",
        visibility="private",
        created_at_ms=NOW + 15,
        refs={"job_id": "job-private"},
    )

    result = resolve_conversation_event_action(
        ledger=DurableLedgerReference(db_path),
        person_id="person-1",
        session_id="shared-session",
        current_user_message="为什么失败",
        now_ms=NOW + 40,
        before_ms=NOW + 20,
    )

    assert result["status"] == "RESOLVED"
    assert result["action_kind"] == "explain_recent_event_failure"
    assert result["source_event"]["event_id"] == "event-before"
    assert result["target"]["job_id"] == "job-before"
    assert result["target"]["reason_code"] == "timeout"
    assert result["evidence_refs"] == [
        {"job_id": "job-before", "url": "https://example.invalid/job"}
    ]


def test_raw_sqlite_connection_compatibility_is_owned_by_repositories(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    repository = MemoryEventRepository(db_path)
    _append_event(
        repository,
        event_id="event-raw-connection",
        created_at_ms=NOW,
        refs={"job_id": "job-raw"},
    )
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        result = resolve_conversation_event_action(
            ledger=connection,
            person_id="person-1",
            session_id="session-1",
            current_user_message="状态怎么样",
            now_ms=NOW + 1,
        )
    assert result["status"] == "RESOLVED"
    assert result["target"]["job_id"] == "job-raw"


def test_resolver_fails_soft_when_no_persistence_adapter_can_bind() -> None:
    result = resolve_conversation_event_action(
        ledger=object(),
        person_id="person-1",
        session_id="session-1",
        current_user_message="继续",
        now_ms=NOW,
    )
    assert result["status"] == "UNRESOLVED"
    assert result["reason_code"] == "NO_RECENT_EVENT_BINDING"


def test_resolver_has_no_generic_ledger_or_sql_boundary() -> None:
    source = (
        ROOT / "host_brain" / "app" / "conversation_event_resolver.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        ".execute(",
        ".connect(",
        ".connection",
        "._conn",
        "._write_lock",
        "sqlite_master",
        "SELECT ",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "PRAGMA",
        "_table_exists",
        "_execute_all",
    )
    for token in forbidden:
        assert token not in source, f"resolver still contains {token}"
