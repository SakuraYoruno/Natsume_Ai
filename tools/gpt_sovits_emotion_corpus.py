from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import wave
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_GPT_SOVITS_ROOT = Path(r"D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50")
DEFAULT_PROJECT = "Natsume_Ai_Xilian_zh_clean_lively"
DEFAULT_BALANCED_PROJECT = "Natsume_Ai_Xilian_zh_emotion_balanced"
LIST_NAME = "0-source.list"
DATASET_MANIFEST_NAME = "dataset_manifest.json"
OUTPUT_DIR_NAME = "emotion_corpus"

EMOTION_ORDER = [
    "happy",
    "playful",
    "loving",
    "comfort",
    "sad",
    "nostalgic",
    "anxious",
    "serious",
    "neutral",
]

KEYWORD_RULES: dict[str, tuple[tuple[str, float], ...]] = {
    "happy": (
        ("开心", 2.2),
        ("欢笑", 2.0),
        ("笑着", 1.7),
        ("美好", 1.6),
        ("太好了", 2.4),
        ("期待", 1.8),
        ("天堂", 1.5),
        ("美丽", 1.2),
        ("漂亮", 1.2),
        ("乐观", 1.6),
        ("开朗", 1.6),
        ("心跳加速", 1.7),
        ("浪漫", 1.2),
        ("梦实现", 1.8),
    ),
    "playful": (
        ("嘻", 2.0),
        ("嘿嘿", 2.0),
        ("哎呀", 1.7),
        ("调皮", 2.2),
        ("玩笑", 1.8),
        ("上当", 1.8),
        ("人设", 1.6),
        ("冒冒失失", 1.6),
        ("可爱", 1.5),
        ("逗", 1.4),
        ("捉弄", 1.7),
        ("恶作剧", 1.9),
    ),
    "loving": (
        ("喜欢", 2.0),
        ("爱你", 2.0),
        ("爱着", 1.8),
        ("爱意", 1.8),
        ("亲爱", 1.7),
        ("亲", 1.4),
        ("怦怦", 1.8),
        ("想我", 1.8),
        ("想你", 1.8),
        ("约好", 1.7),
        ("甜言蜜语", 2.1),
        ("珍藏", 1.6),
        ("温柔", 1.6),
        ("拥抱", 1.6),
        ("双人时间", 1.7),
        ("被你的目光吸引", 2.2),
    ),
    "comfort": (
        ("没关系", 2.2),
        ("别急", 2.1),
        ("别担心", 2.2),
        ("陪", 1.5),
        ("相信", 1.5),
        ("呵护", 2.0),
        ("保护", 1.7),
        ("慢慢", 1.5),
        ("安心", 2.0),
        ("没事", 1.8),
        ("对不对", 1.1),
    ),
    "sad": (
        ("感伤", 2.2),
        ("伤心", 2.2),
        ("别走", 2.2),
        ("不要走", 2.4),
        ("骗", 1.7),
        ("没力气", 1.8),
        ("孤", 1.4),
        ("困", 1.0),
        ("忘了", 1.5),
        ("道别", 2.0),
        ("眼睁睁", 1.8),
        ("痛苦", 2.0),
        ("哭", 1.9),
    ),
    "nostalgic": (
        ("回忆", 2.0),
        ("怀念", 2.1),
        ("童年", 1.9),
        ("过去", 1.4),
        ("故乡", 1.8),
        ("往昔", 2.0),
        ("记得", 1.4),
        ("好久不见", 2.0),
        ("好久", 1.5),
        ("那时", 1.6),
        ("曾经", 1.6),
        ("熟悉", 1.4),
    ),
    "anxious": (
        ("什么动静", 2.3),
        ("小心", 2.1),
        ("不安", 2.2),
        ("担心", 1.8),
        ("急匆匆", 1.8),
        ("吓", 1.6),
        ("危险", 2.0),
        ("千万", 1.6),
        ("糟糕", 1.8),
        ("快跑", 2.0),
    ),
    "serious": (
        ("必须", 2.0),
        ("绝对", 1.7),
        ("坚定", 1.8),
        ("不能", 1.4),
        ("命运", 1.5),
        ("神谕", 1.9),
        ("责任", 1.9),
        ("答案", 1.4),
        ("真相", 1.5),
        ("代价", 1.6),
    ),
}

INTERJECTION_RE = re.compile(r"(呀|啦|呢|喔|哟|唔|嗯|哎呀|嘻|嘿嘿)")


@dataclass(frozen=True)
class SourceListEntry:
    wav_path: Path
    speaker: str
    language: str
    text: str
    source_line: str


@dataclass(frozen=True)
class EmotionScore:
    primary_emotion: str
    scores: dict[str, float]
    confidence: float
    evidence: list[str]


@dataclass(frozen=True)
class EmotionCorpusRecord:
    status: str
    wav_path: str
    speaker: str | None
    language: str | None
    text: str
    duration_s: float
    primary_emotion: str
    confidence: float
    expressive_score: float
    scores: dict[str, float]
    evidence: list[str]
    training_role: str
    source_line: str | None = None
    reason: str | None = None
    recommended_action: str | None = None


def _normalize_text(text: str) -> str:
    text = (text or "").replace("\ufeff", "").replace("|", "｜")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _round(value: float) -> float:
    return round(value, 4)


def _safe_project_name(project: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", project or ""):
        raise ValueError("project must be ASCII and contain only letters, digits, '.', '_' or '-'")
    return project


def _ensure_output_scope(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if root_resolved != candidate_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"refusing to write outside GPT-SoVITS root: {candidate}")


def _wav_duration_s(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as handle:
            frames = int(handle.getnframes())
            sample_rate = int(handle.getframerate())
        return _round(frames / sample_rate) if sample_rate else 0.0
    except Exception:
        return 0.0


def parse_source_list(path: Path) -> list[SourceListEntry]:
    if not path.is_file():
        raise FileNotFoundError(f"source list does not exist: {path}")

    entries: list[SourceListEntry] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"bad source list line {line_number}: expected 4 columns")
        wav_path, speaker, language, text = parts
        entries.append(
            SourceListEntry(
                wav_path=Path(wav_path),
                speaker=speaker,
                language=language,
                text=_normalize_text(text),
                source_line=line,
            )
        )
    return entries


def classify_text_emotion(text: str) -> EmotionScore:
    normalized = _normalize_text(text)
    scores: dict[str, float] = {emotion: 0.0 for emotion in EMOTION_ORDER if emotion != "neutral"}
    evidence: list[str] = []

    for emotion, rules in KEYWORD_RULES.items():
        for keyword, weight in rules:
            occurrences = normalized.count(keyword)
            if occurrences:
                scores[emotion] += weight * occurrences
                evidence.append(f"{emotion}:{keyword}x{occurrences}")

    exclamation_count = normalized.count("!") + normalized.count("！")
    question_count = normalized.count("?") + normalized.count("？")
    ellipsis_count = normalized.count("…") + normalized.count("...")
    if exclamation_count:
        scores["happy"] += min(0.25 * exclamation_count, 0.75)
        scores["playful"] += min(0.15 * exclamation_count, 0.45)
        evidence.append(f"punctuation:exclamationx{exclamation_count}")
    if question_count and any(marker in normalized for marker in ("是不是", "好吗", "对吧", "呢")):
        scores["playful"] += min(0.2 * question_count, 0.6)
        evidence.append(f"punctuation:questionx{question_count}")
    if ellipsis_count and any(marker in normalized for marker in ("好久", "回忆", "记得", "梦", "心")):
        scores["nostalgic"] += min(0.25 * ellipsis_count, 0.75)
        scores["loving"] += min(0.15 * ellipsis_count, 0.45)
        evidence.append(f"punctuation:ellipsisx{ellipsis_count}")

    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    top_emotion, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < 1.0:
        primary = "neutral"
        confidence = 1.0 if top_score == 0.0 else _round(1.0 - min(top_score, 0.99))
    else:
        primary = top_emotion
        confidence = _round((top_score - second_score) / max(top_score, 1.0))

    output_scores = {emotion: _round(value) for emotion, value in scores.items() if value > 0}
    output_scores.setdefault("neutral", 1.0 if primary == "neutral" else 0.0)
    return EmotionScore(
        primary_emotion=primary,
        scores=dict(sorted(output_scores.items(), key=lambda pair: pair[0])),
        confidence=confidence,
        evidence=evidence[:12],
    )


def expressive_score(text: str, duration_s: float, emotion_score: EmotionScore) -> float:
    normalized = _normalize_text(text)
    score = 0.0
    top_score = max((value for emotion, value in emotion_score.scores.items() if emotion != "neutral"), default=0.0)
    score += min(top_score, 4.0)

    punctuation_count = sum(normalized.count(char) for char in ("！", "!", "？", "?", "…", "。", "，", "、", "——"))
    score += min(punctuation_count * 0.18, 1.8)
    score += min(len(INTERJECTION_RE.findall(normalized)) * 0.25, 1.5)

    text_len = len(normalized)
    if 8 <= text_len <= 55:
        score += 1.0
    elif 56 <= text_len <= 95:
        score += 0.6
    elif text_len > 95:
        score += 0.25

    if 3.0 <= duration_s <= 10.0:
        score += 1.0
    elif 2.0 <= duration_s <= 14.0:
        score += 0.5
    elif duration_s > 14.0:
        score += 0.25

    if any(mark in normalized for mark in ("「", "」", "\"", "'")):
        score += 0.25
    if "♪" in normalized or "♫" in normalized:
        score += 0.5
    return _round(score)


def _training_role(record: EmotionCorpusRecord) -> str:
    if record.status != "prepared":
        if record.recommended_action and not record.recommended_action.startswith("exclude"):
            return "recovery_candidate"
        return "excluded"
    if record.primary_emotion != "neutral" and record.expressive_score >= 4.0 and record.confidence >= 0.35:
        return "reference_candidate"
    return "train"


def _recommended_action_for_skipped(reason: str, text: str, duration_s: float, score: float) -> str | None:
    if re.search(r"[{}]", text):
        return "exclude_until_placeholder_rewritten"
    if reason == "too_long" and score >= 3.0:
        return "split_at_punctuation"
    if reason == "music_symbol_text" and score >= 2.5:
        return "sanitize_music_symbol_or_keep_as_style_reference"
    if reason == "too_short" and score >= 4.0 and duration_s >= 1.0:
        return "review_as_short_reference_only"
    return None


def _record_from_prepared(entry: SourceListEntry) -> EmotionCorpusRecord:
    duration_s = _wav_duration_s(entry.wav_path)
    emotion = classify_text_emotion(entry.text)
    record = EmotionCorpusRecord(
        status="prepared",
        wav_path=str(entry.wav_path),
        speaker=entry.speaker,
        language=entry.language,
        text=entry.text,
        duration_s=duration_s,
        primary_emotion=emotion.primary_emotion,
        confidence=emotion.confidence,
        expressive_score=expressive_score(entry.text, duration_s, emotion),
        scores=emotion.scores,
        evidence=emotion.evidence,
        training_role="train",
        source_line=entry.source_line,
    )
    return record.__class__(**(asdict(record) | {"training_role": _training_role(record)}))


def _record_from_skipped(item: dict[str, Any]) -> EmotionCorpusRecord:
    text = _normalize_text(str(item.get("text") or ""))
    duration_s = float(item.get("duration_s") or 0.0)
    reason = str(item.get("reason") or "unknown")
    emotion = classify_text_emotion(text)
    score = expressive_score(text, duration_s, emotion)
    action = _recommended_action_for_skipped(reason, text, duration_s, score)
    record = EmotionCorpusRecord(
        status="skipped",
        wav_path=str(item.get("wav_path") or ""),
        speaker=None,
        language=None,
        text=text,
        duration_s=_round(duration_s),
        primary_emotion=emotion.primary_emotion,
        confidence=emotion.confidence,
        expressive_score=score,
        scores=emotion.scores,
        evidence=emotion.evidence,
        training_role="excluded",
        reason=reason,
        recommended_action=action,
    )
    return record.__class__(**(asdict(record) | {"training_role": _training_role(record)}))


def _candidate_sort_key(record: EmotionCorpusRecord) -> tuple[float, float, float, str]:
    duration_fit = 1.0 if 3.0 <= record.duration_s <= 10.0 else 0.0
    return (-record.expressive_score, -record.confidence, -duration_fit, record.wav_path)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_from_json(payload: dict[str, Any]) -> EmotionCorpusRecord:
    return EmotionCorpusRecord(
        status=str(payload.get("status") or ""),
        wav_path=str(payload.get("wav_path") or ""),
        speaker=payload.get("speaker"),
        language=payload.get("language"),
        text=_normalize_text(str(payload.get("text") or "")),
        duration_s=float(payload.get("duration_s") or 0.0),
        primary_emotion=str(payload.get("primary_emotion") or "neutral"),
        confidence=float(payload.get("confidence") or 0.0),
        expressive_score=float(payload.get("expressive_score") or 0.0),
        scores=dict(payload.get("scores") or {}),
        evidence=list(payload.get("evidence") or []),
        training_role=str(payload.get("training_role") or "train"),
        source_line=payload.get("source_line"),
        reason=payload.get("reason"),
        recommended_action=payload.get("recommended_action"),
    )


def _load_emotion_records(emotion_manifest_path: Path) -> list[EmotionCorpusRecord]:
    if not emotion_manifest_path.is_file():
        raise FileNotFoundError(f"emotion manifest does not exist: {emotion_manifest_path}")
    payload = json.loads(emotion_manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("emotion manifest must be a JSON array")
    return [_record_from_json(item) for item in payload]


def _balanced_sort_key(record: EmotionCorpusRecord) -> tuple[float, float, float, float, str]:
    duration_fit = 1.0 if 3.0 <= record.duration_s <= 10.0 else 0.0
    duration_center_distance = abs(record.duration_s - 6.0)
    return (
        -record.expressive_score,
        -record.confidence,
        -duration_fit,
        duration_center_distance,
        record.wav_path,
    )


def _selection_counts(records: list[EmotionCorpusRecord]) -> dict[str, int]:
    counts = Counter(record.primary_emotion for record in records)
    return {emotion: counts.get(emotion, 0) for emotion in EMOTION_ORDER}


def _duration_minutes_by_emotion_from_records(records: list[EmotionCorpusRecord]) -> dict[str, float]:
    return {
        emotion: _round(sum(record.duration_s for record in records if record.primary_emotion == emotion) / 60.0)
        for emotion in EMOTION_ORDER
    }


def build_balanced_training_project(
    *,
    gpt_sovits_root: Path = DEFAULT_GPT_SOVITS_ROOT,
    source_project: str = DEFAULT_PROJECT,
    output_project: str = DEFAULT_BALANCED_PROJECT,
    target_emotion_count: int = 120,
    target_neutral_ratio: float = 0.30,
    max_neutral_count: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    source_project = _safe_project_name(source_project)
    output_project = _safe_project_name(output_project)
    if not gpt_sovits_root.is_dir():
        raise FileNotFoundError(f"GPT-SoVITS root does not exist: {gpt_sovits_root}")
    if not 0.0 <= target_neutral_ratio < 1.0:
        raise ValueError("target_neutral_ratio must be >= 0 and < 1")
    if target_emotion_count < 0:
        raise ValueError("target_emotion_count must be >= 0")
    if max_neutral_count is not None and max_neutral_count < 0:
        raise ValueError("max_neutral_count must be >= 0")

    source_log_root = gpt_sovits_root / "logs" / source_project
    source_emotion_root = source_log_root / OUTPUT_DIR_NAME
    emotion_manifest_path = source_emotion_root / "emotion_manifest.json"
    recovery_candidates_path = source_emotion_root / "recovery_candidates.json"
    records = _load_emotion_records(emotion_manifest_path)
    prepared = [record for record in records if record.status == "prepared" and record.source_line]
    if not prepared:
        raise ValueError("no prepared records found in emotion manifest")

    selected_by_emotion: dict[str, list[EmotionCorpusRecord]] = {}
    for emotion in EMOTION_ORDER:
        emotion_records = sorted(
            (record for record in prepared if record.primary_emotion == emotion),
            key=_balanced_sort_key,
        )
        if emotion == "neutral":
            continue
        selected_by_emotion[emotion] = emotion_records[:target_emotion_count] if target_emotion_count else emotion_records

    non_neutral_selected_count = sum(len(items) for items in selected_by_emotion.values())
    if max_neutral_count is None:
        if target_neutral_ratio <= 0.0 or non_neutral_selected_count == 0:
            neutral_cap = 0
        else:
            neutral_cap = int(non_neutral_selected_count * target_neutral_ratio / (1.0 - target_neutral_ratio))
    else:
        neutral_cap = max_neutral_count
    neutral_records = sorted(
        (record for record in prepared if record.primary_emotion == "neutral"),
        key=_balanced_sort_key,
    )
    selected_by_emotion["neutral"] = neutral_records[:neutral_cap]

    selected: list[EmotionCorpusRecord] = []
    for emotion in EMOTION_ORDER:
        selected.extend(selected_by_emotion.get(emotion, []))
    if not selected:
        raise ValueError("balanced selection is empty")

    output_log_root = gpt_sovits_root / "logs" / output_project
    _ensure_output_scope(gpt_sovits_root, output_log_root)
    if output_log_root.exists():
        if not overwrite:
            raise FileExistsError(f"output project already exists: {output_log_root}; pass --overwrite to replace generated files")
        _ensure_output_scope(gpt_sovits_root, output_log_root)
        shutil.rmtree(output_log_root)
    output_log_root.mkdir(parents=True, exist_ok=True)
    review_root = output_log_root / "emotion_balance"
    selected_lists_root = review_root / "source_lists"
    selected_lists_root.mkdir(parents=True, exist_ok=True)

    selected_lines = [record.source_line or "" for record in selected]
    (output_log_root / LIST_NAME).write_text("\n".join(selected_lines) + "\n", encoding="utf-8")
    for emotion in EMOTION_ORDER:
        lines = [record.source_line or "" for record in selected_by_emotion.get(emotion, [])]
        (selected_lists_root / f"{emotion}.list").write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")

    recovery_candidates: list[dict[str, Any]] = []
    if recovery_candidates_path.is_file():
        recovery_candidates = json.loads(recovery_candidates_path.read_text(encoding="utf-8-sig"))
    recovery_by_emotion: dict[str, list[dict[str, Any]]] = {emotion: [] for emotion in EMOTION_ORDER}
    for item in recovery_candidates:
        emotion = str(item.get("primary_emotion") or "neutral")
        recovery_by_emotion.setdefault(emotion, []).append(item)
    for emotion, items in recovery_by_emotion.items():
        items.sort(
            key=lambda item: (
                -float(item.get("expressive_score") or 0.0),
                -float(item.get("confidence") or 0.0),
                str(item.get("wav_path") or ""),
            )
        )

    available_counts = _selection_counts(prepared)
    selected_counts = _selection_counts(selected)
    shortages = {
        emotion: max(0, target_emotion_count - available_counts.get(emotion, 0))
        for emotion in EMOTION_ORDER
        if emotion != "neutral" and target_emotion_count
    }
    selected_non_neutral = sum(value for emotion, value in selected_counts.items() if emotion != "neutral")
    selected_neutral = selected_counts.get("neutral", 0)
    actual_neutral_ratio = _round(selected_neutral / len(selected)) if selected else 0.0
    manifest = {
        "schema_version": 1,
        "project": output_project,
        "derived_from_project": source_project,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "list_path": str(output_log_root / LIST_NAME),
        "source_emotion_manifest_path": str(emotion_manifest_path),
        "prepared_source_count": len(prepared),
        "selected_count": len(selected),
        "selected_non_neutral_count": selected_non_neutral,
        "selected_neutral_count": selected_neutral,
        "target_neutral_ratio": target_neutral_ratio,
        "actual_neutral_ratio": actual_neutral_ratio,
        "target_emotion_count": target_emotion_count,
        "max_neutral_count": max_neutral_count,
        "duration_minutes_selected": _round(sum(record.duration_s for record in selected) / 60.0),
        "selected_counts_by_emotion": selected_counts,
        "available_counts_by_emotion": available_counts,
        "shortage_to_target_by_emotion": shortages,
        "duration_minutes_by_emotion": _duration_minutes_by_emotion_from_records(selected),
        "source_list_files": {
            emotion: str(selected_lists_root / f"{emotion}.list")
            for emotion in EMOTION_ORDER
        },
        "notes": [
            "This derived project references prepared wav paths from the source project; it does not duplicate audio files.",
            "Neutral clips are capped so emotion-bearing clips dominate the retraining list.",
            "Shortages show emotions that need more reviewed or recovered material before a truly balanced expressive model.",
        ],
    }
    report = manifest | {
        "top_selected_by_emotion": {
            emotion: [
                {
                    "wav_path": record.wav_path,
                    "duration_s": record.duration_s,
                    "expressive_score": record.expressive_score,
                    "confidence": record.confidence,
                    "text": record.text,
                }
                for record in selected_by_emotion.get(emotion, [])[:8]
            ]
            for emotion in EMOTION_ORDER
        },
        "manual_recovery_queue_by_emotion": {
            emotion: items[:12] for emotion, items in recovery_by_emotion.items() if items
        },
    }

    _write_json(output_log_root / DATASET_MANIFEST_NAME, manifest)
    _write_json(review_root / "emotion_balance_report.json", report)
    _write_json(review_root / "selected_records.json", [asdict(record) for record in selected])
    _write_json(review_root / "manual_recovery_queue_by_emotion.json", report["manual_recovery_queue_by_emotion"])
    return manifest


def build_emotion_corpus(
    *,
    gpt_sovits_root: Path = DEFAULT_GPT_SOVITS_ROOT,
    project: str = DEFAULT_PROJECT,
    output_dir: Path | None = None,
    top_reference_count: int = 12,
    source_name: str | None = None,
) -> dict[str, Any]:
    project = _safe_project_name(project)
    log_root = gpt_sovits_root / "logs" / project
    source_list_path = log_root / LIST_NAME
    dataset_manifest_path = log_root / DATASET_MANIFEST_NAME
    if not gpt_sovits_root.is_dir():
        raise FileNotFoundError(f"GPT-SoVITS root does not exist: {gpt_sovits_root}")
    if not dataset_manifest_path.is_file():
        raise FileNotFoundError(f"dataset manifest does not exist: {dataset_manifest_path}")

    entries = parse_source_list(source_list_path)
    manifest = json.loads(dataset_manifest_path.read_text(encoding="utf-8-sig"))
    skipped_items = list(manifest.get("skipped") or [])

    prepared_records = [_record_from_prepared(entry) for entry in entries]
    skipped_records = [_record_from_skipped(item) for item in skipped_items]
    records = prepared_records + skipped_records

    output_root = output_dir or (log_root / OUTPUT_DIR_NAME)
    source_lists_root = output_root / "source_lists"
    source_lists_root.mkdir(parents=True, exist_ok=True)

    by_emotion: dict[str, list[EmotionCorpusRecord]] = defaultdict(list)
    for record in prepared_records:
        by_emotion[record.primary_emotion].append(record)

    source_list_files: dict[str, str] = {}
    for emotion in EMOTION_ORDER:
        path = source_lists_root / f"{emotion}.list"
        lines = [record.source_line or "" for record in sorted(by_emotion.get(emotion, []), key=lambda item: item.wav_path)]
        path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
        source_list_files[emotion] = str(path)

    reference_candidates: dict[str, list[dict[str, Any]]] = {}
    for emotion in EMOTION_ORDER:
        candidates = [
            record
            for record in prepared_records
            if record.primary_emotion == emotion
            and record.training_role == "reference_candidate"
            and 2.0 <= record.duration_s <= 12.0
        ]
        reference_candidates[emotion] = [
            asdict(record)
            for record in sorted(candidates, key=_candidate_sort_key)[:top_reference_count]
        ]

    recovery_candidates = [
        asdict(record)
        for record in sorted(
            (record for record in skipped_records if record.training_role == "recovery_candidate"),
            key=_candidate_sort_key,
        )
    ]

    emotion_counts_prepared = Counter(record.primary_emotion for record in prepared_records)
    emotion_counts_skipped = Counter(record.primary_emotion for record in skipped_records)
    role_counts = Counter(record.training_role for record in records)
    skip_reason_counts = Counter(record.reason or "ok" for record in skipped_records)
    duration_minutes_by_emotion: dict[str, float] = {}
    for emotion in EMOTION_ORDER:
        duration_minutes_by_emotion[emotion] = _round(
            sum(record.duration_s for record in prepared_records if record.primary_emotion == emotion) / 60.0
        )

    summary = {
        "schema_version": 1,
        "project": project,
        "source_name": source_name or manifest.get("source_name"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpt_sovits_root": str(gpt_sovits_root),
        "source_list_path": str(source_list_path),
        "dataset_manifest_path": str(dataset_manifest_path),
        "output_dir": str(output_root),
        "prepared_count": len(prepared_records),
        "skipped_count": len(skipped_records),
        "total_records": len(records),
        "emotion_counts_prepared": {emotion: emotion_counts_prepared.get(emotion, 0) for emotion in EMOTION_ORDER},
        "emotion_counts_skipped": {emotion: emotion_counts_skipped.get(emotion, 0) for emotion in EMOTION_ORDER},
        "duration_minutes_by_emotion": duration_minutes_by_emotion,
        "role_counts": dict(sorted(role_counts.items())),
        "skip_reason_counts": dict(sorted(skip_reason_counts.items())),
        "reference_candidate_count_by_emotion": {
            emotion: len(reference_candidates.get(emotion, [])) for emotion in EMOTION_ORDER
        },
        "recovery_candidate_count_by_reason": dict(
            sorted(Counter(record["reason"] or "unknown" for record in recovery_candidates).items())
        ),
        "source_list_files": source_list_files,
        "files": {
            "emotion_manifest": str(output_root / "emotion_manifest.json"),
            "emotion_summary": str(output_root / "emotion_summary.json"),
            "reference_candidates": str(output_root / "reference_candidates.json"),
            "recovery_candidates": str(output_root / "recovery_candidates.json"),
        },
        "notes": [
            "Emotion labels are deterministic corpus tags for balancing and reference selection; GPT-SoVITS source lists remain the native wav|speaker|language|text format.",
            "Skipped recovery candidates still need manual audio/text review before retraining.",
        ],
    }

    manifest_payload = [asdict(record) for record in records]
    _write_json(output_root / "emotion_manifest.json", manifest_payload)
    _write_json(output_root / "emotion_summary.json", summary)
    _write_json(output_root / "reference_candidates.json", reference_candidates)
    _write_json(output_root / "recovery_candidates.json", recovery_candidates)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build emotion-tagged corpus views for a GPT-SoVITS project.")
    sub = parser.add_subparsers(dest="command", required=True)

    build_p = sub.add_parser("build", help="Tag an existing GPT-SoVITS source list and manifest by emotion.")
    build_p.add_argument("--gpt-sovits-root", type=Path, default=DEFAULT_GPT_SOVITS_ROOT)
    build_p.add_argument("--project", default=DEFAULT_PROJECT)
    build_p.add_argument("--output-dir", type=Path)
    build_p.add_argument("--source-name")
    build_p.add_argument("--top-reference-count", type=int, default=12)

    balance_p = sub.add_parser("balance", help="Create a derived emotion-balanced GPT-SoVITS training list.")
    balance_p.add_argument("--gpt-sovits-root", type=Path, default=DEFAULT_GPT_SOVITS_ROOT)
    balance_p.add_argument("--source-project", default=DEFAULT_PROJECT)
    balance_p.add_argument("--output-project", default=DEFAULT_BALANCED_PROJECT)
    balance_p.add_argument("--target-emotion-count", type=int, default=120)
    balance_p.add_argument("--target-neutral-ratio", type=float, default=0.30)
    balance_p.add_argument("--max-neutral-count", type=int)
    balance_p.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "build":
        print(
            json.dumps(
                build_emotion_corpus(
                    gpt_sovits_root=args.gpt_sovits_root,
                    project=args.project,
                    output_dir=args.output_dir,
                    source_name=args.source_name,
                    top_reference_count=args.top_reference_count,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "balance":
        print(
            json.dumps(
                build_balanced_training_project(
                    gpt_sovits_root=args.gpt_sovits_root,
                    source_project=args.source_project,
                    output_project=args.output_project,
                    target_emotion_count=args.target_emotion_count,
                    target_neutral_ratio=args.target_neutral_ratio,
                    max_neutral_count=args.max_neutral_count,
                    overwrite=bool(args.overwrite),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
