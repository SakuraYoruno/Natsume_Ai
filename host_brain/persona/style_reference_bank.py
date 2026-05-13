from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STYLE_BANK_PATH = ROOT / "data" / "style_bank" / "original_cn_style_bank.jsonl"


def _clean_text(raw: Any) -> str:
    text = str(raw or "").replace("\u3000", " ").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) >= 2 and text[0] in "「『“\"" and text[-1] in "」』”\"":
        text = text[1:-1].strip()
    return text


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(marker and marker in text for marker in markers)


def infer_reference_lang(text: str, *, source_hint: str = "") -> str:
    hint = str(source_hint or "").lower()
    if "chinese" in hint or "evb_" in hint or "extract\\project" in hint.replace("/", "\\"):
        return "zh"
    if any("\u3040" <= ch <= "\u30ff" for ch in text):
        return "ja"
    return "zh"


def normalize_source_ref(raw_file: Any, raw_index: Any) -> str:
    file_name = str(raw_file or "").replace("\\", "/")
    root_prefix = ROOT.as_posix().rstrip("/") + "/"
    if file_name.lower().startswith(root_prefix.lower()):
        file_name = file_name[len(root_prefix) :]
    index = str(raw_index if raw_index is not None else "").strip()
    return f"{file_name}#{index}" if index else file_name


def _normalize_relationship_stage(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "stepsibling_early": "stranger",
        "sibling_good": "familiar",
        "sibling_close": "close_friend",
        "friend": "trusted_friend",
        "close": "close_friend",
        "intimate": "lover_early",
        "lover": "lover_early",
        "soulmate": "lover_stable",
    }
    return aliases.get(value, value)


def _normalize_relationship_stages(raw_values: Iterable[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in raw_values:
        item = _normalize_relationship_stage(raw)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


@dataclass(frozen=True)
class StyleReference:
    ref_id: str
    lang: str
    source_ref: str
    source_digest: str
    confidence: float
    character_confidence: float
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    rhythm_tags: tuple[str, ...] = field(default_factory=tuple)
    lexical_cues: tuple[str, ...] = field(default_factory=tuple)
    relationship_stages: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "lang": self.lang,
            "source_ref": self.source_ref,
            "source_digest": self.source_digest,
            "confidence": round(float(self.confidence), 4),
            "character_confidence": round(float(self.character_confidence), 4),
            "intent_tags": list(self.intent_tags),
            "emotion_tags": list(self.emotion_tags),
            "style_moves": list(self.style_moves),
            "rhythm_tags": list(self.rhythm_tags),
            "lexical_cues": list(self.lexical_cues),
            "relationship_stages": list(self.relationship_stages),
            "evidence": list(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StyleReference":
        return cls(
            ref_id=str(payload.get("ref_id") or ""),
            lang=str(payload.get("lang") or ""),
            source_ref=str(payload.get("source_ref") or ""),
            source_digest=str(payload.get("source_digest") or ""),
            confidence=float(payload.get("confidence") or 0.0),
            character_confidence=float(payload.get("character_confidence") or 0.0),
            intent_tags=tuple(str(item) for item in payload.get("intent_tags") or [] if str(item).strip()),
            emotion_tags=tuple(str(item) for item in payload.get("emotion_tags") or [] if str(item).strip()),
            style_moves=tuple(str(item) for item in payload.get("style_moves") or [] if str(item).strip()),
            rhythm_tags=tuple(str(item) for item in payload.get("rhythm_tags") or [] if str(item).strip()),
            lexical_cues=tuple(str(item) for item in payload.get("lexical_cues") or [] if str(item).strip()),
            relationship_stages=_normalize_relationship_stages(payload.get("relationship_stages") or []),
            evidence=tuple(str(item) for item in payload.get("evidence") or [] if str(item).strip()),
        )


@dataclass(frozen=True)
class StyleReferenceQuery:
    lang: str = "zh"
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    relationship_stage: str = ""
    limit: int = 5


@dataclass(frozen=True)
class StyleReferenceSelection:
    references: tuple[StyleReference, ...] = field(default_factory=tuple)
    query: StyleReferenceQuery = field(default_factory=StyleReferenceQuery)
    degraded: bool = False
    reason: str = ""

    def source_refs(self) -> list[str]:
        return [ref.source_ref for ref in self.references]


def _ordered_unique(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def _character_confidence(text: str, row_score: int) -> tuple[float, list[str]]:
    evidence: list[str] = []
    strong = ("夏目蓝", "蓝蓝")
    soft = ("笨蛋", "好烦", "哼", "嘛", "啦", "呀", "呢", "行", "知道", "别")
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


def _classify_intents(text: str) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    intents: list[str] = []
    emotions: list[str] = []
    moves: list[str] = []
    rhythms: list[str] = []
    cues: list[str] = []

    if _contains_any(text, ("見えて", "無視", "我来了", "看见", "不理", "返事", "叫")):
        intents.append("presence")
        moves.extend(("clingy_presence", "pull_attention"))
    if _contains_any(text, ("ごちそう", "おごり", "奢り", "请客", "吃饭", "カップ麺", "ハンバーグ", "晚饭")):
        intents.append("bored")
        moves.extend(("action_first", "food_motive"))
    if _contains_any(text, ("手伝", "支え", "照顾", "帮", "心配", "頼って", "看病", "買い出し", "管你")):
        intents.append("help")
        moves.extend(("take_charge", "care_behind_tease"))
        emotions.append("care")
    if _contains_any(text, ("傷", "伤心", "伤人", "聞きたくない", "へこむ", "悲しい", "被你", "别说")):
        intents.append("boundary")
        emotions.extend(("hurt", "wounded"))
        moves.extend(("wounded_pushback", "soft_after_sting"))
    if _contains_any(text, ("うっさい", "うっせ", "烦", "笨蛋", "バカ", "ふざけんな", "兄貴面")):
        intents.append("annoyed")
        emotions.extend(("annoyed", "angry_light"))
        moves.extend(("quick_rebuttal", "familiar_pushback"))
    if _contains_any(text, ("かわいい", "可爱", "褒", "照れ", "害羞")):
        intents.append("praise")
        emotions.append("shy")
        moves.append("shy_deflection")
    if _contains_any(text, ("待って", "もっかい", "为什么", "なんで", "怎么", "なに", "聞け", "说来")):
        moves.append("question_chain")
        rhythms.append("rapid_followup")
    if _contains_any(text, ("フフン", "哼", "へへ", "嘿嘿")):
        moves.append("smug_tease")
        emotions.append("playful")
    if _contains_any(text, ("大丈夫", "没事", "よかった", "最后没事", "無理しなくていい")):
        intents.append("reassurance")
        moves.append("soft_reassurance")
        emotions.append("soft")
    if _contains_any(text, ("好き", "大好き", "恋人", "情侣", "永远", "ずっと")):
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
        cues.append("停顿/照れ/犹豫")
    if "～" in text or "ー" in text:
        rhythms.append("elongation")
        cues.append("拖长音")
    if _contains_any(text, ("じゃん", "でしょ", "嘛", "啦", "呀", "呢")):
        cues.append("口语句尾")

    if not intents:
        intents.append("daily")
    if not emotions:
        emotions.append("playful" if any(move in moves for move in ("smug_tease", "familiar_pushback")) else "neutral")
    if not moves:
        moves.append("quick_reaction")
    return intents, emotions, moves, rhythms, cues


def _relationship_stages_for(intents: Iterable[str], emotions: Iterable[str]) -> tuple[str, ...]:
    stages = ["familiar", "trusted_friend", "close_friend"]
    intent_set = set(intents)
    emotion_set = set(emotions)
    if {"presence", "daily", "bored"}.intersection(intent_set):
        stages.extend(("new_acquaintance", "familiar"))
    if {"help", "reassurance"}.intersection(intent_set) or {"care", "soft"}.intersection(emotion_set):
        stages.extend(("familiar", "trusted_friend", "close_friend", "lover_early", "lover_stable"))
    if {"annoyed", "boundary"}.intersection(intent_set) or {"hurt", "wounded", "annoyed"}.intersection(emotion_set):
        stages.extend(("stranger", "new_acquaintance", "familiar", "trusted_friend", "close_friend", "ambiguous"))
    if "intimacy" in intent_set or "deep_affection" in emotion_set:
        stages.extend(("ambiguous", "pre_confession", "lover_early", "lover_stable"))
    return _ordered_unique(stages)


def _length_ok(text: str, lang: str) -> bool:
    length = len(text)
    if lang == "ja":
        return 4 <= length <= 120
    return 4 <= length <= 90


def style_reference_from_raw_row(row: Mapping[str, Any], *, lang_hint: str = "") -> StyleReference | None:
    text = _clean_text(row.get("text"))
    if not text:
        return None
    kind = str(row.get("kind") or "").strip()
    if kind and kind != "dialogue":
        return None
    if not bool(row.get("daily_style_safe", True)):
        return None
    lang = infer_reference_lang(text, source_hint=lang_hint or str(row.get("file") or ""))
    if not _length_ok(text, lang):
        return None
    row_score = int(row.get("sora_candidate_score") or 0)
    character_confidence, evidence = _character_confidence(text, row_score)
    if row_score < 3 and character_confidence < 0.32:
        return None
    intents, emotions, moves, rhythms, cues = _classify_intents(text)
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
    source_ref = normalize_source_ref(row.get("file"), row.get("index"))
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    ref_id = f"style_reference::{lang}::{digest}"
    return StyleReference(
        ref_id=ref_id,
        lang=lang,
        source_ref=source_ref,
        source_digest=digest,
        confidence=confidence,
        character_confidence=character_confidence,
        intent_tags=_ordered_unique(intents),
        emotion_tags=_ordered_unique(emotions),
        style_moves=_ordered_unique(moves),
        rhythm_tags=_ordered_unique(rhythms),
        lexical_cues=_ordered_unique(cues),
        relationship_stages=_relationship_stages_for(intents, emotions),
        evidence=_ordered_unique(evidence),
    )


class StyleReferenceBank:
    def __init__(self, references: Iterable[StyleReference] = ()) -> None:
        self.references: tuple[StyleReference, ...] = tuple(references)

    def select(self, query: StyleReferenceQuery) -> StyleReferenceSelection:
        lang = str(query.lang or "zh").strip() or "zh"
        candidates = [ref for ref in self.references if ref.lang == lang]
        if not candidates:
            return StyleReferenceSelection(query=query, degraded=True, reason=f"NO_{lang.upper()}_REFERENCES")
        degraded = False
        reason = ""
        intent_set = set(query.intent_tags)
        emotion_set = set(query.emotion_tags)
        move_set = set(query.style_moves)
        stage = str(query.relationship_stage or "").strip()

        def score(ref: StyleReference) -> float:
            value = float(ref.confidence)
            value += len(intent_set.intersection(ref.intent_tags)) * 0.35
            value += len(emotion_set.intersection(ref.emotion_tags)) * 0.25
            value += len(move_set.intersection(ref.style_moves)) * 0.2
            if stage and stage in ref.relationship_stages:
                value += 0.12
            if intent_set and not intent_set.intersection(ref.intent_tags):
                value -= 0.2
            return value

        ranked = sorted(candidates, key=lambda ref: (score(ref), ref.confidence, ref.ref_id), reverse=True)
        limit = max(1, min(int(query.limit or 5), 8))
        selected = tuple(ranked[:limit])
        return StyleReferenceSelection(references=selected, query=query, degraded=degraded, reason=reason)


def load_style_reference_bank(path: Path | str | None = None) -> StyleReferenceBank:
    bank_path = Path(path or os.environ.get("HOST_STYLE_REFERENCE_BANK_PATH") or DEFAULT_STYLE_BANK_PATH)
    if not bank_path.exists():
        return StyleReferenceBank()
    refs: list[StyleReference] = []
    with bank_path.open("r", encoding="utf-8-sig") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, Mapping):
                ref = StyleReference.from_dict(payload)
                if ref.ref_id and ref.lang:
                    refs.append(ref)
    return StyleReferenceBank(refs)


_BANK_CACHE: dict[str, StyleReferenceBank] = {}


def get_style_reference_bank(path: Path | str | None = None) -> StyleReferenceBank:
    bank_path = Path(path or os.environ.get("HOST_STYLE_REFERENCE_BANK_PATH") or DEFAULT_STYLE_BANK_PATH)
    key = str(bank_path.resolve())
    cached = _BANK_CACHE.get(key)
    if cached is None:
        cached = load_style_reference_bank(bank_path)
        _BANK_CACHE[key] = cached
    return cached


def clear_style_reference_bank_cache() -> None:
    _BANK_CACHE.clear()


def render_style_guidance(selection: StyleReferenceSelection, *, render_profile: str) -> str:
    if not selection.references:
        return ""
    moves = _ordered_unique(move for ref in selection.references for move in ref.style_moves)
    emotions = _ordered_unique(emotion for ref in selection.references for emotion in ref.emotion_tags)
    rhythms = _ordered_unique(rhythm for ref in selection.references for rhythm in ref.rhythm_tags)
    cues = _ordered_unique(cue for ref in selection.references for cue in ref.lexical_cues)
    refs = selection.source_refs()[:5]
    lang_label = "中文语音" if render_profile in {"zh_voice", "voice"} else "中文文字"
    lines = [
        f"语言轨道：{lang_label}。",
        "来源：中文风格样本派生特征；禁止照抄或复述样本文本。",
        "可用动作：" + (" / ".join(moves[:8]) or "quick_reaction"),
        "情绪倾向：" + (" / ".join(emotions[:6]) or "neutral"),
    ]
    if rhythms or cues:
        lines.append("节奏线索：" + " / ".join((rhythms + cues)[:8]))
    if selection.query.intent_tags:
        lines.append("本轮匹配场景：" + " / ".join(selection.query.intent_tags[:5]))
    if selection.query.emotion_tags:
        lines.append("本轮匹配情绪：" + " / ".join(selection.query.emotion_tags[:5]))
    lines.append("运行称呼必须服从 Soul 和关系阶段：未熟时用你或省略称呼，熟悉后才可自然使用尼尼。")
    lines.append("source_refs=" + ", ".join(refs))
    return "\n".join(lines)


__all__ = [
    "DEFAULT_STYLE_BANK_PATH",
    "StyleReference",
    "StyleReferenceBank",
    "StyleReferenceQuery",
    "StyleReferenceSelection",
    "clear_style_reference_bank_cache",
    "get_style_reference_bank",
    "infer_reference_lang",
    "load_style_reference_bank",
    "render_style_guidance",
    "style_reference_from_raw_row",
]
