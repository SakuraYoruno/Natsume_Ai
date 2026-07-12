from __future__ import annotations

from typing import Any

from host_brain.app.agent_runtime_shared import (
    _detect_recent_turn_query_role,
    _maybe_build_contextual_memory_answer,
    _maybe_build_contextual_session_answer,
    _maybe_build_contextual_style_answer,
    _memory_recall_plan_should_bypass_direct_status_fastlane,
    _normalize_retrieval_summary_for_audit,
    _needs_selection_followup_retrieval_budget,
    _resolve_selection_followup_retrieval_timeout_seconds,
    _maybe_build_recent_turn_answer,
    _run_surface_persona_session_end_sync,
)


def test_recent_turn_query_role_handles_where_we_left_off() -> None:
    assert _detect_recent_turn_query_role("刚才说到哪了") == "both"
    assert _detect_recent_turn_query_role("刚刚聊到哪了") == "both"
    assert _detect_recent_turn_query_role("你刚刚说了什么") == "assistant"


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


def test_recent_turn_answer_builds_direct_recap_and_skips_current_query(monkeypatch) -> None:
    monkeypatch.setenv("HOST_RECENT_TURN_DIRECT_ANSWER_ENABLED", "true")

    answer = _maybe_build_recent_turn_answer(
        request_id="req-recent-direct",
        session_id="sess-recent-direct",
        person_id="user-1",
        user_message="刚才说到哪了",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "这次主链效果怎么样，短句回答"},
            {"role": "assistant", "content": "当前任务状态为 RUNNING。"},
            {"role": "user", "content": "刚才说到哪了"},
        ],
    )

    assert answer == "你刚才说：「这次主链效果怎么样，短句回答」；我回：「当前任务状态为 RUNNING。」。"


def test_recent_turn_answer_handles_assistant_just_said_query(monkeypatch) -> None:
    monkeypatch.setenv("HOST_RECENT_TURN_DIRECT_ANSWER_ENABLED", "true")

    answer = _maybe_build_recent_turn_answer(
        request_id="req-recent-assistant-just-said",
        session_id="sess-recent-assistant-just-said",
        person_id="user-1",
        user_message="你刚刚说了什么",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "我刚刚在做路由边界测试。"},
            {"role": "assistant", "content": "收到，我继续看路由边界。"},
        ],
    )

    assert answer == "我上一句回的是：「收到，我继续看路由边界。」。"


def test_recent_turn_answer_skips_low_info_turns_for_where_we_left_off(monkeypatch) -> None:
    monkeypatch.setenv("HOST_RECENT_TURN_DIRECT_ANSWER_ENABLED", "true")

    answer = _maybe_build_recent_turn_answer(
        request_id="req-recent-skip-low-info",
        session_id="sess-recent-skip-low-info",
        person_id="user-1",
        user_message="刚才说到哪了",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "这次主链效果怎么样，短句回答"},
            {"role": "assistant", "content": "当前任务状态为 RUNNING。"},
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "接着来。"},
        ],
    )

    assert answer == "你刚才说：「这次主链效果怎么样，短句回答」；我回：「当前任务状态为 RUNNING。」。"


def test_recent_turn_answer_handles_only_low_info_context_without_model(monkeypatch) -> None:
    monkeypatch.setenv("HOST_RECENT_TURN_DIRECT_ANSWER_ENABLED", "true")

    answer = _maybe_build_recent_turn_answer(
        request_id="req-recent-only-low-info",
        session_id="sess-recent-only-low-info",
        person_id="user-1",
        user_message="刚才说到哪了",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "接着来。"},
        ],
    )

    assert answer == "刚才只是短接了一句，还没有新的实质内容。"


def test_contextual_memory_answer_recalls_recent_quoted_token() -> None:
    answer = _maybe_build_contextual_memory_answer(
        request_id="req-context-token",
        session_id="sess-context-token",
        person_id="user-1",
        user_message="我刚才让你记住的临时口令是什么？请只回答口令本身。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "请记住一个临时口令「薄荷月亮」，只需要简短确认。"},
            {"role": "assistant", "content": "嗯，记住了，薄荷月亮。"},
            {"role": "user", "content": "我刚才让你记住的临时口令是什么？请只回答口令本身。"},
            {"role": "assistant", "content": "薄荷月亮"},
        ],
    )

    assert answer == "薄荷月亮"


def test_contextual_memory_answer_leaves_token_transform_to_model() -> None:
    answer = _maybe_build_contextual_memory_answer(
        request_id="req-context-token-transform",
        session_id="sess-context-token",
        person_id="user-1",
        user_message="把这个口令变成一句 12 字以内的短句，仍然要包含它。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "请记住一个临时口令「薄荷月亮」，只需要简短确认。"},
            {"role": "assistant", "content": "嗯，记住了，薄荷月亮。"},
        ],
    )

    assert answer is None


def _style_turns() -> list[dict[str, str]]:
    return [
        {"role": "user", "content": "我喜欢偏冷色、少废话但要有一点温度的表达。"},
        {"role": "assistant", "content": "行，就这么定。"},
        {"role": "user", "content": "我不喜欢太客服腔，把刚才那个名字里的风格说得更像你自己。"},
    ]


def test_contextual_style_answer_leaves_style_naming_to_model() -> None:
    answer = _maybe_build_contextual_style_answer(
        request_id="req-style-name",
        session_id="sess-style",
        person_id="user-1",
        user_message="给这个表达风格起一个临时名字，别超过8个字。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: _style_turns(),
    )

    assert answer is None


def test_contextual_style_answer_does_not_render_expressive_summary_directly() -> None:
    answer = _maybe_build_contextual_style_answer(
        request_id="req-style-summary",
        session_id="sess-style",
        person_id="user-1",
        user_message="总结一下刚才我明确要求你记住的表达偏好，只列要点。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: _style_turns(),
    )

    assert answer is None


def test_contextual_style_answer_does_not_render_comfort_directly() -> None:
    answer = _maybe_build_contextual_style_answer(
        request_id="req-style-comfort",
        session_id="sess-style",
        person_id="user-1",
        user_message="我今天有点累。按刚才那个表达风格回一句。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: _style_turns(),
    )

    assert answer is None


def test_contextual_style_answer_leaves_update_diff_to_model_without_strict_answer() -> None:
    answer = _maybe_build_contextual_style_answer(
        request_id="req-style-update",
        session_id="sess-style",
        person_id="user-1",
        user_message="现在新版表达偏好和旧版哪里不同？别超过两句。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            *_style_turns(),
            {"role": "user", "content": "不是完全冷，应该是偏青色、有余温。"},
        ],
    )

    assert answer is None


def test_contextual_style_answer_leaves_seed_ack_to_model_without_strict_answer() -> None:
    answer = _maybe_build_contextual_style_answer(
        request_id="req-style-seed",
        session_id="sess-style",
        person_id="user-1",
        user_message="建立表达偏好：偏冷色、少废话、保留一点温度、别太客服腔。简短确认。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_style_answer_leaves_updated_style_application_to_model() -> None:
    answer = _maybe_build_contextual_style_answer(
        request_id="req-style-apply-updated",
        session_id="sess-style",
        person_id="user-1",
        user_message="按我们新版表达风格，把上一句换成更像你自己的说法。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            *_style_turns(),
            {"role": "user", "content": "不是完全冷，应该是偏青色、有余温。"},
            {"role": "user", "content": "如果我今晚只想收尾，不想开新坑，你会怎么接？"},
        ],
    )

    assert answer is None


def test_contextual_memory_answer_handles_anchor_and_alias() -> None:
    turns = [
        {"role": "user", "content": "请记住一个临时锚点「橙灯码」，以后我说那个锚点就是它。"},
        {"role": "assistant", "content": "橙灯码，记下了。"},
        {"role": "user", "content": "给锚点加个别名：以后「小橙灯」也指橙灯码。"},
    ]

    anchor = _maybe_build_contextual_memory_answer(
        request_id="req-anchor",
        session_id="sess-anchor",
        person_id="user-1",
        user_message="那个临时锚点还记得吗？只回答锚点。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: turns,
    )
    alias = _maybe_build_contextual_memory_answer(
        request_id="req-anchor-alias",
        session_id="sess-anchor",
        person_id="user-1",
        user_message="小橙灯指什么？只回答。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: turns,
    )

    assert anchor == "橙灯码"
    assert alias == "橙灯码"


def test_contextual_memory_answer_ignores_report_preference_when_recalling_anchor() -> None:
    answer = _maybe_build_contextual_memory_answer(
        request_id="req-anchor-after-pref",
        session_id="sess-anchor-after-pref",
        person_id="user-1",
        user_message="那个临时锚点还记得吗？只回答锚点。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "请记住一个临时锚点「橙灯码」，以后我说那个锚点就是它。"},
            {"role": "user", "content": "记住一个偏好：做系统测试时，我更相信可复现报告，不相信只说体感。"},
            {"role": "user", "content": "再修正：报告要可复现，但也要保留一点主观体验备注。"},
        ],
    )

    assert answer == "橙灯码"


def test_contextual_session_answer_leaves_anchor_write_ack_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-anchor-write",
        session_id="sess-anchor-write",
        person_id="user-1",
        user_message="请记住一个临时锚点「橙灯码」，以后我说那个锚点就是它。只确认。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_session_answer_leaves_report_preference_recall_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-report-pref",
        session_id="sess-report-pref",
        person_id="user-1",
        user_message="现在我对测试报告的要求是什么？不要漏掉刚才修正。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "记住一个偏好：做系统测试时，我更相信可复现报告，不相信只说体感。"},
            {"role": "user", "content": "再修正：报告要可复现，但也要保留一点主观体验备注，不然太干。"},
        ],
    )

    assert answer is None


def test_contextual_session_answer_leaves_report_preference_update_ack_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-report-pref-update",
        session_id="sess-report-pref",
        person_id="user-1",
        user_message="再修正：报告要可复现，但也要保留一点主观体验备注，不然太干。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_session_answer_leaves_key_commitment_summary_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-session-summary",
        session_id="sess-session-summary",
        person_id="user-1",
        user_message="把目前三条关键约定合并成一个短列表：表达风格、锚点、测试报告偏好。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [
            {"role": "user", "content": "偏冷色、少废话、保留一点温度、别太客服腔。"},
            {"role": "user", "content": "不是完全冷，应该是偏青色、有余温。"},
            {"role": "user", "content": "请记住一个临时锚点「橙灯码」。"},
            {"role": "user", "content": "给锚点加个别名：以后「小橙灯」也指橙灯码。"},
            {"role": "user", "content": "报告要可复现，但也要保留一点主观体验备注。"},
        ],
    )

    assert answer is None


def test_contextual_session_answer_leaves_anchor_alias_ack_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-anchor-alias-write",
        session_id="sess-session",
        person_id="user-1",
        user_message="给锚点加个别名：以后「小橙灯」也指橙灯码。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_session_answer_leaves_long_dialogue_metrics_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-long-metrics",
        session_id="sess-session",
        person_id="user-1",
        user_message="如果长对话要测连续性，你觉得最该看哪三类指标？简短说。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_session_answer_does_not_capture_style_seed_as_metrics() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-style-seed-not-metrics",
        session_id="sess-session",
        person_id="user-1",
        user_message="长对话连续性测试01：建立表达偏好。偏冷色、少废话、保留一点温度。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_session_answer_leaves_workbench_color_to_model() -> None:
    answer = _maybe_build_contextual_session_answer(
        request_id="req-workbench-color",
        session_id="sess-session",
        person_id="user-1",
        user_message="如果给今晚的工作台一个颜色，你会选什么？",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert answer is None


def test_contextual_session_answer_leaves_stabilize_and_wrapup_to_model() -> None:
    stabilize = _maybe_build_contextual_session_answer(
        request_id="req-stabilize",
        session_id="sess-session",
        person_id="user-1",
        user_message="好，先不测了。你现在用一句很短的话把场子稳住。",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )
    wrap = _maybe_build_contextual_session_answer(
        request_id="req-wrap",
        session_id="sess-session",
        person_id="user-1",
        user_message="插一句无关的：如果我今晚只想收尾，不想开新坑，你会怎么接？",
        render_target_lang="zh",
        load_continuity_turns=lambda **_kwargs: [],
    )

    assert stabilize is None
    assert wrap is None


def test_surface_session_end_uses_canonical_user_and_forwards_emotion(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "canon-user")
    calls: list[dict[str, Any]] = []

    class _FakeLimbic:
        def on_session_end(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(dict(kwargs))
            return {
                "continuity_score": 0.7,
                "anticipation_topics": list(kwargs.get("pending_topics") or []),
                "inner_stream_size": 3,
            }

    monkeypatch.setattr("host_brain.tribe.limbic_agent.get_limbic_agent", lambda: _FakeLimbic())

    result = _run_surface_persona_session_end_sync(
        request_id="req-1",
        person_id="external-user",
        session_id="sess-1",
        user_message="我有点难受",
        emotion_context={"surface_user_emotion_tag": "SAD", "emotion_intensity": 0.75},
    )

    assert calls
    assert calls[0]["person_id"] == "canon-user"
    assert calls[0]["pending_topics"] == ["我有点难受"]
    assert calls[0]["valence"] < 0
    assert calls[0]["arousal"] >= 0.75
    assert result["status"] == "OK"
    assert result["person_id"] == "canon-user"
    assert result["emotion_tag"] == "SAD"
    assert result["inner_world"]["anticipation_topics"] == ["我有点难受"]

