from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
from host_brain.persona.surface_memory_recall import build_surface_memory_recall_packet
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig


class _SemanticLedger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def list_semantic_memories(self, *, limit: int, offset: int):  # noqa: ANN201
        assert limit > 0
        assert offset == 0
        return (
            [
                {
                    "semantic_id": "sem-low-energy",
                    "summary": "用户低能量时偏好少追问，先给一个小步骤。",
                    "source_memory_ids": ["mem-1", "mem-2"],
                    "distill_strategy": "llm_summary",
                    "confidence": 0.92,
                    "tags": ["preference", "low_energy"],
                    "ts_ms": 1000,
                },
                {
                    "semantic_id": "sem-unrelated",
                    "summary": "用户喜欢芒果和不太甜的奶茶。",
                    "source_memory_ids": ["mem-3"],
                    "confidence": 0.88,
                    "tags": ["preference"],
                    "ts_ms": 900,
                },
            ],
            2,
        )

    def log_retrieval_event(self, row: dict) -> None:
        self.events.append(dict(row))


def test_recall_fusion_reads_semantic_memory_as_long_term_route() -> None:
    ledger = _SemanticLedger()
    runtime_config = RecallRuntimeConfig(
        enabled=True,
        default_route="semantic",
        routes={
            "semantic": RecallRouteConfig(
                route_id="semantic",
                store_id="semantic_memory",
                collection="semantic_memory",
                top_k=3,
                score_weight=1.0,
                node_selector="surface_main_chain_memory_recall",
            )
        },
    )
    service = RecallFusionServiceContract(
        ledger=ledger,  # type: ignore[arg-type]
        runtime_config=runtime_config,
        node_id="surface_main_chain_memory_recall",
        use_hybrid=False,
    )

    result = service.recall(
        request_id="req-semantic",
        query="我有点累，下一步怎么办",
        query_embedding=[0.1] * 8,
        target_store_ids=["semantic_memory"],
        ts_ms=2_000,
    )

    assert result.success
    assert result.output["routes"][0]["store_id"] == "semantic_memory"
    assert result.output["candidates"][0]["doc_id"] == "sem-low-energy"
    assert result.output["candidates"][0]["metadata"]["memory_layer"] == "semantic_long_term"
    assert result.output["candidates"][0]["source_ref"] == "ledger://semantic_memory/sem-low-energy"
    assert ledger.events


def test_life_episode_semantic_distillation_reaches_surface_memory_packet() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written_episode = store.write_chat_episode(
            request_id="req-semantic-life-pref",
            session_id="qq_dm_user_1",
            person_id="user-1",
            user_message="以后轻小说推荐按总览一段、每本书一卡片分段，简介后要换行。",
            assistant_reply="记住了，之后推荐会按这个格式发。",
            ts_ms=1_800_000_000_000,
            metadata={"user_importance": 0.7},
        )

        distill_result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=12,
            max_records=6,
            ts_ms=1_800_000_000_100,
        )
        assert distill_result["written_count"] == 1

        runtime_config = RecallRuntimeConfig(
            enabled=True,
            default_route="semantic",
            routes={
                "semantic": RecallRouteConfig(
                    route_id="semantic",
                    store_id="semantic_memory",
                    collection="semantic_memory",
                    top_k=3,
                    score_weight=1.0,
                    node_selector="surface_main_chain_memory_recall",
                )
            },
        )
        service = RecallFusionServiceContract(
            ledger=ledger,
            runtime_config=runtime_config,
            node_id="surface_main_chain_memory_recall",
            use_hybrid=False,
        )

        recall_result = service.recall(
            request_id="req-semantic-life-recall",
            query="以后轻小说推荐怎么发",
            query_embedding=[0.2] * 8,
            target_store_ids=["semantic_memory"],
            ts_ms=1_800_000_000_200,
        )
        packet = build_surface_memory_recall_packet(recall_result, max_facts=3)

        assert recall_result.success
        assert recall_result.output["candidates"]
        assert recall_result.output["candidates"][0]["metadata"]["source_memory_ids"] == [
            f"life_episode::{written_episode['episode_id']}"
        ]
        rendered = "\n".join(packet.facts)
        assert "长期记忆：用户偏好" in rendered
        assert "总览" in rendered
        assert "卡片" in rendered
        assert "换行" in rendered
        assert "长期记忆只补稳定偏好和关系事实，不替代当前用户句子" in packet.style_anchor
    finally:
        ledger.close()
