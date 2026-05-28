from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface_memory_recall import build_surface_memory_recall_packet


TS_2026_05_29_08 = 1780012800000


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    query: str
    output: dict[str, Any]
    expected_status: str
    expected_response_mode: str
    expected_ready: bool
    expected_reason: str = ""
    expected_detail_basis: str = ""
    kwargs: dict[str, Any] = field(default_factory=dict)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the surface memory recall answer-readiness matrix.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    args = parser.parse_args(argv)

    report = run_quality_matrix()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0 if report["success"] else 2


def run_quality_matrix() -> dict[str, Any]:
    results = [_run_case(case) for case in _cases()]
    return {
        "schema": "Natsume.surface_memory_recall_quality_matrix.v1",
        "success": all(item["passed"] for item in results),
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "failed_count": sum(1 for item in results if not item["passed"]),
        "cases": results,
    }


def _run_case(case: QualityCase) -> dict[str, Any]:
    packet = build_surface_memory_recall_packet(
        SimpleNamespace(output=case.output),
        query=case.query,
        **case.kwargs,
    )
    trace = dict(packet.trace or {})
    readiness = dict(trace.get("recall_answer_readiness") or {})
    detail = dict(readiness.get("detail_evidence") or {})
    checks = {
        "status": readiness.get("status") == case.expected_status,
        "response_mode": readiness.get("response_mode") == case.expected_response_mode,
        "ready_to_answer": bool(readiness.get("ready_to_answer")) is case.expected_ready,
    }
    if case.expected_reason:
        checks["reason"] = case.expected_reason in list(readiness.get("reasons") or [])
    if case.expected_detail_basis:
        checks["detail_basis"] = detail.get("basis") == case.expected_detail_basis
    return {
        "case": asdict(case),
        "passed": all(checks.values()),
        "checks": checks,
        "reason_code": packet.reason_code,
        "facts_count": len(packet.facts),
        "trace": {
            "answer_ready": trace.get("answer_ready"),
            "recall_coverage": trace.get("recall_coverage"),
            "recall_answer_readiness": readiness,
        },
    }


def _cases() -> list[QualityCase]:
    return [
        QualityCase(
            case_id="current_collection_body_ready",
            query="这三本里哪本好看？",
            output={
                "query": "这三本里哪本好看？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "col-current-body",
                        "source_ref": "life_memory_collection://current/body",
                        "evidence_refs": ["gateway://message/current/card/1"],
                        "text": "本次轻小说推荐集合，记录 3 个条目；1. A｜2. B｜3. C",
                        "score": 0.96,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "scope_key": "delivery:ep-current",
                            "domain": "lightnovel_recommendation",
                            "item_count": 3,
                            "evidence_refs_primary": "gateway://message/current/card/1",
                            "items": [
                                {"item_title": "A", "content_excerpt": "正文偏日常恋爱，冲突轻。", "position": 1},
                                {"item_title": "B", "content_excerpt": "正文偏奇幻身份，节奏快。", "position": 2},
                                {"item_title": "C", "content_excerpt": "正文偏喜剧，包袱密。", "position": 3},
                            ],
                        },
                    }
                ],
            },
            expected_status="READY",
            expected_response_mode="answer_from_collection_memory",
            expected_ready=True,
            expected_detail_basis="body_evidence",
            kwargs={
                "max_facts": 4,
                "max_chars_per_fact": 700,
                "temporal_scope": "current",
                "query_kind": "anaphoric_selection",
                "recall_contract_id": "anaphoric_selection",
                "primary_memory_layer": "life_memory_collection",
                "expected_memory_layers": ("life_memory_collection", "life_memory_entity", "life_episode"),
                "min_collection_items": 3,
                "validation_requirements": (
                    "collection_or_episode_hit",
                    "evidence_refs_present",
                    "no_self_echo",
                    "collection_item_min_count",
                    "collection_item_exact_count",
                ),
                "answer_obligations": (
                    "ground_selection_in_recalled_items",
                    "read_detail_evidence_before_deep_quality_judgment",
                ),
            },
        ),
        QualityCase(
            case_id="yesterday_collection_ready",
            query="昨天三本呢？",
            output={
                "query": "昨天三本呢？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "col-yesterday",
                        "source_ref": "life_memory_collection://daily/2026-05-28",
                        "evidence_refs": ["gateway://message/yesterday/summary"],
                        "text": "2026-05-28 当天轻小说推荐集合；1. Yesterday A｜2. Yesterday B｜3. Yesterday C",
                        "score": 0.93,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "scope_key": "daily:2026-05-28",
                            "item_count": 3,
                            "evidence_refs_primary": "gateway://message/yesterday/summary",
                        },
                    }
                ],
            },
            expected_status="READY",
            expected_response_mode="answer_from_collection_memory",
            expected_ready=True,
            kwargs={
                "max_facts": 4,
                "max_chars_per_fact": 260,
                "temporal_scope": "yesterday",
                "query_kind": "collection_summary",
                "recall_contract_id": "collection_date",
                "primary_memory_layer": "life_memory_collection",
                "expected_memory_layers": ("life_memory_collection", "life_memory_entity"),
                "min_collection_items": 3,
                "validation_requirements": (
                    "scope_match",
                    "collection_items_present",
                    "evidence_refs_present",
                    "collection_item_min_count",
                    "collection_item_exact_count",
                ),
                "answer_obligations": ("summarize_collection", "preserve_item_identity"),
            },
        ),
        QualityCase(
            case_id="wrong_date_not_ready",
            query="昨天三本呢？",
            output={
                "query": "昨天三本呢？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "col-today",
                        "source_ref": "life_memory_collection://daily/2026-05-29",
                        "evidence_refs": ["gateway://message/today/summary"],
                        "text": "2026-05-29 当天轻小说推荐集合；1. Today A｜2. Today B｜3. Today C",
                        "score": 0.98,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "scope_key": "daily:2026-05-29",
                            "item_count": 3,
                            "evidence_refs_primary": "gateway://message/today/summary",
                        },
                    }
                ],
            },
            expected_status="NOT_READY",
            expected_response_mode="state_recall_validation_failed",
            expected_ready=False,
            expected_reason="validation_failed",
            kwargs={
                "max_facts": 4,
                "max_chars_per_fact": 260,
                "temporal_scope": "yesterday",
                "query_kind": "collection_summary",
                "recall_contract_id": "collection_date",
                "primary_memory_layer": "life_memory_collection",
                "expected_memory_layers": ("life_memory_collection", "life_memory_entity"),
                "min_collection_items": 3,
                "validation_requirements": (
                    "scope_match",
                    "collection_items_present",
                    "evidence_refs_present",
                    "collection_item_min_count",
                    "collection_item_exact_count",
                ),
                "answer_obligations": ("summarize_collection", "preserve_item_identity"),
            },
        ),
        QualityCase(
            case_id="recent_many_partial_not_ready",
            query="这几天十几本里有哪些？",
            output={
                "query": "这几天十几本里有哪些？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "ep-partial",
                        "source_ref": "chat://request/partial",
                        "evidence_refs": ["chat://request/partial#assistant"],
                        "text": "用户之前问过轻小说推荐格式，但没有推荐集合条目。",
                        "score": 0.92,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "chat://request/partial",
                            "evidence_refs_primary": "chat://request/partial#assistant",
                        },
                    }
                ],
            },
            expected_status="NOT_READY",
            expected_response_mode="state_recall_coverage_missing",
            expected_ready=False,
            expected_reason="insufficient_item_count",
            kwargs={
                "max_facts": 4,
                "max_chars_per_fact": 260,
                "temporal_scope": "recent_days",
                "query_kind": "collection_summary",
                "recall_contract_id": "collection_recent",
                "primary_memory_layer": "life_memory_collection",
                "expected_memory_layers": ("life_memory_collection", "life_memory_entity"),
                "min_collection_items": 10,
                "validation_requirements": (),
                "answer_obligations": ("summarize_cross_day_collection", "preserve_item_identity"),
            },
        ),
        QualityCase(
            case_id="selection_missing_detail_not_ready",
            query="这三本里哪本好看？",
            output={
                "query": "这三本里哪本好看？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_collection", "store_id": "life_memory_collection"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "col-current-no-detail",
                        "source_ref": "life_memory_collection://current/no-detail",
                        "evidence_refs": ["gateway://message/current/card/1"],
                        "text": "本次轻小说推荐集合，记录 3 个条目；1. A｜2. B｜3. C",
                        "score": 0.95,
                        "metadata": {
                            "store_id": "life_memory_collection",
                            "scope_key": "delivery:ep-current",
                            "domain": "lightnovel_recommendation",
                            "item_count": 3,
                            "evidence_refs_primary": "gateway://message/current/card/1",
                            "items": [
                                {"item_title": "A", "item_summary": "看点：恋爱日常", "position": 1},
                                {"item_title": "B", "item_summary": "看点：奇幻身份", "position": 2},
                                {"item_title": "C", "item_summary": "看点：日常喜剧", "position": 3},
                            ],
                        },
                    }
                ],
            },
            expected_status="NOT_READY",
            expected_response_mode="fetch_or_request_detail_evidence",
            expected_ready=False,
            expected_reason="missing_detail_evidence",
            expected_detail_basis="missing_detail_evidence",
            kwargs={
                "max_facts": 4,
                "max_chars_per_fact": 700,
                "temporal_scope": "current",
                "query_kind": "anaphoric_selection",
                "recall_contract_id": "anaphoric_selection",
                "primary_memory_layer": "life_memory_collection",
                "expected_memory_layers": ("life_memory_collection", "life_memory_entity", "life_episode"),
                "min_collection_items": 3,
                "validation_requirements": (
                    "collection_or_episode_hit",
                    "evidence_refs_present",
                    "no_self_echo",
                    "collection_item_min_count",
                    "collection_item_exact_count",
                ),
                "answer_obligations": (
                    "ground_selection_in_recalled_items",
                    "read_detail_evidence_before_deep_quality_judgment",
                ),
            },
        ),
        QualityCase(
            case_id="web_body_evidence_ready",
            query="昨天那个网页内容靠谱吗？依据是什么？",
            output={
                "query": "昨天那个网页内容靠谱吗？依据是什么？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "ep-web-body",
                        "source_ref": "https://example.com/report",
                        "evidence_refs": ["doc://web/report#body"],
                        "text": "昨天查过网页报告。",
                        "score": 0.94,
                        "metadata": {
                            "store_id": "life_episode",
                            "source_ref": "https://example.com/report",
                            "evidence_refs_primary": "doc://web/report#body",
                            "episode_metadata": {
                                "items": [
                                    {
                                        "title": "网页报告",
                                        "detail_url": "https://example.com/report",
                                        "content_excerpt": "报告正文列出了样本来源、更新时间和限制条件。",
                                    }
                                ]
                            },
                        },
                    }
                ],
            },
            expected_status="READY",
            expected_response_mode="answer_from_evidence",
            expected_ready=True,
            expected_detail_basis="body_evidence",
            kwargs={
                "max_facts": 4,
                "max_chars_per_fact": 320,
                "temporal_scope": "yesterday",
                "query_kind": "evidence_review",
                "target_domains": ("web", "content"),
                "recall_contract_id": "evidence_review",
                "primary_memory_layer": "life_episode",
                "expected_memory_layers": ("life_episode", "life_memory_collection", "semantic_memory"),
                "validation_requirements": ("trusted_memory_hit", "source_ref_present", "target_domain_match"),
                "answer_obligations": ("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"),
            },
        ),
        QualityCase(
            case_id="relationship_only_partial",
            query="我今天有点累，你记得该怎么接我吗？",
            output={
                "query": "我今天有点累，你记得该怎么接我吗？",
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "routes": [{"route_id": "surface_bond_graph", "store_id": "neo4j_bond"}],
                "candidate_count": 1,
                "ts_ms": TS_2026_05_29_08,
                "candidates": [
                    {
                        "doc_id": "bond-owner-soft",
                        "source_ref": "bond://owner/support-style",
                        "text": "用户疲惫时更希望先被稳稳接住，而不是被安排一堆任务。",
                        "score": 0.88,
                        "metadata": {"store_id": "neo4j_bond"},
                    }
                ],
            },
            expected_status="PARTIAL",
            expected_response_mode="answer_with_relationship_context",
            expected_ready=False,
            expected_reason="relationship_only_no_factual_memory",
            kwargs={
                "max_facts": 2,
                "max_chars_per_fact": 180,
                "temporal_scope": "",
                "query_kind": "fresh_topic",
                "recall_contract_id": "",
                "primary_memory_layer": "",
                "expected_memory_layers": ("neo4j_bond",),
                "validation_requirements": (),
                "answer_obligations": (),
            },
        ),
    ]


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Surface memory recall quality matrix: {'PASS' if report['success'] else 'FAIL'}",
        f"Cases: {report['passed_count']}/{report['case_count']} passed",
    ]
    for item in report["cases"]:
        mark = "PASS" if item["passed"] else "FAIL"
        case = item["case"]
        readiness = item["trace"]["recall_answer_readiness"]
        lines.append(
            f"- {mark} {case['case_id']}: {readiness.get('status')} / {readiness.get('response_mode')}"
        )
        if not item["passed"]:
            failed = [name for name, ok in item["checks"].items() if not ok]
            lines.append(f"  failed checks: {', '.join(failed)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
