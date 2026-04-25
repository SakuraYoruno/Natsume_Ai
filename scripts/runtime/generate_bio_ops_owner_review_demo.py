"""Generate a deterministic BioOps owner-review observable-effects run.

The script writes only artifacts under the chosen output root. It is meant for
local verification of the owner-gated BioOps chain:

adoption_candidate -> adoption_decision -> owner_import_candidate -> owner_intake_packet
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))

from host_brain.bio_ops.adoption import ADOPTION_DECISION_ACCEPTED
from host_brain.bio_ops.owner_inbox import BioOpsOwnerInbox
from host_brain.codex_runtime.artifact_store import REPO_ROOT
from host_brain.core.ids import now_ms


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "observable_effects"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _candidate_payload(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "task_id": "demo_habit_compiler",
        "capability": "habit_compiler",
        "owner_organ": "social_brain_agent",
        "output_kind": "habit_candidate",
        "summary": "Demo habit candidate staged for owner review.",
        "payload": {
            "proposal": {
                "kind": "habit_candidate",
                "target": "candidate_queue",
                "habit": "Prefer owner-reviewed durable state changes.",
            }
        },
        "evidence_refs": ["demo:bio_ops_owner_review"],
        "artifacts": [],
        "risk_flags": [],
        "review_required": True,
        "next_actions": ["social_brain_agent must explicitly accept or reject this candidate."],
        "status": "PENDING_REVIEW",
        "created_at_ms": now_ms(),
    }


def _decision_payload(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "owner_organ": "social_brain_agent",
        "decision": ADOPTION_DECISION_ACCEPTED,
        "reason": "deterministic owner review demo",
        "operator": "generate_bio_ops_owner_review_demo",
        "direct_long_term_write": False,
        "write_policy": "owner_decision_artifact_only",
        "decided_at_ms": now_ms(),
        "risk_flags": ["bio_ops_owner_decision_artifact_only"],
    }


def generate_demo_run(*, output_root: str | Path = DEFAULT_OUTPUT_ROOT, run_id: str = "bio_ops_owner_review_demo") -> dict[str, Any]:
    root = Path(output_root).expanduser()
    run_dir = root / run_id
    candidate_id = "demo_habit_candidate"
    task_dir = run_dir / "bio_ops_task_artifacts" / "habit_compiler" / "demo_habit_compiler"
    candidate = _candidate_payload(candidate_id)
    decision = _decision_payload(candidate_id)

    _write_json(task_dir / "adoption_candidate.json", candidate)
    _write_json(task_dir / "adoption_decision.json", decision)
    _write_json(
        run_dir / "bio_ops_daily_full.json",
        {
            "status": "AUDITED",
            "cycle": "demo",
            "capability_count": 1,
            "results": [
                {
                    "status": "CANDIDATE",
                    "capability": "habit_compiler",
                    "summary": candidate["summary"],
                    "metadata": {
                        "bio_ops_adoption": {
                            "status": "PENDING_REVIEW",
                            "candidate": candidate,
                        }
                    },
                }
            ],
        },
    )
    _write_json(run_dir / "bio_ops_daily_summary.json", {"status": "AUDITED", "capability_count": 1, "status_counts": {"CANDIDATE": 1}})
    _write_json(
        run_dir / "summary.json",
        {
            "run_id": run_id,
            "bio_ops_capability_count": 1,
            "bio_ops_status_counts": {"CANDIDATE": 1},
            "paths": {
                "daily_full": str(run_dir / "bio_ops_daily_full.json"),
                "daily_summary": str(run_dir / "bio_ops_daily_summary.json"),
            },
        },
    )

    inbox = BioOpsOwnerInbox(source_root=run_dir, inbox_root=run_dir / "owner_inbox")
    materialized = inbox.materialize_ready()
    packets = inbox.materialize_intake_packets()
    imports = inbox.scan_import_candidates()

    manifest = {
        "ok": True,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "candidate_path": str(task_dir / "adoption_candidate.json"),
        "decision_path": str(task_dir / "adoption_decision.json"),
        "owner_inbox": materialized,
        "import_candidates": imports,
        "intake_packets": packets,
        "webui_routes": [
            f"/obs/codex-bio-ops?run_id={run_id}",
            f"/obs/codex-bio-ops/adoptions?run_id={run_id}&status=decided",
            f"/obs/codex-bio-ops/owner-inbox?run_id={run_id}",
            f"/obs/codex-bio-ops/owner-inbox/import-candidates?run_id={run_id}",
        ],
    }
    _write_json(run_dir / "owner_review_demo_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate BioOps owner-review observable artifacts.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Observable effects output root.")
    parser.add_argument("--run-id", default="bio_ops_owner_review_demo", help="Run directory name under output root.")
    args = parser.parse_args(argv)
    manifest = generate_demo_run(output_root=args.output_root, run_id=args.run_id)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
