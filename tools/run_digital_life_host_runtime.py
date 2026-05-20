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

from host_brain.runtime import DigitalLifeRuntimeConfig, DigitalLifeSupervisor  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "digital_life_host_runtime"


def run_host_runtime(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    messages = [str(item) for item in list(getattr(args, "message", []) or [])]
    replies = [str(item) for item in list(getattr(args, "reply", []) or [])]
    snapshots: list[dict[str, Any]] = []
    with DigitalLifeSupervisor(
        DigitalLifeRuntimeConfig(
            mode=str(getattr(args, "mode", "fixture") or "fixture"),
            run_id=str(getattr(args, "run_id", "") or f"host-runtime-{int(time.time())}"),
            persona_self_id=str(getattr(args, "persona_self_id", "") or ""),
            session_id=str(getattr(args, "session_id", "") or "digital-life-host-runtime"),
            out_dir=out_dir / "state",
            start_scheduler_threads=bool(getattr(args, "start_scheduler", False)),
        )
    ) as supervisor:
        snapshots.append(supervisor.snapshot())
        for index, message in enumerate(messages):
            event = supervisor.run_foreground_turn(
                message,
                reply_text=replies[index] if index < len(replies) else "",
                task_mode=str(getattr(args, "task_mode", "") or "daily_chat"),
                response_move=str(getattr(args, "response_move", "") or "reply_current_turn"),
            )
            snapshots.append(dict(event.get("snapshot") or supervisor.snapshot()))
        ticks = max(0, int(getattr(args, "ticks", 1) or 0))
        interval_s = max(0.0, float(getattr(args, "tick_interval_seconds", 0.0) or 0.0))
        for index in range(ticks):
            event = supervisor.run_idle_tick()
            snapshots.append(dict(event.get("snapshot") or supervisor.snapshot()))
            if interval_s > 0 and index < ticks - 1:
                time.sleep(interval_s)
        final_snapshot = supervisor.snapshot()
        snapshots.append(final_snapshot)
        events = supervisor.events

    summary = _summary(final_snapshot=final_snapshot, event_count=len(events), snapshot_count=len(snapshots), out_dir=out_dir)
    details = {
        "summary": summary,
        "snapshots": snapshots,
        "events": events,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    _write_jsonl(out_dir / "snapshots.jsonl", snapshots)
    _write_jsonl(out_dir / "events.jsonl", events)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _summary(
    *,
    final_snapshot: Mapping[str, Any],
    event_count: int,
    snapshot_count: int,
    out_dir: Path,
) -> dict[str, Any]:
    background = dict(final_snapshot.get("background_runtime") or {})
    organ_runtime = dict(final_snapshot.get("organ_runtime") or {})
    scheduler = dict(final_snapshot.get("scheduler") or {})
    proactive_task = dict(background.get("proactive_task") or {})
    proactive_outbox = dict(final_snapshot.get("proactive_outbox") or {})
    proactive_interaction = dict(final_snapshot.get("proactive_interaction") or {})
    return {
        "status": "PASS" if bool(final_snapshot.get("alive")) and bool(background.get("core_ticks_registered")) else "FAIL",
        "evaluation_mode": "digital_life_host_runtime_supervisor_no_text_quality_scoring",
        "mode": str(final_snapshot.get("mode") or ""),
        "run_id": str(final_snapshot.get("run_id") or ""),
        "persona_self_id": str(final_snapshot.get("persona_self_id") or ""),
        "alive": bool(final_snapshot.get("alive")),
        "foreground_turn_count": int(final_snapshot.get("foreground_turn_count") or 0),
        "idle_tick_count": int(final_snapshot.get("idle_tick_count") or 0),
        "registered_organ_count": int(organ_runtime.get("registered_organ_count") or 0),
        "surface_expected_organ_count": int(organ_runtime.get("surface_expected_organ_count") or 0),
        "last_mapped_organ_count": int(organ_runtime.get("last_mapped_organ_count") or 0),
        "scheduler_status": str(scheduler.get("scheduler_status") or ""),
        "core_ticks_registered": bool(background.get("core_ticks_registered")),
        "tick_health": str(background.get("tick_health") or ""),
        "is_advancing": bool(background.get("is_advancing")),
        "proactive_task_status": str(proactive_task.get("status") or ""),
        "proactive_outbox_queued_count": int(proactive_outbox.get("queued_count") or 0),
        "proactive_delivery_receipt_count": int(proactive_interaction.get("delivery_receipt_count") or 0),
        "proactive_interaction_count": int(proactive_interaction.get("interaction_count") or 0),
        "event_count": int(event_count),
        "snapshot_count": int(snapshot_count),
        "output_dir": str(out_dir),
    }


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    snapshots = [dict(item) for item in list(details.get("snapshots") or []) if isinstance(item, Mapping)]
    final = snapshots[-1] if snapshots else {}
    latest_private = dict(final.get("latest_private_thought") or {})
    lines = [
        "# Digital Life Host Runtime",
        "",
        "This starts the host-side runtime supervisor. It is not text quality scoring.",
        "",
        f"- status: `{summary.get('status')}`",
        f"- mode: `{summary.get('mode')}`",
        f"- alive: `{summary.get('alive')}`",
        f"- foreground_turn_count: `{summary.get('foreground_turn_count')}`",
        f"- idle_tick_count: `{summary.get('idle_tick_count')}`",
        f"- registered_organ_count: `{summary.get('registered_organ_count')}`",
        f"- scheduler_status: `{summary.get('scheduler_status')}`",
        f"- tick_health: `{summary.get('tick_health')}`",
        f"- is_advancing: `{summary.get('is_advancing')}`",
        f"- proactive_task_status: `{summary.get('proactive_task_status')}`",
        f"- proactive_outbox_queued_count: `{summary.get('proactive_outbox_queued_count')}`",
        f"- proactive_delivery_receipt_count: `{summary.get('proactive_delivery_receipt_count')}`",
        f"- proactive_interaction_count: `{summary.get('proactive_interaction_count')}`",
        "",
        "## Latest Private Thought",
        "",
        f"- source: `{latest_private.get('source')}` focus: `{latest_private.get('focus')}`",
        f"- text: {latest_private.get('private_text') or ''}",
        "",
        "## Final Snapshot",
        "",
        "```json",
        json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
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
    parser = argparse.ArgumentParser(description="Run the host-side digital-life runtime supervisor.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--persona-self-id", default="")
    parser.add_argument("--session-id", default="digital-life-host-runtime")
    parser.add_argument("--message", action="append", default=[])
    parser.add_argument("--reply", action="append", default=[])
    parser.add_argument("--task-mode", default="daily_chat")
    parser.add_argument("--response-move", default="reply_current_turn")
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--tick-interval-seconds", type=float, default=0.0)
    parser.add_argument("--start-scheduler", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_host_runtime(args)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
