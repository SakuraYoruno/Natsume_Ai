"""Sync persona utterance lines into Chroma vector collection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.memory.persona_utterance_vector_projection import (
    DEFAULT_CHROMA_BACKEND,
    DEFAULT_CHROMA_PATH,
    DEFAULT_PERSONA_UTTERANCE_COLLECTION,
    DEFAULT_STYLE_ARTIFACT_ROOT,
    load_env_file,
    sync_persona_utterance_vector_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync persona utterance lines into Chroma vector collection.")
    parser.add_argument("--style-artifact-root", default=DEFAULT_STYLE_ARTIFACT_ROOT, help="Style artifact root path.")
    parser.add_argument("--collection", default=DEFAULT_PERSONA_UTTERANCE_COLLECTION, help="Target Chroma collection.")
    parser.add_argument("--backend", choices=("http", "persistent"), default=DEFAULT_CHROMA_BACKEND, help="Chroma backend mode.")
    parser.add_argument("--chroma-path", default=DEFAULT_CHROMA_PATH, help="Used when --backend=persistent.")
    parser.add_argument("--chroma-endpoint", default="", help="Optional CHROMA_ENDPOINT override.")
    parser.add_argument("--env-file", default=".env", help="Optional env profile file.")
    parser.add_argument("--node-id", default="", help="Optional embedding node id.")
    parser.add_argument("--ja-only", action="store_true", help="Sync Japanese utterance lines only.")
    parser.add_argument("--cn-only", action="store_true", help="Sync Chinese utterance lines only.")
    parser.add_argument("--summary-out", default="", help="Optional path to write summary JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    include_ja = not bool(args.cn_only)
    include_cn = not bool(args.ja_only)
    if not include_ja and not include_cn:
        print("[error] both ja and cn sync disabled", file=sys.stderr)
        return 2

    env_values = load_env_file(Path(args.env_file).resolve()) if args.env_file else {}
    overrides: dict[str, Any] = {}
    if str(args.chroma_endpoint).strip():
        overrides["CHROMA_ENDPOINT"] = str(args.chroma_endpoint).strip()

    try:
        summary = sync_persona_utterance_vector_index(
            style_artifact_root=Path(args.style_artifact_root).resolve(),
            collection=str(args.collection),
            backend=str(args.backend),
            chroma_path=Path(args.chroma_path).resolve(),
            include_ja=include_ja,
            include_cn=include_cn,
            env=env_values,
            local_overrides=overrides,
            node_id=(str(args.node_id).strip() or None),
        )
    except Exception as exc:
        print(f"[error] sync failed: {exc}", file=sys.stderr)
        return 3

    if args.summary_out:
        out_path = Path(args.summary_out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

