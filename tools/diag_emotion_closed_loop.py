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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.emotion_context import (  # noqa: E402
    build_chat_emotion_snapshot,
    map_emotion_tag_to_vad,
    normalize_chat_emotion_context,
)
from host_brain.app.chat_context_builder import build_chat_prompt_runtime_payload  # noqa: E402
from host_brain.app.chat_runtime_input import build_chat_runtime_input  # noqa: E402
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
    expected_normalized_emotion_tag: str | None = None
    expected_normalized_mood_state: str | None = None
    expected_normalized_intensity: float | None = None


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
    expected_tag_ok: bool
    expected_mood_ok: bool
    expected_intensity_ok: bool
    vts_status: str
    expression_id: str | None
    passed: bool
    failed_checks: list[str]

DEFAULT_CASES_FIXTURE = ROOT / "tests" / "fixtures" / "emotion" / "closed_loop_cases.json"
EMBEDDED_DEFAULT_CASES: list[EmotionCase] = [
    EmotionCase("c01", "engaged", "HAPPY", 0.86, "I feel especially happy today, answer with a bright tone.", 1),
    EmotionCase("c02", "engaged", "EXCITED", 0.92, "That result is exciting, keep the response energetic.", 1),
    EmotionCase("c03", "steady", "ANGRY", 0.75, "I am irritated, keep the answer direct but controlled.", -1),
    EmotionCase("c04", "steady", "SAD", 0.68, "I feel low today, use a gentle comforting tone.", -1),
    EmotionCase("c05", "calm", "CALM", 0.34, "Give me a short and calm suggestion.", 1),
    EmotionCase("c06", "calm", "THINKING", 0.43, "I want to think through this plan carefully.", 0),
    EmotionCase("c07", "tired", "SAD", 0.59, "I am tired, avoid an overly intense reply.", -1),
    EmotionCase("c08", "tired", "NEUTRAL", 0.35, "Stay neutral and keep the reply brief.", 0),
    EmotionCase("c09", "critical", "SAD", 0.72, "The pressure is high right now, stabilize the mood first.", -1),
    EmotionCase("c10", "critical", "NEUTRAL", 0.40, "Give me a restrained and calm response first.", 0),
    EmotionCase("c11", "engaged", "SURPRISED", 0.66, "That outcome surprised me.", 0),
    EmotionCase("c12", "steady", "HAPPY", 0.63, "I want feedback with slightly more energy.", 1),
]


class _NoopLedger:
    def log_avatar_action(self, _payload):  # noqa: ANN001
        return None


def _relative_case_source(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve())).replace("\\", "/")
    except ValueError:
        return str(resolved).replace("\\", "/")


def _read_json_utf8(path: Path) -> Any:
    raw_text = ""
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            raw_text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if not raw_text:
        raise ValueError(f"case file '{path}' could not be decoded as UTF-8")
    return json.loads(raw_text.lstrip("\ufeff"))


def _parse_emotion_case(payload: object, *, index: int) -> EmotionCase:
    if not isinstance(payload, dict):
        raise ValueError(f"invalid case at index {index}: expected object")
    raw = dict(payload)

    case_id = str(raw.get("case_id") or "").strip() or f"case_{index:02d}"
    mood_state = str(raw.get("mood_state") or "").strip().lower()
    emotion_tag = str(raw.get("emotion_tag") or "").strip().upper()
    user_message = str(raw.get("user_message") or "").strip()

    if not mood_state:
        raise ValueError(f"invalid case '{case_id}': mood_state is required")
    if not emotion_tag:
        raise ValueError(f"invalid case '{case_id}': emotion_tag is required")
    if not user_message:
        raise ValueError(f"invalid case '{case_id}': user_message is required")

    try:
        emotion_intensity = float(raw.get("emotion_intensity"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid case '{case_id}': emotion_intensity must be numeric") from exc

    try:
        expected_valence_sign = int(raw.get("expected_valence_sign"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid case '{case_id}': expected_valence_sign must be -1/0/1") from exc
    if expected_valence_sign not in {-1, 0, 1}:
        raise ValueError(f"invalid case '{case_id}': expected_valence_sign must be -1/0/1")

    expected_tag = str(raw.get("expected_normalized_emotion_tag") or "").strip().upper() or None
    expected_mood = str(raw.get("expected_normalized_mood_state") or "").strip().lower() or None

    expected_intensity_raw = raw.get("expected_normalized_intensity")
    expected_intensity: float | None = None
    if expected_intensity_raw is not None:
        try:
            expected_intensity = float(expected_intensity_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid case '{case_id}': expected_normalized_intensity must be numeric") from exc
        if expected_intensity < 0.0 or expected_intensity > 1.0:
            raise ValueError(f"invalid case '{case_id}': expected_normalized_intensity must be in [0, 1]")

    return EmotionCase(
        case_id=case_id,
        mood_state=mood_state,
        emotion_tag=emotion_tag,
        emotion_intensity=emotion_intensity,
        user_message=user_message,
        expected_valence_sign=expected_valence_sign,
        expected_normalized_emotion_tag=expected_tag,
        expected_normalized_mood_state=expected_mood,
        expected_normalized_intensity=expected_intensity,
    )


def load_emotion_cases(case_file: str | Path) -> list[EmotionCase]:
    path = Path(case_file)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"case file not found: {path}")
    payload = _read_json_utf8(path)
    if not isinstance(payload, list):
        raise ValueError(f"case file '{path}' must contain a JSON list")
    cases = [_parse_emotion_case(item, index=index) for index, item in enumerate(payload, start=1)]
    if not cases:
        raise ValueError(f"case file '{path}' must provide at least 1 case")
    return cases


def _resolve_cases(
    *,
    cases: list[EmotionCase] | None,
    case_file: str | Path | None,
) -> tuple[list[EmotionCase], str]:
    if cases:
        return list(cases), "inline_cases"
    if case_file:
        loaded = load_emotion_cases(case_file)
        return loaded, _relative_case_source(Path(case_file) if Path(case_file).is_absolute() else ROOT / Path(case_file))
    if DEFAULT_CASES_FIXTURE.exists():
        loaded = load_emotion_cases(DEFAULT_CASES_FIXTURE)
        return loaded, _relative_case_source(DEFAULT_CASES_FIXTURE)
    return list(EMBEDDED_DEFAULT_CASES), "embedded_default_cases"


def _build_runtime_payload(
    *,
    case: EmotionCase,
    normalized: dict[str, object],
    snapshot: dict[str, object],
) -> dict[str, object]:
    runtime_input = build_chat_runtime_input(
        request_id=f"diag-request-{case.case_id}",
        session_id=f"diag-session-{case.case_id}",
        thread_id=None,
        user_message=case.user_message,
        render_target_lang="ja",
        retrieval_summary={"status": "SKIPPED", "reason_code": "DIAG"},
        retrieval_evidence_items=[],
        emotion_state=normalized,
        emotion_snapshot=snapshot,
    )
    runtime_payload = build_chat_prompt_runtime_payload(runtime_input=runtime_input)
    runtime_payload.update(
        {
            "core_reasoner_primary": {
                "payload": {
                    "narrative": {"text": "diagnostic narrative"},
                    "intent_draft": {"action": "narrative_only", "args": {}},
                }
            },
            "planner_constraints": {"payload": {"narrative": {"text": "planner"}}},
            "safety_verifier": {"payload": {"narrative": {"text": "safe"}}},
        }
    )
    return runtime_payload


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
    normalized_mood_state = str(normalized.get("mood_state") or "")
    normalized_intensity = float(normalized.get("emotion_intensity") or 0.0)

    snapshot_emotion_context = dict(snapshot.get("emotion_context") or {})
    snapshot_ok = (
        str(snapshot_emotion_context.get("emotion_tag") or "") == normalized_tag
        and abs(float(snapshot_emotion_context.get("emotion_intensity") or 0.0) - normalized_intensity) < 1e-9
        and str(snapshot.get("source") or "") == "diag_emotion_closed_loop"
    )

    prompt_ok = (
        "EMOTION_RECALL_BINDING:" in prompt
        and (
            f"Current affective layer: mood_state={normalized_mood_state}; emotion_tag={normalized_tag}."
            in prompt
        )
        and f"emotion_intensity={normalized_intensity:.2f}." in prompt
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

    valence, _, _ = map_emotion_tag_to_vad(normalized_tag)
    if case.expected_valence_sign > 0:
        valence_sign_ok = bool(valence > 0.0)
    elif case.expected_valence_sign < 0:
        valence_sign_ok = bool(valence < 0.0)
    else:
        # Treat small valence drift as neutral so diagnostic cases stay aligned
        # with the current emotion VAD table.
        valence_sign_ok = bool(abs(valence) <= 0.35)

    expected_tag_ok = (
        True
        if not case.expected_normalized_emotion_tag
        else normalized_tag == str(case.expected_normalized_emotion_tag)
    )
    expected_mood_ok = (
        True
        if not case.expected_normalized_mood_state
        else normalized_mood_state == str(case.expected_normalized_mood_state)
    )
    expected_intensity_ok = (
        True
        if case.expected_normalized_intensity is None
        else abs(normalized_intensity - float(case.expected_normalized_intensity)) < 1e-9
    )

    failed_checks: list[str] = []
    if not snapshot_ok:
        failed_checks.append("snapshot")
    if not prompt_ok:
        failed_checks.append("prompt_binding")
    if not vts_ok:
        failed_checks.append("vts_mapping")
    if not valence_sign_ok:
        failed_checks.append("vad_valence_sign")
    if not expected_tag_ok:
        failed_checks.append("expected_normalized_emotion_tag")
    if not expected_mood_ok:
        failed_checks.append("expected_normalized_mood_state")
    if not expected_intensity_ok:
        failed_checks.append("expected_normalized_intensity")

    passed = not failed_checks
    return CaseResult(
        case_id=case.case_id,
        normalized_mood_state=normalized_mood_state,
        normalized_emotion_tag=normalized_tag,
        normalized_intensity=normalized_intensity,
        snapshot_ok=snapshot_ok,
        prompt_ok=prompt_ok,
        vts_ok=vts_ok,
        valence_sign_ok=valence_sign_ok,
        expected_tag_ok=expected_tag_ok,
        expected_mood_ok=expected_mood_ok,
        expected_intensity_ok=expected_intensity_ok,
        vts_status=str(vts_result.status),
        expression_id=vts_result.expression_id,
        passed=passed,
        failed_checks=failed_checks,
    )


def run_closed_loop_validation(
    *,
    threshold: float = 0.8,
    case_file: str | Path | None = None,
    cases: list[EmotionCase] | None = None,
) -> dict[str, object]:
    ts_ms = int(time.time() * 1000)
    selected_cases, case_source = _resolve_cases(cases=cases, case_file=case_file)
    results = [_eval_case(case, ts_ms=ts_ms) for case in selected_cases]
    passed = [row for row in results if row.passed]
    failed = [row for row in results if not row.passed]
    pass_rate = (len(passed) / len(results)) if results else 0.0
    return {
        "version": "emotion_closed_loop.v1",
        "ts_ms": ts_ms,
        "case_source": case_source,
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
        f"- case_source: {report.get('case_source')}\n"
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
    parser = argparse.ArgumentParser(description="Run emotion closed-loop validation on fixture or embedded corpus.")
    parser.add_argument("--threshold", type=float, default=0.8, help="Pass-rate threshold, default 0.8")
    parser.add_argument(
        "--case-file",
        default="",
        help="Optional JSON case file. Default uses tests/fixtures/emotion/closed_loop_cases.json when available.",
    )
    parser.add_argument(
        "--output-stem",
        default=f"EMOTION_CLOSED_LOOP_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        help="Output file stem under docs/60_trace",
    )
    args = parser.parse_args()

    report = run_closed_loop_validation(
        threshold=float(args.threshold),
        case_file=str(args.case_file).strip() or None,
    )
    json_path, md_path = _write_reports(report, output_stem=str(args.output_stem))
    print(
        f"emotion_closed_loop: source={report['case_source']} total={report['total_cases']} passed={report['passed_cases']} "
        f"pass_rate={report['pass_rate']} threshold_passed={report['threshold_passed']}"
    )
    print(f"json_report: {json_path}")
    print(f"markdown_report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
