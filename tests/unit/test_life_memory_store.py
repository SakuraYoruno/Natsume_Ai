from __future__ import annotations

import json
import sqlite3

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import (
    EPISODE_ACTION,
    EPISODE_PERCEPTION,
    LifeMemoryConsolidator,
    MemorySleepReplayTask,
    LifeMemoryStore,
    RETENTION_COLD,
    RETENTION_GONE,
    RETENTION_WARM,
    TRUTH_CORRECTED,
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
        assert items[0]["distill_strategy"] == "life_episode_rule_consolidation"
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
        assert len(ledger.life_memory_collection_item) == 9

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


def test_sleep_replay_does_not_reflect_low_value_delivery_logs() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        delivery = store.write_background_episode(
            event_source="codex_production",
            event_id="delivery-noise-1",
            task_id="codex_lightnovel_recommender",
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
