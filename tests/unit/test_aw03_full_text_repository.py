from __future__ import annotations

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.full_text_repository import FullTextRepository


def test_durable_full_text_repository_requires_and_uses_catalog_schema(tmp_path) -> None:
    db_path = tmp_path / "canonical.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-full-text-repository",
    )
    assert migrated.success, migrated.as_dict()

    repository = FullTextRepository(db_path)
    repository.replace_document(memory_id="episode-1", content="猫 咪 喜 欢 阳 光")
    rows = repository.search("猫 咪", limit=5)

    assert [row["memory_id"] for row in rows] == ["episode-1"]
