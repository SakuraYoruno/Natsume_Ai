"""Artifact-only BioOps daily cycle runner.

This module is intentionally sidecar-friendly: it produces observable artifacts
for WebUI/CI review and does not register a HostMain background loop by itself.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from host_brain.bio_ops.artifact_store import BioOpsArtifactStore
from host_brain.bio_ops.contracts import BioOpsSchedule
from host_brain.bio_ops.owner_inbox import BioOpsOwnerInbox
from host_brain.bio_ops.runner import BioOpsRunner
from host_brain.agent_runtime.artifact_store import REPO_ROOT, sanitize_segment
from host_brain.core.ids import now_ms


DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "observable_effects"
BIO_OPS_DAILY_SCHEDULE_ID = "bio_ops_daily_cycle"
BIO_OPS_DAILY_WRITE_POLICY = "bio_ops_daily_artifacts_only"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _default_run_id() -> str:
    return time.strftime("bio_ops_daily_%Y%m%d_%H%M%S", time.localtime())


def _status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in results:
        status = str(_as_dict(item).get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        counts[status] = int(counts.get(status, 0)) + 1
    return counts


def _next_daily_run_ms(*, hour: int, minute: int, now_ts: float | None = None) -> int:
    now_value = float(now_ts if now_ts is not None else time.time())
    now_local = time.localtime(now_value)
    target_tuple = (
        now_local.tm_year,
        now_local.tm_mon,
        now_local.tm_mday,
        max(0, min(int(hour), 23)),
        max(0, min(int(minute), 59)),
        0,
        now_local.tm_wday,
        now_local.tm_yday,
        now_local.tm_isdst,
    )
    target_ts = time.mktime(target_tuple)
    if target_ts <= now_value:
        target_ts += 24 * 60 * 60
    return int(target_ts * 1000)


def build_bio_ops_daily_schedule(
    *,
    enabled: bool = True,
    hour: int = 4,
    minute: int = 20,
    now_ts: float | None = None,
) -> BioOpsSchedule:
    return BioOpsSchedule(
        schedule_id=BIO_OPS_DAILY_SCHEDULE_ID,
        capability="all",
        cadence=f"daily@{max(0, min(int(hour), 23)):02d}:{max(0, min(int(minute), 59)):02d}",
        enabled=bool(enabled),
        last_run_ms=None,
        next_run_ms=_next_daily_run_ms(hour=hour, minute=minute, now_ts=now_ts),
    )


def run_bio_ops_daily_cycle_artifact_run(
    *,
    output_root: str | Path = DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT,
    run_id: str | None = None,
    inputs: dict[str, Any] | None = None,
    schedule: BioOpsSchedule | None = None,
) -> dict[str, Any]:
    root = Path(output_root).expanduser()
    safe_run_id = sanitize_segment(run_id or _default_run_id(), "bio_ops_daily")
    run_dir = root / safe_run_id
    store = BioOpsArtifactStore(root=run_dir)
    runner = BioOpsRunner(artifact_store=BioOpsArtifactStore(root=run_dir / "bio_ops_task_artifacts"))

    common_inputs = {
        "evidence_refs": ["bio_ops_daily_cycle"],
        "message": "",
        "planned_path": [],
        **_as_dict(inputs),
    }
    cycle = runner.run_daily_cycle(inputs=common_inputs)
    results = [_as_dict(item) for item in _as_list(cycle.get("results"))]
    counts = _status_counts(results)
    review_required_count = sum(1 for item in results if bool(item.get("review_required")))
    candidate_count = int(counts.get("CANDIDATE") or 0)

    full_artifact = store.write_json(run_dir / "bio_ops_daily_full.json", cycle, artifact_type="bio_ops_daily_full")
    summary_payload = {
        "status": cycle.get("status") or "AUDITED",
        "cycle": cycle.get("cycle") or "daily",
        "capability_count": int(cycle.get("capability_count") or len(results)),
        "status_counts": counts,
        "candidate_count": candidate_count,
        "review_required_count": review_required_count,
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "write_policy": BIO_OPS_DAILY_WRITE_POLICY,
        "owner_gated": True,
        "created_at_ms": now_ms(),
    }
    summary_artifact = store.write_json(run_dir / "bio_ops_daily_summary.json", summary_payload, artifact_type="bio_ops_daily_summary")

    owner_inbox = BioOpsOwnerInbox(source_root=run_dir, inbox_root=run_dir / "owner_inbox").scan()
    schedule_payload = (schedule or build_bio_ops_daily_schedule()).to_dict()
    schedule_receipt = {
        "schedule": schedule_payload,
        "run_id": safe_run_id,
        "run_dir": str(run_dir),
        "status": "COMPLETED",
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "write_policy": BIO_OPS_DAILY_WRITE_POLICY,
        "created_at_ms": now_ms(),
    }
    schedule_artifact = store.write_json(run_dir / "bio_ops_daily_schedule_receipt.json", schedule_receipt, artifact_type="bio_ops_daily_schedule_receipt")

    manifest = {
        "ok": True,
        "run_id": safe_run_id,
        "run_dir": str(run_dir),
        "status": summary_payload["status"],
        "bio_ops_capability_count": summary_payload["capability_count"],
        "bio_ops_status_counts": counts,
        "pending_owner_adoptions": int(owner_inbox.get("status_counts", {}).get("PENDING_REVIEW") or 0),
        "paths": {
            "daily_full": str(run_dir / "bio_ops_daily_full.json"),
            "daily_summary": str(run_dir / "bio_ops_daily_summary.json"),
            "schedule_receipt": str(run_dir / "bio_ops_daily_schedule_receipt.json"),
        },
        "artifacts": [full_artifact.to_dict(), summary_artifact.to_dict(), schedule_artifact.to_dict()],
        "owner_inbox": {
            "count": owner_inbox.get("count", 0),
            "status_counts": owner_inbox.get("status_counts", {}),
        },
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "write_policy": BIO_OPS_DAILY_WRITE_POLICY,
        "message": "BioOps daily cycle completed as artifacts only; owner organs must adopt candidates explicitly.",
    }
    manifest_artifact = store.write_json(run_dir / "summary.json", manifest, artifact_type="observable_summary")
    manifest["artifacts"] = [*manifest["artifacts"], manifest_artifact.to_dict()]
    return manifest


__all__ = [
    "BIO_OPS_DAILY_SCHEDULE_ID",
    "BIO_OPS_DAILY_WRITE_POLICY",
    "DEFAULT_BIO_OPS_DAILY_OUTPUT_ROOT",
    "build_bio_ops_daily_schedule",
    "run_bio_ops_daily_cycle_artifact_run",
]
