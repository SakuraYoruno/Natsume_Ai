"""推荐与偏好知识图谱投影及轻量检索。

SQLite 的偏好事件、投递记录仍是唯一权威来源；本模块只将必要关系幂等投影到
Neo4j，并在图不可用时返回空候选。图投影既不能反向修改权威事实，也不会单独
生成新的偏好结论。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.persistence.context_preference_repository import PreferenceRepository


STORE_KNOWLEDGE_GRAPH = "knowledge_graph"
LIGHTNOVEL_DOMAIN = "lightnovel_recommendation"


class KnowledgeGraphProjection:
    """将已持久化的推荐与偏好关系幂等投影到图数据库。"""

    def __init__(self, *, preference_repository: PreferenceRepository, neo4j_client: Any) -> None:
        """绑定权威读取仓储和图写入客户端；二者职责不在此处合并。"""
        self.preference_repository = preference_repository
        self.neo4j_client = neo4j_client

    def rebuild_recommendation_knowledge_graph(
        self,
        *,
        dry_run: bool = True,
        person_id: str | None = None,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """从仍有效的 SQLite 事实重建图投影；``dry_run`` 仅计算语句不产生副作用。"""
        deliveries = self.preference_repository.list_active_deliveries(
            domain=LIGHTNOVEL_DOMAIN, person_id=person_id, limit=limit
        )
        events = self.preference_repository.list_active_events(
            domain=LIGHTNOVEL_DOMAIN, person_id=person_id, limit=limit
        )
        statements: list[tuple[str, dict[str, Any]]] = []
        for delivery in deliveries:
            statements.extend(_delivery_statements(delivery))
        for event in events:
            statements.extend(_preference_statements(event))
        if not dry_run:
            for query, params in statements:
                _run_cypher(self.neo4j_client, query, params)
        return {
            "status": "DRY_RUN" if dry_run else "OK",
            "delivery_count": len(deliveries),
            "preference_event_count": len(events),
            "statement_count": len(statements),
            "store_id": STORE_KNOWLEDGE_GRAPH,
        }

    def project_preference_events(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """仅投影本次已提交的偏好事件，不扫描 SQLite 历史记录。"""

        statements: list[tuple[str, dict[str, Any]]] = []
        for event in events:
            if isinstance(event, Mapping):
                statements.extend(_preference_statements(event))
        for query, params in statements:
            _run_cypher(self.neo4j_client, query, params)
        return {
            "status": "OK",
            "preference_event_count": len(events),
            "statement_count": len(statements),
            "store_id": STORE_KNOWLEDGE_GRAPH,
        }

    def project_recommendation_delivery(
        self,
        delivery: Mapping[str, Any],
    ) -> dict[str, Any]:
        """仅投影本次已提交的推荐投递，不扫描 SQLite 历史记录。"""

        statements = _delivery_statements(delivery)
        for query, params in statements:
            _run_cypher(self.neo4j_client, query, params)
        return {
            "status": "OK",
            "delivery_id": str(delivery.get("delivery_id") or ""),
            "statement_count": len(statements),
            "store_id": STORE_KNOWLEDGE_GRAPH,
        }


class KnowledgeGraphRetriever:
    """最多两跳的 Person–Novel–Trait 检索，并在图不可用时安全降级。"""

    def __init__(self, *, neo4j_client: Any | None = None) -> None:
        """保存可选图客户端；缺失客户端时检索安全降级为空。"""
        self.neo4j_client = neo4j_client

    def search(
        self,
        *,
        person_id: str,
        query_entities: Sequence[str] = (),
        domain: str = LIGHTNOVEL_DOMAIN,
        relation_hints: Sequence[str] = (),
        max_hops: int = 2,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        """在单个人物与有限两跳关系内检索候选，图错误不泄漏为跨人或跨源回退。"""
        if self.neo4j_client is None:
            return []
        traits = [str(item).strip() for item in query_entities if str(item).strip()]
        hints = {str(item).strip().upper() for item in relation_hints if str(item).strip()}
        relation_filter = "DISLIKES" if "DISLIKES" in hints else "LIKES"
        query = """
        MATCH (p:Person {person_id: $person_id})-[pref:%s {active: true}]->(n:Novel)
        OPTIONAL MATCH (n)-[:HAS_TRAIT]->(t:Trait)
        WHERE $domain = '' OR coalesce(n.domain, '') = $domain
        WITH n, pref, collect(t.trait_id) AS trait_ids, collect(t.label) AS trait_labels
        WHERE size($traits) = 0 OR any(label IN trait_labels WHERE label IN $traits)
        RETURN n.novel_id AS novel_id, n.title AS title, pref.evidence_ref AS evidence_ref,
               pref.preference_event_id AS preference_event_id, pref.confidence AS confidence,
               trait_ids, trait_labels
        ORDER BY coalesce(pref.timestamp, 0) DESC LIMIT $limit
        """ % relation_filter
        try:
            rows = _run_cypher(
                self.neo4j_client,
                query,
                {
                    "person_id": str(person_id), "domain": str(domain or ""),
                    "traits": traits, "limit": max(1, min(int(limit), 50)),
                    "max_hops": max(1, min(int(max_hops), 2)),
                },
            )
        except Exception:
            return []
        return [_candidate(row, relation_filter) for row in _rows(rows)][: max(1, int(limit))]


def rebuild_recommendation_knowledge_graph(
    *,
    preference_repository: PreferenceRepository,
    neo4j_client: Any,
    dry_run: bool = True,
    person_id: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """提供无状态重建入口，仍复用投影器的权威来源与 ``dry_run`` 语义。"""
    return KnowledgeGraphProjection(
        preference_repository=preference_repository, neo4j_client=neo4j_client
    ).rebuild_recommendation_knowledge_graph(
        dry_run=dry_run, person_id=person_id, limit=limit
    )


def _delivery_statements(delivery: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """把已提交投递转换为幂等 Cypher 语句；身份或标题不完整时不投影。"""
    person_id = str(delivery.get("person_id") or "").strip()
    delivery_id = str(delivery.get("delivery_id") or "").strip()
    domain = str(delivery.get("domain") or "").strip()
    if not person_id or not delivery_id:
        return []
    rows: list[tuple[str, dict[str, Any]]] = []
    for item in list(delivery.get("items") or []):
        if not isinstance(item, Mapping):
            continue
        novel_id = str(item.get("target_id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not novel_id or not title:
            continue
        params = {
            "person_id": person_id, "delivery_id": delivery_id, "novel_id": novel_id,
            "title": title, "domain": domain, "timestamp": int(delivery.get("created_at_ms") or 0),
            "rank": int(item.get("rank") or 0),
            "source_ref": str(delivery.get("ranked_recommendations_ref") or delivery.get("recommendations_ref") or ""),
        }
        rows.append((
            """MERGE (p:Person {person_id:$person_id}) MERGE (r:Recommendation {recommendation_id:$delivery_id})
            MERGE (n:Novel {novel_id:$novel_id}) SET n.title=$title, n.domain=$domain
            MERGE (p)-[x:RECEIVED_RECOMMENDATION]->(r) SET x.timestamp=$timestamp
            MERGE (r)-[x:RECOMMENDED]->(n) SET x.rank=$rank, x.source_ref=$source_ref, x.timestamp=$timestamp""",
            params,
        ))
        for trait in list(item.get("traits") or []):
            if not isinstance(trait, Mapping) or not str(trait.get("label") or "").strip():
                continue
            trait_params = {**params, "trait_id": str(trait.get("trait_id") or trait.get("label")), "trait_label": str(trait.get("label"))}
            rows.append((
                "MERGE (n:Novel {novel_id:$novel_id}) MERGE (t:Trait {trait_id:$trait_id}) SET t.label=$trait_label MERGE (n)-[:HAS_TRAIT]->(t)",
                trait_params,
            ))
    return rows


def _preference_statements(event: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """把已提交偏好事件转换为受类型限制的关系；未知目标种类不写入图。"""
    person_id = str(event.get("person_id") or "").strip()
    target_id = str(event.get("target_id") or "").strip()
    label = str(event.get("target_label") or target_id).strip()
    if not person_id or not target_id:
        return []
    target_kind = str(event.get("target_kind") or "trait").strip().lower()
    polarity = str(event.get("polarity") or "positive").strip().lower()
    relation = "DISLIKES" if polarity == "negative" else "LIKES"
    if target_kind == "trait":
        relation = "DISLIKES_TRAIT" if polarity == "negative" else "LIKES_TRAIT"
        node_label, node_id, node_name = "Trait", "trait_id", "trait_label"
    elif target_kind == "recommendation_item":
        node_label, node_id, node_name = "Novel", "novel_id", "title"
    else:
        return []
    refs = list(event.get("evidence_refs") or [])
    params = {
        "person_id": person_id, "target_id": target_id, "target_label": label,
        "event_id": str(event.get("event_id") or ""), "evidence_ref": str(refs[0] if refs else ""),
        "confidence": float(event.get("confidence") or 0.0), "timestamp": int(event.get("created_at_ms") or 0),
    }
    return [(
        f"""MERGE (p:Person {{person_id:$person_id}}) MERGE (n:{node_label} {{{node_id}:$target_id}})
        SET n.{node_name}=$target_label
        MERGE (p)-[r:{relation}]->(n)
        SET r.preference_event_id=$event_id, r.evidence_ref=$evidence_ref, r.confidence=$confidence,
            r.timestamp=$timestamp, r.active=true, r.superseded=false""",
        params,
    )]


def _run_cypher(client: Any, query: str, params: Mapping[str, Any]) -> Any:
    """兼容受支持的图客户端调用面；没有执行端口时显式报告图服务不可用。"""
    for name in ("run", "execute", "query"):
        method = getattr(client, name, None)
        if callable(method):
            return method(query, **dict(params))
    driver = getattr(client, "_driver", None)
    database = getattr(client, "_database", None)
    if driver is not None:
        with driver.session(database=database) as session:
            return list(session.run(query, **dict(params)))
    raise RuntimeError("知识图谱服务不可用")


def _rows(value: Any) -> list[Mapping[str, Any]]:
    """归一化客户端行对象为映射，跳过无法安全投影的结果项。"""
    if value is None:
        return []
    return [item.data() if hasattr(item, "data") else dict(item) for item in list(value) if isinstance(item, Mapping) or hasattr(item, "data")]


def _candidate(row: Mapping[str, Any], relation: str) -> dict[str, Any]:
    """把图结果缩减为带证据引用的召回候选，而非暴露图数据库内部对象。"""
    title = str(row.get("title") or row.get("novel_id") or "")
    traits = [str(item) for item in list(row.get("trait_labels") or []) if str(item)]
    return {
        "memory_id": f"knowledge_graph::{row.get('novel_id')}",
        "text": f"{title}（{'、'.join(traits[:4])}）",
        "score": min(1.0, 0.65 + max(0.0, min(float(row.get("confidence") or 0.0), 1.0)) * 0.3),
        "metadata": {
            "store_id": STORE_KNOWLEDGE_GRAPH,
            "source_ref": str(row.get("evidence_ref") or ""),
            "evidence_ref": str(row.get("evidence_ref") or ""),
            "entity_ids": [str(row.get("novel_id") or ""), *[str(item) for item in list(row.get("trait_ids") or [])]],
            "relation_path": ["Person", relation, "Novel", "HAS_TRAIT", "Trait"],
            "preference_event_id": str(row.get("preference_event_id") or ""),
        },
    }


__all__ = ["KnowledgeGraphProjection", "KnowledgeGraphRetriever", "STORE_KNOWLEDGE_GRAPH", "rebuild_recommendation_knowledge_graph"]
