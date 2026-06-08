from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface_original_quote_bank import to_prompt_style_text

DEFAULT_INPUT = ROOT / "artifacts" / "style_source" / "original_cn_all.jsonl"
DEFAULT_OUT = ROOT / "artifacts" / "style_source" / "surface_original_quote_legacy_sample.jsonl"

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
    "姐姐",
    "爸爸",
    "妈妈",
    "父亲",
    "母亲",
    "お",
)
PROMPT_POLLUTION_MARKERS = (
    "我可是你",
    "帮助妹妹",
    "关怀妹妹",
    "监视天",
    "相位",
    "能力",
    "周六",
    "感冒",
    "出发",
    "在家",
    "我就没事",
    "之类的",
)
UNSAFE_MARKERS = (
    "裸体",
    "中出",
    "鸡巴",
    "胸部",
    "内裤",
    "色情",
    "亲热",
    "做爱",
    "中出",
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
    text = re.sub(r"。([？！，])", r"\1", text)
    return text


def _ordered_unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker and marker in text for marker in markers)


def _normalize_source_ref(raw_file: Any, raw_index: Any) -> str:
    file_name = str(raw_file or "").replace("\\", "/")
    root_prefix = ROOT.as_posix().rstrip("/") + "/"
    if file_name.lower().startswith(root_prefix.lower()):
        file_name = file_name[len(root_prefix) :]
    index = str(raw_index if raw_index is not None else "").strip()
    return f"{file_name}#{index}" if index else file_name


def _quote_character_confidence(text: str, row_score: int) -> tuple[float, list[str]]:
    evidence: list[str] = []
    strong = ("老哥", "哥哥", "天爷", "本小姐", "妹妹我")
    soft = ("人家", "笨蛋", "好烦", "哼", "嘛", "啦", "呀", "呢", "行", "知道", "别")
    score = max(0, min(int(row_score or 0), 7)) / 7.0
    for marker in strong:
        if marker in text:
            evidence.append(f"strong_marker:{marker}")
            score += 0.18
    for marker in soft:
        if marker in text:
            evidence.append(f"soft_marker:{marker}")
            score += 0.05
    return min(1.0, score), evidence


def _classify_quote_tags(text: str) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    intents: list[str] = []
    emotions: list[str] = []
    moves: list[str] = []
    rhythms: list[str] = []
    cues: list[str] = []

    if _contains_any(text, ("我来了", "看见", "不理", "叫")):
        intents.append("presence")
        moves.extend(("clingy_presence", "pull_attention"))
    if _contains_any(text, ("请客", "吃饭", "晚饭")):
        intents.append("bored")
        moves.extend(("action_first", "food_motive"))
    if _contains_any(text, ("照顾", "帮", "心配", "看病", "管你")):
        intents.append("help")
        moves.extend(("take_charge", "care_behind_tease"))
        emotions.append("care")
    if _contains_any(text, ("伤心", "伤人", "悲しい", "被你", "别说")):
        intents.append("boundary")
        emotions.extend(("hurt", "wounded"))
        moves.extend(("wounded_pushback", "soft_after_sting"))
    if _contains_any(text, ("烦", "笨蛋", "バカ", "兄貴面")):
        intents.append("annoyed")
        emotions.extend(("annoyed", "angry_light"))
        moves.extend(("quick_rebuttal", "familiar_pushback"))
    if _contains_any(text, ("可爱", "褒", "照れ", "害羞")):
        intents.append("praise")
        emotions.append("shy")
        moves.append("shy_deflection")
    if _contains_any(text, ("为什么", "怎么", "说来")):
        moves.append("question_chain")
        rhythms.append("rapid_followup")
    if _contains_any(text, ("哼", "嘿嘿")):
        moves.append("smug_tease")
        emotions.append("playful")
    if _contains_any(text, ("没事", "最后没事", "無理しなくていい")):
        intents.append("reassurance")
        moves.append("soft_reassurance")
        emotions.append("soft")
    if _contains_any(text, ("喜欢", "想你", "想见", "抱抱", "恋人", "情侣", "永远")):
        intents.append("intimacy")
        emotions.append("deep_affection")
        moves.append("direct_affection")

    if "？" in text or "?" in text:
        rhythms.append("question")
        cues.append("反问/追问")
    if "！" in text or "!" in text:
        rhythms.append("exclamation")
        cues.append("短促提高情绪")
    if "……" in text or "..." in text:
        rhythms.append("hesitation")
        cues.append("停顿/犹豫")
    if "～" in text or "ー" in text:
        rhythms.append("elongation")
        cues.append("拖长音")
    if _contains_any(text, ("嘛", "啦", "呀", "呢")):
        cues.append("口语句尾")

    if not intents:
        intents.append("daily")
    if not emotions:
        emotions.append("playful" if any(move in moves for move in ("smug_tease", "familiar_pushback")) else "neutral")
    if not moves:
        moves.append("quick_reaction")
    return intents, emotions, moves, rhythms, cues


def _relationship_stages_for(intents: Iterable[str], emotions: Iterable[str]) -> list[str]:
    stages = ["familiar", "trusted_friend", "close_friend"]
    intent_set = set(intents)
    emotion_set = set(emotions)
    if {"presence", "daily", "bored"}.intersection(intent_set):
        stages.extend(("familiar", "trusted_friend"))
    if {"help", "reassurance"}.intersection(intent_set) or {"care", "soft"}.intersection(emotion_set):
        stages.extend(("familiar", "trusted_friend", "close_friend", "lover_early", "lover_stable"))
    if {"annoyed", "boundary"}.intersection(intent_set) or {"hurt", "wounded", "annoyed"}.intersection(emotion_set):
        stages.extend(("familiar", "trusted_friend", "close_friend", "ambiguous"))
    if "intimacy" in intent_set or "deep_affection" in emotion_set:
        stages.extend(("ambiguous", "pre_confession", "lover_early", "lover_stable"))
    return _ordered_unique(stages)


def _quote_metadata_from_row(row: Mapping[str, Any], *, text: str) -> dict[str, Any] | None:
    row_score = int(row.get("sora_candidate_score") or 0)
    character_confidence, evidence = _quote_character_confidence(text, row_score)
    if row_score < 3 and character_confidence < 0.32:
        return None
    intents, emotions, moves, rhythms, cues = _classify_quote_tags(text)
    confidence = min(
        1.0,
        0.18
        + character_confidence * 0.55
        + min(len(set(intents)) * 0.05, 0.16)
        + min(len(set(moves)) * 0.04, 0.16)
        + (0.08 if row_score >= 3 else 0.0),
    )
    if confidence < 0.36:
        return None
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return {
        "source_ref": _normalize_source_ref(row.get("file"), row.get("index")),
        "source_digest": digest,
        "confidence": round(float(confidence), 4),
        "character_confidence": round(float(character_confidence), 4),
        "intent_tags": _ordered_unique(intents),
        "emotion_tags": _ordered_unique(emotions),
        "style_moves": _ordered_unique(moves),
        "rhythm_tags": _ordered_unique(rhythms),
        "lexical_cues": _ordered_unique(cues),
        "relationship_stages": _relationship_stages_for(intents, emotions),
        "evidence": _ordered_unique(evidence),
    }


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
    prompt_text = to_prompt_style_text(text)
    if any(marker in prompt_text for marker in PROMPT_POLLUTION_MARKERS):
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
        metadata = _quote_metadata_from_row(row, text=cleaned)
        if metadata is None:
            continue
        record = {
            "quote_id": f"surface_original_quote::{metadata['source_digest']}",
            "text": cleaned,
            "prompt_text": to_prompt_style_text(cleaned),
            "source_ref": metadata["source_ref"],
            "source_digest": metadata["source_digest"],
            "source_score": int(row.get("sora_candidate_score") or 0),
            "confidence": metadata["confidence"],
            "character_confidence": metadata["character_confidence"],
            "intent_tags": metadata["intent_tags"],
            "emotion_tags": metadata["emotion_tags"],
            "style_moves": metadata["style_moves"],
            "rhythm_tags": metadata["rhythm_tags"],
            "lexical_cues": metadata["lexical_cues"],
            "relationship_stages": metadata["relationship_stages"],
        }
        existing = records.get(str(metadata["source_digest"]))
        if existing is None or _record_rank_key(record) > _record_rank_key(existing):
            records[str(metadata["source_digest"])] = record

    ranked = sorted(records.values(), key=_record_rank_key, reverse=True)
    return ranked[: max(1, int(max_quotes or 240))]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a legacy review/sample JSONL from Chinese quotes. Runtime quote guidance "
            "uses the Chroma surface_original_quotes collection instead."
        )
    )
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
