#!/usr/bin/env python
"""Run deterministic LifeMemory lifecycle scenarios.

This batch proves the digital-life memory system can remember, consolidate,
forget low-value details, and physically prune expired audit-only anchors
without losing durable user facts or collection indexes.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.observability_persist import persist_life_action_event_memory  # noqa: E402
from host_brain.audit.retention import RetentionPolicy, run_retention_policy  # noqa: E402
from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import (  # noqa: E402
    LifeMemoryConsolidator,
    LifeMemoryStore,
    MemorySleepReplayTask,
    RETENTION_COLD,
    RETENTION_GONE,
    RETENTION_WARM,
)


CN_TZ = timezone(timedelta(hours=8))
OLD_TS = int(datetime(2026, 5, 1, 8, tzinfo=CN_TZ).timestamp() * 1000)
NOW_TS = OLD_TS + 45 * 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class LifecycleCaseResult:
    case_id: str
    passed: bool
    checks: dict[str, bool]
    detail: dict[str, Any]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic LifeMemory lifecycle scenarios.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_lifecycle_scenario_batch()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0 if report["success"] else 2


def run_lifecycle_scenario_batch() -> dict[str, Any]:
    cases: list[LifecycleCaseResult] = []
    for case_id, fn in (
        ("forgetting_demotes_low_value_without_erasing_preference", _case_forgetting_demotes_low_value),
        ("semantic_anchor_survives_forgetting", _case_semantic_anchor_survives_forgetting),
        ("sleep_replay_compacts_scene_without_swallowing_preference", _case_sleep_replay_compacts_scene),
        ("retention_prunes_expired_gone_and_refreshes_indexes", _case_retention_prunes_and_refreshes_indexes),
        ("gone_episode_hidden_from_default_recall", _case_gone_episode_hidden_from_recall),
        ("sleep_replay_distills_failure_not_delivery_noise", _case_sleep_replay_distills_failure_not_delivery_noise),
        ("policy_backfill_classifies_memory_management_lanes", _case_policy_backfill_classifies_memory_management_lanes),
    ):
        cases.append(_run_case(case_id, fn))
    return {
        "schema": "Natsume.life_memory_lifecycle_scenario_batch.v1",
        "success": all(case.passed for case in cases),
        "case_count": len(cases),
        "passed_count": sum(1 for case in cases if case.passed),
        "failed_count": sum(1 for case in cases if not case.passed),
        "cases": [asdict(case) for case in cases],
    }


def _run_case(case_id: str, fn: Callable[[LifeMemoryStore, SQLiteLedger], tuple[dict[str, bool], dict[str, Any]]]) -> LifecycleCaseResult:
    with tempfile.TemporaryDirectory(prefix=f"life_memory_lifecycle_{case_id}_") as temp_dir:
        ledger = SQLiteLedger(db_path=Path(temp_dir) / "lifecycle.sqlite3")
        try:
            store = LifeMemoryStore(ledger=ledger)
            checks, detail = fn(store, ledger)
            return LifecycleCaseResult(
                case_id=case_id,
                passed=all(checks.values()),
                checks=checks,
                detail=detail,
            )
        finally:
            ledger.close()


def _case_forgetting_demotes_low_value(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    trivial = store.write_background_episode(
        event_source="scheduler",
        event_id="boring-progress",
        task_id="background_progress",
        status="OK",
        detail="任务仍在运行，没有新变化。",
        ts_ms=OLD_TS,
        metadata={"triviality_penalty": 0.05},
    )
    preference = store.write_chat_episode(
        request_id="pref-card-format",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="以后推荐内容按总览一段、每本书一卡片，不要拟人化发送。",
        assistant_reply="记住了，后面按这个格式发。",
        ts_ms=OLD_TS + 1,
    )
    _force_memory_state(ledger, trivial["episode_id"], salience=0.05, strength=0.05, retention=RETENTION_WARM)

    forgetting = store.apply_forgetting_pass(now_ts_ms_value=NOW_TS, cold_after_ms=1, gone_after_ms=1)
    rows = _episode_rows_by_id(ledger, [trivial["episode_id"], preference["episode_id"]])
    checks = {
        "trivial_marked_gone": rows[trivial["episode_id"]]["retention_state"] == RETENTION_GONE,
        "preference_not_gone": rows[preference["episode_id"]]["retention_state"] != RETENTION_GONE,
        "forgetting_scanned": int(forgetting.get("scanned") or 0) >= 2,
    }
    return checks, {"forgetting": forgetting, "rows": rows}


def _case_semantic_anchor_survives_forgetting(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    source = store.write_chat_episode(
        request_id="semantic-anchor-pref",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="记住我喜欢蓝色主题。",
        assistant_reply="记住了，偏冷色一点。",
        ts_ms=OLD_TS,
    )
    _force_memory_state(ledger, source["episode_id"], salience=0.05, strength=0.05, retention=RETENTION_WARM)
    distilled = LifeMemoryConsolidator(store=store).distill_semantic_memories(
        limit=10,
        max_records=5,
        ts_ms=OLD_TS + 100,
    )
    forgetting = store.apply_forgetting_pass(now_ts_ms_value=NOW_TS, cold_after_ms=1, gone_after_ms=1)
    row = _episode_rows_by_id(ledger, [source["episode_id"]])[source["episode_id"]]
    checks = {
        "semantic_written": int(distilled.get("written_count") or 0) == 1,
        "semantic_reference_protected": int(forgetting.get("semantic_reference_protected") or 0) == 1,
        "source_demoted_only_to_cold": row["retention_state"] == RETENTION_COLD,
        "source_not_gone": row["retention_state"] != RETENTION_GONE,
    }
    return checks, {"distilled": distilled, "forgetting": forgetting, "row": row}


def _case_sleep_replay_compacts_scene(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    source_ids: list[str] = []
    for index, detail in enumerate(
        (
            "后台检查轻小说网页，记录页面可访问。",
            "后台整理推荐候选，保留标题和链接。",
            "后台确认 QQ 推送格式，等待下一步发送。",
        ),
        start=1,
    ):
        row = store.write_background_episode(
            event_source="scheduler",
            event_id=f"compact-scene-{index}",
            task_id="background_progress",
            person_id="owner",
            session_id="qq_dm_owner",
            status="OK",
            detail=detail,
            ts_ms=OLD_TS + index,
        )
        source_ids.append(row["episode_id"])
        _force_memory_state(ledger, row["episode_id"], salience=0.34, strength=0.44, retention=RETENTION_WARM)
    preference = store.write_chat_episode(
        request_id="compact-scene-pref",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="推荐卡片要干净分段。",
        assistant_reply="记住了。",
        ts_ms=OLD_TS + 9,
    )
    _force_memory_state(ledger, preference["episode_id"], salience=0.34, strength=0.44, retention=RETENTION_WARM)

    result = MemorySleepReplayTask(store=store).run_once(
        now_ts_ms_value=OLD_TS + 2 * 24 * 60 * 60 * 1000,
        replay_limit=4,
        forgetting_batch_limit=20,
        semantic_limit=4,
        compaction_group_limit=4,
    )
    groups = list(result.get("compaction", {}).get("groups") or [])
    compressed_id = str(groups[0].get("compressed_episode_id") or "") if groups else ""
    rows = _episode_rows_by_id(ledger, [*source_ids, preference["episode_id"], compressed_id])
    checks = {
        "one_compaction_group": int(result["compaction"]["group_count"]) == 1,
        "three_sources_compacted": int(result["compaction"]["episode_count"]) == 3,
        "compaction_row_written": len(ledger.life_memory_compaction) == 1,
        "sources_point_to_summary": bool(compressed_id) and all(rows[eid]["compressed_into_episode_id"] == compressed_id for eid in source_ids),
        "sources_demoted": all(rows[eid]["retention_state"] == RETENTION_COLD for eid in source_ids),
        "preference_not_compacted": rows[preference["episode_id"]]["compressed_into_episode_id"] is None,
        "summary_episode_exists": bool(compressed_id) and "睡眠回放压缩" in rows.get(compressed_id, {}).get("summary", ""),
    }
    return checks, {"run_result": result, "rows": rows}


def _case_retention_prunes_and_refreshes_indexes(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    written = persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id="old-lightnovel-delivery",
        actor_id="natsume",
        request_id="req-old-lightnovel-delivery",
        intent_id="codex_lightnovel_recommender",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary="我给 qq_dm_owner 推荐了 3 本轻小说。",
        ts_ms=OLD_TS,
        evidence_refs=["gateway://message/old/overview", "gateway://message/old/card/1"],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "selected_recommendations": [
                {"title": "旧推荐 A", "selection_reason": "恋爱日常", "intro_snippet": "人物关系轻快。", "detail_url": "https://www.lightnovel.fun/cn/detail/old-a"},
                {"title": "旧推荐 B", "selection_reason": "奇幻身份", "intro_snippet": "设定钩子明显。", "detail_url": "https://www.lightnovel.fun/cn/detail/old-b"},
                {"title": "旧推荐 C", "selection_reason": "皇女皇帝线", "intro_snippet": "路线直给。", "detail_url": "https://www.lightnovel.fun/cn/detail/old-c"},
            ],
        },
    )
    episode_id = str(written["life_memory"]["episode_id"])
    _force_memory_state(ledger, episode_id, salience=0.05, strength=0.05, retention=RETENTION_COLD)

    result = run_retention_policy(
        ledger=ledger,
        policy=RetentionPolicy(
            policy_profile_id="life_memory_lifecycle_batch",
            keep_ms_by_table={},
            max_rows_by_table={},
            life_memory_keep_ms=100,
            life_memory_min_salience=0.40,
            life_memory_episode_kinds=("action_episode",),
        ),
        request_id="req-life-memory-lifecycle-retention",
        now_ts_ms=NOW_TS,
    )
    episode_ids = {row["episode_id"] for row in ledger.life_episode}
    collections = ledger.life_memory_collection
    entities = ledger.life_memory_entity
    checks = {
        "candidate_count_one": int(result.life_memory_candidate_count) == 1,
        "deleted_count_one": int(result.life_memory_deleted_count) == 1,
        "episode_physically_deleted": episode_id not in episode_ids,
        "collection_items_removed": len(ledger.life_memory_collection_item) == 0,
        "collections_gone": bool(collections) and all(row["retention_state"] == RETENTION_GONE and int(row["item_count"] or 0) == 0 for row in collections),
        "entity_mentions_removed": len(ledger.life_memory_entity_mention) == 0,
        "entities_gone": bool(entities) and all(row["retention_state"] == RETENTION_GONE and int(row["mention_count"] or 0) == 0 for row in entities),
    }
    return checks, {"retention": result.as_dict(), "collections": collections, "entities": entities}


def _case_gone_episode_hidden_from_recall(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    written = store.write_chat_episode(
        request_id="gone-hidden-pref",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="记住我喜欢轻小说推荐分段卡片。",
        assistant_reply="会按卡片分段推荐。",
        ts_ms=OLD_TS,
    )
    with ledger._write_lock:
        with ledger._conn:
            ledger._conn.execute(
                "UPDATE life_episode SET retention_state = ? WHERE episode_id = ?",
                (RETENTION_GONE, written["episode_id"]),
            )
    packet = store.recall_episode_packet(
        query="轻小说 推荐 分段卡片",
        top_k=3,
        person_id="owner",
        session_id="qq_dm_owner",
        now_ts_ms_value=OLD_TS + 100,
    )
    checks = {
        "no_default_recall_candidates": packet.candidates == [],
        "episode_still_auditable": any(row["episode_id"] == written["episode_id"] for row in ledger.life_episode),
    }
    return checks, {"candidate_count": len(packet.candidates), "episode_id": written["episode_id"]}


def _case_sleep_replay_distills_failure_not_delivery_noise(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    delivery = store.write_background_episode(
        event_source="codex_production",
        event_id="delivery-noise",
        task_id="codex_lightnovel_recommender",
        status="COMPLETED",
        detail="我刚才通过 qq 给用户推荐了 3 本轻小说；发送结果=sent；详情:https://www.lightnovel.fun/cn/detail/1",
        ts_ms=OLD_TS,
        metadata={"action_kind": "lightnovel_recommendation_delivery"},
    )
    failure = store.write_background_episode(
        event_source="action",
        event_id="gateway-failure",
        task_id="startup_probe",
        status="FAILED",
        detail="Gateway 连不上导致对话无法投递，后续必须先检查 edge 服务 ready。",
        ts_ms=OLD_TS + 1,
    )
    result = MemorySleepReplayTask(store=store).run_once(
        now_ts_ms_value=OLD_TS + 500,
        replay_limit=10,
        forgetting_batch_limit=20,
        semantic_limit=6,
    )
    semantic_items, total = ledger.list_semantic_memories()
    reflection_episode_ids = {
        row["episode_id"]
        for row in ledger.life_evidence
        if row["evidence_kind"] == "reflection"
    }
    summaries = "\n".join(str(item.get("summary") or "") for item in semantic_items)
    checks = {
        "semantic_written": total == 1,
        "failure_semantic_present": "故障经验" in summaries and "Gateway" in summaries,
        "delivery_noise_absent_from_semantic": "发送结果=sent" not in summaries and "lightnovel.fun" not in summaries,
        "failure_reflected": failure["episode_id"] in reflection_episode_ids,
        "delivery_not_reflected": delivery["episode_id"] not in reflection_episode_ids,
    }
    return checks, {"run_result": result, "semantic_items": semantic_items, "reflection_episode_ids": sorted(reflection_episode_ids)}


def _case_policy_backfill_classifies_memory_management_lanes(store: LifeMemoryStore, ledger: SQLiteLedger) -> tuple[dict[str, bool], dict[str, Any]]:
    preference = store.write_chat_episode(
        request_id="policy-backfill-pref",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
        assistant_reply="记住了。",
        ts_ms=OLD_TS,
    )
    failure = store.write_background_episode(
        event_source="scheduler",
        event_id="policy-backfill-failure",
        task_id="llm_pool_call",
        status="FAILED",
        reason_code="DEEPSEEK_TIMEOUT",
        detail="DeepSeek 失败原因：供应商超时。",
        ts_ms=OLD_TS + 1,
    )
    audit = store.write_background_episode(
        event_source="scheduler",
        event_id="policy-backfill-audit",
        task_id="health_check",
        status="OK",
        detail="健康检查 OK。",
        ts_ms=OLD_TS + 2,
        metadata={"anchor_only": True},
    )
    with ledger._write_lock:
        with ledger._conn:
            ledger._conn.executemany(
                "UPDATE life_episode SET metadata = ?, decay_profile_id = ? WHERE episode_id = ?",
                [
                    ("{}", "NORMAL", preference["episode_id"]),
                    ("{}", "NORMAL", failure["episode_id"]),
                    (json.dumps({"anchor_only": True}, ensure_ascii=False), "NORMAL", audit["episode_id"]),
                ],
            )

    result = store.backfill_memory_policy(batch_limit=10)
    rows = _episode_rows_by_id(ledger, [preference["episode_id"], failure["episode_id"], audit["episode_id"]])

    def memory_class(episode_id: str) -> str:
        metadata = rows[episode_id]["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return str((metadata or {}).get("memory_class") or "")

    checks = {
        "updated_three": int(result.get("updated") or 0) == 3,
        "preference_classified": memory_class(preference["episode_id"]) == "preference",
        "failure_classified": memory_class(failure["episode_id"]) == "failure_lesson",
        "audit_classified": memory_class(audit["episode_id"]) == "audit_anchor",
        "preference_stable": rows[preference["episode_id"]]["decay_profile_id"] == "STABLE",
        "failure_slow": rows[failure["episode_id"]]["decay_profile_id"] == "SLOW",
        "audit_ephemeral": rows[audit["episode_id"]]["decay_profile_id"] == "EPHEMERAL",
    }
    return checks, {"backfill": result, "rows": rows}


def _force_memory_state(ledger: SQLiteLedger, episode_id: str, *, salience: float, strength: float, retention: str) -> None:
    with ledger._write_lock:
        with ledger._conn:
            ledger._conn.execute(
                """
                UPDATE life_episode
                SET salience = ?, base_salience = ?, memory_strength = ?, retention_state = ?
                WHERE episode_id = ?
                """,
                (float(salience), float(salience), float(strength), str(retention), str(episode_id)),
            )


def _episode_rows_by_id(ledger: SQLiteLedger, episode_ids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = set(episode_ids)
    return {row["episode_id"]: dict(row) for row in ledger.life_episode if row["episode_id"] in wanted}


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Life memory lifecycle scenario batch: {'PASS' if report['success'] else 'FAIL'}",
        f"Cases: {report['passed_count']}/{report['case_count']} passed",
    ]
    for item in report["cases"]:
        mark = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- {mark} {item['case_id']}")
        if not item["passed"]:
            failed = [name for name, ok in item["checks"].items() if not ok]
            lines.append(f"  failed checks: {', '.join(failed)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
