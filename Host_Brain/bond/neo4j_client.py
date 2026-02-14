from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Neo4jConfig:
    uri: str
    user: str
    password: str
    connect_timeout_s: int = 3


class Neo4jClient:
    """
    Neo4j 轻封装：
    - 允许 neo4j driver 没装时先跑通（上层会降级）
    """
    def __init__(self, cfg: Neo4jConfig) -> None:
        self.cfg = cfg
        self._driver = None

    def _ensure_driver(self):
        if self._driver is not None:
            return self._driver
        try:
            from neo4j import GraphDatabase  # type: ignore
        except Exception:
            raise RuntimeError("未安装 neo4j 驱动。请 pip install neo4j")

        self._driver = GraphDatabase.driver(
            self.cfg.uri,
            auth=(self.cfg.user, self.cfg.password),
            connection_timeout=self.cfg.connect_timeout_s,
        )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass
            self._driver = None

    def run(self, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        driver = self._ensure_driver()
        with driver.session() as session:
            result = session.run(cypher, params or {})
            return [dict(r) for r in result]
