from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.organ_ops.organ_contract_matrix import build_organ_contract_matrix  # noqa: E402
from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS  # noqa: E402
from tools.validate_full_digital_life_loop import (  # noqa: E402
    build_arg_parser as build_full_arg_parser,
    run_full_digital_life_loop_validation,
)
from tools.validate_organ_effect_ab import run_validation as run_effect_ab_validation  # noqa: E402
from tools.validate_organ_runtime_integration import run_validation as run_runtime_validation  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "organ_effectiveness"
EFFECT_TO_DOWNSTREAM_CHECKS: Mapping[str, tuple[str, ...]] = {
    "task": ("surface_context_packet_prepared_each_turn", "surface_selection_is_direct_no_score"),
    "emotion": ("emotion_tags_and_vts_present",),
    "relationship": ("bond_updates_applied",),
    "memory": ("memory_write_succeeded_each_turn", "memory_recall_uses_allowed_routes"),
    "inner_world": ("inner_world_generated_private_thoughts", "self_model_updated"),
    "initiative": ("reply_control_has_compressed_organ_influence",),
    "delivery": ("emotion_tags_and_vts_present",),
    "safety": (
        "surface_prompt_has_no_raw_organ_context",
        "raw_organ_text_not_leaked_to_reply",
        "no_surface_hard_gate_observations",
    ),
    "surface_text": ("mapped_surface_organs_present", "no_internal_markers_in_reply"),
}


def run_validation(
    *,
    mode: str = "fixture",
    turns: int = 4,
    runtime_iterations: int = 1,
    out_dir: Path | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    normalized_mode = str(mode or "fixture").strip().lower()
    if normalized_mode not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")

    full_result = asyncio.run(
        run_full_digital_life_loop_validation(
            build_full_arg_parser().parse_args(
                [
                    "--mode",
                    normalized_mode,
                    "--out-dir",
                    str(resolved_out / "full_loop"),
                    "--run-id",
                    str(run_id or f"organ-effectiveness-{int(time.time())}"),
                    "--max-turns",
                    str(max(1, int(turns or 1))),
                ]
            )
        )
    )
    runtime_result = run_runtime_validation(
        mode=normalized_mode,
        iterations=max(1, int(runtime_iterations or 1)),
        out_dir=resolved_out / "runtime_integration",
    )
    effect_ab_result = run_effect_ab_validation(out_dir=resolved_out / "effect_ab")

    full_summary = dict(full_result.get("summary") or {})
    full_details = dict(full_result.get("details") or {})
    runtime_summary = dict(runtime_result.get("summary") or {})
    runtime_details = dict(runtime_result.get("details") or {})
    effect_ab_summary = dict(effect_ab_result.get("summary") or {})
    effect_ab_details = dict(effect_ab_result.get("details") or {})

    rows = _effectiveness_rows(
        full_summary=full_summary,
        full_details=full_details,
        runtime_summary=runtime_summary,
        runtime_details=runtime_details,
        effect_ab_summary=effect_ab_summary,
        effect_ab_details=effect_ab_details,
    )
    checks = _checks(
        rows=rows,
        full_summary=full_summary,
        runtime_summary=runtime_summary,
        effect_ab_summary=effect_ab_summary,
    )
    category_counts = dict(sorted(_count_by(rows, "category").items()))
    summary = {
        "status": "PASS" if all(bool(check.get("passed")) for check in checks) else "FAIL",
        "evaluation_mode": "organ_effectiveness_contract_validation_no_text_quality_scoring",
        "mode": normalized_mode,
        "turn_count": int(full_summary.get("turn_count") or 0),
        "organ_count": len(rows),
        "category_counts": category_counts,
        "failed_count": sum(1 for check in checks if not bool(check.get("passed"))),
        "checks": {str(check.get("name")): bool(check.get("passed")) for check in checks},
        "output_dir": str(resolved_out),
        "full_loop_output_dir": str(full_summary.get("output_dir") or ""),
        "runtime_output_dir": str(runtime_summary.get("output_dir") or ""),
        "effect_ab_output_dir": str(effect_ab_summary.get("output_dir") or ""),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "rows": rows,
        "full_summary": full_summary,
        "runtime_summary": runtime_summary,
        "effect_ab_summary": effect_ab_summary,
    }
    _write_json(resolved_out / "summary.json", summary)
    _write_json(resolved_out / "details.json", details)
    _write_jsonl(resolved_out / "organ_effectiveness.jsonl", rows)
    (resolved_out / "organ_effectiveness_report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _effectiveness_rows(
    *,
    full_summary: Mapping[str, Any],
    full_details: Mapping[str, Any],
    runtime_summary: Mapping[str, Any],
    runtime_details: Mapping[str, Any],
    effect_ab_summary: Mapping[str, Any],
    effect_ab_details: Mapping[str, Any],
) -> list[dict[str, Any]]:
    matrix = {str(row["organ_id"]): dict(row) for row in build_organ_contract_matrix()}
    full_turns = [dict(turn) for turn in list(full_details.get("turns") or []) if isinstance(turn, Mapping)]
    turn_count = len(full_turns)
    full_stats = _full_loop_organ_stats(full_turns)
    runtime_stats = _runtime_organ_stats(runtime_details)
    ab_rows = {str(row.get("organ_id")): dict(row) for row in list(effect_ab_details.get("rows") or []) if isinstance(row, Mapping)}
    rows: list[dict[str, Any]] = []
    for organ_id in SURFACE_ORGAN_IDS:
        contract = matrix[organ_id]
        full = full_stats.get(organ_id, {})
        runtime = runtime_stats.get(organ_id, {})
        ab = ab_rows.get(organ_id, {})
        effects = [str(item) for item in list(contract.get("main_chain_effects") or [])]
        downstream = _downstream_status(effects=effects, full_summary=full_summary)
        activation_count = int(full.get("mapped_turn_count") or 0)
        compressed_count = int(runtime.get("contract_field_available_count") or 0)
        runtime_count = int(runtime.get("row_count") or 0)
        input_count = int(runtime.get("raw_state_collected_count") or 0)
        activation_rate = _rate(activation_count, turn_count)
        runtime_input_rate = _rate(input_count, runtime_count)
        compressed_rate = _rate(compressed_count, runtime_count)
        ab_reply_changed = bool(ab.get("reply_control_changed"))
        raw_boundary_ok = bool(ab.get("raw_context_not_leaked", True)) and not _runtime_raw_leak(runtime)
        category = _classify(
            activation_rate=activation_rate,
            runtime_input_rate=runtime_input_rate,
            compressed_rate=compressed_rate,
            ab_reply_changed=ab_reply_changed,
            downstream=downstream,
            raw_boundary_ok=raw_boundary_ok,
            ab=ab,
        )
        rows.append(
            {
                "organ_id": organ_id,
                "category": category,
                "activation_rate": activation_rate,
                "mapped_turn_count": activation_count,
                "turn_count": turn_count,
                "runtime_input_rate": runtime_input_rate,
                "runtime_contract_field_rate": compressed_rate,
                "runtime_row_count": runtime_count,
                "present_context_keys": sorted(set(runtime.get("present_context_keys") or [])),
                "available_contract_fields": sorted(set(runtime.get("available_contract_fields") or [])),
                "ab_reply_control_changed": ab_reply_changed,
                "ab_changed_reply_control_fields": list(ab.get("changed_reply_control_fields") or []),
                "ab_changed_contract_fields": list(ab.get("changed_contract_fields") or []),
                "ab_changed_surface_slots": list(ab.get("changed_surface_slots") or []),
                "declared_effects": effects,
                "downstream_consumers": downstream,
                "active_surface_slots": sorted(set(full.get("surface_slots") or [])),
                "raw_boundary_ok": raw_boundary_ok,
                "classification_reasons": _classification_reasons(
                    activation_rate=activation_rate,
                    runtime_input_rate=runtime_input_rate,
                    compressed_rate=compressed_rate,
                    ab_reply_changed=ab_reply_changed,
                    downstream=downstream,
                    raw_boundary_ok=raw_boundary_ok,
                    category=category,
                ),
                "role_summary": str(contract.get("role_summary") or ""),
                "runtime_validation_status": str(runtime_summary.get("status") or ""),
                "effect_ab_validation_status": str(effect_ab_summary.get("status") or ""),
            }
        )
    return rows


def _full_loop_organ_stats(turns: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {
        organ_id: {"mapped_turn_count": 0, "surface_slots": set()} for organ_id in SURFACE_ORGAN_IDS
    }
    for turn in turns:
        packet = dict((turn.get("surface_context_packet") or {}).get("surface_influence_packet") or {})
        trace = dict(packet.get("organ_trace") or {})
        mapped = {str(item) for item in list(trace.get("mapped_organs") or [])}
        slots = {str(item) for item in list(trace.get("slots") or [])}
        for organ_id in SURFACE_ORGAN_IDS:
            if organ_id in mapped:
                stats[organ_id]["mapped_turn_count"] += 1
                stats[organ_id]["surface_slots"].update(slots)
    return stats


def _runtime_organ_stats(details: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "row_count": 0,
            "raw_state_collected_count": 0,
            "contract_field_available_count": 0,
            "raw_context_leak_count": 0,
            "present_context_keys": set(),
            "available_contract_fields": set(),
        }
    )
    for key in ("fixture_rows", "live_rows"):
        for raw in list(details.get(key) or []):
            if not isinstance(raw, Mapping):
                continue
            row = dict(raw)
            organ_id = str(row.get("organ_id") or "")
            if organ_id not in SURFACE_ORGAN_IDS:
                continue
            item = stats[organ_id]
            item["row_count"] += 1
            if bool(row.get("raw_state_collected")) or bool(row.get("mapped_self")) or bool(row.get("mapped_organs")):
                item["raw_state_collected_count"] += 1
            if (
                bool(row.get("contract_field_available"))
                or bool(row.get("contract_effect_hits_allowed_fields"))
                or list(row.get("available_contract_fields") or [])
                or list(row.get("contract_fields") or [])
            ):
                item["contract_field_available_count"] += 1
            if not bool(row.get("raw_context_not_leaked", row.get("raw_context_dropped", True))):
                item["raw_context_leak_count"] += 1
            item["present_context_keys"].update(
                str(value) for value in list(row.get("present_context_keys") or row.get("context_keys") or [])
            )
            item["available_contract_fields"].update(
                str(value) for value in list(row.get("available_contract_fields") or row.get("contract_fields") or [])
            )
    return dict(stats)


def _downstream_status(*, effects: Sequence[str], full_summary: Mapping[str, Any]) -> dict[str, bool]:
    checks = dict(full_summary.get("checks") or {})
    status: dict[str, bool] = {}
    for effect in effects:
        required = EFFECT_TO_DOWNSTREAM_CHECKS.get(str(effect), ())
        if not required:
            continue
        status[str(effect)] = all(bool(checks.get(name)) for name in required)
    return status


def _classify(
    *,
    activation_rate: float,
    runtime_input_rate: float,
    compressed_rate: float,
    ab_reply_changed: bool,
    downstream: Mapping[str, bool],
    raw_boundary_ok: bool,
    ab: Mapping[str, Any],
) -> str:
    if (
        not raw_boundary_ok
        or not bool(ab.get("contract_changes_within_allowed_fields", True))
        or not bool(ab.get("surface_changes_within_allowed_slots", True))
        or not bool(ab.get("main_chain_effects_within_declared_effects", True))
    ):
        return "risk"
    if activation_rate <= 0.0 or runtime_input_rate <= 0.0:
        return "silent"
    if not ab_reply_changed or compressed_rate <= 0.0:
        return "decorative"
    if activation_rate < 0.50 or runtime_input_rate < 0.50 or compressed_rate < 0.50:
        return "weak"
    meaningful_downstream = [value for key, value in downstream.items() if key != "surface_text"]
    if meaningful_downstream and not any(meaningful_downstream):
        return "weak"
    return "active"


def _classification_reasons(
    *,
    activation_rate: float,
    runtime_input_rate: float,
    compressed_rate: float,
    ab_reply_changed: bool,
    downstream: Mapping[str, bool],
    raw_boundary_ok: bool,
    category: str,
) -> list[str]:
    reasons: list[str] = []
    if not raw_boundary_ok:
        reasons.append("raw boundary failed")
    if activation_rate <= 0:
        reasons.append("not mapped in full loop")
    elif activation_rate < 0.5:
        reasons.append("mapped in fewer than half of turns")
    if runtime_input_rate <= 0:
        reasons.append("runtime state not collected")
    elif runtime_input_rate < 0.5:
        reasons.append("runtime state collection is weak")
    if compressed_rate <= 0:
        reasons.append("no compressed contract fields observed")
    elif compressed_rate < 0.5:
        reasons.append("compressed field availability is weak")
    if not ab_reply_changed:
        reasons.append("single-organ AB did not change reply control")
    if downstream and not all(downstream.values()):
        missing = [key for key, value in downstream.items() if not value]
        reasons.append("downstream not confirmed: " + ",".join(missing))
    if not reasons and category == "active":
        reasons.append("mapped, compressed, reply-control observable, downstream confirmed")
    return reasons


def _checks(
    *,
    rows: Sequence[Mapping[str, Any]],
    full_summary: Mapping[str, Any],
    runtime_summary: Mapping[str, Any],
    effect_ab_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    row_list = [dict(row) for row in rows]
    categories = _count_by(row_list, "category")
    return [
        _check("full_loop_validation_passed", bool(full_summary.get("passed")), {"output_dir": full_summary.get("output_dir")}),
        _check("runtime_integration_validation_passed", str(runtime_summary.get("status") or "") == "PASS", {"output_dir": runtime_summary.get("output_dir")}),
        _check("effect_ab_validation_passed", str(effect_ab_summary.get("status") or "") == "PASS", {"output_dir": effect_ab_summary.get("output_dir")}),
        _check(
            "effectiveness_covers_23_organs",
            len(row_list) == 23 and {str(row.get("organ_id")) for row in row_list} == set(SURFACE_ORGAN_IDS),
            {"row_count": len(row_list)},
        ),
        _check(
            "all_organs_are_runtime_active",
            bool(row_list) and all(str(row.get("category")) == "active" for row in row_list),
            {"category_counts": dict(sorted(categories.items()))},
        ),
        _check(
            "no_silent_decorative_or_risk_organs",
            not any(str(row.get("category")) in {"silent", "decorative", "risk"} for row in row_list),
            {
                "failed": [
                    {"organ_id": row.get("organ_id"), "category": row.get("category"), "reasons": row.get("classification_reasons")}
                    for row in row_list
                    if str(row.get("category")) in {"silent", "decorative", "risk"}
                ]
            },
        ),
        _check(
            "each_organ_changes_reply_control_in_ab",
            all(bool(row.get("ab_reply_control_changed")) for row in row_list),
            {"failed": [row.get("organ_id") for row in row_list if not bool(row.get("ab_reply_control_changed"))]},
        ),
        _check(
            "each_organ_has_runtime_input_and_compressed_fields",
            all(float(row.get("runtime_input_rate") or 0.0) > 0 and float(row.get("runtime_contract_field_rate") or 0.0) > 0 for row in row_list),
            {
                "failed": [
                    row.get("organ_id")
                    for row in row_list
                    if not (float(row.get("runtime_input_rate") or 0.0) > 0 and float(row.get("runtime_contract_field_rate") or 0.0) > 0)
                ]
            },
        ),
        _check(
            "downstream_consumers_confirm_declared_effects",
            all(all(bool(value) for value in dict(row.get("downstream_consumers") or {}).values()) for row in row_list),
            {
                "failed": [
                    {
                        "organ_id": row.get("organ_id"),
                        "missing": [key for key, value in dict(row.get("downstream_consumers") or {}).items() if not value],
                    }
                    for row in row_list
                    if not all(bool(value) for value in dict(row.get("downstream_consumers") or {}).values())
                ]
            },
        ),
        _check(
            "raw_boundaries_remain_ok",
            all(bool(row.get("raw_boundary_ok")) for row in row_list),
            {"failed": [row.get("organ_id") for row in row_list if not bool(row.get("raw_boundary_ok"))]},
        ),
    ]


def _runtime_raw_leak(runtime: Mapping[str, Any]) -> bool:
    return int(runtime.get("raw_context_leak_count") or 0) > 0


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(max(0.0, min(1.0, float(numerator) / float(denominator))), 4)


def _count_by(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Organ Effectiveness Validation",
        "",
        "This validates whether the 23 host organs actually affect the compressed main chain. It is not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- organ_count: `{summary.get('organ_count')}`",
        f"- category_counts: `{json.dumps(summary.get('category_counts') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"- full_loop_output_dir: `{summary.get('full_loop_output_dir')}`",
        f"- runtime_output_dir: `{summary.get('runtime_output_dir')}`",
        f"- effect_ab_output_dir: `{summary.get('effect_ab_output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Organ Rows", ""])
    for row in list(details.get("rows") or []):
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "- `{organ}` category=`{category}` activate=`{activate}` input=`{input}` fields=`{fields}` reply=`{reply}` effects=`{effects}` reasons=`{reasons}`".format(
                organ=row.get("organ_id"),
                category=row.get("category"),
                activate=row.get("activation_rate"),
                input=row.get("runtime_input_rate"),
                fields=row.get("runtime_contract_field_rate"),
                reply=bool(row.get("ab_reply_control_changed")),
                effects=",".join(str(item) for item in list(row.get("declared_effects") or [])),
                reasons="; ".join(str(item) for item in list(row.get("classification_reasons") or [])),
            )
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate actual 23-organ effectiveness in the host main chain.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--runtime-iterations", type=int, default=1)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_validation(
        mode=args.mode,
        turns=args.turns,
        runtime_iterations=args.runtime_iterations,
        out_dir=args.out_dir,
        run_id=args.run_id,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
