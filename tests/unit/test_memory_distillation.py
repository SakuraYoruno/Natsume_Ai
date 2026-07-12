"""M10-C 记忆蒸馏系统单元测试。

T-Distill-01：distill_top_k 正常蒸馏 — 有情节记忆时应返回 semantic_id，写入 semantic_memory 表。
T-Distill-02：幂等检查 — 相同 source_memory_ids 组合重复蒸馏应跳过（skipped=True）。
T-Distill-03：无情节记忆 — 空 EpisodicStore 时 distill_top_k 应返回 success=False。
T-Distill-04：list_semantic_memories 分页 — 写入多条后分页查询正确。
T-Distill-05：prompt_context semantic_slot 验证 — 合法 semantic_slot 通过验证，缺字段时报错。
T-Distill-06：build_semantic_slot — 从 ledger 构建 semantic_slot 列表格式正确。
"""

from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.distillation import MemoryDistillationEngine, resolve_memory_distillation_codex_model
from host_brain.memory.episodic import EpisodicStore
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
from host_brain.core.prompt_context import (
    validate_prompt_context,
    build_semantic_slot,
    EXPECTED_SLOTS,
)
from tests.fakes.chroma_client import FakeChromaClient


# ---------------------------------------------------------------------------
# 共享 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger():
    # Direct LifeMemoryStore writes are fixture-only after AW03; production
    # durable writes must go through LifeMemoryService/Repository.
    return SQLiteLedger(db_path=":memory:")


@pytest.fixture()
def episodic(ledger):
    chroma = FakeChromaClient()
    return EpisodicStore(ledger=ledger, chroma_client=chroma)


@pytest.fixture()
def engine(episodic, ledger):
    return MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)


def _write_memories(episodic: EpisodicStore, texts: list[str]) -> list[str]:
    """辅助：批量写入情节记忆，返回 memory_id 列表。"""
    ids = []
    for text in texts:
        result = episodic.write_memory(
            text=text,
            evidence_refs=["test-evidence-001"],
            ts_ms=1000,
        )
        assert result.success
        ids.append(result.memory_id)
    return ids


# ---------------------------------------------------------------------------
# T-Distill-01：正常蒸馏
# ---------------------------------------------------------------------------

def test_distill_top_k_success(engine, episodic, ledger):
    """有情节记忆时 distill_top_k 应返回 semantic_id，写入 semantic_memory 表。"""
    _write_memories(episodic, [
        "夏目贵志是一个能看见妖怪的少年",
        "猫咪老师是一只强大的妖怪，外形像猫",
        "夏目继承了祖母玲子的友人帐",
    ])

    result = engine.distill_top_k(
        query_text="夏目贵志",
        top_k=3,
        tags=["主角", "妖怪"],
        ts_ms=2000,
    )

    assert result.success is True
    assert result.semantic_id is not None
    assert result.source_count > 0
    assert result.skipped is False

    # 验证写入了 semantic_memory 表
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["semantic_id"] == result.semantic_id
    assert "夏目贵志" in items[0]["summary"]
    assert items[0]["confidence"] >= 0.0


# ---------------------------------------------------------------------------
# T-Distill-02：幂等检查
# ---------------------------------------------------------------------------

def test_distill_top_k_idempotent(engine, episodic, ledger):
    """相同 source_memory_ids 组合重复蒸馏应跳过（skipped=True），不重复写入。"""
    _write_memories(episodic, ["夏目贵志能看见妖怪"])

    result1 = engine.distill_top_k(query_text="夏目", top_k=1, ts_ms=1000)
    assert result1.success is True
    assert result1.skipped is False

    # 第二次相同查询（召回相同 source_ids）
    result2 = engine.distill_top_k(query_text="夏目", top_k=1, ts_ms=2000)
    assert result2.success is True
    assert result2.skipped is True
    assert result2.semantic_id == result1.semantic_id

    # 数据库中只有一条记录
    _, total = ledger.list_semantic_memories()
    assert total == 1


# ---------------------------------------------------------------------------
# T-Distill-03：无情节记忆
# ---------------------------------------------------------------------------

def test_distill_top_k_empty_store(engine):
    """空 EpisodicStore 时 distill_top_k 应返回 success=False。"""
    result = engine.distill_top_k(query_text="夏目贵志", top_k=5)
    assert result.success is False
    assert result.semantic_id is None
    assert result.source_count == 0


def test_distill_top_k_uses_persistent_chroma_rows_when_process_records_empty(tmp_path):
    """真实持久化 Chroma 有记录但进程内 _records 为空时，蒸馏仍应可生成 semantic_memory。"""
    ledger = SQLiteLedger(db_path=str(tmp_path / "persistent.sqlite3"), check_same_thread=False)
    chroma = FakeChromaClient(collection_profiles={"episodic": 3}, default_collection="episodic")
    episodic = EpisodicStore(
        ledger=ledger,
        chroma_client=chroma,
        embedding_fn=lambda _text: ([1.0, 0.0, 0.0], "test-embed"),
    )
    chroma.upsert(
        memory_id="audit::mem_low_energy",
        text="用户低能量时希望先得到一个小步骤，不要连续追问。",
        embedding=[1.0, 0.0, 0.0],
        metadata={"memory_layer": "long_term", "ts_ms": 1234, "emotion_tag": "CALM"},
    )
    engine = MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)

    result = engine.distill_top_k(
        query_text="低能量回应偏好",
        top_k=3,
        tags=["acceptance", "persistent_chroma"],
        ts_ms=2000,
    )

    assert len(episodic._records) == 0
    assert result.success is True
    assert result.semantic_id is not None
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["source_memory_ids"] == ["audit::mem_low_energy"]


def test_distill_top_k_ignores_existing_semantic_memory_projection(tmp_path):
    """蒸馏不能把已生成的 semantic_memory 再次当成源记忆，避免长期记忆自循环。"""
    ledger = SQLiteLedger(db_path=str(tmp_path / "no_recursive.sqlite3"), check_same_thread=False)
    chroma = FakeChromaClient(collection_profiles={"episodic": 3}, default_collection="episodic")
    episodic = EpisodicStore(
        ledger=ledger,
        chroma_client=chroma,
        embedding_fn=lambda _text: ([1.0, 0.0, 0.0], "test-embed"),
    )
    chroma.upsert(
        memory_id="semantic::sem_existing",
        text="已经蒸馏过的长期语义摘要。",
        embedding=[1.0, 0.0, 0.0],
        metadata={
            "memory_layer": "long_term",
            "source_table": "semantic_memory",
            "semantic_id": "sem_existing",
        },
    )
    chroma.upsert(
        memory_id="audit::mem_source",
        text="用户低能量时希望先得到一个小步骤，不要连续追问。",
        embedding=[1.0, 0.0, 0.0],
        metadata={"memory_layer": "long_term", "source_table": "memory_ops_log"},
    )
    engine = MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)

    result = engine.distill_top_k(query_text="低能量回应偏好", top_k=1, ts_ms=2000)

    assert result.success is True
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["source_memory_ids"] == ["audit::mem_source"]


def test_distill_top_k_prefers_trusted_life_episode_sources(episodic, ledger):
    """长期语义记忆应优先从可信 life_episode 蒸馏，而不是绕回旧 episodic 文本。"""
    life_store = LifeMemoryStore(ledger=ledger)
    trusted = life_store.write_chat_episode(
        request_id="req-life-distill-trusted",
        session_id="sess-life-distill",
        person_id="user-1",
        user_message="记住我喜欢轻小说推荐按总览和卡片分段",
        assistant_reply="我会按总览一段、每本书一张卡片来发。",
        ts_ms=1_800_000_000_000,
    )
    suppressed = life_store.write_chat_episode(
        request_id="req-life-distill-suppressed",
        session_id="sess-life-distill",
        person_id="user-1",
        user_message="错误偏好：推荐不要分段",
        assistant_reply="已记录错误偏好。",
        ts_ms=1_800_000_000_001,
    )
    life_store.correct_episode(
        episode_id=suppressed["episode_id"],
        correction_kind="suppression",
        suppress=True,
        detail="用户后来明确纠正推荐需要分段。",
        ts_ms=1_800_000_000_100,
    )
    _write_memories(episodic, ["旧 episodic 噪音：用户其实不关心轻小说推荐格式"])
    engine = MemoryDistillationEngine(
        episodic_store=episodic,
        ledger=ledger,
        life_memory_store=life_store,
    )

    result = engine.distill_top_k(
        query_text="轻小说推荐 分段 卡片",
        top_k=3,
        tags=["preference"],
        ts_ms=1_800_000_000_200,
    )

    assert result.success is True
    items, total = ledger.list_semantic_memories()
    assert total == 1
    source_ids = items[0]["source_memory_ids"]
    assert source_ids == [f"life_episode::{trusted['episode_id']}"]
    assert f"life_episode::{suppressed['episode_id']}" not in source_ids
    assert "source:life_episode" in items[0]["tags"]
    assert "life_consolidated" in items[0]["tags"]
    assert items[0]["distill_strategy"] == "life_episode_rule_consolidation"
    assert "用户偏好" in items[0]["summary"]
    assert "User:" not in items[0]["summary"]
    assert "Assistant:" not in items[0]["summary"]
    assert "旧 episodic 噪音" not in items[0]["summary"]


def test_distill_top_k_life_store_uses_llm_distillation_when_available(episodic, ledger):
    """LifeMemory 优先路径有 LLM 客户端时应基于 episode evidence 生成语义摘要。"""

    class FakeSummaryClient:
        def __init__(self) -> None:
            self.calls = []

        def summarize(self, texts, *, query_text, max_tokens=200):
            self.calls.append({"texts": list(texts), "query_text": query_text, "max_tokens": max_tokens})
            assert any("user_text:" in text for text in texts)
            assert any("assistant_reply_as_action_evidence:" in text for text in texts)
            return "用户长期偏好：轻小说推荐要先讲题材差异，再讲读感和推荐理由。"

    life_store = LifeMemoryStore(ledger=ledger)
    episode = life_store.write_chat_episode(
        request_id="req-life-distill-llm",
        session_id="sess-life-distill-llm",
        person_id="user-1",
        user_message="以后推荐轻小说时，不要只看标题，要读正文后讲题材差异和读感。",
        assistant_reply="明白，我会先看正文，再说明哪本更值得看和原因。",
        ts_ms=1_800_000_050_000,
    )
    fake_llm = FakeSummaryClient()
    engine = MemoryDistillationEngine(
        episodic_store=episodic,
        ledger=ledger,
        life_memory_store=life_store,
    )

    result = engine.distill_top_k(
        query_text="轻小说 推荐 正文 读感",
        top_k=3,
        tags=["preference"],
        ts_ms=1_800_000_050_100,
        llm_client=fake_llm,
        require_llm=True,
    )

    assert result.success is True
    assert fake_llm.calls
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["source_memory_ids"] == [f"life_episode::{episode['episode_id']}"]
    assert items[0]["distill_strategy"] == "life_episode_llm_summary"
    assert "llm_distilled" in items[0]["tags"]
    assert "source:life_episode" in items[0]["tags"]
    assert items[0]["summary"] == "用户长期偏好：轻小说推荐要先讲题材差异，再讲读感和推荐理由。"


def test_distill_top_k_life_store_require_llm_does_not_rule_fallback(episodic, ledger):
    """测试/验收模式要求 LLM 时，不能用规则巩固冒充蒸馏成功。"""
    life_store = LifeMemoryStore(ledger=ledger)
    life_store.write_chat_episode(
        request_id="req-life-distill-require-llm",
        session_id="sess-life-distill-require-llm",
        person_id="user-1",
        user_message="以后推荐轻小说要先读正文再判断。",
        assistant_reply="我会先读正文。",
        ts_ms=1_800_000_060_000,
    )
    engine = MemoryDistillationEngine(
        episodic_store=episodic,
        ledger=ledger,
        life_memory_store=life_store,
    )

    result = engine.distill_top_k(
        query_text="轻小说 推荐 正文",
        top_k=3,
        tags=["preference"],
        ts_ms=1_800_000_060_100,
        require_llm=True,
    )

    assert result.success is False
    assert "llm_client is unavailable" in result.detail
    items, total = ledger.list_semantic_memories()
    assert total == 0
    assert items == []


def test_memory_distillation_codex_model_is_decoupled_from_global_codex_model(monkeypatch):
    """蒸馏 Codex 模型应有独立默认值，避免继承主链/全局 Codex 模型。"""
    monkeypatch.setenv("HOST_CODEX_MODEL_ID", "gpt-5.4-mini")
    monkeypatch.setenv("HOST_CODEX_RUNTIME_MODEL", "gpt-5.4-mini")
    monkeypatch.delenv("HOST_MEMORY_DISTILLATION_CODEX_MODEL_ID", raising=False)
    monkeypatch.delenv("HOST_MEMORY_DISTILLATION_CODEX_MODEL", raising=False)

    assert resolve_memory_distillation_codex_model() == "gpt-5.5"

    monkeypatch.setenv("HOST_MEMORY_DISTILLATION_CODEX_MODEL_ID", "gpt-5.3-codex-spark")
    assert resolve_memory_distillation_codex_model() == "gpt-5.3-codex-spark"
    assert resolve_memory_distillation_codex_model("gpt-5.5") == "gpt-5.5"


def test_distill_top_k_filters_noisy_life_episode_sources(episodic, ledger):
    """长期语义蒸馏不能把发送日志、深读日志、后台 tick 升格为主动记忆。"""
    life_store = LifeMemoryStore(ledger=ledger)
    durable = life_store.write_chat_episode(
        request_id="req-life-distill-durable-preference",
        session_id="sess-life-distill-noise",
        person_id="user-1",
        user_message="以后轻小说推荐要总览一段，后面每本书按卡片分段。",
        assistant_reply="收到，我会按总览和每本一卡片发送。",
        ts_ms=1_800_000_100_000,
    )
    deep_review = life_store.write_background_episode(
        event_source="surface_memory_recall",
        event_id="deep-review-noise",
        status="OK",
        reason_code="LIGHTNOVEL_DEEP_REVIEW_OK",
        detail="网页深读结果：轻小说推荐 分段 卡片；正文内容足够支撑判断；详情:https://www.lightnovel.fun/cn/detail/1",
        ts_ms=1_800_000_100_100,
        metadata={"action_kind": "lightnovel_recommendation_deep_review"},
    )
    gateway_send = life_store.write_background_episode(
        event_source="gateway",
        event_id="qq-send-noise",
        status="SENT",
        detail="我通过 qq 给 qq_dm_1 发送了image消息；轻小说推荐 分段 卡片；latency_ms=123",
        ts_ms=1_800_000_100_200,
        metadata={"action_kind": "qq_reply_sent"},
    )
    tick = life_store.write_background_episode(
        event_source="scheduler",
        event_id="tick-noise",
        task_id="emotion_decay_tick",
        status="OK",
        detail="emotion_decay_tick 轻小说推荐 分段 卡片 health_check OK",
        ts_ms=1_800_000_100_300,
    )
    inner = life_store.write_background_episode(
        event_source="inner_world",
        event_id="inner-not-user-pref",
        status="OK",
        detail="先把话磨得更像我自己，别再飘成一口空话。",
        ts_ms=1_800_000_100_350,
        metadata={"should_distill_to_memory": True, "action_kind": "inner_world_monologue_tick"},
    )
    engine = MemoryDistillationEngine(
        episodic_store=episodic,
        ledger=ledger,
        life_memory_store=life_store,
    )

    result = engine.distill_top_k(
        query_text="轻小说推荐 分段 卡片",
        top_k=5,
        tags=["preference"],
        ts_ms=1_800_000_100_400,
    )

    assert result.success is True
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["source_memory_ids"] == [f"life_episode::{durable['episode_id']}"]
    assert f"life_episode::{deep_review['episode_id']}" not in items[0]["source_memory_ids"]
    assert f"life_episode::{gateway_send['episode_id']}" not in items[0]["source_memory_ids"]
    assert f"life_episode::{tick['episode_id']}" not in items[0]["source_memory_ids"]
    assert f"life_episode::{inner['episode_id']}" not in items[0]["source_memory_ids"]
    summary = items[0]["summary"]
    assert "用户偏好" in summary
    assert "网页深读结果" not in summary
    assert "发送了image消息" not in summary
    assert "emotion_decay_tick" not in summary
    assert "latency_ms" not in summary
    assert "一口空话" not in summary


def test_life_memory_consolidator_distills_explicit_project_facts_without_acceptance_query(ledger):
    """长期蒸馏要固化明确项目事实/测试事实，不能把验收查询句当成用户偏好。"""
    life_store = LifeMemoryStore(ledger=ledger)
    turns = [
        (
            "req-distill-test-code-r1",
            "记住一个长对话测试事实：当前测试代号是蓝莲-0531-R1。",
            "记住了。",
        ),
        (
            "req-distill-lighthouse",
            "再补一个项目事实：海边灯塔指连续记忆锚点，不是字面灯塔。",
            "记住了。",
        ),
        (
            "req-distill-prewarm",
            "再补一个项目事实：app-server预热指标是prewarm_latency_ms=665、prewarm_threads=7、prewarm_workers=1。",
            "记住了。",
        ),
        (
            "req-distill-format",
            "记住轻小说推荐格式：书名 + 一句话理由，不要卡片模板。",
            "记住了。",
        ),
        (
            "req-distill-code-r2",
            "纠正测试代号：蓝莲-0531-R1 改成蓝莲-0531-R2。",
            "记住了。",
        ),
        (
            "req-distill-acceptance-query",
            "最终同会话验收：请列出当前测试代号、海边灯塔到底指什么、app-server预热指标、轻小说推荐格式。",
            "测试代号蓝莲-0531-R2，海边灯塔是连续记忆锚点。",
        ),
    ]
    for index, (request_id, user_message, assistant_reply) in enumerate(turns):
        life_store.write_chat_episode(
            request_id=request_id,
            session_id="sess-distill-project-facts",
            person_id="user-distill-project-facts",
            user_message=user_message,
            assistant_reply=assistant_reply,
            ts_ms=1_800_000_300_000 + index,
        )

    result = LifeMemoryConsolidator(store=life_store).distill_semantic_memories(
        limit=40,
        max_records=20,
        ts_ms=1_800_000_300_100,
    )
    items, total = ledger.list_semantic_memories(limit=40)
    summaries = "\n".join(item["summary"] for item in items)

    assert result["written_count"] >= 5
    assert total >= 5
    assert "稳定事实：当前测试代号是蓝莲-0531-R1" in summaries
    assert "稳定事实：海边灯塔指连续记忆锚点，不是字面灯塔" in summaries
    assert "稳定事实：app-server预热指标是prewarm_latency_ms=665、prewarm_threads=7、prewarm_workers=1" in summaries
    assert "用户偏好：轻小说推荐格式：书名 + 一句话理由，不要卡片模板" in summaries
    assert "纠错记忆：纠正测试代号：蓝莲-0531-R1 改成蓝莲-0531-R2" in summaries
    assert "最终同会话验收" not in summaries


def test_distill_top_k_with_life_store_rejects_lightnovel_quality_guess(episodic, ledger):
    """有 LifeMemoryStore 时不能把轻小说追问里的模板化猜测蒸成长期事实，也不能回退旧 episodic。"""
    life_store = LifeMemoryStore(ledger=ledger)
    life_store.write_chat_episode(
        request_id="req-life-distill-quality-guess",
        session_id="sess-life-distill-quality",
        person_id="user-1",
        user_message="昨天那几本哪本好看吗？",
        assistant_reply="就我会选《与神在河岸》，通常算吃设定、吃文风，整体口碑不错。",
        ts_ms=1_800_000_200_000,
    )
    _write_memories(episodic, ["旧 episodic 噪音：用户喜欢《与神在河岸》，通常算吃设定、吃文风。"])
    engine = MemoryDistillationEngine(
        episodic_store=episodic,
        ledger=ledger,
        life_memory_store=life_store,
    )

    result = engine.distill_top_k(
        query_text="与神在河岸 哪本好看",
        top_k=3,
        tags=["long_term_distill"],
        ts_ms=1_800_000_200_100,
    )

    assert result.success is False
    assert "legacy episodic distillation disabled" in result.detail
    items, total = ledger.list_semantic_memories()
    assert total == 0
    assert items == []


def test_distill_top_k_with_life_store_keeps_self_change_principle(episodic, ledger):
    """记忆系统自身原则应作为自我连续性记忆，从 life episode 规则巩固。"""
    life_store = LifeMemoryStore(ledger=ledger)
    episode = life_store.write_chat_episode(
        request_id="req-life-distill-self-change",
        session_id="sess-life-distill-self-change",
        person_id="user-1",
        user_message="记忆系统改造原则：她做过的事都要留下生命经历锚点，但不等于所有日志都进入可召回记忆。",
        assistant_reply="明白，经历可追溯，主动记忆要筛选。",
        ts_ms=1_800_000_300_000,
    )
    engine = MemoryDistillationEngine(
        episodic_store=episodic,
        ledger=ledger,
        life_memory_store=life_store,
    )

    result = engine.distill_top_k(
        query_text="记忆系统 生命经历 锚点",
        top_k=3,
        tags=["self_change"],
        ts_ms=1_800_000_300_100,
    )

    assert result.success is True
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["source_memory_ids"] == [f"life_episode::{episode['episode_id']}"]
    assert "life_consolidated" in items[0]["tags"]
    assert "semantic_kind:self_change" in items[0]["tags"]
    assert "自我改变" in items[0]["summary"]
    assert "生命经历锚点" in items[0]["summary"]


# ---------------------------------------------------------------------------
# T-Distill-04：list_semantic_memories 分页
# ---------------------------------------------------------------------------

def test_list_semantic_memories_pagination(engine, episodic, ledger):
    """写入多条后分页查询正确。"""
    # 写入 3 批不同的情节记忆，蒸馏 3 次
    for i in range(3):
        _write_memories(episodic, [f"记忆片段{i:03d}：内容描述{i}"])
        engine.distill_top_k(query_text=f"记忆{i:03d}", top_k=1, ts_ms=1000 + i * 100)

    # 全量查询
    items_all, total = ledger.list_semantic_memories(limit=10, offset=0)
    assert total == 3
    assert len(items_all) == 3

    # 分页：每页 2 条
    items_p1, _ = ledger.list_semantic_memories(limit=2, offset=0)
    items_p2, _ = ledger.list_semantic_memories(limit=2, offset=2)
    assert len(items_p1) == 2
    assert len(items_p2) == 1


# ---------------------------------------------------------------------------
# T-Distill-05：prompt_context semantic_slot 验证
# ---------------------------------------------------------------------------

def _make_valid_prompt_context(**overrides) -> dict:
    """构造最小合法 prompt_context。"""
    base = {
        "hard_snapshot": {},
        "bond_summary": {},
        "authoritative": [],
        "retrieval": [],
        "constraints": {},
        "tool_meta_pack": [],
    }
    base.update(overrides)
    return base


def test_prompt_context_semantic_slot_valid():
    """合法 semantic_slot 应通过验证。"""
    cp = _make_valid_prompt_context(
        semantic_slot=[
            {
                "slot": "semantic_slot",
                "semantic_id": "sem_abc123",
                "summary": "夏目贵志的语义摘要",
                "confidence": 0.85,
                "tags": ["主角"],
                "source_count": 3,
                "ts_ms": 1000,
            }
        ]
    )
    result = validate_prompt_context(cp)
    assert result.valid, f"应通过验证，但有违规: {result.violations}"


def test_prompt_context_semantic_slot_missing_required_field():
    """semantic_slot 条目缺少 semantic_id 或 summary 时应报错。"""
    cp = _make_valid_prompt_context(
        semantic_slot=[
            {"summary": "缺少 semantic_id 的条目"}  # 缺少 semantic_id
        ]
    )
    result = validate_prompt_context(cp)
    assert not result.valid
    codes = [v.code for v in result.violations]
    assert "PROMPT_CONTEXT_TYPE_INVALID" in codes


def test_prompt_context_without_semantic_slot_still_valid():
    """不包含 semantic_slot 的 prompt_context 应仍然通过验证（可选槽位）。"""
    cp = _make_valid_prompt_context()
    result = validate_prompt_context(cp)
    assert result.valid, f"不含 semantic_slot 应通过验证，但有违规: {result.violations}"


# ---------------------------------------------------------------------------
# T-Distill-06：build_semantic_slot
# ---------------------------------------------------------------------------

def test_build_semantic_slot(engine, episodic, ledger):
    """build_semantic_slot 应从 ledger 构建格式正确的 semantic_slot 列表。"""
    _write_memories(episodic, ["夏目贵志的故事"])
    engine.distill_top_k(query_text="夏目", top_k=1, ts_ms=1000)

    slot = build_semantic_slot(ledger=ledger, top_k=5)
    assert isinstance(slot, list)
    assert len(slot) == 1

    entry = slot[0]
    assert entry["slot"] == "semantic_slot"
    assert "semantic_id" in entry
    assert "summary" in entry
    assert "confidence" in entry
    assert isinstance(entry["tags"], list)
    assert isinstance(entry["source_count"], int)


def test_distill_top_k_concurrent_idempotent(tmp_path):
    """F-41: concurrent distillation with same source_hash should persist exactly once."""
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import MagicMock

    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from host_brain.memory.distillation import MemoryDistillationEngine

    ledger = SQLiteLedger(db_path=str(tmp_path / "concurrent.sqlite3"), check_same_thread=False)
    episodic = MagicMock()
    episodic.recall.return_value = [
        {"memory_id": "mem_a", "text": "A", "score": 0.9},
        {"memory_id": "mem_b", "text": "B", "score": 0.8},
    ]
    engine = MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)

    def _run_once() -> str | None:
        result = engine.distill_top_k(query_text="same query", top_k=2)
        assert result.success is True
        return result.semantic_id

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _idx: _run_once(), range(20)))

        items, total = ledger.list_semantic_memories(limit=10, offset=0)
        assert total == 1
        persisted_id = items[0]["semantic_id"]
        assert all(sid == persisted_id for sid in results)
    finally:
        ledger.close()
