from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.chat_ab_generate import (
    BASELINE_VARIANTS,
    KERNEL_V2_VARIANTS,
    generate_candidates,
    load_cases,
    load_dotenv,
    parse_variants,
    write_jsonl,
)
from tools.chat_ab_gold_score import run_gold_score
from tools.chat_ab_judge_codex import SCORE_KEYS, judge_candidates, load_jsonl


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * p
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] * (1 - frac) + ordered[high] * frac)


def _score_total(scores: Mapping[str, Any]) -> int:
    payload = dict(scores or {})
    return sum(max(0, min(10, int(payload.get(key) or 0))) for key in SCORE_KEYS)


def _score_value(scores: Mapping[str, Any], key: str) -> int:
    payload = dict(scores or {})
    return max(0, min(10, int(payload.get(key) or 0)))


def aggregate_decision(
    *,
    candidate_rows: list[Mapping[str, Any]],
    judgement_rows: list[Mapping[str, Any]],
    variants: list[str],
) -> dict[str, Any]:
    variants = [str(item).strip() for item in variants if str(item).strip()]
    case_count = len({str(row.get("case_id") or "").strip() for row in judgement_rows if str(row.get("case_id") or "").strip()})
    candidate_by_variant: dict[str, list[Mapping[str, Any]]] = {variant: [] for variant in variants}
    for row in candidate_rows:
        variant = str(row.get("variant") or "").strip()
        candidate_by_variant.setdefault(variant, []).append(row)

    current_scores: list[int] = []
    score_by_variant: dict[str, list[int]] = {variant: [] for variant in variants}
    wins_by_variant: dict[str, int] = {variant: 0 for variant in variants}
    hard_failure_cases_by_variant: dict[str, int] = {variant: 0 for variant in variants}

    for judgement in judgement_rows:
        winner = str(judgement.get("winner") or "").strip()
        if winner in wins_by_variant:
            wins_by_variant[winner] += 1
        scores = dict(judgement.get("scores") or {})
        hard_failures = dict(judgement.get("hard_failures") or {})
        current_total = _score_total(dict(scores.get("current") or {}))
        if "current" in variants:
            current_scores.append(current_total)
        for variant in variants:
            score_by_variant.setdefault(variant, []).append(_score_total(dict(scores.get(variant) or {})))
            if list(hard_failures.get(variant) or []):
                hard_failure_cases_by_variant[variant] = hard_failure_cases_by_variant.get(variant, 0) + 1

    current_avg = statistics.mean(current_scores) if current_scores else 0.0
    current_p95 = _percentile(
        [float(row.get("latency_ms") or 0) for row in candidate_by_variant.get("current", [])],
        0.95,
    )
    candidate_decisions: dict[str, dict[str, Any]] = {}
    for variant in variants:
        if variant == "current":
            continue
        avg_score = statistics.mean(score_by_variant.get(variant) or [0])
        win_rate = (wins_by_variant.get(variant, 0) / case_count) if case_count else 0.0
        hard_fail_rate = (hard_failure_cases_by_variant.get(variant, 0) / case_count) if case_count else 1.0
        p95_latency = _percentile(
            [float(row.get("latency_ms") or 0) for row in candidate_by_variant.get(variant, [])],
            0.95,
        )
        latency_limit = current_p95 * 1.2 if current_p95 > 0 else float("inf")
        promoted = (
            win_rate >= 0.70
            and hard_fail_rate <= 0.05
            and avg_score >= current_avg + 8.0
            and p95_latency <= latency_limit
        )
        candidate_decisions[variant] = {
            "promoted": bool(promoted),
            "win_rate": win_rate,
            "hard_fail_rate": hard_fail_rate,
            "avg_score": avg_score,
            "current_avg_score": current_avg,
            "avg_score_delta": avg_score - current_avg,
            "p95_latency_ms": p95_latency,
            "current_p95_latency_ms": current_p95,
            "latency_limit_ms": latency_limit if latency_limit != float("inf") else None,
            "wins": wins_by_variant.get(variant, 0),
            "case_count": case_count,
        }

    best_variant = ""
    for variant in variants:
        if variant != "current" and candidate_decisions.get(variant, {}).get("promoted"):
            best_variant = variant
    if not best_variant:
        best_variant = "current"

    return {
        "case_count": case_count,
        "variants": variants,
        "current": {
            "avg_score": current_avg,
            "p95_latency_ms": current_p95,
            "wins": wins_by_variant.get("current", 0),
        },
        "candidates": candidate_decisions,
        "selected_variant": best_variant,
        "promotion_rules": {
            "win_rate_gte": 0.70,
            "hard_fail_rate_lte": 0.05,
            "avg_score_delta_gte": 8.0,
            "p95_latency_lte_current_multiplier": 1.2,
        },
    }


def _case_lookup(cases: list[Mapping[str, Any]] | None) -> dict[str, Mapping[str, Any]]:
    return {
        str(case.get("case_id") or "").strip(): case
        for case in list(cases or [])
        if str(case.get("case_id") or "").strip()
    }


def _candidate_output_lookup(candidate_rows: list[Mapping[str, Any]] | None) -> dict[tuple[str, str], Mapping[str, Any]]:
    output: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in list(candidate_rows or []):
        case_id = str(row.get("case_id") or "").strip()
        variant = str(row.get("variant") or "").strip()
        if case_id and variant:
            output[(case_id, variant)] = row
    return output


def _case_language_track(case: Mapping[str, Any]) -> str:
    explicit = str(case.get("language_track") or case.get("render_profile") or "").strip().lower()
    if explicit:
        return explicit
    response_form = str(case.get("response_form") or "").strip().lower()
    if response_form == "voice":
        return "ja_voice"
    if response_form in {"sticker", "emoji", "image", "gif", "media_action"}:
        return "media_action"
    return "zh_text"


def build_language_track_summary(
    *,
    cases: list[Mapping[str, Any]] | None,
    candidate_rows: list[Mapping[str, Any]] | None,
    judgement_rows: list[Mapping[str, Any]] | None,
    variants: list[str],
) -> dict[str, Any]:
    variants = [str(item).strip() for item in variants if str(item).strip()]
    cases_by_id = _case_lookup(cases)
    candidates_by_key = _candidate_output_lookup(candidate_rows)
    summary: dict[str, Any] = {}
    for judgement in list(judgement_rows or []):
        case_id = str(judgement.get("case_id") or "").strip()
        if not case_id:
            continue
        case = dict(cases_by_id.get(case_id) or {})
        track = _case_language_track(case)
        track_row = summary.setdefault(
            track,
            {
                "case_count": 0,
                "variants": {
                    variant: {
                        "wins": 0,
                        "hard_failure_cases": 0,
                        "scores": [],
                        "latencies_ms": [],
                    }
                    for variant in variants
                },
            },
        )
        track_row["case_count"] = int(track_row.get("case_count") or 0) + 1
        winner = str(judgement.get("winner") or "").strip()
        scores = dict(judgement.get("scores") or {})
        hard_failures = dict(judgement.get("hard_failures") or {})
        for variant in variants:
            variant_row = track_row["variants"].setdefault(
                variant,
                {"wins": 0, "hard_failure_cases": 0, "scores": [], "latencies_ms": []},
            )
            if winner == variant:
                variant_row["wins"] = int(variant_row.get("wins") or 0) + 1
            variant_row["scores"].append(_score_total(dict(scores.get(variant) or {})))
            if list(hard_failures.get(variant) or []):
                variant_row["hard_failure_cases"] = int(variant_row.get("hard_failure_cases") or 0) + 1
            candidate = dict(candidates_by_key.get((case_id, variant)) or {})
            variant_row["latencies_ms"].append(float(candidate.get("latency_ms") or 0))

    compact: dict[str, Any] = {}
    for track, payload in summary.items():
        case_count = int(dict(payload).get("case_count") or 0)
        compact_variants: dict[str, Any] = {}
        for variant, raw_variant_payload in dict(dict(payload).get("variants") or {}).items():
            row = dict(raw_variant_payload or {})
            scores = [float(item) for item in list(row.get("scores") or [])]
            latencies = [float(item) for item in list(row.get("latencies_ms") or [])]
            compact_variants[variant] = {
                "wins": int(row.get("wins") or 0),
                "win_rate": (float(row.get("wins") or 0) / case_count) if case_count else 0.0,
                "hard_fail_rate": (float(row.get("hard_failure_cases") or 0) / case_count) if case_count else 0.0,
                "avg_score": statistics.mean(scores) if scores else 0.0,
                "p95_latency_ms": _percentile(latencies, 0.95),
            }
        compact[track] = {"case_count": case_count, "variants": compact_variants}
    return compact


def _format_report_cell(value: Any, *, max_len: int = 220) -> str:
    text = str(value or "").strip()
    if not text:
        return "<no reply>"
    text = text.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    if len(text) > max_len:
        return f"{text[: max_len - 1]}…"
    return text


def _format_query(case: Mapping[str, Any]) -> str:
    history = list(case.get("history_turns") or [])
    user_message = str(case.get("user_message") or "").strip()
    if not history:
        return user_message
    tail = " / ".join(
        f"{str(turn.get('role') or '').strip()}:{str(turn.get('content') or '').strip()}"
        for turn in history[-3:]
        if isinstance(turn, Mapping)
    )
    return f"{tail} -> user:{user_message}" if tail else user_message


def build_report(
    decision: Mapping[str, Any],
    judgements: list[Mapping[str, Any]],
    *,
    candidate_rows: list[Mapping[str, Any]] | None = None,
    cases: list[Mapping[str, Any]] | None = None,
) -> str:
    lines = [
        "# Chat A/B Report",
        "",
        f"- Cases: {int(decision.get('case_count') or 0)}",
        f"- Variants: {', '.join(list(decision.get('variants') or []))}",
        f"- Selected: `{decision.get('selected_variant')}`",
        "",
        "## Summary",
        "",
        "| Variant | Promoted | Win Rate | Hard Fail | Avg Score | Delta | P95 Latency |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    current = dict(decision.get("current") or {})
    lines.append(
        f"| current | - | - | - | {float(current.get('avg_score') or 0):.2f} | - | {float(current.get('p95_latency_ms') or 0):.0f}ms |"
    )
    for variant, payload in dict(decision.get("candidates") or {}).items():
        row = dict(payload or {})
        lines.append(
            "| {variant} | {promoted} | {win_rate:.1%} | {hard_fail:.1%} | {avg:.2f} | {delta:+.2f} | {p95:.0f}ms |".format(
                variant=variant,
                promoted="yes" if row.get("promoted") else "no",
                win_rate=float(row.get("win_rate") or 0),
                hard_fail=float(row.get("hard_fail_rate") or 0),
                avg=float(row.get("avg_score") or 0),
                delta=float(row.get("avg_score_delta") or 0),
                p95=float(row.get("p95_latency_ms") or 0),
            )
        )
    variants = [str(item).strip() for item in list(decision.get("variants") or []) if str(item).strip()]
    if judgements and variants:
        lines.extend(["", "## Dimension Averages", ""])
        lines.append("| Dimension | " + " | ".join(variants) + " |")
        lines.append("|" + "|".join(["---", *[":---:" for _ in variants]]) + "|")
        for key in SCORE_KEYS:
            row_values = [key]
            for variant in variants:
                values = [
                    _score_value(dict(dict(judgement.get("scores") or {}).get(variant) or {}), key)
                    for judgement in judgements
                ]
                avg = statistics.mean(values) if values else 0.0
                row_values.append(f"{avg:.2f}")
            lines.append("| " + " | ".join(row_values) + " |")
    track_summary = dict(decision.get("track_summary") or {})
    if track_summary:
        lines.extend(["", "## Language Track Summary", ""])
        lines.append("| Track | Cases | Variant | Wins | Win Rate | Hard Fail | Avg Score | P95 Latency |")
        lines.append("|---|---:|---|---:|---:|---:|---:|---:|")
        for track, payload in sorted(track_summary.items()):
            track_payload = dict(payload or {})
            case_count = int(track_payload.get("case_count") or 0)
            for variant, raw_row in dict(track_payload.get("variants") or {}).items():
                row = dict(raw_row or {})
                lines.append(
                    "| {track} | {cases} | {variant} | {wins} | {win_rate:.1%} | {hard_fail:.1%} | {avg:.2f} | {p95:.0f}ms |".format(
                        track=track,
                        cases=case_count,
                        variant=variant,
                        wins=int(row.get("wins") or 0),
                        win_rate=float(row.get("win_rate") or 0),
                        hard_fail=float(row.get("hard_fail_rate") or 0),
                        avg=float(row.get("avg_score") or 0),
                        p95=float(row.get("p95_latency_ms") or 0),
                    )
                )
    candidates_by_key = _candidate_output_lookup(candidate_rows)
    cases_by_id = _case_lookup(cases)
    if candidate_rows and cases:
        lines.extend(["", "## Case Outputs", ""])
        header = ["Case", "Query", *variants]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for case in list(cases or []):
            case_id = str(case.get("case_id") or "").strip()
            row_values = [
                f"`{case_id}`",
                _format_report_cell(_format_query(case), max_len=260),
            ]
            for variant in variants:
                candidate = dict(candidates_by_key.get((case_id, variant)) or {})
                envelope = dict(candidate.get("envelope") or {})
                text = str(candidate.get("text") or envelope.get("text") or "").strip()
                if text:
                    rendered = _format_report_cell(text)
                else:
                    reason = str(candidate.get("error") or envelope.get("reason_code") or "").strip()
                    rendered = f"<no reply>{': ' + _format_report_cell(reason, max_len=80) if reason else ''}"
                row_values.append(rendered)
            lines.append("| " + " | ".join(row_values) + " |")
    lines.extend(["", "## Case Judgements", ""])
    for judgement in judgements:
        case_id = str(judgement.get("case_id") or "").strip()
        case = dict(cases_by_id.get(case_id) or {})
        query = f" query=`{_format_report_cell(_format_query(case), max_len=140)}`" if case else ""
        lines.append(
            f"- `{judgement.get('case_id')}`{query} winner=`{judgement.get('winner')}` confidence={float(judgement.get('confidence') or 0):.2f}: {judgement.get('reason')}"
        )
    lines.append("")
    return "\n".join(lines)


def apply_auto_promote_stop_rule(decision: dict[str, Any]) -> dict[str, Any]:
    candidates = dict(decision.get("candidates") or {})
    selected = "current"
    stopped_at = ""
    for variant in BASELINE_VARIANTS:
        if variant not in candidates:
            continue
        if bool(dict(candidates.get(variant) or {}).get("promoted")):
            selected = variant
            continue
        stopped_at = variant
        break
    decision["selected_variant"] = selected
    decision["auto_promote"] = {
        "enabled": True,
        "selected_contiguous_variant": selected,
        "stopped_at": stopped_at,
        "stop_reason": "candidate_failed_promotion_rules" if stopped_at else "all_candidates_promoted",
    }
    return decision


def expand_auto_promote_variants(variants: list[str], *, auto_promote: bool) -> list[str]:
    normalized = parse_variants(",".join(variants))
    if not auto_promote:
        return normalized
    if any(item in KERNEL_V2_VARIANTS for item in normalized):
        if normalized == ["current", "kernel_v2"] or normalized == ["kernel_v2", "current"]:
            return ["current", *KERNEL_V2_VARIANTS]
        return normalized
    if not any(item in BASELINE_VARIANTS for item in normalized):
        return ["current", *KERNEL_V2_VARIANTS]
    if normalized == ["current", "baseline_soul"] or normalized == ["baseline_soul", "current"]:
        return ["current", *BASELINE_VARIANTS]
    return normalized


async def run_workflow(
    *,
    cases_path: Path,
    variants: list[str],
    judge: str,
    out_dir: Path,
    auto_promote: bool = False,
    mock_generate: bool = False,
    codex_bin: str = "codex",
    codex_model: str = "gpt-5.4",
    codex_batch_size: int = 4,
    baseline_model_id: str = "",
    gold_path: Path | None = None,
) -> dict[str, Any]:
    load_dotenv()
    expanded_variants = expand_auto_promote_variants(variants, auto_promote=auto_promote)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = load_cases(cases_path)
    candidate_rows = await generate_candidates(
        cases=cases,
        variants=expanded_variants,
        mock=mock_generate,
        baseline_model_id=baseline_model_id,
    )
    candidates_path = out_dir / "raw_candidates.jsonl"
    write_jsonl(candidates_path, candidate_rows)
    judgement_rows = judge_candidates(
        cases_path=cases_path,
        candidates_path=candidates_path,
        out_dir=out_dir,
        schema_path=ROOT / "tests" / "fixtures" / "chat_ab" / "judge.schema.json",
        gold_path=gold_path,
        codex_bin=codex_bin,
        codex_model=codex_model,
        batch_size=int(codex_batch_size or 1),
        mock=(judge == "mock"),
    )
    decision = aggregate_decision(
        candidate_rows=candidate_rows,
        judgement_rows=judgement_rows,
        variants=expanded_variants,
    )
    decision["track_summary"] = build_language_track_summary(
        cases=cases,
        candidate_rows=candidate_rows,
        judgement_rows=judgement_rows,
        variants=expanded_variants,
    )
    if auto_promote:
        decision = apply_auto_promote_stop_rule(decision)
    (out_dir / "decision.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    report_text = build_report(decision, judgement_rows, candidate_rows=candidate_rows, cases=cases)
    if gold_path and gold_path.exists():
        gold_summary = run_gold_score(
            cases_path=cases_path,
            gold_path=gold_path,
            candidates_path=candidates_path,
            out_dir=out_dir,
        )
        decision["gold_summary"] = gold_summary
        (out_dir / "decision.json").write_text(
            json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        report_text += "\n\n## Gold Score Guardrail\n\n"
        report_text += f"- Full gold report: `{out_dir / 'gold_report.md'}`\n"
        for variant, payload in dict(gold_summary.get("variants") or {}).items():
            row = dict(payload or {})
            report_text += (
                f"- `{variant}` avg={float(row.get('avg_score') or 0):.1f}, "
                f"pass_rate={float(row.get('pass_rate') or 0):.1%}, "
                f"hard_fail={float(row.get('hard_fail_rate') or 0):.1%}\n"
            )
    (out_dir / "report.md").write_text(report_text, encoding="utf-8")
    return decision


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full current-vs-kernel chat A/B workflow.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--variants", default="current,kernel_v2")
    parser.add_argument("--judge", choices=("codex", "mock"), default="codex")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--auto-promote", action="store_true")
    parser.add_argument("--mock-generate", action="store_true")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--codex-model", default="gpt-5.4")
    parser.add_argument("--codex-batch-size", type=int, default=4)
    parser.add_argument("--baseline-model", default=os.environ.get("CHAT_AB_BASELINE_MODEL_ID", "gemini-2.5-flash-lite"))
    parser.add_argument("--gold", type=Path, default=None)
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    decision = await run_workflow(
        cases_path=args.cases,
        variants=parse_variants(str(args.variants or "current,kernel_v2")),
        judge=str(args.judge or "codex"),
        out_dir=args.out,
        auto_promote=bool(args.auto_promote),
        mock_generate=bool(args.mock_generate),
        codex_bin=str(args.codex_bin or "codex"),
        codex_model=str(args.codex_model or ""),
        codex_batch_size=int(args.codex_batch_size or 1),
        baseline_model_id=str(args.baseline_model or ""),
        gold_path=args.gold,
    )
    print(f"selected={decision.get('selected_variant')} report={args.out / 'report.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
