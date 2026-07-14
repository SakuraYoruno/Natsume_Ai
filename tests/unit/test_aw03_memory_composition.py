from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.memory import build_process_memory_container
from host_brain.memory.life_memory.contracts import ChatMemoryCandidate


NOW = 1_800_000_000_000


class _Worth:
    def evaluate(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN201
        return SimpleNamespace(
            decision="write",
            worth_score=0.8,
            tags=["new_info"],
            reason="test",
        )


class _Guard:
    def validate(self, _candidate, *, tags) -> None:  # noqa: ANN001
        del tags
        return None


class _ProjectionStore:
    def __init__(self) -> None:
        self.calls = 0

    def write_memory(self, **_kwargs):  # noqa: ANN003, ANN201
        self.calls += 1
        return SimpleNamespace(success=True, reason_code=None, detail="ok", memory_id="projection")


def test_process_memory_container_owns_service_repository_and_projection(tmp_path: Path) -> None:
    db_path = tmp_path / "memory-composition.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="memory-composition",
        ts_ms=NOW,
    )
    assert migration.success, migration.as_dict()
    ledger = SQLiteLedger(db_path=db_path)
    projection = _ProjectionStore()
    try:
        container = build_process_memory_container(
            ledger=ledger,
            canonical_db_path=db_path,
            worth_evaluator=_Worth(),
            memory_guard=_Guard(),
            episodic_projection_store=projection,
        )
        receipt = container.service.admit_chat(
            ChatMemoryCandidate(
                request_id="composition-request",
                session_id="composition-session",
                person_id="owner",
                intent_id="composition-intent",
                user_message="请记住这条偏好",
                assistant_reply="好，我会保留。",
                emotion_tag="NEUTRAL",
                turn_count=3,
                ts_ms=NOW,
            )
        )
        assert receipt.status == "COMMITTED"
        assert receipt.projection_status == "PUBLISHED"
        assert projection.calls == 1
        assert container.startup_projection_summary == {
            "published": 0,
            "failed": 0,
            "skipped": 0,
        }
    finally:
        ledger.close()


def test_memory_composition_does_not_reverse_import_app_layer() -> None:
    source_path = (
        Path(__file__).parents[2] / "host_brain" / "composition" / "memory.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(name == "host_brain.app" for name in imports)
    assert not any(name.startswith("host_brain.app.") for name in imports)
