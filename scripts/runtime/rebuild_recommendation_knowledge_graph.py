"""从 SQLite 的推荐投递与偏好事件重建知识图谱；默认只预览。"""

from __future__ import annotations

import argparse
from pathlib import Path

from host_brain.persistence.context_preference_repository import PreferenceRepository
from host_brain.composition.knowledge_graph_maintenance import (
    rebuild_recommendation_knowledge_graph_with_host_runtime,
)
from host_brain.retrieval.knowledge_graph import rebuild_recommendation_knowledge_graph


def rebuild(*, db_path: str | Path, neo4j_client: object, dry_run: bool = True, limit: int = 100000) -> dict[str, object]:
    return rebuild_recommendation_knowledge_graph(
        preference_repository=PreferenceRepository(db_path, verify_schema=False), neo4j_client=neo4j_client,
        dry_run=dry_run, limit=limit,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="重建推荐知识图谱")
    parser.add_argument("--db", required=True, help="SQLite 数据库路径")
    parser.add_argument("--apply", action="store_true", help="写入 Neo4j；默认仅预览")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--person-id", default="", help="只重建指定用户的图谱投影")
    args = parser.parse_args()
    result = rebuild_recommendation_knowledge_graph_with_host_runtime(
        db_path=args.db,
        dry_run=not bool(args.apply),
        person_id=str(args.person_id or "").strip() or None,
        limit=args.limit,
    )
    print(result)
