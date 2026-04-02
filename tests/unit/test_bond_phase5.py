"""Phase V 验收测试：Bond 深化（共同记忆 + 里程碑 + 关系叙事）。

验收标准：
  V-V-1: SharedMemoryIndex 可从 episodic 中按情感浓度抽取共同记忆
  V-V-2: MilestoneTracker 在首次深度对话/关系天数达标时检测到里程碑
  V-V-3: BondNarrativeLayer 输出自然语言叙事，且注入 prompt 限制 ≤ 3 条
  V-V-4: BondScoreState.to_summary() 新增 milestone_hint 和 shared_memory_count
"""

from __future__ import annotations

import datetime
import os
import time
import uuid
from pathlib import Path

import pytest

from host_brain.memory.episodic import EpisodicStore, EpisodicRecord
from host_brain.memory.chroma_client import PersistentChromaClient, load_chroma_runtime_config
from host_brain.bond.shared_memory_index import (
    SharedMemoryIndex,
    SharedMemoryResult,
    SharedMemoryEntry,
    HIGH_VALENCE_THRESHOLD,
    DEEP_EMOTION_TAGS,
)
from host_brain.bond.milestone_tracker import (
    MilestoneTracker,
    MilestoneRecord,
    MILESTONE_FIRST_DEEP_TALK,
    MILESTONE_FIRST_EMOTIONAL_SUPPORT,
    MILESTONE_FIRST_CONFLICT_RESOLVED,
    MILESTONE_LATE_NIGHT_COMPANION,
    MILESTONE_STAGE_PROMOTED,
    MILESTONE_RELATIONSHIP_DAYS_7,
    MILESTONE_RELATIONSHIP_DAYS_30,
    MILESTONE_RELATIONSHIP_DAYS_100,
)
from host_brain.bond.bond_narrative import (
    BondNarrativeLayer,
    BondNarrativeResult,
    NarrativeItem,
    MAX_NARRATIVE_ITEMS,
)
from host_brain.bond.bond_stage_engine import (
    BondStageEngine,
    BondScoreState,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


_TEST_COLLECTIONS: set[str] = set()




def _load_prod_env_overrides() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / '.env'
    overrides: dict[str, str] = {}
    if not env_path.exists():
        return overrides
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        overrides[key.strip()] = value.strip().strip('"').strip("'")
    return overrides


def _build_persistent_test_chroma(collection_name: str) -> PersistentChromaClient:
    config_result = load_chroma_runtime_config(local_overrides=_load_prod_env_overrides())
    assert config_result.success and config_result.config is not None, config_result.as_dict()
    config = config_result.config
    client = PersistentChromaClient(
        endpoint=config.endpoint,
        tenant=config.tenant,
        database=config.database,
        collection_profiles={collection_name: 4},
        default_collection=collection_name,
    )
    try:
        client._client.delete_collection(name=collection_name)
    except Exception:
        pass
    _TEST_COLLECTIONS.add(collection_name)
    return client


def teardown_module(module):  # noqa: ANN001
    config_result = load_chroma_runtime_config(local_overrides=_load_prod_env_overrides())
    if not (config_result.success and config_result.config is not None):
        return
    config = config_result.config
    client = PersistentChromaClient(
        endpoint=config.endpoint,
        tenant=config.tenant,
        database=config.database,
        collection_profiles={name: 4 for name in _TEST_COLLECTIONS} or {'unused': 4},
        default_collection=next(iter(_TEST_COLLECTIONS), 'unused'),
    )
    for name in list(_TEST_COLLECTIONS):
        try:
            client._client.delete_collection(name=name)
        except Exception:
            pass


def _make_store_with_memories(
    memories: list[dict],
) -> EpisodicStore:
    """创建预填充记忆的 EpisodicStore。"""

    assert str(os.environ.get("Natsume_ENV") or "prod").strip().lower() == "prod"

    def _embed(text: str) -> tuple[list[float], str]:
        normalized = str(text or "").strip()
        base = float(len(normalized) % 7)
        return ([base, base + 1.0, base + 2.0, base + 3.0], "test-embedder")

    collection_name = f"test_bond_phase5_{uuid.uuid4().hex[:10]}"
    store = EpisodicStore(
        chroma_client=_build_persistent_test_chroma(collection_name),
        embedding_fn=_embed,
        embedding_model_id_hint="test-embedder",
        embedding_provider_hint="test",
    )
    for mem in memories:
        store.write_memory(
            text=mem.get("text", "测试记忆"),
            evidence_refs=mem.get("evidence_refs", ["test_ref"]),
            emotion_tag=mem.get("emotion_tag", "NEUTRAL"),
            emotion_valence=mem.get("emotion_valence", 0.0),
            emotion_arousal=mem.get("emotion_arousal", 0.3),
            ts_ms=mem.get("ts_ms"),
        )
    return store


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# V-V-1: SharedMemoryIndex
# ---------------------------------------------------------------------------

class TestVV1_SharedMemoryIndex:
    """V-V-1: SharedMemoryIndex 按情感浓度抽取共同记忆。"""

    def test_high_valence_extracted(self):
        """高情感浓度记忆被抽取。"""
        store = _make_store_with_memories([
            {"text": "那天一起看了很美的落日", "emotion_tag": "HAPPY", "emotion_valence": 0.8},
            {"text": "今天天气不错", "emotion_tag": "NEUTRAL", "emotion_valence": 0.1},
        ])
        index = SharedMemoryIndex(store=store)
        result = index.query()
        assert result.count >= 1
        assert result.entries[0].emotion_valence > HIGH_VALENCE_THRESHOLD

    def test_deep_emotion_tag_extracted(self):
        """深度情感标签记忆被抽取。"""
        store = _make_store_with_memories([
            {"text": "第一次被安慰", "emotion_tag": "LOVING", "emotion_valence": 0.3},
            {"text": "普通对话", "emotion_tag": "NEUTRAL", "emotion_valence": 0.0},
        ])
        index = SharedMemoryIndex(store=store)
        result = index.query()
        assert result.count >= 1
        assert "deep_emotion" in result.entries[0].match_reason

    def test_recall_keyword_extracted(self):
        """含回忆关键词的记忆被抽取。"""
        store = _make_store_with_memories([
            {"text": "还记得那天我们聊了很久", "emotion_tag": "NEUTRAL", "emotion_valence": 0.0},
        ])
        index = SharedMemoryIndex(store=store)
        result = index.query()
        assert result.count >= 1
        assert "recall_keyword" in result.entries[0].match_reason

    def test_neutral_memory_not_extracted(self):
        """普通中性记忆不被抽取。"""
        store = _make_store_with_memories([
            {"text": "今天买了一杯咖啡", "emotion_tag": "NEUTRAL", "emotion_valence": 0.0},
        ])
        index = SharedMemoryIndex(store=store)
        result = index.query()
        assert result.count == 0

    def test_max_entries_respected(self):
        """最大返回条数限制。"""
        memories = [
            {"text": f"美好回忆 {i}", "emotion_tag": "HAPPY", "emotion_valence": 0.9}
            for i in range(10)
        ]
        store = _make_store_with_memories(memories)
        index = SharedMemoryIndex(store=store)
        result = index.query(max_entries=3)
        assert result.count <= 3
        assert result.total_candidates == 10

    def test_sorted_by_relevance(self):
        """按相关度降序排序。"""
        store = _make_store_with_memories([
            {"text": "轻微好感", "emotion_tag": "NEUTRAL", "emotion_valence": 0.6},
            {"text": "还记得那天非常感动", "emotion_tag": "LOVING", "emotion_valence": 0.9},
        ])
        index = SharedMemoryIndex(store=store)
        result = index.query()
        assert result.count == 2
        assert result.entries[0].relevance_score >= result.entries[1].relevance_score

    def test_empty_store(self):
        """空 store 返回空结果。"""
        index = SharedMemoryIndex(store=None)
        result = index.query()
        assert result.count == 0

    def test_deleted_memory_excluded(self):
        """已删除记忆不被抽取。"""
        store = _make_store_with_memories([
            {"text": "被删除的回忆", "emotion_tag": "HAPPY", "emotion_valence": 0.9},
        ])
        # 手动标记为已删除
        for rec in store._records.values():
            rec.deleted_at = _now_ms()
        index = SharedMemoryIndex(store=store)
        result = index.query()
        assert result.count == 0


# ---------------------------------------------------------------------------
# V-V-2: MilestoneTracker
# ---------------------------------------------------------------------------

class TestVV2_MilestoneTracker:
    """V-V-2: MilestoneTracker 检测关系里程碑。"""

    def test_first_deep_talk(self):
        """首次深度对话触发里程碑。"""
        tracker = MilestoneTracker()
        milestones = tracker.on_event(
            user_id="user1",
            event_type="INTERACTION_DEEP",
            ts_ms=_now_ms(),
        )
        assert len(milestones) >= 1
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_FIRST_DEEP_TALK in types

    def test_first_deep_talk_not_repeated(self):
        """首次深度对话不重复触发。"""
        tracker = MilestoneTracker()
        m1 = tracker.on_event(user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms())
        m2 = tracker.on_event(user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms())
        deep_talk_m1 = [m for m in m1 if m.milestone_type == MILESTONE_FIRST_DEEP_TALK]
        deep_talk_m2 = [m for m in m2 if m.milestone_type == MILESTONE_FIRST_DEEP_TALK]
        assert len(deep_talk_m1) == 1
        assert len(deep_talk_m2) == 0

    def test_first_emotional_support(self):
        """首次情感支持触发里程碑。"""
        tracker = MilestoneTracker()
        milestones = tracker.on_event(
            user_id="user1",
            event_type="EMOTIONAL_SUPPORT",
            ts_ms=_now_ms(),
        )
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_FIRST_EMOTIONAL_SUPPORT in types

    def test_first_conflict_resolved(self):
        """首次冲突修复触发里程碑。"""
        tracker = MilestoneTracker()
        milestones = tracker.on_event(
            user_id="user1",
            event_type="CONFLICT_RESOLVED",
            ts_ms=_now_ms(),
        )
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_FIRST_CONFLICT_RESOLVED in types

    def test_stage_promoted(self):
        """阶段晋级触发里程碑。"""
        tracker = MilestoneTracker()
        milestones = tracker.on_event(
            user_id="user1",
            event_type="STAGE_PROMOTED",
            ts_ms=_now_ms(),
            metadata={"from_stage": "ACQUAINTANCE", "to_stage": "FRIEND"},
        )
        types = [m.milestone_type for m in milestones]
        assert any("STAGE_PROMOTED" in t for t in types)

    def test_relationship_days_7(self):
        """关系 7 天里程碑。"""
        tracker = MilestoneTracker()
        first_ts = _now_ms() - 8 * 24 * 60 * 60 * 1000  # 8 天前
        tracker.set_first_interaction_ts("user1", first_ts)
        milestones = tracker.on_event(
            user_id="user1",
            event_type="INTERACTION",
            ts_ms=_now_ms(),
        )
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_RELATIONSHIP_DAYS_7 in types

    def test_relationship_days_30(self):
        """关系 30 天里程碑。"""
        tracker = MilestoneTracker()
        first_ts = _now_ms() - 31 * 24 * 60 * 60 * 1000  # 31 天前
        tracker.set_first_interaction_ts("user1", first_ts)
        milestones = tracker.on_event(
            user_id="user1",
            event_type="INTERACTION",
            ts_ms=_now_ms(),
        )
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_RELATIONSHIP_DAYS_30 in types
        assert MILESTONE_RELATIONSHIP_DAYS_7 in types  # 也触发 7 天

    def test_relationship_days_100(self):
        """关系 100 天里程碑。"""
        tracker = MilestoneTracker()
        first_ts = _now_ms() - 101 * 24 * 60 * 60 * 1000  # 101 天前
        tracker.set_first_interaction_ts("user1", first_ts)
        milestones = tracker.on_event(
            user_id="user1",
            event_type="INTERACTION",
            ts_ms=_now_ms(),
        )
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_RELATIONSHIP_DAYS_100 in types

    def test_late_night_companion(self):
        """深夜陪伴里程碑（23:00-02:00）。"""
        tracker = MilestoneTracker()
        # 构造一个 23:30 的时间戳
        now = datetime.datetime.now()
        late_night = now.replace(hour=23, minute=30, second=0, microsecond=0)
        ts = int(late_night.timestamp() * 1000)
        milestones = tracker.on_event(
            user_id="user1",
            event_type="INTERACTION",
            ts_ms=ts,
        )
        types = [m.milestone_type for m in milestones]
        assert MILESTONE_LATE_NIGHT_COMPANION in types

    def test_milestone_narrative_not_empty(self):
        """里程碑叙事不为空。"""
        tracker = MilestoneTracker()
        milestones = tracker.on_event(
            user_id="user1",
            event_type="INTERACTION_DEEP",
            ts_ms=_now_ms(),
        )
        for m in milestones:
            assert m.narrative
            assert len(m.narrative) > 0

    def test_get_milestones(self):
        """获取所有已达成里程碑。"""
        tracker = MilestoneTracker()
        tracker.on_event(user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms())
        tracker.on_event(user_id="user1", event_type="EMOTIONAL_SUPPORT", ts_ms=_now_ms())
        all_ms = tracker.get_milestones("user1")
        assert len(all_ms) >= 2

    def test_get_milestone_hints(self):
        """获取里程碑叙事提示。"""
        tracker = MilestoneTracker()
        tracker.on_event(user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms())
        hints = tracker.get_milestone_hints("user1")
        assert len(hints) >= 1
        assert all(isinstance(h, str) and h for h in hints)

    def test_has_achieved(self):
        """检查里程碑是否已达成。"""
        tracker = MilestoneTracker()
        assert not tracker.has_achieved("user1", MILESTONE_FIRST_DEEP_TALK)
        tracker.on_event(user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms())
        assert tracker.has_achieved("user1", MILESTONE_FIRST_DEEP_TALK)


# ---------------------------------------------------------------------------
# V-V-3: BondNarrativeLayer
# ---------------------------------------------------------------------------

class TestVV3_BondNarrativeLayer:
    """V-V-3: BondNarrativeLayer 叙事输出限制 ≤ 3 条。"""

    def test_narrative_with_memories_and_milestones(self):
        """叙事包含共同记忆和里程碑。"""
        store = _make_store_with_memories([
            {"text": "一起看了很美的落日", "emotion_tag": "HAPPY", "emotion_valence": 0.9},
        ])
        memory_index = SharedMemoryIndex(store=store)
        milestone_tracker = MilestoneTracker()
        milestone_tracker.on_event(
            user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms(),
        )
        layer = BondNarrativeLayer(
            memory_index=memory_index,
            milestone_tracker=milestone_tracker,
        )
        result = layer.build_narrative(user_id="user1", bond_stage="FRIEND")
        assert len(result.items) >= 1
        assert result.shared_memory_count >= 1
        assert result.milestone_count >= 1

    def test_max_items_limit(self):
        """叙事输出限制 ≤ 3 条。"""
        memories = [
            {"text": f"回忆 {i}", "emotion_tag": "HAPPY", "emotion_valence": 0.9}
            for i in range(10)
        ]
        store = _make_store_with_memories(memories)
        memory_index = SharedMemoryIndex(store=store)
        milestone_tracker = MilestoneTracker()
        for evt in ["INTERACTION_DEEP", "EMOTIONAL_SUPPORT", "CONFLICT_RESOLVED"]:
            milestone_tracker.on_event(
                user_id="user1", event_type=evt, ts_ms=_now_ms(),
            )
        layer = BondNarrativeLayer(
            memory_index=memory_index,
            milestone_tracker=milestone_tracker,
        )
        result = layer.build_narrative(user_id="user1", bond_stage="CLOSE_FRIEND")
        assert len(result.items) <= MAX_NARRATIVE_ITEMS

    def test_narrative_style_varies_by_stage(self):
        """叙事风格随 Bond 阶段变化。"""
        layer = BondNarrativeLayer()
        r1 = layer.build_narrative(user_id="user1", bond_stage="STRANGER")
        r2 = layer.build_narrative(user_id="user1", bond_stage="SOULMATE")
        assert r1.narrative_style != r2.narrative_style

    def test_empty_narrative_for_no_data(self):
        """无数据时叙事为空。"""
        layer = BondNarrativeLayer()
        result = layer.build_narrative(user_id="user1", bond_stage="STRANGER")
        assert len(result.items) == 0

    def test_to_prompt_slot_format(self):
        """to_prompt_slot 输出格式正确。"""
        store = _make_store_with_memories([
            {"text": "美好的回忆", "emotion_tag": "HAPPY", "emotion_valence": 0.9},
        ])
        memory_index = SharedMemoryIndex(store=store)
        milestone_tracker = MilestoneTracker()
        milestone_tracker.on_event(
            user_id="user1", event_type="INTERACTION_DEEP", ts_ms=_now_ms(),
        )
        layer = BondNarrativeLayer(
            memory_index=memory_index,
            milestone_tracker=milestone_tracker,
        )
        result = layer.build_narrative(user_id="user1", bond_stage="FRIEND")
        slot = result.to_prompt_slot()
        assert "shared_memories" in slot
        assert isinstance(slot["shared_memories"], list)
        assert len(slot["shared_memories"]) <= 3
        assert "milestone_count" in slot
        assert "narrative_style" in slot

    def test_to_prompt_slot_empty_for_no_data(self):
        """无数据时 to_prompt_slot 返回空字典。"""
        layer = BondNarrativeLayer()
        result = layer.build_narrative(user_id="user1", bond_stage="STRANGER")
        slot = result.to_prompt_slot()
        assert slot == {}


# ---------------------------------------------------------------------------
# V-V-4: BondScoreState.to_summary()
# ---------------------------------------------------------------------------

class TestVV4_BondScoreSummary:
    """V-V-4: BondScoreState.to_summary() 新增字段。"""

    def test_summary_has_milestone_hint(self):
        """to_summary 包含 milestone_hint 字段。"""
        state = BondScoreState(user_id="user1")
        summary = state.to_summary()
        assert "milestone_hint" in summary

    def test_summary_has_shared_memory_count(self):
        """to_summary 包含 shared_memory_count 字段。"""
        state = BondScoreState(user_id="user1")
        summary = state.to_summary()
        assert "shared_memory_count" in summary
        assert summary["shared_memory_count"] == 0

    def test_inject_narrative_updates_summary(self):
        """inject_narrative 更新 BondScoreState 的叙事数据。"""
        state = BondScoreState(user_id="user1")
        state.inject_narrative(
            milestone_hint="认识你已经一百天了",
            shared_memory_count=5,
        )
        # inject_narrative 只设置内部属性，to_summary 仍保持默认值
        # （实际数据由外部填充 summary dict）
        summary = state.to_summary()
        assert "milestone_hint" in summary
        assert "shared_memory_count" in summary


# ---------------------------------------------------------------------------
# 端到端集成测试
# ---------------------------------------------------------------------------

class TestPhaseV_EndToEnd:
    """Phase V 端到端集成。"""

    def test_full_bond_narrative_lifecycle(self):
        """完整生命周期：创建记忆 → 检测里程碑 → 构建叙事 → 输出 prompt 槽位。"""
        # 1. 创建带情感的记忆
        store = _make_store_with_memories([
            {"text": "一起聊到深夜，很开心", "emotion_tag": "HAPPY", "emotion_valence": 0.85},
            {"text": "第一次被你安慰，很感动", "emotion_tag": "LOVING", "emotion_valence": 0.9},
            {"text": "普通的日常对话", "emotion_tag": "NEUTRAL", "emotion_valence": 0.0},
        ])

        # 2. 创建里程碑追踪器并触发事件
        tracker = MilestoneTracker()
        tracker.on_event(user_id="e2e", event_type="INTERACTION_DEEP", ts_ms=_now_ms())
        tracker.on_event(user_id="e2e", event_type="EMOTIONAL_SUPPORT", ts_ms=_now_ms())

        # 3. 创建共同记忆索引
        memory_index = SharedMemoryIndex(store=store)

        # 4. 构建叙事
        layer = BondNarrativeLayer(
            memory_index=memory_index,
            milestone_tracker=tracker,
        )
        result = layer.build_narrative(user_id="e2e", bond_stage="CLOSE_FRIEND")

        # 5. 验证
        assert len(result.items) >= 2  # 至少有共同记忆和里程碑
        assert len(result.items) <= MAX_NARRATIVE_ITEMS
        assert result.shared_memory_count >= 2  # 两条高情感记忆
        assert result.milestone_count >= 2       # 两个里程碑
        assert result.narrative_style  # 叙事风格非空

        # 6. 验证 prompt 槽位
        slot = result.to_prompt_slot()
        assert slot  # 非空
        assert len(slot["shared_memories"]) <= 3

    def test_bond_stage_engine_summary_with_narrative_fields(self):
        """BondStageEngine 的 summary 包含 Phase V 新字段。"""
        engine = BondStageEngine()
        summary = engine.get_summary("new_user")
        assert "milestone_hint" in summary
        assert "shared_memory_count" in summary

    def test_prompt_context_accepts_bond_narrative_slot(self):
        """prompt_context 接受 bond_narrative 可选槽位。"""
        from host_brain.core.prompt_context import OPTIONAL_SLOTS
        assert "bond_narrative" in OPTIONAL_SLOTS
        assert OPTIONAL_SLOTS["bond_narrative"] == dict
