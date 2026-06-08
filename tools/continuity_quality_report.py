from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_CONSTRAINT_LEAK_MARKERS = (
    "不能问问题",
    "不要问",
    "系统规则",
    "规则要求",
    "系统",
    "候选",
    "must_include",
    "must_not_do",
    "question_budget",
    "prompt",
    "字段",
)
_STIFF_MARKERS = (
    "具体点就是",
    "先发我",
    "我帮你看看嘛",
    "听好了",
)
_CLINGY_MARKERS = (
    "缠着你",
    "不准拒绝",
    "一直陪",
    "永远陪",
    "回来陪我",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_no} must be a JSON object")
        rows.append(payload)
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _contains_any(text: str, markers: Iterable[str]) -> list[str]:
    return [marker for marker in markers if marker and marker in text]


def quality_watch_flags(text: str, selected: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    reason_codes = [str(item) for item in selected.get("reason_codes") or []]
    if _contains_any(text, _CONSTRAINT_LEAK_MARKERS):
        flags.append("constraint_language_leak")
    if _contains_any(text, _STIFF_MARKERS):
        flags.append("stiff_or_toolish_surface")
    if _contains_any(text, _CLINGY_MARKERS):
        flags.append("clingy_distance_risk")
    if len(text) > 42 and any(item in text for item in ("。", "，", "……")):
        flags.append("long_for_light_chat")
    if any(code in reason_codes for code in ("LOW_CATCHINESS", "WEAK_SCENE_GROUNDING", "CONTEXT_MISSING_REQUIRED_MOVE")):
        flags.append("scorer_soft_warning")
    return list(dict.fromkeys(flags))


def build_report(rows: list[dict[str, Any]], *, weak_threshold: float = 83.0) -> dict[str, Any]:
    selected_rows: list[dict[str, Any]] = []
    flag_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    dialogue_counts: Counter[str] = Counter()
    dialogue_passed: Counter[str] = Counter()

    for row in rows:
        selected = row.get("selected")
        if not isinstance(selected, Mapping):
            continue
        text = str(selected.get("text") or "")
        score = float(selected.get("score") or 0.0)
        flags = quality_watch_flags(text, selected)
        for flag in flags:
            flag_counts[flag] += 1
        for reason in selected.get("reason_codes") or []:
            reason_counts[str(reason)] += 1
        case_id = str(row.get("case_id") or "")
        if case_id:
            dialogue_counts[case_id] += 1
            if selected.get("passed"):
                dialogue_passed[case_id] += 1
        selected_rows.append(
            {
                "case_id": case_id,
                "turn_id": str(row.get("turn_id") or ""),
                "turn_index": int(row.get("turn_index") or 0),
                "text": text,
                "score": round(score, 3),
                "passed": bool(selected.get("passed")),
                "hard_fail": bool(selected.get("hard_fail")),
                "control_violations": list(selected.get("control_violations") or []),
                "reason_codes": list(selected.get("reason_codes") or []),
                "watch_flags": flags,
            }
        )

    scores = [float(row["score"]) for row in selected_rows]
    weak_rows = [
        row
        for row in selected_rows
        if (not row["passed"]) or float(row["score"]) < weak_threshold or row["watch_flags"]
    ]
    dialogue_pass_count = sum(
        1
        for case_id, count in dialogue_counts.items()
        if count > 0 and dialogue_passed.get(case_id, 0) == count
    )
    return {
        "selected_count": len(selected_rows),
        "pass_count": sum(1 for row in selected_rows if row["passed"]),
        "pass_rate": round(sum(1 for row in selected_rows if row["passed"]) / len(selected_rows), 4) if selected_rows else None,
        "dialogue_count": len(dialogue_counts),
        "dialogue_pass_count": dialogue_pass_count,
        "dialogue_pass_rate": round(dialogue_pass_count / len(dialogue_counts), 4) if dialogue_counts else None,
        "avg_score": round(statistics.fmean(scores), 3) if scores else None,
        "min_score": round(min(scores), 3) if scores else None,
        "weak_threshold": float(weak_threshold),
        "watch_flag_counts": dict(sorted(flag_counts.items())),
        "reason_counts": dict(reason_counts.most_common(20)),
        "watch_rows": sorted(weak_rows, key=lambda row: (0 if row["watch_flags"] else 1, float(row["score"]))),
    }


def strict_failed(
    report: Mapping[str, Any],
    *,
    min_pass_rate: float = 1.0,
    min_dialogue_pass_rate: float = 1.0,
    max_watch_rows: int = 0,
) -> bool:
    pass_rate = float(report.get("pass_rate") or 0.0)
    dialogue_pass_rate = float(report.get("dialogue_pass_rate") or 0.0)
    watch_rows = list(report.get("watch_rows") or [])
    return (
        pass_rate < float(min_pass_rate)
        or dialogue_pass_rate < float(min_dialogue_pass_rate)
        or len(watch_rows) > int(max_watch_rows)
    )


def render_markdown(report: Mapping[str, Any], *, source: Path) -> str:
    lines = [
        "# Continuity Quality Report",
        "",
        f"- source: `{source}`",
        f"- selected: `{report['selected_count']}`",
        f"- pass: `{report['pass_count']}/{report['selected_count']}`",
        f"- dialogue pass: `{report['dialogue_pass_count']}/{report['dialogue_count']}`",
        f"- avg score: `{report['avg_score']}`",
        f"- min score: `{report['min_score']}`",
        f"- weak threshold: `{report['weak_threshold']}`",
        "",
        "## Watch Flags",
        "",
    ]
    flag_counts = report.get("watch_flag_counts") or {}
    if flag_counts:
        for flag, count in flag_counts.items():
            lines.append(f"- `{flag}`: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Watch Rows", ""])
    watch_rows = list(report.get("watch_rows") or [])
    if not watch_rows:
        lines.append("- none")
    for row in watch_rows[:40]:
        flags = ", ".join(f"`{flag}`" for flag in row.get("watch_flags") or []) or "-"
        reasons = ", ".join(f"`{reason}`" for reason in row.get("reason_codes") or []) or "-"
        lines.extend(
            [
                f"### {row['case_id']} / {row['turn_id']}",
                "",
                f"- score: `{row['score']}`, passed: `{row['passed']}`",
                f"- flags: {flags}",
                f"- reasons: {reasons}",
                f"- text: {row['text']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a human-facing quality watch report for continuity live scores.")
    parser.add_argument("--scores", required=True, type=Path, help="live_scores.jsonl from continuity live runner.")
    parser.add_argument("--out", required=True, type=Path, help="Output directory.")
    parser.add_argument("--weak-threshold", type=float, default=83.0)
    parser.add_argument("--strict", action="store_true", help="Return non-zero when continuity quality gates are not met.")
    parser.add_argument("--min-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-dialogue-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-watch-rows", type=int, default=0)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    report = build_report(_read_jsonl(args.scores), weak_threshold=args.weak_threshold)
    _write_json(args.out / "continuity_quality_summary.json", report)
    (args.out / "continuity_quality_report.md").write_text(
        render_markdown(report, source=args.scores),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and strict_failed(
        report,
        min_pass_rate=float(args.min_pass_rate),
        min_dialogue_pass_rate=float(args.min_dialogue_pass_rate),
        max_watch_rows=int(args.max_watch_rows),
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
