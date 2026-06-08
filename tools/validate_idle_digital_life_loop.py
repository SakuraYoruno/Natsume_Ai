from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_inner_world_self_loop import (  # noqa: E402
    FixtureInnerActivityModel,
    RuntimePatchContext,
    _collect_state_files,
    _latest_inner_event,
    _persona_identity_file_checks,
    _project_inner_state,
    _rewind_inner_world_for_idle_tick,
)


DEFAULT_OUT = ROOT / "artifacts" / "idle_digital_life_loop"
IDLE_TOPICS = (
    "文本质量别再漂",
    "关系连续性",
    "主链稳定性",
    "下次见面先回应当前句",
)
PRIVATE_TEMPLATE_MARKERS = (
    "fallback",
    "tracking topic:",
    "控制包",
    "模型",
    "prompt",
    "内部状态",
    "生成",
    "回复",
    "接住",
)


def run_idle_digital_life_loop_validation(args: argparse.Namespace) -> dict[str, Any]:
    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    inner_model = FixtureInnerActivityModel() if mode == "fixture" else None
    try:
        with RuntimePatchContext(out_dir=out_dir, inner_model=inner_model):
            return _run_loop(args=args, out_dir=out_dir, mode=mode, inner_model=inner_model)
    except Exception as exc:
        return _write_failure_result(out_dir=out_dir, mode=mode, exc=exc)


def _run_loop(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    inner_model: FixtureInnerActivityModel | None,
) -> dict[str, Any]:
    from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415
    from host_brain.persona.inner_world import OfflineInnerWorld, _inner_world_llm_circuit_snapshot  # noqa: PLC0415
    from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state  # noqa: PLC0415
    from host_brain.tribe.default_mode_agent import DefaultModeAgent  # noqa: PLC0415
    from host_brain.tribe.tribe_state import TribeStateRegistry  # noqa: PLC0415

    run_id = str(getattr(args, "run_id", "") or f"idle-{int(time.time())}")
    ticks = max(1, int(getattr(args, "ticks", 8) or 8))
    tick_minutes = max(5, int(getattr(args, "tick_minutes", 180) or 180))
    min_hours = max(0.0, float(getattr(args, "min_hours", 24.0) or 24.0))
    persona_self_id = get_canonical_persona_self_id()
    world = OfflineInnerWorld(state_dir=str(out_dir / "inner_world_state"))
    default_agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    base_ts_ms = int(time.time() * 1000)

    world.on_session_end(
        user_id=persona_self_id,
        valence=-0.08,
        arousal=0.42,
        pending_topics=[IDLE_TOPICS[0], IDLE_TOPICS[1]],
        mood_baseline=-0.02,
    )

    tick_details: list[dict[str, Any]] = []
    private_notes: list[str] = []
    default_notes: list[str] = []
    for index in range(1, ticks + 1):
        topic = IDLE_TOPICS[(index - 1) % len(IDLE_TOPICS)]
        _rewind_inner_world_for_idle_tick(world, user_id=persona_self_id, minutes=tick_minutes, topics=[topic])
        inner_state_before = world.get_state(user_id=persona_self_id)
        before_event_count = int(inner_state_before.get("spontaneous_event_count") or 0)
        latest_before = _latest_inner_event(inner_state_before)
        before_event_marker = _inner_event_marker(latest_before)
        tick_result = world.tick(user_id=persona_self_id, emit_inner_stream=True)
        inner_state = world.get_state(user_id=persona_self_id)
        after_event_count = int(inner_state.get("spontaneous_event_count") or 0)
        latest_inner = _latest_inner_event(inner_state)
        after_event_marker = _inner_event_marker(latest_inner)
        new_event_created = after_event_count > before_event_count or (
            bool(after_event_marker) and after_event_marker != before_event_marker
        )
        private_note = str(latest_inner.get("private_text") or latest_inner.get("note") or "").strip()
        if private_note and new_event_created:
            private_notes.append(private_note)
        default_tick = default_agent.tick_idle_cycle(
            person_id=persona_self_id,
            topic_phrase=topic,
            source="idle_digital_life_loop",
            now_ts_ms=base_ts_ms + index * tick_minutes * 60_000,
            inner_state=inner_state,
        )
        default_note = str(default_tick.get("note") or "").strip()
        if default_note:
            default_notes.append(default_note)
        self_directive = build_self_directive_packet(load_persistent_self_state(user_id=persona_self_id))
        tick_details.append(
            {
                "tick_index": index,
                "simulated_minutes_since_last_tick": tick_minutes,
                "topic": topic,
                "inner_world": {
                    "before_event_count": before_event_count,
                    "after_event_count": after_event_count,
                    "new_event_created": new_event_created,
                    "tick_result": tick_result,
                    "state": _project_inner_state(inner_state),
                    "latest_private_thought": latest_inner,
                    "circuit": _inner_world_llm_circuit_snapshot(),
                },
                "default_mode": {
                    "tick_result": default_tick,
                },
                "self_model": {
                    "after": self_directive,
                },
            }
        )

    runtime_state = default_agent.export_runtime_state()
    details = {
        "run_id": run_id,
        "mode": mode,
        "evaluation_mode": "idle_digital_life_loop_contract_validation_no_text_quality_scoring",
        "persona_self_id": persona_self_id,
        "requested_ticks": ticks,
        "ticks": tick_details,
        "surface_replies": [],
        "default_runtime_state": runtime_state,
        "inner_model_calls": list(inner_model.calls) if inner_model is not None else [],
        "simulated_hours": round(ticks * tick_minutes / 60.0, 4),
        "min_hours": min_hours,
        "state_files": _collect_state_files(out_dir),
    }
    checks = _checks(details=details, private_notes=private_notes, default_notes=default_notes)
    summary = {
        "mode": mode,
        "evaluation_mode": details["evaluation_mode"],
        "passed": all(checks.values()),
        "checks": checks,
        "output_dir": str(out_dir),
        "persona_self_id": persona_self_id,
        "tick_count": len(tick_details),
        "simulated_hours": details["simulated_hours"],
        "private_thought_count": len(private_notes),
        "default_note_count": len(default_notes),
        "self_update_count": int((tick_details[-1]["self_model"]["after"] if tick_details else {}).get("self_update_count") or 0),
        "surface_reply_count": 0,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _checks(
    *,
    details: Mapping[str, Any],
    private_notes: Sequence[str],
    default_notes: Sequence[str],
) -> dict[str, bool]:
    ticks = [dict(item) for item in list(details.get("ticks") or []) if isinstance(item, Mapping)]
    latest_self = dict((ticks[-1].get("self_model") or {}).get("after") or {}) if ticks else {}
    inner_sources = [
        str(((tick.get("inner_world") or {}).get("latest_private_thought") or {}).get("source") or "")
        for tick in ticks
    ]
    event_counts = [
        int((tick.get("inner_world") or {}).get("after_event_count") or 0)
        for tick in ticks
    ]
    event_markers = [
        _inner_event_marker(dict((tick.get("inner_world") or {}).get("latest_private_thought") or {}))
        for tick in ticks
    ]
    default_notes_joined = "\n".join(default_notes)
    private_joined = "\n".join(private_notes)
    circuit = dict(((ticks[-1].get("inner_world") or {}).get("circuit") or {}) if ticks else {})
    mode = str(details.get("mode") or "fixture")
    identity_checks = _persona_identity_file_checks(details)
    default_ticks = [dict((tick.get("default_mode") or {}).get("tick_result") or {}) for tick in ticks]
    return {
        **identity_checks,
        "idle_loop_reaches_requested_ticks": bool(ticks) and len(ticks) == int(details.get("requested_ticks") or len(ticks)),
        "idle_loop_simulates_minimum_hours": float(details.get("simulated_hours") or 0.0) >= float(details.get("min_hours") or 0.0),
        "idle_loop_has_no_surface_replies": len(list(details.get("surface_replies") or [])) == 0,
        "inner_world_generated_private_thoughts_each_tick": len(private_notes) == len(ticks) and bool(private_notes),
        "inner_world_appends_new_private_event_each_tick": bool(ticks)
        and all(bool((tick.get("inner_world") or {}).get("new_event_created")) for tick in ticks)
        and len([marker for marker in event_markers if marker]) == len(ticks)
        and len(set(event_markers)) == len(event_markers),
        "inner_world_uses_llm_source_only": bool(inner_sources) and all(source == "llm" for source in inner_sources),
        "inner_model_was_called": mode == "live" or len(list(details.get("inner_model_calls") or [])) >= len(ticks),
        "default_mode_uses_model_or_waits_without_template": bool(default_ticks)
        and all(_default_tick_uses_model_or_waits(tick) for tick in default_ticks),
        "default_mode_updated_self_each_tick": int(latest_self.get("self_update_count") or 0) >= len(ticks),
        "default_runtime_exports_self_directive": bool(dict(details.get("default_runtime_state") or {}).get("self_directive")),
        "inner_world_circuit_closed_after_success": not bool(circuit.get("open")),
        "private_thoughts_are_not_empty_templates": bool(private_joined)
        and not any(marker in private_joined for marker in PRIVATE_TEMPLATE_MARKERS),
        "default_notes_are_not_empty_templates": not any(marker in default_notes_joined for marker in PRIVATE_TEMPLATE_MARKERS),
    }


def _default_tick_uses_model_or_waits(tick: Mapping[str, Any]) -> bool:
    note = str(tick.get("note") or "").strip()
    llm_meta = dict(tick.get("llm_meta") or {})
    if note:
        return not bool(llm_meta.get("note_unavailable")) and not bool(llm_meta.get("requires_model"))
    return bool(llm_meta.get("note_unavailable")) and bool(llm_meta.get("requires_model"))


def _inner_event_marker(event: Mapping[str, Any]) -> str:
    thought_id = str(event.get("thought_id") or "").strip()
    if thought_id:
        return f"thought:{thought_id}"
    ts_ms = str(event.get("ts_ms") or "").strip()
    if ts_ms:
        return f"ts:{ts_ms}"
    text = str(event.get("private_text") or event.get("note") or "").strip()
    focus = str(event.get("focus") or "").strip()
    return f"text:{focus}:{text}" if text else ""


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Idle Digital Life Loop Validation",
        "",
        "This validates private idle continuity only. It does not generate surface replies.",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- persona_self_id: `{summary.get('persona_self_id')}`",
        f"- tick_count: `{summary.get('tick_count')}`",
        f"- simulated_hours: `{summary.get('simulated_hours')}`",
        f"- private_thought_count: `{summary.get('private_thought_count')}`",
        f"- default_note_count: `{summary.get('default_note_count')}`",
        f"- self_update_count: `{summary.get('self_update_count')}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in dict(summary.get("checks") or {}).items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    lines.extend(["", "## Idle Ticks", ""])
    for tick in list(details.get("ticks") or []):
        if not isinstance(tick, Mapping):
            continue
        inner = dict(tick.get("inner_world") or {})
        latest = dict(inner.get("latest_private_thought") or {})
        default = dict((tick.get("default_mode") or {}).get("tick_result") or {})
        self_after = dict((tick.get("self_model") or {}).get("after") or {})
        lines.extend(
            [
                f"### Tick {tick.get('tick_index')}",
                "",
                f"- topic: {tick.get('topic')}",
                f"- private_inner_monologue: {latest.get('private_text') or latest.get('note') or ''}",
                f"- private_focus: `{latest.get('focus')}` source: `{latest.get('source')}` salience: `{latest.get('salience')}`",
                f"- default_note: {default.get('note')}",
                f"- self: agency=`{self_after.get('agency_score')}` continuity=`{self_after.get('self_continuity_score')}` updates=`{self_after.get('self_update_count')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_failure_result(*, out_dir: Path, mode: str, exc: BaseException) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": str(mode or "fixture"),
        "evaluation_mode": "idle_digital_life_loop_contract_validation_no_text_quality_scoring",
        "passed": False,
        "reason_code": "IDLE_DIGITAL_LIFE_LOOP_VALIDATION_FAILED",
        "error_type": type(exc).__name__,
        "detail": str(exc),
        "output_dir": str(out_dir),
    }
    details = {"failure": dict(summary), "traceback": traceback.format_exc()}
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _resolve_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "mode", "") or "fixture").strip().lower()
    if mode not in {"fixture", "live"}:
        raise ValueError(f"unsupported validation mode: {mode}")
    return mode


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate private idle digital-life continuity without surface replies.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--ticks", type=int, default=8)
    parser.add_argument("--tick-minutes", type=int, default=180)
    parser.add_argument("--min-hours", type=float, default=24.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_idle_digital_life_loop_validation(args)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
