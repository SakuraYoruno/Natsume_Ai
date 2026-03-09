from __future__ import annotations

import json
from pathlib import Path

from host_brain.memory.persona_utterance_vector_projection import (
    collect_persona_utterance_projection_documents,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_collect_persona_projection_docs_from_ja_and_cn_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "style_canon"
    _write_jsonl(
        root / "natsume_ai_ja_speaker_lines.jsonl",
        [
            {
                "file_rel_path": "story/ch01.ast",
                "block_id": "block_01",
                "line_index": 1,
                "speaker_jp": "夏目藍",
                "jp_text": "今日は少しつらいね。",
                "speaker_cn": "夏目蓝",
                "cn_text": "今天有点难受呢。",
            }
        ],
    )
    _write_jsonl(
        root / "natsume_ai_cn_speaker_lines.jsonl",
        [
            {
                "file_rel_path": "story/ch02.ast",
                "block_id": "block_02",
                "line_index": 3,
                "speaker_jp": "夏目藍",
                "jp_text": "無理しないで。",
                "speaker_cn": "夏目蓝",
                "cn_text": "别太勉强自己。",
            }
        ],
    )

    docs, issues = collect_persona_utterance_projection_documents(style_artifact_root=root)
    assert issues == []
    assert len(docs) == 2
    langs = {str(row.metadata.get("lang")) for row in docs}
    assert langs == {"ja", "cn"}
    assert all(str(row.vector_id).startswith("persona_utterance::") for row in docs)
    assert all("[PERSONA_UTTERANCE]" in row.text for row in docs)

