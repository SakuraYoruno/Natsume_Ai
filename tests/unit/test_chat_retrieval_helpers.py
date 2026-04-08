from host_brain.app.chat_retrieval_helpers import (
    filter_retrieval_evidence_items_for_prompt,
)


def test_filter_retrieval_evidence_items_for_prompt_drops_contextual_followup_noise() -> None:
    filtered, meta = filter_retrieval_evidence_items_for_prompt(
        user_message="什么意思",
        retrieval_summary={
            "query_plan": {"intent": "chat", "original_query": "什么意思"},
            "agentic_meta": {
                "final_evaluation": {
                    "sufficient": False,
                    "top_score": 0.02,
                    "confidence": 0.55,
                    "coverage_score": 0.0,
                }
            },
        },
        retrieval_evidence_items=[
            {"store_id": "episodic_vector", "snippet": "这个核心集成测试标记 1234", "score": 0.02},
            {"store_id": "bond_graph", "snippet": "当前关系阶段：ACQUAINTANCE"},
        ],
    )

    assert meta["applied"] is True
    assert meta["mode"] == "contextual_followup"
    assert len(filtered) == 1
    assert filtered[0]["store_id"] == "bond_graph"


def test_filter_retrieval_evidence_items_for_prompt_trims_low_signal_short_chat() -> None:
    filtered, meta = filter_retrieval_evidence_items_for_prompt(
        user_message="很高兴",
        retrieval_summary={
            "query_plan": {"intent": "chat", "original_query": "很高兴"},
            "agentic_meta": {
                "final_evaluation": {
                    "sufficient": False,
                    "top_score": 0.03,
                    "confidence": 0.4,
                    "coverage_score": 0.0,
                }
            },
        },
        retrieval_evidence_items=[
            {"store_id": "episodic_vector", "snippet": "无关测试记忆", "score": 0.03},
            {"store_id": "episodic_vector", "snippet": "更像当前语境", "score": 0.22},
            {"store_id": "bond_graph", "snippet": "当前关系阶段：ACQUAINTANCE"},
        ],
    )

    assert meta["applied"] is True
    assert meta["mode"] == "low_signal_short_chat"
    assert [item["snippet"] for item in filtered] == ["更像当前语境", "当前关系阶段：ACQUAINTANCE"]


def test_filter_retrieval_evidence_items_for_prompt_keeps_normal_chat_evidence() -> None:
    items = [
        {"store_id": "episodic_vector", "snippet": "项目上下文", "score": 0.31},
        {"store_id": "bond_graph", "snippet": "当前关系阶段：FRIEND"},
    ]
    filtered, meta = filter_retrieval_evidence_items_for_prompt(
        user_message="我们继续刚才的数字生命方案",
        retrieval_summary={
            "query_plan": {"intent": "complex", "original_query": "我们继续刚才的数字生命方案"},
        },
        retrieval_evidence_items=items,
    )

    assert meta["applied"] is False
    assert filtered == items
