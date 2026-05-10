from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.chat_ab_generate import load_cases, write_jsonl
from tools.chat_ab_gold_score import load_gold_entries, load_jsonl, score_candidate


def _pick_key(row: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        int(row.get("score") or 0),
        -len(list(row.get("hard_failures") or [])),
        -int(row.get("latency_ms") or 0),
    )


def build_oracle_rows(
    *,
    cases: list[Mapping[str, Any]],
    gold_entries: Mapping[str, Mapping[str, Any]],
    candidate_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    case_by_id = {str(case.get("case_id") or "").strip(): case for case in cases}
    best_by_case: dict[str, dict[str, Any]] = {}
    for candidate in candidate_rows:
        case_id = str(candidate.get("case_id") or "").strip()
        if not case_id:
            continue
        scored = score_candidate(
            case=case_by_id.get(case_id, {"case_id": case_id, "user_message": ""}),
            gold=gold_entries.get(case_id),
            candidate=candidate,
        )
        scored["source_variant"] = str(candidate.get("variant") or "").strip()
        scored["trace"] = dict(candidate.get("trace") or {})
        current = best_by_case.get(case_id)
        if current is None or _pick_key(scored) > _pick_key(current):
            best_by_case[case_id] = scored
    return [
        best_by_case[str(case.get("case_id") or "").strip()]
        for case in cases
        if str(case.get("case_id") or "").strip() in best_by_case
    ]


def summarize_oracle(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    scores = [int(row.get("score") or 0) for row in rows]
    pass_count = sum(1 for row in rows if bool(row.get("passed")))
    hard_fail_count = sum(1 for row in rows if list(row.get("hard_failures") or []))
    return {
        "cases": len(rows),
        "avg_score": (sum(scores) / len(scores)) if scores else 0.0,
        "min_score": min(scores) if scores else 0,
        "pass_rate": (pass_count / len(rows)) if rows else 0.0,
        "hard_fail_rate": (hard_fail_count / len(rows)) if rows else 0.0,
    }


def _cell(value: Any, *, max_len: int = 180) -> str:
    text = str(value or "").strip().replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def build_oracle_report(rows: list[Mapping[str, Any]], summary: Mapping[str, Any]) -> str:
    lines = [
        "# Chat Oracle Best Report",
        "",
        "Oracle 只用于诊断：它从同一 case 的多个候选里取离线评分最高的一条，判断生成池是否已经包含好回复。",
        "",
        "## Summary",
        "",
        f"- Cases: {int(summary.get('cases') or 0)}",
        f"- Avg: {float(summary.get('avg_score') or 0):.1f}",
        f"- Pass Rate: {float(summary.get('pass_rate') or 0):.1%}",
        f"- Hard Fail: {float(summary.get('hard_fail_rate') or 0):.1%}",
        f"- Min: {int(summary.get('min_score') or 0)}",
        "",
        "## Oracle Rows",
        "",
        "| Case | User | Best Variant | Output | Score | Failures |",
        "|---|---|---|---|---:|---|",
    ]
    for row in rows:
        failures = " / ".join(list(row.get("hard_failures") or []) or list(row.get("penalties") or [])[:2])
        lines.append(
            "| {case} | {user} | {variant} | {output} | {score} | {failures} |".format(
                case=f"`{_cell(row.get('case_id'), max_len=80)}`",
                user=_cell(row.get("user_message"), max_len=80),
                variant=_cell(row.get("source_variant") or row.get("variant"), max_len=80),
                output=_cell(row.get("text"), max_len=180) or "<no reply>",
                score=int(row.get("score") or 0),
                failures=_cell(failures, max_len=160),
            )
        )
    lines.append("")
    return "\n".join(lines)


def run_oracle(
    *,
    cases_path: Path,
    gold_path: Path,
    candidates_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    gold_entries = load_gold_entries(gold_path)
    candidate_rows = load_jsonl(candidates_path)
    rows = build_oracle_rows(cases=cases, gold_entries=gold_entries, candidate_rows=candidate_rows)
    summary = summarize_oracle(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "oracle_best.jsonl", rows)
    (out_dir / "oracle_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "oracle_report.md").write_text(build_oracle_report(rows, summary), encoding="utf-8")
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pick per-case oracle best replies from A/B candidate rows.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = run_oracle(
        cases_path=args.cases,
        gold_path=args.gold,
        candidates_path=args.candidates,
        out_dir=args.out,
    )
    print(f"wrote oracle report -> {args.out / 'oracle_report.md'}")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
