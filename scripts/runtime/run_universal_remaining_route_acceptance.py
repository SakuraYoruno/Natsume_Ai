#!/usr/bin/env python3
"""Append Scheduler, Internal and Operator acceptance routes to one runtime DB."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.acceptance.universal_remaining_routes import (  # noqa: E402
    append_remaining_routes,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run real local Scheduler/Internal/Operator governed application "
            "boundaries against one existing canonical acceptance SQLite DB."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        required=True,
        help="Existing file-backed SQLite database containing Host route runs.",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        required=True,
        help="Ignored local directory for outer Operator outcome evidence.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="JSON file receiving the three request ids.",
    )
    return parser


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    routes = asyncio.run(
        append_remaining_routes(
            database=args.database,
            evidence_root=args.evidence_root,
        )
    )
    payload = {
        "schema_version": "natsume.universal-route-request-map.v1",
        "routes": routes,
    }
    _write_json_atomic(args.output, payload)
    print(
        json.dumps(
            {
                "status": "PASS",
                "routes": routes,
                "output": str(args.output.expanduser().resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
