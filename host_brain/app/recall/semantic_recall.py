"""semantic_recall.py —— 语义召回器

五路召回中的 ``episodic_vector`` 路：
- 基于 ChromaDB 向量数据库的语义相似度召回
- 支持 top-k 召回 + 相似度阈值过滤
- 召回结果按相似度降序排列，并可转成 prompt 可读文本
- 降级策略：ChromaDB 不可达时返回空结果（不阻断主链路）
- 召回过程写入 subgraph_trace，供检索运行态观测使用
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 召回结果
# ---------------------------------------------------------------------------

@dataclass
class SemanticHit:
    """单条语义召回结果。"""
    doc_id: str
    content: str
    score: float
    """相似度分数（0.0 ~ 1.0，越高越相关）"""
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "chromadb"


@dataclass
class SemanticRecallResult:
    """SemanticRecaller.recall() 的返回值。"""
    hits: List[SemanticHit]
    query: str
    top_k: int
    threshold: float
    total_found: int
    """ChromaDB 返回的原始结果数（过滤前）"""
    filtered_count: int
    """过滤后的结果数"""
    degraded: bool = False
    """是否降级（ChromaDB 不可达）"""
    degraded_reason: Optional[str] = None
    latency_ms: int = 0

    def to_context_text(self, max_tokens: int = 600) -> str:
        """
        将召回结果转换为 prompt 可读的文本格式。

        格式：
            [EPISODIC_VECTOR]
            [1] (score=0.92) 内容文本...
            [2] (score=0.87) 内容文本...
        """
        if not self.hits:
            return ""
        lines = ["[EPISODIC_VECTOR]"]
        total_chars = len(lines[0])
        for i, hit in enumerate(self.hits, 1):
            line = f"[{i}] (score={hit.score:.2f}) {hit.content}"
            total_chars += len(line)
            # 简单 token 估算：中文 1 char/token，英文 4 chars/token
            estimated_tokens = total_chars // 3
            if estimated_tokens > max_tokens:
                break
            lines.append(line)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 语义召回器
# ---------------------------------------------------------------------------

class SemanticRecaller:
    """
    语义召回器（七路召回中的 episodic_vector 路）。

    支持两种后端：
    1. ChromaDB（生产环境）
    2. 内存向量存储（开发/测试，基于余弦相似度）
    """

    def __init__(
        self,
        collection_name: str = "episodic_memory",
        top_k: int = 5,
        similarity_threshold: float = 0.70,
        use_mock: bool = False,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        embed_fn: Optional[Callable[[str], List[float]]] = None,
    ) -> None:
        """
        Args:
            collection_name: ChromaDB 集合名称
            top_k: 最大召回条数
            similarity_threshold: 相似度阈值（低于此值的结果被过滤）
            use_mock: 是否使用内存 mock 后端（开发/测试用）
            chroma_host: ChromaDB 主机地址
            chroma_port: ChromaDB 端口
            embed_fn: 自定义嵌入函数（None 时使用默认嵌入）
        """
        self._collection_name = collection_name
        self._top_k = top_k
        self._threshold = similarity_threshold
        self._use_mock = use_mock
        self._chroma_host = chroma_host
        self._chroma_port = chroma_port
        self._embed_fn = embed_fn

        # Mock 后端：内存向量存储
        self._mock_store: List[Dict[str, Any]] = []
        self._chroma_client: Any = None

    # ------------------------------------------------------------------
    # 主召回接口
    # ------------------------------------------------------------------

    async def recall(
        self,
        query: str,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> SemanticRecallResult:
        """
        执行语义召回。

        Args:
            query: 查询文本
            top_k: 最大召回条数（None 时使用初始化值）
            threshold: 相似度阈值（None 时使用初始化值）
            filter_metadata: 元数据过滤条件（如 {"session_id": "xxx"}）

        Returns:
            SemanticRecallResult
        """
        t0 = int(time.time() * 1000)
        k = top_k or self._top_k
        thresh = threshold if threshold is not None else self._threshold

        if self._use_mock:
            result = await self._recall_mock(query, k, thresh, filter_metadata)
        else:
            result = await self._recall_chroma(query, k, thresh, filter_metadata)

        result.latency_ms = int(time.time() * 1000) - t0
        logger.debug(
            "SemanticRecaller: 召回完成（query=%r, hits=%d, degraded=%s, latency=%dms）",
            query[:50], result.filtered_count, result.degraded, result.latency_ms,
        )
        return result

    # ------------------------------------------------------------------
    # ChromaDB 后端
    # ------------------------------------------------------------------

    async def _recall_chroma(
        self,
        query: str,
        top_k: int,
        threshold: float,
        filter_metadata: Optional[Dict[str, Any]],
    ) -> SemanticRecallResult:
        """从 ChromaDB 执行向量召回。"""
        try:
            import chromadb  # noqa: PLC0415
            if self._chroma_client is None:
                self._chroma_client = chromadb.HttpClient(
                    host=self._chroma_host,
                    port=self._chroma_port,
                )
            collection = self._chroma_client.get_or_create_collection(
                name=self._collection_name,
            )

            # 构建查询向量
            query_embedding = await self._embed(query)

            # 执行查询
            chroma_filter = filter_metadata or None
            raw = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k * 2,  # 多取一些，过滤后再截断
                where=chroma_filter,
                include=["documents", "metadatas", "distances"],
            )

            # 解析结果
            docs = raw.get("documents", [[]])[0]
            metas = raw.get("metadatas", [[]])[0]
            distances = raw.get("distances", [[]])[0]

            total_found = len(docs)
            hits = []
            for doc, meta, dist in zip(docs, metas, distances):
                # ChromaDB 返回的是 L2 距离，转换为相似度分数
                score = max(0.0, 1.0 - dist / 2.0)
                if score < threshold:
                    continue
                hits.append(SemanticHit(
                    doc_id=str(meta.get("doc_id", "")),
                    content=str(doc),
                    score=round(score, 4),
                    metadata=dict(meta),
                    source="chromadb",
                ))

            hits.sort(key=lambda h: h.score, reverse=True)
            hits = hits[:top_k]

            return SemanticRecallResult(
                hits=hits,
                query=query,
                top_k=top_k,
                threshold=threshold,
                total_found=total_found,
                filtered_count=len(hits),
            )

        except ImportError:
            logger.warning("SemanticRecaller: chromadb 未安装，降级为空结果")
            return self._degraded_result(query, top_k, threshold, "chromadb_not_installed")
        except Exception as exc:
            logger.warning("SemanticRecaller: ChromaDB 召回失败，降级: %s", exc)
            return self._degraded_result(query, top_k, threshold, str(exc)[:80])

    # ------------------------------------------------------------------
    # Mock 后端（开发/测试）
    # ------------------------------------------------------------------

    async def _recall_mock(
        self,
        query: str,
        top_k: int,
        threshold: float,
        filter_metadata: Optional[Dict[str, Any]],
    ) -> SemanticRecallResult:
        """从内存 mock 存储执行召回（基于关键词重叠）。"""
        import re

        def keyword_overlap(a: str, b: str) -> float:
            """简单关键词重叠率作为相似度估算。"""
            words_a = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', a.lower()))
            words_b = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', b.lower()))
            if not words_a or not words_b:
                return 0.0
            intersection = words_a & words_b
            union = words_a | words_b
            return len(intersection) / len(union)

        hits = []
        for item in self._mock_store:
            # 元数据过滤
            if filter_metadata:
                match = all(
                    item.get("metadata", {}).get(k) == v
                    for k, v in filter_metadata.items()
                )
                if not match:
                    continue
            score = keyword_overlap(query, item.get("content", ""))
            if score >= threshold:
                hits.append(SemanticHit(
                    doc_id=str(item.get("doc_id", "")),
                    content=str(item.get("content", "")),
                    score=round(score, 4),
                    metadata=dict(item.get("metadata", {})),
                    source="mock",
                ))

        hits.sort(key=lambda h: h.score, reverse=True)
        hits = hits[:top_k]

        return SemanticRecallResult(
            hits=hits,
            query=query,
            top_k=top_k,
            threshold=threshold,
            total_found=len(self._mock_store),
            filtered_count=len(hits),
        )

    def add_mock_document(
        self,
        doc_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """向 mock 存储添加文档（测试用）。"""
        self._mock_store.append({
            "doc_id": doc_id,
            "content": content,
            "metadata": metadata or {},
        })

    # ------------------------------------------------------------------
    # Ref #112: 运行时动态添加文档
    # ------------------------------------------------------------------

    async def add_document(
        self,
        doc_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        运行时动态添加单条文档（Ref #112）。

        Mock 模式下写入内存存储；ChromaDB 模式下写入向量数据库。
        幂等：相同 doc_id 会覆盖已有文档。

        Args:
            doc_id: 文档唯一 ID
            content: 文档内容
            metadata: 附加元数据

        Returns:
            True 表示添加成功，False 表示失败（降级时不抛出异常）
        """
        if not content or not content.strip():
            logger.warning("add_document: 内容为空，跳过（doc_id=%s）", doc_id)
            return False

        if self._use_mock:
            # Mock 模式：写入内存存储（幂等：先删除再插入）
            self._mock_store = [
                item for item in self._mock_store
                if item.get("doc_id") != doc_id
            ]
            self._mock_store.append({
                "doc_id": doc_id,
                "content": content,
                "metadata": metadata or {},
            })
            logger.debug("add_document(mock): 已添加 doc_id=%s", doc_id)
            return True

        # ChromaDB 模式
        try:
            import chromadb  # noqa: PLC0415
            if self._chroma_client is None:
                self._chroma_client = chromadb.HttpClient(
                    host=self._chroma_host,
                    port=self._chroma_port,
                )
            collection = self._chroma_client.get_or_create_collection(
                name=self._collection_name,
            )
            embedding = await self._embed(content)
            # upsert 实现幂等
            collection.upsert(
                ids=[doc_id],
                documents=[content],
                embeddings=[embedding],
                metadatas=[{**(metadata or {}), "doc_id": doc_id}],
            )
            logger.debug("add_document(chroma): 已 upsert doc_id=%s", doc_id)
            return True
        except ImportError:
            logger.warning("add_document: chromadb 未安装，写入失败（doc_id=%s）", doc_id)
            return False
        except Exception as exc:
            logger.warning("add_document: ChromaDB 写入失败（doc_id=%s）: %s", doc_id, exc)
            return False

    async def add_documents(
        self,
        documents: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        批量添加文档（Ref #112）。

        Args:
            documents: 文档列表，每项格式为：
                       {"doc_id": str, "content": str, "metadata": dict（可选）}

        Returns:
            {"total": int, "success": int, "failed": int, "failed_ids": list}
        """
        total = len(documents)
        success = 0
        failed_ids: List[str] = []

        for doc in documents:
            doc_id = str(doc.get("doc_id") or "")
            content = str(doc.get("content") or "")
            metadata = doc.get("metadata") or {}
            if not doc_id:
                logger.warning("add_documents: 跳过缺少 doc_id 的文档")
                failed_ids.append("<missing_id>")
                continue
            ok = await self.add_document(doc_id, content, metadata)
            if ok:
                success += 1
            else:
                failed_ids.append(doc_id)

        logger.info(
            "add_documents: 批量添加完成（total=%d, success=%d, failed=%d）",
            total, success, len(failed_ids),
        )
        return {
            "total": total,
            "success": success,
            "failed": len(failed_ids),
            "failed_ids": failed_ids,
        }

    # ------------------------------------------------------------------
    # 嵌入工具
    # ------------------------------------------------------------------

    async def _embed(self, text: str) -> List[float]:
        """生成文本嵌入向量。"""
        if self._embed_fn is not None:
            import asyncio
            if asyncio.iscoroutinefunction(self._embed_fn):
                return await self._embed_fn(text)
            return self._embed_fn(text)
        # 默认：使用简单哈希作为伪嵌入（仅用于开发，生产环境必须替换）
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in h[:128]]

    # ------------------------------------------------------------------
    # 降级结果
    # ------------------------------------------------------------------

    @staticmethod
    def _degraded_result(
        query: str,
        top_k: int,
        threshold: float,
        reason: str,
    ) -> SemanticRecallResult:
        return SemanticRecallResult(
            hits=[],
            query=query,
            top_k=top_k,
            threshold=threshold,
            total_found=0,
            filtered_count=0,
            degraded=True,
            degraded_reason=reason,
        )
