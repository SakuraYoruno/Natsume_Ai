#!/usr/bin/env python
"""Run the three trusted NE-04 experiments on real digest-pinned Docker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.migration_runner import SQLiteMigrationRunner  # noqa: E402
from host_brain.core.subprocess_utils import run as silent_run  # noqa: E402
from host_brain.evolution_lab.production_composition import (  # noqa: E402
    build_trusted_evolution_lab_composition,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run real Docker baseline/candidate arms for Research Planning, "
            "Proactive and Memory Write."
        )
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--owner-id", default="ne04-golden-operator")
    parser.add_argument("--receipt-path")
    args = parser.parse_args(argv)

    commit = str(args.commit_sha).strip().lower()
    _require_clean_commit(commit)
    db_path = Path(args.db_path).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne04-golden-suite:{commit[:12]}"
    )
    if (
        not migrated.success
        or int(migrated.summary.get("latest_version", 0)) < 139
    ):
        raise RuntimeError(
            f"NE04_MIGRATION_FAILED:{migrated.reason_code}"
        )
    composition = build_trusted_evolution_lab_composition(
        db_path=db_path,
        runtime_root=runtime_root,
        image_reference=args.image_reference,
    )
    try:
        result = composition.golden_experiments.run_suite(
            workspace_id=args.workspace_id,
            commit_sha=commit,
            owner_id=args.owner_id,
        )
    finally:
        composition.close()
    payload = {
        "schema": "natsume.ne04.golden-experiment-run.v1",
        "status": result.receipt.status,
        "commit_sha": commit,
        "migration_head": result.receipt.migration_head,
        "suite_receipt": result.receipt.to_dict(),
        "report_digests": {
            kind: report.report_digest
            for kind, report in result.reports.items()
        },
        "proposal_ids": {
            kind: proposal.proposal_id
            for kind, proposal in result.proposals.items()
        },
        "production_side_effects": False,
        "production_influence_enabled": False,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    if args.receipt_path:
        receipt_path = Path(args.receipt_path).expanduser().resolve()
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


def _require_clean_commit(expected: str) -> None:
    head = silent_run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    status = silent_run(
        ["git", "status", "--porcelain"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if (
        head.returncode != 0
        or status.returncode != 0
        or str(head.stdout or "").strip().lower() != expected
        or str(status.stdout or "").strip()
    ):
        raise RuntimeError(
            "NE04_GOLDEN_REQUIRES_EXACT_CLEAN_COMMIT"
        )


if __name__ == "__main__":
    raise SystemExit(main())
