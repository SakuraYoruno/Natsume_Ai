from __future__ import annotations

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
