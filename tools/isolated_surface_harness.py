from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_input,
    build_isolated_surface_messages,
    derive_style_reference_anchor,
    parse_surface_candidates,
    score_isolated_surface_candidates,
    select_best_isolated_candidate,
)
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_memory_recall import (
    build_surface_input_with_memory_recall,
    memory_recall_packet_from_mapping,
)


_REQUEST_KEYS = {
    "case_id",
    "control",
    "facts",
    "must_not_do",
    "reply_control_packet",
    "soul_md",
    "user_message",
}
_MEMORY_PACKET_KEYS = ("memory_recall_packet", "memory_recall")


def _read_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"{path}:{line_no} must be a JSON object")
            rows.append(payload)
        return rows
    payload = json.loads(text)
    if isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise ValueError(f"{path} must contain JSON objects")
        return list(payload)
    if isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        return [dict(item) for item in payload["cases"] if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(f"{path} must be a JSON object, JSON array, or JSONL file")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _case_request_payload(case: Mapping[str, Any], soul_md: str) -> dict[str, Any]:
    payload = {key: case[key] for key in _REQUEST_KEYS if key in case}
    payload["soul_md"] = str(case.get("soul_md") or soul_md)
    return payload


def _memory_packet_payload(case: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for key in _MEMORY_PACKET_KEYS:
        raw = case.get(key)
        if isinstance(raw, Mapping):
            return raw
    return None


def _context_from_request(request) -> ThreeLayerContinuityContext:
    control = request.control
    return ThreeLayerContinuityContext(
        current_user_message=request.user_message,
        task_mode_hint=control.task_mode,
        response_move_hint=control.response_move,
        emotional_tone_hint=control.emotional_tone,
        relationship_distance=control.relationship_distance,
        information_budget=control.information_budget,
        question_budget=control.question_budget,
        must_include=control.must_include,
        must_not_do=[*control.must_not_do, *request.must_not_do],
        necessary_facts=[*control.facts, *request.facts],
        style_anchor=control.style_anchor,
    )


def _request_variants(
    case: Mapping[str, Any],
    *,
    soul_md: str,
    with_memory: bool,
) -> list[tuple[str, Any]]:
    baseline = build_isolated_surface_input(_case_request_payload(case, soul_md))
    variants: list[tuple[str, Any]] = [("baseline", baseline)]
    raw_packet = _memory_packet_payload(case)
    if with_memory and raw_packet is not None:
        packet = memory_recall_packet_from_mapping(raw_packet)
        variants.append(
            (
                "with_memory",
                build_surface_input_with_memory_recall(
                    _context_from_request(baseline),
                    packet=packet,
                    soul_md=baseline.soul_md,
                    case_id=baseline.case_id,
                    max_facts=8,
                ),
            )
        )
    return variants


def _candidate_texts(case: Mapping[str, Any]) -> list[str]:
    if "candidate_payload" in case:
        raw = case["candidate_payload"]
        if isinstance(raw, (dict, list)):
            raw = json.dumps(raw, ensure_ascii=False)
        return parse_surface_candidates(raw)
    if "model_output" in case:
        raw = case["model_output"]
        if isinstance(raw, (dict, list)):
            raw = json.dumps(raw, ensure_ascii=False)
        return parse_surface_candidates(raw)
    if isinstance(case.get("candidates"), list):
        return parse_surface_candidates(json.dumps({"candidates": case["candidates"]}, ensure_ascii=False))
    return []


def run_cases(cases: list[dict[str, Any]], *, soul_md: str, out_dir: Path, with_memory: bool = False) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    passed = 0
    scored = 0
    memory_cases = 0
    errors: list[dict[str, Any]] = []

    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"case_{index:03d}")
        try:
            candidates = _candidate_texts(case)
            variants = _request_variants(case, soul_md=soul_md, with_memory=with_memory)
            if any(variant == "with_memory" for variant, _request in variants):
                memory_cases += 1
            for variant, request in variants:
                messages = build_isolated_surface_messages(request)
                prompt_rows.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "messages": messages,
                        "style_reference": derive_style_reference_anchor(request).as_dict(),
                    }
                )

                if not candidates:
                    continue
                scores = score_isolated_surface_candidates(request, candidates)
                selected = select_best_isolated_candidate(scores)
                scored += 1
                if selected and selected.passed:
                    passed += 1
                score_rows.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "candidates": [score.as_dict() for score in scores],
                        "selected": selected.as_dict() if selected else None,
                    }
                )
        except Exception as exc:
            errors.append({"case_id": case_id, "error": str(exc)})

    _write_jsonl(out_dir / "prompts.jsonl", prompt_rows)
    _write_jsonl(out_dir / "scores.jsonl", score_rows)
    summary = {
        "cases": len(cases),
        "errors": len(errors),
        "memory_cases": memory_cases,
        "output_dir": str(out_dir),
        "passed_scored_cases": passed,
        "prompt_variants": len(prompt_rows),
        "scored_cases": scored,
        "score_pass_rate": round(passed / scored, 4) if scored else None,
        "with_memory": bool(with_memory),
    }
    _write_json(out_dir / "summary.json", {**summary, "error_rows": errors})
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build isolated Soul.md + ReplyControlPacket surface prompts and score candidates.")
    parser.add_argument("--cases", required=True, type=Path, help="JSON/JSONL cases with user_message and ReplyControlPacket.")
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path, help="Soul.md path.")
    parser.add_argument("--out", default=None, type=Path, help="Output directory for prompts/scores/summary.")
    parser.add_argument("--with-memory", action="store_true", help="Also build with_memory variants from compressed memory_recall_packet fields.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero if any case errors or scored selected candidate fails.")
    args = parser.parse_args(argv)

    soul_md = args.soul.read_text(encoding="utf-8-sig")
    out_dir = args.out or (ROOT / "artifacts" / "isolated_surface_harness" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    summary = run_cases(_read_json_or_jsonl(args.cases), soul_md=soul_md, out_dir=out_dir, with_memory=args.with_memory)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and (summary["errors"] or summary["passed_scored_cases"] != summary["scored_cases"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
