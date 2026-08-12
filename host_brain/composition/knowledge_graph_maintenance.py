"""Host 图谱维护入口；复用既有 Neo4j 配置与客户端工厂。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.composition.host_bond_runtime import (
    build_host_neo4j_projection_client,
)
from host_brain.persistence.context_preference_repository import (
    PreferenceRepository,
)
from host_brain.retrieval.knowledge_graph import (
    rebuild_recommendation_knowledge_graph,
)


def rebuild_recommendation_knowledge_graph_with_host_runtime(
    *,
    db_path: str | Path,
    dry_run: bool = True,
    person_id: str | None = None,
    limit: int = 100000,
) -> dict[str, Any]:
    """从 SQLite 重建图投影；写入时仅使用 Host 共用 Neo4j 工厂。"""

    repository = PreferenceRepository(db_path, verify_schema=False)
    if dry_run:
        return rebuild_recommendation_knowledge_graph(
            preference_repository=repository,
            neo4j_client=object(),
            dry_run=True,
            person_id=person_id,
            limit=limit,
        )

    client = build_host_neo4j_projection_client()
    if client is None:
        raise RuntimeError("Host Neo4j 运行时未启用或当前不可用")
    try:
        return rebuild_recommendation_knowledge_graph(
            preference_repository=repository,
            neo4j_client=client,
            dry_run=False,
            person_id=person_id,
            limit=limit,
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


__all__ = ["rebuild_recommendation_knowledge_graph_with_host_runtime"]
