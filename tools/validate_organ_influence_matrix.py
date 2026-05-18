from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persona.surface_influence_packet import build_surface_influence_packet  # noqa: E402
from host_brain.persona.surface_organ_influence import (  # noqa: E402
    SURFACE_ORGAN_IDS,
    build_surface_organ_influence_overlay,
    organ_surface_rule_table,
)
from host_brain.persona.surface_packet_compiler import compile_reply_control_packet  # noqa: E402
from tools.validate_surface_organ_system import (  # noqa: E402
    RAW_LEAK_MARKER,
    build_full_surface_organ_probe_context,
)


DEFAULT_OUT = ROOT / "artifacts" / "organ_influence_matrix"
PACKET_SLOTS = ("task", "affect", "relationship", "memory", "continuity", "safety", "delivery")
RAW_MARKERS = (
    RAW_LEAK_MARKER,
    "raw_organ_outputs",
    "organ_outputs",
    "natural_language_advice",
    "module_advice",
    "agent_advice",
    "tool_candidates",
    "uncompressed_rag_documents",
)
ALLOWED_FINAL_SLOTS = {
    "task": {"task"},
    "affect": {"affect"},
    "relationship": {"relationship"},
    "memory": {"memory", "continuity", "task"},
    "continuity": {"continuity", "task"},
    "safety": {"safety"},
    "delivery": {"delivery"},
    "drive": {"affect", "relationship", "continuity"},
}


def run_validation(*, out_dir: Path | None = None) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    rows = [_organ_influence_row(organ_id) for organ_id in SURFACE_ORGAN_IDS]
    full_probe = _full_probe_row()
    checks = _checks(rows=rows, full_probe=full_probe)
    summary = {
        "status": "PASS" if all(bool(item["passed"]) for item in checks) else "FAIL",
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "organ_count": len(SURFACE_ORGAN_IDS),
        "effective_organ_count": sum(1 for row in rows if bool(row.get("has_final_packet_effect"))),
        "failed_count": sum(1 for item in checks if not bool(item["passed"])),
        "checks": {str(item["name"]): bool(item["passed"]) for item in checks},
        "output_dir": str(resolved_out),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "full_probe": full_probe,
        "rows": rows,
    }
    _write_json(resolved_out / "summary.json", summary)
    _write_json(resolved_out / "details.json", details)
    _write_jsonl(resolved_out / "influence_matrix.jsonl", rows)
    (resolved_out / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _organ_influence_row(organ_id: str) -> dict[str, Any]:
    rule = _rule_by_organ()[organ_id]
    context = _fixture_for_organ(organ_id)
    context["raw_organ_outputs"] = {organ_id: {"messages": [RAW_LEAK_MARKER]}}
    baseline = _packet_payload({})
    packet = _packet_payload(context)
    overlay = build_surface_organ_influence_overlay(context)
    compiled = compile_reply_control_packet(
        user_message="验证当前器官是否实际影响主链。",
        task_control={
            "task_mode": "daily_chat",
            "response_move": "reply_current_turn",
            "question_budget": 1,
        },
        influence_packet=packet,
    )
    rendered = json.dumps(
        {
            "overlay": overlay.as_dict(),
            "packet": packet,
            "reply_control": compiled.packet.model_dump(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    changed_slots = [
        slot
        for slot in PACKET_SLOTS
        if packet.get(slot) != baseline.get(slot)
    ]
    allowed_final_slots = _allowed_final_slots(rule["allowed_slots"])
    effect_hits_allowed_contract = bool(set(changed_slots) & allowed_final_slots)
    mapped_organs = list(dict(packet.get("organ_trace") or {}).get("mapped_organs") or [])
    return {
        "organ_id": organ_id,
        "allowed_slots": list(rule["allowed_slots"]),
        "allowed_final_slots": sorted(allowed_final_slots),
        "mapped_organs": mapped_organs,
        "mapped_self": organ_id in mapped_organs,
        "overlay_slots": list(dict(packet.get("organ_trace") or {}).get("slots") or []),
        "changed_slots": changed_slots,
        "has_final_packet_effect": bool(changed_slots),
        "effect_hits_allowed_contract": effect_hits_allowed_contract,
        "raw_context_dropped": RAW_LEAK_MARKER not in rendered
        and int(dict(packet.get("organ_trace") or {}).get("dropped_raw_field_count") or 0) >= 1,
        "packet_chars": len(rendered),
        "packet_extract": _packet_extract(packet, compiled.packet.model_dump()),
    }


def _packet_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    return build_surface_influence_packet(
        reply_plan={
            "task_mode": "daily_chat",
            "primary_topic": "daily_chat",
            "response_move": "reply_current_turn",
            "interaction_move": "reply_current_turn",
            "question_budget": 1,
        },
        organ_context=context,
    ).as_dict()


def _full_probe_row() -> dict[str, Any]:
    context = build_full_surface_organ_probe_context()
    packet = _packet_payload(context)
    trace = dict(packet.get("organ_trace") or {})
    rendered = json.dumps(packet, ensure_ascii=False, sort_keys=True)
    mapped = set(trace.get("mapped_organs") or [])
    return {
        "mapped_organ_count": int(trace.get("mapped_organ_count") or len(mapped)),
        "missing_organs": [organ_id for organ_id in SURFACE_ORGAN_IDS if organ_id not in mapped],
        "raw_leak": any(marker in rendered for marker in RAW_MARKERS),
        "packet_chars": len(rendered),
    }


def _checks(*, rows: Sequence[Mapping[str, Any]], full_probe: Mapping[str, Any]) -> list[dict[str, Any]]:
    row_list = [dict(row) for row in rows]
    return [
        _check(
            "rule_table_covers_23_organs",
            set(_rule_by_organ()) == set(SURFACE_ORGAN_IDS) and len(SURFACE_ORGAN_IDS) == 23,
            {"organ_count": len(SURFACE_ORGAN_IDS)},
        ),
        _check(
            "full_probe_maps_all_23_organs",
            int(full_probe.get("mapped_organ_count") or 0) == 23 and not list(full_probe.get("missing_organs") or []),
            dict(full_probe),
        ),
        _check(
            "each_organ_maps_itself",
            all(bool(row.get("mapped_self")) for row in row_list),
            {"failed": [row["organ_id"] for row in row_list if not bool(row.get("mapped_self"))]},
        ),
        _check(
            "each_organ_changes_final_packet",
            all(bool(row.get("has_final_packet_effect")) for row in row_list),
            {"failed": [row["organ_id"] for row in row_list if not bool(row.get("has_final_packet_effect"))]},
        ),
        _check(
            "each_organ_effect_hits_allowed_contract",
            all(bool(row.get("effect_hits_allowed_contract")) for row in row_list),
            {
                "failed": [
                    {
                        "organ_id": row["organ_id"],
                        "allowed_slots": row.get("allowed_slots"),
                        "changed_slots": row.get("changed_slots"),
                    }
                    for row in row_list
                    if not bool(row.get("effect_hits_allowed_contract"))
                ]
            },
        ),
        _check(
            "raw_context_dropped_for_each_organ",
            all(bool(row.get("raw_context_dropped")) for row in row_list) and not bool(full_probe.get("raw_leak")),
            {"failed": [row["organ_id"] for row in row_list if not bool(row.get("raw_context_dropped"))]},
        ),
        _check(
            "packets_remain_bounded",
            max(int(row.get("packet_chars") or 0) for row in row_list) < 12_000
            and int(full_probe.get("packet_chars") or 0) < 12_000,
            {
                "max_row_packet_chars": max(int(row.get("packet_chars") or 0) for row in row_list),
                "full_probe_packet_chars": int(full_probe.get("packet_chars") or 0),
            },
        ),
    ]


def _fixture_for_organ(organ_id: str) -> dict[str, Any]:
    fixtures: dict[str, dict[str, Any]] = {
        "ans": {"ans_state": {"recovery_mode": True, "proactive_multiplier": 0.42}},
        "cerebellum": {"cerebellum_state": {"jitter_level": 0.76, "correction_load": 0.68}},
        "circadian": {"circadian_state": {"sleep_pressure": 0.82, "phase": "late_night"}},
        "cortex": {"cortex_state": {"context_collector_active": True}, "tribe_route": {"route": "chat", "confidence": 0.91}},
        "default_mode": {
            "default_mode_state": {
                "continuity_score": 0.72,
                "care_drive": 0.58,
                "thought_stream_active": True,
                "self_directive": {
                    "self_identity": "current_soul",
                    "style_anchor": ["自我连续性：像同一个人持续在场。"],
                    "must_not_do": ["不要输出私有自我事件。"],
                },
            }
        },
        "endocrine": {"endocrine_gate": {"stress_index": 0.76, "cortisol_level": 0.72}},
        "glymphatic": {"glymphatic_state": {"recovery_pressure": 0.74}},
        "hippocampus": {
            "memory_layers": {
                "recent_summary_present": True,
                "episodic_available": True,
                "semantic_available": True,
                "recent_memory_count": 4,
            }
        },
        "immune": {"immune_state": {"risk_level": "high", "last_decision": "allow_guarded"}},
        "initiative_governor": {"initiative_decision": {"decision": "defer", "reason": "validation"}},
        "interoception": {"interoception_state": {"fatigue": 0.78, "cognitive_load_index": 0.75}},
        "language_system": {"language_dual_system": {"response_mode": "chat_text", "vts_expression_id": "HK_Thinking"}},
        "limbic": {"emotion_context": {"emotion_tag": "HURT", "emotion_intensity": 0.72}},
        "mirror_neuron": {"mirror_neuron_state": {"resonance_strength": 0.72}},
        "motor_cortex": {"motor_cortex_state": {"last_action_plan": "validation_only"}},
        "pain_reward": {"hedonic_state": {"pain_level": 0.66, "reward_level": 0.18, "loneliness_level": 0.62}},
        "peripheral": {"peripheral_state": {"output_channel": "chat_text", "platform": "dm"}},
        "prefrontal": {"prefrontal_state": {"drift_risk": 0.42, "plan_stability": 0.52}},
        "proprioception": {"proprioception_state": {"cognitive_load_index": 0.80}},
        "social_brain": {
            "bond_context": {
                "stage": "CONFIDANT",
                "stage_label": "亲密恋人",
                "bond_vector": {"intimacy": 0.86, "trust": 0.84, "playfulness": 0.55},
            },
            "social_brain_state": {
                "bond_stage": "CONFIDANT",
                "relationship_playfulness": 0.56,
            },
        },
        "somatic": {"somatic_state": {"fatigue_level": 0.78}},
        "thalamus": {"thalamus_state": {"route": "chat", "route_confidence": 0.89}},
        "vestibular": {"vestibular_state": {"jitter_index": 0.82, "route_bias": "conservative"}},
    }
    return dict(fixtures[organ_id])


def _packet_extract(packet: Mapping[str, Any], reply_control: Mapping[str, Any]) -> dict[str, Any]:
    task = dict(packet.get("task") or {})
    affect = dict(packet.get("affect") or {})
    relationship = dict(packet.get("relationship") or {})
    continuity = dict(packet.get("continuity") or {})
    delivery = dict(packet.get("delivery") or {})
    return {
        "information_budget": task.get("information_budget"),
        "question_budget": task.get("question_budget"),
        "emotional_tone": affect.get("emotional_tone"),
        "withdraw_bias": affect.get("withdraw_bias"),
        "relationship_distance": relationship.get("relationship_distance"),
        "relationship_phase": relationship.get("relationship_phase"),
        "should_be_brief": continuity.get("should_be_brief"),
        "allow_followup": continuity.get("allow_followup"),
        "response_form": delivery.get("response_form"),
        "output_channel": delivery.get("output_channel"),
        "vts_expression_id": delivery.get("vts_expression_id"),
        "reply_control": {
            "emotional_tone": reply_control.get("emotional_tone"),
            "relationship_distance": reply_control.get("relationship_distance"),
            "information_budget": reply_control.get("information_budget"),
            "question_budget": reply_control.get("question_budget"),
            "style_anchor_count": len(list(reply_control.get("style_anchor") or [])),
            "must_not_do_count": len(list(reply_control.get("must_not_do") or [])),
        },
    }


def _rule_by_organ() -> dict[str, dict[str, Any]]:
    return {str(row["organ_id"]): dict(row) for row in organ_surface_rule_table()}


def _allowed_final_slots(allowed_slots: Sequence[Any]) -> set[str]:
    result: set[str] = set()
    for slot in allowed_slots:
        result.update(ALLOWED_FINAL_SLOTS.get(str(slot), set()))
    return result


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Organ Influence Matrix Validation",
        "",
        "This is a contract validation, not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- organ_count: `{summary.get('organ_count')}`",
        f"- effective_organ_count: `{summary.get('effective_organ_count')}`",
        f"- failed_count: `{summary.get('failed_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Matrix", ""])
    for row in list(details.get("rows") or []):
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "- `{organ}` mapped=`{mapped}` changed=`{changed}` allowed=`{allowed}`".format(
                organ=row.get("organ_id"),
                mapped=",".join(str(item) for item in list(row.get("mapped_organs") or [])),
                changed=",".join(str(item) for item in list(row.get("changed_slots") or [])),
                allowed=",".join(str(item) for item in list(row.get("allowed_slots") or [])),
            )
        )
    lines.extend(
        [
            "",
            "## Full Probe",
            "",
            "```json",
            json.dumps(details.get("full_probe") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate per-organ influence on the compressed main-chain packet.")
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_validation(out_dir=args.out_dir)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if str(result["summary"].get("status")) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
