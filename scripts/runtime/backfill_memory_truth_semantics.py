"""回填 Episode/Evidence 的发生来源与事实状态；默认只预览。"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def backfill_memory_truth_semantics(*, db_path: str | Path, dry_run: bool = True) -> dict[str, int | str]:
    conn = sqlite3.connect(str(db_path))
    try:
        episode_columns = {row[1] for row in conn.execute("PRAGMA table_info(life_episode)")}
        evidence_columns = {row[1] for row in conn.execute("PRAGMA table_info(life_evidence)")}
        if not {"occurrence_kind", "claim_truth"} <= episode_columns | evidence_columns:
            raise RuntimeError("记忆真相语义迁移尚未执行")
        evidence_rows = conn.execute("SELECT evidence_id, evidence_kind FROM life_evidence").fetchall()
        episode_rows = conn.execute("SELECT episode_id, truth_state FROM life_episode").fetchall()
        if not dry_run:
            conn.executemany(
                """UPDATE life_evidence SET occurrence_kind = CASE ?
                    WHEN 'user_text' THEN 'USER_SAID' WHEN 'assistant_reply' THEN 'ASSISTANT_SAID'
                    WHEN 'tool_result' THEN 'TOOL_RETURNED' WHEN 'gateway_message' THEN 'GATEWAY_RECEIVED'
                    WHEN 'vision_observation' THEN 'OBSERVED' ELSE 'SYSTEM_RECORDED' END,
                    claim_truth = CASE WHEN ? = 'assistant_reply' THEN 'NARRATIVE_ONLY' ELSE COALESCE(claim_truth, 'CANDIDATE') END
                    WHERE evidence_id = ?""",
                [(kind, kind, evidence_id) for evidence_id, kind in evidence_rows],
            )
            conn.executemany(
                "UPDATE life_episode SET occurrence_kind = 'SYSTEM_RECORDED', claim_truth = COALESCE(NULLIF(truth_state, ''), 'CANDIDATE') WHERE episode_id = ?",
                [(episode_id,) for episode_id, _truth_state in episode_rows],
            )
            conn.commit()
        return {"status": "DRY_RUN" if dry_run else "OK", "episode_count": len(episode_rows), "evidence_count": len(evidence_rows)}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填记忆真相语义")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--apply", action="store_true", help="执行写入；默认仅预览")
    args = parser.parse_args()
    print(backfill_memory_truth_semantics(db_path=args.db, dry_run=not args.apply))
