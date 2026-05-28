from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.observability_persist import persist_life_action_event_memory
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory import LifeMemoryStore
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import plan_surface_memory_recall, recall_surface_memory_for_context
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from host_brain.retrieval.recall_schemas import RecallRouteConfig, RecallRuntimeConfig


CN_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ScenarioExpectation:
    case_id: str
    message: str
    expected_reason: str
    expected_kind: str
    must_route: str
    must_contain: str
    must_not_contain: str


class _NullChromaClient:
    @property
    def collection_profiles(self) -> dict[str, int]:
        return {}

    @property
    def default_collection(self) -> str:
        return "episodic"

    def upsert(self, **_kwargs: Any) -> None:
        raise RuntimeError("chroma disabled for life-memory scenario batch")

    def delete(self, _memory_id: str, **_kwargs: Any) -> None:
        raise RuntimeError("chroma disabled for life-memory scenario batch")

    def get(self, _memory_id: str, **_kwargs: Any) -> None:
        raise RuntimeError("chroma disabled for life-memory scenario batch")

    def search(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def validate_collection(self, _collection: str, *, vector_dim: int | None = None) -> tuple[bool, str | None]:
        return True, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic life-memory recall scenario batch.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report instead of a compact text report.")
    args = parser.parse_args(argv)

    report = run_scenario_batch()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0 if report["success"] else 2


def run_scenario_batch() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="life_memory_scenario_batch_") as temp_dir:
        ledger = SQLiteLedger(db_path=Path(temp_dir) / "life_memory_scenarios.sqlite3")
        try:
            store = LifeMemoryStore(ledger=ledger)
            _seed_world(store)
            service = _service(ledger)
            cases = _scenario_expectations()
            results = [_run_case(service=service, case=case) for case in cases]
            return {
                "schema": "Natsume.life_memory_recall_scenario_batch.v1",
                "success": all(item["passed"] for item in results),
                "case_count": len(results),
                "passed_count": sum(1 for item in results if item["passed"]),
                "failed_count": sum(1 for item in results if not item["passed"]),
                "cases": results,
            }
        finally:
            ledger.close()


def _seed_world(store: LifeMemoryStore) -> None:
    for day in (25, 26, 27, 28):
        _seed_recommendation(store, day=day)
    store.write_background_episode(
        event_source="codex_production",
        event_id="deepseek-failure-1",
        person_id="owner",
        session_id="qq_dm_owner",
        task_id="llm_pool_call",
        status="FAILED",
        reason_code="DEEPSEEK_TIMEOUT",
        detail="DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
        ts_ms=_ts(28, 13),
    )
    store.write_chat_episode(
        request_id="req-pref-format",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
        assistant_reply="记住了，后面按更自然的卡片格式发。",
        ts_ms=_ts(28, 14),
    )
    wrong = store.write_chat_episode(
        request_id="req-corrected-wrong-cat-cover",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="刚才那三本里你选哪本？",
        assistant_reply="我选封面有猫的那本。",
        ts_ms=_ts(28, 15),
    )
    correction = store.write_chat_episode(
        request_id="req-corrected-fix-cat-cover",
        session_id="qq_dm_owner",
        person_id="owner",
        user_message="不对，没有封面有猫这本。",
        assistant_reply="你说得对，我把这个判断撤回。",
        ts_ms=_ts(28, 16),
    )
    store.correct_episode(
        episode_id=wrong["episode_id"],
        correction_kind="correction",
        correction_episode_id=correction["episode_id"],
        reason_code="USER_CORRECTED_FALSE_MEMORY",
        detail="用户指出没有封面有猫这本。",
        suppress=False,
        ts_ms=_ts(28, 17),
    )


def _run_case(*, service: RecallFusionServiceContract, case: ScenarioExpectation) -> dict[str, Any]:
    context = ThreeLayerContinuityContext(current_user_message=case.message, task_mode_hint="daily_chat")
    plan = plan_surface_memory_recall(context)
    packet = recall_surface_memory_for_context(
        recall_service=service,
        context=context,
        request_id=f"req-scenario-{case.case_id}",
        query_embedding=[0.1] * 16,
        person_id="owner",
        session_id="qq_dm_owner",
        ts_ms=_ts(28, 18),
    )
    joined = "\n".join(packet.facts)
    validation = dict(dict(packet.trace or {}).get("validation") or {})
    checks = {
        "plan_reason": plan.reason == case.expected_reason,
        "plan_kind": plan.query_kind == case.expected_kind,
        "route": case.must_route in packet.route_ids,
        "contains": case.must_contain in joined,
        "excludes": case.must_not_contain not in joined,
        "evidence": bool(packet.evidence_refs),
        "validation": bool(validation) and validation.get("passed") is True,
    }
    return {
        "case": asdict(case),
        "passed": all(checks.values()),
        "checks": checks,
        "plan": plan.as_dict(),
        "route_ids": list(packet.route_ids),
        "evidence_refs": list(packet.evidence_refs),
        "validation": validation,
        "facts": list(packet.facts),
    }


def _service(ledger: SQLiteLedger) -> RecallFusionServiceContract:
    node_id = "surface_main_chain_memory_recall"
    routes = {
        "surface_life_collection": RecallRouteConfig(
            route_id="surface_life_collection",
            store_id="life_memory_collection",
            collection="life_memory_collection",
            top_k=5,
            score_weight=1.25,
            node_selector=node_id,
        ),
        "surface_life_episode": RecallRouteConfig(
            route_id="surface_life_episode",
            store_id="life_episode",
            collection="life_episode",
            top_k=8,
            score_weight=1.0,
            node_selector=node_id,
        ),
    }
    return RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=_NullChromaClient(),
        runtime_config=RecallRuntimeConfig(
            enabled=True,
            default_route="surface_life_collection",
            routes=routes,
        ),
        node_id=node_id,
        use_hybrid=False,
    )


def _scenario_expectations() -> list[ScenarioExpectation]:
    return [
        ScenarioExpectation(
            case_id="current_selection",
            message="这三本书，你喜欢哪本？",
            expected_reason="anaphoric_selection_recall",
            expected_kind="anaphoric_selection",
            must_route="surface_life_collection",
            must_contain="5月28日推荐",
            must_not_contain="DeepSeek",
        ),
        ScenarioExpectation(
            case_id="yesterday_collection",
            message="昨天三本呢？",
            expected_reason="collection_memory_recall",
            expected_kind="date_lookup",
            must_route="surface_life_collection",
            must_contain="5月27日推荐",
            must_not_contain="5月28日推荐",
        ),
        ScenarioExpectation(
            case_id="yesterday_event_lookup",
            message="昨天你做了什么？",
            expected_reason="event_memory_recall",
            expected_kind="date_lookup",
            must_route="surface_life_collection",
            must_contain="5月27日推荐",
            must_not_contain="5月28日推荐",
        ),
        ScenarioExpectation(
            case_id="recent_collection",
            message="这几天推荐过哪些轻小说？",
            expected_reason="collection_memory_recall",
            expected_kind="collection_summary",
            must_route="surface_life_collection",
            must_contain="5月26日推荐",
            must_not_contain="DeepSeek",
        ),
        ScenarioExpectation(
            case_id="recent_many_book_listing",
            message="这十几本里有哪些？",
            expected_reason="collection_memory_recall",
            expected_kind="collection_summary",
            must_route="surface_life_collection",
            must_contain="5月26日推荐",
            must_not_contain="DeepSeek",
        ),
        ScenarioExpectation(
            case_id="failure_debug",
            message="DeepSeek失败原因是什么？",
            expected_reason="failure_debug_recall",
            expected_kind="failure_debug",
            must_route="surface_life_episode",
            must_contain="供应商超时",
            must_not_contain="5月27日推荐",
        ),
        ScenarioExpectation(
            case_id="preference_lookup",
            message="还记得我说推荐格式要怎样吗？",
            expected_reason="explicit_memory_recall",
            expected_kind="preference_lookup",
            must_route="surface_life_episode",
            must_contain="简介后应该换行",
            must_not_contain="5月28日推荐",
        ),
        ScenarioExpectation(
            case_id="correction_lookup",
            message="你刚才是不是记错了封面猫那本？",
            expected_reason="correction_recall",
            expected_kind="correction",
            must_route="surface_life_episode",
            must_contain="没有封面有猫",
            must_not_contain="我选封面有猫",
        ),
    ]


def _seed_recommendation(store: LifeMemoryStore, *, day: int) -> None:
    persist_life_action_event_memory(
        event_source="codex_production",
        action_kind="lightnovel_recommendation_delivery",
        action_id=f"job-202605{day}:lightnovel_gateway_delivery",
        actor_id="natsume",
        request_id=f"req-rec-{day}",
        intent_id="codex_lightnovel_recommender",
        person_id="owner",
        session_id="qq_dm_owner",
        status="COMPLETED",
        action_summary=f"我在 5 月 {day} 日给 qq_dm_owner 推荐了 3 本轻小说。",
        ts_ms=_ts(day),
        evidence_refs=[f"gateway://message/202605{day}/card/{index}" for index in range(1, 4)],
        life_memory_store=store,
        episodic_store=None,
        metadata={
            "platform": "qq",
            "conversation_id": "qq_dm_owner",
            "selected_recommendations": _cards(day),
        },
    )


def _cards(day: int) -> list[dict[str, object]]:
    return [
        {
            "title": f"5月{day}日推荐 A",
            "selection_reason": "恋爱日常",
            "intro_snippet": "人物关系推进比较轻。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{day}01",
        },
        {
            "title": f"5月{day}日推荐 B",
            "selection_reason": "奇幻身份钩子",
            "intro_snippet": "设定比较新鲜。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{day}02",
        },
        {
            "title": f"5月{day}日推荐 C",
            "selection_reason": "皇女皇帝线",
            "intro_snippet": "路线更直给。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{day}03",
        },
    ]


def _ts(day: int, hour: int = 12) -> int:
    return int(datetime(2026, 5, day, hour, tzinfo=CN_TZ).timestamp() * 1000)


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Life memory scenario batch: {'PASS' if report['success'] else 'FAIL'}",
        f"Cases: {report['passed_count']}/{report['case_count']} passed",
    ]
    for item in report["cases"]:
        mark = "PASS" if item["passed"] else "FAIL"
        case = item["case"]
        lines.append(f"- {mark} {case['case_id']}: {case['message']}")
        if not item["passed"]:
            failed = [name for name, ok in item["checks"].items() if not ok]
            lines.append(f"  failed checks: {', '.join(failed)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
