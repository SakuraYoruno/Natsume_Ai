from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_GPT_SOVITS_ROOT = Path(r"D:\AI_Models\GPT-SoVITS-v2pro-20250604-nvidia50")
DEFAULT_MIN_DURATION_S = 1.0
DEFAULT_MAX_DURATION_S = 12.0
LIST_NAME = "0-source.list"
MANIFEST_NAME = "dataset_manifest.json"
AUTHORIZATION_NAME = "source_authorization.json"


@dataclass(frozen=True)
class LabClip:
    wav_path: Path
    lab_path: Path | None
    rel_path: str
    text: str
    duration_s: float
    sample_rate: int
    channels: int
    sample_width_bytes: int
    byte_count: int
    status: str
    reason: str


def _read_lab_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    return _normalize_text(raw)


def _normalize_text(text: str) -> str:
    text = (text or "").replace("\ufeff", "").replace("|", "｜")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _wav_info(path: Path) -> tuple[float, int, int, int]:
    with wave.open(str(path), "rb") as handle:
        sample_rate = int(handle.getframerate())
        channels = int(handle.getnchannels())
        sample_width = int(handle.getsampwidth())
        frames = int(handle.getnframes())
    duration = frames / sample_rate if sample_rate else 0.0
    return duration, sample_rate, channels, sample_width


def _clip_reason(
    *,
    text: str,
    duration_s: float,
    sample_rate: int,
    channels: int,
    sample_width_bytes: int,
    min_duration_s: float,
    max_duration_s: float,
    skip_placeholder_text: bool = False,
    skip_music_symbols: bool = False,
    skip_latin_text: bool = False,
) -> tuple[str, str]:
    if not text:
        return "skipped", "empty_lab"
    if duration_s < min_duration_s:
        return "skipped", "too_short"
    if duration_s > max_duration_s:
        return "skipped", "too_long"
    if sample_rate < 16000:
        return "skipped", "sample_rate_too_low"
    if channels != 1:
        return "skipped", "not_mono"
    if sample_width_bytes != 2:
        return "skipped", "not_16bit_pcm"
    if skip_placeholder_text and re.search(r"[{}]", text):
        return "skipped", "placeholder_text"
    if skip_music_symbols and re.search(r"[♪♫]", text):
        return "skipped", "music_symbol_text"
    if skip_latin_text and re.search(r"[A-Za-z]", text):
        return "skipped", "latin_text"
    return "usable", "ok"


def read_lab_dataset(
    source_root: Path,
    *,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
    skip_placeholder_text: bool = False,
    skip_music_symbols: bool = False,
    skip_latin_text: bool = False,
) -> list[LabClip]:
    if not source_root.is_dir():
        raise FileNotFoundError(f"source root does not exist: {source_root}")

    clips: list[LabClip] = []
    for wav_path in sorted(source_root.rglob("*.wav"), key=lambda path: path.as_posix().lower()):
        lab_path = wav_path.with_suffix(".lab")
        rel_path = wav_path.relative_to(source_root).as_posix()
        if not lab_path.is_file():
            clips.append(
                LabClip(
                    wav_path=wav_path,
                    lab_path=None,
                    rel_path=rel_path,
                    text="",
                    duration_s=0.0,
                    sample_rate=0,
                    channels=0,
                    sample_width_bytes=0,
                    byte_count=wav_path.stat().st_size,
                    status="skipped",
                    reason="missing_lab",
                )
            )
            continue

        try:
            duration_s, sample_rate, channels, sample_width = _wav_info(wav_path)
        except Exception as exc:  # pragma: no cover - platform wave errors vary
            clips.append(
                LabClip(
                    wav_path=wav_path,
                    lab_path=lab_path,
                    rel_path=rel_path,
                    text=_read_lab_text(lab_path),
                    duration_s=0.0,
                    sample_rate=0,
                    channels=0,
                    sample_width_bytes=0,
                    byte_count=wav_path.stat().st_size,
                    status="skipped",
                    reason=f"bad_wav:{exc}",
                )
            )
            continue

        text = _read_lab_text(lab_path)
        status, reason = _clip_reason(
            text=text,
            duration_s=duration_s,
            sample_rate=sample_rate,
            channels=channels,
            sample_width_bytes=sample_width,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
            skip_placeholder_text=skip_placeholder_text,
            skip_music_symbols=skip_music_symbols,
            skip_latin_text=skip_latin_text,
        )
        clips.append(
            LabClip(
                wav_path=wav_path,
                lab_path=lab_path,
                rel_path=rel_path,
                text=text,
                duration_s=round(duration_s, 4),
                sample_rate=sample_rate,
                channels=channels,
                sample_width_bytes=sample_width,
                byte_count=wav_path.stat().st_size,
                status=status,
                reason=reason,
            )
        )
    return clips


def _count_by(items: list[LabClip], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, attr))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: pair[0]))


def inspect_lab_dataset(
    source_root: Path,
    *,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
    skip_placeholder_text: bool = False,
    skip_music_symbols: bool = False,
    skip_latin_text: bool = False,
) -> dict[str, Any]:
    clips = read_lab_dataset(
        source_root,
        min_duration_s=min_duration_s,
        max_duration_s=max_duration_s,
        skip_placeholder_text=skip_placeholder_text,
        skip_music_symbols=skip_music_symbols,
        skip_latin_text=skip_latin_text,
    )
    usable = [clip for clip in clips if clip.status == "usable"]
    skipped = [clip for clip in clips if clip.status != "usable"]
    return {
        "source_root": str(source_root),
        "wav_count": len(clips),
        "usable_count": len(usable),
        "skipped_count": len(skipped),
        "duration_minutes_total": round(sum(clip.duration_s for clip in clips) / 60.0, 2),
        "duration_minutes_usable": round(sum(clip.duration_s for clip in usable) / 60.0, 2),
        "duration_min_s": round(min((clip.duration_s for clip in clips), default=0.0), 3),
        "duration_max_s": round(max((clip.duration_s for clip in clips), default=0.0), 3),
        "sample_rates": _count_by(clips, "sample_rate"),
        "channels": _count_by(clips, "channels"),
        "sample_widths_bytes": _count_by(clips, "sample_width_bytes"),
        "skip_reasons": _count_by(skipped, "reason"),
        "text_filters": {
            "skip_placeholder_text": skip_placeholder_text,
            "skip_music_symbols": skip_music_symbols,
            "skip_latin_text": skip_latin_text,
        },
        "sample_usable": [
            {
                "rel_path": clip.rel_path,
                "duration_s": clip.duration_s,
                "text": clip.text[:80],
            }
            for clip in usable[:8]
        ],
    }


def _safe_project_name(project: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", project or ""):
        raise ValueError("project must be ASCII and contain only letters, digits, '.', '_' or '-'")
    return project


def _make_authorization_record(
    *,
    source_root: Path,
    source_name: str,
    user_asserted_authorization: str | None,
    authorization_file: Path | None,
) -> dict[str, Any]:
    if authorization_file is None and not user_asserted_authorization:
        raise ValueError("prepare requires --authorization-file or --user-asserted-authorization")

    record: dict[str, Any] = {
        "schema_version": 1,
        "source_name": source_name,
        "source_root": str(source_root),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "allowed_for_voice_model_training": True,
    }
    if authorization_file is not None:
        if not authorization_file.is_file():
            raise FileNotFoundError(f"authorization file does not exist: {authorization_file}")
        record.update(
            {
                "authorization_status": "provided_file",
                "authorization_file": str(authorization_file),
                "machine_verified": False,
                "authorization_text": authorization_file.read_text(encoding="utf-8-sig", errors="replace")[:8000],
            }
        )
    else:
        record.update(
            {
                "authorization_status": "user_asserted",
                "machine_verified": False,
                "user_assertion": user_asserted_authorization,
            }
        )
    return record


def _ensure_output_scope(root: Path, candidate: Path) -> None:
    root_resolved = root.resolve()
    candidate_resolved = candidate.resolve()
    if root_resolved != candidate_resolved and root_resolved not in candidate_resolved.parents:
        raise ValueError(f"refusing to write outside GPT-SoVITS root: {candidate}")


def prepare_lab_dataset(
    *,
    source_root: Path,
    gpt_sovits_root: Path,
    project: str,
    speaker: str,
    language: str,
    source_name: str,
    min_duration_s: float = DEFAULT_MIN_DURATION_S,
    max_duration_s: float = DEFAULT_MAX_DURATION_S,
    skip_placeholder_text: bool = False,
    skip_music_symbols: bool = False,
    skip_latin_text: bool = False,
    authorization_file: Path | None = None,
    user_asserted_authorization: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    project = _safe_project_name(project)
    if language not in {"zh", "ja", "en", "ko", "yue"}:
        raise ValueError(f"unsupported GPT-SoVITS language: {language}")
    if not speaker:
        raise ValueError("speaker must not be empty")
    if not gpt_sovits_root.is_dir():
        raise FileNotFoundError(f"GPT-SoVITS root does not exist: {gpt_sovits_root}")

    authorization = _make_authorization_record(
        source_root=source_root,
        source_name=source_name,
        user_asserted_authorization=user_asserted_authorization,
        authorization_file=authorization_file,
    )
    clips = read_lab_dataset(
        source_root,
        min_duration_s=min_duration_s,
        max_duration_s=max_duration_s,
        skip_placeholder_text=skip_placeholder_text,
        skip_music_symbols=skip_music_symbols,
        skip_latin_text=skip_latin_text,
    )
    usable = [clip for clip in clips if clip.status == "usable"]
    if not usable:
        raise ValueError("no usable clips after duration and format filtering")

    dataset_root = gpt_sovits_root / "datasets" / project
    wav_out = dataset_root / "wavs"
    lab_out = dataset_root / "labels"
    log_root = gpt_sovits_root / "logs" / project
    for path in (dataset_root, log_root):
        _ensure_output_scope(gpt_sovits_root, path)
        if path.exists() and not overwrite:
            raise FileExistsError(f"output already exists: {path}; pass --overwrite to replace generated files")

    if overwrite:
        for path in (dataset_root, log_root):
            if path.exists():
                _ensure_output_scope(gpt_sovits_root, path)
                shutil.rmtree(path)

    wav_out.mkdir(parents=True, exist_ok=True)
    lab_out.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    list_lines: list[str] = []
    prepared: list[dict[str, Any]] = []
    for index, clip in enumerate(usable, start=1):
        stem = f"{speaker}_{index:05d}"
        wav_dst = wav_out / f"{stem}.wav"
        lab_dst = lab_out / f"{stem}.lab"
        shutil.copy2(clip.wav_path, wav_dst)
        lab_dst.write_text(clip.text + "\n", encoding="utf-8")
        list_lines.append(f"{wav_dst}|{speaker}|{language}|{clip.text}")
        prepared.append(
            {
                "source_rel_path": clip.rel_path,
                "wav_path": str(wav_dst),
                "lab_path": str(lab_dst),
                "duration_s": clip.duration_s,
                "text": clip.text,
            }
        )

    list_path = log_root / LIST_NAME
    list_path.write_text("\n".join(list_lines) + "\n", encoding="utf-8")

    skipped = [asdict(clip) | {"wav_path": str(clip.wav_path), "lab_path": str(clip.lab_path) if clip.lab_path else None} for clip in clips if clip.status != "usable"]
    manifest = {
        "schema_version": 1,
        "project": project,
        "source_name": source_name,
        "source_root": str(source_root),
        "speaker": speaker,
        "language": language,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "min_duration_s": min_duration_s,
        "max_duration_s": max_duration_s,
        "text_filters": {
            "skip_placeholder_text": skip_placeholder_text,
            "skip_music_symbols": skip_music_symbols,
            "skip_latin_text": skip_latin_text,
        },
        "clip_count": len(clips),
        "prepared_count": len(prepared),
        "skipped_count": len(skipped),
        "duration_minutes_prepared": round(sum(item["duration_s"] for item in prepared) / 60.0, 2),
        "list_path": str(list_path),
        "dataset_root": str(dataset_root),
        "authorization_path": str(log_root / AUTHORIZATION_NAME),
        "skipped": skipped,
        "prepared_samples": prepared[:12],
    }
    (log_root / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (log_root / AUTHORIZATION_NAME).write_text(
        json.dumps(authorization, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and prepare wav+lab datasets for GPT-SoVITS training.")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_p = sub.add_parser("inspect-lab", help="Inspect a directory of .wav/.lab pairs.")
    inspect_p.add_argument("--source-root", type=Path, required=True)
    inspect_p.add_argument("--min-duration-s", type=float, default=DEFAULT_MIN_DURATION_S)
    inspect_p.add_argument("--max-duration-s", type=float, default=DEFAULT_MAX_DURATION_S)
    inspect_p.add_argument("--skip-placeholder-text", action="store_true")
    inspect_p.add_argument("--skip-music-symbols", action="store_true")
    inspect_p.add_argument("--skip-latin-text", action="store_true")

    prepare_p = sub.add_parser("prepare-lab", help="Copy usable .wav/.lab pairs into a GPT-SoVITS project.")
    prepare_p.add_argument("--source-root", type=Path, required=True)
    prepare_p.add_argument("--gpt-sovits-root", type=Path, default=DEFAULT_GPT_SOVITS_ROOT)
    prepare_p.add_argument("--project", required=True)
    prepare_p.add_argument("--speaker", default="xilian")
    prepare_p.add_argument("--language", default="zh")
    prepare_p.add_argument("--source-name", default="xilian")
    prepare_p.add_argument("--min-duration-s", type=float, default=DEFAULT_MIN_DURATION_S)
    prepare_p.add_argument("--max-duration-s", type=float, default=DEFAULT_MAX_DURATION_S)
    prepare_p.add_argument("--skip-placeholder-text", action="store_true")
    prepare_p.add_argument("--skip-music-symbols", action="store_true")
    prepare_p.add_argument("--skip-latin-text", action="store_true")
    prepare_p.add_argument("--authorization-file", type=Path)
    prepare_p.add_argument("--user-asserted-authorization")
    prepare_p.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "inspect-lab":
        print(
            json.dumps(
                inspect_lab_dataset(
                    args.source_root,
                    min_duration_s=args.min_duration_s,
                    max_duration_s=args.max_duration_s,
                    skip_placeholder_text=bool(args.skip_placeholder_text),
                    skip_music_symbols=bool(args.skip_music_symbols),
                    skip_latin_text=bool(args.skip_latin_text),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "prepare-lab":
        print(
            json.dumps(
                prepare_lab_dataset(
                    source_root=args.source_root,
                    gpt_sovits_root=args.gpt_sovits_root,
                    project=args.project,
                    speaker=args.speaker,
                    language=args.language,
                    source_name=args.source_name,
                    min_duration_s=args.min_duration_s,
                    max_duration_s=args.max_duration_s,
                    skip_placeholder_text=bool(args.skip_placeholder_text),
                    skip_music_symbols=bool(args.skip_music_symbols),
                    skip_latin_text=bool(args.skip_latin_text),
                    authorization_file=args.authorization_file,
                    user_asserted_authorization=args.user_asserted_authorization,
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
