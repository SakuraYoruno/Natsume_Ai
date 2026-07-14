from __future__ import annotations

import inspect
import json
from pathlib import Path

from host_brain.app import canon_routes
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.canon.application import CanonApplicationService
from host_brain.memory.canon_world_store import compute_world_canon_hash
from host_brain.persistence.canon_repository import CanonRepository


NOW = 1_800_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "canon-app.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-canon-app",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _write_document(root: Path, *, version: int = 1) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "doc_id": "wc-app",
        "version": version,
        "status": "active",
        "title": "Application Canon",
        "tags": ["application", "aw03"],
        "body": "Canonical application state.",
        "updated_by": "canon.test",
        "ts_ms": NOW,
    }
    payload["hash"] = compute_world_canon_hash(payload)
    (root / "wc-app.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return payload


def test_preview_is_read_only_and_startup_sync_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "world"
    _write_document(root)
    repository = CanonRepository(_migrated_db(tmp_path), clock=lambda: NOW)
    service = CanonApplicationService(repository=repository, world_root=root)

    assert service.repository.journal_count() == 0
    preview = service.preview_sync()
    assert preview.records[0].changed is True
    assert service.repository.journal_count() == 0

    result = service.sync_startup(ts_ms=NOW, import_legacy=False)
    assert result.changed_count == 1
    assert service.get_document("wc-app")["body"] == "Canonical application state."
    journal_count = service.repository.journal_count()
    assert service.sync_startup(ts_ms=NOW + 1, import_legacy=False).changed_count == 0
    assert service.repository.journal_count() == journal_count

    before = service.list_documents()
    service.rebuild_projections()
    assert service.list_documents() == before


def test_preview_of_missing_root_does_not_create_it(tmp_path: Path) -> None:
    missing = tmp_path / "missing-world"
    service = CanonApplicationService(
        repository=CanonRepository(_migrated_db(tmp_path)),
        world_root=missing,
    )
    assert service.preview_sync().records == ()
    assert missing.exists() is False


def test_http_adapter_contains_no_direct_canon_writer() -> None:
    source = inspect.getsource(canon_routes)
    assert "WorldCanonStore" not in source
    assert "ledger.log_canon" not in source
    assert "ledger.update_proposal" not in source
    assert "ledger.upsert_world_canon" not in source
    assert "service.submit_proposal(" in source
    assert "service.reject_proposal(" in source
    assert "service.apply(" in source
