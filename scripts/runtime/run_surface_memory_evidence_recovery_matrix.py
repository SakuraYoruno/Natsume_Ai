from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.chat_retrieval_helpers import _maybe_apply_generic_evidence_review
from host_brain.persona.surface_memory_recall import build_surface_memory_recall_packet


TS_2026_05_29_08 = 1780012800000


@dataclass(frozen=True)
class EvidenceRecoveryCase:
    case_id: str
    query: str
    source_text: str
    source_suffix: str
    expected_trace: str


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the surface memory evidence recovery matrix.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    args = parser.parse_args(argv)
    report = asyncio.run(run_matrix())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_report(report))
    return 0 if report["success"] else 2


async def run_matrix() -> dict[str, Any]:
    previous = os.environ.get("HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED")
    os.environ["HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED"] = "0"
    try:
        results = [_run_case(case) for case in _cases()]
        resolved = [await item for item in results]
    finally:
        if previous is None:
            os.environ.pop("HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED", None)
        else:
            os.environ["HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED"] = previous
    return {
        "schema": "Natsume.surface_memory_evidence_recovery_matrix.v1",
        "success": all(item["passed"] for item in resolved),
        "case_count": len(resolved),
        "passed_count": sum(1 for item in resolved if item["passed"]),
        "failed_count": sum(1 for item in resolved if not item["passed"]),
        "cases": resolved,
    }


async def _run_case(case: EvidenceRecoveryCase) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="natsume-evidence-review-") as tmp:
        source = Path(tmp) / f"{case.case_id}{case.source_suffix}"
        source.write_text(case.source_text, encoding="utf-8")
        packet = build_surface_memory_recall_packet(
            SimpleNamespace(
                output={
                    "query": case.query,
                    "status": "OK",
                    "stage": "S_RET_RECALL",
                    "degraded": False,
                    "routes": [{"route_id": "surface_life_episode", "store_id": "life_episode"}],
                    "candidate_count": 1,
                    "ts_ms": TS_2026_05_29_08,
                    "candidates": [
                        {
                            "doc_id": f"ep-{case.case_id}",
                            "source_ref": str(source),
                            "text": "昨天读过一个文件，但旧记忆只有摘要，没有正文依据。",
                            "score": 0.94,
                            "metadata": {
                                "store_id": "life_episode",
                                "source_ref": str(source),
                                "domain": "file",
                            },
                        }
                    ],
                }
            ),
            query=case.query,
            max_facts=4,
            max_chars_per_fact=320,
            temporal_scope="yesterday",
            query_kind="evidence_review",
            target_domains=("file", "content"),
            recall_contract_id="evidence_review",
            primary_memory_layer="life_episode",
            expected_memory_layers=("life_episode", "semantic_memory"),
            validation_requirements=("trusted_memory_hit", "source_ref_present", "target_domain_match"),
            answer_obligations=("use_detail_or_body_evidence", "say_if_detail_evidence_is_missing"),
        )
        before_trace = dict(packet.trace or {})
        recovered = await _maybe_apply_generic_evidence_review(
            packet=packet,
            message=case.query,
            request_id=f"req-{case.case_id}",
            person_id="surface-memory-evidence-recovery",
            session_id="surface-memory-evidence-recovery",
            ts_ms=TS_2026_05_29_08,
        )
    trace = dict(recovered.trace or {})
    readiness = dict(trace.get("recall_answer_readiness") or {})
    generic = dict(trace.get("generic_evidence_review") or {})
    checks = {
        "before_not_ready": before_trace.get("answer_ready") is False,
        "before_missing_detail": dict(before_trace.get("recall_answer_readiness") or {}).get("response_mode") == "fetch_or_request_detail_evidence",
        "status_ready": readiness.get("status") == "READY",
        "response_mode": readiness.get("response_mode") == "answer_from_evidence",
        "trace_ok": generic.get("status") == "OK",
        "fact_contains_expected": case.expected_trace in "\n".join(list(recovered.facts or [])),
        "reason_cleared": recovered.reason_code is None,
        "degraded_false": recovered.degraded is False,
    }
    return {
        "case": asdict(case),
        "passed": all(checks.values()),
        "checks": checks,
        "reason_code": recovered.reason_code,
        "degraded": recovered.degraded,
        "readiness": readiness,
        "generic_evidence_review": generic,
        "facts": list(recovered.facts or [])[:2],
    }


def _cases() -> list[EvidenceRecoveryCase]:
    return [
        EvidenceRecoveryCase(
            case_id="local_markdown_report",
            query="昨天那个文件内容靠谱吗？依据是什么？",
            source_suffix=".md",
            source_text="# 样本报告\n\n报告正文列出了样本来源、更新时间和限制条件。结论只适用于这个小样本。",
            expected_trace="样本来源、更新时间和限制条件",
        ),
        EvidenceRecoveryCase(
            case_id="local_html_report",
            query="昨天那个网页内容靠谱吗？依据是什么？",
            source_suffix=".html",
            source_text="<html><head><title>网页报告</title></head><body><p>正文写明数据来自公开页面，并标注了采集日期。</p></body></html>",
            expected_trace="公开页面",
        ),
    ]


def _render_report(report: dict[str, Any]) -> str:
    lines = [
        f"Surface memory evidence recovery matrix: {'PASS' if report['success'] else 'FAIL'}",
        f"Cases: {report['passed_count']}/{report['case_count']} passed",
    ]
    for item in report["cases"]:
        mark = "PASS" if item["passed"] else "FAIL"
        lines.append(f"- {mark} {item['case']['case_id']}: {item['readiness'].get('status')} / {item['readiness'].get('response_mode')}")
        if not item["passed"]:
            failed = [name for name, ok in item["checks"].items() if not ok]
            lines.append(f"  failed checks: {', '.join(failed)}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
