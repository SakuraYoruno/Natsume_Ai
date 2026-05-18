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

from tools.validate_full_digital_life_loop import (  # noqa: E402
    build_arg_parser as build_full_arg_parser,
    run_full_digital_life_loop_validation,
)


DEFAULT_OUT = ROOT / "artifacts" / "long_run_digital_life_loop"


async def run_long_run_digital_life_loop_validation(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    turns = max(1, int(getattr(args, "turns", 32) or 32))
    min_turns = max(1, int(getattr(args, "min_turns", 30) or 30))
    mode = str(getattr(args, "mode", "fixture") or "fixture").strip().lower()
    full_args = build_full_arg_parser().parse_args(
        [
            "--mode",
            mode,
            "--out-dir",
            str(out_dir / "full_loop"),
            "--run-id",
            str(getattr(args, "run_id", "") or f"long-{int(time.time())}"),
            "--max-turns",
            str(turns),
        ]
    )
    full_result = await run_full_digital_life_loop_validation(full_args)
    details = dict(full_result.get("details") or {})
    full_summary = dict(full_result.get("summary") or {})
    timeline = _build_timeline(details)
    long_checks = _long_checks(
        full_summary=full_summary,
        details=details,
        timeline=timeline,
        requested_turns=turns,
        min_turns=min_turns,
    )
    summary = {
        "mode": mode,
        "evaluation_mode": "long_run_digital_life_loop_contract_validation_no_text_quality_scoring",
        "passed": bool(full_summary.get("passed")) and all(long_checks.values()),
        "turn_count": len(list(details.get("turns") or [])),
        "requested_turns": turns,
        "min_turns": min_turns,
        "timeline_event_count": len(timeline),
        "full_loop_passed": bool(full_summary.get("passed")),
        "checks": {
            **{f"full_loop.{key}": bool(value) for key, value in dict(full_summary.get("checks") or {}).items()},
            **long_checks,
        },
        "output_dir": str(out_dir),
        "full_loop_output_dir": str(full_summary.get("output_dir") or ""),
    }
    long_details = {
        "summary": summary,
        "full_summary": full_summary,
        "full_details": details,
        "timeline": timeline,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", long_details)
    _write_jsonl(out_dir / "timeline.jsonl", timeline)
    (out_dir / "timeline.md").write_text(_timeline_markdown(summary, timeline), encoding="utf-8")
    (out_dir / "report.md").write_text(_report_markdown(summary, details, timeline), encoding="utf-8")
    return {"summary": summary, "details": long_details}


def _build_timeline(details: Mapping[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    run_id = str(details.get("run_id") or "")
    for index, raw_turn in enumerate(list(details.get("turns") or []), start=1):
        if not isinstance(raw_turn, Mapping):
            continue
        turn = dict(raw_turn)
        base = {
            "run_id": run_id,
            "turn_index": index,
            "case_id": str(turn.get("case_id") or ""),
        }
        surface = dict(turn.get("surface") or {})
        memory = dict(turn.get("memory_recall") or {})
        memory_write = dict(turn.get("memory_write") or {})
        bond = dict(turn.get("bond") or {})
        inner = dict(turn.get("inner_world") or {})
        latest_inner = dict(inner.get("latest_private_thought") or {})
        self_after = dict((turn.get("self_model") or {}).get("after") or {})
        organ_packet = dict((turn.get("surface_context_packet") or {}).get("surface_influence_packet") or {})
        organ_trace = dict(organ_packet.get("organ_trace") or {})
        timeline.extend(
            [
                {
                    **base,
                    "event_type": "user_turn",
                    "text": str(turn.get("user_message") or ""),
                    "emotion_tag": str(surface.get("user_emotion_tag") or ""),
                    "task_mode": str(turn.get("task_mode") or ""),
                },
                {
                    **base,
                    "event_type": "surface_reply",
                    "text": str(turn.get("reply_text") or ""),
                    "natsume_emotion_tag": str(surface.get("natsume_emotion_tag") or ""),
                    "vts_expression_id": str(surface.get("vts_expression_id") or ""),
                    "generation_selection": str(dict(surface.get("generation_trace") or {}).get("selection") or ""),
                },
                {
                    **base,
                    "event_type": "memory_recall",
                    "fact_count": len(list(memory.get("facts") or [])),
                    "route_ids": list(memory.get("route_ids") or []),
                },
                {
                    **base,
                    "event_type": "memory_write",
                    "success": bool(memory_write.get("success")),
                    "memory_id": str(memory_write.get("memory_id") or ""),
                },
                {
                    **base,
                    "event_type": "bond_update",
                    "status": str(dict(bond.get("update") or {}).get("status") or ""),
                    "update_kind": str(dict(bond.get("payload") or {}).get("update_kind") or ""),
                    "stage": str(dict(bond.get("state") or {}).get("current_stage") or ""),
                    "total_score": int(dict(bond.get("state") or {}).get("total_score") or 0),
                },
                {
                    **base,
                    "event_type": "inner_idle_tick",
                    "private_text": str(latest_inner.get("private_text") or latest_inner.get("note") or ""),
                    "focus": str(latest_inner.get("focus") or ""),
                    "source": str(latest_inner.get("source") or ""),
                    "salience": latest_inner.get("salience"),
                },
                {
                    **base,
                    "event_type": "self_model_update",
                    "agency_score": self_after.get("agency_score"),
                    "self_continuity_score": self_after.get("self_continuity_score"),
                    "self_update_count": int(self_after.get("self_update_count") or 0),
                },
                {
                    **base,
                    "event_type": "organ_surface_influence",
                    "mapped_organ_count": int(organ_trace.get("mapped_organ_count") or 0),
                    "mapped_organs": list(organ_trace.get("mapped_organs") or []),
                    "slots": list(organ_trace.get("slots") or []),
                    "high_load": bool(organ_trace.get("high_load")),
                },
            ]
        )
    return timeline


def _long_checks(
    *,
    full_summary: Mapping[str, Any],
    details: Mapping[str, Any],
    timeline: Sequence[Mapping[str, Any]],
    requested_turns: int,
    min_turns: int,
) -> dict[str, bool]:
    turns = [dict(item) for item in list(details.get("turns") or []) if isinstance(item, Mapping)]
    replies = [str(turn.get("reply_text") or "") for turn in turns]
    bond_scores = [
        int(((turn.get("bond") or {}).get("state") or {}).get("total_score") or 0)
        for turn in turns
    ]
    self_updates = [
        int(((turn.get("self_model") or {}).get("after") or {}).get("self_update_count") or 0)
        for turn in turns
    ]
    user_emotions = {
        str((turn.get("surface") or {}).get("user_emotion_tag") or "")
        for turn in turns
        if str((turn.get("surface") or {}).get("user_emotion_tag") or "")
    }
    natsume_emotions = {
        str((turn.get("surface") or {}).get("natsume_emotion_tag") or "")
        for turn in turns
        if str((turn.get("surface") or {}).get("natsume_emotion_tag") or "")
    }
    inner_focuses = {
        str(((turn.get("inner_world") or {}).get("latest_private_thought") or {}).get("focus") or "")
        for turn in turns
        if str(((turn.get("inner_world") or {}).get("latest_private_thought") or {}).get("focus") or "")
    }
    timeline_types = {str(item.get("event_type") or "") for item in timeline}
    required_types = {
        "user_turn",
        "surface_reply",
        "memory_recall",
        "memory_write",
        "bond_update",
        "inner_idle_tick",
        "self_model_update",
        "organ_surface_influence",
    }
    mapped_counts = [
        int((item.get("mapped_organ_count") or 0))
        for item in timeline
        if str(item.get("event_type") or "") == "organ_surface_influence"
    ]
    memory_writes = [
        item for item in timeline if str(item.get("event_type") or "") == "memory_write"
    ]
    idle_ticks = [
        item for item in timeline if str(item.get("event_type") or "") == "inner_idle_tick"
    ]
    return {
        "long_run.full_loop_passed": bool(full_summary.get("passed")),
        "long_run.reaches_requested_turns": len(turns) == requested_turns,
        "long_run.reaches_minimum_30_turns": len(turns) >= min_turns,
        "long_run.timeline_has_required_event_types": required_types <= timeline_types,
        "long_run.timeline_has_events_for_each_turn": len(timeline) >= len(turns) * len(required_types),
        "long_run.each_turn_has_reply_text": bool(replies) and all(bool(reply.strip()) for reply in replies),
        "long_run.each_turn_has_idle_private_thought": len(idle_ticks) == len(turns)
        and all(str(item.get("private_text") or "").strip() for item in idle_ticks),
        "long_run.memory_written_each_turn": len(memory_writes) == len(turns)
        and all(bool(item.get("success")) for item in memory_writes),
        "long_run.bond_score_changes_over_time": bool(bond_scores) and max(bond_scores) > min(bond_scores),
        "long_run.self_model_keeps_updating": bool(self_updates) and max(self_updates) >= len(turns),
        "long_run.emotion_tags_vary_over_time": len(user_emotions) >= 3 and len(natsume_emotions) >= 3,
        "long_run.inner_focus_varies_over_time": len(inner_focuses) >= 3,
        "long_run.all_turns_map_23_organs": bool(mapped_counts) and min(mapped_counts) >= 23,
    }


def _timeline_markdown(summary: Mapping[str, Any], timeline: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# Long Run Timeline",
        "",
        f"- passed: `{summary.get('passed')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- timeline_event_count: `{summary.get('timeline_event_count')}`",
        "",
    ]
    for item in timeline:
        event_type = str(item.get("event_type") or "")
        turn_index = item.get("turn_index")
        case_id = item.get("case_id")
        if event_type == "surface_reply":
            lines.append(f"- turn {turn_index} `{case_id}` reply: {item.get('text')}")
        elif event_type == "inner_idle_tick":
            lines.append(f"- turn {turn_index} `{case_id}` inner: {item.get('private_text')}")
        elif event_type == "bond_update":
            lines.append(
                f"- turn {turn_index} `{case_id}` bond: {item.get('update_kind')} score={item.get('total_score')} stage={item.get('stage')}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _report_markdown(
    summary: Mapping[str, Any],
    details: Mapping[str, Any],
    timeline: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Long Run Digital Life Loop Validation",
        "",
        "This is a long-run contract validation, not text quality scoring.",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- timeline_event_count: `{summary.get('timeline_event_count')}`",
        f"- full_loop_output_dir: `{summary.get('full_loop_output_dir')}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in dict(summary.get("checks") or {}).items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    lines.extend(["", "## Replies", ""])
    for index, turn in enumerate(list(details.get("turns") or []), start=1):
        if not isinstance(turn, Mapping):
            continue
        lines.extend(
            [
                f"### {index}. {turn.get('case_id')}",
                "",
                f"- user: {turn.get('user_message')}",
                f"- reply: {turn.get('reply_text')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Timeline Sample",
            "",
            "```json",
            json.dumps(list(timeline)[:24], ensure_ascii=False, indent=2, sort_keys=True),
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


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a 30+ turn digital-life long-run loop validation.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--turns", type=int, default=32)
    parser.add_argument("--min-turns", type=int, default=30)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_long_run_digital_life_loop_validation(args))
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
