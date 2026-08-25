from __future__ import annotations

from typing import Any

from host_brain.agent_runtime.contextual_answers import (
    _memory_recall_plan_should_bypass_direct_status_fastlane,
)
from host_brain.observability.runtime_audit import _normalize_retrieval_summary_for_audit
from host_brain.agent_runtime.fastpaths import _needs_selection_followup_retrieval_budget
from host_brain.persona.session_hooks import (
    _resolve_selection_followup_retrieval_timeout_seconds,
)


def test_memory_recall_plan_bypasses_recent_turn_fastlane_for_evidence_and_correction() -> None:
    assert _memory_recall_plan_should_bypass_direct_status_fastlane("你刚才是不是记错了封面猫那本？", task_mode="recent_turn")
    assert _memory_recall_plan_should_bypass_direct_status_fastlane("昨天那个网页内容靠谱吗？依据是什么？", task_mode="recent_turn")
    assert _memory_recall_plan_should_bypass_direct_status_fastlane("这三本书，你喜欢哪本？", task_mode="recent_turn")
    assert _memory_recall_plan_should_bypass_direct_status_fastlane("记忆系统还有什么待办没完成？", task_mode="task_progress")
    assert not _memory_recall_plan_should_bypass_direct_status_fastlane("你刚刚说了什么", task_mode="recent_turn")


def _surface_memory_summary(
    *,
    contract_id: str,
    query_kind: str,
    evidence_depth: str = "evidence_anchor",
    response_mode: str = "answer_from_collection_memory",
    detail_required: bool = False,
    facts: list[str] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "OK",
        "surface_memory_recall_packet": {
            "facts": list(facts or []),
            "candidate_count": 1,
            "degraded": False,
            "reason_code": None,
            "trace": {
                "selected_collection_items": list(items or []),
                "recall_answer_readiness": {
                    "schema": "Natsume.surface_recall_answer_readiness.v1",
                    "status": "READY",
                    "ready_to_answer": True,
                    "response_mode": response_mode,
                    "surface_action": "answer",
                    "reasons": [],
                    "need_type": "collection",
                    "recall_contract_id": contract_id,
                    "query_kind": query_kind,
                    "temporal_scope": "yesterday",
                    "evidence_depth": evidence_depth,
                    "coverage_score": 1.0,
                    "coverage_passed": True,
                    "validation_passed": True,
                    "validation_failed": False,
                    "coverage_failed": False,
                    "selected_item_count": len(items or []),
                    "detail_evidence": {
                        "required": detail_required,
                        "ready_for_deep_review": detail_required,
                        "basis": "available",
                        "body_item_count": 0,
                        "anchor_item_count": len(items or []),
                        "structured_item_count": len(items or []),
                    },
                },
            },
        },
    }


def test_selection_followup_retrieval_budget_detects_book_choice(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_SELECTION_FOLLOWUP_RETRIEVAL_TIMEOUT_MS", "42000")

    assert _needs_selection_followup_retrieval_budget("这三本书，你喜欢哪本？")
    assert _needs_selection_followup_retrieval_budget("这几本轻小说哪本好看")
    assert _needs_selection_followup_retrieval_budget("昨天那几本好看吗")
    assert _needs_selection_followup_retrieval_budget("前天那几本你喜欢哪本")
    assert _needs_selection_followup_retrieval_budget("这几天十几本里哪本最好看")
    assert _needs_selection_followup_retrieval_budget("我问你喜欢哪本")
    assert _needs_selection_followup_retrieval_budget("这些推荐哪本值得看")
    assert not _needs_selection_followup_retrieval_budget("今天吃什么？")
    assert _resolve_selection_followup_retrieval_timeout_seconds() == 42.0


def test_normalize_retrieval_summary_keeps_safe_surface_memory_trace() -> None:
    summary = _normalize_retrieval_summary_for_audit(
        {
            "status": "OK",
            "candidate_count": 1,
            "route_count": 4,
            "surface_memory_recall_packet": {
                "facts": ["网页深读结果（含正文）：..."],
                "trace": {
                    "plan": {
                        "reason": "anaphoric_selection_recall",
                        "task_mode": "daily_chat",
                        "recall_policy": "MUST_RECALL",
                        "max_facts": 3,
                        "target_store_ids": ["life_episode", "semantic_memory"],
                    },
                    "recall_decision": {
                        "schema": "Natsume.surface_recall_decision.v1",
                        "decision": "recall",
                        "invoked": True,
                        "recall_policy": "MUST_RECALL",
                        "reason": "anaphoric_selection_recall",
                        "task_mode": "daily_chat",
                        "query_kind": "anaphoric_selection",
                        "temporal_scope": "current",
                        "recall_contract_id": "anaphoric_selection",
                        "need_type": "collection_selection",
                        "primary_memory_layer": "life_memory_collection",
                        "expected_memory_layers": ["life_memory_collection", "life_episode"],
                        "target_store_ids": ["life_memory_collection", "life_episode"],
                        "target_domains": ["lightnovel_recommendation"],
                        "validation_requirements": ["collection_or_episode_hit", "evidence_refs_present"],
                        "answer_obligations": ["ground_selection_in_recalled_items"],
                        "evidence_depth": "detail_or_body_evidence",
                        "min_collection_items": 3,
                        "time_lookback_days": 1,
                        "max_facts": 5,
                        "selection_basis": ["policy:MUST_RECALL", "reason:anaphoric_selection_recall"],
                    },
                    "route_count": 4,
                    "recall_need": {
                        "schema": "Natsume.surface_recall_need.v1",
                        "query_kind": "anaphoric_selection",
                        "temporal_scope": "current",
                        "recall_contract_id": "anaphoric_selection",
                        "need_type": "collection_selection",
                        "primary_memory_layer": "life_memory_collection",
                        "expected_memory_layers": ["life_memory_collection", "life_episode"],
                        "target_domains": ["lightnovel_recommendation"],
                        "min_collection_items": 3,
                        "evidence_depth": "detail_or_body_evidence",
                    },
                    "recall_coverage": {
                        "score": 1.0,
                        "passed": True,
                        "missing": [],
                        "dimensions": {"validation": 1.0, "item_count": 1.0, "evidence": 1.0},
                        "required_count": 5,
                        "passed_required_count": 5,
                        "selected_item_count": 3,
                        "min_collection_items": 3,
                        "evidence_ref_count": 3,
                        "source_ref_count": 1,
                        "detail_evidence": {
                            "required": True,
                            "ready_for_deep_review": True,
                            "basis": "body_evidence",
                            "body_item_count": 1,
                            "anchor_item_count": 3,
                            "structured_item_count": 3,
                            "detail_refs": ["https://www.lightnovel.fun/cn/detail/1"],
                        },
                    },
                    "recall_answer_readiness": {
                        "schema": "Natsume.surface_recall_answer_readiness.v1",
                        "status": "READY",
                        "ready_to_answer": True,
                        "response_mode": "answer_from_collection_memory",
                        "surface_action": "answer",
                        "reasons": [],
                        "need_type": "collection_selection",
                        "recall_contract_id": "anaphoric_selection",
                        "query_kind": "anaphoric_selection",
                        "temporal_scope": "current",
                        "evidence_depth": "detail_or_body_evidence",
                        "coverage_score": 1.0,
                        "coverage_passed": True,
                        "validation_passed": True,
                        "validation_failed": False,
                        "coverage_failed": False,
                        "missing": [],
                        "selected_fact_count": 3,
                        "selected_user_fact_count": 3,
                        "selected_bond_count": 0,
                        "selected_item_count": 3,
                        "min_collection_items": 3,
                        "required_count": 5,
                        "passed_required_count": 5,
                        "detail_evidence": {
                            "required": True,
                            "ready_for_deep_review": True,
                            "basis": "body_evidence",
                            "body_item_count": 1,
                            "anchor_item_count": 3,
                            "structured_item_count": 3,
                            "detail_refs": ["https://www.lightnovel.fun/cn/detail/1"],
                        },
                    },
                    "lightnovel_deep_review": {
                        "status": "OK",
                        "reason_code": "LIGHTNOVEL_DEEP_REVIEW_CACHE_HIT",
                        "selected": {"index": 3, "title": "异世界皇女"},
                        "item_count": 3,
                        "latency_ms": 27,
                        "cached": True,
                        "source": "cache",
                        "recommendation_signature": "sig-1",
                        "source_episode_id": "ep-cache",
                        "persist_status": "",
                        "evidence_ref_count": 3,
                    },
                    "coverage_failed_resolved_by_generic_evidence_review": True,
                    "generic_evidence_review": {
                        "status": "OK",
                        "reason_code": "GENERIC_EVIDENCE_REVIEW_OK",
                        "item_count": 1,
                        "latency_ms": 11,
                        "cached": False,
                        "source": "fetch",
                        "persisted_episode_id": "ep-generic-review",
                        "persist_status": "OK",
                        "evidence_ref_count": 1,
                    },
                    "collection_fastpath": {
                        "attempted": True,
                        "status": "HIT",
                        "latency_ms": 3,
                        "candidate_count": 1,
                    },
                },
            },
        }
    )

    trace = summary["surface_memory_trace"]
    assert trace["plan"]["reason"] == "anaphoric_selection_recall"
    assert trace["route_count"] == 4
    assert trace["recall_decision"]["schema"] == "Natsume.surface_recall_decision.v1"
    assert trace["recall_decision"]["decision"] == "recall"
    assert trace["recall_decision"]["need_type"] == "collection_selection"
    assert trace["recall_decision"]["target_store_ids"] == ["life_memory_collection", "life_episode"]
    assert "policy:MUST_RECALL" in trace["recall_decision"]["selection_basis"]
    assert trace["recall_need"]["need_type"] == "collection_selection"
    assert trace["recall_need"]["evidence_depth"] == "detail_or_body_evidence"
    assert trace["recall_coverage"]["score"] == 1.0
    assert trace["recall_coverage"]["selected_item_count"] == 3
    assert trace["recall_coverage"]["detail_evidence"] == {
        "required": True,
        "ready_for_deep_review": True,
        "basis": "body_evidence",
        "body_item_count": 1,
        "anchor_item_count": 3,
        "structured_item_count": 3,
    }
    assert trace["coverage_score"] == 1.0
    assert trace["recall_answer_readiness"]["schema"] == "Natsume.surface_recall_answer_readiness.v1"
    assert trace["recall_answer_readiness"]["status"] == "READY"
    assert trace["recall_answer_readiness"]["ready_to_answer"] is True
    assert trace["recall_answer_readiness"]["response_mode"] == "answer_from_collection_memory"
    assert trace["recall_answer_readiness"]["selected_item_count"] == 3
    assert trace["recall_answer_readiness"]["detail_evidence"] == {
        "required": True,
        "ready_for_deep_review": True,
        "basis": "body_evidence",
        "body_item_count": 1,
        "anchor_item_count": 3,
        "structured_item_count": 3,
    }
    assert trace["lightnovel_deep_review"]["cached"] is True
    assert trace["lightnovel_deep_review"]["source_episode_id"] == "ep-cache"
    assert trace["lightnovel_deep_review"]["selected"] == {"index": 3, "title": "异世界皇女"}
    assert trace["coverage_failed_resolved_by_generic_evidence_review"] is True
    assert trace["generic_evidence_review"] == {
        "status": "OK",
        "reason_code": "GENERIC_EVIDENCE_REVIEW_OK",
        "item_count": 1,
        "latency_ms": 11,
        "cached": False,
        "source": "fetch",
        "persisted_episode_id": "ep-generic-review",
        "persist_status": "OK",
        "evidence_ref_count": 1,
    }
    assert trace["collection_fastpath"] == {
        "attempted": True,
        "status": "HIT",
        "latency_ms": 3,
        "candidate_count": 1,
    }
    assert "facts" not in trace
