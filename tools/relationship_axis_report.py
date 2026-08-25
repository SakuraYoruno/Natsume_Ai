from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface.relationship_affect import relationship_control_axes_for_distance  # noqa: E402


_AXIS_KEYS = (
    "stage",
    "progress",
    "distance",
    "initiative",
    "tease_permission",
    "affection_visibility",
    "care_intensity",
    "vulnerability",
    "nickname_policy",
    "physical_intimacy_allowed",
    "explicit_romance_allowed",
    "commitment_language_allowed",
)
_PHYSICAL_MARKERS = ("抱一下", "抱抱", "抱", "靠我", "靠过来", "靠一下")
_EXPLICIT_ROMANCE_MARKERS = ("想你", "爱你", "亲爱的", "宝贝", "只属于", "心里有我")
_COMMITMENT_MARKERS = ("永远陪", "永远都在", "一直在", "不走", "命都给你")
_CARE_MARKERS = (
    "别硬撑",
    "别硬扛",
    "别急",
    "别慌",
    "缓",
    "歇",
    "我看",
    "我听",
    "给我看",
    "看看",
    "卡住",
    "先",
)
_CLOSE_PRESENCE_MARKERS = (
    "我在",
    "陪你",
    "陪着",
    "陪着你",
    "你的事",
    "心疼",
    "会当真",
    "心里放着",
    "放在心里",
    "心里想着",
    "想着你",
    "赖着你",
    "懂你",
    "不懂你",
)
_TECHNICAL_SCENES = {"broken", "failure", "technical", "technical_help", "complaint", "small_failure"}
_AFFECTION_OPTIONAL_SCENES = {*_TECHNICAL_SCENES, "creative_edit"}
_INTIMATE_COME_HERE_RE = re.compile(r"(?<!拿)(?<!贴)(?<!发)(?<!递)(?<!传)(?<!丢)(?<!给)过来")
_RELATIONSHIP_STAGE_SUFFIXES = (
    "trusted_friend",
    "close_friend",
    "pre_confession",
    "lover_stable",
    "lover_early",
    "familiar",
    "ambiguous",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_no} must be a JSON object")
        rows.append(payload)
    return rows


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _safe_list(raw: Any) -> list[Any]:
    return list(raw or []) if isinstance(raw, list | tuple) else []


def _selected(row: Mapping[str, Any]) -> dict[str, Any]:
    selected = row.get("selected")
    if isinstance(selected, Mapping):
        return dict(selected)
    return {}


def _case_id(row: Mapping[str, Any], fallback: int) -> str:
    return _clean_text(row.get("case_id")) or f"case_{fallback:03d}"


def _score(selected: Mapping[str, Any]) -> float:
    return float(selected.get("score") or 0.0)


def _scene_from_case_id(case_id: str) -> str:
    parts = [part for part in case_id.lower().split("_") if part]
    if not parts:
        return "unknown"
    if parts[0] in {"relstage", "relaxis", "case"} and len(parts) >= 2:
        rest = "_".join(parts[1:])
        for stage in _RELATIONSHIP_STAGE_SUFFIXES:
            suffix = f"_{stage}"
            if rest.endswith(suffix):
                scene = rest[: -len(suffix)]
                return scene or parts[1]
        return parts[1]
    return parts[0]


def _relationship_distance_for_case(case: Mapping[str, Any]) -> str:
    control = _safe_mapping(case.get("reply_control_packet") or case.get("control"))
    return _clean_text(control.get("relationship_distance")) or "familiar"


def _marker_hits(text: str) -> dict[str, list[str]]:
    physical_hits = [marker for marker in _PHYSICAL_MARKERS if marker in text]
    if _INTIMATE_COME_HERE_RE.search(text):
        physical_hits.append("过来")
    return {
        "nickname": [marker for marker in ("尼尼",) if marker in text],
        "physical_intimacy": list(dict.fromkeys(physical_hits)),
        "explicit_romance": [marker for marker in _EXPLICIT_ROMANCE_MARKERS if marker in text],
        "commitment": [marker for marker in _COMMITMENT_MARKERS if marker in text],
        "care": [marker for marker in _CARE_MARKERS if marker in text],
        "close_presence": [marker for marker in _CLOSE_PRESENCE_MARKERS if marker in text],
    }


def _violation_flags(
    *,
    axes: Mapping[str, Any],
    markers: Mapping[str, Sequence[str]],
    scene: str,
) -> list[str]:
    flags: list[str] = []
    if axes.get("nickname_policy") == "forbidden" and markers.get("nickname"):
        flags.append("nickname_forbidden")
    if not axes.get("physical_intimacy_allowed") and markers.get("physical_intimacy"):
        flags.append("physical_intimacy_overstep")
    if not axes.get("explicit_romance_allowed") and markers.get("explicit_romance"):
        flags.append("explicit_romance_overstep")
    if not axes.get("commitment_language_allowed") and markers.get("commitment"):
        flags.append("commitment_overstep")
    if (
        axes.get("affection_visibility") in {"explicit_short", "default_present"}
        and scene not in _AFFECTION_OPTIONAL_SCENES
        and not (
            markers.get("physical_intimacy")
            or markers.get("explicit_romance")
            or markers.get("close_presence")
        )
    ):
        flags.append("lover_affection_under_visible")
    return flags


def _avg(values: Sequence[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _group_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    scores = [float(row["score"]) for row in rows]
    total = len(rows)
    marker_counts = Counter()
    violation_counts = Counter()
    scenes = Counter()
    for row in rows:
        scenes[str(row.get("scene") or "unknown")] += 1
        marker_flags = _safe_mapping(row.get("marker_flags"))
        for key, value in marker_flags.items():
            if value:
                marker_counts[key] += 1
        for flag in _safe_list(row.get("violations")):
            violation_counts[_clean_text(flag)] += 1
    return {
        "count": total,
        "pass_count": sum(1 for row in rows if row.get("passed")),
        "pass_rate": _rate(sum(1 for row in rows if row.get("passed")), total),
        "avg_score": _avg(scores),
        "min_score": round(min(scores), 3) if scores else None,
        "marker_rates": {key: _rate(value, total) for key, value in sorted(marker_counts.items())},
        "violation_counts": dict(violation_counts),
        "scenes": [{"scene": key, "count": value} for key, value in scenes.most_common()],
    }


def _case_rows(
    *,
    cases: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    cases_by_id = {_case_id(case, index): dict(case) for index, case in enumerate(cases, start=1)}
    rows: list[dict[str, Any]] = []
    for index, score_row in enumerate(score_rows, start=1):
        case_id = _case_id(score_row, index)
        case = cases_by_id.get(case_id, {})
        distance = _relationship_distance_for_case(case)
        axes = relationship_control_axes_for_distance(distance).as_dict()
        selected = _selected(score_row)
        text = _clean_text(selected.get("text"))
        scene = _scene_from_case_id(case_id)
        markers = _marker_hits(text)
        marker_flags = {key: bool(value) for key, value in markers.items()}
        marker_flags["intimacy_signal"] = bool(
            marker_flags.get("physical_intimacy")
            or marker_flags.get("explicit_romance")
            or marker_flags.get("close_presence")
        )
        violations = _violation_flags(axes=axes, markers=markers, scene=scene)
        rows.append(
            {
                "case_id": case_id,
                "scene": scene,
                "relationship_distance": distance,
                "axes": {key: axes.get(key) for key in _AXIS_KEYS},
                "text": text,
                "score": round(_score(selected), 3),
                "passed": bool(selected.get("passed")),
                "reason_codes": [_clean_text(item) for item in _safe_list(selected.get("reason_codes")) if _clean_text(item)],
                "markers": markers,
                "marker_flags": marker_flags,
                "violations": violations,
            }
        )
    return rows


def build_relationship_axis_report(
    *,
    cases: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
    max_examples: int = 4,
) -> dict[str, Any]:
    rows = _case_rows(cases=cases, score_rows=score_rows)
    total = len(rows)
    rows_by_stage: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    rows_by_axis: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        axes = _safe_mapping(row.get("axes"))
        rows_by_stage[str(axes.get("stage") or "unknown")].append(row)
        for key in _AXIS_KEYS:
            rows_by_axis[key][str(axes.get(key))].append(row)

    stage_summaries = []
    for stage, stage_rows in rows_by_stage.items():
        axes = _safe_mapping(stage_rows[0].get("axes")) if stage_rows else {}
        summary = _group_summary(stage_rows)
        summary.update(
            {
                "stage": stage,
                "progress": int(axes.get("progress") or 0),
                "axes": axes,
                "lowest_examples": sorted(
                    (
                        {
                            "case_id": str(row["case_id"]),
                            "scene": str(row["scene"]),
                            "score": float(row["score"]),
                            "text": str(row["text"]),
                            "violations": list(_safe_list(row.get("violations"))),
                        }
                        for row in stage_rows
                    ),
                    key=lambda item: (float(item["score"]), item["case_id"]),
                )[:max_examples],
            }
        )
        stage_summaries.append(summary)

    axis_summaries: dict[str, list[dict[str, Any]]] = {}
    for axis, grouped in rows_by_axis.items():
        axis_summaries[axis] = [
            {"value": value, **_group_summary(axis_rows)}
            for value, axis_rows in sorted(grouped.items(), key=lambda item: item[0])
        ]

    violation_rows = [
        {
            "case_id": row["case_id"],
            "stage": _safe_mapping(row.get("axes")).get("stage"),
            "scene": row["scene"],
            "score": row["score"],
            "text": row["text"],
            "violations": row["violations"],
            "markers": row["markers"],
        }
        for row in rows
        if row.get("violations")
    ]
    selected_scores = [float(row["score"]) for row in rows]
    return {
        "total": total,
        "pass_count": sum(1 for row in rows if row.get("passed")),
        "pass_rate": _rate(sum(1 for row in rows if row.get("passed")), total),
        "avg_score": _avg(selected_scores),
        "min_score": round(min(selected_scores), 3) if selected_scores else None,
        "stage_summaries": sorted(stage_summaries, key=lambda item: int(item.get("progress") or 0)),
        "axis_summaries": axis_summaries,
        "violation_count": len(violation_rows),
        "violations": violation_rows,
        "rows": rows,
    }


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# Relationship Axis Report",
        "",
        f"- total: {report.get('total')}",
        f"- pass_rate: {report.get('pass_rate')}",
        f"- avg_score: {report.get('avg_score')}",
        f"- min_score: {report.get('min_score')}",
        f"- violation_count: {report.get('violation_count')}",
        "",
        "## Stage Summary",
        "",
        "| stage | n | avg | min | pass | care | nickname | intimate | physical | romance | violations |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in _safe_list(report.get("stage_summaries")):
        marker_rates = _safe_mapping(row.get("marker_rates"))
        violation_counts = _safe_mapping(row.get("violation_counts"))
        lines.append(
            "| {stage} | {count} | {avg} | {min_score} | {pass_rate} | {care} | {nickname} | {intimate} | {physical} | {romance} | {violations} |".format(
                stage=row.get("stage"),
                count=row.get("count"),
                avg=row.get("avg_score"),
                min_score=row.get("min_score"),
                pass_rate=row.get("pass_rate"),
                care=marker_rates.get("care", 0.0),
                nickname=marker_rates.get("nickname", 0.0),
                intimate=marker_rates.get("intimacy_signal", 0.0),
                physical=marker_rates.get("physical_intimacy", 0.0),
                romance=marker_rates.get("explicit_romance", 0.0),
                violations=sum(int(value) for value in violation_counts.values()),
            )
        )

    lines.extend(["", "## Axis Summary", ""])
    axis_summaries = _safe_mapping(report.get("axis_summaries"))
    for axis in (
        "affection_visibility",
        "nickname_policy",
        "physical_intimacy_allowed",
        "explicit_romance_allowed",
        "commitment_language_allowed",
    ):
        lines.extend([f"### {axis}", "", "| value | n | avg | pass | violations |", "| --- | ---: | ---: | ---: | ---: |"])
        for item in _safe_list(axis_summaries.get(axis)):
            violation_counts = _safe_mapping(item.get("violation_counts"))
            lines.append(
                f"| {item.get('value')} | {item.get('count')} | {item.get('avg_score')} | {item.get('pass_rate')} | {sum(int(value) for value in violation_counts.values())} |"
            )
        lines.append("")

    violations = _safe_list(report.get("violations"))
    lines.extend(["## Violations", ""])
    if not violations:
        lines.append("No axis-level violations detected.")
    else:
        for row in violations[:30]:
            lines.append(
                f"- {row.get('case_id')} [{row.get('stage')}/{row.get('scene')}] {row.get('score')}: "
                f"{', '.join(str(item) for item in _safe_list(row.get('violations')))} | {row.get('text')}"
            )
    lines.append("")
    return "\n".join(lines)


def write_relationship_axis_report(
    *,
    cases_path: Path,
    scores_path: Path,
    out_dir: Path,
    max_examples: int = 4,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = build_relationship_axis_report(
        cases=_read_jsonl(cases_path),
        score_rows=_read_jsonl(scores_path),
        max_examples=max_examples,
    )
    _write_json(out_dir / "relationship_axis_summary.json", report)
    (out_dir / "relationship_axis_report.md").write_text(render_markdown_report(report), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize isolated surface output by derived relationship control axes.")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--scores", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-examples", type=int, default=4)
    args = parser.parse_args(argv)

    report = write_relationship_axis_report(
        cases_path=args.cases,
        scores_path=args.scores,
        out_dir=args.out,
        max_examples=args.max_examples,
    )
    print(
        "FINAL "
        + json.dumps(
            {
                "total": report.get("total"),
                "pass_rate": report.get("pass_rate"),
                "avg_score": report.get("avg_score"),
                "min_score": report.get("min_score"),
                "violation_count": report.get("violation_count"),
                "output_dir": str(args.out),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
