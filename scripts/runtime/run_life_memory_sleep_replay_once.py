#!/usr/bin/env python
"""Preview or run one LifeMemory sleep-replay pass against the runtime ledger."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory.consolidator import LifeMemoryConsolidator
from host_brain.memory.life_memory.sleep import MemorySleepReplayTask
from host_brain.memory.life_memory.store import LifeMemoryStore


DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
DEFAULT_OUT_DIR = "runtime/host/logs"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _resolve_db_path(explicit: str) -> Path:
    raw = str(explicit or os.environ.get("Natsume_AUDIT_DB") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return _resolve_path(raw)


def _compact_proposal(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "semantic_kind": item.get("semantic_kind"),
        "summary": item.get("summary"),
        "confidence": item.get("confidence"),
        "source_episode_ids": list(item.get("source_episode_ids") or []),
        "source_collection_ids": list(item.get("source_collection_ids") or []),
        "source_entity_ids": list(item.get("source_entity_ids") or []),
        "source_refs": list(item.get("source_refs") or [])[:8],
        "tags": list(item.get("tags") or []),
    }


def _compact_episode(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "episode_id": item.get("episode_id"),
        "episode_kind": item.get("episode_kind"),
        "action": item.get("action"),
        "truth_state": item.get("truth_state"),
        "retention_state": item.get("retention_state"),
        "salience": item.get("salience"),
        "memory_strength": item.get("memory_strength"),
        "source_ref": item.get("source_ref"),
        "summary": item.get("summary"),
    }


def _compact_compaction_group(group: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "source_count": len(group),
        "episode_ids": [item.get("episode_id") for item in group],
        "episode_kind": group[0].get("episode_kind") if group else "",
        "session_id": group[0].get("session_id") if group else "",
        "items": [_compact_episode(item) for item in group[:8]],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview or run LifeMemory sleep replay.")
    parser.add_argument("--db-path", default="", help="SQLite audit ledger path. Defaults to Natsume_AUDIT_DB or host_brain/data/audit.sqlite3.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--proposal-limit", type=int, default=120)
    parser.add_argument("--semantic-limit", type=int, default=12)
    parser.add_argument("--replay-limit", type=int, default=30)
    parser.add_argument("--forgetting-limit", type=int, default=500)
    parser.add_argument("--compaction-limit", type=int, default=6)
    parser.add_argument("--apply", action="store_true", help="Actually update forgetting state, compaction summaries, semantic_memory, and reflection evidence.")
    parser.add_argument("--out", default="", help="Write JSON report to this path. Defaults to runtime/host/logs/life_memory_sleep_replay_once_*.json.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_env_file(ROOT / ".env")
    _load_env_file(_resolve_path(str(args.env_file)))

    db_path = _resolve_db_path(str(args.db_path))
    if not db_path.exists():
        print(json.dumps({"status": "ERROR", "reason_code": "DB_NOT_FOUND", "db_path": str(db_path)}, ensure_ascii=False))
        return 2

    ledger = SQLiteLedger(db_path=db_path)
    try:
        store = LifeMemoryStore(ledger=ledger)
        consolidator = LifeMemoryConsolidator(store=store)
        sleep_task = MemorySleepReplayTask(store=store)
        current = int(time.time() * 1000)
        if args.apply:
            run_result = sleep_task.run_once(
                now_ts_ms_value=current,
                replay_limit=max(1, int(args.replay_limit)),
                forgetting_batch_limit=max(1, int(args.forgetting_limit)),
                semantic_limit=max(1, int(args.semantic_limit)),
                compaction_group_limit=max(0, int(args.compaction_limit)),
            )
            mode = "apply"
            semantic_cleanup = run_result.get("semantic_cleanup")
            semantic_growth = run_result.get("semantic_growth")
            semantic_gap_report = consolidator.semantic_growth_gap_report(
                limit=max(30000, int(args.proposal_limit) * 20)
            )
            proposals: list[dict[str, Any]] = []
            replay_candidates: list[dict[str, Any]] = []
            compaction_groups: list[dict[str, Any]] = []
        else:
            run_result = {"status": "DRY_RUN", "detail": "No ledger writes were performed."}
            semantic_cleanup = consolidator.prune_synthetic_semantic_memories(
                dry_run=True,
                limit=max(200, int(args.proposal_limit)),
            )
            proposals = [
                _compact_proposal(item)
                for item in consolidator.build_semantic_proposals(
                    limit=max(1, int(args.proposal_limit)),
                    max_records=max(1, int(args.semantic_limit)),
                )
            ]
            semantic_growth = consolidator.semantic_growth_diagnostics(limit=max(200, int(args.proposal_limit) * 3))
            semantic_gap_report = consolidator.semantic_growth_gap_report(
                limit=max(30000, int(args.proposal_limit) * 20)
            )
            replay_candidates = [
                _compact_episode(item)
                for item in sleep_task.select_replay_batch(limit=max(1, int(args.replay_limit)))
            ]
            compaction_groups = [
                _compact_compaction_group(group)
                for group in sleep_task.select_compaction_groups(
                    now_ts_ms_value=current,
                    group_limit=max(0, int(args.compaction_limit)),
                )
            ]
            mode = "dry_run"
        report = {
            "status": "OK",
            "mode": mode,
            "db_path": str(db_path),
            "ts_ms": current,
            "proposal_count": len(proposals),
            "proposals": proposals,
            "replay_candidate_count": len(replay_candidates),
            "replay_candidates": replay_candidates,
            "compaction_group_count": len(compaction_groups),
            "compaction_groups": compaction_groups,
            "semantic_cleanup": semantic_cleanup,
            "semantic_growth": semantic_growth,
            "semantic_gap_report": semantic_gap_report,
            "run_result": run_result,
        }
    finally:
        ledger.close()

    out_path = _resolve_path(str(args.out)) if args.out else ROOT / DEFAULT_OUT_DIR / f"life_memory_sleep_replay_once_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": "OK",
                "mode": report["mode"],
                "proposal_count": report["proposal_count"],
                "compaction_group_count": report["compaction_group_count"],
                "out": str(out_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
