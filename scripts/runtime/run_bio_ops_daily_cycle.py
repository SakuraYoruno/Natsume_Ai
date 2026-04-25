"""Run an artifact-only BioOps daily cycle for WebUI/CI observation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))

from host_brain.bio_ops.daily_cycle import DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT, run_bio_ops_daily_cycle_artifact_run


def _inputs_from_args(args: argparse.Namespace) -> dict[str, Any]:
    evidence_refs = [item.strip() for item in str(args.evidence or "").split(",") if item.strip()]
    return {
        "evidence_refs": evidence_refs or ["bio_ops_daily_cycle_cli"],
        "message": str(args.message or ""),
        "planned_path": [item.strip() for item in str(args.planned_path or "").split(",") if item.strip()],
        "current_hour": args.current_hour,
        "user_stress": str(args.user_stress or ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run BioOps daily cycle into observable artifacts.")
    parser.add_argument("--output-root", default=str(DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT), help="Observable effects output root.")
    parser.add_argument("--run-id", default=None, help="Run directory name under output root.")
    parser.add_argument("--evidence", default="bio_ops_daily_cycle_cli", help="Comma-separated evidence refs.")
    parser.add_argument("--message", default="", help="Optional recent message sample.")
    parser.add_argument("--planned-path", default="", help="Comma-separated planned path sample.")
    parser.add_argument("--current-hour", type=int, default=None, help="Optional current hour sample.")
    parser.add_argument("--user-stress", default="", help="Optional user stress sample.")
    args = parser.parse_args(argv)
    manifest = run_bio_ops_daily_cycle_artifact_run(
        output_root=args.output_root,
        run_id=args.run_id,
        inputs=_inputs_from_args(args),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
