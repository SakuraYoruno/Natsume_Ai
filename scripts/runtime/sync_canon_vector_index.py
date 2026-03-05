"""Sync canon SSOT files into Chroma vector index.

Default behavior:
- Read style canon files from host_brain/canon/style
- Read world canon files from host_brain/canon/world
- Upsert projection docs into Chroma collection "canon"
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

# Allow standalone execution from repository root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_brain.memory.canon_vector_projection import (
    DEFAULT_CANON_COLLECTION,
    DEFAULT_CHROMA_BACKEND,
    DEFAULT_CHROMA_PATH,
    DEFAULT_STYLE_ROOT,
    DEFAULT_WORLD_ROOT,
    collect_canon_projection_documents,
    load_env_file,
    sync_canon_vector_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync canon SSOT files into Chroma vector collection.")
    parser.add_argument("--style-root", default=DEFAULT_STYLE_ROOT, help="Style canon root path.")
    parser.add_argument("--world-root", default=DEFAULT_WORLD_ROOT, help="World canon root path.")
    parser.add_argument(
        "--collection",
        default=DEFAULT_CANON_COLLECTION,
        help="Target Chroma collection name.",
    )
    parser.add_argument(
        "--backend",
        choices=("http", "persistent"),
        default=DEFAULT_CHROMA_BACKEND,
        help="Chroma backend mode.",
    )
    parser.add_argument(
        "--chroma-path",
        default=DEFAULT_CHROMA_PATH,
        help="Used when --backend=persistent.",
    )
    parser.add_argument(
        "--chroma-endpoint",
        default="",
        help="Optional CHROMA_ENDPOINT override (used by --backend=http).",
    )
    parser.add_argument(
        "--collection-dim",
        type=int,
        default=0,
        help="Optional explicit vector dim. 0 means auto-resolve from runtime config.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional env profile to load before sync.",
    )
    parser.add_argument(
        "--node-id",
        default="",
        help="Optional node id for RETR_EMBED route selection (fallback to env/default).",
    )
    parser.add_argument("--style-only", action="store_true", help="Sync style canon only.")
    parser.add_argument("--world-only", action="store_true", help="Sync world canon only.")
    parser.add_argument("--dry-run", action="store_true", help="Collect docs and print summary only.")
    parser.add_argument(
        "--summary-out",
        default="",
        help="Optional path to write sync summary JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    include_style = not bool(args.world_only)
    include_world = not bool(args.style_only)
    if not include_style and not include_world:
        print("[error] both style and world sync disabled", file=sys.stderr)
        return 2

    style_root = Path(args.style_root).resolve()
    world_root = Path(args.world_root).resolve()

    if args.dry_run:
        docs, issues = collect_canon_projection_documents(
            style_root=style_root,
            world_root=world_root,
            include_style=include_style,
            include_world=include_world,
        )
        style_count = sum(1 for row in docs if row.metadata.get("canon_kind") == "style")
        world_count = sum(1 for row in docs if row.metadata.get("canon_kind") == "world")
        summary: dict[str, Any] = {
            "success": True,
            "dry_run": True,
            "docs_collected": len(docs),
            "style_docs": style_count,
            "world_docs": world_count,
            "issues_count": len(issues),
            "issues": issues,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    env_values = load_env_file(Path(args.env_file).resolve()) if args.env_file else {}
    overrides: dict[str, Any] = {}
    if str(args.chroma_endpoint or "").strip():
        overrides["CHROMA_ENDPOINT"] = str(args.chroma_endpoint).strip()

    try:
        summary = sync_canon_vector_index(
            style_root=style_root,
            world_root=world_root,
            collection=str(args.collection),
            backend=str(args.backend),
            chroma_path=Path(args.chroma_path).resolve(),
            collection_dim=(int(args.collection_dim) if int(args.collection_dim) > 0 else None),
            include_style=include_style,
            include_world=include_world,
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
