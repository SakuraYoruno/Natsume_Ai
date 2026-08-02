"""Prepare the approved Nanami Japanese corpus for Style-Bert-VITS2 JP-Extra.

The source corpus has no trustworthy per-line emotion labels.  This tool keeps
all ordinary training clips in ``Natural`` and only places explicitly reviewed
anchor clips in named style directories.  Style-Bert-VITS2 derives inference
style vectors from those directories; changing the anchor bank later therefore
does not require retraining the acoustic model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_PLAN_PATH = Path("configs/voice/style_bert_vits2_nanami.json")


@dataclass(frozen=True, slots=True)
class SourceClip:
    clip_id: str
    wav_path: Path
    speaker: str
    language: str
    text: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_text(value: object, *, field_name: str, limit: int = 2_000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("|", "｜")).strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def parse_source_list(path: Path) -> list[SourceClip]:
    if not path.is_file():
        raise FileNotFoundError(f"source list does not exist: {path}")
    clips: list[SourceClip] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        if not raw.strip():
            continue
        columns = raw.split("|", 3)
        if len(columns) != 4:
            raise ValueError(f"source line {line_number} must have four columns")
        wav_raw, speaker, language, text = (item.strip() for item in columns)
        wav_path = Path(wav_raw).resolve()
        clip_id = wav_path.stem
        if not wav_path.is_file():
            raise FileNotFoundError(f"source wav unavailable at line {line_number}: {wav_path}")
        if clip_id in seen:
            raise ValueError(f"duplicate clip id in source list: {clip_id}")
        seen.add(clip_id)
        clips.append(
            SourceClip(
                clip_id=_safe_text(clip_id, field_name="clip_id", limit=128),
                wav_path=wav_path,
                speaker=_safe_text(speaker, field_name="speaker", limit=128),
                language=_safe_text(language, field_name="language", limit=16),
                text=_safe_text(text, field_name="text"),
            )
        )
    if not clips:
        raise ValueError("source list is empty")
    return clips


def load_prosody(path: Path) -> dict[str, dict[str, float]]:
    if not path.is_file():
        raise FileNotFoundError(f"prosody file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_features = payload.get("features") if isinstance(payload, Mapping) else None
    if not isinstance(raw_features, Mapping):
        raise ValueError("prosody file must contain a features object")
    result: dict[str, dict[str, float]] = {}
    for clip_id, raw in raw_features.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"prosody feature for {clip_id} must be an object")
        result[str(clip_id)] = {
            str(key): float(value)
            for key, value in raw.items()
            if isinstance(value, (int, float))
        }
    return result


def load_plan(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"dataset plan does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("dataset plan must be a JSON object")
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("dataset plan schema_version must be 1")
    if str(payload.get("language") or "").upper() != "JP":
        raise ValueError("Nanami Style-Bert-VITS2 plan must use JP")
    anchors = payload.get("style_anchors")
    if not isinstance(anchors, dict) or not anchors:
        raise ValueError("dataset plan requires style_anchors")
    return payload


def _style_by_anchor(plan: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    anchors = plan.get("style_anchors")
    assert isinstance(anchors, Mapping)
    for style_raw, clip_ids_raw in anchors.items():
        style = _safe_text(style_raw, field_name="style", limit=64)
        if style.lower() in {"neutral", "natural"}:
            raise ValueError("Natural and Neutral are reserved style names")
        if not isinstance(clip_ids_raw, Sequence) or isinstance(clip_ids_raw, (str, bytes)):
            raise ValueError(f"style {style} anchors must be a list")
        clip_ids = [_safe_text(value, field_name=f"{style} anchor", limit=128) for value in clip_ids_raw]
        if not clip_ids:
            raise ValueError(f"style {style} must have at least one anchor")
        for clip_id in clip_ids:
            previous = result.get(clip_id)
            if previous:
                raise ValueError(f"anchor {clip_id} appears in both {previous} and {style}")
            result[clip_id] = style
    return result


def _explicit_exclusions(plan: Mapping[str, Any]) -> dict[str, str]:
    raw = plan.get("excluded_clips") or {}
    if not isinstance(raw, Mapping):
        raise ValueError("excluded_clips must be an object")
    return {
        _safe_text(clip_id, field_name="excluded clip id", limit=128): _safe_text(
            reason, field_name="exclusion reason", limit=128
        )
        for clip_id, reason in raw.items()
    }


def select_clips(
    *,
    clips: Sequence[SourceClip],
    prosody: Mapping[str, Mapping[str, float]],
    plan: Mapping[str, Any],
) -> tuple[list[tuple[SourceClip, str, bool]], dict[str, int]]:
    selection = plan.get("selection")
    if not isinstance(selection, Mapping):
        raise ValueError("dataset plan requires selection")
    min_duration = float(selection.get("min_duration_s", 2.0))
    max_duration = float(selection.get("max_duration_s", 10.0))
    min_voiced = float(selection.get("min_voiced_ratio", 0.2))
    max_pause = float(selection.get("max_pause_ratio", 0.55))
    if not 0.0 < min_duration <= max_duration:
        raise ValueError("invalid duration selection bounds")
    if not 0.0 <= min_voiced <= 1.0 or not 0.0 <= max_pause <= 1.0:
        raise ValueError("invalid voiced/pause selection bounds")

    anchors = _style_by_anchor(plan)
    explicit_exclusions = _explicit_exclusions(plan)
    overlap = sorted(set(anchors) & set(explicit_exclusions))
    if overlap:
        raise ValueError(f"style anchors cannot be explicitly excluded: {overlap}")
    clip_by_id = {clip.clip_id: clip for clip in clips}
    unknown = sorted((set(anchors) | set(explicit_exclusions)) - set(clip_by_id))
    if unknown:
        raise ValueError(f"configured clips missing from source list: {unknown}")

    selected: list[tuple[SourceClip, str, bool]] = []
    reasons: dict[str, int] = {}
    for clip in sorted(clips, key=lambda item: item.clip_id):
        if clip.clip_id in explicit_exclusions:
            reasons["explicit_exclusion"] = reasons.get("explicit_exclusion", 0) + 1
            continue
        feature = prosody.get(clip.clip_id)
        forced_anchor = clip.clip_id in anchors
        reason = "selected"
        if feature is None:
            reason = "missing_prosody"
        elif float(feature.get("duration_s", 0.0)) < min_duration:
            reason = "too_short"
        elif float(feature.get("duration_s", 0.0)) > max_duration:
            reason = "too_long"
        elif float(feature.get("voiced_ratio", 0.0)) < min_voiced:
            reason = "voiced_ratio_low"
        elif float(feature.get("pause_ratio", 1.0)) > max_pause:
            reason = "pause_ratio_high"
        if reason != "selected" and not forced_anchor:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        style = anchors.get(clip.clip_id, "Natural")
        selected.append((clip, style, reason != "selected"))
    return selected, dict(sorted(reasons.items()))


def _link_or_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def prepare_dataset(
    *,
    source_list: Path,
    prosody_path: Path,
    plan_path: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    actual_source_sha = _sha256(source_list)
    expected_source_sha = str(plan.get("source_sha256") or "").strip().lower()
    if expected_source_sha and actual_source_sha != expected_source_sha:
        raise ValueError(
            "source list digest drift: "
            f"expected={expected_source_sha} actual={actual_source_sha}"
        )
    clips = parse_source_list(source_list)
    prosody = load_prosody(prosody_path)
    selected, excluded_reasons = select_clips(clips=clips, prosody=prosody, plan=plan)
    if not selected:
        raise ValueError("selection produced no training clips")

    output_dir = output_dir.resolve()
    if output_dir.exists() and not overwrite:
        raise FileExistsError(f"output dataset already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    style_counts: dict[str, int] = {}
    materialization_counts = {"hardlink": 0, "copy": 0}
    forced_anchor_ids: list[str] = []
    esd_lines: list[str] = []
    duration_s = 0.0
    try:
        raw_root = staging / "raw"
        for clip, style, forced_anchor in selected:
            destination = raw_root / style / f"{clip.clip_id}.wav"
            method = _link_or_copy(clip.wav_path, destination)
            materialization_counts[method] += 1
            style_counts[style] = style_counts.get(style, 0) + 1
            if forced_anchor:
                forced_anchor_ids.append(clip.clip_id)
            relative = destination.relative_to(raw_root).as_posix()
            esd_lines.append(
                f"{relative}|{_safe_text(plan.get('speaker'), field_name='speaker', limit=128)}"
                f"|JP|{clip.text}"
            )
            duration_s += float(prosody.get(clip.clip_id, {}).get("duration_s", 0.0))

        (staging / "esd.list").write_text("\n".join(esd_lines) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "profile_id": str(plan.get("profile_id") or ""),
            "model_name": str(plan.get("model_name") or ""),
            "language": "JP",
            "source_list": str(source_list.resolve()),
            "source_sha256": actual_source_sha,
            "prosody_path": str(prosody_path.resolve()),
            "prosody_sha256": _sha256(prosody_path),
            "plan_path": str(plan_path.resolve()),
            "plan_sha256": _sha256(plan_path),
            "source_clip_count": len(clips),
            "selected_clip_count": len(selected),
            "selected_duration_hours": round(duration_s / 3600.0, 6),
            "style_counts": dict(sorted(style_counts.items())),
            "forced_anchor_ids": sorted(forced_anchor_ids),
            "excluded_reasons": excluded_reasons,
            "materialization": materialization_counts,
            "selection": dict(plan.get("selection") or {}),
            "explicit_exclusions": _explicit_exclusions(plan),
            "runtime_style_map": dict(plan.get("runtime_style_map") or {}),
            "status": "DATASET_PREPARED_NOT_TRAINED",
        }
        (staging / "nanami_style_dataset_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.replace(output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the reviewed Nanami Japanese corpus for Style-Bert-VITS2 JP-Extra."
    )
    parser.add_argument("--source-list", type=Path, required=True)
    parser.add_argument("--prosody", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = prepare_dataset(
        source_list=args.source_list,
        prosody_path=args.prosody,
        plan_path=args.plan,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
