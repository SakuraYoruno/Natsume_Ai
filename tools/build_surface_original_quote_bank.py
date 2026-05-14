from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.style_reference_bank import style_reference_from_raw_row

DEFAULT_INPUT = ROOT / "artifacts" / "style_source" / "original_cn_daily_sora_candidates.jsonl"
DEFAULT_OUT = ROOT / "data" / "style_bank" / "surface_original_quote_bank.jsonl"

KANA_RE = re.compile(r"[\u3040-\u30ff]")
ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")

SCENE_POLLUTION_MARKERS = (
    "老师",
    "同学",
    "学校",
    "教室",
    "作业",
    "社团",
    "班长",
    "学生",
    "考试",
    "清洁公司",
    "传唤",
    "独居",
    "男朋友",
    "女朋友",
    "哥哥",
    "老哥",
    "兄",
    "妹妹",
    "姐姐",
    "爸爸",
    "妈妈",
    "父亲",
    "母亲",
    "お",
)
UNSAFE_MARKERS = (
    "裸体",
    "中出",
    "鸡巴",
    "胸部",
    "内裤",
    "色情",
)


def _read_jsonl(path: Path) -> Iterable[Mapping[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as file:
        for lineno, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, Mapping):
                raise ValueError(f"{path}:{lineno}: expected JSON object")
            yield payload


def _clean_quote(raw: Any) -> str:
    text = str(raw or "").replace("\u3000", " ").strip()
    text = re.sub(r"\s+", "", text)
    while len(text) >= 2 and text[0] in "「『“\"" and text[-1] in "」』”\"":
        text = text[1:-1].strip()
    text = text.replace("～", "").replace("~", "")
    text = re.sub(r"。([？！，])", r"\1", text)
    return text


def _row_is_usable(row: Mapping[str, Any]) -> bool:
    text = _clean_quote(row.get("text"))
    if not text:
        return False
    if str(row.get("kind") or "") != "dialogue":
        return False
    if not bool(row.get("daily_style_safe", True)):
        return False
    if KANA_RE.search(text) or ASCII_WORD_RE.search(text):
        return False
    if not (4 <= len(text) <= 46):
        return False
    if int(row.get("sora_candidate_score") or 0) <= 0:
        return False
    if any(marker in text for marker in UNSAFE_MARKERS):
        return False
    if any(marker in text for marker in SCENE_POLLUTION_MARKERS):
        return False
    return True


def _record_rank_key(record: Mapping[str, Any]) -> tuple[float, float, int, str]:
    source_score = float(record.get("source_score") or 0.0)
    confidence = float(record.get("confidence") or 0.0)
    text_len = len(str(record.get("text") or ""))
    return (source_score, confidence, -abs(text_len - 18), str(record.get("quote_id") or ""))


def build_quote_bank(rows: Iterable[Mapping[str, Any]], *, max_quotes: int = 240) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _row_is_usable(row):
            continue
        cleaned = _clean_quote(row.get("text"))
        normalized_row = dict(row)
        normalized_row["text"] = cleaned
        ref = style_reference_from_raw_row(normalized_row, lang_hint="zh")
        if ref is None:
            continue
        record = {
            "quote_id": f"surface_original_quote::{ref.source_digest}",
            "text": cleaned,
            "source_ref": ref.source_ref,
            "source_digest": ref.source_digest,
            "source_score": int(row.get("sora_candidate_score") or 0),
            "confidence": round(float(ref.confidence), 4),
            "character_confidence": round(float(ref.character_confidence), 4),
            "intent_tags": list(ref.intent_tags),
            "emotion_tags": list(ref.emotion_tags),
            "style_moves": list(ref.style_moves),
            "rhythm_tags": list(ref.rhythm_tags),
            "lexical_cues": list(ref.lexical_cues),
            "relationship_stages": list(ref.relationship_stages),
        }
        existing = records.get(ref.source_digest)
        if existing is None or _record_rank_key(record) > _record_rank_key(existing):
            records[ref.source_digest] = record

    ranked = sorted(records.values(), key=_record_rank_key, reverse=True)
    return ranked[: max(1, int(max_quotes or 240))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build raw Chinese quote bank for isolated surface few-shot style references.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input daily-safe candidate JSONL from extract_vn_scn_strings.py.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output raw quote JSONL.")
    parser.add_argument("--max-quotes", type=int, default=240)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"missing input candidates: {input_path}")
    records = build_quote_bank(_read_jsonl(input_path), max_quotes=max(1, int(args.max_quotes or 240)))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"wrote {len(records)} raw style quotes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
