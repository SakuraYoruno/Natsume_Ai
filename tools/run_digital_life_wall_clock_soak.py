from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import traceback
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.runtime import DigitalLifeRuntimeConfig, DigitalLifeSupervisor  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "digital_life_wall_clock_soak"


def run_wall_clock_soak(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(getattr(args, "run_id", "") or f"wall-clock-soak-{int(time.time())}")
    duration_seconds = _duration_seconds(args)
    tick_interval_seconds = max(0.0, float(getattr(args, "tick_interval_seconds", 300.0) or 0.0))
    max_ticks = int(getattr(args, "max_ticks", 0) or 0)
    started_at = time.time()
    stop_at = started_at + duration_seconds
    stop_requested = {"value": False, "reason": ""}

    def _request_stop(signum: int, _frame: Any) -> None:
        stop_requested["value"] = True
        stop_requested["reason"] = f"signal_{signum}"

    _install_signal_handler(signal.SIGINT, _request_stop)
    _install_signal_handler(getattr(signal, "SIGTERM", None), _request_stop)

    checkpoints: list[dict[str, Any]] = []
    final_snapshot: dict[str, Any] = {}
    run_status = "RUNNING"
    error: dict[str, Any] = {}

    try:
        with DigitalLifeSupervisor(
            DigitalLifeRuntimeConfig(
                mode=str(getattr(args, "mode", "live") or "live"),
                run_id=run_id,
                persona_self_id=str(getattr(args, "persona_self_id", "") or ""),
                session_id=str(getattr(args, "session_id", "") or "digital-life-wall-clock-soak"),
                out_dir=out_dir / "state",
                start_scheduler_threads=bool(getattr(args, "start_scheduler", False)),
            )
        ) as supervisor:
            final_snapshot = supervisor.snapshot()
            _append_checkpoint(
                out_dir,
                checkpoints,
                _checkpoint(
                    event_type="runtime_started",
                    run_status=run_status,
                    started_at=started_at,
                    stop_at=stop_at,
                    tick_interval_seconds=tick_interval_seconds,
                    tick_index=0,
                    snapshot=final_snapshot,
                ),
            )
            _write_summary(
                out_dir,
                _summary(
                    run_status=run_status,
                    run_id=run_id,
                    started_at=started_at,
                    stop_at=stop_at,
                    tick_interval_seconds=tick_interval_seconds,
                    checkpoints=checkpoints,
                    snapshot=final_snapshot,
                    error=error,
                ),
            )

            message = str(getattr(args, "message", "") or "").strip()
            if message:
                foreground = supervisor.run_foreground_turn(
                    message,
                    reply_text=str(getattr(args, "reply", "") or ""),
                    task_mode=str(getattr(args, "task_mode", "") or "daily_chat"),
                    response_move=str(getattr(args, "response_move", "") or "reply_current_turn"),
                    metadata={"soak_kind": "real_wall_clock"},
                )
                final_snapshot = dict(foreground.get("snapshot") or supervisor.snapshot())
                _append_checkpoint(
                    out_dir,
                    checkpoints,
                    _checkpoint(
                        event_type="foreground_turn",
                        run_status=run_status,
                        started_at=started_at,
                        stop_at=stop_at,
                        tick_interval_seconds=tick_interval_seconds,
                        tick_index=0,
                        snapshot=final_snapshot,
                        payload={"turn": dict(foreground.get("turn") or {})},
                    ),
                )
                _write_summary(
                    out_dir,
                    _summary(
                        run_status=run_status,
                        run_id=run_id,
                        started_at=started_at,
                        stop_at=stop_at,
                        tick_interval_seconds=tick_interval_seconds,
                        checkpoints=checkpoints,
                        snapshot=final_snapshot,
                        error=error,
                    ),
                )

            tick_index = 0
            next_tick_at = time.time()
            while not stop_requested["value"]:
                now = time.time()
                if now >= stop_at:
                    break
                if max_ticks > 0 and tick_index >= max_ticks:
                    break
                if now < next_tick_at:
                    time.sleep(min(1.0, next_tick_at - now))
                    continue

                tick_index += 1
                event = supervisor.run_idle_tick()
                final_snapshot = dict(event.get("snapshot") or supervisor.snapshot())
                _append_checkpoint(
                    out_dir,
                    checkpoints,
                    _checkpoint(
                        event_type="idle_tick",
                        run_status=run_status,
                        started_at=started_at,
                        stop_at=stop_at,
                        tick_interval_seconds=tick_interval_seconds,
                        tick_index=tick_index,
                        snapshot=final_snapshot,
                        payload={"task_results": dict(event.get("task_results") or {})},
                    ),
                )
                _write_summary(
                    out_dir,
                    _summary(
                        run_status=run_status,
                        run_id=run_id,
                        started_at=started_at,
                        stop_at=stop_at,
                        tick_interval_seconds=tick_interval_seconds,
                        checkpoints=checkpoints,
                        snapshot=final_snapshot,
                        error=error,
                    ),
                )
                next_tick_at = time.time() + tick_interval_seconds

            run_status = "STOPPED" if stop_requested["value"] else "COMPLETED"
            final_snapshot = supervisor.snapshot()
            _write_json(out_dir / "events.json", {"events": supervisor.events})
    except Exception as exc:
        run_status = "FAILED"
        error = {
            "error_type": type(exc).__name__,
            "detail": str(exc),
            "traceback": traceback.format_exc(),
        }

    summary = _summary(
        run_status=run_status,
        run_id=run_id,
        started_at=started_at,
        stop_at=stop_at,
        tick_interval_seconds=tick_interval_seconds,
        checkpoints=checkpoints,
        snapshot=final_snapshot,
        error=error,
    )
    _write_summary(out_dir, summary)
    _write_json(
        out_dir / "details.json",
        {
            "summary": summary,
            "checkpoints": checkpoints,
            "final_snapshot": final_snapshot,
            "error": error,
        },
    )
    (out_dir / "report.md").write_text(_markdown_report(summary), encoding="utf-8")
    return {"summary": summary, "details": {"checkpoints": checkpoints, "final_snapshot": final_snapshot, "error": error}}


def _checkpoint(
    *,
    event_type: str,
    run_status: str,
    started_at: float,
    stop_at: float,
    tick_interval_seconds: float,
    tick_index: int,
    snapshot: Mapping[str, Any],
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    return {
        "event_type": str(event_type),
        "run_status": str(run_status),
        "ts_ms": int(now * 1000),
        "elapsed_seconds": round(max(0.0, now - started_at), 3),
        "remaining_seconds": round(max(0.0, stop_at - now), 3),
        "tick_interval_seconds": float(tick_interval_seconds),
        "tick_index": int(tick_index),
        "snapshot": _project_snapshot(snapshot),
        "payload": _strip_large(dict(payload or {})),
    }


def _summary(
    *,
    run_status: str,
    run_id: str,
    started_at: float,
    stop_at: float,
    tick_interval_seconds: float,
    checkpoints: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any],
    error: Mapping[str, Any],
) -> dict[str, Any]:
    now = time.time()
    projected = _project_snapshot(snapshot)
    target_seconds = max(0.0, stop_at - started_at)
    elapsed_seconds = max(0.0, now - started_at)
    idle_tick_count = int(projected.get("idle_tick_count") or 0)
    return {
        "status": "PASS" if run_status == "COMPLETED" and not error else run_status,
        "run_status": str(run_status),
        "evaluation_mode": "real_wall_clock_digital_life_soak",
        "run_id": str(run_id),
        "started_at_ms": int(started_at * 1000),
        "updated_at_ms": int(now * 1000),
        "target_stop_at_ms": int(stop_at * 1000),
        "target_seconds": round(target_seconds, 3),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "progress_ratio": round(min(1.0, elapsed_seconds / target_seconds), 6) if target_seconds > 0 else 1.0,
        "tick_interval_seconds": float(tick_interval_seconds),
        "checkpoint_count": len(list(checkpoints)),
        "idle_tick_count": idle_tick_count,
        "foreground_turn_count": int(projected.get("foreground_turn_count") or 0),
        "alive": bool(projected.get("alive")),
        "is_advancing": bool(projected.get("is_advancing")),
        "tick_health": str(projected.get("tick_health") or ""),
        "scheduler_status": str(projected.get("scheduler_status") or ""),
        "registered_organ_count": int(projected.get("registered_organ_count") or 0),
        "last_mapped_organ_count": int(projected.get("last_mapped_organ_count") or 0),
        "persona_self_id": str(projected.get("persona_self_id") or ""),
        "last_private_focus": str(projected.get("last_private_focus") or ""),
        "last_private_source": str(projected.get("last_private_source") or ""),
        "error": dict(error or {}),
    }


def _project_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(snapshot or {})
    background = dict(data.get("background_runtime") or {})
    organ = dict(data.get("organ_runtime") or {})
    scheduler = dict(data.get("scheduler") or {})
    latest_private = dict(data.get("latest_private_thought") or {})
    return {
        "alive": bool(data.get("alive")),
        "mode": str(data.get("mode") or ""),
        "run_id": str(data.get("run_id") or ""),
        "persona_self_id": str(data.get("persona_self_id") or ""),
        "foreground_turn_count": int(data.get("foreground_turn_count") or 0),
        "idle_tick_count": int(data.get("idle_tick_count") or 0),
        "tick_count": int(data.get("tick_count") or 0),
        "registered_organ_count": int(organ.get("registered_organ_count") or 0),
        "last_mapped_organ_count": int(organ.get("last_mapped_organ_count") or 0),
        "scheduler_status": str(scheduler.get("scheduler_status") or ""),
        "core_ticks_registered": bool(background.get("core_ticks_registered")),
        "is_advancing": bool(background.get("is_advancing")),
        "tick_health": str(background.get("tick_health") or ""),
        "last_private_focus": str(latest_private.get("focus") or ""),
        "last_private_source": str(latest_private.get("source") or ""),
        "last_private_salience": latest_private.get("salience"),
    }


def _markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Real Wall Clock Digital Life Soak",
        "",
        f"- status: `{summary.get('status')}`",
        f"- run_status: `{summary.get('run_status')}`",
        f"- elapsed_seconds: `{summary.get('elapsed_seconds')}` / `{summary.get('target_seconds')}`",
        f"- progress_ratio: `{summary.get('progress_ratio')}`",
        f"- idle_tick_count: `{summary.get('idle_tick_count')}`",
        f"- alive: `{summary.get('alive')}`",
        f"- is_advancing: `{summary.get('is_advancing')}`",
        f"- scheduler_status: `{summary.get('scheduler_status')}`",
        f"- tick_health: `{summary.get('tick_health')}`",
        f"- registered_organ_count: `{summary.get('registered_organ_count')}`",
        f"- last_mapped_organ_count: `{summary.get('last_mapped_organ_count')}`",
        "",
    ]
    return "\n".join(lines)


def _duration_seconds(args: argparse.Namespace) -> float:
    raw_seconds = getattr(args, "duration_seconds", None)
    if raw_seconds is not None:
        return max(0.0, float(raw_seconds or 0.0))
    return max(0.0, float(getattr(args, "duration_hours", 24.0) or 0.0) * 3600.0)


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def _append_checkpoint(out_dir: Path, checkpoints: list[dict[str, Any]], checkpoint: Mapping[str, Any]) -> None:
    row = dict(checkpoint)
    checkpoints.append(row)
    with (out_dir / "checkpoints.jsonl").open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_summary(out_dir: Path, summary: Mapping[str, Any]) -> None:
    _write_json(out_dir / "summary.json", summary)
    (out_dir / "report.md").write_text(_markdown_report(summary), encoding="utf-8")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _strip_large(value: Any, *, limit: int = 2000) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _strip_large(child, limit=limit) for key, child in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_strip_large(item, limit=limit) for item in list(value)[:40]]
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1].rstrip() + "..."
    return value


def _install_signal_handler(signum: Any, handler: Any) -> None:
    if signum is None:
        return
    try:
        signal.signal(signum, handler)
    except (OSError, ValueError):
        return


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a real wall-clock digital-life soak with incremental checkpoints.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="live")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--persona-self-id", default="")
    parser.add_argument("--session-id", default="digital-life-wall-clock-soak")
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--tick-interval-seconds", type=float, default=300.0)
    parser.add_argument("--max-ticks", type=int, default=0)
    parser.add_argument("--message", default="")
    parser.add_argument("--reply", default="")
    parser.add_argument("--task-mode", default="daily_chat")
    parser.add_argument("--response-move", default="reply_current_turn")
    parser.add_argument("--start-scheduler", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_wall_clock_soak(args)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("run_status") in {"RUNNING", "COMPLETED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
