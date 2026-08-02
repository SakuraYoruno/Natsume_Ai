"""Package one explicit Style-Bert-VITS2 checkpoint for the Edge worker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping


SCHEMA_VERSION = "natsume.style-bert-vits2.edge-release.v1"
MANIFEST_NAME = "natsume_style_bert_vits2_manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _safe_model_id(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    if not result or len(result) > 128:
        raise ValueError("model_id must contain 1-128 safe characters")
    return result


def _validated_style_map(
    *,
    style_plan: Mapping[str, Any],
    model_config: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    raw_style2id = (
        model_config.get("data", {}).get("style2id")
        if isinstance(model_config.get("data"), Mapping)
        else None
    )
    if not isinstance(raw_style2id, Mapping) or not raw_style2id:
        raise ValueError("model config must contain data.style2id")
    available = {str(value) for value in raw_style2id}
    raw_map = style_plan.get("runtime_style_map")
    if not isinstance(raw_map, Mapping) or not raw_map:
        raise ValueError("style plan must contain runtime_style_map")
    result: dict[str, dict[str, Any]] = {}
    for raw_profile, raw_binding in raw_map.items():
        if not isinstance(raw_binding, Mapping):
            raise ValueError(f"style binding must be an object: {raw_profile}")
        profile = str(raw_profile or "").strip().upper()
        style = str(raw_binding.get("style") or "").strip()
        try:
            weight = float(raw_binding.get("weight"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid style weight: {profile}") from exc
        if not profile or style not in available or not 0.0 <= weight <= 2.0:
            raise ValueError(f"invalid style binding: {profile}")
        result[profile] = {"style": style, "weight": weight}
    return dict(sorted(result.items()))


def _link_or_copy(source: Path, destination: Path) -> str:
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def _replaceable_release(output_dir: Path) -> bool:
    manifest = output_dir / MANIFEST_NAME
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("schema_version") == SCHEMA_VERSION


def build_release(
    *,
    model_file: Path,
    config_file: Path,
    style_vectors_file: Path,
    style_plan_file: Path,
    output_dir: Path,
    model_id: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    sources = {
        "model": model_file.resolve(),
        "config": config_file.resolve(),
        "styles": style_vectors_file.resolve(),
    }
    for label, path in sources.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label} asset does not exist: {path}")
    if sources["model"].suffix.lower() != ".safetensors":
        raise ValueError("model_file must be an explicit .safetensors checkpoint")
    model_config = _json_object(sources["config"], label="model config")
    style_plan = _json_object(style_plan_file.resolve(), label="style plan")
    if str(style_plan.get("language") or "").strip().upper() != "JP":
        raise ValueError("style plan must use Japanese")
    runtime_style_map = _validated_style_map(
        style_plan=style_plan,
        model_config=model_config,
    )

    output_dir = output_dir.resolve()
    if output_dir.exists() and not overwrite:
        raise FileExistsError(f"release already exists: {output_dir}")
    if output_dir.exists() and not _replaceable_release(output_dir):
        raise ValueError("refusing to overwrite a directory without a matching release manifest")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        names = {
            "model": sources["model"].name,
            "config": "config.json",
            "styles": "style_vectors.npy",
        }
        methods: dict[str, str] = {}
        for label, source in sources.items():
            methods[label] = _link_or_copy(source, staging / names[label])
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "backend": "style_bert_vits2",
            "model_id": _safe_model_id(model_id),
            "voice_language": "ja",
            "model_file": names["model"],
            "config_file": names["config"],
            "style_vectors_file": names["styles"],
            "sha256": {
                label: _sha256(staging / names[label])
                for label in ("model", "config", "styles")
            },
            "runtime_style_map": runtime_style_map,
            "source_style_plan_sha256": _sha256(style_plan_file.resolve()),
            "materialization": methods,
            "promotion_status": "CANDIDATE_NOT_HUMAN_ACCEPTED",
        }
        (staging / MANIFEST_NAME).write_text(
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-file", type=Path, required=True)
    parser.add_argument("--config-file", type=Path, required=True)
    parser.add_argument("--style-vectors-file", type=Path, required=True)
    parser.add_argument("--style-plan-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest = build_release(
        model_file=args.model_file,
        config_file=args.config_file,
        style_vectors_file=args.style_vectors_file,
        style_plan_file=args.style_plan_file,
        output_dir=args.output_dir,
        model_id=args.model_id,
        overwrite=args.overwrite,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
