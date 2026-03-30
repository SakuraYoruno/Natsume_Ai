"""QueryPlanner 单元测试 — 覆盖全部 9 种意图 + 边界情况。

测试矩阵：
  greeting / phatic / affection / chat / recall_memory /
  emotion / knowledge / tool / complex
"""

import pytest

from host_brain.retrieval.query_planner import (
    ALL_STORE_IDS,
    INTENT_AFFECTION,
    INTENT_CHAT,
    INTENT_COMPLEX,
    INTENT_EMOTION,
    INTENT_GREETING,
    INTENT_KNOWLEDGE,
    INTENT_PHATIC,
    INTENT_RECALL,
    INTENT_TOOL,
    QueryPlanner,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _plan(text: str, turn_index: int = 2):
    """快捷 plan（默认 turn_index=2 避免首轮 greeting 干扰）。"""
    return QueryPlanner().plan(text, turn_index=turn_index)


# ── 1. GREETING ──────────────────────────────────────────────────────────


class TestGreeting:
    """greeting 意图：仅在 turn_index=0 且匹配寒暄正则时触发。"""

    @pytest.mark.parametrize("text", [
        "你好", "早上好", "hello", "hi", "おはよう", "こんにちは", "晚安",
    ])
    def test_first_turn_greeting_skips_retrieval(self, text: str) -> None:
        plan = QueryPlanner().plan(text, turn_index=0)
        assert plan.intent == INTENT_GREETING
        assert plan.skip_retrieval is True
        assert plan.target_stores == []
        assert plan.max_rounds == 0

    def test_non_first_turn_greeting_becomes_phatic(self) -> None:
        """turn_index > 0 时即使文本一样也不走 greeting。"""
        plan = QueryPlanner().plan("hello", turn_index=3)
        assert plan.intent != INTENT_GREETING
        assert plan.skip_retrieval is False

    def test_empty_message_treated_as_greeting(self) -> None:
        plan = QueryPlanner().plan("", turn_index=0)
        assert plan.intent == INTENT_GREETING
        assert plan.skip_retrieval is True


# ── 2. PHATIC ────────────────────────────────────────────────────────────


class TestPhatic:
    """phatic 意图：短促社交互动（嗯、ok、在吗 等）。"""

    @pytest.mark.parametrize("text", [
        "嗯", "好的", "ok", "嗯嗯", "好", "知道了", "了解", "收到", "好哒",
        "在吗", "在么", "喂", "嘿",
    ])
    def test_phatic_routes_to_episodic_only(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_PHATIC
        assert plan.target_stores == ["episodic_vector"]
        assert plan.max_rounds == 1
        assert plan.skip_retrieval is False


# ── 3. AFFECTION ─────────────────────────────────────────────────────────


class TestAffection:
    """affection 意图：亲密表达、告白。"""

    @pytest.mark.parametrize("text", [
        "我爱你", "好喜欢你", "想你了", "抱抱", "亲亲",
        "愛してる", "好きだよ", "会いたい",
    ])
    def test_affection_routes_to_bond_and_episodic(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_AFFECTION
        assert plan.target_stores == ["neo4j_bond", "episodic_vector"]
        assert plan.max_rounds == 1


# ── 4. CHAT ──────────────────────────────────────────────────────────────


class TestChat:
    """chat 意图：普通日常对话（不命中任何特殊关键词）。"""

    @pytest.mark.parametrize("text", [
        "今天天气怎么样", "你喜欢什么颜色", "晚饭吃什么呢",
        "给我讲个笑话吧", "无聊了",
    ])
    def test_chat_routes_to_episodic(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_CHAT
        assert plan.target_stores == ["episodic_vector"]
        assert plan.max_rounds == 1
        assert plan.skip_retrieval is False


# ── 5. RECALL_MEMORY ─────────────────────────────────────────────────────


class TestRecallMemory:
    """recall_memory 意图：用户试图回忆过去的对话/事件。"""

    @pytest.mark.parametrize("text", [
        "还记得上次我们聊的那件事吗",
        "你记不记得之前我说过的",
        "以前我们讨论过这个话题",
        "曾经有一次你跟我说过",
    ])
    def test_recall_routes_to_episodic_and_ledger(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_RECALL
        assert plan.target_stores == ["episodic_vector", "sqlite_ledger"]
        assert plan.max_rounds == 2

    def test_recall_extracts_time_lookback(self) -> None:
        plan = _plan("昨天你说了什么来着")
        # "昨天" 同时命中 recall 关键词("之前"类) 或者 chat + time_lookback
        assert plan.time_lookback_days == 2  # "昨天" → 2 天


# ── 6. EMOTION ───────────────────────────────────────────────────────────


class TestEmotion:
    """emotion 意图：情绪表达、心情倾诉。"""

    @pytest.mark.parametrize("text", [
        "心情不好", "好难过啊", "我好开心", "好累啊",
        "最近好焦虑", "感觉好孤独",
    ])
    def test_emotion_routes_to_bond_and_episodic(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_EMOTION
        assert plan.target_stores == ["neo4j_bond", "episodic_vector"]
        assert plan.max_rounds == 2

    def test_emotion_triggers_synonym_expansion(self) -> None:
        """情绪类查询应触发同义词扩展改写。"""
        plan = _plan("好累")
        assert plan.intent == INTENT_EMOTION
        # 应该有改写 query（去停用词 + 同义词扩展）
        assert len(plan.rewritten_queries) > 0


# ── 7. KNOWLEDGE ─────────────────────────────────────────────────────────


class TestKnowledge:
    """knowledge 意图：询问世界观设定、角色信息。"""

    @pytest.mark.parametrize("text", [
        "你是谁", "你叫什么名字", "这个世界观是怎样的",
        "人设是什么", "关于canon的规则",
    ])
    def test_knowledge_routes_to_world_canon(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_KNOWLEDGE
        assert plan.target_stores == ["world_canon"]
        assert plan.max_rounds == 1


# ── 8. TOOL ──────────────────────────────────────────────────────────────


class TestTool:
    """tool 意图：工具调用请求。"""

    @pytest.mark.parametrize("text", [
        "帮我查一下天气", "帮我找个文件", "帮我搜一下这个",
        "执行这个命令", "打开编辑器",
    ])
    def test_tool_routes_to_artifact_vault(self, text: str) -> None:
        plan = _plan(text)
        assert plan.intent == INTENT_TOOL
        assert plan.target_stores == ["artifact_vault"]
        assert plan.max_rounds == 1


# ── 9. COMPLEX ───────────────────────────────────────────────────────────


class TestComplex:
    """complex 意图：长文本 或 多问号触发，全量 5 路。"""

    def test_long_text_triggers_complex(self) -> None:
        long_text = "请帮我详细分析一下" + "这个问题" * 20  # > 100 字
        plan = _plan(long_text)
        assert plan.intent == INTENT_COMPLEX
        assert set(plan.target_stores) == set(ALL_STORE_IDS)
        assert plan.max_rounds == 3

    def test_multiple_question_marks_triggers_complex(self) -> None:
        plan = _plan("为什么会这样？你觉得呢？")
        assert plan.intent == INTENT_COMPLEX
        assert set(plan.target_stores) == set(ALL_STORE_IDS)


# ── 边界情况 ─────────────────────────────────────────────────────────────


class TestEdgeCases:
    """边界情况和交叉验证。"""

    def test_affection_takes_priority_over_emotion(self) -> None:
        """'想你' 同时命中 affection 和 emotion，affection 优先。"""
        plan = _plan("想你了")
        # affection regex 检查在 emotion keyword 之前
        assert plan.intent == INTENT_AFFECTION

    def test_time_lookback_with_recall(self) -> None:
        plan = _plan("上次我们聊的那个话题")
        assert plan.intent == INTENT_RECALL
        assert plan.time_lookback_days == 7  # "上次" → 7 天

    def test_time_lookback_without_recall_intent(self) -> None:
        """即使不是 recall 意图，时间词也会被提取。"""
        plan = _plan("昨天天气真好")
        assert plan.time_lookback_days == 2  # "昨天" → 2 天

    def test_complex_stores_cover_all(self) -> None:
        """complex 意图的 target_stores = 全量 ALL_STORE_IDS。"""
        plan = _plan("x" * 200)  # 超长文本
        assert plan.intent == INTENT_COMPLEX
        assert len(plan.target_stores) == len(ALL_STORE_IDS)

    def test_rewrite_for_retry(self) -> None:
        """rewrite_for_retry 应清除停用词并追加 missing_aspects。"""
        planner = QueryPlanner()
        result = planner.rewrite_for_retry("我想知道这个问题", ["技术", "方案"])
        assert "技术" in result
        assert "方案" in result

    def test_as_dict_serializable(self) -> None:
        """QueryPlan.as_dict() 应返回可序列化的 dict。"""
        plan = _plan("你好呀")
        d = plan.as_dict()
        assert isinstance(d, dict)
        assert "intent" in d
        assert "target_stores" in d
        assert isinstance(d["target_stores"], list)
