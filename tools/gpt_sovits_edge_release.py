from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import time
import wave
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MANIFEST_NAME = "natsume_gpt_sovits_model_manifest.json"
SAMPLE_RANGE_RE = re.compile(r"_(\d{10})_(\d{10})\.wav$", flags=re.IGNORECASE)
EMOTION_REFERENCE_TAGS = {
    "DEFAULT": "neutral",
    "NEUTRAL": "neutral",
    "HAPPY": "happy",
    "HAPPINESS": "happy",
    "JOY": "happy",
    "EXCITED": "happy",
    "EXCITEMENT": "happy",
    "PLAYFUL": "playful",
    "COMFORT": "comfort",
    "CALM": "comfort",
    "SOOTHING": "comfort",
    "LOVING": "loving",
    "LOVE": "loving",
    "AFFECTION": "loving",
    "COQUETTISH": "loving",
    "SAD": "sad",
    "SADNESS": "sad",
    "NOSTALGIC": "nostalgic",
    "ANXIOUS": "anxious",
    "EMBARRASSED": "anxious",
    "SERIOUS": "serious",
}

# Human-listened Xilian references.  These styles describe delivery only; all
# five remain separate from the canonical self-emotion vocabulary.  The
# explicit-love clip is used as a second same-speaker reference for the tender
# style rather than inventing another near-duplicate runtime category.
CURATED_ROMANCE_STYLE_REFERENCES = (
    {
        "voice_style": "SHY_AFFECTION",
        "emotion_tag": "LOVING",
        "wav_name": "xilian_00025.wav",
    },
    {
        "voice_style": "FLIRTING",
        "emotion_tag": "LOVING",
        "wav_name": "xilian_00003.wav",
    },
    {
        "voice_style": "LONGING",
        "emotion_tag": "LOVING",
        "wav_name": "xilian_01292.wav",
    },
    {
        "voice_style": "QUIET_INTIMACY",
        "emotion_tag": "LOVING",
        "wav_name": "xilian_01162.wav",
    },
    {
        "voice_style": "ROMANTIC_TENDER",
        "emotion_tag": "LOVING",
        "wav_name": "xilian_00048.wav",
        "aux_wav_names": ("xilian_01101.wav",),
    },
)
WEIGHT_DIRS = (
    ("GPT_weights_v2ProPlus", ".ckpt", "t2s"),
    ("GPT_weights_v2Pro", ".ckpt", "t2s"),
    ("GPT_weights_v4", ".ckpt", "t2s"),
    ("GPT_weights_v3", ".ckpt", "t2s"),
    ("GPT_weights_v2", ".ckpt", "t2s"),
    ("GPT_weights", ".ckpt", "t2s"),
    ("SoVITS_weights_v2ProPlus", ".pth", "vits"),
    ("SoVITS_weights_v2Pro", ".pth", "vits"),
    ("SoVITS_weights_v4", ".pth", "vits"),
    ("SoVITS_weights_v3", ".pth", "vits"),
    ("SoVITS_weights_v2", ".pth", "vits"),
    ("SoVITS_weights", ".pth", "vits"),
)


@dataclass(frozen=True)
class ClipRow:
    name: str
    prompt_text: str
    language: str
    samples: int
    duration_s: float
    rel_path: str


def _looks_japanese(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", text or ""))


def _looks_chinese(text: str) -> bool:
    if _looks_japanese(text):
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def _detect_text_language(text: str) -> str:
    if _looks_japanese(text):
        return "ja"
    if _looks_chinese(text):
        return "zh"
    return "unknown"


def _sample_count_from_name(name: str) -> int:
    match = SAMPLE_RANGE_RE.search(name)
    if not match:
        return 0
    return max(int(match.group(2)) - int(match.group(1)), 0)


def _duration_from_wav(path: Path) -> tuple[int, float]:
    with wave.open(str(path), "rb") as handle:
        frames = int(handle.getnframes())
        sample_rate = int(handle.getframerate())
    duration_s = frames / sample_rate if sample_rate else 0.0
    return frames, round(duration_s, 4)


def _read_clip_rows(source_root: Path, project: str) -> list[ClipRow]:
    text_path = source_root / "logs" / project / "2-name2text.txt"
    wav_root = source_root / "logs" / project / "5-wav32k"
    if not text_path.is_file():
        raise FileNotFoundError(f"missing manifest text file: {text_path}")
    rows: list[ClipRow] = []
    with text_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            parts = raw.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            name = parts[0].strip()
            prompt_text = parts[3].strip()
            if not name or not prompt_text:
                continue
            wav_path = wav_root / name
            rel_path = str(Path("logs") / project / "5-wav32k" / name)
            if not wav_path.is_file():
                continue
            samples = _sample_count_from_name(name)
            duration_s = round(samples / 32000.0, 4) if samples else 0.0
            if not duration_s:
                try:
                    samples, duration_s = _duration_from_wav(wav_path)
                except Exception:
                    samples = 0
                    duration_s = 0.0
            rows.append(
                ClipRow(
                    name=name,
                    prompt_text=prompt_text,
                    language=_detect_text_language(prompt_text),
                    samples=samples,
                    duration_s=duration_s,
                    rel_path=rel_path.replace("\\", "/"),
                )
            )
    return rows


def _language_counts(rows: list[ClipRow]) -> dict[str, int]:
    counts = {"zh": 0, "ja": 0, "unknown": 0}
    for row in rows:
        counts[row.language] = counts.get(row.language, 0) + 1
    return counts


def _dominant_language(counts: dict[str, int]) -> str:
    supported = {key: value for key, value in counts.items() if key in {"zh", "ja"}}
    if not supported:
        return "unknown"
    dominant, count = max(supported.items(), key=lambda item: item[1])
    total = sum(supported.values())
    if total <= 0 or count / total < 0.65:
        return "mixed"
    return dominant


def _project_weight_score(path: Path, *, project: str, kind: str) -> tuple[int, int, int, str]:
    stem = path.stem
    if kind == "t2s":
        match = re.fullmatch(rf"{re.escape(project)}-e(\d+)", stem)
        epoch = int(match.group(1)) if match else -1
        step = 0
    else:
        match = re.fullmatch(rf"{re.escape(project)}_e(\d+)_s(\d+)", stem)
        epoch = int(match.group(1)) if match else -1
        step = int(match.group(2)) if match else -1
    return epoch, step, path.stat().st_mtime_ns, path.name


def _latest_weight(source_root: Path, kind: str, *, project: str) -> Path | None:
    candidates: list[Path] = []
    for folder, suffix, candidate_kind in WEIGHT_DIRS:
        if candidate_kind != kind:
            continue
        base = source_root / folder
        if not base.is_dir():
            continue
        project_prefix = f"{project}-" if kind == "t2s" else f"{project}_"
        candidates.extend(
            path
            for path in base.iterdir()
            if path.is_file()
            and path.suffix.lower() == suffix
            and path.stem.startswith(project_prefix)
        )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda path: _project_weight_score(path, project=project, kind=kind),
    )


def _select_references(rows: list[ClipRow], *, voice_language: str, limit: int) -> list[ClipRow]:
    usable = [
        row for row in rows
        if row.language == voice_language and 3.0 <= row.duration_s <= 10.0 and len(row.prompt_text) >= 4
    ]
    usable.sort(key=lambda row: (abs(row.duration_s - 5.0), len(row.prompt_text), row.name))
    return usable[: max(1, limit)]


def _select_emotion_manifest_references(
    source_root: Path,
    project: str,
    *,
    voice_language: str,
) -> list[dict[str, Any]]:
    report_path = source_root / "logs" / project / "emotion_balance" / "emotion_balance_report.json"
    if not report_path.is_file():
        return []
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    by_emotion = report.get("top_selected_by_emotion")
    if not isinstance(by_emotion, dict):
        return []

    wav_root = source_root / "logs" / project / "5-wav32k"
    refs: list[dict[str, Any]] = []
    for tag, source_emotion in EMOTION_REFERENCE_TAGS.items():
        rows = by_emotion.get(source_emotion)
        if not isinstance(rows, list):
            continue
        selected: dict[str, Any] | None = None
        for item in rows:
            if not isinstance(item, dict):
                continue
            duration_s = float(item.get("duration_s") or 0.0)
            prompt_text = str(item.get("text") or "").strip()
            wav_name = Path(str(item.get("wav_path") or "")).name
            wav_path = wav_root / wav_name
            if (
                3.0 <= duration_s <= 10.0
                and len(prompt_text) >= 4
                and wav_name
                and wav_path.is_file()
                and _detect_text_language(prompt_text) == voice_language
            ):
                selected = item
                break
        if selected is None:
            continue
        wav_name = Path(str(selected.get("wav_path") or "")).name
        rel_path = (Path("logs") / project / "5-wav32k" / wav_name).as_posix()
        refs.append(
            {
                "emotion_tag": tag,
                "audio_path": rel_path,
                "prompt_text": str(selected.get("text") or "").strip(),
                "prompt_lang": voice_language,
                "duration_s": round(float(selected.get("duration_s") or 0.0), 4),
                "source_emotion": source_emotion,
                "expressive_score": selected.get("expressive_score"),
                "confidence": selected.get("confidence"),
            }
        )
    return refs


def _select_curated_romance_references(
    source_root: Path,
    project: str,
    *,
    voice_language: str,
    rows: list[ClipRow],
) -> list[dict[str, Any]]:
    """Return only complete, exact human-approved reference entries.

    A project without the approved Xilian clips simply receives no romance
    overlay.  We never substitute a similarly named or automatically labelled
    clip because that was the source of the weak style separation in the first
    candidate bank.
    """

    row_by_name = {row.name: row for row in rows}
    wav_root = source_root / "logs" / project / "5-wav32k"
    selected: list[dict[str, Any]] = []
    for spec in CURATED_ROMANCE_STYLE_REFERENCES:
        wav_name = str(spec["wav_name"])
        row = row_by_name.get(wav_name)
        wav_path = wav_root / wav_name
        if (
            row is None
            or row.language != voice_language
            or not 3.0 <= row.duration_s <= 10.0
            or len(row.prompt_text) < 4
            or not wav_path.is_file()
        ):
            continue

        aux_paths: list[str] = []
        aux_complete = True
        for aux_name in tuple(spec.get("aux_wav_names") or ()):
            aux_row = row_by_name.get(str(aux_name))
            aux_path = wav_root / str(aux_name)
            if (
                aux_row is None
                or aux_row.language != voice_language
                or not 3.0 <= aux_row.duration_s <= 10.0
                or not aux_path.is_file()
            ):
                aux_complete = False
                break
            aux_paths.append(
                (Path("logs") / project / "5-wav32k" / str(aux_name)).as_posix()
            )
        if not aux_complete:
            continue

        item: dict[str, Any] = {
            "voice_style": str(spec["voice_style"]),
            "emotion_tag": str(spec["emotion_tag"]),
            "audio_path": (Path("logs") / project / "5-wav32k" / wav_name).as_posix(),
            "prompt_text": row.prompt_text,
            "prompt_lang": voice_language,
            "duration_s": row.duration_s,
            "curation": "HUMAN_LISTENING_APPROVED",
        }
        if aux_paths:
            item["aux_audio_paths"] = aux_paths
        selected.append(item)
    return selected


def inspect_project(source_root: Path, project: str) -> dict[str, Any]:
    rows = _read_clip_rows(source_root, project)
    counts = _language_counts(rows)
    refs = [row for row in rows if 3.0 <= row.duration_s <= 10.0]
    t2s = _latest_weight(source_root, "t2s", project=project)
    vits = _latest_weight(source_root, "vits", project=project)
    return {
        "source_root": str(source_root),
        "project": project,
        "clip_count": len(rows),
        "reference_candidate_count": len(refs),
        "language_counts": counts,
        "dominant_language": _dominant_language(counts),
        "latest_weights": {
            "t2s": str(t2s) if t2s else None,
            "vits": str(vits) if vits else None,
        },
        "ready_for_packaging": bool(rows and t2s and vits),
    }


def package_release(
    *,
    source_root: Path,
    project: str,
    voice_language: str,
    output_dir: Path,
    model_id: str,
    reference_limit: int,
    allow_language_mismatch: bool,
) -> Path:
    summary = inspect_project(source_root, project)
    dominant = str(summary["dominant_language"])
    if not allow_language_mismatch and dominant not in {voice_language, "mixed"}:
        raise SystemExit(
            f"refusing to package {voice_language!r} release from dominant {dominant!r} corpus. "
            "Use the correct trained project, or pass --allow-language-mismatch for manual recovery only."
        )

    t2s = _latest_weight(source_root, "t2s", project=project)
    vits = _latest_weight(source_root, "vits", project=project)
    if t2s is None or vits is None:
        raise FileNotFoundError("cannot locate both GPT (.ckpt) and SoVITS (.pth) weights")

    rows = _read_clip_rows(source_root, project)
    refs = _select_references(rows, voice_language=voice_language, limit=reference_limit)
    if not refs:
        raise SystemExit(f"no 3-10s {voice_language!r} reference clips found under logs/{project}/5-wav32k")

    created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    release_name = f"{model_id}-{voice_language}-{time.strftime('%Y%m%d-%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / f"{release_name}.zip"

    with tempfile.TemporaryDirectory(prefix="natsume_gsv_release_") as tmp:
        stage = Path(tmp) / release_name
        stage.mkdir(parents=True, exist_ok=True)

        copied_weights: dict[str, str] = {}
        for key, src in {"t2s": t2s, "vits": vits}.items():
            rel = src.relative_to(source_root)
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied_weights[key] = rel.as_posix()

        text_src = source_root / "logs" / project / "2-name2text.txt"
        text_rel = Path("logs") / project / "2-name2text.txt"
        text_dst = stage / text_rel
        text_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(text_src, text_dst)

        manifest_refs = _select_emotion_manifest_references(
            source_root,
            project,
            voice_language=voice_language,
        )
        if not manifest_refs:
            manifest_refs = [
                {
                    "emotion_tag": "NEUTRAL" if index == 0 else "ALT",
                    "audio_path": row.rel_path,
                    "prompt_text": row.prompt_text,
                    "prompt_lang": row.language,
                    "duration_s": row.duration_s,
                }
                for index, row in enumerate(refs)
            ]

        manifest_refs.extend(
            _select_curated_romance_references(
                source_root,
                project,
                voice_language=voice_language,
                rows=rows,
            )
        )

        for item in manifest_refs:
            paths = [
                str(item.get("audio_path") or "").strip(),
                *[
                    str(path or "").strip()
                    for path in list(item.get("aux_audio_paths") or [])
                ],
            ]
            for rel_path in paths:
                if not rel_path:
                    continue
                src = source_root / rel_path
                dst = stage / rel_path
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        authorization_path = source_root / "logs" / project / "source_authorization.json"
        source_authorization = None
        if authorization_path.is_file():
            try:
                source_authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                source_authorization = {
                    "authorization_status": "unparseable",
                    "path": str(authorization_path),
                }

        manifest = {
            "schema_version": 1,
            "model_id": model_id,
            "project": project,
            "created_at": created_at,
            "voice_language": voice_language,
            "source_dominant_language": dominant,
            "language_counts": summary["language_counts"],
            "weights": copied_weights,
            "references": manifest_refs,
            "source_authorization": source_authorization,
            "edge_env": {
                "EDGE_TTS_LANGUAGE": voice_language,
                "EDGE_TTS_MODEL_PATH": "<edge GPT-SoVITS root after extracting this package>",
            },
        }
        (stage / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in stage.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(stage).as_posix())

    return package_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and package GPT-SoVITS model releases for Edge inference.")
    sub = parser.add_subparsers(dest="command", required=True)

    common: dict[str, Any] = {
        "type": Path,
        "default": Path(r"D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50"),
    }
    inspect_p = sub.add_parser("inspect", help="Inspect trained project language, clips, and weights.")
    inspect_p.add_argument("--source-root", **common)
    inspect_p.add_argument("--project", default="Natsume_Ai")

    package_p = sub.add_parser("package", help="Create a minimal Edge overlay package from trained weights.")
    package_p.add_argument("--source-root", **common)
    package_p.add_argument("--project", default="Natsume_Ai")
    package_p.add_argument("--voice-language", choices=["zh", "ja"], required=True)
    package_p.add_argument("--output-dir", type=Path, default=Path("artifacts/model_releases"))
    package_p.add_argument("--model-id", default="natsume-gpt-sovits")
    package_p.add_argument("--reference-limit", type=int, default=8)
    package_p.add_argument("--allow-language-mismatch", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "inspect":
        print(json.dumps(inspect_project(args.source_root, args.project), ensure_ascii=False, indent=2))
        return 0
    if args.command == "package":
        package_path = package_release(
            source_root=args.source_root,
            project=args.project,
            voice_language=args.voice_language,
            output_dir=args.output_dir,
            model_id=args.model_id,
            reference_limit=args.reference_limit,
            allow_language_mismatch=bool(args.allow_language_mismatch),
        )
        print(json.dumps({"package_path": str(package_path)}, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
