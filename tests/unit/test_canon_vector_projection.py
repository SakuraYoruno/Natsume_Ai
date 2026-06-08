from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.memory.canon_vector_projection import collect_canon_projection_documents


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_collect_world_canon_projection_documents(tmp_path: Path) -> None:
    world_root = tmp_path / "world"
    _write_json(
        world_root / "academy_rules.json",
        {
            "doc_id": "academy_rules",
            "version": 1,
            "hash": "world_hash_v1",
            "title": "Academy Rules",
            "body": "World fact for projection.",
            "tags": ["world", "rules"],
        },
    )

    docs, issues = collect_canon_projection_documents(world_root=world_root, include_world=True)

    assert issues == []
    assert len(docs) == 1
    assert docs[0].vector_id == "canon_world::academy_rules"
    assert docs[0].metadata["canon_kind"] == "world"
    assert docs[0].metadata["tags"] == "world,rules"
    assert docs[0].metadata["tags_count"] == 2
