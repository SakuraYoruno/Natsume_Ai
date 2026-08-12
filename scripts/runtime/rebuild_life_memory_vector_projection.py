"""从 SQLite 重新排队当前有效 Episode 的向量投影；默认只预览。"""

from __future__ import annotations

import argparse
from pathlib import Path

from host_brain.persistence.life_memory_repository import LifeMemoryRepository


def rebuild_life_memory_vector_projection(*, db_path: str | Path, dry_run: bool = True, limit: int = 100000) -> dict[str, int | str]:
    repository = LifeMemoryRepository(db_path, verify_schema=False)
    rows = repository.list_episode_records(
        statuses=("ACTIVE",), excluded_truth_states=("CORRECTED", "SUPPRESSED"),
        excluded_retention_states=("GONE",), order="oldest", limit=max(1, int(limit)),
    )
    if not dry_run:
        for row in rows:
            repository.enqueue_projection_event(
                episode_id=str(row["episode_id"]), event_type="UPSERT", reason_code="REBUILD",
                payload={"rebuild": True}, created_ts_ms=int(row.get("last_touched_ts_ms") or row.get("created_ts_ms") or 0),
            )
    return {"status": "DRY_RUN" if dry_run else "QUEUED", "episode_count": len(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="重建 LifeMemory 向量投影队列")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--apply", action="store_true", help="执行入队；默认仅预览")
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()
    print(rebuild_life_memory_vector_projection(db_path=args.db, dry_run=not args.apply, limit=args.limit))
