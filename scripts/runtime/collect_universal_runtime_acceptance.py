"""Collect a commit-bound Universal Governed Runtime acceptance receipt."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from host_brain.release.universal_route_acceptance import (  # noqa: E402
    REQUIRED_ROUTES,
    collect_universal_route_acceptance,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read one canonical SQLite database in mode=ro/query_only and "
            "emit a seven-route commit-bound acceptance receipt"
        )
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="ROUTE=REQUEST_ID",
        help=(
            "Bind one logical route to its real Host request. Repeat for "
            + ", ".join(REQUIRED_ROUTES)
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _route_mapping(values: list[str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for raw in values:
        route, separator, request_id = str(raw or "").partition("=")
        route = route.strip().lower()
        request_id = request_id.strip()
        if (
            separator != "="
            or route not in REQUIRED_ROUTES
            or not request_id
            or route in selected
        ):
            raise ValueError(f"invalid or duplicate --route binding: {raw}")
        selected[route] = request_id
    if not selected:
        raise ValueError("at least one --route binding is required")
    return selected


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    args = _parser().parse_args()
    receipt = collect_universal_route_acceptance(
        repository_root=args.repository_root,
        canonical_db_path=args.database,
        request_ids=_route_mapping(args.route),
    )
    _write_json_atomic(args.output, receipt)
    print(
        json.dumps(
            {
                "status": receipt["overall_status"],
                "run_id": receipt["run_id"],
                "receipt_digest": receipt["receipt_digest"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
