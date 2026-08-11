"""Mine acoustically similar GPT-SoVITS clips from approved style seeds.

This tool deliberately does not infer a delivery style from transcript
keywords.  It combines an emotion2vec utterance embedding with low-level
prosody (pitch, energy, speaking rate, and pauses), then emits a human-listening
queue.  A style config may restrict the candidate pool by transcript shape,
but those restrictions only remove obviously unsuitable material; they never
turn a candidate into an automatic training label.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_MODEL_ID = "iic/emotion2vec_plus_base"
DEFAULT_ROMANCE_SEEDS: dict[str, tuple[str, ...]] = {
    "ROMANTIC_TENDER": ("xilian_00048", "xilian_01101"),
    "SHY_AFFECTION": ("xilian_00025",),
    "FLIRTING": ("xilian_00003",),
    "LONGING": ("xilian_01292",),
    "QUIET_INTIMACY": ("xilian_01162",),
}

PROSODY_FEATURE_WEIGHTS: dict[str, float] = {
    "f0_median_st": 0.65,
    "f0_range_st": 1.05,
    "f0_std_st": 1.0,
    "f0_delta_st": 0.85,
    "f0_slope_st_per_s": 0.45,
    "rms_mean_db": 0.45,
    "rms_std_db": 0.8,
    "rms_range_db": 0.7,
    "pause_ratio": 1.0,
    "max_pause_s": 0.8,
    "chars_per_s": 1.0,
    "duration_s": 0.25,
}


@dataclass(frozen=True)
class SourceEntry:
    clip_id: str
    wav_path: str
    speaker: str
    language: str
    text: str


@dataclass(frozen=True)
class RankedCandidate:
    rank: int
    clip_id: str
    wav_path: str
    text: str
    score: float
    embedding_similarity: float
    prosody_similarity: float
    top_emotion_label: str
    top_emotion_score: float
    prosody: dict[str, float]


def parse_source_list(path: Path) -> list[SourceEntry]:
    entries: list[SourceEntry] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not raw.strip():
            continue
        parts = raw.split("|", 3)
        if len(parts) != 4:
            raise ValueError(f"bad source list line {line_number}: expected 4 columns")
        wav_path, speaker, language, text = (part.strip() for part in parts)
        clip_id = Path(wav_path).stem
        if not clip_id or not Path(wav_path).is_file():
            raise FileNotFoundError(f"source clip unavailable at line {line_number}: {wav_path}")
        entries.append(
            SourceEntry(
                clip_id=clip_id,
                wav_path=str(Path(wav_path).resolve()),
                speaker=speaker,
                language=language,
                text=text,
            )
        )
    if not entries:
        raise ValueError("source list is empty")
    return entries


def _source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized(vector: Any) -> Any:
    import numpy as np

    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    return arr if norm <= 1e-12 else arr / norm


def _robust_feature_matrix(
    prosody_by_id: Mapping[str, Mapping[str, float]],
    clip_ids: Sequence[str],
) -> tuple[Any, list[str]]:
    import numpy as np

    keys = list(PROSODY_FEATURE_WEIGHTS)
    raw = np.asarray(
        [[float(prosody_by_id[clip_id].get(key, 0.0)) for key in keys] for clip_id in clip_ids],
        dtype=np.float32,
    )
    median = np.median(raw, axis=0)
    q1 = np.percentile(raw, 25, axis=0)
    q3 = np.percentile(raw, 75, axis=0)
    scale = np.maximum(q3 - q1, 1e-4)
    standardized = (raw - median) / scale
    weights = np.sqrt(np.asarray([PROSODY_FEATURE_WEIGHTS[key] for key in keys], dtype=np.float32))
    return standardized * weights, keys


def rank_style_candidates(
    *,
    entries: Sequence[SourceEntry],
    embeddings_by_id: Mapping[str, Any],
    prosody_by_id: Mapping[str, Mapping[str, float]],
    emotion_labels_by_id: Mapping[str, tuple[str, float]],
    style_seeds: Mapping[str, Sequence[str]],
    top_k: int,
    min_duration_s: float = 2.5,
    max_duration_s: float = 10.0,
    eligible_clip_ids: set[str] | None = None,
) -> dict[str, list[RankedCandidate]]:
    """Rank candidates without converting the result into an automatic label."""

    import numpy as np

    if top_k <= 0:
        raise ValueError("top_k must be positive")
    entry_by_id = {entry.clip_id: entry for entry in entries}
    clip_ids = [entry.clip_id for entry in entries]
    missing = [clip_id for clip_id in clip_ids if clip_id not in embeddings_by_id or clip_id not in prosody_by_id]
    if missing:
        raise ValueError(f"missing analysis for {len(missing)} clips; first={missing[0]}")

    # All clips share one speaker, so the raw embedding is dominated by voice
    # identity.  Corpus-centering removes that common component and makes the
    # distance reflect delivery style instead of merely saying "same speaker".
    raw_embedding_matrix = np.stack(
        [np.asarray(embeddings_by_id[clip_id], dtype=np.float32).reshape(-1) for clip_id in clip_ids]
    )
    centered_embedding_matrix = raw_embedding_matrix - np.mean(raw_embedding_matrix, axis=0, keepdims=True)
    embedding_matrix = np.stack([_normalized(vector) for vector in centered_embedding_matrix])
    prosody_matrix, _ = _robust_feature_matrix(prosody_by_id, clip_ids)
    index_by_id = {clip_id: index for index, clip_id in enumerate(clip_ids)}
    rankings: dict[str, list[RankedCandidate]] = {}

    for style, raw_seed_ids in style_seeds.items():
        seed_ids = tuple(dict.fromkeys(str(item).strip() for item in raw_seed_ids if str(item).strip()))
        unknown = [seed_id for seed_id in seed_ids if seed_id not in index_by_id]
        if not seed_ids or unknown:
            raise ValueError(f"style {style} has invalid seeds: {unknown or 'none'}")
        seed_indexes = [index_by_id[seed_id] for seed_id in seed_ids]
        embedding_centroid = _normalized(np.mean(embedding_matrix[seed_indexes], axis=0))
        prosody_centroid = np.mean(prosody_matrix[seed_indexes], axis=0)
        candidates: list[RankedCandidate] = []

        for index, clip_id in enumerate(clip_ids):
            if clip_id in seed_ids:
                continue
            if eligible_clip_ids is not None and clip_id not in eligible_clip_ids:
                continue
            prosody = dict(prosody_by_id[clip_id])
            duration_s = float(prosody.get("duration_s", 0.0))
            if duration_s < min_duration_s or duration_s > max_duration_s:
                continue
            embedding_similarity = float(np.dot(embedding_matrix[index], embedding_centroid))
            distance = float(np.sqrt(np.mean(np.square(prosody_matrix[index] - prosody_centroid))))
            prosody_similarity = math.exp(-0.5 * distance)
            score = 0.8 * ((embedding_similarity + 1.0) / 2.0) + 0.2 * prosody_similarity
            label, label_score = emotion_labels_by_id.get(clip_id, ("", 0.0))
            entry = entry_by_id[clip_id]
            candidates.append(
                RankedCandidate(
                    rank=0,
                    clip_id=clip_id,
                    wav_path=entry.wav_path,
                    text=entry.text,
                    score=round(score, 6),
                    embedding_similarity=round(embedding_similarity, 6),
                    prosody_similarity=round(prosody_similarity, 6),
                    top_emotion_label=str(label),
                    top_emotion_score=round(float(label_score), 6),
                    prosody={key: round(float(value), 6) for key, value in prosody.items()},
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.clip_id))
        rankings[style] = [
            RankedCandidate(**(asdict(candidate) | {"rank": rank}))
            for rank, candidate in enumerate(candidates[:top_k], start=1)
        ]
    return rankings


def extract_prosody_features(wav_path: str, text: str) -> dict[str, float]:
    """Extract content-agnostic acoustic features with the voice-model runtime."""

    import librosa
    import numpy as np
    import pyworld
    import soundfile as sf

    audio, sample_rate = sf.read(wav_path, dtype="float32", always_2d=False)
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)
    if audio.size == 0 or sample_rate <= 0:
        raise ValueError(f"empty audio: {wav_path}")
    target_rate = 16_000
    if sample_rate != target_rate:
        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=target_rate)
        sample_rate = target_rate
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-6:
        audio = audio / peak

    frame_length = 400
    hop_length = 160
    rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length, center=True)[0]
    rms_db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    active_threshold = max(float(np.max(rms_db)) - 36.0, -52.0)
    active = rms_db >= active_threshold
    pause_ratio = 1.0 - float(np.mean(active)) if active.size else 0.0
    max_pause_frames = 0
    current_pause = 0
    for value in active:
        if bool(value):
            current_pause = 0
        else:
            current_pause += 1
            max_pause_frames = max(max_pause_frames, current_pause)

    audio64 = np.asarray(audio, dtype=np.float64)
    raw_f0, temporal = pyworld.dio(
        audio64,
        sample_rate,
        f0_floor=80.0,
        f0_ceil=520.0,
        frame_period=10.0,
    )
    f0 = pyworld.stonemask(audio64, raw_f0, temporal, sample_rate)
    voiced = f0[f0 > 1.0]
    if voiced.size:
        median_hz = float(np.median(voiced))
        semitones = 12.0 * np.log2(np.maximum(voiced, 1.0))
        f0_median_st = 12.0 * math.log2(max(median_hz, 1.0))
        f0_range_st = float(np.percentile(semitones, 90) - np.percentile(semitones, 10))
        f0_std_st = float(np.std(semitones))
        f0_delta_st = float(np.mean(np.abs(np.diff(semitones)))) if semitones.size > 1 else 0.0
        voiced_times = temporal[f0 > 1.0]
        if voiced_times.size > 1 and float(np.ptp(voiced_times)) > 1e-6:
            f0_slope = float(np.polyfit(voiced_times, semitones, 1)[0])
        else:
            f0_slope = 0.0
    else:
        f0_median_st = f0_range_st = f0_std_st = f0_delta_st = f0_slope = 0.0

    duration_s = float(audio.size / sample_rate)
    active_duration_s = max(duration_s * (1.0 - pause_ratio), 0.1)
    char_count = len(re.findall(r"[\u3400-\u9fffA-Za-z0-9]", text or ""))
    return {
        "duration_s": duration_s,
        "f0_median_st": f0_median_st,
        "f0_range_st": f0_range_st,
        "f0_std_st": f0_std_st,
        "f0_delta_st": f0_delta_st,
        "f0_slope_st_per_s": f0_slope,
        "voiced_ratio": float(np.mean(f0 > 1.0)) if f0.size else 0.0,
        "rms_mean_db": float(np.mean(rms_db[active])) if np.any(active) else float(np.mean(rms_db)),
        "rms_std_db": float(np.std(rms_db[active])) if np.any(active) else float(np.std(rms_db)),
        "rms_range_db": float(np.percentile(rms_db, 90) - np.percentile(rms_db, 10)),
        "pause_ratio": pause_ratio,
        "max_pause_s": float(max_pause_frames * hop_length / sample_rate),
        "chars_per_s": float(char_count / active_duration_s),
    }


def _load_or_extract_embeddings(
    *,
    entries: Sequence[SourceEntry],
    source_digest: str,
    output_dir: Path,
    model_id: str,
    hub: str,
    device: str,
    batch_size: int,
) -> tuple[dict[str, Any], dict[str, tuple[str, float]]]:
    import numpy as np

    cache_path = output_dir / "emotion_embeddings.npz"
    metadata_path = output_dir / "emotion_embeddings.meta.json"
    if cache_path.is_file() and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (
            metadata.get("source_sha256") == source_digest
            and metadata.get("model_id") == model_id
            and int(metadata.get("clip_count", -1)) == len(entries)
        ):
            with np.load(cache_path, allow_pickle=False) as cached:
                ids = [str(value) for value in cached["clip_ids"].tolist()]
                vectors = np.asarray(cached["embeddings"], dtype=np.float32)
                labels = [str(value) for value in cached["labels"].tolist()]
                scores = np.asarray(cached["label_scores"], dtype=np.float32)
            return (
                {clip_id: vectors[index] for index, clip_id in enumerate(ids)},
                {clip_id: (labels[index], float(scores[index])) for index, clip_id in enumerate(ids)},
            )

    from funasr import AutoModel

    log_path = output_dir / "emotion2vec.log"
    embeddings: dict[str, Any] = {}
    emotion_labels: dict[str, tuple[str, float]] = {}
    with log_path.open("a", encoding="utf-8", errors="replace") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        model = AutoModel(model=model_id, hub=hub, device=device, disable_update=True)
        for offset in range(0, len(entries), batch_size):
            batch = entries[offset : offset + batch_size]
            results = model.generate(
                [entry.wav_path for entry in batch],
                granularity="utterance",
                extract_embedding=True,
                batch_size=len(batch),
            )
            by_key = {str(item.get("key") or ""): item for item in results}
            for position, entry in enumerate(batch):
                item = by_key.get(entry.clip_id) or results[position]
                vector = np.asarray(item.get("feats"), dtype=np.float32).reshape(-1)
                if vector.size == 0:
                    raise RuntimeError(f"emotion2vec returned no embedding for {entry.clip_id}")
                labels = list(item.get("labels") or [])
                scores = [float(value) for value in (item.get("scores") or [])]
                best = max(range(len(scores)), key=scores.__getitem__) if scores else -1
                embeddings[entry.clip_id] = vector
                emotion_labels[entry.clip_id] = (
                    str(labels[best]) if 0 <= best < len(labels) else "",
                    scores[best] if best >= 0 else 0.0,
                )
            print(f"embedded {min(offset + len(batch), len(entries))}/{len(entries)}", file=sys.__stdout__, flush=True)

    ordered_ids = [entry.clip_id for entry in entries]
    np.savez_compressed(
        cache_path,
        clip_ids=np.asarray(ordered_ids),
        embeddings=np.stack([embeddings[clip_id] for clip_id in ordered_ids]),
        labels=np.asarray([emotion_labels[clip_id][0] for clip_id in ordered_ids]),
        label_scores=np.asarray([emotion_labels[clip_id][1] for clip_id in ordered_ids], dtype=np.float32),
    )
    metadata_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha256": source_digest,
                "model_id": model_id,
                "clip_count": len(entries),
                "embedding_dimensions": len(next(iter(embeddings.values()))),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return embeddings, emotion_labels


def _load_or_extract_prosody(
    *,
    entries: Sequence[SourceEntry],
    source_digest: str,
    output_dir: Path,
) -> dict[str, dict[str, float]]:
    cache_path = output_dir / "prosody_features.json"
    if cache_path.is_file():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("source_sha256") == source_digest and int(cached.get("clip_count", -1)) == len(entries):
            return {
                str(clip_id): {str(key): float(value) for key, value in values.items()}
                for clip_id, values in dict(cached.get("features") or {}).items()
            }

    features: dict[str, dict[str, float]] = {}
    for index, entry in enumerate(entries, start=1):
        features[entry.clip_id] = extract_prosody_features(entry.wav_path, entry.text)
        if index % 100 == 0 or index == len(entries):
            print(f"prosody {index}/{len(entries)}", flush=True)
    cache_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha256": source_digest,
                "clip_count": len(entries),
                "features": features,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return features


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists():
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def materialize_review_queue(output_dir: Path, rankings: Mapping[str, Sequence[RankedCandidate]]) -> None:
    review_root = output_dir / "review"
    review_root.mkdir(parents=True, exist_ok=True)
    lines = ["# 声学风格候选试听队列", "", "候选只用于人工听审；排名不是自动训练标签。", ""]
    for style, candidates in rankings.items():
        style_dir = review_root / style.lower()
        style_dir.mkdir(parents=True, exist_ok=True)
        lines.extend([f"## {style}", ""])
        for candidate in candidates:
            target = style_dir / f"{candidate.rank:02d}_{candidate.clip_id}.wav"
            _link_or_copy(Path(candidate.wav_path), target)
            lines.extend(
                [
                    f"{candidate.rank}. `{candidate.clip_id}`，score={candidate.score:.4f}",
                    "",
                    candidate.text,
                    "",
                    f"![{candidate.clip_id}]({target.resolve().as_posix()})",
                    "",
                ]
            )
        preview = _write_style_preview(review_root, style, candidates)
        if preview is not None:
            lines.extend(
                [
                    f"整组连播：![{style} preview]({preview.as_posix()})",
                    "",
                ]
            )
    (review_root / "index.md").write_text("\n".join(lines), encoding="utf-8")


def _write_style_preview(
    review_root: Path,
    style: str,
    candidates: Sequence[RankedCandidate],
) -> Path | None:
    try:
        import librosa
        import numpy as np
        import soundfile as sf
    except ImportError:
        return None
    if not candidates:
        return None
    sample_rate = 48_000
    silence = np.zeros(int(sample_rate * 0.65), dtype=np.float32)
    parts: list[Any] = []
    timeline: list[dict[str, Any]] = []
    cursor = 0
    for candidate in candidates:
        audio, source_rate = sf.read(candidate.wav_path, dtype="float32", always_2d=False)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)
        if int(source_rate) != sample_rate:
            audio = librosa.resample(audio, orig_sr=int(source_rate), target_sr=sample_rate)
        start_s = cursor / sample_rate
        parts.append(audio)
        cursor += len(audio)
        end_s = cursor / sample_rate
        timeline.append(
            {
                "rank": candidate.rank,
                "clip_id": candidate.clip_id,
                "start_s": round(start_s, 3),
                "end_s": round(end_s, 3),
                "text": candidate.text,
            }
        )
        parts.append(silence)
        cursor += len(silence)
    preview_path = (review_root / f"preview_{style.lower()}.wav").resolve()
    sf.write(preview_path, np.concatenate(parts), sample_rate, subtype="PCM_16")
    preview_path.with_suffix(".timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return preview_path


def _load_style_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "profile_id": "xilian_romance_v1",
            "style_seeds": {key: list(value) for key, value in DEFAULT_ROMANCE_SEEDS.items()},
            "candidate_policy": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("style config must be a JSON object")
    source_language = str(payload.get("source_language") or "").strip().lower()
    target_language = str(payload.get("target_language") or "").strip().lower()
    if source_language and target_language and source_language != target_language:
        raise ValueError(
            "cross-language voice profiles are not eligible for the production voice candidate pipeline"
        )
    raw_seeds = payload.get("style_seeds")
    if not isinstance(raw_seeds, dict) or not raw_seeds:
        raise ValueError("style config must define non-empty style_seeds")
    normalized: dict[str, list[str]] = {}
    for raw_style, raw_ids in raw_seeds.items():
        style = str(raw_style or "").strip().upper()
        if not style or not isinstance(raw_ids, list):
            raise ValueError("style_seeds must map style names to clip-id arrays")
        clip_ids = [str(item or "").strip() for item in raw_ids if str(item or "").strip()]
        if not clip_ids:
            raise ValueError(f"style {style!r} has no seed clips")
        normalized[style] = clip_ids
    candidate_policy = payload.get("candidate_policy")
    if candidate_policy is None:
        candidate_policy = {}
    if not isinstance(candidate_policy, dict):
        raise ValueError("candidate_policy must be a JSON object")
    return dict(payload) | {"style_seeds": normalized, "candidate_policy": candidate_policy}


def _eligible_clip_ids(
    entries: Sequence[SourceEntry],
    policy: Mapping[str, Any],
) -> set[str]:
    raw_patterns = policy.get("exclude_text_patterns") or []
    if not isinstance(raw_patterns, list):
        raise ValueError("candidate_policy.exclude_text_patterns must be an array")
    patterns = [re.compile(str(pattern)) for pattern in raw_patterns if str(pattern).strip()]
    raw_required_patterns = policy.get("require_text_patterns") or []
    if not isinstance(raw_required_patterns, list):
        raise ValueError("candidate_policy.require_text_patterns must be an array")
    required_patterns = [
        re.compile(str(pattern)) for pattern in raw_required_patterns if str(pattern).strip()
    ]
    max_exclamations = int(policy.get("max_exclamation_marks", 999))
    reject_truncated = bool(policy.get("reject_truncated_lines", False))
    eligible: set[str] = set()
    for entry in entries:
        text_value = str(entry.text or "")
        if required_patterns and not any(pattern.search(text_value) for pattern in required_patterns):
            continue
        if any(pattern.search(text_value) for pattern in patterns):
            continue
        if text_value.count("!") + text_value.count("！") > max_exclamations:
            continue
        if reject_truncated and re.search(r"(?:―|—|-)\s*$", text_value):
            continue
        eligible.add(entry.clip_id)
    return eligible


def _parse_seed_overrides(
    values: Iterable[str],
    *,
    base_seeds: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    parsed = {
        str(style).strip().upper(): tuple(str(item).strip() for item in ids if str(item).strip())
        for style, ids in (base_seeds or DEFAULT_ROMANCE_SEEDS).items()
    }
    for raw in values:
        style, separator, ids = str(raw).partition("=")
        seed_ids = tuple(item.strip() for item in ids.split(",") if item.strip())
        if not separator or not style.strip() or not seed_ids:
            raise ValueError(f"invalid --seed value: {raw!r}; expected STYLE=id[,id]")
        parsed[style.strip().upper()] = seed_ids
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mine acoustic neighbors for approved voice-style seeds.")
    parser.add_argument("--source-list", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--style-config", type=Path)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--hub", default="ms")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--seed", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    source_list = args.source_list.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = parse_source_list(source_list)
    source_digest = _source_digest(source_list)
    style_config = _load_style_config(args.style_config.resolve() if args.style_config else None)
    embeddings, emotion_labels = _load_or_extract_embeddings(
        entries=entries,
        source_digest=source_digest,
        output_dir=output_dir,
        model_id=str(args.model_id),
        hub=str(args.hub),
        device=str(args.device),
        batch_size=max(1, int(args.batch_size)),
    )
    prosody = _load_or_extract_prosody(entries=entries, source_digest=source_digest, output_dir=output_dir)
    seeds = _parse_seed_overrides(args.seed, base_seeds=style_config["style_seeds"])
    eligible_ids = _eligible_clip_ids(entries, style_config.get("candidate_policy") or {})
    eligible_ids.update(seed_id for seed_ids in seeds.values() for seed_id in seed_ids)
    eligible_entries = [entry for entry in entries if entry.clip_id in eligible_ids]
    eligible_source_list = output_dir / "eligible-source.list"
    eligible_source_list.write_text(
        "".join(
            f"{entry.wav_path}|{entry.speaker}|{entry.language}|{entry.text}\n"
            for entry in eligible_entries
        ),
        encoding="utf-8",
    )
    rankings = rank_style_candidates(
        entries=entries,
        embeddings_by_id=embeddings,
        prosody_by_id=prosody,
        emotion_labels_by_id=emotion_labels,
        style_seeds=seeds,
        top_k=max(1, int(args.top_k)),
        eligible_clip_ids=eligible_ids,
    )
    result = {
        "schema_version": 1,
        "status": "HUMAN_REVIEW_REQUIRED",
        "source_list": str(source_list),
        "source_sha256": source_digest,
        "source_clip_count": len(entries),
        "embedding_model": str(args.model_id),
        "style_profile_id": str(style_config.get("profile_id") or "custom"),
        "style_seeds": {style: list(ids) for style, ids in seeds.items()},
        "candidate_pool_count": len(eligible_ids),
        "candidate_pool_duration_hours": round(
            sum(float(prosody[entry.clip_id].get("duration_s", 0.0)) for entry in eligible_entries) / 3600.0,
            4,
        ),
        "eligible_source_list": str(eligible_source_list),
        "eligible_source_sha256": _source_digest(eligible_source_list),
        "candidate_policy": dict(style_config.get("candidate_policy") or {}),
        "ranking_policy": {
            "embedding_weight": 0.8,
            "prosody_weight": 0.2,
            "transcript_keyword_weight": 0.0,
            "speaker_identity_control": "corpus_mean_centered",
        },
        "candidates": {
            style: [asdict(candidate) for candidate in candidates]
            for style, candidates in rankings.items()
        },
    }
    (output_dir / "acoustic_style_candidates.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    materialize_review_queue(output_dir, rankings)
    print(
        json.dumps(
            {
                "status": result["status"],
                "source_clip_count": len(entries),
                "styles": {style: len(candidates) for style, candidates in rankings.items()},
                "output_dir": str(output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
