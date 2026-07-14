from __future__ import annotations

from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persistence.life_memory_repository import (
    LifeMemoryRepository,
    SemanticLineageBatch,
)
from host_brain.retrieval.recall_fusion_stores import search_semantic_memory


ROOT = Path(__file__).resolve().parents[2]
NOW = 1_800_000_000_000


def test_recall_fusion_has_no_private_connection_or_raw_sql_authority() -> None:
    source = (
        ROOT / "host_brain" / "retrieval" / "recall_fusion_stores.py"
    ).read_text(encoding="utf-8")
    lowered = source.lower()
    assert "sqlite3" not in lowered
    assert "._conn" not in source
    assert "getattr(ledger, \"_conn\"" not in source
    assert ".execute(" not in source
    assert not re.search(r"\bselect\s+.+\s+from\b", lowered)
    assert "LifeMemoryRepository.from_ledger" in source
    assert "SemanticLineageBatch" in source


def test_durable_repository_batches_ordered_collection_entity_and_correction_lineage(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "semantic-lineage.sqlite3"
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-recall-lineage",
        ts_ms=NOW,
    )
    assert migration.success is True, migration.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO life_episode(
                episode_id, episode_kind, person_id, session_id, primary_ref,
                created_ts_ms, last_touched_ts_ms
            ) VALUES (?, 'CHAT', ?, ?, ?, ?, ?)
            """,
            [
                ("episode-old", "person-old", "session-old", "chat://old", NOW, NOW),
                ("episode-new", "person-new", "session-new", "chat://new", NOW + 1, NOW + 1),
            ],
        )
        conn.executemany(
            """
            INSERT INTO life_evidence(
                evidence_id, episode_id, evidence_kind, source_ref, created_ts_ms
            ) VALUES (?, ?, 'USER_TEXT', ?, ?)
            """,
            [
                ("evidence-old-2", "episode-old", "evidence://old-2", NOW + 2),
                ("evidence-old-1", "episode-old", "evidence://old-1", NOW + 1),
                ("evidence-new", "episode-new", "evidence://new", NOW + 3),
            ],
        )
        conn.execute(
            """
            INSERT INTO life_memory_collection(
                collection_id, collection_kind, domain, scope_key,
                created_ts_ms, updated_ts_ms
            ) VALUES ('collection-1', 'recommendation', 'books', 'recent', ?, ?)
            """,
            (NOW, NOW),
        )
        conn.executemany(
            """
            INSERT INTO life_memory_collection_item(
                collection_item_id, collection_id, episode_id, item_key,
                source_ref, evidence_ref, position, created_ts_ms
            ) VALUES (?, 'collection-1', ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "item-old",
                    "episode-old",
                    "old",
                    "source://old-item",
                    "evidence://old-item",
                    2,
                    NOW,
                ),
                (
                    "item-new",
                    "episode-new",
                    "new",
                    "source://new-item",
                    "evidence://new-item",
                    1,
                    NOW + 1,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO life_memory_entity(
                entity_id, entity_kind, canonical_name, normalized_key,
                first_seen_ts_ms, last_seen_ts_ms
            ) VALUES ('entity-1', 'item', 'Book', 'book', ?, ?)
            """,
            (NOW, NOW + 1),
        )
        conn.executemany(
            """
            INSERT INTO life_memory_entity_mention(
                mention_id, entity_id, episode_id, collection_id,
                collection_item_id, source_ref, evidence_ref, position,
                created_ts_ms
            ) VALUES (?, 'entity-1', ?, 'collection-1', ?, ?, ?, ?, ?)
            """,
            [
                (
                    "mention-old",
                    "episode-old",
                    "item-old",
                    "source://old-mention",
                    "evidence://old-mention",
                    0,
                    NOW,
                ),
                (
                    "mention-new",
                    "episode-new",
                    "item-new",
                    "source://new-mention",
                    "evidence://new-mention",
                    1,
                    NOW + 1,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO life_memory_correction(
                correction_id, episode_id, correction_episode_id,
                correction_kind, created_ts_ms
            ) VALUES ('correction-1', 'episode-old', 'episode-new', 'replace', ?)
            """,
            (NOW + 2,),
        )
        conn.commit()

    ledger = SQLiteLedger(
        db_path,
        initialize_schema=False,
        check_same_thread=False,
    )
    try:
        repository = LifeMemoryRepository.from_ledger(ledger)
        assert repository.db_path == db_path.resolve()
        assert repository._shared_connection is None
        lineage = repository.load_semantic_lineage_batch(
            collection_ids=["collection-1"],
            entity_ids=["entity-1"],
            correction_ids=["correction-1"],
        )
    finally:
        ledger.close()

    assert [
        item["episode_id"]
        for item in lineage.collection_items_by_collection_id["collection-1"]
    ] == ["episode-new", "episode-old"]
    assert [
        item["episode_id"]
        for item in lineage.entity_mentions_by_entity_id["entity-1"]
    ] == ["episode-new", "episode-old"]
    assert [
        item["source_ref"] for item in lineage.evidence_by_episode_id["episode-old"]
    ] == ["evidence://old-1", "evidence://old-2"]
    assert lineage.episodes_by_id["episode-new"]["person_id"] == "person-new"
    assert lineage.corrections_by_id["correction-1"]["correction_episode_id"] == "episode-new"


def test_semantic_recall_loads_one_typed_lineage_snapshot_for_all_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Ledger:
        def list_semantic_memories(self, *, limit: int, offset: int):  # noqa: ANN201
            assert limit == 16
            assert offset == 0
            return (
                [
                    {
                        "semantic_id": "semantic-direct",
                        "summary": "alpha shared preference direct",
                        "source_memory_ids": ["life_episode::episode-direct"],
                        "confidence": 0.8,
                        "tags": ["preference"],
                        "ts_ms": 100,
                    },
                    {
                        "semantic_id": "semantic-derived",
                        "summary": "alpha shared preference derived",
                        "source_memory_ids": [
                            "life_memory_collection::collection-1",
                            "life_memory_entity::entity-1",
                            "life_memory_correction::correction-1",
                        ],
                        "confidence": 0.8,
                        "tags": ["preference"],
                        "ts_ms": 90,
                    },
                ],
                2,
            )

    lineage = SemanticLineageBatch(
        episodes_by_id={
            "episode-direct": {
                "episode_id": "episode-direct",
                "person_id": "person-direct",
                "session_id": "session-direct",
                "primary_ref": "chat://direct",
            },
            "episode-derived": {
                "episode_id": "episode-derived",
                "person_id": "person-derived",
                "session_id": "session-derived",
                "primary_ref": "chat://derived",
            },
        },
        evidence_by_episode_id={
            "episode-direct": (
                {"evidence_id": "evidence-direct", "source_ref": "evidence://direct"},
            ),
            "episode-derived": (
                {"evidence_id": "evidence-derived", "source_ref": "evidence://derived"},
            ),
        },
        collection_items_by_collection_id={
            "collection-1": (
                {
                    "episode_id": "episode-derived",
                    "source_ref": "source://collection-item",
                    "evidence_ref": "evidence://collection-item",
                },
            )
        },
        entity_mentions_by_entity_id={
            "entity-1": (
                {
                    "episode_id": "episode-derived",
                    "collection_id": "collection-1",
                    "source_ref": "source://entity-mention",
                    "evidence_ref": "evidence://entity-mention",
                },
            )
        },
        corrections_by_id={
            "correction-1": {
                "correction_id": "correction-1",
                "episode_id": "episode-derived",
                "correction_episode_id": None,
            }
        },
    )

    class Repository:
        def __init__(self) -> None:
            self.calls: list[dict[str, list[str]]] = []

        def load_semantic_lineage_batch(self, **groups):  # noqa: ANN003, ANN201
            self.calls.append({key: list(value) for key, value in groups.items()})
            return lineage

    repository = Repository()
    monkeypatch.setattr(
        LifeMemoryRepository,
        "from_ledger",
        classmethod(lambda cls, ledger: repository),
    )

    results = search_semantic_memory(
        SimpleNamespace(ledger=Ledger()),
        query="alpha shared preference",
        top_k=2,
        person_id="person-direct",
        session_id="session-direct",
        now_ts_ms=200,
    )

    assert repository.calls == [
        {
            "episode_ids": ["episode-direct"],
            "collection_ids": ["collection-1"],
            "entity_ids": ["entity-1"],
            "correction_ids": ["correction-1"],
        }
    ]
    by_id = {item["memory_id"]: item for item in results}
    direct = by_id["semantic-direct"]["metadata"]
    derived = by_id["semantic-derived"]["metadata"]
    assert direct["semantic_person_ids"] == ["person-direct"]
    assert direct["semantic_evidence_refs"] == ["evidence://direct"]
    assert derived["semantic_person_ids"] == ["person-derived"]
    assert "evidence://collection-item" in derived["semantic_evidence_refs"]
    assert "evidence://entity-mention" in derived["semantic_evidence_refs"]
    assert "life_memory_correction://correction-1" in derived["semantic_evidence_refs"]
