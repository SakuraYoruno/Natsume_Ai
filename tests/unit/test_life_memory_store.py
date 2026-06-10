from __future__ import annotations

import json
import hashlib
import sqlite3

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import (
    EVIDENCE_REFLECTION,
    EPISODE_ACTION,
    EPISODE_PERCEPTION,
    EPISODE_REFLECTION,
    LifeMemoryConsolidator,
    MemorySleepReplayTask,
    LifeMemoryStore,
    RETENTION_COLD,
    RETENTION_GONE,
    RETENTION_WARM,
    TRUTH_CORRECTED,
    TRUTH_NARRATIVE_ONLY,
    TRUTH_SUPPRESSED,
)


def test_same_chat_request_creates_one_life_episode_with_many_evidence() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)

        first = store.write_chat_episode(
            request_id="req-life-001",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="你觉得这几本书怎么样",
            assistant_reply="我这边没拿到图。",
            ts_ms=1_800_000_000_000,
        )
        second = store.write_chat_episode(
            request_id="req-life-001",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="你觉得这几本书怎么样",
            assistant_reply="补充：这张图里有三本。",
            attachments=[{"type": "image", "url": "qq://image/img-1"}],
            multimodal_observations={"primary_modality": "image", "visual_facts": "三本书摆在桌上"},
            attachment_summary="观察到三本书摆在桌上。",
            ts_ms=1_800_000_000_010,
        )

        assert first["episode_id"] == second["episode_id"]
        assert len(ledger.life_episode) == 1
        evidence = ledger.life_evidence
        assert {row["evidence_kind"] for row in evidence} >= {
            "user_text",
            "assistant_reply",
            "gateway_message",
            "vision_observation",
        }
        assert any(row["source_ref"] == "gateway://message/req-life-001" for row in evidence)
        assert any(row["source_ref"] == "qq://image/img-1" for row in evidence)
    finally:
        ledger.close()


def test_voice_gateway_anchor_binds_person_and_message_ref(monkeypatch) -> None:
    monkeypatch.setenv("HOST_DEFAULT_USER_ID", "owner")
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        ledger.log_voice_event(
            {
                "request_id": "req-voice-gateway",
                "session_id": "qq_dm_3414578517",
                "event_type": "INPUT_COMPLETED",
                "direction": "EDGE_TO_HOST",
                "status": "OK",
                "reason_code": "SURFACE_REPLY",
                "detail": "gateway reply status=queued",
                "payload": json.dumps(
                    {
                        "source": "GATEWAY",
                        "platform": "qq",
                        "conversation_id": "qq_dm_3414578517",
                        "from_id": "3414578517",
                        "person_id": "3414578517",
                        "msg_id": "msg-voice-1",
                    },
                    ensure_ascii=False,
                ),
                "ts_ms": 1_800_000_000_000,
            }
        )

        episode = ledger.life_episode[0]
        metadata = episode["metadata"] if isinstance(episode["metadata"], dict) else json.loads(episode["metadata"])
        assert episode["person_id"] == "owner"
        assert "gateway://message/msg-voice-1" in metadata["evidence_refs"]
        assert "chat://person/owner" in metadata["evidence_refs"]
        assert any(row["source_ref"] == "voice://event/req-voice-gateway" for row in ledger.life_evidence)
    finally:
        ledger.close()


def test_correction_suppresses_episode_from_default_recall() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-life-correction",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="比如哪本呢",
            assistant_reply="封面上有只猫的那本。",
            ts_ms=1_800_000_000_000,
        )

        before = store.recall_episode_packet(
            query="封面 猫",
            top_k=3,
            person_id="user-1",
            session_id="qq_dm_1",
            now_ts_ms_value=1_800_000_000_100,
        )
        assert before.candidates

        store.correct_episode(
            episode_id=written["episode_id"],
            correction_kind="suppression",
            reason_code="USER_CORRECTED_HALLUCINATION",
            detail="用户指出没有这种书。",
            suppress=True,
            ts_ms=1_800_000_000_200,
        )

        after = store.recall_episode_packet(
            query="封面 猫",
            top_k=3,
            person_id="user-1",
            session_id="qq_dm_1",
            now_ts_ms_value=1_800_000_000_300,
        )
        assert after.candidates == []

        audit = store.recall_episode_packet(
            query="封面 猫",
            top_k=3,
            include_suppressed=True,
            now_ts_ms_value=1_800_000_000_300,
        )
        assert audit.candidates
        assert audit.candidates[0]["metadata"]["truth_state"] == TRUTH_SUPPRESSED
        assert audit.candidates[0]["metadata"]["correction_count"] == 1
    finally:
        ledger.close()


def test_corrected_episode_is_audit_only_not_default_fact() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-life-corrected-wrong",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="刚才那三本里你选哪本",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.write_chat_episode(
            request_id="req-life-corrected-fix",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="不对，没有封面有猫这本。",
            assistant_reply="你说得对，我把这个判断撤回。",
            ts_ms=1_800_000_000_100,
        )

        store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="correction",
            correction_episode_id=correction["episode_id"],
            reason_code="USER_CORRECTED_FALSE_MEMORY",
            detail="用户指出没有封面有猫这本。",
            suppress=False,
            ts_ms=1_800_000_000_200,
        )

        default = store.recall_episode_packet(
            query="封面 猫",
            top_k=3,
            person_id="user-1",
            session_id="qq_dm_1",
            now_ts_ms_value=1_800_000_000_300,
        )
        audit = store.recall_episode_packet(
            query="封面 猫",
            top_k=3,
            include_suppressed=True,
            person_id="user-1",
            session_id="qq_dm_1",
            now_ts_ms_value=1_800_000_000_300,
        )

        default_ids = {item["memory_id"] for item in default.candidates}
        audit_rows = {item["memory_id"]: item for item in audit.candidates}
        assert wrong["episode_id"] not in default_ids
        assert correction["episode_id"] in default_ids
        assert wrong["episode_id"] in audit_rows
        assert audit_rows[wrong["episode_id"]]["metadata"]["truth_state"] == TRUTH_CORRECTED
        assert audit_rows[wrong["episode_id"]]["metadata"]["correction_count"] == 1
    finally:
        ledger.close()


def test_failure_recall_prefers_recorded_failure_over_failure_word_in_title() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/lightnovel-title-noise"],
            primary_ref="action://event/lightnovel-title-noise",
            person_id="user-1",
            session_id="sess-1",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="今日推荐 A：培养失败的前美少女童星；推荐已成功发送。",
            salience=0.55,
            ts_ms=1_800_000_000_000,
            metadata={"selected_recommendations": [{"title": "培养失败的前美少女童星"}]},
        )
        store.write_background_episode(
            event_source="codex_production",
            event_id="deepseek-timeout-1",
            person_id="user-1",
            session_id="sess-1",
            task_id="llm_pool_call",
            status="FAILED",
            reason_code="DEEPSEEK_TIMEOUT",
            detail="DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
            ts_ms=1_800_000_000_100,
        )

        packet = store.recall_episode_packet(
            query="DeepSeek失败原因是什么？",
            top_k=3,
            person_id="user-1",
            session_id="sess-1",
            now_ts_ms_value=1_800_000_000_200,
        )

        assert packet.candidates
        assert "供应商超时" in packet.candidates[0]["text"]
        assert "培养失败的前美少女童星" not in packet.candidates[0]["text"]
    finally:
        ledger.close()


def test_correction_recall_prefers_correction_chain_over_question_echo() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-wrong-cat",
            session_id="sess-1",
            person_id="user-1",
            user_message="刚才那三本里你选哪本？",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.write_chat_episode(
            request_id="req-fix-cat",
            session_id="sess-1",
            person_id="user-1",
            user_message="不对，没有封面有猫这本。",
            assistant_reply="你说得对，我撤回这个判断。",
            ts_ms=1_800_000_000_100,
        )
        store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="correction",
            correction_episode_id=correction["episode_id"],
            reason_code="USER_CORRECTED_FALSE_MEMORY",
            detail="用户指出没有封面有猫这本。",
            suppress=False,
            ts_ms=1_800_000_000_200,
        )
        store.write_chat_episode(
            request_id="req-bad-echo-cat",
            session_id="sess-1",
            person_id="user-1",
            user_message="你刚才是不是记错了封面猫那本？",
            assistant_reply="哈？那本封面确实有只猫，我特意留意过的。",
            ts_ms=1_800_000_000_300,
        )

        packet = store.recall_episode_packet(
            query="你刚才是不是记错了封面猫那本？",
            top_k=3,
            person_id="user-1",
            session_id="sess-1",
            now_ts_ms_value=1_800_000_000_400,
        )

        assert packet.candidates
        assert "没有封面有猫" in packet.candidates[0]["text"]
        assert "确实有只猫" not in packet.candidates[0]["text"]
    finally:
        ledger.close()


def test_evidence_review_prefers_substantive_web_episode_over_generic_absence() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_background_episode(
            event_source="skill",
            event_id="generic-web-absence",
            person_id="user-1",
            session_id="sess-1",
            task_id="sub_agent:intent_agent",
            status="OK",
            reason_code="SUB_AGENT_OK",
            detail="我现在没有看到昨天那个网页的具体内容，不能直接判断它靠不靠谱。",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "audit_anchor"},
        )
        store.write_background_episode(
            event_source="browser",
            event_id="onebot-web-report",
            person_id="user-1",
            session_id="sess-1",
            task_id="web_research",
            status="COMPLETED",
            reason_code="WEB_REPORT_READ",
            detail=(
                "昨天阅读了一个网页报告；网页主题：OneBot 部署位置核对；"
                "正文内容：OneBot 在 edge-lan 上运行，本机只负责 host；"
                "依据：报告正文包含 edge-lan ssh、网关地址和运行位置三项互相一致的信息。"
            ),
            ts_ms=1_800_000_000_100,
            metadata={"domain": "web", "evidence_refs": ["doc://web/onebot-edge#body"]},
        )

        packet = store.recall_episode_packet(
            query="昨天那个网页内容靠谱吗？依据是什么？",
            top_k=3,
            person_id="user-1",
            session_id="sess-1",
            now_ts_ms_value=1_800_000_000_200,
        )

        assert packet.candidates
        assert "OneBot 在 edge-lan" in packet.candidates[0]["text"]
        assert "没有看到" not in packet.candidates[0]["text"]
    finally:
        ledger.close()


def test_consolidator_uses_only_trusted_active_episodes() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-trusted",
            session_id="sess-1",
            person_id="user-1",
            user_message="记住我喜欢深夜写代码",
            assistant_reply="记住了。",
            ts_ms=1_800_000_000_000,
        )
        suppressed = store.write_chat_episode(
            request_id="req-life-suppressed",
            session_id="sess-1",
            person_id="user-1",
            user_message="错误事实",
            assistant_reply="错误事实已写入。",
            ts_ms=1_800_000_000_001,
        )
        store.correct_episode(
            episode_id=suppressed["episode_id"],
            correction_kind="suppression",
            suppress=True,
            detail="bad fact",
            ts_ms=1_800_000_000_100,
        )

        candidates = LifeMemoryConsolidator(store=store).build_semantic_candidates(limit=10)
        ids = {row["episode_id"] for row in candidates}

        assert trusted["episode_id"] in ids
        assert suppressed["episode_id"] not in ids
    finally:
        ledger.close()


def test_consolidator_distills_user_preference_from_trusted_episode_only() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-pref",
            session_id="sess-1",
            person_id="user-1",
            user_message="记住我喜欢轻小说推荐按总览一段、每本书一卡片分段",
            assistant_reply="记住了，以后按总览和卡片来。",
            ts_ms=1_800_000_000_000,
        )
        suppressed = store.write_chat_episode(
            request_id="req-life-semantic-bad",
            session_id="sess-1",
            person_id="user-1",
            user_message="错误偏好：推荐不要分段",
            assistant_reply="已记录。",
            ts_ms=1_800_000_000_001,
        )
        store.correct_episode(
            episode_id=suppressed["episode_id"],
            correction_kind="suppression",
            suppress=True,
            detail="用户后来明确纠正推荐需要分段。",
            ts_ms=1_800_000_000_100,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        second = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_300,
        )

        items, total = ledger.list_semantic_memories()
        assert result["written_count"] == 1
        assert second["written_count"] == 0
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "总览" in items[0]["summary"]
        assert "卡片" in items[0]["summary"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
        assert f"life_episode::{suppressed['episode_id']}" not in items[0]["source_memory_ids"]
        assert "source:life_episode" in items[0]["tags"]
        assert "semantic_kind:user_preference" in items[0]["tags"]
        assert "memory_family:user_model" in items[0]["tags"]
        assert "semantic_tier:durable_profile" in items[0]["tags"]
        assert items[0]["distill_strategy"] == "life_episode_rule_consolidation"
    finally:
        ledger.close()


def test_consolidator_does_not_distill_lookup_questions_as_semantic_facts() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_chat_episode(
            request_id="req-life-semantic-pref-question",
            session_id="sess-1",
            person_id="user-1",
            user_message="还记得我说推荐格式要怎样吗？",
            assistant_reply="记得，推荐按总览和卡片分段。",
            ts_ms=1_800_000_000_000,
        )
        store.write_chat_episode(
            request_id="req-life-semantic-failure-question",
            session_id="sess-1",
            person_id="user-1",
            user_message="DeepSeek失败原因是什么？",
            assistant_reply="那次记录是 provider timeout。",
            ts_ms=1_800_000_000_001,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 0
        assert total == 0
        assert items == []
    finally:
        ledger.close()


def test_semantic_growth_diagnostics_explain_lookup_question_skips() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        episode = store.write_chat_episode(
            request_id="req-life-semantic-selection-question",
            session_id="sess-1",
            person_id="user-1",
            user_message="我问你喜欢哪本",
            assistant_reply="第二本吧。",
            ts_ms=1_800_000_000_000,
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (
                        json.dumps(
                            {
                                "memory_class": "preference",
                                "memory_policy": {"memory_class": "preference", "semantic_candidate": True},
                            },
                            ensure_ascii=False,
                        ),
                        episode["episode_id"],
                    ),
                )

        diagnostics = LifeMemoryConsolidator(store=store).semantic_growth_diagnostics(limit=10)

        assert diagnostics["semantic_candidate_counts"]["preference"] == 1
        assert diagnostics["proposal_counts"] == {}
        assert diagnostics["skip_reason_counts"]["lookup_question"] == 1
        assert diagnostics["skip_samples"][0]["reason_code"] == "lookup_question"
    finally:
        ledger.close()


def test_semantic_growth_gap_report_flags_synthetic_only_priority_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        synthetic = store.write_chat_episode(
            request_id="req-life-memory-real-scene-e2e-pref",
            session_id="life-memory-real-scene-e2e",
            person_id="life-memory-real-scene-e2e",
            user_message="以后推荐轻小说时，每本书的简介后应该换行。",
            assistant_reply="好的，我会按这个格式来。",
            ts_ms=1_800_000_000_000,
        )
        metadata = {
            "memory_class": "preference",
            "memory_policy": {"memory_class": "preference", "semantic_candidate": True},
        }
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (json.dumps(metadata, ensure_ascii=False), synthetic["episode_id"]),
                )

        report = LifeMemoryConsolidator(store=store).semantic_growth_gap_report(limit=20, sample_limit=4)

        assert report["synthetic_candidate_counts"]["preference"] == 1
        assert report["production_candidate_counts"].get("preference", 0) == 0
        assert "preference" in report["synthetic_only_candidate_classes"]
        assert any("production_source_absent" in item for item in report["recommendations"])
    finally:
        ledger.close()


def test_semantic_growth_gap_report_clears_gap_when_production_preference_exists() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        synthetic = store.write_chat_episode(
            request_id="req-life-memory-real-scene-e2e-pref",
            session_id="life-memory-real-scene-e2e",
            person_id="life-memory-real-scene-e2e",
            user_message="以后推荐轻小说时，每本书的简介后应该换行。",
            assistant_reply="好的。",
            ts_ms=1_800_000_000_000,
        )
        production = store.write_chat_episode(
            request_id="req-production-pref-format",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="以后推荐轻小说时，每本书的简介后应该换行。",
            assistant_reply="好的，我记住这个格式。",
            ts_ms=1_800_000_000_100,
        )
        metadata = {
            "memory_class": "preference",
            "memory_policy": {"memory_class": "preference", "semantic_candidate": True},
        }
        with ledger._write_lock:
            with ledger._conn:
                for episode in (synthetic, production):
                    ledger._conn.execute(
                        "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                        (json.dumps(metadata, ensure_ascii=False), episode["episode_id"]),
                    )

        report = LifeMemoryConsolidator(store=store).semantic_growth_gap_report(limit=20, sample_limit=4)

        assert report["production_candidate_counts"]["preference"] == 1
        assert report["synthetic_candidate_counts"]["preference"] == 1
        assert "preference" not in report["synthetic_only_candidate_classes"]
    finally:
        ledger.close()


def test_semantic_growth_gap_report_keeps_suppressed_source_audit_only() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-gap-suppressed-selection-source",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="我问你喜欢哪本",
            assistant_reply="第二本吧，标题更对胃口。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.write_chat_episode(
            request_id="req-gap-suppressed-selection-correction",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="不对，这个判断没有正文证据。",
            assistant_reply="你说得对，我撤回这个判断。",
            ts_ms=1_800_000_000_100,
        )
        store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="suppression",
            correction_episode_id=correction["episode_id"],
            suppress=True,
            detail="用户指出这次选书判断没有正文证据。",
            ts_ms=1_800_000_000_200,
        )
        store.backfill_memory_policy(batch_limit=10, include_existing=True)

        report = LifeMemoryConsolidator(store=store).semantic_growth_gap_report(limit=20, sample_limit=4)

        assert report["production_inactive_priority_counts"]["correction"]["corrected_or_suppressed_source"] >= 1
        assert report["production_candidate_counts"]["correction"] == 1
        assert report["suppressed_source_counts"]["correction"] >= 1
    finally:
        ledger.close()


def test_owner_directive_episode_distills_to_production_preference_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_owner_directive_episode(
            request_id="req-owner-directive-format",
            session_id="codex_operator",
            person_id="owner",
            directive_kind="recommendation_format",
            directive_text="以后推荐轻小说时，不要拟人化发送；每本简介后应该换行。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        assert metadata["owner_directive"] is True
        assert metadata["memory_class"] == "preference"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert any(
            item["episode_id"] == written["episode_id"]
            and item["evidence_kind"] == "user_text"
            and item["source_ref"] == "chat://request/req-owner-directive-format#owner"
            for item in ledger.life_evidence
        )

        distilled = LifeMemoryConsolidator(store=store).distill_semantic_memories(limit=10, max_records=5)
        report = LifeMemoryConsolidator(store=store).semantic_growth_gap_report(limit=20)

        assert distilled["written_count"] == 1
        assert "用户偏好" in distilled["written"][0]["summary"]
        assert report["production_candidate_counts"]["preference"] == 1
        assert "preference" not in report["synthetic_only_candidate_classes"]
    finally:
        ledger.close()


def test_owner_directive_episode_distills_memory_system_self_change() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_owner_directive_episode(
            request_id="req-owner-directive-memory-design",
            session_id="codex_operator",
            person_id="owner",
            directive_kind="memory_system_design",
            directive_text="记忆系统需要遗忘：低价值细节降为 COLD 或 GONE，重要偏好、承诺、纠错、自我改变和失败经验不能轻易忘。",
            applied_result="LifeMemory 增加 owner_directive 入口，控制面指令也会先成为 Episode。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        assert metadata["memory_class"] == "self_change"
        assert metadata["memory_policy"]["semantic_candidate"] is True

        distilled = LifeMemoryConsolidator(store=store).distill_semantic_memories(limit=10, max_records=5)

        assert distilled["written_count"] == 1
        assert "自我改变" in distilled["written"][0]["summary"]
        assert any("self_change" in item for item in distilled["written"][0]["tags"])
    finally:
        ledger.close()


def test_sleep_replay_reflection_does_not_pollute_self_change_semantic_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_owner_directive_episode(
            request_id="req-owner-directive-memory-design-clean",
            session_id="codex_operator",
            person_id="owner",
            directive_kind="memory_system_design",
            directive_text="她做过的事都要留下生命经历锚点，但不等于所有日志都进入可召回记忆。",
            ts_ms=1_800_000_000_000,
        )
        store.attach_evidence(
            episode_id=written["episode_id"],
            evidence_kind=EVIDENCE_REFLECTION,
            source_ref=f"sleep_replay://episode/{written['episode_id']}/1800000000100",
            request_id="sleep_replay::1800000000100",
            content="睡眠回放强化：Owner directive: 以后推荐轻小说时，不要拟人化发送；每本简介后应该换行；Thalamus LLM 用模型池，不要用 Codex。",
            payload={"replay_reason": "high_memory_strength"},
            trust_level=TRUTH_NARRATIVE_ONLY,
            ts_ms=1_800_000_000_100,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "生命经历锚点" in items[0]["summary"]
        assert "可召回记忆" in items[0]["summary"]
        assert "睡眠回放强化" not in items[0]["summary"]
        assert "Owner directive" not in items[0]["summary"]
        assert "推荐轻小说" not in items[0]["summary"]
        assert "Thalamus" not in items[0]["summary"]
    finally:
        ledger.close()


def test_consolidator_replaces_polluted_self_change_semantic_summary_with_clean_proposal() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        semantic_key = "self_change|global|memory_system_design"
        source_hash = hashlib.sha256(f"life_semantic:v1:{semantic_key}".encode("utf-8")).hexdigest()
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_polluted_self_change",
                "summary": (
                    "自我改变：睡眠回放强化：Owner directive: 以后推荐轻小说时，不要拟人化发送；"
                    "Thalamus LLM 用模型池，不要用 Codex；她做过的事都要留下 LifeMemory Episode。"
                ),
                "source_memory_ids": ["life_episode::ep_old_polluted"],
                "source_hash": source_hash,
                "distill_strategy": "life_episode_rule_consolidation",
                "confidence": 0.7,
                "tags": ["self_change", "semantic_kind:self_change", "source:life_episode"],
                "ts_ms": 1_800_000_000_000,
                "operator_node_id": "memory_sleep_replay",
            }
        )
        written = store.write_owner_directive_episode(
            request_id="req-owner-directive-memory-design-replace",
            session_id="codex_operator",
            person_id="owner",
            directive_kind="memory_system_design",
            directive_text="记忆系统需要遗忘：低价值细节降为 COLD 或 GONE，重要偏好、承诺、纠错、自我改变和失败经验不能轻易忘。",
            ts_ms=1_800_000_000_100,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        refreshed = ledger.get_semantic_memory_by_source_hash(source_hash)

        assert result["written_count"] == 1
        assert refreshed is not None
        assert refreshed["summary"].startswith("自我改变：")
        assert "记忆系统需要遗忘" in refreshed["summary"]
        assert "睡眠回放强化" not in refreshed["summary"]
        assert "Owner directive" not in refreshed["summary"]
        assert "推荐轻小说" not in refreshed["summary"]
        assert "Thalamus" not in refreshed["summary"]
        assert f"life_episode::{written['episode_id']}" in refreshed["source_memory_ids"]
        assert "life_episode::ep_old_polluted" in refreshed["source_memory_ids"]
    finally:
        ledger.close()


def test_consolidator_distills_direct_recall_answer_as_durable_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        recall_packet = {
            "facts": ["长期事实：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。"],
            "evidence_refs": ["chat://request/req-pref-format#user"],
            "source_refs": ["ledger://semantic_memory/sem-pref-format"],
            "degraded": False,
            "trace": {
                "recall_answer_readiness": {
                    "ready_to_answer": True,
                    "response_mode": "answer_from_memory",
                }
            },
        }
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-direct-pref",
            session_id="sess-1",
            person_id="user-1",
            user_message="还记得我说推荐格式要怎样吗？",
            assistant_reply="记得：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            ts_ms=1_800_000_000_000,
            metadata={
                "reply_source": "surface_memory_direct_answer",
                "trace_stage_meta": {
                    "reply_source": "surface_memory_direct_answer",
                    "surface_memory_recall_packet": recall_packet,
                },
            },
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "每本书的简介后应该换行" in items[0]["summary"]
        assert "别模板腔" in items[0]["summary"]
        assert "还记得" not in items[0]["summary"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
    finally:
        ledger.close()


def test_consolidator_does_not_distill_unproven_direct_answer_self_echo() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_chat_episode(
            request_id="req-life-semantic-direct-pref-no-provenance",
            session_id="sess-1",
            person_id="user-1",
            user_message="还记得我说推荐格式要怎样吗？",
            assistant_reply="记得：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            ts_ms=1_800_000_000_000,
            metadata={"reply_source": "surface_memory_direct_answer"},
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()
        diagnostics = LifeMemoryConsolidator(store=store).semantic_growth_diagnostics(limit=10)

        assert result["written_count"] == 0
        assert total == 0
        assert items == []
        assert diagnostics["skip_reason_counts"]["lookup_question"] == 1
    finally:
        ledger.close()


def test_consolidator_does_not_distill_direct_answer_with_semantic_only_provenance() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        recall_packet = {
            "facts": ["长期事实：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。"],
            "evidence_refs": ["semantic://sem-pref-format"],
            "source_refs": ["ledger://semantic_memory/sem-pref-format"],
            "rows": [
                {
                    "source_ref": "ledger://semantic_memory/sem-pref-format",
                    "evidence_ref": "semantic://sem-pref-format",
                }
            ],
            "degraded": False,
            "trace": {
                "recall_answer_readiness": {
                    "ready_to_answer": True,
                    "response_mode": "answer_from_memory",
                }
            },
        }
        store.write_chat_episode(
            request_id="req-life-semantic-direct-pref-semantic-only",
            session_id="sess-1",
            person_id="user-1",
            user_message="还记得我说推荐格式要怎样吗？",
            assistant_reply="记得：以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            ts_ms=1_800_000_000_000,
            metadata={
                "reply_source": "surface_memory_direct_answer",
                "trace_stage_meta": {
                    "reply_source": "surface_memory_direct_answer",
                    "surface_memory_recall_packet": recall_packet,
                },
            },
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()
        diagnostics = LifeMemoryConsolidator(store=store).semantic_growth_diagnostics(limit=10)

        assert result["written_count"] == 0
        assert total == 0
        assert items == []
        assert diagnostics["skip_reason_counts"]["lookup_question"] == 1
    finally:
        ledger.close()


def test_consolidator_distills_reasoned_correction_record_without_repromoting_source() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-life-correction-record-wrong",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="刚才那三本里你选哪本？",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="suppression",
            reason_code="USER_CORRECTED_HALLUCINATION",
            detail="用户指出没有封面有猫这本。",
            suppress=True,
            ts_ms=1_800_000_000_100,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "纠错记忆" in items[0]["summary"]
        assert "没有“封面有猫”这本" in items[0]["summary"]
        assert items[0]["source_memory_ids"] == [f"life_memory_correction::{correction['correction_id']}"]
        assert f"life_episode::{wrong['episode_id']}" not in items[0]["source_memory_ids"]
        assert "semantic_kind:correction" in items[0]["tags"]
        assert "source:life_memory_correction" in items[0]["tags"]
    finally:
        ledger.close()


def test_consolidator_distills_llm_routing_constraint_as_user_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-llm-route-pref",
            session_id="sess-1",
            person_id="user-1",
            user_message="thalamus LLM 用模型池，不要用 Codex，太慢了；模型池里优先使用 DeepSeek。",
            assistant_reply="记住了，thalamus 走模型池，优先 DeepSeek。",
            ts_ms=1_800_000_000_000,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "thalamus LLM" in items[0]["summary"]
        assert "不要用 Codex" in items[0]["summary"]
        assert "优先使用 DeepSeek" in items[0]["summary"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
    finally:
        ledger.close()


def test_consolidator_distills_feature_toggle_as_user_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-feature-toggle-pref",
            session_id="sess-1",
            person_id="user-1",
            user_message="先把拟人化发送关了，影响体验，纯负优化。",
            assistant_reply="记住了，先关掉拟人化发送。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == trusted["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "preference"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "拟人化发送关了" in items[0]["summary"]
        assert "semantic_kind:user_preference" in items[0]["tags"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
    finally:
        ledger.close()


def test_consolidator_distills_relationship_feedback_about_bad_recall() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-feedback-recall-quality",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="简直胡说八道，召回不能编造昨天推荐。以后先看证据再回答。",
            assistant_reply="确实，这类问题以后先核对推荐 episode 和证据，不再凭感觉补。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == trusted["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "relationship_feedback"
        assert metadata["recall_grain"] == "semantic"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert row["decay_profile_id"] == "STABLE"
        assert result["written_count"] == 1
        assert total == 1
        assert "关系反馈" in items[0]["summary"]
        assert "记忆召回和判断必须回到证据" in items[0]["summary"]
        assert "胡说八道" in items[0]["summary"]
        assert "semantic_kind:relationship_feedback" in items[0]["tags"]
        assert "memory_family:relationship_model" in items[0]["tags"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
    finally:
        ledger.close()


def test_consolidator_keeps_natural_chat_feedback_separate_from_recall_quality() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        natural = store.write_chat_episode(
            request_id="req-life-semantic-feedback-natural-chat",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="文本质量不像人话，太模板腔了，要像正常聊天。",
            assistant_reply="收到，我会把证据和判断转成更自然的聊天方式。",
            ts_ms=1_800_000_000_000,
        )
        recall_quality = store.write_chat_episode(
            request_id="req-life-semantic-feedback-recall-evidence",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="简直胡说八道，召回不能编造昨天推荐。以后先看证据再回答。",
            assistant_reply="确实，这类问题以后先核对推荐 episode 和证据，不再凭感觉补。",
            ts_ms=1_800_000_000_100,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=10,
            ts_ms=1_800_000_000_300,
        )
        items, total = ledger.list_semantic_memories()
        natural_rows = [item for item in items if "回答要像正常聊天" in item["summary"]]
        recall_rows = [item for item in items if "记忆召回和判断必须回到证据" in item["summary"]]

        assert result["written_count"] == 2
        assert total == 2
        assert len(natural_rows) == 1
        assert len(recall_rows) == 1
        natural_key = "relationship_feedback|owner|natural_conversation_quality_feedback"
        recall_key = "relationship_feedback|owner|memory_recall_quality_feedback"
        natural_hash = hashlib.sha256(f"life_semantic:v1:{natural_key}".encode("utf-8")).hexdigest()
        recall_hash = hashlib.sha256(f"life_semantic:v1:{recall_key}".encode("utf-8")).hexdigest()
        assert natural_rows[0]["source_hash"] == natural_hash
        assert recall_rows[0]["source_hash"] == recall_hash
        assert natural_rows[0]["source_hash"] != recall_rows[0]["source_hash"]
        assert natural_rows[0]["source_memory_ids"] == [f"life_episode::{natural['episode_id']}"]
        assert recall_rows[0]["source_memory_ids"] == [f"life_episode::{recall_quality['episode_id']}"]
    finally:
        ledger.close()


def test_relationship_feedback_without_quality_signal_stays_regular_chat() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-life-feedback-vague-negative",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="这不行啊。",
            assistant_reply="那我换个方式。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        _items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "chat_experience"
        assert metadata["memory_policy"]["semantic_candidate"] is False
        assert result["written_count"] == 0
        assert total == 0
    finally:
        ledger.close()


def test_relationship_feedback_is_recallable_before_semantic_distillation() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        feedback = store.write_chat_episode(
            request_id="req-life-feedback-direct-recall",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="文本质量不像人话，太模板腔了。",
            assistant_reply="我会改成更自然的聊天方式。",
            ts_ms=1_800_000_000_000,
        )

        packet = store.recall_episode_packet(
            query="我之前说文本质量不像人话，是哪里的问题？",
            top_k=3,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=1_800_000_000_100,
        )

        assert packet.candidates
        assert packet.candidates[0]["memory_id"] == feedback["episode_id"]
        assert "不像人话" in packet.candidates[0]["text"]
    finally:
        ledger.close()


def test_owner_directive_episode_distills_to_open_loop_commitment() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_owner_directive_episode(
            request_id="req-owner-directive-open-loop",
            session_id="codex_operator",
            person_id="owner",
            directive_kind="memory_system_open_loop",
            directive_text="待办：继续完善记忆系统，补齐长期语义蒸馏和召回验证。",
            applied_result="已写入当前记忆系统完善目标。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        distilled = LifeMemoryConsolidator(store=store).distill_semantic_memories(limit=10, max_records=5)
        open_loops = store.list_open_loops(status="OPEN")
        report = LifeMemoryConsolidator(store=store).semantic_growth_gap_report(limit=20)

        assert metadata["owner_directive"] is True
        assert metadata["memory_class"] == "commitment"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        commitment = next(item for item in distilled["written"] if "semantic_kind:commitment" in item["tags"])
        assert distilled["written_count"] >= 1
        assert "承诺和待办" in commitment["summary"]
        assert "open_loop:open" in commitment["tags"]
        assert open_loops and open_loops[0]["episode_id"] == written["episode_id"]
        assert open_loops[0]["status"] == "OPEN"
        assert report["production_candidate_counts"]["commitment"] == 1
        assert "commitment" not in report["synthetic_only_candidate_classes"]
    finally:
        ledger.close()


def test_consolidator_distills_memory_system_directive_as_self_change() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-memory-system-directive",
            session_id="sess-1",
            person_id="user-1",
            user_message="她做过的事都要留下生命经历锚点，但不等于所有日志都进入可召回记忆。",
            assistant_reply="明白，经历要可追溯，主动记忆要筛选。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == trusted["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "self_change"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert result["written_count"] == 1
        assert total == 1
        assert "自我改变" in items[0]["summary"]
        assert "生命经历锚点" in items[0]["summary"]
        assert "可召回记忆" in items[0]["summary"]
        assert "semantic_kind:self_change" in items[0]["tags"]
        assert "memory_family:self_model" in items[0]["tags"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
    finally:
        ledger.close()


def test_memory_system_no_patch_directive_is_self_change_not_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-memory-system-no-patch",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="不要打补丁，我的意思是她所有做过的事都要进入记忆，这样才能是真实生命吧。",
            assistant_reply="明白，这属于记忆系统的设计约束，不是临时补丁。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == trusted["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "self_change"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert result["written_count"] == 1
        assert total == 1
        assert "自我改变" in items[0]["summary"]
        assert "用户偏好" not in items[0]["summary"]
        assert "所有做过的事都要进入记忆" in items[0]["summary"]
        assert "semantic_kind:self_change" in items[0]["tags"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{trusted['episode_id']}"]
    finally:
        ledger.close()


def test_recommendation_format_change_remains_preference_not_self_change() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        trusted = store.write_chat_episode(
            request_id="req-life-semantic-format-change-pref",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="以后推荐格式改成总览一段、每本卡片一段，简介后换行。",
            assistant_reply="记住了，之后按这个推荐格式发。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == trusted["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "preference"
        assert metadata["memory_policy"]["semantic_candidate"] is True
        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "自我改变" not in items[0]["summary"]
        assert "推荐格式改成总览一段" in items[0]["summary"]
    finally:
        ledger.close()


def test_consolidator_tracks_open_loop_lifecycle_from_commitment_and_closure() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        opened = store.write_chat_episode(
            request_id="req-life-open-loop-open",
            session_id="sess-1",
            person_id="user-1",
            user_message="后续要把记忆系统的召回修好，记成待办。",
            assistant_reply="记下了，我会把记忆召回修复作为待办继续推进。",
            ts_ms=1_800_000_000_000,
        )

        first = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        open_rows = store.list_open_loops(status="OPEN")

        assert first["written_count"] == 1
        assert len(open_rows) == 1
        assert open_rows[0]["status"] == "OPEN"
        assert open_rows[0]["episode_id"] == opened["episode_id"]
        assert "记忆系统" in open_rows[0]["title"]
        items, total = ledger.list_semantic_memories()
        assert total == 1
        assert "open_loop:open" in items[0]["tags"]

        closed = store.write_chat_episode(
            request_id="req-life-open-loop-closed",
            session_id="sess-1",
            person_id="user-1",
            user_message="记忆系统的召回修复已经完成，关闭这个待办。",
            assistant_reply="好，这个待办我标成完成。",
            ts_ms=1_800_000_000_200,
        )

        second = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_300,
        )
        closed_rows = store.list_open_loops(status="CLOSED")
        still_open = store.list_open_loops(status="OPEN")
        items, total = ledger.list_semantic_memories()

        assert second["written_count"] == 1
        assert len(closed_rows) == 1
        assert still_open == []
        assert closed_rows[0]["status"] == "CLOSED"
        assert closed_rows[0]["close_episode_id"] == closed["episode_id"]
        assert closed_rows[0]["semantic_key"] == open_rows[0]["semantic_key"]
        assert total == 1
        assert "open_loop:closed" in items[0]["tags"]
        assert "open_loop:open" not in items[0]["tags"]
        assert set(items[0]["source_memory_ids"]) == {
            f"life_episode::{opened['episode_id']}",
            f"life_episode::{closed['episode_id']}",
        }
    finally:
        ledger.close()


def test_episode_recall_targets_open_loop_commitments_without_recommendation_noise() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        opened = store.write_chat_episode(
            request_id="req-life-open-loop-recall-target",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="后续要把记忆系统的召回覆盖 open loop 和自我改变，记成待办。",
            assistant_reply="记下了，这个作为记忆系统待办继续推进。",
            ts_ms=1_800_000_000_000,
        )
        store.write_background_episode(
            event_source="codex_production",
            event_id="job-lightnovel-noise",
            person_id="owner",
            session_id="qq_dm_owner",
            task_id="lightnovel_recommendation_delivery",
            status="COMPLETED",
            detail="5月28日推荐 A；详情：https://www.lightnovel.fun/cn/detail/2801",
            ts_ms=1_800_000_000_050,
            metadata={"domain": "lightnovel_recommendation"},
        )

        packet = store.recall_episode_packet(
            query="记忆系统还有什么待办没完成？",
            top_k=3,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=1_800_000_000_100,
        )

        joined = "\n".join(str(item.get("text") or "") for item in packet.candidates)
        assert packet.candidates
        assert packet.candidates[0]["memory_id"] == opened["episode_id"]
        assert "召回覆盖 open loop 和自我改变" in joined
        assert "5月28日推荐 A" not in joined
    finally:
        ledger.close()


def test_preference_recall_prefers_format_rule_over_response_quality_feedback() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        preference = store.write_chat_episode(
            request_id="req-life-pref-format-rule",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            assistant_reply="记住了，后面按自然的卡片格式发。",
            ts_ms=1_800_000_000_000,
        )
        store.write_chat_episode(
            request_id="req-life-relationship-feedback",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="文本质量不像人话，太台词腔了。",
            assistant_reply="收到，我以后把证据转成更自然的聊天方式。",
            ts_ms=1_800_000_000_050,
        )

        packet = store.recall_episode_packet(
            query="还记得我说推荐格式要怎样吗？",
            top_k=4,
            person_id="owner",
            session_id="qq_dm_owner",
            now_ts_ms_value=1_800_000_000_100,
        )

        assert packet.candidates
        assert packet.candidates[0]["memory_id"] == preference["episode_id"]
        assert "简介后应该换行" in str(packet.candidates[0].get("text") or "")
    finally:
        ledger.close()


def test_consolidator_merges_related_preference_sources_into_one_semantic_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        first = store.write_chat_episode(
            request_id="req-life-semantic-pref-format-a",
            session_id="sess-1",
            person_id="user-1",
            user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_000_000,
        )
        second = store.write_chat_episode(
            request_id="req-life-semantic-pref-format-b",
            session_id="sess-1",
            person_id="user-1",
            user_message="修改下推送的格式，前面的总览是完整一段，后面各个卡片各一段。",
            assistant_reply="明白，后面按总览和卡片分段。",
            ts_ms=1_800_000_000_010,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "简介后应该换行" in items[0]["summary"]
        assert "总览" in items[0]["summary"]
        assert "卡片" in items[0]["summary"]
        assert set(items[0]["source_memory_ids"]) == {
            f"life_episode::{first['episode_id']}",
            f"life_episode::{second['episode_id']}",
        }
    finally:
        ledger.close()


def test_consolidator_updates_existing_semantic_memory_with_later_preference_source() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        first = store.write_chat_episode(
            request_id="req-life-semantic-pref-format-initial",
            session_id="sess-1",
            person_id="user-1",
            user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_000_000,
        )
        first_result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        second = store.write_chat_episode(
            request_id="req-life-semantic-pref-format-later",
            session_id="sess-1",
            person_id="user-1",
            user_message="修改下推送的格式，前面的总览是完整一段，后面各个卡片各一段。",
            assistant_reply="明白，后面按总览和卡片分段。",
            ts_ms=1_800_000_000_200,
        )
        second_result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_300,
        )
        items, total = ledger.list_semantic_memories()

        assert first_result["written_count"] == 1
        assert second_result["written_count"] == 1
        assert second_result["written"][0]["updated_existing"] is True
        assert total == 1
        assert "简介后应该换行" in items[0]["summary"]
        assert "总览" in items[0]["summary"]
        assert set(items[0]["source_memory_ids"]) == {
            f"life_episode::{first['episode_id']}",
            f"life_episode::{second['episode_id']}",
        }
    finally:
        ledger.close()


def test_consolidator_prioritizes_user_model_over_collection_index_when_records_are_limited() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/recommendation-real"],
            primary_ref="action://event/recommendation-real",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="给用户推荐了 3 本轻小说。",
            salience=0.7,
            ts_ms=1_800_000_000_000,
            metadata={
                "selected_recommendations": [
                    {"title": "书 A", "selection_reason": "设定新鲜"},
                    {"title": "书 B", "selection_reason": "对白轻快"},
                    {"title": "书 C", "selection_reason": "题材稳定"},
                ],
            },
        )
        store.index_episode_collections(episode_id=source["episode_id"], ts_ms=1_800_000_000_000)
        preference = store.write_chat_episode(
            request_id="req-life-semantic-priority-pref",
            session_id="sess-1",
            person_id="user-1",
            user_message="以后推荐轻小说时，不要模板腔，要自然一点。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_000_100,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=1,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert f"life_episode::{preference['episode_id']}" in items[0]["source_memory_ids"]
        assert not any("life_memory_collection::" in source_id for source_id in items[0]["source_memory_ids"])
    finally:
        ledger.close()


def test_consolidator_overfetches_past_synthetic_priority_rows_to_real_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        for index in range(16):
            store.write_chat_episode(
                request_id=f"req-life-e2e-synthetic-pref-{index}",
                session_id=f"surface-memory-recall-e2e-session-{index}",
                person_id="surface-memory-recall-e2e",
                user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
                assistant_reply="记住了。",
                ts_ms=1_800_000_100_000 + index,
                metadata={"e2e_probe": True},
            )
        real = store.write_chat_episode(
            request_id="req-life-real-preference-not-starved",
            session_id="qq_dm_real",
            person_id="user-1",
            user_message="以后推荐轻小说时，不要模板腔，每本简介后换行。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_000_000,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=3,
            max_records=1,
            ts_ms=1_800_000_200_000,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "用户偏好" in items[0]["summary"]
        assert "模板腔" in items[0]["summary"]
        assert f"life_episode::{real['episode_id']}" in items[0]["source_memory_ids"]
        assert not any("surface-memory-recall-e2e" in source_id for source_id in items[0]["source_memory_ids"])
    finally:
        ledger.close()


def test_consolidator_does_not_distill_sub_agent_probe_as_correction() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        store.write_background_episode(
            event_source="action",
            event_id="evt-sub-agent-correction-probe",
            task_id="intent_agent",
            status="OK",
            reason_code="SUB_AGENT_OK",
            detail="有可能，但我现在看不到前面的具体内容，没法确认是不是记错了。",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "sub_agent:intent_agent"},
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 0
        assert total == 0
        assert items == []
    finally:
        ledger.close()


def test_collection_semantic_distillation_skips_suppressed_source_episode() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/bad-recommendation"],
            primary_ref="action://event/bad-recommendation",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="错误地推荐了 3 本轻小说。",
            salience=0.52,
            ts_ms=1_800_000_000_000,
            metadata={
                "selected_recommendations": [
                    {"title": "坏推荐 A", "selection_reason": "错误候选"},
                    {"title": "坏推荐 B", "selection_reason": "错误候选"},
                    {"title": "坏推荐 C", "selection_reason": "错误候选"},
                ],
            },
        )
        indexed = store.index_episode_collections(
            episode_id=source["episode_id"],
            ts_ms=1_800_000_000_000,
        )
        assert indexed["status"] == "OK"
        assert len(ledger.life_memory_collection_item) == 18

        store.correct_episode(
            episode_id=source["episode_id"],
            correction_kind="suppression",
            suppress=True,
            detail="用户指出这批推荐是错误结果。",
            ts_ms=1_800_000_000_100,
        )

        proposals = LifeMemoryConsolidator(store=store).build_semantic_proposals(limit=10, max_records=10)
        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=10,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert proposals == []
        assert result["written_count"] == 0
        assert total == 0
        assert items == []
    finally:
        ledger.close()


def test_collection_semantic_distillation_skips_synthetic_source_episode() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/real-scene-batch:today:lightnovel_gateway_delivery"],
            primary_ref="action://event/real-scene-batch:today:lightnovel_gateway_delivery",
            person_id="life-memory-real-scene-e2e",
            session_id="life-memory-real-scene-session",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="我在今天给 life-memory-real-scene-e2e 推荐了 3 本轻小说。",
            salience=0.52,
            ts_ms=1_800_000_000_000,
            metadata={
                "e2e_probe": True,
                "real_scene_batch": "real-scene-batch",
                "selected_recommendations": [
                    {
                        "title": "今天推荐 A：培养失败的前美少女童星",
                        "selection_reason": "测试候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/today-a",
                    },
                    {
                        "title": "今天推荐 B：与神在河岸",
                        "selection_reason": "测试候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/today-b",
                    },
                    {
                        "title": "今天推荐 C：异世界皇女皇帝线",
                        "selection_reason": "测试候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/today-c",
                    },
                ],
            },
        )
        indexed = store.index_episode_collections(
            episode_id=source["episode_id"],
            ts_ms=1_800_000_000_000,
        )
        assert indexed["status"] == "OK"
        assert len(ledger.life_memory_collection_item) >= 3

        proposals = LifeMemoryConsolidator(store=store).build_semantic_proposals(limit=10, max_records=10)
        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=10,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert proposals == []
        assert result["written_count"] == 0
        assert total == 0
        assert items == []
    finally:
        ledger.close()


def test_synthetic_collection_recall_is_allowed_inside_synthetic_scope() -> None:
    from datetime import datetime, timedelta, timezone

    cn_tz = timezone(timedelta(hours=8))
    now_ts = int(datetime(2026, 5, 30, 12, tzinfo=cn_tz).timestamp() * 1000)
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old_source = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/real-scene-old:today:lightnovel_gateway_delivery"],
            primary_ref="action://event/real-scene-old:today:lightnovel_gateway_delivery",
            person_id="life-memory-real-scene-e2e",
            session_id="life-memory-real-scene-old-session",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="我在今天给 life-memory-real-scene-e2e 推荐了 1 本轻小说。",
            salience=0.52,
            ts_ms=now_ts - 10,
            metadata={
                "e2e_probe": True,
                "real_scene_batch": "real-scene-old",
                "selected_recommendations": [
                    {
                        "title": "旧会话推荐 X：不该串进当前测试",
                        "selection_reason": "旧会话候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/old-x",
                    },
                ],
            },
        )
        store.index_episode_collections(
            episode_id=old_source["episode_id"],
            ts_ms=now_ts - 10,
        )
        source = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/real-scene-test:today:lightnovel_gateway_delivery"],
            primary_ref="action://event/real-scene-test:today:lightnovel_gateway_delivery",
            person_id="life-memory-real-scene-e2e",
            session_id="life-memory-real-scene-session",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="我在今天给 life-memory-real-scene-e2e 推荐了 3 本轻小说。",
            salience=0.52,
            ts_ms=now_ts,
            metadata={
                "e2e_probe": True,
                "real_scene_batch": "real-scene-test",
                "selected_recommendations": [
                    {
                        "title": "今天推荐 A：培养失败的前美少女童星",
                        "selection_reason": "测试候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/today-a",
                    },
                    {
                        "title": "今天推荐 B：与神在河岸",
                        "selection_reason": "测试候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/today-b",
                    },
                    {
                        "title": "今天推荐 C：异世界皇女皇帝线",
                        "selection_reason": "测试候选",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/today-c",
                    },
                ],
            },
        )
        indexed = store.index_episode_collections(
            episode_id=source["episode_id"],
            ts_ms=now_ts,
        )

        packet = store.recall_collection_packet(
            query="这几天推荐过哪些轻小说？",
            person_id="life-memory-real-scene-e2e",
            session_id="life-memory-real-scene-session",
            now_ts_ms_value=now_ts,
        )

        assert indexed["status"] == "OK"
        assert packet.candidates
        joined = "\n".join(str(item.get("text") or "") for item in packet.candidates)
        assert "今天推荐 A：培养失败的前美少女童星" in joined
        assert "今天推荐 B：与神在河岸" in joined
        assert "旧会话推荐 X" not in joined
    finally:
        ledger.close()


def test_consolidator_soft_deletes_existing_synthetic_semantic_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/real-scene-old:today:lightnovel_gateway_delivery"],
            primary_ref="action://event/real-scene-old:today:lightnovel_gateway_delivery",
            person_id="life-memory-real-scene-e2e",
            session_id="life-memory-real-scene-session",
            action="lightnovel_recommendation_delivery",
            result="COMPLETED",
            summary="我在今天给 life-memory-real-scene-e2e 推荐了 3 本轻小说。",
            salience=0.52,
            ts_ms=1_800_000_000_000,
            metadata={
                "e2e_probe": True,
                "selected_recommendations": [
                    {"title": "今天推荐 A：培养失败的前美少女童星"},
                    {"title": "今天推荐 B：与神在河岸"},
                    {"title": "今天推荐 C：异世界皇女皇帝线"},
                ],
            },
        )
        store.index_episode_collections(episode_id=source["episode_id"], ts_ms=1_800_000_000_000)
        collection_id = next(
            row["collection_id"]
            for row in ledger.life_memory_collection
            if str(row["scope_key"]).startswith("daily:")
        )
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_synthetic_collection",
                "summary": "经历集合：2026-05-29 的轻小说推荐记录了 3 个条目：今天推荐 A：培养失败的前美少女童星、今天推荐 B：与神在河岸、今天推荐 C：异世界皇女皇帝线。",
                "source_memory_ids": [f"life_episode::{source['episode_id']}", f"life_memory_collection::{collection_id}"],
                "source_hash": "synthetic-source-hash",
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.75,
                "tags": ["experience_collection", "source:life_memory_collection"],
                "ts_ms": 1_800_000_000_100,
                "operator_node_id": "life_memory_real_scene_batch",
            }
        )

        dry_run = LifeMemoryConsolidator(store=store).prune_synthetic_semantic_memories(
            ts_ms=1_800_000_000_200,
            dry_run=True,
        )
        before_items, before_total = ledger.list_semantic_memories()
        cleanup = LifeMemoryConsolidator(store=store).prune_synthetic_semantic_memories(
            ts_ms=1_800_000_000_300,
        )
        after_items, after_total = ledger.list_semantic_memories()

        assert dry_run["pruned_count"] == 1
        assert before_total == 1
        assert before_items[0]["semantic_id"] == "sem_synthetic_collection"
        assert cleanup["pruned_count"] == 1
        assert after_total == 0
        assert after_items == []
    finally:
        ledger.close()


def test_consolidator_revives_soft_deleted_semantic_memory_for_same_source_hash() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        semantic_key = "experience_collection|owner|recommendation_collection|lightnovel_recommendation|recent"
        source_hash = hashlib.sha256(f"life_semantic:v1:{semantic_key}".encode("utf-8")).hexdigest()
        ledger.log_semantic_memory(
            {
                "semantic_id": "sem_deleted_synthetic_recent",
                "summary": "旧 synthetic 最近推荐集合。",
                "source_memory_ids": ["life_episode::ep_synthetic_probe"],
                "source_hash": source_hash,
                "distill_strategy": "life_memory_collection_rule_consolidation",
                "confidence": 0.7,
                "tags": ["experience_collection", "source:life_memory_collection"],
                "ts_ms": 1_800_000_000_000,
                "operator_node_id": "life_memory_real_scene_batch",
            }
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE semantic_memory SET deleted_at = ? WHERE source_hash = ?",
                    (1_800_000_000_100, source_hash),
                )

        for index, title in enumerate(("复活测试 A", "复活测试 B"), start=1):
            source = store.upsert_episode(
                episode_kind=EPISODE_ACTION,
                evidence_refs=[f"action://event/revive-semantic-{index}"],
                primary_ref=f"action://event/revive-semantic-{index}",
                person_id="owner",
                session_id="qq_dm_owner",
                action="lightnovel_recommendation_delivery",
                result="COMPLETED",
                summary=f"我给 owner 推荐了轻小说：{title}。",
                salience=0.52,
                ts_ms=1_800_000_010_000 + index,
                metadata={
                    "selected_recommendations": [
                        {
                            "title": title,
                            "selection_reason": "恋爱日常",
                            "detail_url": f"https://www.lightnovel.fun/cn/detail/revive-{index}",
                        }
                    ]
                },
            )
            store.index_episode_collections(episode_id=source["episode_id"], ts_ms=1_800_000_010_000 + index)

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=20,
            ts_ms=1_800_000_020_000,
        )
        revived = ledger.get_semantic_memory_by_source_hash(source_hash)

        assert revived is not None
        assert revived["deleted_at"] is None
        assert revived["semantic_id"] != "sem_deleted_synthetic_recent"
        assert "复活测试 A" in revived["summary"]
        assert "复活测试 B" in revived["summary"]
        assert not any(
            item.get("reason_code") == "SEMANTIC_MEMORY_WRITE_FAILED"
            and item.get("semantic_key") == semantic_key
            for item in result["skipped"]
        )
    finally:
        ledger.close()


def test_forgetting_pass_keeps_semantic_memory_source_anchor_traceable() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old_ts = 1_800_000_000_000
        source = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/semantic-anchor-pref"],
            primary_ref="chat://request/semantic-anchor-pref",
            person_id="user-1",
            session_id="sess-1",
            summary="用户喜欢蓝色主题。",
            salience=0.05,
            memory_strength=0.05,
            decay_profile_id="FAST",
            ts_ms=old_ts,
        )
        distilled = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=old_ts + 100,
        )
        assert distilled["written_count"] == 1

        forgetting = store.apply_forgetting_pass(
            now_ts_ms_value=old_ts + 45 * 24 * 60 * 60 * 1000,
            cold_after_ms=1,
            gone_after_ms=1,
        )

        row = next(item for item in ledger.life_episode if item["episode_id"] == source["episode_id"])
        assert forgetting["semantic_reference_protected"] == 1
        assert row["retention_state"] == RETENTION_COLD
        assert row["forget_reason"] == "semantic_reference_protected"
        assert row["retention_state"] != RETENTION_GONE
    finally:
        ledger.close()


def test_sleep_replay_distills_failure_lesson_and_skips_test_probe() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        failure = store.write_background_episode(
            event_source="action",
            event_id="service-failure-1",
            task_id="startup_probe",
            status="FAILED",
            detail="Chroma 没起来会导致召回假测，业务实测前必须检查外部依赖 ready。",
            ts_ms=1_800_000_000_000,
        )
        store.write_chat_episode(
            request_id="req-life-e2e-probe",
            session_id="sess-1",
            person_id="user-1",
            user_message="记住这个测试偏好不应该进入长期记忆",
            assistant_reply="测试记录。",
            ts_ms=1_800_000_000_001,
            metadata={"e2e_probe": True},
        )

        result = MemorySleepReplayTask(store=store).run_once(
            now_ts_ms_value=1_800_000_000_200,
            replay_limit=6,
            forgetting_batch_limit=20,
            semantic_limit=6,
        )

        items, total = ledger.list_semantic_memories()
        assert result["semantic_distillation"]["written_count"] == 1
        assert total == 1
        assert "故障经验" in items[0]["summary"]
        assert "Chroma" in items[0]["summary"]
        assert items[0]["source_memory_ids"] == [f"life_episode::{failure['episode_id']}"]
        assert "测试偏好" not in items[0]["summary"]
    finally:
        ledger.close()


def test_sleep_replay_selection_skips_synthetic_probe_metadata() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        real = store.write_chat_episode(
            request_id="req-life-real-preference",
            session_id="sess-1",
            person_id="user-1",
            user_message="以后推荐轻小说时，简介后要换行，别模板腔。",
            assistant_reply="记住了。",
            ts_ms=1_800_000_000_000,
        )
        synthetic = store.write_chat_episode(
            request_id="req-life-e2e-preference-probe",
            session_id="surface-memory-recall-e2e-session",
            person_id="surface-memory-recall-e2e",
            user_message="以后这个测试偏好不要进入睡眠回放。",
            assistant_reply="测试记录。",
            ts_ms=1_800_000_000_001,
            metadata={"e2e_probe": True},
        )

        batch = MemorySleepReplayTask(store=store).select_replay_batch(limit=10)
        ids = {str(item.get("episode_id") or "") for item in batch}

        assert real["episode_id"] in ids
        assert synthetic["episode_id"] not in ids
    finally:
        ledger.close()


def test_consolidator_distills_operational_failure_without_report_noise() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        failure = store.write_background_episode(
            event_source="codex_production",
            event_id="codex-job-failure-1",
            person_id="owner",
            task_id="agent_lightnovel_recommender",
            status="FAILED",
            detail=(
                "Codex production job agent_lightnovel_recommender status=FAILED；"
                "summary=Native lightnovel recommender found no usable recommendation candidates.；"
                "reason=LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS；"
                "result_text=# 轻小说推荐报告\n\n- 来源数：2，可读来源：0\n- 推荐候选：0\n\n## 推荐列表"
            ),
            ts_ms=1_800_000_000_000,
        )
        same_failure = store.write_background_episode(
            event_source="codex_production",
            event_id="codex-job-failure-2",
            person_id="default",
            task_id="agent_lightnovel_recommender",
            status="FAILED",
            detail=(
                "Codex production job agent_lightnovel_recommender status=FAILED；"
                "summary=Native lightnovel recommender found no usable recommendation candidates.；"
                "reason=LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS；"
                "result_text=# 轻小说推荐报告\n\n- 来源数：2，可读来源：0\n- 推荐候选：0\n\n## 推荐列表"
            ),
            ts_ms=1_800_000_000_001,
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_200,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        assert "故障经验" in items[0]["summary"]
        assert "agent_lightnovel_recommender 失败" in items[0]["summary"]
        assert "LIGHTNOVEL_NATIVE_NO_RECOMMENDATIONS" in items[0]["summary"]
        assert "推荐列表" not in items[0]["summary"]
        assert "result_text" not in items[0]["summary"]
        assert set(items[0]["source_memory_ids"]) == {
            f"life_episode::{failure['episode_id']}",
            f"life_episode::{same_failure['episode_id']}",
        }
    finally:
        ledger.close()


def test_sleep_replay_does_not_reflect_low_value_delivery_logs() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        delivery = store.write_background_episode(
            event_source="codex_production",
            event_id="delivery-noise-1",
            task_id="agent_lightnovel_recommender",
            status="COMPLETED",
            detail="我刚才通过 qq 给用户推荐了 3 本轻小说；发送结果=sent；详情:https://www.lightnovel.fun/cn/detail/1",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        failure = store.write_background_episode(
            event_source="action",
            event_id="failure-should-replay-1",
            task_id="startup_probe",
            status="FAILED",
            detail="Gateway 连不上导致对话无法投递，后续必须先检查 edge 服务 ready。",
            ts_ms=1_800_000_000_010,
        )

        result = MemorySleepReplayTask(store=store).run_once(
            now_ts_ms_value=1_800_000_000_500,
            replay_limit=10,
            forgetting_batch_limit=20,
            semantic_limit=6,
        )

        reflection_episode_ids = {
            row["episode_id"]
            for row in ledger.life_evidence
            if row["evidence_kind"] == "reflection"
        }
        assert result["reflection_count"] >= 1
        assert failure["episode_id"] in reflection_episode_ids
        assert delivery["episode_id"] not in reflection_episode_ids
    finally:
        ledger.close()


def test_sleep_replay_does_not_reinforce_recommendation_followup_chat_noise() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        noisy = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/noisy-selection"],
            primary_ref="chat://request/noisy-selection",
            person_id="user-1",
            session_id="qq_dm_1",
            summary="User: 我问你喜欢哪本\nAssistant: 哈？最近在看一本讲宇宙和人的，挺有意思的。",
            salience=0.36,
            memory_strength=0.95,
            ts_ms=1_800_000_000_000,
        )
        durable = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/durable-pref"],
            primary_ref="chat://request/durable-pref",
            person_id="user-1",
            session_id="qq_dm_1",
            summary="用户要求：不要拟人化发送，推荐要按自然卡片格式。",
            salience=0.50,
            memory_strength=0.90,
            ts_ms=1_800_000_000_010,
        )

        result = MemorySleepReplayTask(store=store).run_once(
            now_ts_ms_value=1_800_000_000_500,
            replay_limit=10,
            forgetting_batch_limit=20,
            semantic_limit=6,
        )

        reflection_episode_ids = {
            row["episode_id"]
            for row in ledger.life_evidence
            if row["evidence_kind"] == "reflection"
        }
        assert result["reflection_count"] >= 1
        assert durable["episode_id"] in reflection_episode_ids
        assert noisy["episode_id"] not in reflection_episode_ids
    finally:
        ledger.close()


def test_sleep_replay_compacts_medium_old_scene_without_swallowing_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old_ts = 1_800_000_000_000
        source_ids: list[str] = []
        for index, summary in enumerate(
            [
                "后台检查轻小说网页，记录页面可访问。",
                "后台整理推荐候选，保留标题和链接。",
                "后台确认 QQ 推送格式，等待下一步发送。",
            ],
            start=1,
        ):
            row = store.upsert_episode(
                episode_kind="background_episode",
                evidence_refs=[f"scheduler://event/medium-scene-{index}"],
                primary_ref=f"scheduler://event/medium-scene-{index}",
                person_id="owner",
                session_id="qq_dm_owner",
                action="background_progress",
                result="progress",
                summary=summary,
                salience=0.34,
                memory_strength=0.44,
                retention_state=RETENTION_WARM,
                ts_ms=old_ts + index,
            )
            source_ids.append(row["episode_id"])
        preference = store.upsert_episode(
            episode_kind="background_episode",
            evidence_refs=["scheduler://event/medium-scene-preference"],
            primary_ref="scheduler://event/medium-scene-preference",
            person_id="owner",
            session_id="qq_dm_owner",
            action="background_progress",
            result="progress",
            summary="用户偏好：不要拟人化发送，推荐卡片要干净分段。",
            salience=0.34,
            memory_strength=0.44,
            retention_state=RETENTION_WARM,
            ts_ms=old_ts + 9,
        )
        recommendation_noise = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/recommendation-followup-noise"],
            primary_ref="chat://request/recommendation-followup-noise",
            person_id="owner",
            session_id="qq_dm_owner",
            summary="User: 刚才今日推荐的结果是什么？只说刚才那条结果。\nAssistant: 刚才推的是《百年孤独》。",
            salience=0.34,
            memory_strength=0.44,
            retention_state=RETENTION_WARM,
            ts_ms=old_ts + 10,
        )

        result = MemorySleepReplayTask(store=store).run_once(
            now_ts_ms_value=old_ts + 2 * 24 * 60 * 60 * 1000,
            replay_limit=4,
            forgetting_batch_limit=20,
            semantic_limit=4,
            compaction_group_limit=4,
        )

        assert result["compaction"]["group_count"] == 1
        assert result["compaction"]["episode_count"] == 3
        assert len(ledger.life_memory_compaction) == 1
        rows = {row["episode_id"]: row for row in ledger.life_episode}
        for episode_id in source_ids:
            assert rows[episode_id]["compressed_into_episode_id"] == result["compaction"]["groups"][0]["compressed_episode_id"]
            assert rows[episode_id]["retention_state"] == RETENTION_COLD
        assert rows[preference["episode_id"]]["compressed_into_episode_id"] is None
        assert rows[preference["episode_id"]]["retention_state"] != RETENTION_GONE
        assert rows[recommendation_noise["episode_id"]]["compressed_into_episode_id"] is None
        compressed = rows[result["compaction"]["groups"][0]["compressed_episode_id"]]
        assert "睡眠回放压缩" in compressed["summary"]
    finally:
        ledger.close()


def test_action_and_perception_events_create_life_anchors_with_retention_state() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)

        action = store.write_background_episode(
            event_source="skill",
            event_id="exec-1",
            request_id="req-skill-1",
            intent_id="intent-1",
            task_id="lightnovel_recommend",
            status="SUCCESS",
            detail="生成并发送了今日轻小说推荐。",
            ts_ms=1_800_000_000_000,
            metadata={"actor_id": "natsume", "action_kind": "skill_execution"},
        )
        perception = store.write_background_episode(
            event_source="vision",
            event_id="screen-1",
            request_id="req-vision-1",
            task_id="screen_ocr",
            status="OK",
            detail="屏幕 OCR 观察到用户正在查看 QQ 对话。",
            ts_ms=1_800_000_000_010,
        )

        rows = {row["episode_id"]: row for row in ledger.life_episode}
        assert rows[action["episode_id"]]["episode_kind"] == EPISODE_ACTION
        assert rows[perception["episode_id"]]["episode_kind"] == EPISODE_PERCEPTION
        assert rows[action["episode_id"]]["memory_strength"] > 0.0
        assert rows[action["episode_id"]]["retention_state"] in {"HOT", "WARM", "COLD"}
        assert any(row["source_ref"] == "skill://exec/exec-1" for row in ledger.life_evidence)
        assert any(row["source_ref"] == "vision://observation/screen-1" for row in ledger.life_evidence)
    finally:
        ledger.close()


def test_surface_memory_recall_action_creates_action_episode() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="deep-review-1",
            request_id="req-deep-review-1",
            person_id="owner",
            session_id="qq_dm_owner",
            task_id="lightnovel_recommendation_deep_review",
            status="OK",
            detail="我翻过这组轻小说的详情和正文。",
            ts_ms=1_800_000_000_020,
            metadata={"action_kind": "lightnovel_recommendation_deep_review"},
        )

        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        assert row["episode_kind"] == EPISODE_ACTION
        assert any(
            item["episode_id"] == written["episode_id"]
            and item["source_ref"] == "action://event/deep-review-1"
            and item["evidence_kind"] == "tool_result"
            for item in ledger.life_evidence
        )
    finally:
        ledger.close()


def test_consolidator_distills_lightnovel_deep_review_as_action_pattern() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        for index, title in enumerate(("恋爱日常 A", "皇女皇帝 B"), start=1):
            store.write_background_episode(
                event_source="surface_memory_recall",
                event_id=f"deep-review-pattern-{index}",
                request_id=f"req-deep-review-pattern-{index}",
                person_id="1234567890",
                session_id="qq_dm_1234567890",
                task_id="lightnovel_recommendation_deep_review",
                status="OK",
                reason_code="LIGHTNOVEL_DEEP_REVIEW_OK",
                detail=f"我翻过这组轻小说的详情和正文；当时更偏向第{index}本《{title}》，因为开篇比较抓人，人物互动更好接。",
                ts_ms=1_800_000_000_000 + index,
                metadata={
                    "action_kind": "lightnovel_recommendation_deep_review",
                    "domain_event_kind": "lightnovel_recommendation_deep_review",
                    "memory_anchor": f"我翻过这组轻小说的详情和正文；当时更偏向第{index}本《{title}》。",
                    "fact": f"网页深读结果（含正文）：当前更推荐第{index}本《{title}》。原因：开篇比较抓人；人物互动更好接。",
                    "selected": {
                        "title": title,
                        "body_quality": "READABLE_BODY",
                        "content_char_count": 1800 + index,
                        "body_observations": ["正文开篇已经进入故事叙事，不只是资料卡", "对白密度高，读感更偏轻快互动"],
                        "score_reasons": ["人物互动更好接", "章节线索也比较清楚"],
                    },
                    "items": [
                        {
                            "title": title,
                            "body_quality": "READABLE_BODY",
                            "body_observations": ["读到的正文偏恋爱日常，核心在人物关系和亲密感推进"],
                            "selection_reason": "恋爱日常",
                        }
                    ],
                },
            )
        degraded = store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="deep-review-pattern-degraded",
            request_id="req-deep-review-pattern-degraded",
            person_id="1234567890",
            session_id="qq_dm_1234567890",
            task_id="lightnovel_recommendation_deep_review",
            status="DEGRADED",
            reason_code="LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
            detail="我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
            ts_ms=1_800_000_000_010,
            metadata={
                "action_kind": "lightnovel_recommendation_deep_review",
                "domain_event_kind": "lightnovel_recommendation_deep_review",
                "memory_anchor": "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
                "fact": "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
                "reason_code": "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
            },
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 1
        assert total == 1
        semantic = items[0]
        assert semantic["distill_strategy"] == "life_action_pattern_rule_consolidation"
        assert "主动行动模式" in semantic["summary"]
        assert "详情页或正文" in semantic["summary"]
        assert "开篇是不是已经进入故事" in semantic["summary"]
        assert "对白和人物互动" in semantic["summary"]
        assert "证据不足" in semantic["summary"]
        assert "网页深读结果" not in semantic["summary"]
        assert "semantic_kind:action_pattern" in semantic["tags"]
        assert "action_pattern" in semantic["tags"]
        assert f"life_episode::{degraded['episode_id']}" in semantic["source_memory_ids"]
    finally:
        ledger.close()


def test_consolidator_does_not_distill_delivery_or_send_logs_as_action_pattern() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        for index in range(2):
            store.write_background_episode(
                event_source="channel",
                event_id=f"qq-send-log-{index}",
                person_id="owner",
                session_id="qq_dm_owner",
                task_id="qq_reply_sent",
                status="SENT",
                detail="发送了 text 消息；发送结果=sent；普通投递日志不应进入长期行动模式。",
                ts_ms=1_800_000_000_000 + index,
                metadata={"action_kind": "qq_reply_sent"},
            )
        store.write_background_episode(
            event_source="codex_production",
            event_id="delivery-no-action-pattern",
            person_id="owner",
            session_id="qq_dm_owner",
            task_id="lightnovel_recommendation_delivery",
            status="COMPLETED",
            detail="我刚才通过 qq 给用户推荐了 3 本轻小说；发送结果=sent。",
            ts_ms=1_800_000_000_010,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=20,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        items, total = ledger.list_semantic_memories()

        assert result["written_count"] == 0
        assert total == 0
        assert not any("action_pattern" in row["tags"] for row in items)
    finally:
        ledger.close()


def test_recall_reinforces_episode_but_gone_is_hidden_from_default_recall() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-recall-reinforce",
            session_id="sess-1",
            person_id="user-1",
            user_message="记住我喜欢轻小说推荐分段卡片",
            assistant_reply="我会按卡片分段推荐。",
            ts_ms=1_800_000_000_000,
        )

        packet = store.recall_episode_packet(
            query="轻小说 推荐 分段卡片",
            top_k=3,
            person_id="user-1",
            session_id="sess-1",
            now_ts_ms_value=1_800_000_000_100,
        )
        assert packet.candidates
        row = ledger._conn.execute(
            "SELECT recall_count, reinforcement_count FROM life_episode WHERE episode_id = ?",
            (written["episode_id"],),
        ).fetchone()
        assert int(row["recall_count"]) == 1
        assert int(row["reinforcement_count"]) == 1

        ledger._conn.execute(
            "UPDATE life_episode SET retention_state = ? WHERE episode_id = ?",
            (RETENTION_GONE, written["episode_id"]),
        )
        hidden = store.recall_episode_packet(
            query="轻小说 推荐 分段卡片",
            top_k=3,
            now_ts_ms_value=1_800_000_000_200,
        )
        assert hidden.candidates == []
    finally:
        ledger.close()


def test_recall_keyword_fetch_finds_action_episode_behind_inner_stream_noise() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old_recommendation = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/old-job:lightnovel_gateway_delivery"],
            primary_ref="action://event/old-job:lightnovel_gateway_delivery",
            summary=(
                "codex_production lightnovel_recommendation_delivery COMPLETED；"
                "我刚才通过 qq 给 qq_dm_owner 推荐了 3 本轻小说：1. 旧 A；2. 旧 B；3. 旧 C。"
            ),
            salience=0.55,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_799_999_900_000,
        )
        recommendation = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/job-1:lightnovel_gateway_delivery"],
            primary_ref="action://event/job-1:lightnovel_gateway_delivery",
            summary=(
                "codex_production lightnovel_recommendation_delivery COMPLETED；"
                "我刚才通过 qq 给 qq_dm_owner 推荐了 3 本轻小说：1. A；2. B；3. C。"
            ),
            salience=0.55,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
        )
        for index in range(80):
            store.upsert_episode(
                episode_kind="inner_episode",
                evidence_refs=[f"inner_world://event/noise-{index}"],
                primary_ref=f"inner_world://event/noise-{index}",
                summary="inner_world OK 先把话磨实一点，别让她觉得我只是在贴近。",
                salience=0.05,
                retention_state="WARM",
                truth_state="VERIFIED",
                ts_ms=1_800_000_000_100 + index,
            )

        packet = store.recall_episode_packet(
            query="这三本书 你喜欢哪本 刚才推荐 轻小说",
            top_k=3,
            now_ts_ms_value=1_800_000_001_000,
        )

        assert packet.candidates
        assert packet.candidates[0]["memory_id"] == recommendation["episode_id"]
        assert packet.candidates[0]["memory_id"] != old_recommendation["episode_id"]
        assert "推荐了 3 本轻小说" in packet.candidates[0]["text"]
    finally:
        ledger.close()


def test_anaphoric_selection_recall_prefers_recent_action_over_self_chat_echo() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        recommendation = store.upsert_episode(
            episode_kind=EPISODE_ACTION,
            evidence_refs=["action://event/job-1:lightnovel_gateway_delivery"],
            primary_ref="action://event/job-1:lightnovel_gateway_delivery",
            person_id="user-1",
            session_id="qq_dm_1",
            action="lightnovel_recommendation_delivery",
            summary=(
                "codex_production lightnovel_recommendation_delivery COMPLETED；"
                "我刚才通过 qq 给 qq_dm_1 推荐了 3 本轻小说：1. A；2. B；3. C。"
            ),
            salience=0.55,
            retention_state="HOT",
            truth_state="VERIFIED",
            ts_ms=1_800_000_000_000,
            metadata={"action_kind": "lightnovel_recommendation_delivery"},
        )
        store.write_chat_episode(
            request_id="req-wrong-self-echo",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="我问你喜欢哪本",
            assistant_reply="哈？最近在看一本讲宇宙和人的，挺有意思的。",
            ts_ms=1_800_000_000_300,
        )

        packet = store.recall_episode_packet(
            query="我问你喜欢哪本",
            top_k=3,
            person_id="user-1",
            session_id="qq_dm_1",
            now_ts_ms_value=1_800_000_000_600,
        )

        assert packet.candidates
        assert packet.candidates[0]["memory_id"] == recommendation["episode_id"]
        assert "推荐了 3 本轻小说" in packet.candidates[0]["text"]
        assert "哈？最近在看" not in packet.candidates[0]["text"]
    finally:
        ledger.close()


def test_forgetting_pass_demotes_trivial_old_episode_without_erasing_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        old_ts = 1_800_000_000_000
        trivial = store.upsert_episode(
            episode_kind="background_episode",
            evidence_refs=["scheduler://event/boring-progress"],
            summary="任务仍在运行，没有新变化。",
            salience=0.05,
            memory_strength=0.05,
            decay_profile_id="FAST",
            ts_ms=old_ts,
            metadata={"triviality_penalty": 0.05},
        )
        preference = store.upsert_episode(
            episode_kind="chat_episode",
            evidence_refs=["chat://request/preference-1"],
            summary="用户明确要求：不要拟人化发送，推荐内容按总览一段、每本书一卡片。",
            salience=0.20,
            ts_ms=old_ts,
            metadata={"user_importance": 0.18},
        )

        store.apply_forgetting_pass(
            now_ts_ms_value=old_ts + 45 * 24 * 60 * 60 * 1000,
            cold_after_ms=1,
            gone_after_ms=1,
        )

        rows = {
            row["episode_id"]: row
            for row in ledger.life_episode
            if row["episode_id"] in {trivial["episode_id"], preference["episode_id"]}
        }
        assert rows[trivial["episode_id"]]["retention_state"] == RETENTION_GONE
        assert rows[preference["episode_id"]]["retention_state"] != RETENTION_GONE
    finally:
        ledger.close()


def test_forgetting_pass_backfills_missing_reason_for_historical_gone_episode() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        gone = store.upsert_episode(
            episode_kind="inner_episode",
            evidence_refs=["inner_world://event/historical-gone"],
            primary_ref="inner_world://event/historical-gone",
            person_id="natsume_ai",
            summary="低价值历史内心流片段。",
            salience=0.01,
            memory_strength=0.01,
            retention_state=RETENTION_GONE,
            ts_ms=1_700_000_000_000,
        )

        result = store.apply_forgetting_pass(now_ts_ms_value=1_800_000_000_000, batch_limit=10)
        row = ledger._conn.execute(
            "SELECT retention_state, forget_reason FROM life_episode WHERE episode_id = ?",
            (gone["episode_id"],),
        ).fetchone()

        assert result["gone_reason_backfilled"] == 1
        assert row["retention_state"] == RETENTION_GONE
        assert row["forget_reason"] == "historical_low_value_retired"
    finally:
        ledger.close()


def test_life_episode_metadata_records_management_policy() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        preference = store.write_chat_episode(
            request_id="req-policy-preference",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            assistant_reply="记住了，后面按这个格式发。",
            ts_ms=1_800_000_000_000,
        )
        audit = store.write_background_episode(
            event_source="skill",
            event_id="audit-anchor-policy",
            task_id="audit_anchor",
            status="OK",
            detail="审计锚点：skill 执行日志已投递为 LifeMemory anchor。",
            ts_ms=1_800_000_000_001,
            metadata={"anchor_only": True},
        )

        rows = {
            row["episode_id"]: row
            for row in ledger.life_episode
            if row["episode_id"] in {preference["episode_id"], audit["episode_id"]}
        }
        preference_raw = rows[preference["episode_id"]]["metadata"]
        audit_raw = rows[audit["episode_id"]]["metadata"]
        preference_meta = preference_raw if isinstance(preference_raw, dict) else json.loads(preference_raw)
        audit_meta = audit_raw if isinstance(audit_raw, dict) else json.loads(audit_raw)

        assert preference_meta["memory_class"] == "preference"
        assert preference_meta["recall_grain"] == "semantic"
        assert preference_meta["memory_policy"]["semantic_candidate"] is True
        assert rows[preference["episode_id"]]["decay_profile_id"] == "STABLE"
        assert audit_meta["memory_class"] == "audit_anchor"
        assert audit_meta["recall_grain"] == "audit_only"
        assert audit_meta["memory_policy"]["active_recall_default"] is False
        assert rows[audit["episode_id"]]["decay_profile_id"] == "EPHEMERAL"
    finally:
        ledger.close()


def test_backfill_memory_policy_classifies_legacy_episode() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_background_episode(
            event_source="scheduler",
            event_id="legacy-deepseek-failure",
            task_id="llm_pool_call",
            status="FAILED",
            reason_code="DEEPSEEK_TIMEOUT",
            detail="DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
            ts_ms=1_800_000_000_000,
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ?, decay_profile_id = ? WHERE episode_id = ?",
                    ("{}", "NORMAL", written["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=10)
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        raw_meta = row["metadata"]
        metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)

        assert result["updated"] == 1
        assert result["class_counts"]["failure_lesson"] == 1
        assert metadata["memory_class"] == "failure_lesson"
        assert metadata["recall_grain"] == "semantic"
        assert row["decay_profile_id"] == "SLOW"
    finally:
        ledger.close()


def test_backfill_memory_policy_overwrites_stale_top_level_class() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_background_episode(
            event_source="codex_production",
            event_id="legacy-recommendation-report",
            task_id="agent_lightnovel_recommender",
            status="COMPLETED",
            detail="# 轻小说推荐报告\n推荐候选：4\n看点：命中偏好：恋爱日常。",
            ts_ms=1_800_000_000_000,
        )
        stale_metadata = {
            "memory_class": "preference",
            "recall_grain": "semantic",
            "memory_policy": {
                "schema": "Natsume.life_memory_policy.v1",
                "memory_class": "preference",
                "recall_grain": "semantic",
                "semantic_candidate": True,
            },
        }
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (json.dumps(stale_metadata, ensure_ascii=False), written["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=10, include_existing=True)
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        raw_meta = row["metadata"]
        metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)

        assert result["updated"] == 1
        assert result["class_counts"]["tool_action"] == 1
        assert metadata["memory_class"] == "tool_action"
        assert metadata["recall_grain"] == "episode"
        assert metadata["memory_policy"]["memory_class"] == "tool_action"
        assert metadata["memory_policy"]["semantic_candidate"] is False
    finally:
        ledger.close()


def test_backfill_memory_policy_dry_run_reports_transitions_without_writing() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-life-stale-self-change",
            session_id="qq_dm_owner",
            person_id="owner",
            user_message="不要打补丁，她所有做过的事都要进入记忆。",
            assistant_reply="明白，这是记忆系统设计约束。",
            ts_ms=1_800_000_000_000,
        )
        stale_metadata = {
            "memory_class": "preference",
            "recall_grain": "semantic",
            "memory_policy": {
                "schema": "Natsume.life_memory_policy.v1",
                "memory_class": "preference",
                "recall_grain": "semantic",
                "semantic_candidate": True,
            },
        }
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ?, decay_profile_id = ? WHERE episode_id = ?",
                    (json.dumps(stale_metadata, ensure_ascii=False), "STABLE", written["episode_id"]),
                )

        preview = store.backfill_memory_policy(
            batch_limit=10,
            include_existing=True,
            dry_run=True,
            sample_limit=5,
        )
        preview_row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        preview_meta = preview_row["metadata"] if isinstance(preview_row["metadata"], dict) else json.loads(preview_row["metadata"])

        applied = store.backfill_memory_policy(
            batch_limit=10,
            include_existing=True,
            dry_run=False,
            sample_limit=5,
        )
        applied_row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        applied_meta = applied_row["metadata"] if isinstance(applied_row["metadata"], dict) else json.loads(applied_row["metadata"])

        assert preview["dry_run"] is True
        assert preview["updated"] == 0
        assert preview["would_update"] == 1
        assert preview["transition_counts"] == {"preference->self_change": 1}
        assert preview["semantic_transition_counts"] == {"true->true": 1}
        assert preview["changes"][0]["old_memory_class"] == "preference"
        assert preview["changes"][0]["new_memory_class"] == "self_change"
        assert preview_meta["memory_class"] == "preference"
        assert applied["updated"] == 1
        assert applied["would_update"] == 1
        assert applied_meta["memory_class"] == "self_change"
        assert applied_meta["memory_policy"]["semantic_candidate"] is True
    finally:
        ledger.close()


def test_memory_compaction_reflection_is_not_self_change_or_semantic_candidate() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.upsert_episode(
            episode_kind=EPISODE_REFLECTION,
            evidence_refs=["reflection://memory_compaction/scene-1"],
            primary_ref="reflection://memory_compaction/scene-1",
            person_id="owner",
            session_id="qq_dm_owner",
            action="memory_compaction",
            result="OK",
            summary="睡眠回放压缩：把几条内心流整理成一个场景摘要。",
            salience=0.4,
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        result = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=10,
            max_records=5,
            ts_ms=1_800_000_000_100,
        )
        items, total = ledger.list_semantic_memories()

        assert metadata["memory_class"] == "reflection"
        assert metadata["recall_grain"] == "episode"
        assert metadata["memory_policy"]["semantic_candidate"] is False
        assert row["decay_profile_id"] == "SLOW"
        assert result["written_count"] == 0
        assert total == 0
        assert items == []
    finally:
        ledger.close()


def test_memory_policy_does_not_classify_selection_followup_as_user_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-selection-followup-not-preference",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="我问你喜欢哪本",
            assistant_reply="第二本吧，标题更对胃口。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        raw_meta = row["metadata"]
        metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)

        assert metadata["memory_class"] == "chat_experience"
        assert metadata["recall_grain"] == "episode"
        assert metadata["memory_policy"]["semantic_candidate"] is False

        stale_metadata = {
            **metadata,
            "memory_class": "preference",
            "recall_grain": "semantic",
            "memory_policy": {
                **metadata["memory_policy"],
                "memory_class": "preference",
                "recall_grain": "semantic",
                "semantic_candidate": True,
            },
        }
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (json.dumps(stale_metadata, ensure_ascii=False), written["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=10, include_existing=True)
        refreshed = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        refreshed_meta = refreshed["metadata"] if isinstance(refreshed["metadata"], dict) else json.loads(refreshed["metadata"])

        assert result["updated"] == 1
        assert result["class_counts"]["chat_experience"] == 1
        assert refreshed_meta["memory_class"] == "chat_experience"
        assert refreshed_meta["recall_grain"] == "episode"
        assert refreshed_meta["memory_policy"]["semantic_candidate"] is False
    finally:
        ledger.close()


def test_backfill_memory_policy_keeps_suppressed_source_out_of_semantic_candidates() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        wrong = store.write_chat_episode(
            request_id="req-suppressed-selection-source",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="我问你喜欢哪本",
            assistant_reply="第二本吧，标题更对胃口。",
            ts_ms=1_800_000_000_000,
        )
        correction = store.write_chat_episode(
            request_id="req-suppressed-selection-correction",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="不对，这个判断没有正文证据。",
            assistant_reply="你说得对，我撤回这个判断。",
            ts_ms=1_800_000_000_100,
        )
        store.correct_episode(
            episode_id=wrong["episode_id"],
            correction_kind="suppression",
            correction_episode_id=correction["episode_id"],
            suppress=True,
            detail="用户指出这次选书判断没有正文证据。",
            ts_ms=1_800_000_000_200,
        )
        stale_metadata = {
            "memory_class": "preference",
            "recall_grain": "semantic",
            "memory_policy": {
                "schema": "Natsume.life_memory_policy.v1",
                "memory_class": "preference",
                "recall_grain": "semantic",
                "semantic_candidate": True,
                "active_recall_default": True,
            },
        }
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (json.dumps(stale_metadata, ensure_ascii=False), wrong["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=10, include_existing=True)
        wrong_row = next(item for item in ledger.life_episode if item["episode_id"] == wrong["episode_id"])
        correction_row = next(item for item in ledger.life_episode if item["episode_id"] == correction["episode_id"])
        wrong_meta = wrong_row["metadata"] if isinstance(wrong_row["metadata"], dict) else json.loads(wrong_row["metadata"])
        correction_meta = correction_row["metadata"] if isinstance(correction_row["metadata"], dict) else json.loads(correction_row["metadata"])

        assert result["updated"] >= 1
        assert wrong_meta["memory_class"] == "correction"
        assert wrong_meta["recall_grain"] == "audit_only"
        assert wrong_meta["memory_policy"]["semantic_candidate"] is False
        assert wrong_meta["memory_policy"]["active_recall_default"] is False
        assert "corrected_or_suppressed_source" in wrong_meta["memory_policy"]["reason_codes"]
        assert correction_meta["memory_class"] == "correction"
        assert correction_meta["memory_policy"]["semantic_candidate"] is True
    finally:
        ledger.close()


def test_memory_policy_does_not_promote_memory_lookup_answer_to_relationship_feedback() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_chat_episode(
            request_id="req-lookup-echo-not-feedback",
            session_id="qq_dm_1",
            person_id="user-1",
            user_message="我之前说文本质量不像人话，是哪里的问题？",
            assistant_reply="你当时说的是节奏太像念稿子，缺一点自然停顿。",
            ts_ms=1_800_000_000_000,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        metadata = row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"])

        assert metadata["memory_class"] == "chat_experience"
        assert metadata["recall_grain"] == "episode"
        assert metadata["memory_policy"]["semantic_candidate"] is False

        stale_metadata = {
            **metadata,
            "memory_class": "relationship_feedback",
            "recall_grain": "semantic",
            "memory_policy": {
                **metadata["memory_policy"],
                "memory_class": "relationship_feedback",
                "recall_grain": "semantic",
                "semantic_candidate": True,
            },
        }
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (json.dumps(stale_metadata, ensure_ascii=False), written["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=10, include_existing=True)
        refreshed = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        refreshed_meta = refreshed["metadata"] if isinstance(refreshed["metadata"], dict) else json.loads(refreshed["metadata"])

        assert result["updated"] == 1
        assert refreshed_meta["memory_class"] == "chat_experience"
        assert refreshed_meta["memory_policy"]["semantic_candidate"] is False
    finally:
        ledger.close()


def test_backfill_memory_policy_keeps_inner_life_from_becoming_user_preference() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        written = store.write_background_episode(
            event_source="inner_world",
            event_id="inner-not-user-rule",
            task_id="inner_world_monologue_tick",
            status="OK",
            detail="先把话说准一点，别用空话糊过去。",
            ts_ms=1_800_000_000_000,
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    (json.dumps({"memory_class": "preference"}, ensure_ascii=False), written["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=10, include_existing=True)
        row = next(item for item in ledger.life_episode if item["episode_id"] == written["episode_id"])
        raw_meta = row["metadata"]
        metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)

        assert result["updated"] == 1
        assert result["class_counts"]["inner_life"] == 1
        assert metadata["memory_class"] == "inner_life"
        assert metadata["memory_policy"]["semantic_candidate"] is False
    finally:
        ledger.close()


def test_backfill_memory_policy_scans_missing_policy_rows_not_only_oldest_batch() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        existing = store.write_background_episode(
            event_source="scheduler",
            event_id="already-classified",
            task_id="health_check",
            status="OK",
            detail="健康检查 OK。",
            ts_ms=1_800_000_000_000,
        )
        missing = store.write_background_episode(
            event_source="scheduler",
            event_id="missing-policy",
            task_id="gateway_probe",
            status="FAILED",
            detail="Gateway 连不上导致对话无法投递。",
            ts_ms=1_800_000_000_001,
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ? WHERE episode_id = ?",
                    ("{}", missing["episode_id"]),
                )

        result = store.backfill_memory_policy(batch_limit=1)
        rows = {row["episode_id"]: row for row in ledger.life_episode}
        raw_meta = rows[missing["episode_id"]]["metadata"]
        metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)

        assert result["updated"] == 1
        assert result["skipped_existing"] == 0
        assert existing["episode_id"] in rows
        assert metadata["memory_class"] == "failure_lesson"
    finally:
        ledger.close()


def test_sleep_replay_backfills_missing_memory_policy_before_lifecycle_passes() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        failure = store.write_background_episode(
            event_source="scheduler",
            event_id="legacy-gateway-failure",
            task_id="gateway_probe",
            status="FAILED",
            detail="Gateway 连不上导致对话无法投递，后续必须先检查 edge 服务 ready。",
            ts_ms=1_800_000_000_000,
        )
        with ledger._write_lock:
            with ledger._conn:
                ledger._conn.execute(
                    "UPDATE life_episode SET metadata = ?, decay_profile_id = ? WHERE episode_id = ?",
                    ("{}", "NORMAL", failure["episode_id"]),
                )

        result = MemorySleepReplayTask(store=store).run_once(
            now_ts_ms_value=1_800_000_000_200,
            replay_limit=2,
            policy_backfill_limit=10,
            forgetting_batch_limit=10,
            semantic_limit=2,
        )
        row = next(item for item in ledger.life_episode if item["episode_id"] == failure["episode_id"])
        raw_meta = row["metadata"]
        metadata = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)

        assert result["policy_backfill"]["updated"] == 1
        assert result["policy_backfill"]["class_counts"]["failure_lesson"] == 1
        assert metadata["memory_class"] == "failure_lesson"
        assert metadata["recall_grain"] == "semantic"
        assert row["decay_profile_id"] == "SLOW"
    finally:
        ledger.close()


def test_compaction_keeps_summary_episode_and_marks_sources_cold_or_gone() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        first = store.write_background_episode(
            event_source="gateway",
            event_id="reply-1",
            task_id="qq_reply_sent",
            status="COMPLETED",
            detail="我给用户推荐了 3 本轻小说。",
            ts_ms=1_800_000_000_000,
        )
        second = store.write_background_episode(
            event_source="chat",
            event_id="followup-1",
            task_id="followup",
            status="OK",
            detail="用户追问哪本好看，是在测试我是否记得刚才做过推荐。",
            ts_ms=1_800_000_000_001,
        )

        result = store.compact_episodes(
            source_episode_ids=[first["episode_id"], second["episode_id"]],
            summary="5 月 27 日我给用户推荐了 3 本轻小说，随后用户用追问测试记忆连续性。",
            kept_facts=["推荐了 3 本轻小说", "用户关心记忆连续性"],
            dropped_details=["热度数字", "更新时间"],
            ts_ms=1_800_000_000_100,
        )

        assert result["status"] == "OK"
        assert len(ledger.life_memory_compaction) == 1
        source_rows = {
            row["episode_id"]: row
            for row in ledger.life_episode
            if row["episode_id"] in {first["episode_id"], second["episode_id"]}
        }
        assert all(row["compressed_into_episode_id"] == result["compressed_episode_id"] for row in source_rows.values())
        assert all(row["retention_state"] in {"COLD", "GONE"} for row in source_rows.values())
    finally:
        ledger.close()


def test_ledger_skill_exec_creates_action_anchor_without_legacy_memory() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        ledger.log_skill_exec(
            {
                "request_id": "req-skill-anchor",
                "intent_id": "intent-skill-anchor",
                "event_id": "exec-anchor-1",
                "skill_name": "daily_recommendation",
                "result_status": "SUCCESS",
                "result_code": "OK",
                "result_digest": "发送了今日推荐。",
                "evidence_ref": "skill_exec::req-skill-anchor::daily_recommendation",
                "ts_ms": 1_800_000_000_000,
            }
        )

        assert len(ledger.skill_exec_log) == 1
        assert len(ledger.life_episode) == 1
        episode = ledger.life_episode[0]
        assert episode["episode_kind"] == EPISODE_ACTION
        assert episode["action"] == "daily_recommendation"
        assert episode["retention_state"] in {"HOT", "WARM", "COLD"}
        assert any(row["source_ref"] == "skill://exec/exec-anchor-1" for row in ledger.life_evidence)
    finally:
        ledger.close()


def test_existing_life_episode_table_migrates_retention_columns_before_index(tmp_path) -> None:
    db_path = tmp_path / "old_life.sqlite3"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            CREATE TABLE life_episode (
                episode_id TEXT PRIMARY KEY,
                episode_kind TEXT NOT NULL,
                thread_id TEXT,
                person_id TEXT,
                session_id TEXT,
                primary_ref TEXT,
                summary TEXT,
                salience REAL NOT NULL DEFAULT 0.0,
                truth_state TEXT NOT NULL DEFAULT 'CANDIDATE',
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                created_ts_ms INTEGER NOT NULL,
                last_touched_ts_ms INTEGER NOT NULL,
                metadata TEXT
            );
            INSERT INTO life_episode (
                episode_id, episode_kind, summary, salience, truth_state,
                status, created_ts_ms, last_touched_ts_ms
            )
            VALUES ('ep_old', 'chat_episode', '旧经历', 0.42, 'VERIFIED', 'ACTIVE', 1, 1);
            """
        )
    finally:
        conn.close()

    ledger = SQLiteLedger(db_path=str(db_path))
    try:
        row = ledger._conn.execute(
            "SELECT memory_strength, retention_state FROM life_episode WHERE episode_id = 'ep_old'"
        ).fetchone()
        assert row is not None
        assert row["memory_strength"] >= 0.42
        assert row["retention_state"] == RETENTION_WARM
    finally:
        ledger.close()
