from types import SimpleNamespace

from host_brain.app.agent_runtime_shared import _memory_recall_plan_should_bypass_direct_status_fastlane
from host_brain.app.executive_runtime_core import _message_requests_memory_recall
from host_brain.app.surface_main_chain_adapter import _surface_memory_acceptance_must_include
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import (
    RECALL_POLICY_MUST,
    build_surface_memory_recall_packet,
    plan_surface_memory_recall,
)
from host_brain.retrieval.recall_fusion_stores import search_semantic_memory
from host_brain.persona.text_quality_gate import get_text_quality_gate
from host_brain.tribe.thalamus_agent import classify_task_intent_by_rules


def test_project_fact_seed_does_not_route_to_open_app() -> None:
    decision = classify_task_intent_by_rules(
        "再补一个项目事实：app-server预热指标是prewarm_latency_ms=665，Host启动时会预热7个线程。"
    )

    assert decision is not None
    assert decision["route"] == "skill"
    assert decision["target_id"] == "memory_store"


def test_memory_acceptance_bypasses_status_fastlane() -> None:
    message = "最终同会话验收：请列出当前测试代号、海边灯塔到底指什么、app-server预热指标、轻小说推荐格式。"

    assert _message_requests_memory_recall(message)
    assert _memory_recall_plan_should_bypass_direct_status_fastlane(message, task_mode="task_progress")


def test_surface_memory_plan_recalls_for_acceptance_query() -> None:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message="最终同会话验收：请列出当前测试代号、海边灯塔到底指什么、app-server预热指标、轻小说推荐格式。",
            task_mode_hint="daily_chat",
        )
    )

    assert plan.should_recall
    assert plan.recall_policy == RECALL_POLICY_MUST
    assert plan.reason == "explicit_memory_recall"


def test_explicit_memory_ranking_prefers_user_fact_over_assistant_echo() -> None:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(
            output={
                "query": "新会话验收：请复述测试代号、海边灯塔、app-server预热指标、轻小说推荐格式。",
                "status": "OK",
                "candidate_count": 2,
                "candidates": [
                    {
                        "snippet": "Assistant: 测试代号是最终同会话验收，海边灯塔就是字面意思的灯塔。",
                        "confidence": 0.99,
                        "metadata": {
                            "store_id": "life_episode",
                            "evidence_refs_primary": "evd://life/assistant_reply/wrong-final",
                        },
                    },
                    {
                        "snippet": (
                            "User: 项目事实：测试代号修正为蓝莲-0531-R2；海边灯塔指连续记忆锚点；"
                            "app-server预热指标是prewarm_latency_ms=665、prewarm_threads=7；"
                            "轻小说推荐格式是书名+一句话理由。"
                        ),
                        "confidence": 0.72,
                        "metadata": {
                            "store_id": "life_episode",
                            "evidence_refs_primary": "gateway://message/user-fact",
                        },
                    },
                ],
            }
        ),
        query="新会话验收：请复述测试代号、海边灯塔、app-server预热指标、轻小说推荐格式。",
        max_facts=1,
    )

    assert packet.facts
    assert "蓝莲-0531-R2" in packet.facts[0]
    assert "连续记忆锚点" in packet.facts[0]
    assert "字面意思的灯塔" not in packet.facts[0]


def test_text_quality_gate_blocks_runtime_leak_outputs() -> None:
    gate = get_text_quality_gate()

    for bad_text in (
        "NoneNoneNoneNoneNoneNoneNoneNone",
        "当前输入渠道：中文文字私聊。",
        "当前任务 cortex_orchestrator 状态为 RUNNING，已运行 35s。",
    ):
        result = gate.validate(text=bad_text, render_profile="zh_text", response_form="text")
        assert result.hard_fail
        assert not result.accepted


def test_semantic_memory_recall_prefers_same_person_distilled_correction(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=str(tmp_path / "semantic_person_scope.sqlite3"))
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_chat_episode(
            request_id="req-old-code",
            session_id="sess-old",
            person_id="person-old",
            user_message="纠正测试代号：蓝莲-0531-R1 改成蓝莲-0531-R2。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_400_000,
        )
        store.write_chat_episode(
            request_id="req-old-acceptance-noise",
            session_id="sess-old",
            person_id="person-old",
            user_message="最终同会话验收：请列出当前测试代号、海边灯塔到底指什么、app-server预热指标、轻小说推荐格式。",
            assistant_reply="测试代号蓝莲-0531-R2。",
            ts_ms=1_800_000_400_050,
        )
        store.write_chat_episode(
            request_id="req-new-code",
            session_id="sess-new",
            person_id="person-new",
            user_message="纠正测试代号：蓝莲-0531-D1 改成蓝莲-0531-D2。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_400_100,
        )
        LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=20,
            ts_ms=1_800_000_400_200,
        )

        rows = search_semantic_memory(
            SimpleNamespace(ledger=ledger),
            query="跨会话蒸馏验收：请列出当前测试代号、海边灯塔含义、app-server预热指标、轻小说推荐格式。",
            top_k=2,
            person_id="person-new",
            session_id="sess-recall",
            now_ts_ms=1_800_000_400_300,
        )

        assert rows
        assert "蓝莲-0531-D2" in rows[0]["text"]
        assert rows[0]["metadata"]["score_boost"]["identity_boost"] > 0
    finally:
        ledger.close()


def test_memory_acceptance_must_include_uses_latest_distilled_test_code() -> None:
    agent_input = SimpleNamespace(
        user_message="跨会话蒸馏最终验收：请列出当前测试代号、海边灯塔含义、app-server预热指标、轻小说推荐格式。",
        trace_stage_meta={
            "surface_memory_recall_packet": {
                "facts": [
                    "长期事实：稳定事实：当前测试代号是蓝莲-0531-D3",
                    "长期事实：纠错记忆：纠正测试代号：蓝莲-0531-D3 改成蓝莲-0531-D4",
                    "长期事实：稳定事实：海边灯塔指连续记忆锚点，不是字面灯塔",
                    "长期事实：稳定事实：app-server预热指标是prewarm_latency_ms=665、prewarm_threads=7、prewarm_workers=1",
                    "长期事实：轻小说推荐格式：书名 + 一句话理由，不要卡片模板",
                ]
            }
        },
    )

    required = _surface_memory_acceptance_must_include(agent_input)

    assert "蓝莲-0531-D4" in required
    assert "蓝莲-0531-D3" not in required
    assert "连续记忆锚点" in required
    assert "prewarm_latency_ms=665" in required
    assert "书名 + 一句话理由" in required
