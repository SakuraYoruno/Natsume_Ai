from __future__ import annotations

import argparse
import json
import sys
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_continuity import build_continuity_surface_inputs
from host_brain.persona.isolated_surface_harness import (
    parse_surface_candidates,
    score_isolated_surface_candidates,
)
from host_brain.persona.surface_runtime import build_direct_surface_messages, surface_observation_codes
from tools.isolated_surface_harness import _read_json_or_jsonl


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _candidate_texts(turn: Mapping[str, Any]) -> list[str]:
    if "candidate_payload" in turn:
        raw = turn["candidate_payload"]
        if isinstance(raw, (dict, list)):
            raw = json.dumps(raw, ensure_ascii=False)
        return parse_surface_candidates(raw)
    if "model_output" in turn:
        raw = turn["model_output"]
        if isinstance(raw, (dict, list)):
            raw = json.dumps(raw, ensure_ascii=False)
        return parse_surface_candidates(raw)
    if isinstance(turn.get("candidates"), list):
        return parse_surface_candidates(json.dumps({"candidates": turn["candidates"]}, ensure_ascii=False))
    return []


def _observation_codes(selected: Mapping[str, Any] | None) -> tuple[str, ...]:
    return surface_observation_codes(selected)


def run_cases(cases: list[dict[str, Any]], *, soul_md: str, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    dialogue_turn_counts: Counter[str] = Counter()
    fact_counts: list[int] = []
    selected_scores: list[float] = []
    observation_counts: Counter[str] = Counter()
    scored = 0
    passed = 0
    turn_count = 0

    for dialogue_index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"dialogue_{dialogue_index:03d}")
        try:
            turns = build_continuity_surface_inputs({**case, "case_id": case_id}, soul_md=soul_md)
        except Exception as exc:
            errors.append({"case_id": case_id, "error": str(exc)})
            continue

        dialogue_turn_counts[case_id] = len(turns)
        for turn in turns:
            turn_count += 1
            try:
                request = turn.request
                fact_counts.append(len(request.control.facts))
                messages = build_direct_surface_messages(request)
                prompt_rows.append(
                    {
                        **turn.as_trace(),
                        "messages": messages,
                    }
                )

                candidates = _candidate_texts(turn.source_turn)
                if not candidates:
                    continue
                scores = score_isolated_surface_candidates(request, candidates[:1])
                selected = scores[0] if scores else None
                selected_dict = selected.as_dict() if selected else None
                observations = _observation_codes(selected_dict)
                for code in observations:
                    observation_counts[code] += 1
                if selected:
                    selected_scores.append(float(selected.score))
                scored += 1
                if selected and selected.passed:
                    passed += 1
                score_rows.append(
                    {
                        **turn.as_trace(),
                        "candidates": [score.as_dict() for score in scores],
                        "observation_codes": list(observations),
                        "selected": selected_dict,
                        "selection": "direct_first",
                    }
                )
            except Exception as exc:
                errors.append({"case_id": case_id, "turn_id": turn.turn_id, "error": str(exc)})

    _write_jsonl(out_dir / "prompts.jsonl", prompt_rows)
    _write_jsonl(out_dir / "scores.jsonl", score_rows)
    summary = {
        "dialogues": len(cases),
        "errors": len(errors),
        "facts_budget_max": max(fact_counts) if fact_counts else 0,
        "facts_budget_violations": sum(1 for count in fact_counts if count > 6),
        "long_dialogues_30_plus": sum(1 for count in dialogue_turn_counts.values() if count >= 30),
        "max_turns_per_dialogue": max(dialogue_turn_counts.values()) if dialogue_turn_counts else 0,
        "min_selected_score": round(min(selected_scores), 3) if selected_scores else None,
        "min_turns_per_dialogue": min(dialogue_turn_counts.values()) if dialogue_turn_counts else 0,
        "observation_count": sum(observation_counts.values()),
        "observation_reason_counts": dict(sorted(observation_counts.items())),
        "output_dir": str(out_dir),
        "passed_scored_turns": passed,
        "scored_turns": scored,
        "score_pass_rate": round(passed / scored, 4) if scored else None,
        "selected_score_avg": round(statistics.fmean(selected_scores), 3) if selected_scores else None,
        "turns": turn_count,
    }
    _write_json(out_dir / "summary.json", {**summary, "error_rows": errors})
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build compressed continuity prompts for the isolated Soul.md surface harness."
    )
    parser.add_argument("--cases", required=True, type=Path, help="JSON/JSONL continuity cases with a turns list.")
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path, help="Soul.md path.")
    parser.add_argument("--out", default=None, type=Path, help="Output directory for prompts/scores/summary.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero if any turn errors or scored selected turn fails.")
    args = parser.parse_args(argv)

    soul_md = args.soul.read_text(encoding="utf-8-sig")
    out_dir = args.out or (ROOT / "artifacts" / "isolated_surface_harness" / datetime.now().strftime("continuity_%Y%m%d_%H%M%S"))
    summary = run_cases(_read_json_or_jsonl(args.cases), soul_md=soul_md, out_dir=out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and (summary["errors"] or summary["passed_scored_turns"] != summary["scored_turns"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
