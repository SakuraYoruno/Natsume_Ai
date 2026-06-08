from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.trace_digital_life_causality import build_causality_traces  # noqa: E402
from tools.validate_long_run_digital_life_loop import (  # noqa: E402
    build_arg_parser as build_long_run_arg_parser,
    run_long_run_digital_life_loop_validation,
)


DEFAULT_OUT = ROOT / "artifacts" / "digital_life_soak"
LEGACY_IDENTITY_MARKERS = (
    "新海天",
    "夏目蓝",
    "夏目藍",
    "七海",
    "sora",
    "Sora",
    "user_1",
)
INTERNAL_REPLY_MARKERS = (
    "ReplyControlPacket",
    "控制包",
    "器官",
    "organ_outputs",
    "raw_organ_outputs",
    "inner_monologue",
    "self_model",
    "source_refs=",
    "memory://",
    "evd://",
    "候选",
    "评分",
    "score_trace",
)
MAX_BOND_JUMP_PER_TURN = 25


async def run_digital_life_soak_validation(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = _normalize_mode(str(getattr(args, "mode", "fixture") or "fixture"))
    turns = max(1, int(getattr(args, "turns", 32) or 32))
    min_turns = max(1, int(getattr(args, "min_turns", 30) or 30))

    long_result = await run_long_run_digital_life_loop_validation(
        _long_run_args(args=args, out_dir=out_dir, mode=mode, turns=turns, min_turns=min_turns)
    )
    long_summary = dict(long_result.get("summary") or {})
    long_details = dict(long_result.get("details") or {})
    full_details = dict(long_details.get("full_details") or {})
    timeline = [dict(item) for item in list(long_details.get("timeline") or []) if isinstance(item, Mapping)]
    traces = build_causality_traces(full_details)
    drift = _drift_report(full_details=full_details, traces=traces)
    checks = _checks(
        long_summary=long_summary,
        full_details=full_details,
        timeline=timeline,
        traces=traces,
        drift=drift,
        min_turns=min_turns,
    )
    failed = [check for check in checks if not bool(check.get("passed"))]
    summary = {
        "status": "PASS" if not failed else "FAIL",
        "evaluation_mode": "digital_life_soak_contract_validation_no_text_quality_scoring",
        "mode": mode,
        "turn_count": len(list(full_details.get("turns") or [])),
        "min_turns": min_turns,
        "requested_turns": turns,
        "timeline_event_count": len(timeline),
        "causality_turn_count": len(traces),
        "drift_issue_count": len(list(drift.get("issues") or [])),
        "failed_count": len(failed),
        "idle_loop_tick_count": int(long_summary.get("idle_loop_tick_count") or 0),
        "idle_loop_simulated_hours": float(long_summary.get("idle_loop_simulated_hours") or 0.0),
        "checks": {str(check.get("name")): bool(check.get("passed")) for check in checks},
        "output_dir": str(out_dir),
        "long_run_output_dir": str(long_summary.get("output_dir") or out_dir / "long_run"),
        "full_loop_output_dir": str(long_summary.get("full_loop_output_dir") or ""),
        "idle_loop_output_dir": str(long_summary.get("idle_loop_output_dir") or ""),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "drift": drift,
        "long_summary": long_summary,
        "causality_traces": traces,
        "timeline": timeline,
        "run_id": str(full_details.get("run_id") or ""),
        "persona_self_id": str(full_details.get("persona_self_id") or ""),
        "bond_user_id": str(full_details.get("bond_user_id") or ""),
        "session_id": str(full_details.get("session_id") or ""),
    }
    _write_json(out_dir / "soak_summary.json", summary)
    _write_json(out_dir / "soak_details.json", details)
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    _write_jsonl(out_dir / "soak_timeline.jsonl", timeline)
    _write_jsonl(out_dir / "soak_causality.jsonl", traces)
    (out_dir / "soak_report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _checks(
    *,
    long_summary: Mapping[str, Any],
    full_details: Mapping[str, Any],
    timeline: Sequence[Mapping[str, Any]],
    traces: Sequence[Mapping[str, Any]],
    drift: Mapping[str, Any],
    min_turns: int,
) -> list[dict[str, Any]]:
    turns = [dict(turn) for turn in list(full_details.get("turns") or []) if isinstance(turn, Mapping)]
    long_checks = dict(long_summary.get("checks") or {})
    drift_flags = dict(drift.get("flags") or {})
    return [
        _check("long_run_validation_passed", bool(long_summary.get("passed")), {"output_dir": long_summary.get("output_dir")}),
        _check("soak_reaches_min_turns", len(turns) >= min_turns, {"turn_count": len(turns), "min_turns": min_turns}),
        _check(
            "soak_has_causality_for_each_turn",
            len(traces) == len(turns) and bool(traces),
            {"turn_count": len(turns), "causality_turn_count": len(traces)},
        ),
        _check(
            "soak_all_turns_map_23_organs",
            bool(traces) and all(int(trace.get("active_organ_count") or 0) >= 23 for trace in traces),
            {
                "failed": [
                    trace.get("turn_index")
                    for trace in traces
                    if int(trace.get("active_organ_count") or 0) < 23
                ],
                "long_run_check": long_checks.get("long_run.all_turns_map_23_organs"),
            },
        ),
        _check(
            "soak_memory_writes_each_turn",
            bool(traces) and all(bool(dict(trace.get("memory") or {}).get("write_success")) for trace in traces),
            {"long_run_check": long_checks.get("long_run.memory_written_each_turn")},
        ),
        _check(
            "soak_emotion_and_vts_each_turn",
            bool(traces)
            and all(
                dict(trace.get("emotion_vts") or {}).get("user_emotion_tag")
                and dict(trace.get("emotion_vts") or {}).get("natsume_emotion_tag")
                and dict(trace.get("emotion_vts") or {}).get("vts_expression_id")
                for trace in traces
            ),
            {"failed": _turns_missing_emotion_vts(traces)},
        ),
        _check(
            "soak_bond_updates_each_turn",
            bool(traces)
            and all(str(dict(trace.get("bond") or {}).get("status") or "") == "APPLIED" for trace in traces),
            {"failed": _turns_missing_bond_updates(traces)},
        ),
        _check(
            "soak_idle_private_thoughts_ok",
            bool(long_summary.get("idle_loop_passed"))
            and bool(long_checks.get("idle_loop.default_mode_uses_model_or_waits_without_template", True))
            and bool(long_checks.get("idle_loop.inner_world_generated_private_thoughts_each_tick", True)),
            {
                "idle_loop_passed": long_summary.get("idle_loop_passed"),
                "idle_loop_tick_count": long_summary.get("idle_loop_tick_count"),
                "idle_loop_simulated_hours": long_summary.get("idle_loop_simulated_hours"),
            },
        ),
        _check(
            "soak_self_model_progresses",
            not bool(drift_flags.get("self_update_stopped")),
            {"self_update_counts": drift.get("self_update_counts")},
        ),
        _check(
            "soak_no_identity_drift",
            not bool(drift_flags.get("identity_drift")),
            {"issues": _issues_by_kind(drift, "identity_drift")},
        ),
        _check(
            "soak_no_internal_or_private_leak",
            not bool(drift_flags.get("internal_leak")) and not bool(drift_flags.get("private_leak")),
            {
                "internal_issues": _issues_by_kind(drift, "internal_leak"),
                "private_issues": _issues_by_kind(drift, "private_leak"),
            },
        ),
        _check(
            "soak_emotion_varies",
            not bool(drift_flags.get("emotion_monotony")),
            {
                "user_emotion_tags": drift.get("user_emotion_tags"),
                "natsume_emotion_tags": drift.get("natsume_emotion_tags"),
            },
        ),
        _check(
            "soak_bond_changes_without_abnormal_jump",
            not bool(drift_flags.get("bond_stuck")) and not bool(drift_flags.get("bond_abnormal_jump")),
            {"bond_scores": drift.get("bond_scores"), "bond_deltas": drift.get("bond_deltas")},
        ),
        _check(
            "soak_memory_recall_not_dead",
            not bool(drift_flags.get("memory_recall_dead")),
            {"recall_fact_counts": drift.get("recall_fact_counts"), "recall_route_count": drift.get("recall_route_count")},
        ),
        _check(
            "soak_timeline_has_every_turn",
            _timeline_has_each_turn(timeline=timeline, turn_count=len(turns)),
            {"timeline_event_count": len(timeline), "turn_count": len(turns)},
        ),
    ]


def _drift_report(*, full_details: Mapping[str, Any], traces: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    turns = [dict(turn) for turn in list(full_details.get("turns") or []) if isinstance(turn, Mapping)]
    issues: list[dict[str, Any]] = []
    bond_scores: list[int] = []
    bond_deltas: list[int] = []
    self_update_counts: list[int] = []
    user_emotions: set[str] = set()
    natsume_emotions: set[str] = set()
    recall_fact_counts: list[int] = []
    recall_route_count = 0
    threshold = _variation_threshold(len(turns))

    for index, turn in enumerate(turns, start=1):
        reply = str(turn.get("reply_text") or "")
        private_text = _latest_private_text(turn)
        _collect_marker_issues(
            issues,
            kind="identity_drift",
            turn_index=index,
            text=reply,
            markers=LEGACY_IDENTITY_MARKERS,
            field="reply",
        )
        _collect_marker_issues(
            issues,
            kind="internal_leak",
            turn_index=index,
            text=reply,
            markers=INTERNAL_REPLY_MARKERS,
            field="reply",
        )
        if private_text and private_text in reply:
            issues.append(
                {
                    "kind": "private_leak",
                    "turn_index": index,
                    "field": "reply",
                    "marker": "latest_private_thought",
                    "sample": _sample(reply),
                }
            )
        surface = dict(turn.get("surface") or {})
        user_emotion = str(surface.get("user_emotion_tag") or "").strip()
        natsume_emotion = str(surface.get("natsume_emotion_tag") or "").strip()
        if user_emotion:
            user_emotions.add(user_emotion)
        if natsume_emotion:
            natsume_emotions.add(natsume_emotion)
        bond_state = dict(dict(turn.get("bond") or {}).get("state") or {})
        bond_scores.append(int(bond_state.get("total_score") or 0))
        self_after = dict(dict(turn.get("self_model") or {}).get("after") or {})
        self_update_counts.append(int(self_after.get("self_update_count") or 0))
        memory_recall = dict(turn.get("memory_recall") or {})
        recall_fact_counts.append(len(list(memory_recall.get("facts") or [])))
        recall_route_count += len(list(memory_recall.get("route_ids") or []))

    for previous, current in zip(bond_scores, bond_scores[1:]):
        bond_deltas.append(current - previous)
    for index, delta in enumerate(bond_deltas, start=2):
        if abs(delta) > MAX_BOND_JUMP_PER_TURN:
            issues.append(
                {
                    "kind": "bond_abnormal_jump",
                    "turn_index": index,
                    "delta": delta,
                    "max_allowed_abs_delta": MAX_BOND_JUMP_PER_TURN,
                }
            )
    if bond_scores and max(bond_scores) <= min(bond_scores):
        issues.append({"kind": "bond_stuck", "turn_index": None, "bond_scores": bond_scores})
    if len(user_emotions) < threshold or len(natsume_emotions) < threshold:
        issues.append(
            {
                "kind": "emotion_monotony",
                "turn_index": None,
                "threshold": threshold,
                "user_emotion_tags": sorted(user_emotions),
                "natsume_emotion_tags": sorted(natsume_emotions),
            }
        )
    if self_update_counts and (
        any(current < previous for previous, current in zip(self_update_counts, self_update_counts[1:]))
        or max(self_update_counts) < len(turns)
    ):
        issues.append({"kind": "self_update_stopped", "turn_index": None, "self_update_counts": self_update_counts})
    if turns and max(recall_fact_counts or [0]) <= 0 and recall_route_count <= 0:
        issues.append({"kind": "memory_recall_dead", "turn_index": None, "recall_fact_counts": recall_fact_counts})
    if len(traces) != len(turns):
        issues.append(
            {
                "kind": "causality_missing",
                "turn_index": None,
                "turn_count": len(turns),
                "causality_turn_count": len(traces),
            }
        )

    flags = {str(issue.get("kind")): True for issue in issues}
    return {
        "issues": issues,
        "flags": flags,
        "bond_scores": bond_scores,
        "bond_deltas": bond_deltas,
        "self_update_counts": self_update_counts,
        "user_emotion_tags": sorted(user_emotions),
        "natsume_emotion_tags": sorted(natsume_emotions),
        "recall_fact_counts": recall_fact_counts,
        "recall_route_count": recall_route_count,
        "variation_threshold": threshold,
    }


def _long_run_args(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    turns: int,
    min_turns: int,
) -> argparse.Namespace:
    argv = [
        "--mode",
        mode,
        "--out-dir",
        str(out_dir / "long_run"),
        "--run-id",
        str(getattr(args, "run_id", "") or f"soak-{int(time.time())}"),
        "--turns",
        str(turns),
        "--min-turns",
        str(min_turns),
        "--idle-ticks",
        str(max(1, int(getattr(args, "idle_ticks", 8) or 8))),
        "--idle-tick-minutes",
        str(max(5, int(getattr(args, "idle_tick_minutes", 180) or 180))),
        "--idle-min-hours",
        str(max(0.0, float(getattr(args, "idle_min_hours", 24.0) or 24.0))),
    ]
    if bool(getattr(args, "allow_hash_embedding", False)):
        argv.append("--allow-hash-embedding")
    if bool(getattr(args, "skip_idle_validation", False)):
        argv.append("--skip-idle-validation")
    return build_long_run_arg_parser().parse_args(argv)


def _collect_marker_issues(
    issues: list[dict[str, Any]],
    *,
    kind: str,
    turn_index: int,
    text: str,
    markers: Sequence[str],
    field: str,
) -> None:
    text_lower = text.lower()
    for marker in markers:
        marker_text = str(marker)
        haystack = text_lower if marker_text.isascii() else text
        needle = marker_text.lower() if marker_text.isascii() else marker_text
        if needle and needle in haystack:
            issues.append(
                {
                    "kind": kind,
                    "turn_index": turn_index,
                    "field": field,
                    "marker": marker_text,
                    "sample": _sample(text),
                }
            )


def _turns_missing_emotion_vts(traces: Sequence[Mapping[str, Any]]) -> list[Any]:
    failed = []
    for trace in traces:
        emotion = dict(trace.get("emotion_vts") or {})
        if not (emotion.get("user_emotion_tag") and emotion.get("natsume_emotion_tag") and emotion.get("vts_expression_id")):
            failed.append(trace.get("turn_index"))
    return failed


def _turns_missing_bond_updates(traces: Sequence[Mapping[str, Any]]) -> list[Any]:
    return [
        trace.get("turn_index")
        for trace in traces
        if str(dict(trace.get("bond") or {}).get("status") or "") != "APPLIED"
    ]


def _issues_by_kind(drift: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    return [dict(issue) for issue in list(drift.get("issues") or []) if str(dict(issue).get("kind") or "") == kind]


def _timeline_has_each_turn(*, timeline: Sequence[Mapping[str, Any]], turn_count: int) -> bool:
    if turn_count <= 0:
        return False
    by_turn: dict[int, set[str]] = {}
    for item in timeline:
        index = int(item.get("turn_index") or 0)
        if index <= 0:
            continue
        by_turn.setdefault(index, set()).add(str(item.get("event_type") or ""))
    required = {
        "user_turn",
        "surface_reply",
        "memory_recall",
        "memory_write",
        "bond_update",
        "inner_idle_tick",
        "self_model_update",
        "organ_surface_influence",
    }
    return all(required <= by_turn.get(index, set()) for index in range(1, turn_count + 1))


def _variation_threshold(turn_count: int) -> int:
    count = max(1, int(turn_count or 0))
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    return 3


def _latest_private_text(turn: Mapping[str, Any]) -> str:
    latest_private = dict(dict(turn.get("inner_world") or {}).get("latest_private_thought") or {})
    return str(latest_private.get("private_text") or latest_private.get("note") or "").strip()


def _sample(text: str, limit: int = 120) -> str:
    value = str(text or "").replace("\r", " ").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    drift = dict(details.get("drift") or {})
    lines = [
        "# Digital Life Soak Validation",
        "",
        "This is a long-run host-side contract and drift validation. It does not score generated text quality.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- min_turns: `{summary.get('min_turns')}`",
        f"- causality_turn_count: `{summary.get('causality_turn_count')}`",
        f"- drift_issue_count: `{summary.get('drift_issue_count')}`",
        f"- idle_loop_tick_count: `{summary.get('idle_loop_tick_count')}`",
        f"- idle_loop_simulated_hours: `{summary.get('idle_loop_simulated_hours')}`",
        f"- long_run_output_dir: `{summary.get('long_run_output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Drift", ""])
    if list(drift.get("issues") or []):
        for issue in list(drift.get("issues") or []):
            lines.append(f"- `{issue.get('kind')}` turn=`{issue.get('turn_index')}` detail=`{json.dumps(issue, ensure_ascii=False)}`")
    else:
        lines.append("- no drift issues detected")
    lines.extend(["", "## Causality Sample", ""])
    for trace in list(details.get("causality_traces") or [])[:8]:
        if not isinstance(trace, Mapping):
            continue
        reply = dict(trace.get("reply") or {})
        bond = dict(trace.get("bond") or {})
        emotion = dict(trace.get("emotion_vts") or {})
        lines.extend(
            [
                f"### Turn {trace.get('turn_index')} `{trace.get('case_id')}`",
                "",
                f"- organs: `{trace.get('active_organ_count')}` slots=`{','.join(str(item) for item in list(trace.get('control_delta_slots') or []))}`",
                f"- reply: {reply.get('text')}",
                f"- emotion/vts: user=`{emotion.get('user_emotion_tag')}` natsume=`{emotion.get('natsume_emotion_tag')}` vts=`{emotion.get('vts_expression_id')}`",
                f"- bond: status=`{bond.get('status')}` score_delta=`{bond.get('score_delta_from_previous_turn')}` stage=`{bond.get('stage')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "fixture").strip().lower()
    if normalized not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    return normalized


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a 30+ turn digital-life soak and drift validation.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--turns", type=int, default=32)
    parser.add_argument("--min-turns", type=int, default=30)
    parser.add_argument(
        "--allow-hash-embedding",
        action="store_true",
        help="Allow hash embedding fallback to pass the live service boundary preflight.",
    )
    parser.add_argument("--idle-ticks", type=int, default=8)
    parser.add_argument("--idle-tick-minutes", type=int, default=180)
    parser.add_argument("--idle-min-hours", type=float, default=24.0)
    parser.add_argument(
        "--skip-idle-validation",
        action="store_true",
        help="Skip the idle validation inside the underlying long-run contract.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_digital_life_soak_validation(args))
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
