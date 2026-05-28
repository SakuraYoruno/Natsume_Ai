from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.observability_persist import (  # noqa: E402
    persist_background_event_memory,
    persist_life_action_event_memory,
    persist_life_chat_episode,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryStore  # noqa: E402
from scripts.runtime.audit_life_memory_coverage import build_coverage_report  # noqa: E402


CN_TZ = timezone(timedelta(hours=8))
REQUIRED_ENTRYPOINTS = (
    "surface_chat",
    "gateway_input_chat",
    "gateway_reply_action",
    "codex_lightnovel_delivery",
    "scheduler_background",
    "inner_world",
    "perception_input",
    "generic_tool_action",
    "audit_anchor",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic LifeMemory entrypoint coverage scenario batch.")
    parser.add_argument("--json", action="store_true", help="Print full JSON instead of a compact text report.")
    args = parser.parse_args(argv)
    report = run_entrypoint_scenario_batch()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0 if report["success"] else 2


def run_entrypoint_scenario_batch() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="life_memory_entrypoint_batch_") as temp_dir:
        ledger = SQLiteLedger(db_path=Path(temp_dir) / "life_memory_entrypoints.sqlite3")
        try:
            store = LifeMemoryStore(ledger=ledger)
            _seed_entrypoints(store=store)
            coverage = build_coverage_report(
                ledger._conn,
                include_synthetic=True,
                required_entrypoints=REQUIRED_ENTRYPOINTS,
                sample_limit=20,
            )
            summary = dict(coverage.get("summary") or {})
            success = (
                int(summary.get("critical_issue_count") or 0) == 0
                and int(summary.get("collection_critical_issue_count") or 0) == 0
                and int(summary.get("entrypoint_critical_issue_count") or 0) == 0
                and all(_entrypoint_count(coverage, entrypoint) > 0 for entrypoint in REQUIRED_ENTRYPOINTS)
            )
            return {
                "schema": "Natsume.life_memory_entrypoint_scenario_batch.v1",
                "success": success,
                "required_entrypoints": list(REQUIRED_ENTRYPOINTS),
                "entrypoint_counts": coverage.get("entrypoint_counts") or [],
                "summary": summary,
                "issue_counts": coverage.get("issue_counts") or {},
                "collection_issue_counts": coverage.get("collection_issue_counts") or {},
                "entrypoint_issue_counts": coverage.get("entrypoint_issue_counts") or {},
                "issues": coverage.get("issues") or [],
                "collection_issues": coverage.get("collection_issues") or [],
                "entrypoint_issues": coverage.get("entrypoint_issues") or [],
            }
        finally:
            ledger.close()


def _seed_entrypoints(*, store: LifeMemoryStore) -> None:
    common_meta = {"synthetic_probe": True, "scenario_batch": "life_memory_entrypoint"}
    persist_life_chat_episode(
        request_id="req-entry-surface-chat",
        intent_id="daily_chat",
        person_id="owner",
        session_id="desktop_main",
        user_message="今天继续做记忆系统验收。",
        assistant_reply="我会先检查入口覆盖。",
        ts_ms=_ts(29, 9),
        life_memory_store=store,
        metadata={**common_meta, "entrypoint_probe": "surface_chat"},
    )
    persist_life_chat_episode(
        request_id="msg-entry-gateway-in",
        intent_id="daily_chat",
        person_id="owner",
        session_id="qq_dm_owner",
        user_message="QQ 上问一下昨天推荐的书。",
        assistant_reply="我先查昨天的推荐集合。",
        ts_ms=_ts(29, 9, 1),
        life_memory_store=store,
        metadata={
            **common_meta,
            "entrypoint_probe": "gateway_input_chat",
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "reply_channel": "qq",
            "gateway_message_id": "msg-entry-gateway-in",
        },
    )
    persist_life_action_event_memory(
        event_source="gateway",
        action_kind="qq_reply_sent",
        action_id="msg-entry-gateway-out",
        actor_id="natsume",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary="我通过 QQ 回复了用户的追问。",
        evidence_refs=["gateway://message/msg-entry-gateway-out"],
        life_memory_store=store,
        episodic_store=None,
        metadata={**common_meta, "entrypoint_probe": "gateway_reply_action", "platform": "qq", "conversation_id": "qq_dm_owner"},
    )
    persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id="job-entry-lightnovel-delivery",
        actor_id="natsume",
        request_id="req-entry-lightnovel",
        intent_id="codex_lightnovel_recommender",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary="我通过 QQ 给用户推荐了 3 本轻小说。",
        evidence_refs=[
            "gateway://message/job-entry-lightnovel/overview",
            "gateway://message/job-entry-lightnovel/card/1",
            "gateway://message/job-entry-lightnovel/card/2",
            "gateway://message/job-entry-lightnovel/card/3",
        ],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            **common_meta,
            "entrypoint_probe": "codex_lightnovel_delivery",
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "selected_recommendations": _cards(),
        },
    )
    persist_background_event_memory(
        event_source="scheduler",
        event_id="sched-entry-daily",
        request_id="sched-entry-daily",
        intent_id="daily_scheduler",
        task_id="daily_recommendation",
        status="ERROR",
        reason_code="PROVIDER_TIMEOUT",
        detail="每日推荐任务 provider 超时。",
        ts_ms=_ts(29, 9, 5),
        life_memory_store=store,
        episodic_store=None,
        metadata={**common_meta, "entrypoint_probe": "scheduler_background"},
    )
    persist_background_event_memory(
        event_source="inner_world",
        event_id="inner-entry-1",
        request_id="inner-entry-1",
        intent_id="inner_world_monologue_tick",
        person_id="owner",
        session_id="desktop_main",
        task_id="inner_world_monologue_tick",
        status="OK",
        detail="我把还没完成的入口覆盖审计记在心里。",
        ts_ms=_ts(29, 9, 10),
        life_memory_store=store,
        episodic_store=None,
        metadata={**common_meta, "entrypoint_probe": "inner_world"},
    )
    store.write_background_episode(
        event_source="vision",
        event_id="screen-entry-1",
        request_id="screen-entry-1",
        person_id="owner",
        session_id="desktop_main",
        task_id="screen_observation",
        status="OK",
        detail="屏幕上显示记忆系统验收页面。",
        ts_ms=_ts(29, 9, 15),
        metadata={**common_meta, "entrypoint_probe": "perception_input"},
    )
    store.write_background_episode(
        event_source="skill",
        event_id="skill-entry-1",
        request_id="skill-entry-1",
        intent_id="tool_action_probe",
        task_id="memory_recall",
        status="OK",
        detail="工具动作完成：读取记忆集合。",
        ts_ms=_ts(29, 9, 20),
        metadata={**common_meta, "entrypoint_probe": "generic_tool_action"},
    )
    store.write_background_episode(
        event_source="skill",
        event_id="audit-anchor-entry-1",
        request_id="audit-anchor-entry-1",
        intent_id="audit_anchor_probe",
        task_id="audit_anchor",
        status="OK",
        detail="审计锚点：skill 执行日志已投递为 LifeMemory anchor。",
        ts_ms=_ts(29, 9, 25),
        metadata={**common_meta, "entrypoint_probe": "audit_anchor", "anchor_only": True, "audit_anchor_source": "skill"},
    )


def _cards() -> list[dict[str, object]]:
    return [
        {
            "title": "入口覆盖 A",
            "selection_reason": "恋爱日常",
            "intro_snippet": "用于验证推荐集合入口。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/entry-a",
        },
        {
            "title": "入口覆盖 B",
            "selection_reason": "奇幻身份",
            "intro_snippet": "用于验证 gateway card evidence。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/entry-b",
        },
        {
            "title": "入口覆盖 C",
            "selection_reason": "皇女皇帝线",
            "intro_snippet": "用于验证 collection item anchor。",
            "detail_url": "https://www.lightnovel.fun/cn/detail/entry-c",
        },
    ]


def _entrypoint_count(report: dict[str, Any], entrypoint: str) -> int:
    for item in report.get("entrypoint_counts") or []:
        if item.get("entrypoint") == entrypoint:
            return int(item.get("count") or 0)
    return 0


def _ts(day: int, hour: int = 12, minute: int = 0) -> int:
    return int(datetime(2026, 5, day, hour, minute, tzinfo=CN_TZ).timestamp() * 1000)


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Life memory entrypoint batch: {'PASS' if report['success'] else 'FAIL'}",
        f"Required entrypoints: {len(report['required_entrypoints'])}",
    ]
    counts = {item.get("entrypoint"): item.get("count") for item in report.get("entrypoint_counts") or []}
    for entrypoint in report["required_entrypoints"]:
        lines.append(f"- {entrypoint}: {counts.get(entrypoint, 0)}")
    if not report["success"]:
        lines.append(f"issues: {json.dumps(report.get('entrypoint_issues') or report.get('issues') or [], ensure_ascii=False)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
