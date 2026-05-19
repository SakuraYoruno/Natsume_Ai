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

from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS  # noqa: E402
from tools.validate_digital_life_background_runtime import (  # noqa: E402
    build_arg_parser as build_background_arg_parser,
    run_background_runtime_validation,
)
from tools.validate_digital_life_soak import (  # noqa: E402
    build_arg_parser as build_soak_arg_parser,
    run_digital_life_soak_validation,
)
from tools.validate_organ_effectiveness import run_validation as run_organ_effectiveness_validation  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "digital_life_closed_loop"
MAX_BOND_JUMP_PER_TURN = 25


def run_digital_life_closed_loop_validation(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = _normalize_mode(str(getattr(args, "mode", "fixture") or "fixture"))
    run_id = str(getattr(args, "run_id", "") or f"closed-loop-{int(time.time())}")

    soak_result = _load_or_run_soak(args=args, out_dir=out_dir, mode=mode, run_id=run_id)
    background_result = run_background_runtime_validation(
        build_background_arg_parser().parse_args(
            [
                "--mode",
                mode,
                "--out-dir",
                str(out_dir / "background_runtime"),
                "--run-id",
                f"{run_id}-background",
            ]
        )
    )
    effectiveness_result = run_organ_effectiveness_validation(
        mode=mode,
        turns=max(1, int(getattr(args, "effectiveness_turns", 4) or 4)),
        runtime_iterations=max(1, int(getattr(args, "runtime_iterations", 1) or 1)),
        out_dir=out_dir / "organ_effectiveness",
        run_id=f"{run_id}-effectiveness",
    )

    soak_summary = dict(soak_result.get("summary") or {})
    soak_details = dict(soak_result.get("details") or {})
    background_summary = dict(background_result.get("summary") or {})
    background_details = dict(background_result.get("details") or {})
    effectiveness_summary = dict(effectiveness_result.get("summary") or {})
    effectiveness_details = dict(effectiveness_result.get("details") or {})

    cycles = _closure_cycles(
        soak_summary=soak_summary,
        soak_details=soak_details,
        background_summary=background_summary,
        background_details=background_details,
        effectiveness_summary=effectiveness_summary,
        effectiveness_details=effectiveness_details,
    )
    checks = _checks(
        cycles=cycles,
        soak_summary=soak_summary,
        background_summary=background_summary,
        effectiveness_summary=effectiveness_summary,
    )
    failed_checks = [check for check in checks if not bool(check.get("passed"))]
    active_organs = _active_organ_ids(effectiveness_details)
    summary = {
        "status": "PASS" if not failed_checks else "FAIL",
        "evaluation_mode": "digital_life_closed_loop_contract_validation_no_text_quality_scoring",
        "mode": mode,
        "run_id": run_id,
        "turn_count": int(soak_summary.get("turn_count") or 0),
        "min_turns": int(soak_summary.get("min_turns") or 0),
        "causality_turn_count": int(soak_summary.get("causality_turn_count") or 0),
        "organ_count": len(SURFACE_ORGAN_IDS),
        "active_organ_count": len(active_organs),
        "cycle_count": len(cycles),
        "closed_cycle_count": sum(1 for cycle in cycles if bool(cycle.get("passed"))),
        "failed_count": len(failed_checks),
        "checks": {str(check.get("name")): bool(check.get("passed")) for check in checks},
        "output_dir": str(out_dir),
        "soak_output_dir": str(soak_summary.get("output_dir") or ""),
        "background_output_dir": str(background_summary.get("output_dir") or ""),
        "effectiveness_output_dir": str(effectiveness_summary.get("output_dir") or ""),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "cycles": cycles,
        "soak_summary": soak_summary,
        "background_summary": background_summary,
        "effectiveness_summary": effectiveness_summary,
        "active_organs": active_organs,
        "soak_source": "existing" if str(getattr(args, "soak_dir", "") or "").strip() else "fresh_run",
    }
    _write_json(out_dir / "closed_loop_summary.json", summary)
    _write_json(out_dir / "closed_loop_details.json", details)
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    _write_jsonl(out_dir / "closed_loop_cycles.jsonl", cycles)
    (out_dir / "closed_loop_report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _load_or_run_soak(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    run_id: str,
) -> dict[str, Any]:
    raw_soak_dir = str(getattr(args, "soak_dir", "") or "").strip()
    if raw_soak_dir:
        soak_dir = Path(raw_soak_dir)
        return {
            "summary": _read_json(soak_dir / "soak_summary.json"),
            "details": _read_json(soak_dir / "soak_details.json"),
        }

    soak_argv = [
        "--mode",
        mode,
        "--out-dir",
        str(out_dir / "soak"),
        "--run-id",
        f"{run_id}-soak",
        "--turns",
        str(max(1, int(getattr(args, "turns", 32) or 32))),
        "--min-turns",
        str(max(1, int(getattr(args, "min_turns", 30) or 30))),
        "--idle-ticks",
        str(max(1, int(getattr(args, "idle_ticks", 8) or 8))),
        "--idle-tick-minutes",
        str(max(5, int(getattr(args, "idle_tick_minutes", 180) or 180))),
        "--idle-min-hours",
        str(max(0.0, float(getattr(args, "idle_min_hours", 24.0) or 24.0))),
    ]
    if bool(getattr(args, "allow_hash_embedding", False)):
        soak_argv.append("--allow-hash-embedding")
    return asyncio.run(run_digital_life_soak_validation(build_soak_arg_parser().parse_args(soak_argv)))


def _closure_cycles(
    *,
    soak_summary: Mapping[str, Any],
    soak_details: Mapping[str, Any],
    background_summary: Mapping[str, Any],
    background_details: Mapping[str, Any],
    effectiveness_summary: Mapping[str, Any],
    effectiveness_details: Mapping[str, Any],
) -> list[dict[str, Any]]:
    traces = [dict(trace) for trace in list(soak_details.get("causality_traces") or []) if isinstance(trace, Mapping)]
    soak_checks = dict(soak_summary.get("checks") or {})
    background_checks = dict(background_summary.get("checks") or {})
    effectiveness_checks = dict(effectiveness_summary.get("checks") or {})
    effectiveness_rows = [dict(row) for row in list(effectiveness_details.get("rows") or []) if isinstance(row, Mapping)]
    bond_scores = [int(dict(trace.get("bond") or {}).get("total_score") or 0) for trace in traces]
    bond_deltas = [current - previous for previous, current in zip(bond_scores, bond_scores[1:])]
    self_updates = [
        int(dict(trace.get("self_model") or {}).get("self_update_count") or 0)
        for trace in traces
    ]
    user_emotions = sorted(
        {
            str(dict(trace.get("emotion_vts") or {}).get("user_emotion_tag") or "")
            for trace in traces
            if str(dict(trace.get("emotion_vts") or {}).get("user_emotion_tag") or "")
        }
    )
    natsume_emotions = sorted(
        {
            str(dict(trace.get("emotion_vts") or {}).get("natsume_emotion_tag") or "")
            for trace in traces
            if str(dict(trace.get("emotion_vts") or {}).get("natsume_emotion_tag") or "")
        }
    )
    recall_fact_counts = [int(dict(trace.get("memory") or {}).get("recall_fact_count") or 0) for trace in traces]
    recall_route_ids = [
        str(route)
        for trace in traces
        for route in list(dict(trace.get("memory") or {}).get("route_ids") or [])
        if route
    ]
    private_focuses = sorted(
        {
            str(dict(trace.get("inner_world") or {}).get("focus") or "")
            for trace in traces
            if str(dict(trace.get("inner_world") or {}).get("focus") or "")
        }
    )
    control_slots = sorted(
        {
            str(slot)
            for trace in traces
            for slot in list(trace.get("control_delta_slots") or [])
            if slot
        }
    )
    background_trigger_results = dict(background_details.get("trigger_results") or {})
    active_organs = _active_organ_ids(effectiveness_details)
    active_categories = dict(effectiveness_summary.get("category_counts") or {})
    cycles = [
        _cycle(
            "organ_surface_reply_state_writeback",
            bool(traces)
            and all(int(trace.get("active_organ_count") or 0) >= 23 for trace in traces)
            and all(list(trace.get("control_delta_slots") or []) for trace in traces)
            and all(str(dict(trace.get("reply") or {}).get("text") or "").strip() for trace in traces)
            and all(bool(dict(trace.get("memory") or {}).get("write_success")) for trace in traces)
            and all(str(dict(trace.get("bond") or {}).get("status") or "") == "APPLIED" for trace in traces),
            {
                "turn_count": len(traces),
                "control_slots": control_slots,
                "soak_checks": {
                    key: soak_checks.get(key)
                    for key in (
                        "soak_all_turns_map_23_organs",
                        "soak_memory_writes_each_turn",
                        "soak_bond_updates_each_turn",
                    )
                },
            },
        ),
        _cycle(
            "emotion_to_vts_to_bond_feedback",
            bool(traces)
            and all(dict(trace.get("emotion_vts") or {}).get("vts_expression_id") for trace in traces)
            and len(user_emotions) >= _variation_threshold(len(traces))
            and len(natsume_emotions) >= _variation_threshold(len(traces))
            and bool(bond_deltas)
            and max(abs(delta) for delta in bond_deltas) <= MAX_BOND_JUMP_PER_TURN
            and max(bond_scores) > min(bond_scores),
            {
                "user_emotion_tags": user_emotions,
                "natsume_emotion_tags": natsume_emotions,
                "bond_scores": bond_scores,
                "bond_deltas": bond_deltas,
            },
        ),
        _cycle(
            "memory_write_to_later_recall_feedback",
            bool(traces)
            and all(bool(dict(trace.get("memory") or {}).get("write_success")) for trace in traces)
            and max(recall_fact_counts or [0]) > 0
            and bool(recall_route_ids),
            {
                "recall_fact_counts": recall_fact_counts,
                "recall_route_count": len(recall_route_ids),
                "sample_route_ids": sorted(set(recall_route_ids))[:8],
            },
        ),
        _cycle(
            "idle_private_thought_to_self_model_feedback",
            bool(traces)
            and all(bool(dict(trace.get("inner_world") or {}).get("private_text_present")) for trace in traces)
            and bool(self_updates)
            and self_updates[-1] >= len(traces)
            and all(current >= previous for previous, current in zip(self_updates, self_updates[1:])),
            {
                "private_focuses": private_focuses,
                "self_update_counts": self_updates,
                "background_private_thought_count": background_summary.get("private_thought_count"),
                "background_self_update_count": background_summary.get("self_update_count"),
            },
        ),
        _cycle(
            "background_core_tick_runtime_feedback",
            bool(background_summary.get("passed"))
            and bool(background_checks.get("continuous_core_ticks_enabled"))
            and bool(background_checks.get("manual_core_ticks_executed"))
            and bool(background_checks.get("background_runtime_is_advancing"))
            and bool(background_checks.get("default_mode_inline_tick_ran"))
            and bool(background_checks.get("self_model_updated_by_background_tick")),
            {
                "core_task_count": background_summary.get("core_task_count"),
                "registered_task_count": background_summary.get("registered_task_count"),
                "triggered_tasks": sorted(background_trigger_results),
                "tick_health": background_summary.get("tick_health"),
            },
        ),
        _cycle(
            "twenty_three_organ_effectiveness_feedback",
            str(effectiveness_summary.get("status") or "") == "PASS"
            and len(active_organs) == len(SURFACE_ORGAN_IDS)
            and bool(effectiveness_checks.get("all_organs_are_runtime_active"))
            and bool(effectiveness_checks.get("downstream_consumers_confirm_declared_effects")),
            {
                "active_organ_count": len(active_organs),
                "category_counts": active_categories,
                "failed_organs": [
                    row.get("organ_id")
                    for row in effectiveness_rows
                    if str(row.get("category") or "") != "active"
                ],
            },
        ),
        _cycle(
            "boundary_and_identity_feedback_guard",
            bool(soak_checks.get("soak_no_identity_drift"))
            and bool(soak_checks.get("soak_no_internal_or_private_leak"))
            and int(soak_summary.get("drift_issue_count") or 0) == 0,
            {
                "drift_issue_count": soak_summary.get("drift_issue_count"),
                "failed_count": soak_summary.get("failed_count"),
            },
        ),
    ]
    return cycles


def _checks(
    *,
    cycles: Sequence[Mapping[str, Any]],
    soak_summary: Mapping[str, Any],
    background_summary: Mapping[str, Any],
    effectiveness_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    cycle_list = [dict(cycle) for cycle in cycles]
    return [
        _check("soak_validation_passed", str(soak_summary.get("status") or "") == "PASS", {"output_dir": soak_summary.get("output_dir")}),
        _check(
            "soak_reaches_continuous_runtime_minimum",
            int(soak_summary.get("turn_count") or 0) >= int(soak_summary.get("min_turns") or 0) >= 1,
            {"turn_count": soak_summary.get("turn_count"), "min_turns": soak_summary.get("min_turns")},
        ),
        _check(
            "background_runtime_validation_passed",
            bool(background_summary.get("passed")),
            {"output_dir": background_summary.get("output_dir")},
        ),
        _check(
            "organ_effectiveness_validation_passed",
            str(effectiveness_summary.get("status") or "") == "PASS",
            {"output_dir": effectiveness_summary.get("output_dir")},
        ),
        _check(
            "all_declared_closure_cycles_pass",
            bool(cycle_list) and all(bool(cycle.get("passed")) for cycle in cycle_list),
            {"failed": [cycle.get("name") for cycle in cycle_list if not bool(cycle.get("passed"))]},
        ),
        _check(
            "closed_loop_covers_23_organs",
            int(effectiveness_summary.get("organ_count") or 0) == len(SURFACE_ORGAN_IDS)
            and dict(effectiveness_summary.get("category_counts") or {}).get("active") == len(SURFACE_ORGAN_IDS),
            {
                "organ_count": effectiveness_summary.get("organ_count"),
                "category_counts": effectiveness_summary.get("category_counts"),
            },
        ),
    ]


def _active_organ_ids(effectiveness_details: Mapping[str, Any]) -> list[str]:
    return sorted(
        str(row.get("organ_id") or "")
        for row in list(effectiveness_details.get("rows") or [])
        if isinstance(row, Mapping) and str(row.get("category") or "") == "active"
    )


def _cycle(name: str, passed: bool, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": dict(evidence or {})}


def _variation_threshold(turn_count: int) -> int:
    count = max(1, int(turn_count or 0))
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    return 3


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Digital Life Closed Loop Validation",
        "",
        "This validates host-side closed-loop runtime behavior. It is not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- causality_turn_count: `{summary.get('causality_turn_count')}`",
        f"- active_organ_count: `{summary.get('active_organ_count')}` / `{summary.get('organ_count')}`",
        f"- closed_cycle_count: `{summary.get('closed_cycle_count')}` / `{summary.get('cycle_count')}`",
        f"- failed_count: `{summary.get('failed_count')}`",
        f"- soak_output_dir: `{summary.get('soak_output_dir')}`",
        f"- background_output_dir: `{summary.get('background_output_dir')}`",
        f"- effectiveness_output_dir: `{summary.get('effectiveness_output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Closure Cycles", ""])
    for cycle in list(details.get("cycles") or []):
        if not isinstance(cycle, Mapping):
            continue
        marker = "PASS" if cycle.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {cycle.get('name')}: `{json.dumps(cycle.get('evidence') or {}, ensure_ascii=False)}`")
    lines.extend(
        [
            "",
            "## Active Organs",
            "",
            "```text",
            "\n".join(str(item) for item in list(details.get("active_organs") or [])),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(payload or {}) if isinstance(payload, Mapping) else {}


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
    parser = argparse.ArgumentParser(description="Validate host-side digital-life closed-loop runtime behavior.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--turns", type=int, default=32)
    parser.add_argument("--min-turns", type=int, default=30)
    parser.add_argument("--idle-ticks", type=int, default=8)
    parser.add_argument("--idle-tick-minutes", type=int, default=180)
    parser.add_argument("--idle-min-hours", type=float, default=24.0)
    parser.add_argument("--effectiveness-turns", type=int, default=4)
    parser.add_argument("--runtime-iterations", type=int, default=1)
    parser.add_argument("--soak-dir", default="")
    parser.add_argument(
        "--allow-hash-embedding",
        action="store_true",
        help="Allow hash embedding fallback in nested live soak validation.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_digital_life_closed_loop_validation(args)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
