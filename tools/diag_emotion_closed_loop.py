"""Emotion closed-loop validator for chat prompt + VTS mapping path.

Validates the chain on 10+ samples:
1) emotion normalization + snapshot consistency
2) prompt emotion binding contains expected tag/intensity
3) VTS expression mapping resolves to deterministic expression

Outputs JSON + Markdown reports under docs/60_trace.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.emotion_context import (  # noqa: E402
    build_chat_emotion_snapshot,
    map_emotion_tag_to_vad,
    normalize_chat_emotion_context,
)
from host_brain.app.prompt_builder import render_node_generation_prompt  # noqa: E402
from host_brain.channels.vts import (  # noqa: E402
    STATUS_APPLIED,
    TRIGGER_HOST,
    resolve_vts_expression,
)


@dataclass(frozen=True)
class EmotionCase:
    case_id: str
    mood_state: str
    emotion_tag: str
    emotion_intensity: float
    user_message: str
    expected_valence_sign: int  # -1 / 0 / 1


@dataclass
class CaseResult:
    case_id: str
    normalized_mood_state: str
    normalized_emotion_tag: str
    normalized_intensity: float
    snapshot_ok: bool
    prompt_ok: bool
    vts_ok: bool
    valence_sign_ok: bool
    vts_status: str
    expression_id: str | None
    passed: bool
    failed_checks: list[str]


DEFAULT_CASES: list[EmotionCase] = [
    EmotionCase("c01", "engaged", "HAPPY", 0.86, "今天特别开心，想要积极一点回应。", 1),
    EmotionCase("c02", "engaged", "EXCITED", 0.92, "太棒了！这件事让我很兴奋。", 1),
    EmotionCase("c03", "steady", "ANGRY", 0.75, "我有点生气，需要直接一点。", -1),
    EmotionCase("c04", "steady", "SAD", 0.68, "今天状态低落，想要温和安慰。", -1),
    EmotionCase("c05", "calm", "CALM", 0.34, "请平静地给我一个简短建议。", 1),
    EmotionCase("c06", "calm", "THINKING", 0.43, "我想认真思考下这个方案。", 0),
    EmotionCase("c07", "tired", "SAD", 0.59, "我有点累，回复别太激进。", -1),
    EmotionCase("c08", "tired", "NEUTRAL", 0.35, "今天先中性一点，短回复就好。", 0),
    EmotionCase("c09", "critical", "SAD", 0.72, "现在压力很大，先稳住情绪。", -1),
    EmotionCase("c10", "critical", "NEUTRAL", 0.40, "先给我一个克制、冷静的回应。", 0),
    EmotionCase("c11", "engaged", "SURPRISED", 0.66, "这个结果让我很惊讶。", 0),
    EmotionCase("c12", "steady", "HAPPY", 0.63, "想听到更有活力一点的反馈。", 1),
]


class _NoopLedger:
    def log_avatar_action(self, _payload):  # noqa: ANN001
        return None


def _build_runtime_payload(
    *,
    case: EmotionCase,
    normalized: dict[str, object],
    snapshot: dict[str, object],
) -> dict[str, object]:
    return {
        "user_message": case.user_message,
        "retrieval": {"status": "SKIPPED", "reason_code": "DIAG"},
        "context_pack": {
            "hard_snapshot": {"session_id": f"diag-session-{case.case_id}"},
            "bond_summary": {},
            "authoritative": [],
            "retrieval": [],
            "style_pack": {"node_bindings": {}},
            "constraints": {},
            "tool_meta_pack": [],
            "emotion_pulse": {
                "emotion_tag": normalized.get("emotion_tag"),
                "mood_state": normalized.get("mood_state"),
                "style_hint": "diag-emotion-closed-loop",
                "emotion_range_hint": normalized.get("emotion_range_hint"),
            },
            "memory_emotion_hint": {
                "emotion_tag": normalized.get("emotion_tag"),
                "mood_state": normalized.get("mood_state"),
                "retrieval_status": "SKIPPED",
            },
            "bond_texture": {
                "emotion_tag": normalized.get("emotion_tag"),
                "mood_state": normalized.get("mood_state"),
            },
        },
        "context_pack_validation": {
            "valid": True,
            "reason_code": None,
            "violation_count": 0,
        },
        "emotion_context": dict(normalized),
        "emotion_snapshot": dict(snapshot),
        "core_reasoner_primary": {
            "payload": {
                "narrative": {"text": "这是语义草案。"},
                "intent_draft": {"action": "narrative_only", "args": {}},
            }
        },
        "planner_constraints": {"payload": {"narrative": {"text": "planner"}}},
        "safety_verifier": {"payload": {"narrative": {"text": "safe"}}},
    }


def _eval_case(case: EmotionCase, *, ts_ms: int) -> CaseResult:
    normalized = normalize_chat_emotion_context(
        {
            "mood_state": case.mood_state,
            "emotion_tag": case.emotion_tag,
            "emotion_intensity": case.emotion_intensity,
            "emotion_ts_ms": ts_ms - 30,
        }
    )
    snapshot = build_chat_emotion_snapshot(
        emotion_context=normalized,
        source="diag_emotion_closed_loop",
        ts_ms=ts_ms,
    )
    runtime_payload = _build_runtime_payload(
        case=case,
        normalized=normalized,
        snapshot=snapshot,
    )
    prompt = render_node_generation_prompt(
        node_id="chat_direct_ja",
        runtime_payload=runtime_payload,
    )

    normalized_tag = str(normalized.get("emotion_tag") or "")
    normalized_intensity = float(normalized.get("emotion_intensity") or 0.0)

    snapshot_emotion_context = dict(snapshot.get("emotion_context") or {})
    snapshot_ok = (
        str(snapshot_emotion_context.get("emotion_tag") or "") == normalized_tag
        and abs(float(snapshot_emotion_context.get("emotion_intensity") or 0.0) - normalized_intensity) < 1e-9
    )

    prompt_ok = (
        f"emotion_tag: {normalized_tag}" in prompt
        and f"emotion_intensity: {normalized_intensity}" in prompt
        and "snapshot_source: diag_emotion_closed_loop" in prompt
    )

    vts_result = resolve_vts_expression(
        emotion_tag=normalized_tag,
        ledger=_NoopLedger(),
        request_id=f"diag-emo-{case.case_id}",
        trigger=TRIGGER_HOST,
        ts_ms=ts_ms,
        edge_available=True,
    )
    vts_ok = bool(vts_result.status == STATUS_APPLIED and vts_result.expression_id)

    valence, _ = map_emotion_tag_to_vad(normalized_tag)
    if case.expected_valence_sign > 0:
        valence_sign_ok = bool(valence > 0.0)
    elif case.expected_valence_sign < 0:
        valence_sign_ok = bool(valence < 0.0)
    else:
        valence_sign_ok = bool(abs(valence) <= 1e-9)

    failed_checks: list[str] = []
    if not snapshot_ok:
        failed_checks.append("snapshot")
    if not prompt_ok:
        failed_checks.append("prompt_binding")
    if not vts_ok:
        failed_checks.append("vts_mapping")
    if not valence_sign_ok:
        failed_checks.append("vad_valence_sign")

    passed = not failed_checks
    return CaseResult(
        case_id=case.case_id,
        normalized_mood_state=str(normalized.get("mood_state") or ""),
        normalized_emotion_tag=normalized_tag,
        normalized_intensity=normalized_intensity,
        snapshot_ok=snapshot_ok,
        prompt_ok=prompt_ok,
        vts_ok=vts_ok,
        valence_sign_ok=valence_sign_ok,
        vts_status=str(vts_result.status),
        expression_id=vts_result.expression_id,
        passed=passed,
        failed_checks=failed_checks,
    )


def run_closed_loop_validation(*, threshold: float = 0.8) -> dict[str, object]:
    ts_ms = int(time.time() * 1000)
    results = [_eval_case(case, ts_ms=ts_ms) for case in DEFAULT_CASES]
    passed = [row for row in results if row.passed]
    failed = [row for row in results if not row.passed]
    pass_rate = (len(passed) / len(results)) if results else 0.0
    return {
        "version": "emotion_closed_loop.v1",
        "ts_ms": ts_ms,
        "total_cases": len(results),
        "passed_cases": len(passed),
        "failed_cases": len(failed),
        "pass_rate": round(pass_rate, 4),
        "threshold": float(threshold),
        "threshold_passed": bool(pass_rate >= float(threshold)),
        "results": [asdict(item) for item in results],
    }


def _write_reports(report: dict[str, object], *, output_stem: str) -> tuple[Path, Path]:
    docs_root = ROOT / "docs" / "60_trace"
    docs_root.mkdir(parents=True, exist_ok=True)
    json_path = docs_root / f"{output_stem}.json"
    md_path = docs_root / f"{output_stem}.md"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    rows = list(report.get("results") or [])
    failed_rows = [row for row in rows if not bool(dict(row).get("passed"))]
    line_rows = []
    for row in rows:
        item = dict(row)
        line_rows.append(
            "| {case_id} | {tag} | {mood} | {passed} | {snapshot_ok} | {prompt_ok} | {vts_ok} | {valence_ok} | {failed_checks} |".format(
                case_id=item.get("case_id"),
                tag=item.get("normalized_emotion_tag"),
                mood=item.get("normalized_mood_state"),
                passed="PASS" if item.get("passed") else "FAIL",
                snapshot_ok=item.get("snapshot_ok"),
                prompt_ok=item.get("prompt_ok"),
                vts_ok=item.get("vts_ok"),
                valence_ok=item.get("valence_sign_ok"),
                failed_checks=",".join(list(item.get("failed_checks") or [])) or "-",
            )
        )

    summary = (
        f"# Emotion Closed Loop Validation Report\n\n"
        f"- generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- total_cases: {report.get('total_cases')}\n"
        f"- passed_cases: {report.get('passed_cases')}\n"
        f"- failed_cases: {report.get('failed_cases')}\n"
        f"- pass_rate: {report.get('pass_rate')}\n"
        f"- threshold: {report.get('threshold')}\n"
        f"- threshold_passed: {report.get('threshold_passed')}\n\n"
        "## Case Matrix\n\n"
        "| case_id | emotion_tag | mood_state | passed | snapshot_ok | prompt_ok | vts_ok | valence_sign_ok | failed_checks |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        + "\n".join(line_rows)
        + "\n\n## Failed Cases\n\n"
        + (
            "- none\n"
            if not failed_rows
            else "\n".join(
                f"- {dict(row).get('case_id')}: {','.join(list(dict(row).get('failed_checks') or []))}"
                for row in failed_rows
            )
            + "\n"
        )
    )
    md_path.write_text(summary, encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run emotion closed-loop validation on default 12-case corpus.")
    parser.add_argument("--threshold", type=float, default=0.8, help="Pass-rate threshold, default 0.8")
    parser.add_argument(
        "--output-stem",
        default=f"EMOTION_CLOSED_LOOP_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help="Output file stem under docs/60_trace",
    )
    args = parser.parse_args()

    report = run_closed_loop_validation(threshold=float(args.threshold))
    json_path, md_path = _write_reports(report, output_stem=str(args.output_stem))
    print(
        f"emotion_closed_loop: total={report['total_cases']} passed={report['passed_cases']} "
        f"pass_rate={report['pass_rate']} threshold_passed={report['threshold_passed']}"
    )
    print(f"json_report: {json_path}")
    print(f"markdown_report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
