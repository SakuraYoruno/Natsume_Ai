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


class TestCanonVectorProjectionContract:
    def test_world_projection_has_deterministic_vector_id_and_metadata(self, tmp_path: Path) -> None:
        world_root = tmp_path / "world"
        _write_json(
            world_root / "natsume_world.json",
            {
                "doc_id": "natsume_world",
                "version": 3,
                "hash": "world_hash_v3",
                "title": "Natsume World",
                "body": "Canonical world note.",
                "tags": ["world", "natsume"],
            },
        )

        docs, issues = collect_canon_projection_documents(world_root=world_root, include_world=True)

        assert issues == []
        assert len(docs) == 1
        doc = docs[0]
        assert doc.vector_id == "canon_world::natsume_world"
        assert doc.metadata["canon_kind"] == "world"
        assert doc.metadata["doc_id"] == "natsume_world"
        assert doc.metadata["version"] == 3
        assert doc.metadata["tags"] == "world,natsume"
        assert doc.metadata["tags_count"] == 2
