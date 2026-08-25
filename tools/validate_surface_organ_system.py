from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.organ_ops.organ_registry import build_default_organ_registry  # noqa: E402
from host_brain.persona.surface_influence_packet import build_surface_influence_packet  # noqa: E402
from host_brain.persona.surface_organ_influence import (  # noqa: E402
    SURFACE_ORGAN_IDS,
    build_surface_organ_influence_overlay,
    organ_surface_rule_table,
)
from host_brain.persona.surface_packet_compiler import compile_reply_control_packet  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "surface_organ_system_validation"
RAW_LEAK_MARKER = "RAW ORGAN TEXT SHOULD NOT PASS"


def build_full_surface_organ_probe_context() -> dict[str, Any]:
    """Synthetic context that exercises every surface-facing organ rule."""

    return {
        "tribe_context_source": "validation_cortex_probe",
        "tribe_route": {"route": "chat", "reason": "validation", "confidence": 0.91},
        "cortex_state": {"context_collector_active": True},
        "thalamus_state": {"route": "chat", "route_source": "validation.rule"},
        "prefrontal_state": {"drift_risk": 0.12, "plan_stability": 0.82},
        "vital_signs": {
            "stress_index": 0.82,
            "cognitive_load_index": 0.79,
            "sleep_pressure": 0.81,
            "immune_risk_level": "high",
            "loneliness_level": 0.66,
            "mirror_resonance_strength": 0.58,
            "pain_level": 0.42,
            "silence_willingness": 0.32,
        },
        "emotion_context": {"emotion_tag": "ANGRY", "emotion_intensity": 0.62},
        "voice_emotion": {"emotion_tag": "ANGRY", "emotion_intensity": 0.55},
        "bond_context": {
            "stage": "CONFIDANT",
            "stage_label": "亲密恋人",
            "relationship_substage": "stable_lovers",
            "bond_vector": {"intimacy": 0.86, "trust": 0.82, "playfulness": 0.52},
        },
        "bond_vector": {"intimacy": 0.86, "trust": 0.82, "playfulness": 0.52},
        "social_brain_state": {
            "bond_stage": "CONFIDANT",
            "relationship_playfulness": 0.56,
            "relationship_conflict_rift": 0.12,
        },
        "endocrine_gate": {"stress_index": 0.78, "cortisol_level": 0.7},
        "immune_state": {"risk_level": "high", "last_decision": "allow_guarded"},
        "hedonic_state": {"pain_level": 0.42, "reward_level": 0.36, "loneliness_level": 0.66},
        "pain_reward_state": {"pain_level": 0.42, "reward_level": 0.36},
        "somatic_state": {"fatigue_level": 0.72},
        "interoception_state": {"fatigue": 0.76, "cognitive_load_index": 0.74},
        "circadian_state": {"sleep_pressure": 0.83, "phase": "late"},
        "glymphatic_state": {"recovery_pressure": 0.62},
        "proprioception_state": {"cognitive_load_index": 0.79},
        "initiative_decision": {"decision": "defer"},
        "initiative_governor_state": {"status": "deferred"},
        "default_mode_state": {"continuity_score": 0.64, "care_drive": 0.52},
        "mirror_neuron_state": {"resonance_strength": 0.58},
        "memory_layers": {"recent_summary_present": True, "episodic_available": True},
        "language_dual_system": {"response_mode": "chat_text", "vts_expression_id": "HK_Sad"},
        "language_style": {"response_mode": "chat_text"},
        "motor_cortex_state": {"last_action_plan": "validation_only"},
        "cerebellum_state": {"jitter_level": 0.21},
        "vestibular_state": {"route_bias": "conservative"},
        "peripheral_state": {"output_channel": "chat_text"},
        "raw_organ_outputs": {"limbic": {"messages": [RAW_LEAK_MARKER]}},
    }


def build_calm_surface_organ_probe_context() -> dict[str, Any]:
    return {
        "tribe_context_source": "validation_calm_probe",
        "vital_signs": {
            "stress_index": 0.12,
            "cognitive_load_index": 0.18,
            "sleep_pressure": 0.12,
            "immune_risk_level": "low",
            "silence_willingness": 0.82,
        },
        "emotion_context": {"emotion_tag": "CALM", "emotion_intensity": 0.24},
        "bond_context": {"stage": "FRIEND", "bond_vector": {"intimacy": 0.42, "trust": 0.62}},
        "social_brain_state": {"bond_stage": "FRIEND", "relationship_playfulness": 0.48},
        "language_dual_system": {"response_mode": "chat_text"},
    }


def run_validation(*, iterations: int = 30, include_live: bool = True, out_dir: Path | None = None) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    live_rows: list[dict[str, Any]] = []

    checks.extend(_offline_checks())
    if include_live:
        live_rows = _live_context_stability_rows(iterations=max(1, int(iterations or 1)))
        checks.extend(_live_checks(live_rows))

    failed = [item for item in checks if not bool(item.get("passed"))]
    summary = {
        "status": "PASS" if not failed else "FAIL",
        "check_count": len(checks),
        "failed_count": len(failed),
        "iterations": int(iterations or 0) if include_live else 0,
        "include_live": bool(include_live),
        "organ_count": len(SURFACE_ORGAN_IDS),
        "output_dir": str(resolved_out),
        "checks_jsonl": str(resolved_out / "checks.jsonl"),
        "live_context_jsonl": str(resolved_out / "live_context.jsonl"),
        "report": str(resolved_out / "report.md"),
    }
    _write_json(resolved_out / "summary.json", summary)
    _write_jsonl(resolved_out / "checks.jsonl", checks)
    if live_rows:
        _write_jsonl(resolved_out / "live_context.jsonl", live_rows)
    _write_report(resolved_out / "report.md", summary=summary, checks=checks, live_rows=live_rows)
    return summary


def _offline_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    registry_ids = {item["organ_id"] for item in build_default_organ_registry().list_organs()}
    rule_rows = organ_surface_rule_table()
    rule_ids = {row["organ_id"] for row in rule_rows}
    expected_ids = set(SURFACE_ORGAN_IDS)

    _add_check(
        checks,
        "registry_has_23_organs",
        registry_ids == expected_ids,
        {"registry_count": len(registry_ids), "missing": sorted(expected_ids - registry_ids), "extra": sorted(registry_ids - expected_ids)},
    )
    _add_check(
        checks,
        "surface_rule_table_covers_23_organs",
        rule_ids == expected_ids and all(row.get("allowed_slots") for row in rule_rows),
        {"rule_count": len(rule_ids), "missing": sorted(expected_ids - rule_ids), "extra": sorted(rule_ids - expected_ids)},
    )
    _add_check(
        checks,
        "surface_rules_are_compressed_only",
        all(row.get("prompt_visibility") == "compressed_only" for row in rule_rows),
        {"non_compressed": [row for row in rule_rows if row.get("prompt_visibility") != "compressed_only"]},
    )

    full_context = build_full_surface_organ_probe_context()
    full_overlay = build_surface_organ_influence_overlay(full_context)
    full_packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn", "question_budget": 1},
        organ_context=full_context,
    )
    compiled = compile_reply_control_packet(
        user_message="今天有点累",
        task_control={"task_mode": "daily_chat", "response_move": "reply_current_turn", "question_budget": 1},
        influence_packet=full_packet.as_dict(),
    )
    full_rendered = json.dumps({"packet": full_packet.as_dict(), "reply_control": compiled.packet.model_dump()}, ensure_ascii=False)
    mapped = set(full_overlay.trace.get("mapped_organs") or [])

    _add_check(
        checks,
        "synthetic_full_context_maps_23_organs",
        mapped == expected_ids,
        {"mapped_count": len(mapped), "missing": sorted(expected_ids - mapped), "mapped": sorted(mapped)},
    )
    _add_check(
        checks,
        "raw_organ_text_is_dropped",
        RAW_LEAK_MARKER not in full_rendered and int(full_overlay.trace.get("dropped_raw_field_count") or 0) >= 1,
        {"dropped_raw_field_count": full_overlay.trace.get("dropped_raw_field_count")},
    )
    _add_check(
        checks,
        "high_load_organs_shorten_and_stop_questions",
        full_packet.task.information_budget == "very_short" and full_packet.task.question_budget == 0,
        {
            "information_budget": full_packet.task.information_budget,
            "question_budget": full_packet.task.question_budget,
            "high_load": full_packet.organ_trace.get("high_load"),
        },
    )
    _add_check(
        checks,
        "limbic_endocrine_immune_drive_soft_containment",
        full_packet.affect.emotional_tone == "soft_containment" and full_packet.organ_trace.get("risk_level") == "high",
        {"emotional_tone": full_packet.affect.emotional_tone, "risk_level": full_packet.organ_trace.get("risk_level")},
    )
    _add_check(
        checks,
        "social_brain_bond_changes_relationship_surface",
        full_packet.relationship.relationship_phase == "lover"
        and full_packet.relationship.relationship_distance == "close"
        and full_packet.relationship.intimacy_ceiling >= 0.7,
        {
            "phase": full_packet.relationship.relationship_phase,
            "distance": full_packet.relationship.relationship_distance,
            "intimacy_ceiling": full_packet.relationship.intimacy_ceiling,
        },
    )
    _add_check(
        checks,
        "delivery_and_safety_affect_reply_control_packet",
        any("器官状态" in item for item in compiled.packet.must_not_do)
        and any("输出面" in item for item in compiled.packet.style_anchor),
        {
            "must_not_do": list(compiled.packet.must_not_do),
            "style_anchor": list(compiled.packet.style_anchor),
        },
    )
    _add_check(
        checks,
        "reply_control_packet_stays_bounded",
        len(compiled.packet.facts) <= 8 and len(compiled.packet.style_anchor) <= 10 and len(compiled.packet.must_not_do) <= 12,
        {
            "facts": len(compiled.packet.facts),
            "style_anchor": len(compiled.packet.style_anchor),
            "must_not_do": len(compiled.packet.must_not_do),
        },
    )

    calm_context = build_calm_surface_organ_probe_context()
    calm_packet = build_surface_influence_packet(
        reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
        organ_context=calm_context,
    )
    _add_check(
        checks,
        "organ_effects_change_with_state_not_constant",
        full_packet.continuity.should_be_brief
        and not calm_packet.continuity.should_be_brief
        and full_packet.affect.withdraw_bias > calm_packet.affect.withdraw_bias,
        {
            "high_should_be_brief": full_packet.continuity.should_be_brief,
            "calm_should_be_brief": calm_packet.continuity.should_be_brief,
            "high_withdraw": full_packet.affect.withdraw_bias,
            "calm_withdraw": calm_packet.affect.withdraw_bias,
        },
    )

    started = time.perf_counter()
    stability_errors = 0
    max_packet_chars = 0
    for _index in range(200):
        try:
            packet = build_surface_influence_packet(
                reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
                organ_context=full_context,
            )
            max_packet_chars = max(max_packet_chars, len(json.dumps(packet.as_dict(), ensure_ascii=False)))
        except Exception:
            stability_errors += 1
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _add_check(
        checks,
        "offline_compression_stress_200_cycles",
        stability_errors == 0 and elapsed_ms < 5_000 and max_packet_chars < 8_000,
        {"errors": stability_errors, "elapsed_ms": elapsed_ms, "max_packet_chars": max_packet_chars},
    )
    return checks


def _live_context_stability_rows(*, iterations: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(iterations):
        started = time.perf_counter()
        try:
            from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

            request_id = f"surface-organ-validation-{index + 1:03d}"
            context = get_cortex_agent().collect_tribe_context(
                request_body={
                    "request_id": request_id,
                    "person_id": "surface-organ-validation-user",
                    "session_id": "surface-organ-validation-session",
                    "user_message": "验证器官族系统稳定性",
                },
                session_id="surface-organ-validation-session",
                user_id="surface-organ-validation-user",
            )
            overlay = build_surface_organ_influence_overlay(context if isinstance(context, Mapping) else {})
            packet = build_surface_influence_packet(
                reply_plan={"primary_topic": "daily_chat", "interaction_move": "reply_current_turn"},
                organ_context=context if isinstance(context, Mapping) else {},
            )
            rendered = json.dumps(packet.as_dict(), ensure_ascii=False)
            mapped = set(overlay.trace.get("mapped_organs") or [])
            rows.append(
                {
                    "index": index + 1,
                    "ok": True,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "key_count": len(context) if isinstance(context, Mapping) else 0,
                    "mapped_organ_count": len(mapped),
                    "missing_organs": [organ for organ in SURFACE_ORGAN_IDS if organ not in mapped],
                    "slots": list(overlay.trace.get("slots") or []),
                    "risk_level": overlay.trace.get("risk_level"),
                    "high_load": overlay.trace.get("high_load"),
                    "packet_chars": len(rendered),
                    "raw_leak": RAW_LEAK_MARKER in rendered or "raw_organ_outputs" in rendered,
                    "error": "",
                }
            )
        except Exception as exc:  # pragma: no cover - diagnostic path
            rows.append(
                {
                    "index": index + 1,
                    "ok": False,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error": repr(exc),
                }
            )
    return rows


def _live_checks(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    ok_rows = [dict(row) for row in rows if bool(row.get("ok"))]
    latencies = [int(row.get("latency_ms") or 0) for row in ok_rows]
    mapped_counts = [int(row.get("mapped_organ_count") or 0) for row in ok_rows]
    slot_sets = [set(row.get("slots") or []) for row in ok_rows]
    error_rows = [dict(row) for row in rows if not bool(row.get("ok"))]
    raw_leaks = [dict(row) for row in ok_rows if bool(row.get("raw_leak"))]

    _add_check(
        checks,
        "live_cortex_context_collects_without_errors",
        len(error_rows) == 0 and len(ok_rows) == len(rows),
        {"iterations": len(rows), "error_count": len(error_rows), "errors": error_rows[:3]},
    )
    _add_check(
        checks,
        "live_context_maps_surface_relevant_organs",
        bool(mapped_counts) and min(mapped_counts) >= 21,
        {
            "min_mapped": min(mapped_counts) if mapped_counts else 0,
            "max_mapped": max(mapped_counts) if mapped_counts else 0,
            "missing_sets": sorted({",".join(row.get("missing_organs") or []) for row in ok_rows}),
        },
    )
    required_slots = {"affect", "relationship", "drive", "continuity", "safety", "delivery"}
    _add_check(
        checks,
        "live_context_exposes_expected_surface_slots",
        bool(slot_sets) and all(required_slots.issubset(slots) for slots in slot_sets),
        {"required_slots": sorted(required_slots), "slot_sets": sorted({",".join(sorted(slots)) for slots in slot_sets})},
    )
    _add_check(
        checks,
        "live_context_has_no_raw_prompt_leak",
        not raw_leaks,
        {"raw_leak_count": len(raw_leaks)},
    )
    _add_check(
        checks,
        "live_context_latency_is_stable",
        bool(latencies) and max(latencies) < 2_000,
        {
            "min_ms": min(latencies) if latencies else 0,
            "max_ms": max(latencies) if latencies else 0,
            "avg_ms": round(statistics.mean(latencies), 2) if latencies else 0,
        },
    )
    return checks


def _add_check(checks: list[dict[str, Any]], name: str, passed: bool, details: Mapping[str, Any] | None = None) -> None:
    checks.append({"name": name, "passed": bool(passed), "details": dict(details or {})})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _write_report(
    path: Path,
    *,
    summary: Mapping[str, Any],
    checks: Sequence[Mapping[str, Any]],
    live_rows: Sequence[Mapping[str, Any]],
) -> None:
    lines = [
        "# Surface Organ System Validation",
        "",
        "No text quality scoring is used. This validates organ collection, compression, bounded packet effects, and raw-context isolation.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- checks: `{summary.get('check_count')}`",
        f"- failed: `{summary.get('failed_count')}`",
        f"- iterations: `{summary.get('iterations')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    if live_rows:
        ok_rows = [row for row in live_rows if row.get("ok")]
        mapped_counts = [int(row.get("mapped_organ_count") or 0) for row in ok_rows]
        latencies = [int(row.get("latency_ms") or 0) for row in ok_rows]
        missing_sets = sorted({",".join(row.get("missing_organs") or []) for row in ok_rows})
        lines.extend(
            [
                "",
                "## Live Context",
                "",
                f"- ok_rows: `{len(ok_rows)}/{len(live_rows)}`",
                f"- mapped_range: `{min(mapped_counts) if mapped_counts else 0}-{max(mapped_counts) if mapped_counts else 0}`",
                f"- missing_sets: `{'; '.join(missing_sets) or 'none'}`",
                f"- latency_ms: `min={min(latencies) if latencies else 0}, max={max(latencies) if latencies else 0}, avg={round(statistics.mean(latencies), 2) if latencies else 0}`",
            ]
        )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate surface-facing organ system stability and effects.")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--no-live", action="store_true", help="Skip live CortexAgent context collection.")
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = run_validation(iterations=args.iterations, include_live=not args.no_live, out_dir=args.out_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
