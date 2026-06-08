from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_relationship_axis_contrastive_cases import RELATIONSHIP_STAGES  # noqa: E402
from tools.relationship_axis_report import (  # noqa: E402
    _read_jsonl,
    _safe_list,
    _safe_mapping,
    build_relationship_axis_report,
)


_TECHNICAL_SCENES = {"technical_help"}
_AFFECTION_OPTIONAL_SCENES = {*_TECHNICAL_SCENES, "creative_edit"}
_CJK_PUNCT = set(" \t\r\n，。！？!?,.、~～…“”\"'：:；;（）()[]{}")


def _avg(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _compact_chars(text: str) -> str:
    return "".join(ch for ch in text if ch not in _CJK_PUNCT)


def _char_bigrams(text: str) -> set[str]:
    compact = _compact_chars(text)
    if len(compact) <= 1:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}


def _similarity(left: str, right: str) -> float:
    if _compact_chars(left) == _compact_chars(right):
        return 1.0
    left_set = _char_bigrams(left)
    right_set = _char_bigrams(right)
    if not left_set and not right_set:
        return 1.0
    if not left_set or not right_set:
        return 0.0
    return round(len(left_set & right_set) / len(left_set | right_set), 4)


def _marker_delta(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    left_flags = _safe_mapping(left.get("marker_flags"))
    right_flags = _safe_mapping(right.get("marker_flags"))
    keys = ("care", "nickname", "intimacy_signal", "physical_intimacy", "explicit_romance", "close_presence")
    return sum(1 for key in keys if bool(left_flags.get(key)) != bool(right_flags.get(key)))


def _has_intimacy_signal(row: Mapping[str, Any]) -> bool:
    flags = _safe_mapping(row.get("marker_flags"))
    return bool(flags.get("intimacy_signal") or flags.get("physical_intimacy") or flags.get("explicit_romance"))


def _has_romantic_or_physical(row: Mapping[str, Any]) -> bool:
    flags = _safe_mapping(row.get("marker_flags"))
    return bool(flags.get("physical_intimacy") or flags.get("explicit_romance"))


def _is_nonlover(row: Mapping[str, Any]) -> bool:
    return _clean_text(row.get("relationship_distance")) not in {"lover_early", "lover_stable"}


def _pair_reasons(
    *,
    scene: str,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    similarity: float,
    marker_delta: int,
) -> list[str]:
    left_text = _clean_text(left.get("text"))
    right_text = _clean_text(right.get("text"))
    left_stage = _clean_text(left.get("relationship_distance"))
    right_stage = _clean_text(right.get("relationship_distance"))
    reasons: list[str] = []

    if _compact_chars(left_text) == _compact_chars(right_text):
        reasons.append("duplicate_text")
    elif similarity >= 0.86 and marker_delta == 0:
        reasons.append("too_similar_without_marker_shift")

    if left_stage == "pre_confession" and right_stage == "lover_early" and scene not in _AFFECTION_OPTIONAL_SCENES:
        if not _has_intimacy_signal(right):
            reasons.append("lover_early_missing_intimacy_signal")

    if _is_nonlover(left) and _has_romantic_or_physical(left):
        reasons.append("nonlover_over_intimate_left")
    if _is_nonlover(right) and _has_romantic_or_physical(right):
        reasons.append("nonlover_over_intimate_right")

    if scene in _TECHNICAL_SCENES and (_has_romantic_or_physical(left) or _has_romantic_or_physical(right)):
        reasons.append("technical_scene_over_romantic")

    return reasons


def _stage_order(stage: str) -> int:
    try:
        return RELATIONSHIP_STAGES.index(stage)
    except ValueError:
        return len(RELATIONSHIP_STAGES) + 1


def _row_by_stage(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    grouped = {_clean_text(row.get("relationship_distance")): row for row in rows}
    return {stage: grouped[stage] for stage in RELATIONSHIP_STAGES if stage in grouped}


def _scene_summary(scene: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_stage = _row_by_stage(rows)
    ordered_rows = [by_stage[stage] for stage in RELATIONSHIP_STAGES if stage in by_stage]
    pairs: list[dict[str, Any]] = []
    weak_pairs: list[dict[str, Any]] = []
    for left_stage, right_stage in zip(RELATIONSHIP_STAGES, RELATIONSHIP_STAGES[1:]):
        left = by_stage.get(left_stage)
        right = by_stage.get(right_stage)
        if not left or not right:
            continue
        similarity = _similarity(_clean_text(left.get("text")), _clean_text(right.get("text")))
        marker_delta = _marker_delta(left, right)
        reasons = _pair_reasons(scene=scene, left=left, right=right, similarity=similarity, marker_delta=marker_delta)
        pair = {
            "left_stage": left_stage,
            "right_stage": right_stage,
            "similarity": similarity,
            "marker_delta": marker_delta,
            "reasons": reasons,
            "left_case_id": left.get("case_id"),
            "right_case_id": right.get("case_id"),
            "left_text": left.get("text"),
            "right_text": right.get("text"),
        }
        pairs.append(pair)
        if reasons:
            weak_pairs.append(pair)

    scores = [float(row.get("score") or 0.0) for row in ordered_rows]
    similarities = [float(pair["similarity"]) for pair in pairs]
    unique_text_count = len({_compact_chars(_clean_text(row.get("text"))) for row in ordered_rows})
    axis_violations = [
        {
            "case_id": row.get("case_id"),
            "stage": row.get("relationship_distance"),
            "text": row.get("text"),
            "violations": list(_safe_list(row.get("violations"))),
        }
        for row in ordered_rows
        if _safe_list(row.get("violations"))
    ]
    critical_reasons = {
        "duplicate_text",
        "lover_early_missing_intimacy_signal",
        "nonlover_over_intimate_left",
        "nonlover_over_intimate_right",
        "technical_scene_over_romantic",
    }
    critical_pairs = [
        pair for pair in weak_pairs if any(reason in critical_reasons for reason in _safe_list(pair.get("reasons")))
    ]
    return {
        "scene": scene,
        "case_count": len(ordered_rows),
        "distinct_text_count": unique_text_count,
        "avg_score": _avg(scores),
        "min_score": round(min(scores), 3) if scores else None,
        "avg_adjacent_similarity": _avg(similarities),
        "max_adjacent_similarity": round(max(similarities), 4) if similarities else None,
        "adjacent_pairs": pairs,
        "weak_pair_count": len(weak_pairs),
        "weak_pairs": weak_pairs,
        "axis_violation_count": len(axis_violations),
        "axis_violations": axis_violations,
        "critical_pair_count": len(critical_pairs),
        "passed": (
            len(axis_violations) == 0
            and len(critical_pairs) == 0
            and len(weak_pairs) <= 2
            and unique_text_count >= max(1, len(ordered_rows) - 1)
        ),
    }


def build_relationship_axis_contrast_report(
    *,
    cases: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    axis_report = build_relationship_axis_report(cases=cases, score_rows=score_rows, max_examples=2)
    rows_by_scene: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in _safe_list(axis_report.get("rows")):
        rows_by_scene[_clean_text(row.get("scene")) or "unknown"].append(row)

    scene_summaries = [
        _scene_summary(scene, sorted(rows, key=lambda row: _stage_order(_clean_text(row.get("relationship_distance")))))
        for scene, rows in sorted(rows_by_scene.items())
    ]
    weak_pairs = [
        {**pair, "scene": scene.get("scene")}
        for scene in scene_summaries
        for pair in _safe_list(scene.get("weak_pairs"))
    ]
    critical_pairs = [pair for pair in weak_pairs if any("duplicate_text" == reason or "over_" in reason or "missing_" in reason for reason in _safe_list(pair.get("reasons")))]
    scores = [float(row.get("score") or 0.0) for row in _safe_list(axis_report.get("rows"))]
    return {
        "total": int(axis_report.get("total") or 0),
        "pass_rate": axis_report.get("pass_rate"),
        "avg_score": _avg(scores),
        "min_score": round(min(scores), 3) if scores else None,
        "axis_violation_count": int(axis_report.get("violation_count") or 0),
        "scene_count": len(scene_summaries),
        "stage_count": len(RELATIONSHIP_STAGES),
        "scene_pass_count": sum(1 for scene in scene_summaries if scene.get("passed")),
        "scene_pass_rate": round(
            sum(1 for scene in scene_summaries if scene.get("passed")) / len(scene_summaries),
            4,
        )
        if scene_summaries
        else 0.0,
        "weak_pair_count": len(weak_pairs),
        "critical_pair_count": len(critical_pairs),
        "weak_pairs": weak_pairs,
        "scene_summaries": scene_summaries,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# Relationship Axis Contrast Report",
        "",
        f"- total: {report.get('total')}",
        f"- pass_rate: {report.get('pass_rate')}",
        f"- avg_score: {report.get('avg_score')}",
        f"- min_score: {report.get('min_score')}",
        f"- axis_violation_count: {report.get('axis_violation_count')}",
        f"- scene_pass_rate: {report.get('scene_pass_rate')}",
        f"- weak_pair_count: {report.get('weak_pair_count')}",
        f"- critical_pair_count: {report.get('critical_pair_count')}",
        "",
        "## Scene Separation",
        "",
        "| scene | n | distinct | avg | min | avg_adj_sim | max_adj_sim | weak | critical | axis | pass |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for scene in _safe_list(report.get("scene_summaries")):
        lines.append(
            "| {scene} | {case_count} | {distinct_text_count} | {avg_score} | {min_score} | {avg_adjacent_similarity} | {max_adjacent_similarity} | {weak_pair_count} | {critical_pair_count} | {axis_violation_count} | {passed} |".format(
                scene=scene.get("scene"),
                case_count=scene.get("case_count"),
                distinct_text_count=scene.get("distinct_text_count"),
                avg_score=scene.get("avg_score"),
                min_score=scene.get("min_score"),
                avg_adjacent_similarity=scene.get("avg_adjacent_similarity"),
                max_adjacent_similarity=scene.get("max_adjacent_similarity"),
                weak_pair_count=scene.get("weak_pair_count"),
                critical_pair_count=scene.get("critical_pair_count"),
                axis_violation_count=scene.get("axis_violation_count"),
                passed=scene.get("passed"),
            )
        )

    lines.extend(["", "## Weak Adjacent Pairs", ""])
    weak_pairs = _safe_list(report.get("weak_pairs"))
    if not weak_pairs:
        lines.append("No weak adjacent pairs detected.")
    else:
        for pair in weak_pairs[:60]:
            lines.append(
                "- {scene} {left_stage}->{right_stage} sim={similarity} delta={marker_delta} reasons={reasons}\n"
                "  - A: {left_text}\n"
                "  - B: {right_text}".format(
                    scene=pair.get("scene"),
                    left_stage=pair.get("left_stage"),
                    right_stage=pair.get("right_stage"),
                    similarity=pair.get("similarity"),
                    marker_delta=pair.get("marker_delta"),
                    reasons=",".join(str(item) for item in _safe_list(pair.get("reasons"))),
                    left_text=pair.get("left_text"),
                    right_text=pair.get("right_text"),
                )
            )
    lines.extend(["", "## Axis Violations", ""])
    axis_rows = [
        {**violation, "scene": scene.get("scene")}
        for scene in _safe_list(report.get("scene_summaries"))
        for violation in _safe_list(scene.get("axis_violations"))
    ]
    if not axis_rows:
        lines.append("No axis-level scene violations detected.")
    else:
        for row in axis_rows[:60]:
            lines.append(
                "- {scene} {stage} {case_id}: {violations} | {text}".format(
                    scene=row.get("scene"),
                    stage=row.get("stage"),
                    case_id=row.get("case_id"),
                    violations=",".join(str(item) for item in _safe_list(row.get("violations"))),
                    text=row.get("text"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def write_relationship_axis_contrast_report(
    *,
    cases_path: Path,
    scores_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_relationship_axis_contrast_report(
        cases=_read_jsonl(cases_path),
        score_rows=_read_jsonl(scores_path),
    )
    _write_json(out_dir / "relationship_axis_contrast_summary.json", report)
    (out_dir / "relationship_axis_contrast_report.md").write_text(render_markdown_report(report), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check whether adjacent relationship stages separate under the same scene.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    report = write_relationship_axis_contrast_report(cases_path=args.cases, scores_path=args.scores, out_dir=args.out)
    print(
        "FINAL "
        + json.dumps(
            {
                "total": report.get("total"),
                "avg_score": report.get("avg_score"),
                "axis_violation_count": report.get("axis_violation_count"),
                "scene_pass_rate": report.get("scene_pass_rate"),
                "weak_pair_count": report.get("weak_pair_count"),
                "critical_pair_count": report.get("critical_pair_count"),
                "output_dir": str(args.out),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
