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

from host_brain.persona.organ_influence_contract import (  # noqa: E402
    MAIN_CHAIN_EFFECTS,
    build_organ_influence_packet,
    organ_main_chain_integration_table,
)
from host_brain.persona.surface_influence_packet import build_surface_influence_packet  # noqa: E402
from host_brain.persona.surface_organ_influence import SURFACE_ORGAN_IDS  # noqa: E402
from host_brain.persona.surface_packet_compiler import compile_reply_control_packet  # noqa: E402
from tools.validate_organ_influence_matrix import (  # noqa: E402
    ALLOWED_FINAL_SLOTS,
    PACKET_SLOTS,
    RAW_MARKERS,
    _fixture_for_organ,
)
from tools.validate_surface_organ_system import RAW_LEAK_MARKER  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "organ_effect_ab"
CONTRACT_PACKET_FIELDS = (
    "task_delta",
    "emotion_delta",
    "relationship_delta",
    "drive_delta",
    "memory_candidates",
    "continuity_delta",
    "attention_focus",
    "inner_world_seed",
    "surface_constraints",
    "delivery_delta",
)
CONTRACT_FIELD_EFFECTS: Mapping[str, tuple[str, ...]] = {
    "task_delta": ("task",),
    "emotion_delta": ("emotion",),
    "relationship_delta": ("relationship",),
    "drive_delta": ("initiative",),
    "memory_candidates": ("memory",),
    "continuity_delta": ("inner_world", "initiative"),
    "attention_focus": ("surface_text",),
    "inner_world_seed": ("inner_world",),
    "surface_constraints": ("safety",),
    "delivery_delta": ("delivery",),
}
REPLY_CONTROL_FIELDS = (
    "response_move",
    "task_mode",
    "emotional_tone",
    "relationship_distance",
    "information_budget",
    "question_budget",
    "must_include",
    "must_not_do",
    "facts",
    "style_anchor",
)


def run_validation(*, out_dir: Path | None = None) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    rows = [_organ_ab_row(organ_id) for organ_id in SURFACE_ORGAN_IDS]
    checks = _checks(rows)
    summary = {
        "status": "PASS" if all(bool(item.get("passed")) for item in checks) else "FAIL",
        "evaluation_mode": "organ_effect_ab_contract_validation_no_text_quality_scoring",
        "organ_count": len(SURFACE_ORGAN_IDS),
        "changed_organ_count": sum(1 for row in rows if bool(row.get("contract_changed")) and bool(row.get("surface_changed"))),
        "failed_count": sum(1 for item in checks if not bool(item.get("passed"))),
        "checks": {str(item.get("name")): bool(item.get("passed")) for item in checks},
        "output_dir": str(resolved_out),
    }
    details = {"summary": summary, "checks": checks, "rows": rows}
    _write_json(resolved_out / "summary.json", summary)
    _write_json(resolved_out / "details.json", details)
    _write_jsonl(resolved_out / "effect_ab.jsonl", rows)
    (resolved_out / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _organ_ab_row(organ_id: str) -> dict[str, Any]:
    integration = _integration_by_organ()[organ_id]
    allowed_contract_fields = set(str(item) for item in list(integration.get("contract_fields") or []))
    allowed_surface_slots = _allowed_surface_slots_for_contract_fields(allowed_contract_fields)
    allowed_effects = set(str(item) for item in list(integration.get("main_chain_effects") or []))
    context = _fixture_for_organ(organ_id)
    context["raw_organ_outputs"] = {organ_id: {"messages": [RAW_LEAK_MARKER]}}

    baseline = _pipeline_payload({})
    variant = _pipeline_payload(context)

    changed_contract_fields = _changed_fields(
        baseline["organ_influence_packet"],
        variant["organ_influence_packet"],
        CONTRACT_PACKET_FIELDS,
    )
    changed_surface_slots = _changed_fields(
        baseline["surface_influence_packet"],
        variant["surface_influence_packet"],
        PACKET_SLOTS,
    )
    changed_reply_fields = _changed_fields(
        baseline["reply_control_packet"],
        variant["reply_control_packet"],
        REPLY_CONTROL_FIELDS,
    )
    implied_effects = _implied_effects(changed_contract_fields)
    rendered = json.dumps(variant, ensure_ascii=False, sort_keys=True)
    disallowed_contract_fields = sorted(set(changed_contract_fields) - allowed_contract_fields)
    disallowed_surface_slots = sorted(set(changed_surface_slots) - allowed_surface_slots)
    disallowed_effects = sorted(implied_effects - allowed_effects)
    return {
        "organ_id": organ_id,
        "context_keys": list(integration.get("context_keys") or []),
        "allowed_contract_fields": sorted(allowed_contract_fields),
        "allowed_surface_slots": sorted(allowed_surface_slots),
        "allowed_main_chain_effects": sorted(allowed_effects),
        "changed_contract_fields": changed_contract_fields,
        "changed_surface_slots": changed_surface_slots,
        "changed_reply_control_fields": changed_reply_fields,
        "changed_main_chain_effects": sorted(implied_effects),
        "contract_changed": bool(changed_contract_fields),
        "surface_changed": bool(changed_surface_slots),
        "reply_control_changed": bool(changed_reply_fields),
        "contract_changes_within_allowed_fields": not disallowed_contract_fields,
        "surface_changes_within_allowed_slots": not disallowed_surface_slots,
        "main_chain_effects_within_declared_effects": not disallowed_effects,
        "disallowed_contract_fields": disallowed_contract_fields,
        "disallowed_surface_slots": disallowed_surface_slots,
        "disallowed_main_chain_effects": disallowed_effects,
        "mapped_self": organ_id in list(dict(variant["surface_influence_packet"].get("organ_trace") or {}).get("mapped_organs") or []),
        "raw_context_not_leaked": not any(marker in rendered for marker in RAW_MARKERS),
        "packet_chars": len(rendered),
        "reply_extract": {
            key: variant["reply_control_packet"].get(key)
            for key in (
                "emotional_tone",
                "relationship_distance",
                "information_budget",
                "question_budget",
            )
        },
    }


def _pipeline_payload(context: Mapping[str, Any]) -> dict[str, Any]:
    reply_plan = {
        "task_mode": "daily_chat",
        "primary_topic": "daily_chat",
        "response_move": "reply_current_turn",
        "interaction_move": "reply_current_turn",
        "question_budget": 1,
    }
    organ_packet = build_organ_influence_packet(context).as_dict()
    surface_packet = build_surface_influence_packet(
        reply_plan=reply_plan,
        organ_influence_packet=organ_packet,
    ).as_dict()
    compiled = compile_reply_control_packet(
        user_message="验证单个器官状态变化是否越界影响主链。",
        task_control=reply_plan,
        influence_packet=surface_packet,
    )
    return {
        "organ_influence_packet": organ_packet,
        "surface_influence_packet": surface_packet,
        "reply_control_packet": compiled.packet.model_dump(),
    }


def _checks(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    row_list = [dict(row) for row in rows]
    return [
        _check(
            "effect_ab_covers_23_organs",
            len(row_list) == 23 and {str(row.get("organ_id")) for row in row_list} == set(SURFACE_ORGAN_IDS),
            {"row_count": len(row_list)},
        ),
        _check(
            "each_organ_changes_contract_and_surface",
            all(bool(row.get("contract_changed")) and bool(row.get("surface_changed")) for row in row_list),
            {
                "failed": [
                    row.get("organ_id")
                    for row in row_list
                    if not (bool(row.get("contract_changed")) and bool(row.get("surface_changed")))
                ]
            },
        ),
        _check(
            "each_organ_maps_itself",
            all(bool(row.get("mapped_self")) for row in row_list),
            {"failed": [row.get("organ_id") for row in row_list if not bool(row.get("mapped_self"))]},
        ),
        _check(
            "contract_changes_stay_inside_allowed_fields",
            all(bool(row.get("contract_changes_within_allowed_fields")) for row in row_list),
            {
                "failed": [
                    {
                        "organ_id": row.get("organ_id"),
                        "disallowed": row.get("disallowed_contract_fields"),
                        "changed": row.get("changed_contract_fields"),
                    }
                    for row in row_list
                    if not bool(row.get("contract_changes_within_allowed_fields"))
                ]
            },
        ),
        _check(
            "surface_changes_stay_inside_allowed_slots",
            all(bool(row.get("surface_changes_within_allowed_slots")) for row in row_list),
            {
                "failed": [
                    {
                        "organ_id": row.get("organ_id"),
                        "disallowed": row.get("disallowed_surface_slots"),
                        "changed": row.get("changed_surface_slots"),
                    }
                    for row in row_list
                    if not bool(row.get("surface_changes_within_allowed_slots"))
                ]
            },
        ),
        _check(
            "main_chain_effects_stay_declared",
            all(bool(row.get("main_chain_effects_within_declared_effects")) for row in row_list),
            {
                "failed": [
                    {
                        "organ_id": row.get("organ_id"),
                        "disallowed": row.get("disallowed_main_chain_effects"),
                        "changed": row.get("changed_main_chain_effects"),
                    }
                    for row in row_list
                    if not bool(row.get("main_chain_effects_within_declared_effects"))
                ]
            },
        ),
        _check(
            "reply_control_changes_are_observable",
            all(bool(row.get("reply_control_changed")) for row in row_list),
            {"failed": [row.get("organ_id") for row in row_list if not bool(row.get("reply_control_changed"))]},
        ),
        _check(
            "raw_context_never_reaches_packets",
            all(bool(row.get("raw_context_not_leaked")) for row in row_list),
            {"failed": [row.get("organ_id") for row in row_list if not bool(row.get("raw_context_not_leaked"))]},
        ),
        _check(
            "effect_ab_packets_are_bounded",
            max(int(row.get("packet_chars") or 0) for row in row_list) < 12_000,
            {"max_packet_chars": max(int(row.get("packet_chars") or 0) for row in row_list)},
        ),
    ]


def _allowed_surface_slots_for_contract_fields(fields: set[str]) -> set[str]:
    slots: set[str] = set()
    if "task_delta" in fields or "attention_focus" in fields:
        slots.add("task")
    if "emotion_delta" in fields:
        slots.add("affect")
    if "relationship_delta" in fields:
        slots.add("relationship")
    if "drive_delta" in fields:
        slots.update(ALLOWED_FINAL_SLOTS["drive"])
    if "memory_candidates" in fields:
        slots.update(ALLOWED_FINAL_SLOTS["memory"])
    if "continuity_delta" in fields or "inner_world_seed" in fields:
        slots.update(ALLOWED_FINAL_SLOTS["continuity"])
    if "surface_constraints" in fields:
        slots.add("safety")
    if "delivery_delta" in fields:
        slots.add("delivery")
    return slots


def _changed_fields(
    baseline: Mapping[str, Any],
    variant: Mapping[str, Any],
    fields: Sequence[str],
) -> list[str]:
    return [
        field
        for field in fields
        if _normalized_value(baseline.get(field)) != _normalized_value(variant.get(field))
    ]


def _normalized_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalized_value(child) for key, child in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalized_value(item) for item in value]
    return value


def _implied_effects(contract_fields: Sequence[str]) -> set[str]:
    effects: set[str] = set()
    for field in contract_fields:
        effects.update(CONTRACT_FIELD_EFFECTS.get(str(field), ()))
    effects.add("surface_text")
    return effects & set(MAIN_CHAIN_EFFECTS)


def _integration_by_organ() -> dict[str, dict[str, Any]]:
    return {str(row["organ_id"]): dict(row) for row in organ_main_chain_integration_table()}


def _check(name: str, passed: bool, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "details": dict(details or {})}


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Organ Effect AB Validation",
        "",
        "This validates single-organ state perturbations against the compressed contract. It is not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- organ_count: `{summary.get('organ_count')}`",
        f"- changed_organ_count: `{summary.get('changed_organ_count')}`",
        f"- failed_count: `{summary.get('failed_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Rows", ""])
    for row in list(details.get("rows") or []):
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "- `{organ}` contract=`{contract}` surface=`{surface}` reply=`{reply}` effects=`{effects}`".format(
                organ=row.get("organ_id"),
                contract=",".join(str(item) for item in list(row.get("changed_contract_fields") or [])),
                surface=",".join(str(item) for item in list(row.get("changed_surface_slots") or [])),
                reply=",".join(str(item) for item in list(row.get("changed_reply_control_fields") or [])),
                effects=",".join(str(item) for item in list(row.get("changed_main_chain_effects") or [])),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate per-organ AB effects against the main-chain contract.")
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    result = run_validation(out_dir=args.out_dir)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if str(result["summary"].get("status")) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
