from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.chat_ab_generate import (
    generate_candidates,
    load_cases,
    load_dotenv,
    parse_variants,
    write_jsonl,
)
from tools.chat_ab_gold_score import run_gold_score
from tools.chat_ab_judge_codex import load_jsonl


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def summarize_repeat_runs(
    *,
    run_summaries: list[Mapping[str, Any]],
    score_rows_by_run: list[list[Mapping[str, Any]]],
) -> dict[str, Any]:
    variant_payloads: dict[str, list[Mapping[str, Any]]] = {}
    for summary in run_summaries:
        for variant, payload in dict(summary.get("variants") or {}).items():
            variant_payloads.setdefault(str(variant), []).append(dict(payload or {}))

    variants: dict[str, dict[str, Any]] = {}
    for variant, payloads in variant_payloads.items():
        avg_scores = [float(item.get("avg_score") or 0.0) for item in payloads]
        pass_rates = [float(item.get("pass_rate") or 0.0) for item in payloads]
        hard_fail_rates = [float(item.get("hard_fail_rate") or 0.0) for item in payloads]
        error_rates = [float(item.get("error_rate") or 0.0) for item in payloads]
        min_scores = [int(item.get("min_score") or 0) for item in payloads]
        variants[variant] = {
            "runs": len(payloads),
            "avg_score_mean": _mean(avg_scores),
            "avg_score_min": min(avg_scores) if avg_scores else 0.0,
            "pass_rate_mean": _mean(pass_rates),
            "pass_rate_min": min(pass_rates) if pass_rates else 0.0,
            "hard_fail_rate_max": max(hard_fail_rates) if hard_fail_rates else 0.0,
            "error_rate_max": max(error_rates) if error_rates else 0.0,
            "min_score_min": min(min_scores) if min_scores else 0,
        }

    failed_cases: dict[str, dict[str, Any]] = {}
    for run_index, rows in enumerate(score_rows_by_run, start=1):
        for row in rows:
            if bool(row.get("passed")):
                continue
            key = str(row.get("case_id") or "").strip()
            if not key:
                continue
            payload = failed_cases.setdefault(
                key,
                {
                    "case_id": key,
                    "count": 0,
                    "runs": [],
                    "min_score": 100,
                    "examples": [],
                },
            )
            payload["count"] += 1
            payload["runs"].append(run_index)
            payload["min_score"] = min(int(payload["min_score"]), int(row.get("score") or 0))
            if len(payload["examples"]) < 3:
                payload["examples"].append(
                    {
                        "run": run_index,
                        "variant": str(row.get("variant") or ""),
                        "score": int(row.get("score") or 0),
                        "text": str(row.get("text") or "")[:160],
                        "failures": list(row.get("hard_failures") or []),
                        "penalties": list(row.get("penalties") or [])[:3],
                    }
                )

    top_failed_cases = sorted(
        failed_cases.values(),
        key=lambda item: (-int(item.get("count") or 0), int(item.get("min_score") or 0), str(item.get("case_id") or "")),
    )
    return {
        "runs": len(run_summaries),
        "variants": variants,
        "top_failed_cases": top_failed_cases,
    }


def build_repeat_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Chat Repeat Quality Report",
        "",
        "多轮离线质量护栏：用于观察同一 cases 在随机生成下的稳定性。",
        "",
        "## Summary",
        "",
        "| Variant | Runs | Avg Mean | Avg Min | Pass Mean | Pass Min | Hard Fail Max | Error Max | Min Score |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for variant, payload in dict(summary.get("variants") or {}).items():
        item = dict(payload or {})
        lines.append(
            f"| {variant} | {int(item.get('runs') or 0)} | {float(item.get('avg_score_mean') or 0):.2f} | "
            f"{float(item.get('avg_score_min') or 0):.2f} | {float(item.get('pass_rate_mean') or 0):.1%} | "
            f"{float(item.get('pass_rate_min') or 0):.1%} | {float(item.get('hard_fail_rate_max') or 0):.1%} | "
            f"{float(item.get('error_rate_max') or 0):.1%} | {int(item.get('min_score_min') or 0)} |"
        )
    lines.extend(["", "## Failed Cases", "", "| Case | Count | Runs | Min | Example |", "|---|---:|---|---:|---|"])
    for item in list(summary.get("top_failed_cases") or [])[:20]:
        examples = list(item.get("examples") or [])
        example = dict(examples[0] or {}) if examples else {}
        text = str(example.get("text") or "").replace("|", "\\|")
        lines.append(
            f"| `{item.get('case_id')}` | {int(item.get('count') or 0)} | "
            f"{','.join(str(run) for run in list(item.get('runs') or []))} | {int(item.get('min_score') or 0)} | {text} |"
        )
    lines.append("")
    return "\n".join(lines)


async def run_repeat_quality(
    *,
    cases_path: Path,
    gold_path: Path,
    variants: list[str],
    out_dir: Path,
    runs: int = 3,
    mock: bool = False,
    limit: int = 0,
    case_delay_ms: int = 0,
    max_case_retries: int = 0,
    error_backoff_ms: int = 0,
) -> dict[str, Any]:
    if runs <= 0:
        raise ValueError("runs must be positive")
    load_dotenv()
    cases = load_cases(cases_path)
    if int(limit or 0) > 0:
        cases = cases[: int(limit)]
    parsed_variants = parse_variants(",".join(variants))
    out_dir.mkdir(parents=True, exist_ok=True)

    run_summaries: list[Mapping[str, Any]] = []
    score_rows_by_run: list[list[Mapping[str, Any]]] = []
    for run_index in range(1, runs + 1):
        run_dir = out_dir / f"run_{run_index:02d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        candidate_rows = await generate_candidates(
            cases=cases,
            variants=parsed_variants,
            mock=mock,
            baseline_provider="",
            baseline_model_id="",
            case_delay_ms=case_delay_ms,
            max_case_retries=max_case_retries,
            error_backoff_ms=error_backoff_ms,
        )
        candidates_path = run_dir / "raw_candidates.jsonl"
        write_jsonl(candidates_path, candidate_rows)
        run_summary = run_gold_score(
            cases_path=cases_path,
            gold_path=gold_path,
            candidates_path=candidates_path,
            out_dir=run_dir,
        )
        run_summaries.append(run_summary)
        score_rows_by_run.append(load_jsonl(run_dir / "gold_scores.jsonl"))

    summary = summarize_repeat_runs(run_summaries=run_summaries, score_rows_by_run=score_rows_by_run)
    (out_dir / "repeat_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "repeat_report.md").write_text(build_repeat_report(summary), encoding="utf-8")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run chat generation + gold scoring multiple times.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--variants", default="kernel_v2_plain_soul")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--case-delay-ms", type=int, default=0)
    parser.add_argument("--max-case-retries", type=int, default=0)
    parser.add_argument("--error-backoff-ms", type=int, default=0)
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    variants = [item.strip() for item in str(args.variants or "").split(",") if item.strip()]
    summary = await run_repeat_quality(
        cases_path=args.cases,
        gold_path=args.gold,
        variants=variants,
        out_dir=args.out,
        runs=int(args.runs or 1),
        mock=bool(args.mock),
        limit=int(args.limit or 0),
        case_delay_ms=int(args.case_delay_ms or 0),
        max_case_retries=int(args.max_case_retries or 0),
        error_backoff_ms=int(args.error_backoff_ms or 0),
    )
    print(f"wrote repeat quality report -> {args.out / 'repeat_report.md'}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
