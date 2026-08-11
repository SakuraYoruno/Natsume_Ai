from __future__ import annotations

import ast
from pathlib import Path
import sqlite3

import pytest

from host_brain.composition.host_memory_runtime import (
    build_host_emotion_timeline,
)
from host_brain.persistence.emotion_timeline_query_repository import (
    EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY,
    EmotionTimelineQueryRepository,
    EmotionTimelineQueryRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
AVATAR_ASSET = ROOT / "host_brain" / "audit" / "migrations" / "0007_avatar_schema.sql"
NOW = 1_900_000_000_000


def _avatar_db(tmp_path: Path, rows: list[tuple[str | None, str, int]]) -> Path:
    path = tmp_path / "canonical.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(AVATAR_ASSET.read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT INTO avatar_action_log(emotion_tag, status, ts_ms) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    return path


def test_repository_reads_latest_window_in_chronological_order(tmp_path: Path) -> None:
    since_ms = NOW - 86_400_000
    rows = [
        ("HAPPY" if index % 2 == 0 else "ANGRY", "OK", since_ms + index)
        for index in range(200)
    ]
    rows.extend(
        [
            ("SAD", "FAILED", NOW),
            (None, "OK", NOW),
            ("CALM", "OK", since_ms - 1),
        ]
    )
    repository = EmotionTimelineQueryRepository(_avatar_db(tmp_path, rows))

    records = repository.recent_actions(since_ms=since_ms, limit=150)

    assert len(records) == 150
    assert records[0].ts_ms == since_ms + 50
    assert records[-1].ts_ms == since_ms + 199
    assert [record.ts_ms for record in records] == sorted(
        record.ts_ms for record in records
    )


def test_repository_is_read_only_and_never_creates_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(EmotionTimelineQueryRepositoryError) as raised:
        EmotionTimelineQueryRepository(missing)
    assert raised.value.reason_code == EMOTION_TIMELINE_QUERY_SCHEMA_NOT_READY
    assert missing.exists() is False

    db_path = _avatar_db(tmp_path, [("HAPPY", "OK", NOW)])
    repository = EmotionTimelineQueryRepository(db_path)
    assert len(repository.recent_actions(since_ms=NOW - 1, limit=10)) == 1
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM avatar_action_log").fetchone()[0] == 1


def test_host_runtime_assembles_vad_timeline_from_public_canonical_path(
    tmp_path: Path,
) -> None:
    db_path = _avatar_db(
        tmp_path,
        [
            ("happy", "OK", NOW - 3),
            ("ANGRY", "OK", NOW - 2),
            ("custom", "OK", NOW - 1),
        ],
    )

    class PublicLedgerReference:
        canonical_db_path = str(db_path)

        def __getattr__(self, name: str):
            if name in {"_db_path", "_conn"}:
                raise AssertionError("private ledger persistence was accessed")
            raise AttributeError(name)

    timeline = build_host_emotion_timeline(
        ledger=PublicLedgerReference(),
        clock_ms=lambda: NOW,
    )

    assert [entry.emotion_tag for entry in timeline.entries] == [
        "HAPPY",
        "ANGRY",
        "CUSTOM",
    ]
    assert timeline.entries[0].valence == 0.60
    assert timeline.entries[0].arousal == 0.40
    assert timeline.entries[1].valence == -0.70
    assert timeline.entries[1].arousal == 0.80
    assert timeline.entries[2].valence == 0.0
    assert timeline.entries[2].arousal == 0.0
    assert all(entry.source == "restored_from_log" for entry in timeline.entries)


def test_host_runtime_fails_soft_to_empty_timeline_on_query_failure(
    tmp_path: Path,
) -> None:
    class MissingLedgerReference:
        canonical_db_path = str(tmp_path / "missing.sqlite3")

    timeline = build_host_emotion_timeline(
        ledger=MissingLedgerReference(),
        clock_ms=lambda: NOW,
    )

    assert timeline.entries == []


def test_host_runtime_emotion_timeline_has_no_sqlite_or_private_path_access() -> None:
    path = ROOT / "host_brain" / "composition" / "host_memory_runtime.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    function = next(
        node
        for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "build_host_emotion_timeline"
        )
    )
    function_source = ast.get_source_segment(source, function) or ""
    forbidden = (
        "sqlite3",
        ".execute(",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "CREATE TABLE",
    )
    for token in forbidden:
        assert token not in function_source, f"dependency factory still contains {token}"
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"_db_path", "_conn"}
        for node in ast.walk(function)
    )
    assert not any(
        isinstance(node, ast.Constant) and node.value in {"_db_path", "_conn"}
        for node in ast.walk(function)
    )

    repository_source = (
        ROOT / "host_brain" / "persistence" / "emotion_timeline_query_repository.py"
    ).read_text(encoding="utf-8")
    assert "?mode=ro" in repository_source
    assert "def _snapshot(" in repository_source
