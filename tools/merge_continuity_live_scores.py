from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_continuity import build_continuity_surface_inputs
from tools.isolated_surface_harness import _read_json_or_jsonl


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


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _selected(row: Mapping[str, Any]) -> Mapping[str, Any]:
    selected = row.get("selected")
    return selected if isinstance(selected, Mapping) else {}


def _row_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("case_id") or ""), str(row.get("turn_id") or ""))


def _rank(row: Mapping[str, Any]) -> tuple[int, int, int, float]:
    selected = _selected(row)
    return (
        1 if selected.get("passed") else 0,
        0 if selected.get("hard_fail") else 1,
        -len(selected.get("control_violations") or []),
        float(selected.get("score") or 0.0),
    )


def _expected_turn_keys(cases_path: Path | None, *, soul_md: str) -> set[tuple[str, str]]:
    if cases_path is None:
        return set()
    keys: set[tuple[str, str]] = set()
    for case in _read_json_or_jsonl(cases_path):
        for turn in build_continuity_surface_inputs(case, soul_md=soul_md):
            keys.add((turn.case_id, turn.turn_id))
    return keys


def merge_score_rows(score_paths: list[Path]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for source_index, path in enumerate(score_paths, start=1):
        source_label = f"{source_index}:{path.parent.name or path.name}"
        for row in _read_jsonl(path):
            key = _row_key(row)
            if not all(key):
                continue
            candidate = dict(row)
            candidate["merged_source"] = source_label
            if key not in merged or _rank(candidate) > _rank(merged[key]):
                merged[key] = candidate
    return sorted(
        merged.values(),
        key=lambda row: (
            str(row.get("case_id") or ""),
            int(row.get("turn_index") or 0),
            str(row.get("turn_id") or ""),
        ),
    )


def summarize_rows(rows: list[Mapping[str, Any]], *, expected_keys: set[tuple[str, str]] | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    expected_keys = expected_keys or set()
    selected = [_selected(row) for row in rows if _selected(row)]
    scores = [float(row.get("score") or 0.0) for row in selected]
    dialogue_counts: dict[str, int] = {}
    dialogue_passed: dict[str, int] = {}
    for row in rows:
        case_id = str(row.get("case_id") or "")
        if not case_id:
            continue
        dialogue_counts[case_id] = dialogue_counts.get(case_id, 0) + 1
        if _selected(row).get("passed"):
            dialogue_passed[case_id] = dialogue_passed.get(case_id, 0) + 1
    dialogue_pass_count = sum(
        1
        for case_id, count in dialogue_counts.items()
        if count > 0 and dialogue_passed.get(case_id, 0) == count
    )
    actual_keys = {_row_key(row) for row in rows}
    missing = sorted(expected_keys - actual_keys)
    summary = {
        "turns": len(rows),
        "selected_count": len(selected),
        "pass_count": sum(1 for row in selected if row.get("passed")),
        "pass_rate": round(sum(1 for row in selected if row.get("passed")) / len(selected), 4) if selected else None,
        "dialogues": len(dialogue_counts),
        "dialogue_pass_count": dialogue_pass_count,
        "dialogue_pass_rate": round(dialogue_pass_count / len(dialogue_counts), 4) if dialogue_counts else None,
        "avg_selected_score": round(statistics.fmean(scores), 3) if scores else None,
        "min_selected_score": round(min(scores), 3) if scores else None,
        "low_score_count": sum(1 for row in selected if float(row.get("score") or 0.0) < 80.0 or not row.get("passed")),
        "hard_fail_count": sum(1 for row in selected if row.get("hard_fail")),
        "control_violation_count": sum(1 for row in selected if row.get("control_violations")),
        "expected_turns": len(expected_keys) if expected_keys else None,
        "missing_turn_count": len(missing),
        "missing_turns": [{"case_id": case_id, "turn_id": turn_id} for case_id, turn_id in missing],
        "retry_used_count": sum(1 for row in rows if not str(row.get("merged_source") or "").startswith("1:")),
    }
    if out_dir is not None:
        summary["output_dir"] = str(out_dir)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge source and retry continuity live score rows by best selected candidate.")
    parser.add_argument("--scores", required=True, nargs="+", type=Path, help="live_scores.jsonl files, source first then retry files.")
    parser.add_argument("--out", required=True, type=Path, help="Output directory.")
    parser.add_argument("--expected-cases", type=Path, default=None, help="Optional continuity cases file to report missing turns.")
    parser.add_argument("--soul", type=Path, default=ROOT / "Soul.md", help="Soul.md path for expected case validation.")
    args = parser.parse_args(argv)

    soul_md = args.soul.read_text(encoding="utf-8-sig")
    rows = merge_score_rows(args.scores)
    expected = _expected_turn_keys(args.expected_cases, soul_md=soul_md)
    args.out.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.out / "merged_scores.jsonl", rows)
    summary = summarize_rows(rows, expected_keys=expected, out_dir=args.out)
    _write_json(args.out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not summary["missing_turn_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
