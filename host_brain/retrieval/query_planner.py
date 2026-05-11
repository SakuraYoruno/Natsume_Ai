"""Agentic RAG 查询规划器。

职责：分析用户消息 → 意图分类 → Store 路由 → Query 改写。
设计原则：纯规则，零 LLM 调用，<5ms 延迟。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.retrieval.query_planner")

# ---------------------------------------------------------------------------
# 5 路 Store ID 常量（Phase XIII 精简：剔除 style_canon + persona_utterance）
# style_canon → StyleCanon v5 常驻注入（不走检索）
# persona_utterance → 已从当前主链移除；表层风格参考走 style_reference_bank。
# ---------------------------------------------------------------------------
ALL_STORE_IDS: tuple[str, ...] = (
    "episodic_vector",
    "sqlite_ledger",
    "neo4j_bond",
    "world_canon",
    "artifact_vault",
)

# ---------------------------------------------------------------------------
# 意图标签
# ---------------------------------------------------------------------------
INTENT_GREETING = "greeting"
INTENT_PHATIC = "phatic"
INTENT_CHAT = "chat"
INTENT_RECALL = "recall_memory"
INTENT_EMOTION = "emotion"
INTENT_AFFECTION = "affection"
INTENT_KNOWLEDGE = "knowledge"
INTENT_TOOL = "tool"
INTENT_COMPLEX = "complex"

# ---------------------------------------------------------------------------
# 意图 → Store 路由映射
# ---------------------------------------------------------------------------
INTENT_STORE_MAP: dict[str, list[str] | None] = {
    INTENT_GREETING:  [],  # 跳过检索
    # 多轮寒暄仍可读取最近对话痕迹；风格本身由 v5 常驻注入保证。
    INTENT_PHATIC:    ["episodic_vector"],
    INTENT_CHAT:      ["episodic_vector"],
    INTENT_RECALL:    ["episodic_vector", "sqlite_ledger"],
    INTENT_EMOTION:   ["neo4j_bond", "episodic_vector"],
    INTENT_AFFECTION: ["neo4j_bond", "episodic_vector"],
    INTENT_KNOWLEDGE: ["world_canon"],
    INTENT_TOOL:      ["artifact_vault"],
    INTENT_COMPLEX:   None,  # None = 全量 5 路
}

# 意图 → 最大检索轮次
INTENT_MAX_ROUNDS: dict[str, int] = {
    INTENT_GREETING:  0,
    INTENT_PHATIC:    1,
    INTENT_CHAT:      1,
    INTENT_RECALL:    2,
    INTENT_EMOTION:   2,
    INTENT_AFFECTION: 1,
    INTENT_KNOWLEDGE: 1,
    INTENT_TOOL:      1,
    INTENT_COMPLEX:   3,
}

# ---------------------------------------------------------------------------
# 意图分类规则（关键词匹配，优先级从高到低）
# ---------------------------------------------------------------------------
_GREETING_PATTERN = re.compile(
    r"^[\s]*(你好|早上好|早安|晚安|午安|嗨|hello|hi|hey|good\s*(morning|night|evening)|おはよう|こんにちは|おやすみ)[\s!！。.~～♡🌸]*$",
    re.IGNORECASE,
)
_SIMPLE_ACK_PATTERN = re.compile(
    r"^[\s]*(嗯|哦|好的|ok|好|是的|对|行|知道了|了解|收到|嗯嗯|好哒|okk)[\s!！。.~～]*$",
    re.IGNORECASE,
)
_PHATIC_PATTERN = re.compile(
    r"^[\s]*(hello|hi|hey|yo|嗨|哈喽|哈囉|喂|在吗|在么|在嘛|早|早呀|早啊|晚安|晚安啦|嘿)[\s!！。.?？~～]*$",
    re.IGNORECASE,
)
_AFFECTION_PATTERN = re.compile(
    r"(我爱你|爱你|喜欢你|好喜欢你|我好喜欢你|想你|抱抱|抱一下|亲亲|陪陪我|想和你在一起|"
    r"愛してる|好きだよ|好きです|会いたい|ぎゅっ)",
    re.IGNORECASE,
)

_RECALL_KEYWORDS = frozenset({
    "还记得", "记得吗", "记不记得", "之前", "上次", "那次", "以前",
    "有一次", "过去", "当时", "那天", "那时候", "曾经",
})
_EMOTION_KEYWORDS = frozenset({
    "心情", "感觉", "难过", "开心", "伤心", "生气", "烦",
    "累", "疲惫", "高兴", "快乐", "郁闷", "焦虑", "害怕",
    "寂寞", "孤独", "想你", "喜欢", "讨厌", "感动", "委屈",
    "关系", "我们之间",
})
_KNOWLEDGE_KEYWORDS = frozenset({
    "设定", "你是谁", "你叫什么", "你的名字", "规则", "系统",
    "世界观", "人设", "canon",
})
_TOOL_KEYWORDS = frozenset({
    "执行", "打开", "运行", "搜索一下", "帮我查", "帮我找",
    "帮我搜", "工具", "命令",
})


# ---------------------------------------------------------------------------
# Query 改写：停用词
# ---------------------------------------------------------------------------
_STOPWORDS = frozenset({
    "你", "我", "她", "他", "它", "我们", "你们", "他们",
    "的", "了", "吗", "呢", "吧", "啊", "呀", "哦", "嘛", "哈",
    "是", "有", "在", "不", "也", "还", "就", "都", "很", "太",
    "和", "与", "或", "但", "可以", "能", "会",
    "这", "那", "什么", "怎么", "为什么", "哪", "谁",
    "一个", "一些", "一下", "一点",
})

# ---------------------------------------------------------------------------
# Query 改写：时间词 → lookback 天数
# ---------------------------------------------------------------------------
TIME_MARKERS: dict[str, int] = {
    "刚才": 1, "刚刚": 1,
    "昨天": 2, "昨晚": 2,
    "前天": 3,
    "上次": 7, "最近": 7,
    "之前": 30, "以前": 30,
    "好久": 90, "很久": 90, "曾经": 90,
}

# ---------------------------------------------------------------------------
# Query 改写：情绪同义词扩展
# ---------------------------------------------------------------------------
EMOTION_SYNONYMS: dict[str, list[str]] = {
    "心情不好": ["难过", "低落", "安慰"],
    "不开心":   ["难过", "伤心", "安慰"],
    "难过":     ["伤心", "低落", "哭"],
    "开心":     ["高兴", "快乐", "好消息"],
    "生气":     ["愤怒", "不满", "委屈"],
    "累":       ["疲惫", "辛苦", "休息"],
    "好累":     ["疲惫", "辛苦", "休息"],
    "烦":       ["烦躁", "焦虑", "压力"],
    "焦虑":     ["紧张", "担心", "压力"],
    "害怕":     ["恐惧", "担心", "不安"],
    "寂寞":     ["孤独", "想你", "陪伴"],
    "孤独":     ["寂寞", "想你", "陪伴"],
    "想你":     ["思念", "想念", "陪伴"],
    "委屈":     ["难过", "不公平", "安慰"],
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class QueryPlan:
    """查询规划结果。"""

    original_query: str
    rewritten_queries: list[str] = field(default_factory=list)
    intent: str = INTENT_CHAT
    target_stores: list[str] = field(default_factory=list)
    skip_retrieval: bool = False
    max_rounds: int = 1
    time_lookback_days: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "rewritten_queries": list(self.rewritten_queries),
            "intent": self.intent,
            "target_stores": list(self.target_stores),
            "skip_retrieval": self.skip_retrieval,
            "max_rounds": self.max_rounds,
            "time_lookback_days": self.time_lookback_days,
        }


# ---------------------------------------------------------------------------
# QueryPlanner
# ---------------------------------------------------------------------------
class QueryPlanner:
    """Agentic RAG 查询规划器。

    纯规则引擎，<5ms 延迟，零 LLM 调用。
    """

    def __init__(
        self,
        *,
        intent_store_map: dict[str, list[str] | None] | None = None,
        intent_max_rounds: dict[str, int] | None = None,
    ) -> None:
        self._store_map = dict(intent_store_map or INTENT_STORE_MAP)
        self._max_rounds = dict(intent_max_rounds or INTENT_MAX_ROUNDS)

    def plan(
        self,
        user_message: str,
        *,
        turn_index: int = 0,
    ) -> QueryPlan:
        """分析用户消息，输出检索计划。

        Args:
            user_message: 原始用户消息文本。
            turn_index: 当前对话轮次索引（0=新对话首轮）。

        Returns:
            QueryPlan
        """
        text = str(user_message or "").strip()
        if not text:
            return QueryPlan(
                original_query=text,
                intent=INTENT_GREETING,
                skip_retrieval=True,
                max_rounds=0,
            )

        # 1. 意图分类
        intent = self._classify_intent(text, turn_index=turn_index)

        # 2. Store 路由
        stores_or_none = self._store_map.get(intent)
        target_stores = list(stores_or_none) if stores_or_none is not None else list(ALL_STORE_IDS)
        skip = intent == INTENT_GREETING or not target_stores

        # 3. Query 改写
        rewritten = self._rewrite(text, intent) if not skip else []

        # 4. 时间 lookback
        lookback = self._extract_time_lookback(text)

        max_rounds = self._max_rounds.get(intent, 1)

        plan = QueryPlan(
            original_query=text,
            rewritten_queries=rewritten,
            intent=intent,
            target_stores=target_stores,
            skip_retrieval=skip,
            max_rounds=max_rounds,
            time_lookback_days=lookback,
        )

        _logger.debug(
            "QueryPlan: intent=%s stores=%d skip=%s rounds=%d rewrites=%d",
            intent, len(target_stores), skip, max_rounds, len(rewritten),
        )
        return plan

    # ── 意图分类 ──────────────────────────────────────────────────────────

    def _classify_intent(self, text: str, *, turn_index: int = 0) -> str:
        """纯关键词规则意图分类。"""
        # 简单寒暄：仅在新对话首轮判定
        if turn_index == 0 and self._is_greeting(text):
            return INTENT_GREETING

        # 多轮中的短促社交互动（寒暄、回应、试探）：保留风格，不再让随机台词召回污染 prompt
        if self._is_phatic(text):
            return INTENT_PHATIC

        if self._is_affection(text):
            return INTENT_AFFECTION

        # 复杂度判定
        if len(text) > 100 or text.count("？") + text.count("?") > 1:
            return INTENT_COMPLEX

        # 关键词匹配（优先级：recall > emotion > knowledge > tool > chat）
        text_lower = text.lower()

        if any(kw in text_lower for kw in _RECALL_KEYWORDS):
            return INTENT_RECALL

        if any(kw in text_lower for kw in _EMOTION_KEYWORDS):
            return INTENT_EMOTION

        if any(kw in text_lower for kw in _KNOWLEDGE_KEYWORDS):
            return INTENT_KNOWLEDGE

        if any(kw in text_lower for kw in _TOOL_KEYWORDS):
            return INTENT_TOOL

        return INTENT_CHAT

    @staticmethod
    def _is_greeting(text: str) -> bool:
        """判定是否为简单寒暄。"""
        return bool(_GREETING_PATTERN.match(text.strip()))

    @staticmethod
    def _is_phatic(text: str) -> bool:
        """判定是否为短消息社交互动。"""
        normalized = str(text or "").strip()
        if not normalized:
            return False
        if _SIMPLE_ACK_PATTERN.match(normalized):
            return True
        if _PHATIC_PATTERN.match(normalized):
            return True
        return False

    @staticmethod
    def _is_affection(text: str) -> bool:
        """判定是否为直接的亲密表达或告白。"""
        normalized = str(text or "").strip()
        if not normalized:
            return False
        return bool(_AFFECTION_PATTERN.search(normalized))

    # ── Query 改写 ────────────────────────────────────────────────────────

    def _rewrite(self, text: str, intent: str) -> list[str]:
        """轻量级 Query 改写。

        返回改写后的 query 列表（不包含原始 query，原始 query 由调用方保留）。
        """
        rewrites: list[str] = []

        # 层 1：停用词去除
        cleaned = self._remove_stopwords(text)
        if cleaned and cleaned != text and len(cleaned) >= 2:
            rewrites.append(cleaned)

        # 层 2：情绪同义词扩展
        expanded = self._expand_emotions(text)
        if expanded and expanded not in rewrites:
            rewrites.append(expanded)

        return rewrites

    @staticmethod
    def _remove_stopwords(text: str) -> str:
        """去除停用词，保留实义词。"""
        # 简单按字符边界拆分（中文不需要分词，逐字检查即可）
        # 对于短文本（<50字），逐个检查 2-3 字的停用词
        result = text
        for sw in sorted(_STOPWORDS, key=len, reverse=True):
            result = result.replace(sw, " ")
        # 清理多余空白
        return " ".join(part for part in result.split() if part).strip()

    @staticmethod
    def _expand_emotions(text: str) -> str:
        """情绪同义词扩展。"""
        additions: list[str] = []
        for keyword, synonyms in EMOTION_SYNONYMS.items():
            if keyword in text:
                additions.extend(synonyms)
        if not additions:
            return ""
        # 去重 + 拼接到原文后面
        seen: set[str] = set()
        unique: list[str] = []
        for word in additions:
            if word not in seen and word not in text:
                seen.add(word)
                unique.append(word)
        if not unique:
            return ""
        return f"{text} {' '.join(unique)}"

    @staticmethod
    def _extract_time_lookback(text: str) -> int | None:
        """从文本中提取时间 lookback 天数。"""
        for marker, days in TIME_MARKERS.items():
            if marker in text:
                return days
        return None

    # ── 重试改写（供 AgenticRetrievalLoop 调用）────────────────────────────

    def rewrite_for_retry(
        self,
        original_query: str,
        missing_aspects: list[str] | None = None,
    ) -> str:
        """为重试轮生成改写 query。

        策略：去除停用词 + 追加 missing_aspects 关键词。
        """
        cleaned = self._remove_stopwords(original_query)
        if missing_aspects:
            cleaned = f"{cleaned} {' '.join(missing_aspects)}"
        return cleaned.strip() or original_query
