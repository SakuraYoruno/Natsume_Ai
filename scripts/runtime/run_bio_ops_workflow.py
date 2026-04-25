"""Run the BioOps artifact-only automation workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))

from host_brain.bio_ops.daily_cycle import DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT
from host_brain.bio_ops.workflow import run_bio_ops_automation_workflow


def _inputs_from_args(args: argparse.Namespace) -> dict[str, Any]:
    evidence_refs = [item.strip() for item in str(args.evidence or "").split(",") if item.strip()]
    planned_path = [item.strip() for item in str(args.planned_path or "").split(",") if item.strip()]
    return {
        "evidence_refs": evidence_refs or ["bio_ops_workflow_cli"],
        "message": str(args.message or ""),
        "planned_path": planned_path,
        "current_hour": args.current_hour,
        "user_stress": str(args.user_stress or ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run BioOps workflow automation into observable artifacts.")
    parser.add_argument("--output-root", default=str(DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT), help="Observable effects output root.")
    parser.add_argument("--run-id", default=None, help="Run directory name under output root.")
    parser.add_argument("--evidence", default="bio_ops_workflow_cli", help="Comma-separated evidence refs.")
    parser.add_argument("--message", default="", help="Optional recent message sample.")
    parser.add_argument("--planned-path", default="", help="Comma-separated planned path sample.")
    parser.add_argument("--current-hour", type=int, default=None, help="Optional current hour sample.")
    parser.add_argument("--user-stress", default="", help="Optional user stress sample.")
    parser.add_argument(
        "--seed-demo-owner-decision",
        action="store_true",
        help="Seed one accepted demo candidate for workflow smoke verification. Does not auto-accept real candidates.",
    )
    parser.add_argument(
        "--skip-owner-handoff",
        action="store_true",
        help="Only run the daily cycle and owner inbox scan; do not materialize import candidates or intake packets.",
    )
    parser.add_argument(
        "--skip-owner-consume",
        action="store_true",
        help="Do not consume owner intake packets into owner queues.",
    )
    parser.add_argument(
        "--skip-owner-durable-import",
        action="store_true",
        help="Do not import queued owner items into owner candidate state artifacts.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate owner packets without writing queue items.")
    args = parser.parse_args(argv)

    manifest = run_bio_ops_automation_workflow(
        output_root=args.output_root,
        run_id=args.run_id,
        inputs=_inputs_from_args(args),
        seed_demo_owner_decision=bool(args.seed_demo_owner_decision),
        materialize_owner_handoff=not bool(args.skip_owner_handoff),
        consume_owner_queues=not bool(args.skip_owner_consume),
        import_owner_durable_candidates=not bool(args.skip_owner_durable_import),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
