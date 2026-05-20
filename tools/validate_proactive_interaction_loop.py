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


DEFAULT_OUT = ROOT / "artifacts" / "proactive_interaction_loop"


def _foreground_runner(context: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(context.get("metadata") or {})
    return {
        "request_id": context["request_id"],
        "user_message": context["user_message"],
        "reply_text": context.get("reply_text") or "我在，刚才那句主动问候已经收到。",
        "status": "OK",
        "mapped_organ_count": 23,
        "user_emotion_tag": "NEUTRAL",
        "natsume_emotion_tag": "CALM",
        "vts_expression_id": "HK_Normal",
        "organ_trace": {
            "mapped_organ_count": 23,
            "mapped_organs": ["cortex"] * 23,
            "slots": ["task", "affect", "memory", "proactive_interaction"],
        },
        "reply_control_packet": {
            "task_mode": "daily_chat",
            "response_move": "reply_current_turn",
            "proactive_reply": True,
        },
        "raw": {
            "metadata_keys": sorted(metadata.keys()),
            "thalamus_route": str(dict(metadata.get("thalamus_intent_draft") or {}).get("route") or ""),
        },
    }


def run_validation(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(getattr(args, "run_id", "") or f"proactive-interaction-{int(time.time())}")
    with DigitalLifeSupervisor(
        DigitalLifeRuntimeConfig(
            mode="fixture",
            run_id=run_id,
            persona_self_id=str(getattr(args, "persona_self_id", "") or ""),
            session_id=str(getattr(args, "session_id", "") or "proactive-interaction-dry-run"),
            out_dir=out_dir / "state",
            foreground_runner=_foreground_runner,
        )
    ) as supervisor:
        event = supervisor.run_proactive_interaction_dry_run(
            user_reply=str(getattr(args, "user_reply", "") or "在啊，怎么啦？"),
            reply_text=str(getattr(args, "reply_text", "") or "我在，刚才那句主动问候已经收到。"),
        )
        final_snapshot = supervisor.snapshot()
        events = supervisor.events

    summary = _summary(run_id=run_id, out_dir=out_dir, event=event, final_snapshot=final_snapshot)
    details = {
        "summary": summary,
        "event": event,
        "final_snapshot": final_snapshot,
        "events": events,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    _write_jsonl(out_dir / "events.jsonl", events)
    (out_dir / "report.md").write_text(_markdown_report(summary), encoding="utf-8")
    return {"summary": summary, "details": details}


def _summary(
    *,
    run_id: str,
    out_dir: Path,
    event: Mapping[str, Any],
    final_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    interaction = dict(event.get("interaction") or {})
    receipt = dict(event.get("delivery_receipt") or {})
    thalamus = dict(event.get("thalamus_intent_draft") or {})
    foreground = dict(event.get("foreground_turn") or {})
    turn = dict(foreground.get("turn") or {})
    proactive_outbox = dict(final_snapshot.get("proactive_outbox") or {})
    proactive_interaction = dict(final_snapshot.get("proactive_interaction") or {})
    status = (
        "PASS"
        if str(event.get("status") or "") == "PASS"
        and str(receipt.get("delivery_status") or "") == "DELIVERED_DRY_RUN"
        and str(thalamus.get("route") or "") == "chat"
        and str(thalamus.get("route_reason") or "") == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"
        and int(turn.get("mapped_organ_count") or 0) >= 23
        else "FAIL"
    )
    return {
        "status": status,
        "evaluation_mode": "proactive_interaction_dry_run",
        "run_id": run_id,
        "output_dir": str(out_dir),
        "alive": bool(final_snapshot.get("alive")),
        "foreground_turn_count": int(final_snapshot.get("foreground_turn_count") or 0),
        "idle_tick_count": int(final_snapshot.get("idle_tick_count") or 0),
        "proactive_outbox_queued_count": int(proactive_outbox.get("queued_count") or 0),
        "delivery_receipt_count": int(proactive_interaction.get("delivery_receipt_count") or 0),
        "interaction_count": int(proactive_interaction.get("interaction_count") or 0),
        "delivery_status": str(receipt.get("delivery_status") or ""),
        "gateway_mode": str(receipt.get("gateway_mode") or ""),
        "thalamus_route": str(thalamus.get("route") or ""),
        "thalamus_reason_code": str(thalamus.get("route_reason") or thalamus.get("reason_code") or ""),
        "thalamus_route_source": str(thalamus.get("route_source") or ""),
        "mapped_organ_count": int(interaction.get("mapped_organ_count") or turn.get("mapped_organ_count") or 0),
        "receipt_path": str((out_dir / "state" / "delivery_receipts.jsonl")),
        "interaction_path": str((out_dir / "state" / "proactive_interactions.jsonl")),
    }


def _markdown_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Proactive Interaction Dry-Run",
        "",
        f"- status: `{summary.get('status')}`",
        f"- run_id: `{summary.get('run_id')}`",
        f"- proactive_outbox_queued_count: `{summary.get('proactive_outbox_queued_count')}`",
        f"- delivery_receipt_count: `{summary.get('delivery_receipt_count')}`",
        f"- interaction_count: `{summary.get('interaction_count')}`",
        f"- delivery_status: `{summary.get('delivery_status')}`",
        f"- gateway_mode: `{summary.get('gateway_mode')}`",
        f"- thalamus_route: `{summary.get('thalamus_route')}`",
        f"- thalamus_reason_code: `{summary.get('thalamus_reason_code')}`",
        f"- mapped_organ_count: `{summary.get('mapped_organ_count')}`",
        "",
    ]
    return "\n".join(lines)


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate proactive interaction dry-run loop.")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--persona-self-id", default="")
    parser.add_argument("--session-id", default="proactive-interaction-dry-run")
    parser.add_argument("--user-reply", default="在啊，怎么啦？")
    parser.add_argument("--reply-text", default="我在，刚才那句主动问候已经收到。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = run_validation(args)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
