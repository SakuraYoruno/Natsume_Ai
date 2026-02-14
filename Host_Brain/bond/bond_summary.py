from __future__ import annotations

from typing import Any, Dict

from bond.neo4j_client import Neo4jClient, Neo4jConfig


class BondSummaryProvider:
    """
    Bond SSOT（Neo4j） -> Host 摘要快照。
    如果 Neo4j 不可用，返回 available=false 并给出原因。
    """
    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        summary_query: str = "",
        connect_timeout_s: int = 3,
    ) -> None:
        self.neo4j_uri = (neo4j_uri or "").strip()
        self.neo4j_user = (neo4j_user or "").strip()
        self.neo4j_password = (neo4j_password or "").strip()
        self.summary_query = (summary_query or "").strip()
        self.connect_timeout_s = connect_timeout_s

    def get_summary_safe(self) -> Dict[str, Any]:
        if not self.neo4j_uri:
            return {"available": False, "reason": "未设置 NEO4J_URI"}
        if not self.neo4j_user or not self.neo4j_password:
            return {"available": False, "reason": "未设置 Neo4j 账号或密码"}

        cfg = Neo4jConfig(
            uri=self.neo4j_uri,
            user=self.neo4j_user,
            password=self.neo4j_password,
            connect_timeout_s=self.connect_timeout_s,
        )
        client = Neo4jClient(cfg)
        try:
            if self.summary_query:
                rows = client.run(self.summary_query)
                if rows:
                    if "summary" in rows[0]:
                        return {"available": True, "summary": rows[0]["summary"]}
                    return {"available": True, "summary": rows[0]}
                return {"available": True, "summary": {}}

            # 不做任何业务查询，只验证连通性
            rows = client.run("RETURN 1 AS ok")
            ok = bool(rows and rows[0].get("ok") == 1)
            return {"available": ok, "summary": {}}
        except Exception as e:
            return {"available": False, "reason": f"neo4j_error: {type(e).__name__}: {e}"}
        finally:
            try:
                client.close()
            except Exception:
                pass
