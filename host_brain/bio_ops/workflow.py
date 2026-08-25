"""Artifact-only BioOps automation workflow.

The workflow composes the daily cycle, owner inbox materialization, intake
packet generation, and owner queue consumption into one sidecar-friendly run.
It never writes durable memory, Bond, Canon, persona core, secrets, or
production configuration.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from host_brain.bio_ops.adoption import ADOPTION_DECISION_ACCEPTED
from host_brain.bio_ops.artifact_store import BioOpsArtifactStore, _as_dict, _as_list
from host_brain.bio_ops.daily_cycle import (
    BIO_OPS_DAILY_WRITE_POLICY,
    DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT,
    run_bio_ops_daily_cycle_artifact_run,
)
from host_brain.bio_ops.owner_consumer import BioOpsOwnerIntakeConsumer, OWNER_QUEUE_WRITE_POLICY
from host_brain.bio_ops.owner_inbox import BioOpsOwnerInbox
from host_brain.agent_runtime.artifact_store import sanitize_segment
from host_brain.core.ids import now_ms


BIO_OPS_WORKFLOW_WRITE_POLICY = "bio_ops_workflow_artifacts_only"


def _default_run_id() -> str:
    return time.strftime("bio_ops_workflow_%Y%m%d_%H%M%S", time.localtime())


def _write_demo_owner_decision_seed(run_dir: Path) -> dict[str, Any]:
    """Seed one accepted demo candidate so CI can verify the owner handoff path.

    This seed is intentionally separate from real daily-cycle candidates. It is
    useful for workflow smoke tests and observable demos, but it does not imply
    automatic acceptance of production candidates.
    """

    candidate_id = "workflow_demo_habit_candidate"
    task_dir = run_dir / "bio_ops_task_artifacts" / "habit_compiler" / "workflow_demo_habit_compiler"
    candidate = {
        "candidate_id": candidate_id,
        "task_id": "workflow_demo_habit_compiler",
        "capability": "habit_compiler",
        "owner_organ": "social_brain_agent",
        "output_kind": "habit_candidate",
        "summary": "Workflow smoke candidate staged for owner queue verification.",
        "payload": {
            "proposal": {
                "kind": "habit_candidate",
                "target": "candidate_queue",
                "habit": "Use owner-gated BioOps automation for durable changes.",
            }
        },
        "evidence_refs": ["workflow:bio_ops_owner_handoff_smoke"],
        "artifacts": [],
        "risk_flags": ["workflow_smoke_seed"],
        "review_required": True,
        "next_actions": ["social_brain_agent must explicitly own any durable import."],
        "status": "PENDING_REVIEW",
        "created_at_ms": now_ms(),
    }
    decision = {
        "candidate_id": candidate_id,
        "owner_organ": "social_brain_agent",
        "decision": ADOPTION_DECISION_ACCEPTED,
        "reason": "workflow smoke seed; not production auto-acceptance",
        "operator": "bio_ops_workflow",
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "write_policy": "owner_decision_artifact_only",
        "risk_flags": ["bio_ops_owner_decision_artifact_only", "workflow_smoke_seed"],
        "decided_at_ms": now_ms(),
    }
    store = BioOpsArtifactStore(root=run_dir)
    candidate_artifact = store.write_json(task_dir / "adoption_candidate.json", candidate, artifact_type="adoption_candidate")
    decision_artifact = store.write_json(task_dir / "adoption_decision.json", decision, artifact_type="adoption_decision")
    return {
        "enabled": True,
        "candidate_id": candidate_id,
        "candidate_path": str(task_dir / "adoption_candidate.json"),
        "decision_path": str(task_dir / "adoption_decision.json"),
        "artifacts": [candidate_artifact.to_dict(), decision_artifact.to_dict()],
        "direct_long_term_write": False,
        "durable_write_performed": False,
    }


def _workflow_status(*, daily_manifest: dict[str, Any], owner_queues: dict[str, Any], blocked_count: int) -> str:
    if not bool(daily_manifest.get("ok")):
        return "FAILED"
    if blocked_count > 0:
        return "DEGRADED"
    if int(owner_queues.get("count") or 0) > 0:
        return "QUEUED"
    return "AUDITED"


def run_bio_ops_automation_workflow(
    *,
    output_root: str | Path = DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT,
    run_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    seed_demo_owner_decision: bool = False,
    materialize_owner_handoff: bool = True,
    consume_owner_queues: bool = True,
    import_owner_durable_candidates: bool = True,
    owner_candidate_state_root: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the full artifact-only BioOps automation workflow."""

    root = Path(output_root).expanduser()
    safe_run_id = sanitize_segment(run_id or _default_run_id(), "bio_ops_workflow")
    run_dir = root / safe_run_id

    daily_manifest = run_bio_ops_daily_cycle_artifact_run(
        output_root=root,
        run_id=safe_run_id,
        inputs={
            "evidence_refs": ["bio_ops_workflow"],
            **_as_dict(inputs),
        },
    )
    demo_seed = _write_demo_owner_decision_seed(run_dir) if seed_demo_owner_decision else {"enabled": False}

    inbox = BioOpsOwnerInbox(source_root=run_dir, inbox_root=run_dir / "owner_inbox")
    owner_inbox_before = inbox.scan()
    if materialize_owner_handoff:
        materialized = inbox.materialize_ready()
        intake_packets = inbox.materialize_intake_packets()
        import_candidates = inbox.scan_import_candidates()
    else:
        materialized = {"ok": True, "materialized_count": 0, "skipped_count": 0, "artifacts": [], "skipped": []}
        intake_packets = {"ok": True, "packet_count": 0, "blocked_count": 0, "packets": [], "artifacts": [], "blocked": []}
        import_candidates = {"ok": True, "count": 0, "status_counts": {}, "items": []}

    consumer = BioOpsOwnerIntakeConsumer(inbox_root=run_dir / "owner_inbox", queue_root=run_dir / "owner_queues")
    if consume_owner_queues:
        owner_consumer = consumer.consume_all(dry_run=dry_run)
    else:
        owner_consumer = {
            "ok": True,
            "dry_run": bool(dry_run),
            "packet_count": 0,
            "blocked_count": 0,
            "queued_count": 0,
            "receipts": [],
            "blocked": [],
            "direct_long_term_write": False,
            "durable_write_performed": False,
        }
    owner_queues = consumer.scan_queue()
    from host_brain.organ_ops.owner_candidate_store import (
        OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
        OwnerCandidateStateStore,
    )

    owner_state_store = OwnerCandidateStateStore(
        queue_root=run_dir / "owner_queues",
        state_root=owner_candidate_state_root or run_dir / "owner_state",
    )
    if import_owner_durable_candidates and consume_owner_queues:
        owner_durable_import = owner_state_store.import_all(dry_run=dry_run)
    else:
        owner_durable_import = {
            "ok": True,
            "dry_run": bool(dry_run),
            "source_queue_count": int(owner_queues.get("count") or 0),
            "imported_count": 0,
            "already_imported_count": 0,
            "blocked_count": 0,
            "imported": [],
            "already_imported": [],
            "blocked": [],
            "direct_long_term_write": False,
            "durable_write_performed": False,
            "durable_memory_write_performed": False,
            "owner_candidate_state_write_performed": False,
            "write_policy": OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
        }
    owner_candidate_state = owner_state_store.scan_state()
    owner_runtime_hints = owner_state_store.scan_runtime_hints()

    skipped = _as_list(materialized.get("skipped"))
    blocked = [
        *_as_list(intake_packets.get("blocked")),
        *_as_list(owner_consumer.get("blocked")),
        *_as_list(owner_durable_import.get("blocked")),
    ]
    blocked_count = len(blocked)
    status = _workflow_status(daily_manifest=daily_manifest, owner_queues=owner_queues, blocked_count=blocked_count)
    manifest = {
        "ok": status in {"AUDITED", "QUEUED"},
        "status": status,
        "run_id": safe_run_id,
        "run_dir": str(run_dir),
        "write_policy": BIO_OPS_WORKFLOW_WRITE_POLICY,
        "daily_write_policy": BIO_OPS_DAILY_WRITE_POLICY,
        "owner_queue_write_policy": OWNER_QUEUE_WRITE_POLICY,
        "owner_durable_candidate_write_policy": OWNER_DURABLE_CANDIDATE_WRITE_POLICY,
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "durable_memory_write_performed": False,
        "owner_candidate_state_write_performed": bool(owner_durable_import.get("owner_candidate_state_write_performed")),
        "materialize_owner_handoff": bool(materialize_owner_handoff),
        "consume_owner_queues": bool(consume_owner_queues),
        "import_owner_durable_candidates": bool(import_owner_durable_candidates),
        "dry_run": bool(dry_run),
        "demo_owner_decision_seed": demo_seed,
        "daily_cycle": daily_manifest,
        "owner_inbox_before": owner_inbox_before,
        "owner_handoff": {
            "materialized": materialized,
            "import_candidates": import_candidates,
            "intake_packets": intake_packets,
            "consumer": owner_consumer,
            "queues": owner_queues,
            "durable_import": owner_durable_import,
            "owner_candidate_state": owner_candidate_state,
            "runtime_hints": owner_runtime_hints,
        },
        "counts": {
            "capabilities": int(daily_manifest.get("bio_ops_capability_count") or 0),
            "pending_owner_adoptions": int(owner_inbox_before.get("status_counts", {}).get("PENDING_REVIEW") or 0),
            "ready_owner_imports": int(import_candidates.get("status_counts", {}).get("READY_FOR_ORGAN_CONSUMPTION") or 0),
            "intake_packets": int(intake_packets.get("packet_count") or 0),
            "queued_owner_items": int(owner_queues.get("count") or 0),
            "imported_owner_candidates": int(owner_durable_import.get("imported_count") or 0),
            "owner_candidate_state_items": int(owner_candidate_state.get("count") or 0),
            "owner_runtime_hints": int(owner_runtime_hints.get("count") or 0),
            "skipped_owner_items": len(skipped),
            "blocked_owner_items": blocked_count,
        },
        "webui_routes": [
            f"/monitor/codex-bio-ops?run_id={safe_run_id}",
            f"/obs/codex-bio-ops?run_id={safe_run_id}",
            f"/obs/codex-bio-ops/owner-inbox?run_id={safe_run_id}",
            f"/obs/codex-bio-ops/owner-inbox/import-candidates?run_id={safe_run_id}",
            f"/obs/codex-bio-ops/owner-queues?run_id={safe_run_id}",
            f"/obs/codex-bio-ops/owner-state?run_id={safe_run_id}",
            f"/obs/codex-bio-ops/owner-state/runtime-hints?run_id={safe_run_id}",
        ],
        "next_actions": [
            "Review queued owner items in the Codex/BioOps WebUI.",
            "Owner organs may review owner_candidate_state artifacts before applying any real long-term state change.",
            "Do not treat workflow artifacts as completed long-term memory, Bond, Canon, or persona writes.",
        ],
        "created_at_ms": now_ms(),
    }
    store = BioOpsArtifactStore(root=run_dir)
    artifact = store.write_json(run_dir / "bio_ops_workflow_manifest.json", manifest, artifact_type="bio_ops_workflow_manifest")
    manifest["artifact"] = artifact.to_dict()
    return manifest


__all__ = [
    "BIO_OPS_WORKFLOW_WRITE_POLICY",
    "run_bio_ops_automation_workflow",
]
