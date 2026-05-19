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

from host_brain.organ_ops.organ_contract_matrix import build_organ_contract_matrix  # noqa: E402
from tools.validate_full_digital_life_loop import (  # noqa: E402
    build_arg_parser as build_full_arg_parser,
    run_full_digital_life_loop_validation,
)


DEFAULT_OUT = ROOT / "artifacts" / "digital_life_causality"
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
RAW_BOUNDARY_CHECKS = (
    "surface_prompt_has_no_raw_organ_context",
    "raw_organ_text_not_leaked_to_reply",
    "private_thought_not_leaked_to_surface_prompt",
    "private_thought_not_leaked_to_reply",
    "no_source_refs_in_reply",
    "no_internal_markers_in_reply",
)


def run_validation(
    *,
    mode: str = "fixture",
    turns: int = 4,
    out_dir: Path | None = None,
    run_id: str = "",
    details_json: Path | None = None,
) -> dict[str, Any]:
    resolved_out = out_dir or (DEFAULT_OUT / time.strftime("%Y%m%d-%H%M%S"))
    resolved_out.mkdir(parents=True, exist_ok=True)
    if details_json is not None:
        loaded = _load_existing_details(details_json)
        full_summary = loaded["summary"]
        full_details = loaded["details"]
        full_output_dir = str(details_json.parent)
    else:
        normalized_mode = _normalize_mode(mode)
        full_result = asyncio.run(
            run_full_digital_life_loop_validation(
                build_full_arg_parser().parse_args(
                    [
                        "--mode",
                        normalized_mode,
                        "--out-dir",
                        str(resolved_out / "full_loop"),
                        "--run-id",
                        str(run_id or f"causality-{int(time.time())}"),
                        "--max-turns",
                        str(max(1, int(turns or 1))),
                    ]
                )
            )
        )
        full_summary = dict(full_result.get("summary") or {})
        full_details = dict(full_result.get("details") or {})
        full_output_dir = str(full_summary.get("output_dir") or resolved_out / "full_loop")

    traces = build_causality_traces(full_details)
    checks = _checks(full_summary=full_summary, traces=traces)
    summary = {
        "status": "PASS" if all(bool(check.get("passed")) for check in checks) else "FAIL",
        "evaluation_mode": "digital_life_causality_trace_contract_no_text_quality_scoring",
        "mode": str(full_details.get("mode") or full_summary.get("mode") or mode),
        "turn_count": len(traces),
        "failed_count": sum(1 for check in checks if not bool(check.get("passed"))),
        "checks": {str(check.get("name")): bool(check.get("passed")) for check in checks},
        "output_dir": str(resolved_out),
        "full_loop_output_dir": full_output_dir,
        "source_details_json": str(details_json or ""),
    }
    details = {
        "summary": summary,
        "checks": checks,
        "traces": traces,
        "full_summary": full_summary,
        "run_id": str(full_details.get("run_id") or ""),
        "persona_self_id": str(full_details.get("persona_self_id") or ""),
        "bond_user_id": str(full_details.get("bond_user_id") or ""),
        "session_id": str(full_details.get("session_id") or ""),
    }
    _write_json(resolved_out / "summary.json", summary)
    _write_json(resolved_out / "details.json", details)
    _write_jsonl(resolved_out / "turn_causality.jsonl", traces)
    (resolved_out / "causality_report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def build_causality_traces(full_details: Mapping[str, Any]) -> list[dict[str, Any]]:
    matrix = {str(row["organ_id"]): dict(row) for row in build_organ_contract_matrix()}
    turns = [dict(turn) for turn in list(full_details.get("turns") or []) if isinstance(turn, Mapping)]
    traces: list[dict[str, Any]] = []
    previous_score = 0
    previous_self_updates = 0
    for index, turn in enumerate(turns, start=1):
        surface_context = dict(turn.get("surface_context_packet") or {})
        surface_packet = dict(surface_context.get("surface_influence_packet") or {})
        organ_trace = dict(surface_packet.get("organ_trace") or {})
        active_organs = [str(item) for item in list(organ_trace.get("mapped_organs") or [])]
        control_deltas = _control_deltas(surface_packet)
        reply_control = dict(turn.get("reply_control_packet") or {})
        prompt_fields = _prompt_bound_fields(reply_control)
        surface = dict(turn.get("surface") or {})
        memory_write = dict(turn.get("memory_write") or {})
        memory_recall = dict(turn.get("memory_recall") or {})
        bond = dict(turn.get("bond") or {})
        bond_state = dict(bond.get("state") or {})
        bond_update = dict(bond.get("update") or {})
        inner = dict(turn.get("inner_world") or {})
        latest_private = dict(inner.get("latest_private_thought") or {})
        self_after = dict((turn.get("self_model") or {}).get("after") or {})
        current_score = int(bond_state.get("total_score") or 0)
        current_self_updates = int(self_after.get("self_update_count") or 0)
        traces.append(
            {
                "turn_index": index,
                "case_id": str(turn.get("case_id") or ""),
                "user_message": str(turn.get("user_message") or ""),
                "active_organs": active_organs,
                "active_organ_count": len(active_organs),
                "organs_to_control_fields": _organs_to_control_fields(active_organs, matrix=matrix, organ_trace=organ_trace),
                "control_deltas": control_deltas,
                "control_delta_slots": sorted(control_deltas),
                "prompt_bound_fields": prompt_fields,
                "prompt_bound_field_names": sorted(prompt_fields),
                "reply": {
                    "text": str(turn.get("reply_text") or ""),
                    "generation_selection": str(dict(surface.get("generation_trace") or {}).get("selection") or ""),
                    "observation_codes": list(surface.get("observation_codes") or []),
                },
                "emotion_vts": {
                    "user_emotion_tag": str(surface.get("user_emotion_tag") or ""),
                    "natsume_emotion_tag": str(surface.get("natsume_emotion_tag") or ""),
                    "vts_expression_id": str(surface.get("vts_expression_id") or ""),
                    "emotion_confidence": surface.get("emotion_confidence"),
                },
                "memory": {
                    "recall_fact_count": len(list(memory_recall.get("facts") or [])),
                    "route_ids": list(memory_recall.get("route_ids") or []),
                    "write_success": bool(memory_write.get("success")),
                    "memory_id": str(memory_write.get("memory_id") or ""),
                },
                "bond": {
                    "status": str(bond_update.get("status") or ""),
                    "update_kind": str(bond_update.get("update_kind") or ""),
                    "reason_codes": list(bond_update.get("reason_codes") or []),
                    "stage": str(bond_state.get("current_stage") or ""),
                    "total_score": current_score,
                    "score_delta_from_previous_turn": current_score - previous_score,
                },
                "inner_world": {
                    "private_text_present": bool(
                        str(latest_private.get("private_text") or latest_private.get("note") or "").strip()
                    ),
                    "private_text": str(latest_private.get("private_text") or latest_private.get("note") or ""),
                    "focus": str(latest_private.get("focus") or ""),
                    "source": str(latest_private.get("source") or ""),
                },
                "self_model": {
                    "self_update_count": current_self_updates,
                    "self_update_delta_from_previous_turn": current_self_updates - previous_self_updates,
                    "agency_score": self_after.get("agency_score"),
                    "self_continuity_score": self_after.get("self_continuity_score"),
                },
                "causal_chain": _causal_chain_summary(
                    active_organs=active_organs,
                    control_deltas=control_deltas,
                    prompt_fields=prompt_fields,
                    turn=turn,
                    bond_score_delta=current_score - previous_score,
                    self_update_delta=current_self_updates - previous_self_updates,
                ),
                "boundary": {
                    "raw_context_dropped": bool(organ_trace.get("raw_context_dropped")),
                    "dropped_raw_field_count": int(organ_trace.get("dropped_raw_field_count") or 0),
                    "private_thought_not_in_reply": _private_not_in_reply(turn),
                    "raw_markers_not_in_reply": _raw_markers_not_in_reply(turn),
                },
            }
        )
        previous_score = current_score
        previous_self_updates = current_self_updates
    return traces


def _control_deltas(surface_packet: Mapping[str, Any]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for slot in ("task", "affect", "relationship", "memory", "continuity", "safety", "delivery"):
        value = surface_packet.get(slot)
        cleaned = _strip_empty(value)
        if cleaned not in (None, "", [], {}, ()):
            deltas[slot] = cleaned
    return deltas


def _prompt_bound_fields(reply_control: Mapping[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for field in REPLY_CONTROL_FIELDS:
        value = reply_control.get(field)
        cleaned = _strip_empty(value)
        if cleaned not in (None, "", [], {}, ()):
            fields[field] = cleaned
    return fields


def _organs_to_control_fields(
    active_organs: Sequence[str],
    *,
    matrix: Mapping[str, Mapping[str, Any]],
    organ_trace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    allowed_by_organ = dict(organ_trace.get("contract_allowed_fields_by_organ") or {})
    rows: list[dict[str, Any]] = []
    for organ_id in active_organs:
        contract = dict(matrix.get(organ_id) or {})
        rows.append(
            {
                "organ_id": organ_id,
                "compressed_fields": list(allowed_by_organ.get(organ_id) or contract.get("compressed_output_fields") or []),
                "surface_slots": list(contract.get("surface_slots") or []),
                "declared_effects": list(contract.get("main_chain_effects") or []),
                "role_summary": str(contract.get("role_summary") or ""),
            }
        )
    return rows


def _causal_chain_summary(
    *,
    active_organs: Sequence[str],
    control_deltas: Mapping[str, Any],
    prompt_fields: Mapping[str, Any],
    turn: Mapping[str, Any],
    bond_score_delta: int,
    self_update_delta: int,
) -> dict[str, Any]:
    surface = dict(turn.get("surface") or {})
    memory_write = dict(turn.get("memory_write") or {})
    inner = dict(turn.get("inner_world") or {})
    latest_private = dict(inner.get("latest_private_thought") or {})
    return {
        "organ_count": len(active_organs),
        "control_slots": sorted(control_deltas),
        "prompt_fields": sorted(prompt_fields),
        "reply_emitted": bool(str(turn.get("reply_text") or "").strip()),
        "emotion_vts_emitted": bool(
            surface.get("user_emotion_tag") and surface.get("natsume_emotion_tag") and surface.get("vts_expression_id")
        ),
        "memory_written": bool(memory_write.get("success")),
        "bond_score_delta": bond_score_delta,
        "private_thought_generated": bool(str(latest_private.get("private_text") or latest_private.get("note") or "").strip()),
        "self_model_advanced": self_update_delta > 0,
    }


def _checks(*, full_summary: Mapping[str, Any], traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    trace_list = [dict(trace) for trace in traces]
    full_checks = dict(full_summary.get("checks") or {})
    return [
        _check("full_loop_passed", bool(full_summary.get("passed")), {"output_dir": full_summary.get("output_dir")}),
        _check("trace_has_turns", bool(trace_list), {"turn_count": len(trace_list)}),
        _check(
            "each_turn_has_organ_influence_trace",
            all(int(trace.get("active_organ_count") or 0) > 0 and list(trace.get("organs_to_control_fields") or []) for trace in trace_list),
            {"failed": [trace.get("turn_index") for trace in trace_list if not list(trace.get("organs_to_control_fields") or [])]},
        ),
        _check(
            "each_turn_has_control_deltas_and_reply_control",
            all(list(trace.get("control_delta_slots") or []) and list(trace.get("prompt_bound_field_names") or []) for trace in trace_list),
            {
                "failed": [
                    trace.get("turn_index")
                    for trace in trace_list
                    if not (list(trace.get("control_delta_slots") or []) and list(trace.get("prompt_bound_field_names") or []))
                ]
            },
        ),
        _check(
            "each_turn_has_reply",
            all(str(dict(trace.get("reply") or {}).get("text") or "").strip() for trace in trace_list),
            {"failed": [trace.get("turn_index") for trace in trace_list if not str(dict(trace.get("reply") or {}).get("text") or "").strip()]},
        ),
        _check(
            "each_turn_has_emotion_and_vts",
            all(
                dict(trace.get("emotion_vts") or {}).get("user_emotion_tag")
                and dict(trace.get("emotion_vts") or {}).get("natsume_emotion_tag")
                and dict(trace.get("emotion_vts") or {}).get("vts_expression_id")
                for trace in trace_list
            ),
            {"failed": [trace.get("turn_index") for trace in trace_list if not dict(trace.get("emotion_vts") or {}).get("vts_expression_id")]},
        ),
        _check(
            "each_turn_writes_memory",
            all(bool(dict(trace.get("memory") or {}).get("write_success")) for trace in trace_list),
            {"failed": [trace.get("turn_index") for trace in trace_list if not bool(dict(trace.get("memory") or {}).get("write_success"))]},
        ),
        _check(
            "each_turn_updates_bond",
            all(str(dict(trace.get("bond") or {}).get("status") or "") == "APPLIED" for trace in trace_list),
            {"failed": [trace.get("turn_index") for trace in trace_list if str(dict(trace.get("bond") or {}).get("status") or "") != "APPLIED"]},
        ),
        _check(
            "each_turn_has_private_thought",
            all(bool(dict(trace.get("inner_world") or {}).get("private_text_present")) for trace in trace_list),
            {"failed": [trace.get("turn_index") for trace in trace_list if not bool(dict(trace.get("inner_world") or {}).get("private_text_present"))]},
        ),
        _check(
            "self_model_advances_each_turn",
            all(int(dict(trace.get("self_model") or {}).get("self_update_delta_from_previous_turn") or 0) > 0 for trace in trace_list),
            {
                "failed": [
                    trace.get("turn_index")
                    for trace in trace_list
                    if int(dict(trace.get("self_model") or {}).get("self_update_delta_from_previous_turn") or 0) <= 0
                ]
            },
        ),
        _check(
            "raw_and_private_boundaries_hold",
            all(bool(full_checks.get(name, True)) for name in RAW_BOUNDARY_CHECKS)
            and all(bool(dict(trace.get("boundary") or {}).get("private_thought_not_in_reply")) for trace in trace_list)
            and all(bool(dict(trace.get("boundary") or {}).get("raw_markers_not_in_reply")) for trace in trace_list),
            {
                "full_loop_boundary_checks": {name: full_checks.get(name) for name in RAW_BOUNDARY_CHECKS if name in full_checks},
                "failed_turns": [
                    trace.get("turn_index")
                    for trace in trace_list
                    if not (
                        bool(dict(trace.get("boundary") or {}).get("private_thought_not_in_reply"))
                        and bool(dict(trace.get("boundary") or {}).get("raw_markers_not_in_reply"))
                    )
                ],
            },
        ),
    ]


def _private_not_in_reply(turn: Mapping[str, Any]) -> bool:
    reply = str(turn.get("reply_text") or "")
    latest_private = dict((turn.get("inner_world") or {}).get("latest_private_thought") or {})
    private_text = str(latest_private.get("private_text") or latest_private.get("note") or "").strip()
    return not private_text or private_text not in reply


def _raw_markers_not_in_reply(turn: Mapping[str, Any]) -> bool:
    reply = str(turn.get("reply_text") or "")
    return not any(marker in reply for marker in ("raw_organ_outputs", "organ_outputs", "ReplyControlPacket", "inner_monologue"))


def _strip_empty(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            cleaned = _strip_empty(child)
            if cleaned not in (None, "", [], {}, ()):
                result[str(key)] = cleaned
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result_list = []
        for item in value:
            cleaned = _strip_empty(item)
            if cleaned not in (None, "", [], {}, ()):
                result_list.append(cleaned)
        return result_list
    return value


def _load_existing_details(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("details json must be a mapping")
    data = dict(payload)
    details = _extract_details(data)
    summary = _extract_summary(data, path=path)
    return {"summary": summary, "details": details}


def _extract_details(payload: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("turns"), list):
        return dict(payload)
    if isinstance(payload.get("details"), Mapping) and isinstance(dict(payload["details"]).get("turns"), list):
        return dict(payload["details"])
    if isinstance(payload.get("full_details"), Mapping) and isinstance(dict(payload["full_details"]).get("turns"), list):
        return dict(payload["full_details"])
    raise ValueError("could not find full-loop turns in details json")


def _extract_summary(payload: Mapping[str, Any], *, path: Path) -> dict[str, Any]:
    if isinstance(payload.get("summary"), Mapping):
        return dict(payload["summary"])
    if isinstance(payload.get("full_summary"), Mapping):
        return dict(payload["full_summary"])
    sibling = path.with_name("summary.json")
    if sibling.exists():
        raw = json.loads(sibling.read_text(encoding="utf-8"))
        return dict(raw or {}) if isinstance(raw, Mapping) else {}
    return {"passed": True, "checks": {}, "output_dir": str(path.parent), "turn_count": len(list(_extract_details(payload).get("turns") or []))}


def _normalize_mode(mode: str) -> str:
    normalized = str(mode or "fixture").strip().lower()
    if normalized not in {"fixture", "live"}:
        raise ValueError("mode must be fixture or live")
    return normalized


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Digital Life Causality Trace",
        "",
        "This traces organ influence through compressed controls and downstream state updates. It is not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- failed_count: `{summary.get('failed_count')}`",
        f"- full_loop_output_dir: `{summary.get('full_loop_output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for check in list(details.get("checks") or []):
        marker = "PASS" if check.get("passed") else "FAIL"
        lines.append(f"- `{marker}` {check.get('name')}: `{json.dumps(check.get('details') or {}, ensure_ascii=False)}`")
    lines.extend(["", "## Turns", ""])
    for trace in list(details.get("traces") or []):
        if not isinstance(trace, Mapping):
            continue
        reply = dict(trace.get("reply") or {})
        emotion = dict(trace.get("emotion_vts") or {})
        memory = dict(trace.get("memory") or {})
        bond = dict(trace.get("bond") or {})
        inner = dict(trace.get("inner_world") or {})
        self_model = dict(trace.get("self_model") or {})
        lines.extend(
            [
                f"### Turn {trace.get('turn_index')} `{trace.get('case_id')}`",
                "",
                f"- user: {trace.get('user_message')}",
                f"- active_organs: `{trace.get('active_organ_count')}` slots=`{','.join(str(item) for item in list(trace.get('control_delta_slots') or []))}`",
                f"- prompt_fields: `{','.join(str(item) for item in list(trace.get('prompt_bound_field_names') or []))}`",
                f"- reply: {reply.get('text')}",
                f"- emotion/vts: user=`{emotion.get('user_emotion_tag')}` natsume=`{emotion.get('natsume_emotion_tag')}` vts=`{emotion.get('vts_expression_id')}`",
                f"- memory: write=`{memory.get('write_success')}` id=`{memory.get('memory_id')}` recall_facts=`{memory.get('recall_fact_count')}`",
                f"- bond: status=`{bond.get('status')}` kind=`{bond.get('update_kind')}` score_delta=`{bond.get('score_delta_from_previous_turn')}` stage=`{bond.get('stage')}`",
                f"- inner: source=`{inner.get('source')}` focus=`{inner.get('focus')}` private_present=`{inner.get('private_text_present')}`",
                f"- self: update_count=`{self_model.get('self_update_count')}` delta=`{self_model.get('self_update_delta_from_previous_turn')}`",
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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trace digital-life causality from organs to reply and state updates.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--details-json", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_validation(
        mode=args.mode,
        turns=args.turns,
        out_dir=args.out_dir,
        run_id=args.run_id,
        details_json=args.details_json,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
