"""Review and rebuild Nanami Style-Bert-VITS2 style vectors without retraining."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping, Sequence

import numpy as np


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _anchors(plan: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    raw = plan.get("style_anchors")
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("style plan must contain style_anchors")
    result: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for raw_style, raw_ids in raw.items():
        style = str(raw_style or "").strip()
        if not style or not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            raise ValueError(f"invalid style anchors for {style or '<empty>'}")
        clip_ids = tuple(str(value or "").strip() for value in raw_ids if str(value or "").strip())
        if not clip_ids:
            raise ValueError(f"style {style} has no anchors")
        overlap = seen.intersection(clip_ids)
        if overlap:
            raise ValueError(f"style anchors overlap: {sorted(overlap)}")
        seen.update(clip_ids)
        result[style] = clip_ids
    return result


def _embedding_files(dataset_dir: Path) -> dict[str, Path]:
    root = dataset_dir.resolve() / "wavs"
    if not root.is_dir():
        raise FileNotFoundError(f"dataset wavs directory does not exist: {root}")
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*.wav.npy")):
        clip_id = path.name[: -len(".wav.npy")]
        if clip_id in result:
            raise ValueError(f"duplicate style embedding clip id: {clip_id}")
        result[clip_id] = path
    if not result:
        raise ValueError("dataset has no generated style embeddings")
    return result


def _load_vectors(files: Mapping[str, Path]) -> tuple[list[str], np.ndarray]:
    clip_ids = sorted(files)
    vectors: list[np.ndarray] = []
    shape: tuple[int, ...] | None = None
    for clip_id in clip_ids:
        value = np.asarray(np.load(files[clip_id]), dtype=np.float32).reshape(-1)
        if not value.size or not np.isfinite(value).all():
            raise ValueError(f"invalid style embedding: {clip_id}")
        if shape is None:
            shape = value.shape
        elif value.shape != shape:
            raise ValueError(f"style embedding shape drift: {clip_id}")
        vectors.append(value)
    return clip_ids, np.stack(vectors)


def _normalized(values: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 1e-9):
        raise ValueError("style embedding contains a zero vector")
    return values / norms


def _transcripts(dataset_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("esd.list", "esd.list.cleaned"):
        path = dataset_dir / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            columns = raw.split("|", 3)
            if len(columns) != 4:
                continue
            clip_id = Path(columns[0]).name
            if clip_id.endswith(".wav"):
                clip_id = clip_id[:-4]
            result.setdefault(clip_id, columns[3].strip())
        if result:
            break
    return result


def mine_review_candidates(
    *,
    dataset_dir: Path,
    style_plan_file: Path,
    per_style: int = 12,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    plan = _json_object(style_plan_file.resolve(), label="style plan")
    anchors = _anchors(plan)
    files = _embedding_files(dataset_dir)
    clip_ids, vectors = _load_vectors(files)
    index = {clip_id: offset for offset, clip_id in enumerate(clip_ids)}
    missing = sorted({clip_id for values in anchors.values() for clip_id in values} - set(index))
    if missing:
        raise ValueError(f"style anchors have no generated embedding: {missing}")
    normalized = _normalized(vectors)
    centroids: dict[str, np.ndarray] = {}
    for style, members in anchors.items():
        centroid = np.mean(normalized[[index[clip_id] for clip_id in members]], axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm <= 1e-9 or not math.isfinite(norm):
            raise ValueError(f"invalid anchor centroid: {style}")
        centroids[style] = centroid / norm

    styles = list(anchors)
    scores = np.stack([normalized @ centroids[style] for style in styles], axis=1)
    anchor_ids = {clip_id for values in anchors.values() for clip_id in values}
    transcripts = _transcripts(dataset_dir)
    candidates: dict[str, list[dict[str, Any]]] = {style: [] for style in styles}
    limit = max(1, min(100, int(per_style)))
    for style_index, style in enumerate(styles):
        rows: list[tuple[float, float, str]] = []
        for clip_index, clip_id in enumerate(clip_ids):
            if clip_id in anchor_ids:
                continue
            similarity = float(scores[clip_index, style_index])
            other = max(
                (float(scores[clip_index, offset]) for offset in range(len(styles)) if offset != style_index),
                default=-1.0,
            )
            rows.append((similarity - other, similarity, clip_id))
        rows.sort(key=lambda item: (-item[0], -item[1], item[2]))
        for margin, similarity, clip_id in rows[:limit]:
            wav_path = files[clip_id].with_suffix("")
            candidates[style].append(
                {
                    "clip_id": clip_id,
                    "similarity": round(similarity, 6),
                    "margin": round(margin, 6),
                    "text": transcripts.get(clip_id, ""),
                    "wav_path": str(wav_path),
                }
            )
    return {
        "schema_version": 1,
        "status": "CANDIDATE_REVIEW_REQUIRED",
        "dataset_dir": str(dataset_dir),
        "embedding_count": len(clip_ids),
        "per_style": limit,
        "anchors": {key: list(value) for key, value in anchors.items()},
        "candidates": candidates,
    }


def materialize_review_candidates(*, receipt: Mapping[str, Any], output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = receipt.get("candidates")
    if not isinstance(candidates, Mapping):
        raise ValueError("candidate receipt has no candidates")
    for style, raw_rows in candidates.items():
        if not isinstance(raw_rows, Sequence):
            continue
        style_dir = output_dir / str(style)
        style_dir.mkdir(parents=True, exist_ok=True)
        for position, raw in enumerate(raw_rows, start=1):
            if not isinstance(raw, Mapping):
                continue
            source = Path(str(raw.get("wav_path") or ""))
            if not source.is_file():
                raise FileNotFoundError(f"candidate audio is missing: {source}")
            destination = style_dir / f"{position:02d}_{source.name}"
            try:
                os.link(source, destination)
            except FileExistsError:
                continue
            except OSError:
                shutil.copy2(source, destination)
    (output_dir / "candidate_review.json").write_text(
        json.dumps(dict(receipt), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_style_bank(
    *,
    dataset_dir: Path,
    style_plan_file: Path,
    source_config_file: Path,
    output_config_file: Path,
    output_vectors_file: Path,
) -> dict[str, Any]:
    plan = _json_object(style_plan_file.resolve(), label="style plan")
    anchors = _anchors(plan)
    files = _embedding_files(dataset_dir.resolve())
    clip_ids, vectors = _load_vectors(files)
    index = {clip_id: offset for offset, clip_id in enumerate(clip_ids)}
    anchor_ids = {clip_id for values in anchors.values() for clip_id in values}
    missing = sorted(anchor_ids - set(index))
    if missing:
        raise ValueError(f"style anchors have no generated embedding: {missing}")
    natural_indexes = [offset for offset, clip_id in enumerate(clip_ids) if clip_id not in anchor_ids]
    if not natural_indexes:
        raise ValueError("style bank has no Natural members")

    names = ["Neutral", "Natural", *anchors]
    style_vectors = [
        np.mean(vectors, axis=0),
        np.mean(vectors[natural_indexes], axis=0),
    ]
    member_counts = {"Neutral": len(clip_ids), "Natural": len(natural_indexes)}
    for style, members in anchors.items():
        style_vectors.append(np.mean(vectors[[index[clip_id] for clip_id in members]], axis=0))
        member_counts[style] = len(members)
    stacked = np.stack(style_vectors).astype(np.float32)
    if not np.isfinite(stacked).all():
        raise ValueError("generated style bank contains non-finite values")

    config = _json_object(source_config_file.resolve(), label="source model config")
    data = config.get("data")
    if not isinstance(data, dict):
        raise ValueError("source model config has no data object")
    data["num_styles"] = len(names)
    data["style2id"] = {name: offset for offset, name in enumerate(names)}
    output_config_file = output_config_file.resolve()
    output_vectors_file = output_vectors_file.resolve()
    output_config_file.parent.mkdir(parents=True, exist_ok=True)
    output_vectors_file.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".nanami-style-bank-", dir=output_config_file.parent))
    try:
        staged_config = staging / "config.json"
        staged_vectors = staging / "style_vectors.npy"
        staged_config.write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        np.save(staged_vectors, stacked)
        os.replace(staged_config, output_config_file)
        os.replace(staged_vectors, output_vectors_file)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "schema_version": 1,
        "status": "STYLE_BANK_GENERATED_NOT_HUMAN_ACCEPTED",
        "style2id": data["style2id"],
        "member_counts": member_counts,
        "vector_shape": list(stacked.shape),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    mine = sub.add_parser("mine")
    mine.add_argument("--dataset-dir", type=Path, required=True)
    mine.add_argument("--style-plan-file", type=Path, required=True)
    mine.add_argument("--output-dir", type=Path, required=True)
    mine.add_argument("--per-style", type=int, default=12)
    build = sub.add_parser("build")
    build.add_argument("--dataset-dir", type=Path, required=True)
    build.add_argument("--style-plan-file", type=Path, required=True)
    build.add_argument("--source-config-file", type=Path, required=True)
    build.add_argument("--output-config-file", type=Path, required=True)
    build.add_argument("--output-vectors-file", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "mine":
        receipt = mine_review_candidates(
            dataset_dir=args.dataset_dir,
            style_plan_file=args.style_plan_file,
            per_style=args.per_style,
        )
        materialize_review_candidates(receipt=receipt, output_dir=args.output_dir)
    else:
        receipt = build_style_bank(
            dataset_dir=args.dataset_dir,
            style_plan_file=args.style_plan_file,
            source_config_file=args.source_config_file,
            output_config_file=args.output_config_file,
            output_vectors_file=args.output_vectors_file,
        )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
