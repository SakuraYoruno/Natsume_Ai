from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.isolated_surface_harness import (
    build_isolated_surface_input,
    parse_surface_candidates,
    score_isolated_surface_candidates,
)
from host_brain.persona.surface_runtime import build_direct_surface_messages
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext
from host_brain.persona.surface_affect_control import (
    SurfaceAffectControlPacket,
    affect_control_packet_from_mapping,
    build_surface_affect_control_packet,
    build_surface_input_with_affect_control,
)
from host_brain.persona.surface_memory_recall import (
    build_surface_input_with_memory_recall,
    memory_recall_packet_from_mapping,
)
from tools.surface_tool_contract import (  # noqa: E402
    OFFLINE_FIXTURE_EVAL_LAYER,
    SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED as _SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED,
    surface_tool_contract_payload,
)


SURFACE_TOOL_LAYER = OFFLINE_FIXTURE_EVAL_LAYER
SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED = _SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED


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
_AFFECT_PACKET_KEYS = ("affect_control_packet", "surface_affect_packet", "emotion_control_packet")
_AFFECT_STATE_KEYS = ("affect_state", "emotion_state", "emotion_context")
_AFFECT_BOND_KEYS = ("bond_summary", "relationship_summary", "relationship_state")
_AFFECT_RELATIONSHIP_CLUE_KEYS = ("relationship_clues", "affect_relationship_clues")
_CJK_ONE_CHAR_RE = re.compile(r"^[\u4e00-\u9fff]$")
_EXPECTED_ACTION_OBJECT_RE = re.compile(r"(?:想要|想|要|打算|准备)?(?:去)?(?:看|听|吃|写|改|修|买|玩|做)([\u4e00-\u9fff]{1,4})")


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


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


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


def _first_mapping(case: Mapping[str, Any], keys: tuple[str, ...]) -> Mapping[str, Any] | None:
    for key in keys:
        raw = case.get(key)
        if isinstance(raw, Mapping):
            return raw
    return None


def _first_sequence(case: Mapping[str, Any], keys: tuple[str, ...]) -> list[Any]:
    for key in keys:
        raw = case.get(key)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            return list(raw)
    return []


def _affect_packet_payload(case: Mapping[str, Any], *, baseline: Any | None = None) -> SurfaceAffectControlPacket | None:
    for key in _AFFECT_PACKET_KEYS:
        raw = case.get(key)
        if isinstance(raw, Mapping):
            return affect_control_packet_from_mapping(raw)
    raw_affect_state = _first_mapping(case, _AFFECT_STATE_KEYS)
    if raw_affect_state is None:
        return None
    if baseline is not None:
        task_mode = _clean_text(baseline.control.task_mode or baseline.control.response_move)
    else:
        raw_control = case.get("reply_control_packet") or case.get("control") or {}
        task_mode = _clean_text(raw_control.get("task_mode") or raw_control.get("response_move")) if isinstance(raw_control, Mapping) else ""
    return build_surface_affect_control_packet(
        emotion_state=raw_affect_state,
        bond_summary=_first_mapping(case, _AFFECT_BOND_KEYS),
        relationship_clues=_first_sequence(case, _AFFECT_RELATIONSHIP_CLUE_KEYS),
        task_mode=task_mode,
    )


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
    with_affect: bool = False,
) -> list[tuple[str, Any]]:
    baseline = build_isolated_surface_input(_case_request_payload(case, soul_md))
    variants: list[tuple[str, Any]] = [("baseline", baseline)]
    raw_packet = _memory_packet_payload(case)
    memory_request = None
    if with_memory and raw_packet is not None:
        packet = memory_recall_packet_from_mapping(raw_packet)
        memory_request = build_surface_input_with_memory_recall(
            _context_from_request(baseline),
            packet=packet,
            soul_md=baseline.soul_md,
            case_id=baseline.case_id,
            max_facts=8,
        )
        variants.append(("with_memory", memory_request))
    affect_packet = _affect_packet_payload(case, baseline=baseline)
    if with_affect and affect_packet is not None:
        affect_request = build_surface_input_with_affect_control(
            _context_from_request(baseline),
            packet=affect_packet,
            soul_md=baseline.soul_md,
            case_id=baseline.case_id,
            max_facts=8,
        )
        variants.append(("with_affect", affect_request))
        if memory_request is not None:
            variants.append(
                (
                    "with_memory_affect",
                    build_surface_input_with_affect_control(
                        _context_from_request(memory_request),
                        packet=affect_packet,
                        soul_md=memory_request.soul_md,
                        case_id=memory_request.case_id,
                        max_facts=8,
                    ),
                )
            )
    return variants


def _parse_candidate_value(raw: Any) -> list[str]:
    if isinstance(raw, (dict, list)):
        raw = json.dumps(raw, ensure_ascii=False)
    return parse_surface_candidates(raw)


def _candidate_texts(case: Mapping[str, Any], *, variant: str | None = None) -> list[str]:
    if variant:
        for key in ("candidates_by_variant", "candidate_payload_by_variant", "model_output_by_variant"):
            raw_map = case.get(key)
            if isinstance(raw_map, Mapping) and variant in raw_map:
                return _parse_candidate_value(raw_map[variant])
    if "candidate_payload" in case:
        return _parse_candidate_value(case["candidate_payload"])
    if "model_output" in case:
        return _parse_candidate_value(case["model_output"])
    if isinstance(case.get("candidates"), list):
        return _parse_candidate_value({"candidates": case["candidates"]})
    return []


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _selected_dimensions(selected: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(selected, Mapping):
        return {}
    raw = selected.get("dimensions")
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): _safe_float(value) for key, value in raw.items()}


def _memory_watch_flags(text: str) -> list[str]:
    markers = {
        "memory_source_leak": ("memory://", "evd://", "chat://"),
        "recall_process_leak": ("召回", "检索到", "根据记忆", "记忆线索", "关系线索", "压缩记忆", "证据", "熟人私聊"),
        "stiff_memory_surface": ("具体点就是", "记录显示", "资料显示", "事实如下"),
    }
    flags: list[str] = []
    for flag, tokens in markers.items():
        if any(token in text for token in tokens):
            flags.append(flag)
    return flags


def _contains_expected_term(text: str, expected_terms: Iterable[Any]) -> bool:
    terms: list[str] = []
    for raw_term in expected_terms:
        term = str(raw_term).strip()
        if not term:
            continue
        terms.append(term)
        for match in _EXPECTED_ACTION_OBJECT_RE.finditer(term):
            obj = match.group(1).strip()
            if obj:
                terms.append(obj)
    for term in dict.fromkeys(terms):
        if len(term) == 1 and _CJK_ONE_CHAR_RE.fullmatch(term):
            if any(marker in text for marker in (f"那个{term}", f"这个{term}", f"什么{term}", f"看{term}", f"听{term}", f"吃{term}", f"{term}票", f"{term}嘛", f"{term}。", f"{term}，")):
                return True
            continue
        if term in text:
            return True
    return False


def _summarize_score_rows(score_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_variant: dict[str, list[dict[str, Any]]] = {}
    for row in score_rows:
        selected = row.get("selected")
        if not isinstance(selected, Mapping):
            continue
        by_variant.setdefault(str(row.get("variant") or "baseline"), []).append(row)

    summary: dict[str, Any] = {}
    for variant, rows in sorted(by_variant.items()):
        selected_rows = [row["selected"] for row in rows if isinstance(row.get("selected"), Mapping)]
        scores = [_safe_float(selected.get("score")) for selected in selected_rows]
        pass_count = sum(1 for selected in selected_rows if selected.get("passed"))
        hard_fail_count = sum(1 for selected in selected_rows if selected.get("hard_fail"))
        flag_counts: Counter[str] = Counter()
        dimensions: dict[str, list[float]] = {}
        for selected in selected_rows:
            for flag in _memory_watch_flags(str(selected.get("text") or "")):
                flag_counts[flag] += 1
            for dimension, value in _selected_dimensions(selected).items():
                dimensions.setdefault(dimension, []).append(value)
        summary[variant] = {
            "selected_count": len(selected_rows),
            "pass_count": pass_count,
            "pass_rate": round(pass_count / len(selected_rows), 4) if selected_rows else None,
            "avg_score": round(statistics.fmean(scores), 3) if scores else None,
            "min_score": round(min(scores), 3) if scores else None,
            "hard_fail_count": hard_fail_count,
            "memory_watch_flags": dict(sorted(flag_counts.items())),
            "avg_dimensions": {
                key: round(statistics.fmean(values), 3)
                for key, values in sorted(dimensions.items())
                if values
            },
        }
    return summary


def _build_memory_ab_comparison(
    score_rows: list[dict[str, Any]],
    *,
    case_expectations: Mapping[str, list[str]],
) -> dict[str, Any]:
    rows_by_case: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in score_rows:
        selected = row.get("selected")
        if not isinstance(selected, Mapping):
            continue
        case_id = str(row.get("case_id") or "")
        variant = str(row.get("variant") or "baseline")
        rows_by_case.setdefault(case_id, {})[variant] = selected

    compared: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    for case_id, variants in sorted(rows_by_case.items()):
        baseline = variants.get("baseline")
        memory = variants.get("with_memory")
        if not baseline or not memory:
            continue
        base_dims = _selected_dimensions(baseline)
        memory_dims = _selected_dimensions(memory)
        expected_terms = case_expectations.get(case_id, [])
        base_text = str(baseline.get("text") or "")
        memory_text = str(memory.get("text") or "")
        score_delta = _safe_float(memory.get("score")) - _safe_float(baseline.get("score"))
        naturalness_delta = memory_dims.get("naturalness", 0.0) - base_dims.get("naturalness", 0.0)
        context_delta = memory_dims.get("context_fit", 0.0) - base_dims.get("context_fit", 0.0)
        distance_delta = memory_dims.get("distance_control", 0.0) - base_dims.get("distance_control", 0.0)
        usefulness_delta = memory_dims.get("usefulness", 0.0) - base_dims.get("usefulness", 0.0)
        technical_delta = memory_dims.get("technical_helpfulness", 0.0) - base_dims.get("technical_helpfulness", 0.0)
        memory_flags = _memory_watch_flags(memory_text)
        with_memory_expected_hit = _contains_expected_term(memory_text, expected_terms)
        baseline_expected_hit = _contains_expected_term(base_text, expected_terms)
        baseline_passed = bool(baseline.get("passed"))
        with_memory_passed = bool(memory.get("passed"))
        row = {
            "case_id": case_id,
            "baseline_text": base_text,
            "with_memory_text": memory_text,
            "expected_terms": list(expected_terms),
            "score_delta": round(score_delta, 3),
            "naturalness_delta": round(naturalness_delta, 3),
            "context_fit_delta": round(context_delta, 3),
            "distance_control_delta": round(distance_delta, 3),
            "usefulness_delta": round(usefulness_delta, 3),
            "technical_helpfulness_delta": round(technical_delta, 3),
            "baseline_passed": baseline_passed,
            "with_memory_passed": with_memory_passed,
            "baseline_expected_hit": baseline_expected_hit,
            "with_memory_expected_hit": with_memory_expected_hit,
            "with_memory_flags": memory_flags,
        }
        compared.append(row)
        technical_priority_ok = (
            with_memory_expected_hit
            and technical_delta >= 0
            and usefulness_delta >= 0
            and memory_dims.get("technical_helpfulness", 0.0) >= 90.0
            and memory_dims.get("usefulness", 0.0) >= 90.0
        )
        memory_alignment_ok = bool(
            with_memory_expected_hit
            and score_delta >= 0
            and context_delta >= 0
            and distance_delta >= 0
        )
        naturalness_regressed = naturalness_delta < -5 and not technical_priority_ok and not memory_alignment_ok
        expected_case_regressed = bool(expected_terms) and (
            score_delta < -5 or naturalness_regressed or distance_delta < -5
        )
        if not with_memory_passed or expected_case_regressed or memory_flags:
            regressions.append(row)

    score_deltas = [row["score_delta"] for row in compared]
    naturalness_deltas = [row["naturalness_delta"] for row in compared]
    context_deltas = [row["context_fit_delta"] for row in compared]
    distance_deltas = [row["distance_control_delta"] for row in compared]
    usefulness_deltas = [row["usefulness_delta"] for row in compared]
    technical_deltas = [row["technical_helpfulness_delta"] for row in compared]
    expected_compared = [row for row in compared if row.get("expected_terms")]
    expected_denominator = len(expected_compared)
    memory_hit_gain = sum(
        1
        for row in expected_compared
        if row["with_memory_expected_hit"] and not row["baseline_expected_hit"]
    )
    baseline_hit_count = sum(1 for row in expected_compared if row["baseline_expected_hit"])
    with_memory_hit_count = sum(1 for row in expected_compared if row["with_memory_expected_hit"])
    regression_count = len(regressions)
    compared_count = len(compared)
    memory_hit_gain_rate = memory_hit_gain / expected_denominator if expected_denominator else None
    with_memory_hit_rate = with_memory_hit_count / expected_denominator if expected_denominator else None
    baseline_hit_rate = baseline_hit_count / expected_denominator if expected_denominator else None
    avg_score_delta = round(statistics.fmean(score_deltas), 3) if score_deltas else None
    avg_naturalness_delta = round(statistics.fmean(naturalness_deltas), 3) if naturalness_deltas else None
    avg_context_delta = round(statistics.fmean(context_deltas), 3) if context_deltas else None
    avg_distance_delta = round(statistics.fmean(distance_deltas), 3) if distance_deltas else None
    verdict = "not_run"
    if compared_count:
        has_flag_regression = regression_count > 0
        avg_memory_alignment_ok = bool(
            avg_score_delta is not None
            and avg_score_delta >= 0
            and avg_context_delta is not None
            and avg_context_delta >= 0
            and (avg_distance_delta is None or avg_distance_delta >= -1.0)
        )
        avg_quality_ok = (
            (avg_naturalness_delta is None or avg_naturalness_delta >= -5.0 or avg_memory_alignment_ok)
            and (avg_distance_delta is None or avg_distance_delta >= -5.0)
        )
        memory_usage_ok = expected_denominator == 0 or memory_hit_gain_rate >= 0.5 or (
            memory_hit_gain > 0 and with_memory_hit_rate is not None and with_memory_hit_rate >= 0.6
        ) or (
            with_memory_hit_rate is not None
            and baseline_hit_rate is not None
            and with_memory_hit_rate >= min(0.75, baseline_hit_rate)
        )
        if memory_usage_ok and avg_quality_ok and not has_flag_regression:
            verdict = "pass"
        elif memory_hit_gain > 0 and avg_quality_ok:
            verdict = "watch"
        else:
            verdict = "fail"
    return {
        "compared_cases": len(compared),
        "expected_compared_cases": expected_denominator,
        "avg_score_delta": avg_score_delta,
        "avg_naturalness_delta": avg_naturalness_delta,
        "avg_context_fit_delta": avg_context_delta,
        "avg_distance_control_delta": avg_distance_delta,
        "avg_usefulness_delta": round(statistics.fmean(usefulness_deltas), 3) if usefulness_deltas else None,
        "avg_technical_helpfulness_delta": round(statistics.fmean(technical_deltas), 3) if technical_deltas else None,
        "baseline_expected_hit_count": baseline_hit_count,
        "baseline_expected_hit_rate": round(baseline_hit_rate, 4) if baseline_hit_rate is not None else None,
        "with_memory_expected_hit_count": with_memory_hit_count,
        "with_memory_expected_hit_rate": round(with_memory_hit_rate, 4) if with_memory_hit_rate is not None else None,
        "memory_expected_hit_gain_count": memory_hit_gain,
        "memory_expected_hit_gain_rate": round(memory_hit_gain_rate, 4) if memory_hit_gain_rate is not None else None,
        "regression_count": regression_count,
        "regressions": regressions[:20],
        "rows": compared,
        "verdict": verdict,
    }


def _write_memory_ab_report(path: Path, comparison: Mapping[str, Any], variant_summary: Mapping[str, Any]) -> None:
    lines = [
        "# Surface Memory A/B Report",
        "",
        f"- compared cases: `{comparison.get('compared_cases')}`",
        f"- avg score delta: `{comparison.get('avg_score_delta')}`",
        f"- avg naturalness delta: `{comparison.get('avg_naturalness_delta')}`",
        f"- avg context_fit delta: `{comparison.get('avg_context_fit_delta')}`",
        f"- avg distance_control delta: `{comparison.get('avg_distance_control_delta')}`",
        f"- avg usefulness delta: `{comparison.get('avg_usefulness_delta')}`",
        f"- avg technical_helpfulness delta: `{comparison.get('avg_technical_helpfulness_delta')}`",
        f"- expected-term cases: `{comparison.get('expected_compared_cases')}`",
        f"- baseline expected hit: `{comparison.get('baseline_expected_hit_count')}/{comparison.get('expected_compared_cases')}`",
        f"- with_memory expected hit: `{comparison.get('with_memory_expected_hit_count')}/{comparison.get('expected_compared_cases')}`",
        f"- memory expected hit gain: `{comparison.get('memory_expected_hit_gain_count')}/{comparison.get('expected_compared_cases')}`",
        f"- regressions: `{comparison.get('regression_count')}`",
        f"- verdict: `{comparison.get('verdict')}`",
        "",
        "## Variant Summary",
        "",
    ]
    for variant, summary in variant_summary.items():
        lines.append(f"- `{variant}`: pass `{summary.get('pass_count')}/{summary.get('selected_count')}`, avg `{summary.get('avg_score')}`, min `{summary.get('min_score')}`")
    lines.extend(["", "## Compared Rows", ""])
    for row in list(comparison.get("rows") or [])[:60]:
        flags = ", ".join(f"`{flag}`" for flag in row.get("with_memory_flags") or []) or "-"
        lines.extend(
            [
                f"### {row['case_id']}",
                "",
                f"- delta: score `{row['score_delta']}`, naturalness `{row['naturalness_delta']}`, context `{row['context_fit_delta']}`, distance `{row['distance_control_delta']}`, usefulness `{row['usefulness_delta']}`, technical `{row['technical_helpfulness_delta']}`",
                f"- passed: baseline `{row['baseline_passed']}`, with_memory `{row['with_memory_passed']}`",
                f"- expected hit: baseline `{row['baseline_expected_hit']}`, with_memory `{row['with_memory_expected_hit']}`",
                f"- flags: {flags}",
                f"- baseline: {row['baseline_text']}",
                f"- with_memory: {row['with_memory_text']}",
                "",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")


def run_cases(
    cases: list[dict[str, Any]],
    *,
    soul_md: str,
    out_dir: Path,
    with_memory: bool = False,
    with_affect: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt_rows: list[dict[str, Any]] = []
    score_rows: list[dict[str, Any]] = []
    passed = 0
    scored = 0
    memory_cases = 0
    affect_cases = 0
    errors: list[dict[str, Any]] = []
    case_expectations: dict[str, list[str]] = {}

    for index, case in enumerate(cases, start=1):
        case_id = str(case.get("case_id") or f"case_{index:03d}")
        case_expectations[case_id] = [str(term) for term in list(case.get("memory_expected_terms") or [])]
        try:
            variants = _request_variants(
                case,
                soul_md=soul_md,
                with_memory=with_memory,
                with_affect=with_affect,
            )
            if any(variant == "with_memory" for variant, _request in variants):
                memory_cases += 1
            if any(variant in {"with_affect", "with_memory_affect"} for variant, _request in variants):
                affect_cases += 1
            for variant, request in variants:
                messages = build_direct_surface_messages(request)
                prompt_rows.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "messages": messages,
                    }
                )

                candidates = _candidate_texts(case, variant=variant)
                if not candidates:
                    continue
                scores = score_isolated_surface_candidates(request, candidates[:1])
                selected = scores[0] if scores else None
                scored += 1
                if selected and selected.passed:
                    passed += 1
                score_rows.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "candidates": [score.as_dict() for score in scores],
                        "memory_expected_terms": case_expectations.get(case_id, []),
                        "selected": selected.as_dict() if selected else None,
                        "selection": "direct_first",
                    }
                )
        except Exception as exc:
            errors.append({"case_id": case_id, "error": str(exc)})

    _write_jsonl(out_dir / "prompts.jsonl", prompt_rows)
    _write_jsonl(out_dir / "scores.jsonl", score_rows)
    variant_summary = _summarize_score_rows(score_rows)
    memory_ab = _build_memory_ab_comparison(score_rows, case_expectations=case_expectations) if with_memory else {}
    summary = {
        **surface_tool_contract_payload("isolated_surface_harness"),
        "cases": len(cases),
        "errors": len(errors),
        "affect_cases": affect_cases,
        "memory_cases": memory_cases,
        "output_dir": str(out_dir),
        "passed_scored_cases": passed,
        "prompt_variants": len(prompt_rows),
        "scored_cases": scored,
        "score_pass_rate": round(passed / scored, 4) if scored else None,
        "variant_summary": variant_summary,
        "with_affect": bool(with_affect),
        "with_memory": bool(with_memory),
    }
    if with_memory:
        summary["memory_ab"] = memory_ab
        _write_json(out_dir / "memory_ab_summary.json", memory_ab)
        _write_memory_ab_report(out_dir / "memory_ab_report.md", memory_ab, variant_summary)
    _write_json(out_dir / "summary.json", {**summary, "error_rows": errors})
    return summary


def _strict_failed(summary: Mapping[str, Any]) -> bool:
    if summary.get("errors"):
        return True
    if summary.get("with_memory") or summary.get("with_affect"):
        variant_summary = summary.get("variant_summary") or {}
        for variant in ("with_memory_affect", "with_memory", "with_affect"):
            row = variant_summary.get(variant) or {}
            selected = int(row.get("selected_count") or 0)
            passed = int(row.get("pass_count") or 0)
            if selected > 0 and passed != selected:
                return True
        return False
    return summary["passed_scored_cases"] != summary["scored_cases"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build isolated Soul.md + ReplyControlPacket surface prompts and score candidates.")
    parser.add_argument("--cases", required=True, type=Path, help="JSON/JSONL cases with user_message and ReplyControlPacket.")
    parser.add_argument("--soul", default=ROOT / "Soul.md", type=Path, help="Soul.md path.")
    parser.add_argument("--out", default=None, type=Path, help="Output directory for prompts/scores/summary.")
    parser.add_argument("--with-memory", action="store_true", help="Also build with_memory variants from compressed memory_recall_packet fields.")
    parser.add_argument("--with-affect", action="store_true", help="Also build variants from compressed affect_control_packet fields or upstream affect_state fields.")
    parser.add_argument("--strict", action="store_true", help="Return non-zero if any case errors or scored selected candidate fails.")
    args = parser.parse_args(argv)

    soul_md = args.soul.read_text(encoding="utf-8-sig")
    out_dir = args.out or (ROOT / "artifacts" / "isolated_surface_harness" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    summary = run_cases(
        _read_json_or_jsonl(args.cases),
        soul_md=soul_md,
        out_dir=out_dir,
        with_memory=args.with_memory,
        with_affect=args.with_affect,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    if args.strict and _strict_failed(summary):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
