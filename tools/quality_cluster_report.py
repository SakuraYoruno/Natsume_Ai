from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def _safe_mapping(raw: Any) -> dict[str, Any]:
    return dict(raw or {}) if isinstance(raw, Mapping) else {}


def _safe_list(raw: Any) -> list[Any]:
    return list(raw or []) if isinstance(raw, list | tuple) else []


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _selected_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    selected = row.get("selected")
    if isinstance(selected, Mapping):
        return dict(selected)
    if "text" in row and "score" in row:
        return dict(row)
    return {}


def _case_id(row: Mapping[str, Any], fallback: int) -> str:
    return _clean_text(row.get("case_id")) or f"case_{fallback:03d}"


def _selected_from(row: Mapping[str, Any]) -> str:
    return _clean_text(row.get("selected_from")) or _clean_text(row.get("source")) or "unknown"


def _score(selected: Mapping[str, Any]) -> float:
    return float(selected.get("score") or 0.0)


def _reason_codes(selected: Mapping[str, Any]) -> list[str]:
    return [_clean_text(item) for item in _safe_list(selected.get("reason_codes")) if _clean_text(item)]


def _control_violations(selected: Mapping[str, Any]) -> list[str]:
    return [_clean_text(item) for item in _safe_list(selected.get("control_violations")) if _clean_text(item)]


def _quality(selected: Mapping[str, Any]) -> dict[str, Any]:
    return _safe_mapping(selected.get("quality"))


def _quality_forbidden_hits(selected: Mapping[str, Any]) -> list[str]:
    return [_clean_text(item) for item in _safe_list(_quality(selected).get("forbidden_hits")) if _clean_text(item)]


def _dimensions(selected: Mapping[str, Any]) -> dict[str, float]:
    return {str(key): float(value) for key, value in _safe_mapping(selected.get("dimensions")).items()}


def _family_from_case_id(case_id: str) -> str:
    parts = [part for part in case_id.split("_") if part and not part.isdigit()]
    if len(parts) >= 3:
        return "_".join(parts[:3])
    return "_".join(parts[:2]) if len(parts) >= 2 else case_id


_SCENE_TOKENS = (
    "morning",
    "back",
    "tired",
    "recall",
    "broken",
    "failure",
    "presence",
    "quiet",
    "creative",
    "technical",
    "daily",
)


def _scene_from_case_id(case_id: str) -> str:
    normalized = case_id.lower()
    for token in _SCENE_TOKENS:
        if f"_{token}_" in f"_{normalized}_" or normalized.endswith(f"_{token}") or normalized.startswith(f"{token}_"):
            return token
    parts = [part for part in normalized.split("_") if part and not part.isdigit()]
    if len(parts) >= 2:
        return parts[1] if parts[0] in {"relstage", "case"} else parts[0]
    return normalized or "unknown"


def _control_cluster_name(violation: str) -> str:
    return "control:" + violation.split(":", 1)[0]


def _surface_clusters(selected: Mapping[str, Any]) -> list[str]:
    text = _clean_text(selected.get("text"))
    clusters: list[str] = []
    forbidden_hits = set(_quality_forbidden_hits(selected))
    wave_count = text.count("~") + text.count("～")
    if "overused_wave_punctuation" in forbidden_hits or wave_count > 2:
        clusters.append("surface:overused_wave_punctuation")
    if "ASCII_ELLIPSIS_OUTPUT" in set(_reason_codes(selected)) or "..." in text:
        clusters.append("surface:ascii_ellipsis")
    if text.count("老哥") + text.count("哥哥") > 2 or text.count("尼尼") > 1:
        clusters.append("surface:repeated_address")
    if text.count("？") + text.count("?") > 0:
        clusters.append("watch:question")
    if len(text) <= 8 and not _reason_codes(selected):
        clusters.append("watch:very_short_clean_pass")
    return clusters


def clusters_for_selected(
    selected: Mapping[str, Any],
    *,
    threshold: float,
    challenge_threshold: float | None,
    dimension_threshold: float,
) -> list[str]:
    clusters: list[str] = []
    score = _score(selected)
    if selected.get("hard_fail"):
        clusters.append("status:hard_fail")
    if not selected.get("passed") or score < threshold:
        clusters.append("status:low_score")
    elif challenge_threshold is not None and score < challenge_threshold:
        clusters.append("status:weak_pass")

    quality_hits = set(_quality_forbidden_hits(selected))
    generic_quality_reasons = {"FORBIDDEN_TEMPLATE_TEXT", "QUALITY_SOFT_FORBIDDEN_TEMPLATE_TEXT"}
    clusters.extend(
        "reason:" + reason
        for reason in _reason_codes(selected)
        if reason not in generic_quality_reasons or not quality_hits
    )
    clusters.extend("quality_hit:" + hit for hit in quality_hits if hit != "overused_wave_punctuation")
    clusters.extend(_control_cluster_name(item) for item in _control_violations(selected))
    clusters.extend(_surface_clusters(selected))

    for dimension, value in _dimensions(selected).items():
        if value < dimension_threshold:
            clusters.append("low_dimension:" + dimension)
    return list(dict.fromkeys(clusters))


def _example_payload(row: Mapping[str, Any], selected: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    case_id = _case_id(row, index)
    return {
        "index": int(row.get("index") or index),
        "case_id": case_id,
        "case_family": _family_from_case_id(case_id),
        "scene": _scene_from_case_id(case_id),
        "score": round(_score(selected), 3),
        "passed": bool(selected.get("passed")),
        "hard_fail": bool(selected.get("hard_fail")),
        "selected_from": _selected_from(row),
        "reason_codes": _reason_codes(selected),
        "quality_forbidden_hits": _quality_forbidden_hits(selected),
        "text": _clean_text(selected.get("text")),
    }


def _summarize_clusters(
    cluster_rows: Mapping[str, list[dict[str, Any]]],
    *,
    total: int,
    max_examples: int,
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for name, rows in cluster_rows.items():
        scores = [float(row["score"]) for row in rows]
        families = Counter(str(row["case_family"]) for row in rows)
        summaries.append(
            {
                "cluster": name,
                "count": len(rows),
                "rate": round(len(rows) / total, 4) if total else 0.0,
                "avg_score": round(statistics.fmean(scores), 3) if scores else None,
                "min_score": round(min(scores), 3) if scores else None,
                "case_families": [{"family": key, "count": value} for key, value in families.most_common(6)],
                "examples": sorted(rows, key=lambda item: (float(item["score"]), int(item["index"])))[:max_examples],
            }
        )
    return sorted(
        summaries,
        key=lambda item: (-int(item["count"]), float(item["min_score"] if item["min_score"] is not None else 999.0), item["cluster"]),
    )


def _is_watch_cluster(cluster_name: str) -> bool:
    return cluster_name.startswith("watch:")


def _is_redundant_issue_cluster(cluster_name: str) -> bool:
    return cluster_name in {
        "reason:LOW_CATCHINESS",
        "reason:CONTEXT_SURFACE_PENALTY",
    }


def _duplicate_surface_groups(selected_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows_by_text: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        text = _clean_text(row.get("text"))
        if text:
            rows_by_text[text].append(row)
    groups: list[dict[str, Any]] = []
    for text, rows in rows_by_text.items():
        if len(rows) < 2:
            continue
        groups.append(
            {
                "text": text,
                "count": len(rows),
                "scenes": sorted({str(row.get("scene") or "unknown") for row in rows}),
                "case_ids": [str(row.get("case_id") or "") for row in rows],
                "scores": [float(row.get("score") or 0.0) for row in rows],
            }
        )
    return sorted(groups, key=lambda item: (-int(item["count"]), item["text"]))


def _scene_diversity(selected_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows_by_scene: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        rows_by_scene[str(row.get("scene") or "unknown")].append(row)
    diversity: list[dict[str, Any]] = []
    for scene, rows in sorted(rows_by_scene.items()):
        texts = [_clean_text(row.get("text")) for row in rows if _clean_text(row.get("text"))]
        unique_texts = set(texts)
        duplicate_count = max(0, len(texts) - len(unique_texts))
        diversity.append(
            {
                "scene": scene,
                "count": len(rows),
                "unique_text_count": len(unique_texts),
                "duplicate_count": duplicate_count,
                "unique_rate": round(len(unique_texts) / len(texts), 4) if texts else None,
                "repeated_texts": [
                    {"text": text, "count": count}
                    for text, count in Counter(texts).most_common()
                    if count > 1
                ],
            }
        )
    return diversity


def build_quality_cluster_report(
    rows: Sequence[Mapping[str, Any]],
    *,
    source: str = "",
    threshold: float = 80.0,
    challenge_threshold: float | None = 83.0,
    dimension_threshold: float = 83.0,
    cluster_alert_percent: float = 3.0,
    min_cluster_count: int | None = None,
    max_examples: int = 4,
    tail_count: int = 12,
) -> dict[str, Any]:
    selected_rows: list[dict[str, Any]] = []
    cluster_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_counts: Counter[str] = Counter()

    for index, row in enumerate(rows, start=1):
        selected = _selected_from_row(row)
        if not selected:
            continue
        example = _example_payload(row, selected, index=index)
        selected_rows.append(example)
        source_counts[example["selected_from"]] += 1
        for cluster in clusters_for_selected(
            selected,
            threshold=threshold,
            challenge_threshold=challenge_threshold,
            dimension_threshold=dimension_threshold,
        ):
            cluster_rows[cluster].append(example)

    total = len(selected_rows)
    duplicate_surface_groups = _duplicate_surface_groups(selected_rows)
    duplicate_case_ids = {
        case_id
        for group in duplicate_surface_groups
        for case_id in group.get("case_ids", [])
    }
    if duplicate_case_ids:
        for row in selected_rows:
            if row["case_id"] in duplicate_case_ids:
                cluster_rows["batch_duplicate_surface"].append(row)
    scene_diversity = _scene_diversity(selected_rows)
    scores = [float(row["score"]) for row in selected_rows]
    alert_count = min_cluster_count
    if alert_count is None:
        alert_count = max(2, int(math.ceil(total * (cluster_alert_percent / 100.0))))
    clusters = _summarize_clusters(cluster_rows, total=total, max_examples=max_examples)
    large_clusters = [
        cluster
        for cluster in clusters
        if int(cluster["count"]) >= int(alert_count)
        and not _is_watch_cluster(str(cluster["cluster"]))
        and not _is_redundant_issue_cluster(str(cluster["cluster"]))
    ]
    diagnostic_clusters = [
        cluster
        for cluster in clusters
        if int(cluster["count"]) >= int(alert_count) and _is_redundant_issue_cluster(str(cluster["cluster"]))
    ]
    watch_clusters = [
        cluster
        for cluster in clusters
        if int(cluster["count"]) >= int(alert_count) and _is_watch_cluster(str(cluster["cluster"]))
    ]
    singleton_clusters = [cluster for cluster in clusters if int(cluster["count"]) == 1]
    tail_cases = sorted(selected_rows, key=lambda item: (float(item["score"]), int(item["index"])))[:tail_count]
    weak_pass_count = sum(
        1
        for row in selected_rows
        if row["passed"] and challenge_threshold is not None and threshold <= float(row["score"]) < challenge_threshold
    )
    low_score_count = sum(1 for row in selected_rows if not row["passed"] or float(row["score"]) < threshold)

    return {
        "source": source,
        "cases": len(rows),
        "selected_count": total,
        "pass_count": sum(1 for row in selected_rows if row["passed"]),
        "pass_rate": round(sum(1 for row in selected_rows if row["passed"]) / total, 4) if total else None,
        "avg_score": round(statistics.fmean(scores), 3) if scores else None,
        "min_score": round(min(scores), 3) if scores else None,
        "threshold": float(threshold),
        "challenge_threshold": float(challenge_threshold) if challenge_threshold is not None else None,
        "dimension_threshold": float(dimension_threshold),
        "cluster_alert_count": int(alert_count),
        "low_score_count": low_score_count,
        "weak_pass_count": weak_pass_count,
        "hard_fail_count": sum(1 for row in selected_rows if row["hard_fail"]),
        "source_counts": dict(source_counts),
        "duplicate_surface_group_count": len(duplicate_surface_groups),
        "duplicate_surface_case_count": len(duplicate_case_ids),
        "duplicate_surface_groups": duplicate_surface_groups,
        "scene_diversity": scene_diversity,
        "cluster_count": len(clusters),
        "large_cluster_count": len(large_clusters),
        "diagnostic_cluster_count": len(diagnostic_clusters),
        "watch_cluster_count": len(watch_clusters),
        "singleton_cluster_count": len(singleton_clusters),
        "large_clusters": large_clusters,
        "diagnostic_clusters": diagnostic_clusters,
        "watch_clusters": watch_clusters,
        "clusters": clusters,
        "tail_cases": tail_cases,
    }


def _markdown_table(rows: Iterable[Sequence[Any]]) -> list[str]:
    materialized = [list(row) for row in rows]
    if not materialized:
        return []
    header = materialized[0]
    lines = [
        "| " + " | ".join(str(item) for item in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in materialized[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return lines


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        "# Isolated Surface Quality Cluster Report",
        "",
        f"- source: `{report.get('source') or ''}`",
        f"- selected: {report.get('selected_count')} / cases: {report.get('cases')}",
        f"- pass: {report.get('pass_count')} ({report.get('pass_rate')})",
        f"- avg_score: {report.get('avg_score')} / min_score: {report.get('min_score')}",
        f"- low_score: {report.get('low_score_count')} / weak_pass: {report.get('weak_pass_count')} / hard_fail: {report.get('hard_fail_count')}",
        f"- duplicate_surface_groups: {report.get('duplicate_surface_group_count')} / duplicate_cases: {report.get('duplicate_surface_case_count')}",
        f"- large_cluster_threshold: >= {report.get('cluster_alert_count')} cases",
        "",
        "## Large Issue Clusters",
        "",
    ]
    large_clusters = list(report.get("large_clusters") or [])
    if large_clusters:
        lines.extend(
            _markdown_table(
                [
                    ("cluster", "count", "rate", "avg", "min", "families", "example"),
                    *[
                        (
                            cluster["cluster"],
                            cluster["count"],
                            cluster["rate"],
                            cluster["avg_score"],
                            cluster["min_score"],
                            ", ".join(f"{item['family']}:{item['count']}" for item in cluster.get("case_families", [])[:3]),
                            (cluster.get("examples") or [{}])[0].get("text", ""),
                        )
                        for cluster in large_clusters
                    ],
                ]
            )
        )
    else:
        lines.append("No cluster reached the alert threshold.")
    diagnostic_clusters = list(report.get("diagnostic_clusters") or [])
    lines.extend(["", "## Diagnostic Overlap", ""])
    if diagnostic_clusters:
        lines.extend(
            _markdown_table(
                [
                    ("cluster", "count", "rate", "min", "example"),
                    *[
                        (
                            cluster["cluster"],
                            cluster["count"],
                            cluster["rate"],
                            cluster["min_score"],
                            (cluster.get("examples") or [{}])[0].get("text", ""),
                        )
                        for cluster in diagnostic_clusters
                    ],
                ]
            )
        )
    else:
        lines.append("No large redundant diagnostic cluster.")
    lines.extend(["", "## Batch Diversity", ""])
    scene_diversity = list(report.get("scene_diversity") or [])
    if scene_diversity:
        lines.extend(
            _markdown_table(
                [
                    ("scene", "count", "unique", "unique_rate", "duplicates", "top_repeated"),
                    *[
                        (
                            row["scene"],
                            row["count"],
                            row["unique_text_count"],
                            row["unique_rate"],
                            row["duplicate_count"],
                            " / ".join(
                                f"{item['count']}x {item['text']}" for item in (row.get("repeated_texts") or [])[:2]
                            ),
                        )
                        for row in scene_diversity
                    ],
                ]
            )
        )
    else:
        lines.append("No scene diversity data.")
    duplicate_groups = list(report.get("duplicate_surface_groups") or [])
    lines.extend(["", "## Repeated Selected Text", ""])
    if duplicate_groups:
        lines.extend(
            _markdown_table(
                [
                    ("count", "scenes", "case_ids", "text"),
                    *[
                        (
                            group["count"],
                            ", ".join(group.get("scenes") or []),
                            ", ".join(group.get("case_ids") or []),
                            group["text"],
                        )
                        for group in duplicate_groups[:12]
                    ],
                ]
            )
        )
    else:
        lines.append("No exact selected text repeated.")
    watch_clusters = list(report.get("watch_clusters") or [])
    lines.extend(["", "## Watch Clusters", ""])
    if watch_clusters:
        lines.extend(
            _markdown_table(
                [
                    ("cluster", "count", "rate", "avg", "min", "example"),
                    *[
                        (
                            cluster["cluster"],
                            cluster["count"],
                            cluster["rate"],
                            cluster["avg_score"],
                            cluster["min_score"],
                            (cluster.get("examples") or [{}])[0].get("text", ""),
                        )
                        for cluster in watch_clusters
                    ],
                ]
            )
        )
    else:
        lines.append("No watch cluster reached the alert threshold.")
    lines.extend(["", "## Lowest Selected Cases", ""])
    tail_cases = list(report.get("tail_cases") or [])
    if tail_cases:
        lines.extend(
            _markdown_table(
                [
                    ("case_id", "score", "passed", "from", "reasons", "text"),
                    *[
                        (
                            row["case_id"],
                            row["score"],
                            row["passed"],
                            row["selected_from"],
                            ", ".join(row.get("reason_codes") or []),
                            row["text"],
                        )
                        for row in tail_cases
                    ],
                ]
            )
        )
    else:
        lines.append("No selected rows.")
    lines.extend(["", "## Top Clusters", ""])
    clusters = list(report.get("clusters") or [])[:20]
    if clusters:
        lines.extend(
            _markdown_table(
                [
                    ("cluster", "count", "rate", "min", "examples"),
                    *[
                        (
                            cluster["cluster"],
                            cluster["count"],
                            cluster["rate"],
                            cluster["min_score"],
                            " / ".join(example["case_id"] for example in (cluster.get("examples") or [])[:3]),
                        )
                        for cluster in clusters
                    ],
                ]
            )
        )
    else:
        lines.append("No clusters.")
    lines.append("")
    return "\n".join(lines)


def run_report(args: argparse.Namespace) -> dict[str, Any]:
    rows = _read_jsonl(args.scores)
    report = build_quality_cluster_report(
        rows,
        source=str(args.scores),
        threshold=float(args.threshold),
        challenge_threshold=None if args.challenge_threshold is None else float(args.challenge_threshold),
        dimension_threshold=float(args.dimension_threshold),
        cluster_alert_percent=float(args.cluster_alert_percent),
        min_cluster_count=args.min_cluster_count,
        max_examples=int(args.max_examples),
        tail_count=int(args.tail_count),
    )
    args.out.mkdir(parents=True, exist_ok=True)
    _write_json(args.out / "quality_cluster_summary.json", report)
    (args.out / "quality_cluster_report.md").write_text(render_markdown_report(report), encoding="utf-8")
    return report


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cluster isolated surface quality issues from scored JSONL artifacts.")
    parser.add_argument("--scores", required=True, type=Path, help="Path to merged_scores.jsonl, live_scores.jsonl, or retry_scores.jsonl.")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--threshold", type=float, default=80.0)
    parser.add_argument("--challenge-threshold", type=float, default=83.0)
    parser.add_argument("--dimension-threshold", type=float, default=83.0)
    parser.add_argument("--cluster-alert-percent", type=float, default=3.0)
    parser.add_argument("--min-cluster-count", type=int, default=None)
    parser.add_argument("--max-examples", type=int, default=4)
    parser.add_argument("--tail-count", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_report(args)
    print(
        "FINAL "
        + json.dumps(
            {
                "selected_count": report["selected_count"],
                "pass_rate": report["pass_rate"],
                "large_cluster_count": report["large_cluster_count"],
                "duplicate_surface_group_count": report["duplicate_surface_group_count"],
                "output_dir": str(args.out),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
