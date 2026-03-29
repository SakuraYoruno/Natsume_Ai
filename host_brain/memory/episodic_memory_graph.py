"""情景记忆知识图谱（Phase XI 方案 2）。

职责：将扁平的情景记忆连成主题线，支持 graph walk 召回完整上下文。
在 Neo4j 中建立 (:Memory)-[:CONTINUES]->(:Memory) 关系。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.memory.episodic_graph")

# ---------------------------------------------------------------------------
# 话题聚类配置
# ---------------------------------------------------------------------------
DEFAULT_TIME_WINDOW_HOURS = 72     # 72h 内的记忆才考虑链接
DEFAULT_KEYWORD_OVERLAP_THRESHOLD = 0.3  # 关键词重叠≥30% 视为同主题
DEFAULT_MAX_CHAIN_LENGTH = 10      # 单条主题链最长 10 节点

# ---------------------------------------------------------------------------
# 话题关键词提取（轻量级，不依赖 NLP 库）
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "你", "我", "她", "他", "的", "了", "吗", "呢", "吧", "是", "有", "在",
    "不", "也", "还", "就", "都", "很", "太", "和", "或", "但", "这", "那",
    "什么", "怎么", "为什么", "嗯", "哦", "好的", "好",
})


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class TopicCluster:
    """话题聚类。"""
    topic_id: str
    keywords: set[str]
    memory_ids: list[str]  # 按时间排序的记忆 ID
    first_ts_ms: int = 0
    last_ts_ms: int = 0
    label: str = ""  # 自动生成的话题标签

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "keywords": sorted(self.keywords)[:10],
            "memory_count": len(self.memory_ids),
            "label": self.label,
            "first_ts_ms": self.first_ts_ms,
            "last_ts_ms": self.last_ts_ms,
        }


@dataclass
class MemoryNode:
    """记忆图谱节点。"""
    memory_id: str
    text: str
    keywords: set[str]
    ts_ms: int
    topic_id: str | None = None
    prev_memory_id: str | None = None  # 同主题前一条
    next_memory_id: str | None = None  # 同主题后一条

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "keywords": sorted(self.keywords)[:5],
            "ts_ms": self.ts_ms,
            "topic_id": self.topic_id,
            "prev": self.prev_memory_id,
            "next": self.next_memory_id,
        }


@dataclass
class GraphWalkResult:
    """Graph walk 召回结果。"""
    seed_memory_id: str
    topic_id: str | None
    chain: list[MemoryNode]
    total_in_topic: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed_memory_id": self.seed_memory_id,
            "topic_id": self.topic_id,
            "chain_length": len(self.chain),
            "total_in_topic": self.total_in_topic,
        }


# ---------------------------------------------------------------------------
# EpisodicMemoryGraph
# ---------------------------------------------------------------------------
class EpisodicMemoryGraph:
    """情景记忆知识图谱——将记忆连成主题线。

    核心能力：
    1. add_memory(): 新记忆写入时自动做话题聚类
    2. walk(): 从一条记忆出发，沿主题线走，召回完整上下文
    3. get_topics(): 获取所有活跃话题

    聚类算法：关键词 Jaccard 重叠 + 时间窗口约束（轻量，<5ms）。
    """

    def __init__(
        self,
        *,
        time_window_hours: float = DEFAULT_TIME_WINDOW_HOURS,
        overlap_threshold: float = DEFAULT_KEYWORD_OVERLAP_THRESHOLD,
        max_chain_length: int = DEFAULT_MAX_CHAIN_LENGTH,
    ) -> None:
        self._time_window_ms = int(time_window_hours * 3600 * 1000)
        self._overlap_threshold = float(overlap_threshold)
        self._max_chain = int(max_chain_length)

        # 内存状态
        self._nodes: dict[str, MemoryNode] = {}  # memory_id → node
        self._topics: dict[str, TopicCluster] = {}  # topic_id → cluster
        self._topic_counter = 0

    def add_memory(
        self,
        *,
        memory_id: str,
        text: str,
        ts_ms: int = 0,
    ) -> MemoryNode:
        """新记忆加入图谱，自动做话题聚类。"""
        ts = int(ts_ms or int(time.time() * 1000))
        keywords = self._extract_keywords(text)

        node = MemoryNode(
            memory_id=memory_id,
            text=text,
            keywords=keywords,
            ts_ms=ts,
        )

        # 尝试匹配已有话题
        best_topic: TopicCluster | None = None
        best_overlap = 0.0

        for topic in self._topics.values():
            # 时间窗口检查
            if ts - topic.last_ts_ms > self._time_window_ms:
                continue
            # 话题链长度检查
            if len(topic.memory_ids) >= self._max_chain:
                continue
            # 关键词重叠
            overlap = self._keyword_overlap(keywords, topic.keywords)
            if overlap >= self._overlap_threshold and overlap > best_overlap:
                best_overlap = overlap
                best_topic = topic

        if best_topic is not None:
            # 加入已有话题
            node.topic_id = best_topic.topic_id
            # 建立链接
            if best_topic.memory_ids:
                prev_id = best_topic.memory_ids[-1]
                prev_node = self._nodes.get(prev_id)
                if prev_node:
                    prev_node.next_memory_id = memory_id
                    node.prev_memory_id = prev_id
            best_topic.memory_ids.append(memory_id)
            best_topic.keywords |= keywords
            best_topic.last_ts_ms = ts
        else:
            # 创建新话题
            self._topic_counter += 1
            topic_id = f"topic_{self._topic_counter:04d}"
            label = self._generate_topic_label(keywords)
            new_topic = TopicCluster(
                topic_id=topic_id,
                keywords=set(keywords),
                memory_ids=[memory_id],
                first_ts_ms=ts,
                last_ts_ms=ts,
                label=label,
            )
            self._topics[topic_id] = new_topic
            node.topic_id = topic_id

        self._nodes[memory_id] = node
        _logger.debug(
            "EpisodicGraph: add %s → topic=%s keywords=%s",
            memory_id[:20], node.topic_id, sorted(keywords)[:5],
        )
        return node

    def walk(
        self,
        memory_id: str,
        *,
        max_steps: int = 5,
        direction: str = "both",  # "both" / "backward" / "forward"
    ) -> GraphWalkResult:
        """从一条记忆出发沿主题线走，召回完整上下文。"""
        seed = self._nodes.get(memory_id)
        if seed is None or seed.topic_id is None:
            return GraphWalkResult(
                seed_memory_id=memory_id,
                topic_id=None,
                chain=[],
                total_in_topic=0,
            )

        topic = self._topics.get(seed.topic_id)
        chain: list[MemoryNode] = []

        # 向前走
        if direction in ("both", "backward"):
            backward: list[MemoryNode] = []
            curr = seed
            for _ in range(max_steps):
                if curr.prev_memory_id is None:
                    break
                prev = self._nodes.get(curr.prev_memory_id)
                if prev is None:
                    break
                backward.append(prev)
                curr = prev
            chain.extend(reversed(backward))

        chain.append(seed)

        # 向后走
        if direction in ("both", "forward"):
            curr = seed
            for _ in range(max_steps):
                if curr.next_memory_id is None:
                    break
                nxt = self._nodes.get(curr.next_memory_id)
                if nxt is None:
                    break
                chain.append(nxt)
                curr = nxt

        return GraphWalkResult(
            seed_memory_id=memory_id,
            topic_id=seed.topic_id,
            chain=chain,
            total_in_topic=len(topic.memory_ids) if topic else 0,
        )

    def get_active_topics(self, *, max_age_hours: float = 168) -> list[TopicCluster]:
        """获取活跃话题（默认 7 天内）。"""
        cutoff = int(time.time() * 1000) - int(max_age_hours * 3600 * 1000)
        return sorted(
            [t for t in self._topics.values() if t.last_ts_ms > cutoff],
            key=lambda t: -t.last_ts_ms,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """轻量级关键词提取（中文 2-3 gram + 去停用词）。"""
        clean = re.sub(r"[^\u4e00-\u9fff\w]", " ", str(text or "").lower())
        words = set()
        # 空格分词
        for w in clean.split():
            if len(w) >= 2 and w not in _STOPWORDS:
                words.add(w)
        # 中文 2-gram
        chars = "".join(ch for ch in clean if not ch.isspace())
        for i in range(len(chars) - 1):
            gram = chars[i:i + 2]
            if all("\u4e00" <= ch <= "\u9fff" for ch in gram):
                if gram not in _STOPWORDS:
                    words.add(gram)
        return words

    @staticmethod
    def _keyword_overlap(set_a: set[str], set_b: set[str]) -> float:
        """Jaccard 关键词重叠度。"""
        if not set_a or not set_b:
            return 0.0
        return len(set_a & set_b) / len(set_a | set_b)

    @staticmethod
    def _generate_topic_label(keywords: set[str]) -> str:
        """从关键词生成话题标签。"""
        sorted_kw = sorted(keywords, key=len, reverse=True)
        return "/".join(sorted_kw[:3]) if sorted_kw else "unknown"
