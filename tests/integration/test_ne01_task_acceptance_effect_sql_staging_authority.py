from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_repository import DurableTaskStore


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
NOW = 1_900_000_000_000


def _catalog_through_0124(tmp_path: Path) -> Path:
    target = tmp_path / "migrations-through-0124"
    target.mkdir()
    for source in MIGRATIONS.glob("*.sql"):
        if int(source.name.split("_", 1)[0]) <= 124:
            shutil.copy2(source, target / source.name)
    return target


def _faulted_0125_catalog(tmp_path: Path) -> Path:
    target = tmp_path / "migrations-faulted-0125"
    target.mkdir()
    for source in MIGRATIONS.glob("*.sql"):
        destination = target / source.name
        if source.name != "0125_task_acceptance_effect_staging_authority.sql":
            shutil.copy2(source, destination)
            continue
        sql = source.read_text(encoding="utf-8")
        destination.write_text(
            sql.replace(
                "\nCOMMIT;\n",
                """

CREATE TRIGGER trg_0125_forced_failure
BEFORE INSERT ON task_runtime_missing_0125_table
BEGIN
    SELECT 1;
END;

COMMIT;
""",
                1,
            ),
            encoding="utf-8",
        )
    return target


def _insert_historical_effect(conn: sqlite3.Connection) -> tuple[str, str]:
    effect_id = "acceptance-effect://0125/existing-row"
    payload_json = json.dumps(
        {"content": "existing"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = "sha256:" + hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO task_runtime_acceptance_effect_intent(
            effect_id, graph_id, graph_revision, node_id, attempt_id,
            commit_scope, effect_type, payload_ref, payload_json,
            payload_sha256, idempotency_key, status, available_ts_ms,
            fencing_token, attempt_count, status_version,
            created_ts_ms, updated_ts_ms
        ) VALUES (?, 'graph-existing-0125', 1, 'node-existing-0125',
                  'attempt-existing-0125', 'NODE',
                  'conversation.assistant_turn',
                  'effect-payload://0125/existing-row', ?, ?,
                  '0125:existing-row', 'PENDING', ?, 0, 0, 0, ?, ?)
        """,
        (effect_id, payload_json, digest, NOW, NOW, NOW),
    )
    return effect_id, digest


def test_0125_failure_rolls_back_trigger_and_retry_preserves_existing_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "effect-staging-authority-rollback.sqlite3"
    head_124 = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_catalog_through_0124(tmp_path),
    ).apply_migrations(
        request_id="ne01-effect-authority-head-0124",
        ts_ms=NOW,
    )
    assert head_124.success is True, head_124.as_dict()
    assert head_124.summary["current_version"] == 124

    with sqlite3.connect(db_path) as conn:
        effect_id, digest = _insert_historical_effect(conn)
        before = conn.execute(
            """
            SELECT effect_id, payload_sha256, status, status_version
            FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone()
        conn.commit()

    failed = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=_faulted_0125_catalog(tmp_path),
    ).apply_migrations(
        request_id="ne01-effect-authority-fault",
        ts_ms=NOW + 1,
    )
    assert failed.success is False
    assert (
        failed.records[-1].migration_id
        == "0125_task_acceptance_effect_staging_authority"
    )
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT MAX(migration_id) FROM schema_migrations"
        ).fetchone() == (124,)
        assert conn.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'trg_task_runtime_acceptance_effect_staging_authority_insert'
            """
        ).fetchone() == (0,)
        assert conn.execute(
            """
            SELECT effect_id, payload_sha256, status, status_version
            FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone() == before

    retried = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-effect-authority-retry",
        ts_ms=NOW + 2,
    )
    assert retried.success is True, retried.as_dict()
    assert retried.summary["current_version"] == 125
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            """
            SELECT effect_id, payload_sha256, status, status_version
            FROM task_runtime_acceptance_effect_intent
            WHERE effect_id = ?
            """,
            (effect_id,),
        ).fetchone() == before
        assert str(before[1]) == digest
    DurableTaskStore(db_path).assert_acceptance_effect_intent_ready()
