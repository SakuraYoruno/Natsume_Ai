from __future__ import annotations

import json

from tools.build_sora_style_bank import build_style_references, write_jsonl, write_summary


def test_build_sora_style_bank_writes_derived_features_without_raw_text(tmp_path) -> None:
    source = tmp_path / "candidates.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "file": "zh.scn",
                        "index": 1,
                        "kind": "dialogue",
                        "sora_candidate_score": 3,
                        "daily_style_safe": True,
                        "text": "「喂。别说了。 别偷偷说一些会伤人家心的话」",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "file": "zh.scn",
                        "index": 2,
                        "kind": "narration",
                        "sora_candidate_score": 7,
                        "daily_style_safe": True,
                        "text": "这是旁白。",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    refs = build_style_references([source])
    out = tmp_path / "bank.jsonl"
    summary = tmp_path / "summary.md"
    write_jsonl(out, refs)
    write_summary(summary, refs, inputs=[source])

    payload = out.read_text(encoding="utf-8")
    assert len(refs) == 1
    assert '"intent_tags"' in payload
    assert "boundary" in payload
    assert "别偷偷说" not in payload
    assert "does not duplicate raw VN dialogue text" in summary.read_text(encoding="utf-8")
