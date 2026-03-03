"""Download faster-whisper ASR models for Edge runtime.

Usage example:
    python scripts/runtime/download_edge_asr_model.py \
        --model large-v3 \
        --output-dir D:/AI_Models/faster-whisper-large-v3 \
        --auto-install
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MODEL_ALIAS_TO_REPO: dict[str, str] = {
    "tiny": "Systran/faster-whisper-tiny",
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "base": "Systran/faster-whisper-base",
    "base.en": "Systran/faster-whisper-base.en",
    "small": "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium": "Systran/faster-whisper-medium",
    "medium.en": "Systran/faster-whisper-medium.en",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Edge ASR model (faster-whisper compatible).",
    )
    parser.add_argument(
        "--model",
        default="large-v3",
        help="Model alias (e.g. large-v3) or full HF repo id (e.g. Systran/faster-whisper-large-v3).",
    )
    parser.add_argument(
        "--output-dir",
        default="runtime/models/faster-whisper-large-v3",
        help="Destination directory for downloaded model files.",
    )
    parser.add_argument(
        "--revision",
        default="main",
        help="Hugging Face revision/tag/commit. Default: main",
    )
    parser.add_argument(
        "--token",
        default="",
        help="Optional Hugging Face token. Defaults to HF_TOKEN env if omitted.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing output directory before downloading.",
    )
    parser.add_argument(
        "--auto-install",
        action="store_true",
        help="Auto-install huggingface_hub if missing.",
    )
    return parser.parse_args()


def _resolve_repo_id(model_arg: str) -> str:
    key = str(model_arg or "").strip()
    if not key:
        return MODEL_ALIAS_TO_REPO["large-v3"]
    if "/" in key:
        return key
    return MODEL_ALIAS_TO_REPO.get(key.lower(), key)


def _ensure_hf_snapshot_download(auto_install: bool):
    try:
        from huggingface_hub import snapshot_download  # type: ignore

        return snapshot_download
    except Exception as exc:
        if not auto_install:
            raise RuntimeError(
                "huggingface_hub is required. Run: "
                f"{sys.executable} -m pip install huggingface_hub"
            ) from exc

    install_cmd = [sys.executable, "-m", "pip", "install", "huggingface_hub>=0.23.0"]
    completed = subprocess.run(install_cmd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            "Failed to auto-install huggingface_hub.\n"
            f"stdout:\n{completed.stdout}\n\nstderr:\n{completed.stderr}"
        )

    from huggingface_hub import snapshot_download  # type: ignore

    return snapshot_download


def _download_model(
    *,
    snapshot_download,
    repo_id: str,
    revision: str,
    output_dir: Path,
    token: str | None,
) -> str:
    kwargs: dict[str, Any] = {
        "repo_id": repo_id,
        "revision": revision,
        "local_dir": str(output_dir),
        "token": token,
        "resume_download": True,
    }
    # Keep broad compatibility across huggingface_hub versions.
    try:
        return str(snapshot_download(local_dir_use_symlinks=False, **kwargs))
    except TypeError:
        return str(snapshot_download(**kwargs))


def main() -> int:
    args = _parse_args()
    repo_id = _resolve_repo_id(args.model)
    output_dir = Path(args.output_dir).expanduser().resolve()
    token = (str(args.token).strip() or None)

    if args.force and output_dir.exists():
        # Safe recursive delete within target path chosen by the operator.
        for p in sorted(output_dir.rglob("*"), reverse=True):
            if p.is_file() or p.is_symlink():
                p.unlink(missing_ok=True)
            elif p.is_dir():
                p.rmdir()
        output_dir.rmdir()
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshot_download = _ensure_hf_snapshot_download(auto_install=bool(args.auto_install))
    resolved_path = _download_model(
        snapshot_download=snapshot_download,
        repo_id=repo_id,
        revision=str(args.revision or "main").strip() or "main",
        output_dir=output_dir,
        token=token,
    )

    marker = output_dir / "_download_info.json"
    marker.write_text(
        json.dumps(
            {
                "repo_id": repo_id,
                "revision": str(args.revision or "main").strip() or "main",
                "resolved_path": resolved_path,
                "edge_env": {
                    "EDGE_ASR_ENABLED": "true",
                    "EDGE_ASR_MODEL_SIZE": str(output_dir),
                    "EDGE_ASR_DEVICE": "cuda",
                    "EDGE_ASR_COMPUTE_TYPE": "float16",
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "repo_id": repo_id,
                "revision": str(args.revision or "main").strip() or "main",
                "output_dir": str(output_dir),
                "resolved_path": resolved_path,
                "next_env": {
                    "EDGE_ASR_ENABLED": "true",
                    "EDGE_ASR_MODEL_SIZE": str(output_dir),
                    "EDGE_ASR_DEVICE": "cuda",
                    "EDGE_ASR_COMPUTE_TYPE": "float16",
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
