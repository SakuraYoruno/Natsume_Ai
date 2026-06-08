from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import (
    build_surface_memory_recall_decision_trace,
    plan_surface_memory_recall,
)


@dataclass(frozen=True)
class PolicyCase:
    case_id: str
    message: str
    task_mode: str
    expected_decision: str
    expected_policy: str
    expected_reason: str
    expected_kind: str = ""
    expected_temporal_scope: str = ""
    expected_need_type: str = ""
    expected_recall_grain: str = ""
    expected_primary_layer: str = ""
    expected_evidence_depth: str = ""
    must_store: str = ""
    must_domain: str = ""
    must_validation: str = ""
    min_items: int = 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the surface memory recall policy matrix.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    args = parser.parse_args(argv)

    report = run_policy_matrix()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0 if report["success"] else 2


def run_policy_matrix() -> dict[str, Any]:
    results = [_run_case(case) for case in _cases()]
    return {
        "schema": "Natsume.surface_memory_recall_policy_matrix.v1",
        "success": all(item["passed"] for item in results),
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "failed_count": sum(1 for item in results if not item["passed"]),
        "cases": results,
    }


def _run_case(case: PolicyCase) -> dict[str, Any]:
    plan = plan_surface_memory_recall(
        ThreeLayerContinuityContext(
            current_user_message=case.message,
            task_mode_hint=case.task_mode,
        )
    )
    decision = build_surface_memory_recall_decision_trace(plan, invoked=plan.should_recall)
    checks = {
        "decision": decision["decision"] == case.expected_decision,
        "policy": decision["recall_policy"] == case.expected_policy,
        "reason": decision["reason"] == case.expected_reason,
    }
    if case.expected_kind:
        checks["kind"] = decision["query_kind"] == case.expected_kind
    if case.expected_temporal_scope:
        checks["temporal_scope"] = decision["temporal_scope"] == case.expected_temporal_scope
    if case.expected_need_type:
        checks["need_type"] = decision["need_type"] == case.expected_need_type
    if case.expected_recall_grain:
        checks["recall_grain"] = decision["recall_grain"] == case.expected_recall_grain
    if case.expected_primary_layer:
        checks["primary_layer"] = decision["primary_memory_layer"] == case.expected_primary_layer
    if case.expected_evidence_depth:
        checks["evidence_depth"] = decision["evidence_depth"] == case.expected_evidence_depth
    if case.must_store:
        checks["store"] = case.must_store in list(decision.get("target_store_ids") or [])
    if case.must_domain:
        checks["domain"] = case.must_domain in list(decision.get("target_domains") or [])
    if case.must_validation:
        checks["validation"] = case.must_validation in list(decision.get("validation_requirements") or [])
    if case.min_items:
        checks["min_items"] = int(decision.get("min_collection_items") or 0) >= int(case.min_items)
    return {
        "case": asdict(case),
        "passed": all(checks.values()),
        "checks": checks,
        "plan": plan.as_dict(),
        "decision": decision,
    }


def _cases() -> list[PolicyCase]:
    return [
        PolicyCase(
            case_id="low_signal_ack",
            message="继续",
            task_mode="short_ack",
            expected_decision="skip",
            expected_policy="NO_RECALL",
            expected_reason="low_signal_surface_turn",
            expected_kind="fresh_topic",
            expected_need_type="memory_hit",
            expected_evidence_depth="memory_hit",
        ),
        PolicyCase(
            case_id="plain_without_memory_hint",
            message="今天先随便聊两句吧。",
            task_mode="plain_answer",
            expected_decision="skip",
            expected_policy="NO_RECALL",
            expected_reason="no_memory_recall_needed",
            expected_kind="fresh_topic",
            expected_need_type="memory_hit",
        ),
        PolicyCase(
            case_id="memory_write_current_turn",
            message="记一下，我喜欢芒果，不喜欢太甜的奶茶。",
            task_mode="daily_chat",
            expected_decision="skip",
            expected_policy="NO_RECALL",
            expected_reason="memory_write_current_turn_only",
            expected_kind="fresh_topic",
            expected_need_type="memory_hit",
        ),
        PolicyCase(
            case_id="preference_update_current_turn",
            message="不要用 pro，没有必要。",
            task_mode="daily_chat",
            expected_decision="skip",
            expected_policy="NO_RECALL",
            expected_reason="memory_write_current_turn_only",
            expected_kind="fresh_topic",
            expected_need_type="memory_hit",
        ),
        PolicyCase(
            case_id="style_feedback_current_turn",
            message="昨天那几本我能对上，这种说辞不像人话。",
            task_mode="daily_chat",
            expected_decision="skip",
            expected_policy="NO_RECALL",
            expected_reason="response_style_feedback_current_turn",
            expected_kind="fresh_topic",
            expected_temporal_scope="yesterday",
            expected_need_type="memory_hit",
        ),
        PolicyCase(
            case_id="current_collection_selection",
            message="这三本书，你喜欢哪本？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="anaphoric_selection_recall",
            expected_kind="anaphoric_selection",
            expected_need_type="collection_selection",
            expected_recall_grain="collection_selection",
            expected_primary_layer="life_memory_collection",
            expected_evidence_depth="detail_or_body_evidence",
            must_store="life_memory_collection",
            must_domain="lightnovel_recommendation",
            must_validation="collection_or_episode_hit",
            min_items=3,
        ),
        PolicyCase(
            case_id="yesterday_collection",
            message="昨天三本呢？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="collection_memory_recall",
            expected_kind="date_lookup",
            expected_need_type="collection",
            expected_recall_grain="collection",
            expected_primary_layer="life_memory_collection",
            expected_evidence_depth="evidence_anchor",
            must_store="life_memory_collection",
            must_validation="scope_match",
            min_items=3,
        ),
        PolicyCase(
            case_id="yesterday_collection_which_books",
            message="昨天给我推荐了哪几本书？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="collection_memory_recall",
            expected_kind="date_lookup",
            expected_temporal_scope="yesterday",
            expected_need_type="collection",
            expected_recall_grain="collection",
            expected_primary_layer="life_memory_collection",
            expected_evidence_depth="evidence_anchor",
            must_store="life_memory_collection",
            must_validation="collection_item_exact_count",
            min_items=3,
        ),
        PolicyCase(
            case_id="last_week_collection",
            message="上周推过哪些书？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="collection_memory_recall",
            expected_kind="collection_summary",
            expected_temporal_scope="recent_days",
            expected_need_type="collection",
            expected_recall_grain="collection",
            expected_primary_layer="life_memory_collection",
            expected_evidence_depth="evidence_anchor",
            must_store="life_memory_collection",
            must_validation="collection_item_min_count",
            min_items=3,
        ),
        PolicyCase(
            case_id="recent_many_selection",
            message="这几天十几本里哪本最好看？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="anaphoric_selection_recall",
            expected_kind="anaphoric_selection",
            expected_need_type="collection_selection",
            expected_recall_grain="collection_selection",
            expected_primary_layer="life_memory_collection",
            expected_evidence_depth="detail_or_body_evidence",
            must_store="life_memory_collection",
            must_validation="collection_item_min_count",
            min_items=10,
        ),
        PolicyCase(
            case_id="web_evidence_review",
            message="昨天那个网页内容靠谱吗？依据是什么？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="evidence_review_recall",
            expected_kind="evidence_review",
            expected_need_type="evidence_review",
            expected_recall_grain="evidence",
            expected_primary_layer="life_episode",
            expected_evidence_depth="detail_or_body_evidence",
            must_store="life_episode",
            must_domain="web",
            must_validation="target_domain_match",
        ),
        PolicyCase(
            case_id="browser_event_lookup",
            message="昨天打开浏览器查了什么？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="event_memory_recall",
            expected_kind="date_lookup",
            expected_need_type="episode_event",
            expected_recall_grain="episode",
            expected_primary_layer="life_episode",
            expected_evidence_depth="memory_hit",
            must_store="life_episode",
            must_domain="web",
            must_validation="event_time_scope_match",
        ),
        PolicyCase(
            case_id="failure_debug",
            message="DeepSeek失败原因是什么？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="failure_debug_recall",
            expected_kind="failure_debug",
            expected_need_type="failure_episode",
            expected_recall_grain="episode",
            expected_primary_layer="life_episode",
            must_store="life_episode",
            must_domain="failure",
            must_validation="error_episode_or_semantic_hit",
        ),
        PolicyCase(
            case_id="stuck_followup_failure_debug",
            message="为什么又卡住了？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="failure_debug_recall",
            expected_kind="failure_debug",
            expected_need_type="failure_episode",
            expected_recall_grain="episode",
            expected_primary_layer="life_episode",
            must_store="life_episode",
            must_domain="failure",
            must_validation="error_episode_or_semantic_hit",
        ),
        PolicyCase(
            case_id="preference_lookup",
            message="还记得我说推荐格式要怎样吗？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="explicit_memory_recall",
            expected_kind="preference_lookup",
            expected_need_type="semantic_or_episode_preference",
            expected_recall_grain="semantic_first",
            expected_primary_layer="semantic_memory",
            must_store="semantic_memory",
            must_domain="preference",
            must_validation="trusted_memory_hit",
        ),
        PolicyCase(
            case_id="open_loop_lookup",
            message="记忆系统还有什么待办没完成？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="explicit_memory_recall",
            expected_kind="open_loop",
            expected_need_type="open_loop_memory",
            expected_recall_grain="semantic_first",
            expected_primary_layer="semantic_memory",
            must_store="semantic_memory",
            must_validation="trusted_memory_hit",
        ),
        PolicyCase(
            case_id="self_change_lookup",
            message="记忆系统改过什么？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="explicit_memory_recall",
            expected_kind="self_change",
            expected_need_type="self_change_memory",
            expected_recall_grain="semantic_first",
            expected_primary_layer="semantic_memory",
            must_store="semantic_memory",
            must_validation="trusted_memory_hit",
        ),
        PolicyCase(
            case_id="correction_lookup",
            message="你刚才是不是记错了封面猫那本？",
            task_mode="daily_chat",
            expected_decision="recall",
            expected_policy="MUST_RECALL",
            expected_reason="correction_recall",
            expected_kind="correction",
            expected_need_type="correction_chain",
            expected_recall_grain="correction_chain",
            expected_primary_layer="life_episode",
            must_store="life_episode",
            must_validation="correction_state_checked",
        ),
        PolicyCase(
            case_id="technical_context",
            message="下一步怎么处理这个报错",
            task_mode="next_step",
            expected_decision="recall",
            expected_policy="MAY_RECALL",
            expected_reason="technical_user_memory_context",
            expected_kind="fresh_topic",
            expected_need_type="memory_hit",
            must_store="life_episode",
        ),
        PolicyCase(
            case_id="emotion_context",
            message="我今天有点累。",
            task_mode="tired",
            expected_decision="recall",
            expected_policy="MAY_RECALL",
            expected_reason="emotion_or_relationship_context",
            expected_kind="fresh_topic",
            expected_need_type="memory_hit",
            must_store="neo4j_bond",
        ),
    ]


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Surface memory recall policy matrix: {'PASS' if report['success'] else 'FAIL'}",
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
