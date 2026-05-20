from __future__ import annotations

import json
import time

from host_brain.runtime import DigitalLifeRuntimeConfig, DigitalLifeSupervisor
from tools.run_digital_life_host_runtime import build_arg_parser, run_host_runtime
from tools.run_digital_life_wall_clock_soak import (
    build_arg_parser as build_wall_clock_arg_parser,
    run_wall_clock_soak,
)
from tools.validate_proactive_interaction_loop import (
    build_arg_parser as build_proactive_interaction_arg_parser,
    run_validation as run_proactive_interaction_validation,
)


def _foreground_runner(context: dict[str, object]) -> dict[str, object]:
    return {
        "request_id": context["request_id"],
        "user_message": context["user_message"],
        "reply_text": "知道了，我先记着。",
        "status": "OK",
        "mapped_organ_count": 23,
        "user_emotion_tag": "NEUTRAL",
        "natsume_emotion_tag": "CALM",
        "vts_expression_id": "HK_Normal",
        "organ_trace": {
            "mapped_organ_count": 23,
            "mapped_organs": ["cortex"] * 23,
            "slots": ["task", "affect", "memory"],
        },
        "reply_control_packet": {
            "task_mode": "daily_chat",
            "response_move": "reply_current_turn",
        },
    }


def test_digital_life_supervisor_runs_foreground_and_idle_tick(tmp_path, monkeypatch) -> None:
    import host_brain.persona.inner_world as inner_world

    original_inner_world_dir = inner_world._DEFAULT_STATE_DIR
    supervisor = DigitalLifeSupervisor(
        DigitalLifeRuntimeConfig(
            mode="fixture",
            run_id="unit-runtime",
            out_dir=tmp_path,
            foreground_runner=_foreground_runner,  # type: ignore[arg-type]
        )
    )
    monkeypatch.setattr(
        supervisor,
        "_dispatch_scheduler_intent",
        lambda system_intent: {"status": "OK", "skill_name": system_intent.get("skill_name")},
    )

    with supervisor:
        start_snapshot = supervisor.snapshot()
        foreground = supervisor.run_foreground_turn("早啊")
        idle = supervisor.run_idle_tick()
        final_snapshot = supervisor.snapshot()

    assert start_snapshot["alive"] is True
    assert foreground["turn"]["reply_text"] == "知道了，我先记着。"
    assert foreground["turn"]["mapped_organ_count"] == 23
    assert idle["task_results"]["inner_world_numeric_tick"]["status"] == "OK"
    assert final_snapshot["foreground_turn_count"] == 1
    assert final_snapshot["idle_tick_count"] == 1
    assert final_snapshot["organ_runtime"]["registered_organ_count"] >= 23
    assert final_snapshot["background_runtime"]["core_ticks_registered"] is True
    assert any(event["event_type"] == "foreground_turn" for event in supervisor.events)
    assert any(event["event_type"] == "idle_tick" for event in supervisor.events)
    assert inner_world._DEFAULT_STATE_DIR == original_inner_world_dir


def test_digital_life_supervisor_runs_proactive_talk_into_safe_outbox(tmp_path) -> None:
    supervisor = DigitalLifeSupervisor(
        DigitalLifeRuntimeConfig(
            mode="fixture",
            run_id="unit-proactive-outbox",
            out_dir=tmp_path,
        )
    )

    with supervisor:
        idle = supervisor.run_idle_tick(task_ids=("proactive_talk_periodic",))
        final_snapshot = supervisor.snapshot()

    result = idle["task_results"]["proactive_talk_periodic"]
    nested = result["result"]
    push_result = nested["push_result"]
    outbox_path = tmp_path / "proactive_outbox.jsonl"
    episode_path = tmp_path / "proactive_episodes.jsonl"
    rows = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    episode_rows = [json.loads(line) for line in episode_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert result["status"] == "EXECUTED"
    assert nested["message"]["type"] == "proactive_talk"
    assert nested["message"]["proactive_episode"]["source"] == "emotional_impulse"
    assert push_result["status"] == "QUEUED"
    assert push_result["delivery_mode"] == "safe_outbox"
    assert len(rows) == 1
    assert len(episode_rows) == 1
    assert episode_rows[0]["send_allowed"] is True
    assert rows[0]["delivery_status"] == "QUEUED"
    assert rows[0]["message"]["type"] == "proactive_talk"
    assert rows[0]["message"]["proactive_episode"]["episode_id"] == episode_rows[0]["episode_id"]
    assert final_snapshot["background_runtime"]["proactive_task"]["enabled"] is True
    assert final_snapshot["background_runtime"]["proactive_task"]["status"] == "OK"
    assert final_snapshot["proactive_emotional_episode"]["episode_count"] == 1
    assert final_snapshot["proactive_emotional_episode"]["last_send_allowed"] is True
    assert final_snapshot["proactive_outbox"]["queued_count"] == 1


def test_digital_life_supervisor_holds_emotional_proactive_when_recent_outbound(tmp_path) -> None:
    supervisor = DigitalLifeSupervisor(
        DigitalLifeRuntimeConfig(
            mode="fixture",
            run_id="unit-proactive-held",
            out_dir=tmp_path,
        )
    )

    with supervisor:
        supervisor._last_proactive_outbox_ts_ms = int(time.time() * 1000)
        result = supervisor._dispatch_scheduler_intent({"skill_name": "proactive_talk", "args": {}})
        final_snapshot = supervisor.snapshot()

    episode_path = tmp_path / "proactive_episodes.jsonl"
    outbox_path = tmp_path / "proactive_outbox.jsonl"
    episode_rows = [json.loads(line) for line in episode_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert result["status"] == "SKIPPED"
    assert result["reason_code"] == "EMOTIONAL_PROACTIVE_HELD"
    assert len(episode_rows) == 1
    assert episode_rows[0]["send_allowed"] is False
    assert not outbox_path.exists()
    assert final_snapshot["proactive_emotional_episode"]["episode_count"] == 1
    assert final_snapshot["proactive_emotional_episode"]["last_send_allowed"] is False
    assert final_snapshot["proactive_outbox"]["queued_count"] == 0


def test_digital_life_supervisor_runs_proactive_interaction_dry_run(tmp_path) -> None:
    supervisor = DigitalLifeSupervisor(
        DigitalLifeRuntimeConfig(
            mode="fixture",
            run_id="unit-proactive-interaction",
            out_dir=tmp_path,
            foreground_runner=_foreground_runner,  # type: ignore[arg-type]
        )
    )

    with supervisor:
        event = supervisor.run_proactive_interaction_dry_run(user_reply="在啊，怎么啦？")
        final_snapshot = supervisor.snapshot()

    receipt_path = tmp_path / "delivery_receipts.jsonl"
    interaction_path = tmp_path / "proactive_interactions.jsonl"
    aftereffect_path = tmp_path / "proactive_aftereffects.jsonl"
    receipts = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    interactions = [json.loads(line) for line in interaction_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    aftereffects = [json.loads(line) for line in aftereffect_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert event["status"] == "PASS"
    assert event["delivery_receipt"]["delivery_status"] == "DELIVERED_DRY_RUN"
    assert event["aftereffect"]["outcome"] == "replied"
    assert event["aftereffect"]["reply_mode"] == "warm_ack"
    assert event["thalamus_intent_draft"]["route"] == "chat"
    assert event["thalamus_intent_draft"]["route_reason"] == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"
    assert event["foreground_turn"]["turn"]["mapped_organ_count"] == 23
    assert len(receipts) == 1
    assert receipts[0]["gateway_mode"] == "dry_run"
    assert len(interactions) == 1
    assert interactions[0]["status"] == "PASS"
    assert len(aftereffects) == 1
    assert interactions[0]["aftereffect_id"] == aftereffects[0]["aftereffect_id"]
    assert aftereffects[0]["outcome"] == "replied"
    assert aftereffects[0]["loneliness_delta"] < 0
    assert final_snapshot["foreground_turn_count"] == 1
    assert final_snapshot["proactive_emotional_episode"]["episode_count"] == 1
    assert final_snapshot["proactive_emotional_aftereffect"]["aftereffect_count"] == 1
    assert final_snapshot["proactive_emotional_aftereffect"]["last_outcome"] == "replied"
    assert final_snapshot["proactive_outbox"]["queued_count"] == 1
    assert final_snapshot["proactive_interaction"]["delivery_receipt_count"] == 1
    assert final_snapshot["proactive_interaction"]["interaction_count"] == 1
    assert final_snapshot["proactive_interaction"]["last_thalamus_reason_code"] == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"


def test_run_digital_life_host_runtime_parser_defaults() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "fixture"
    assert args.ticks == 1
    assert args.session_id == "digital-life-host-runtime"


def test_run_digital_life_host_runtime_writes_artifacts(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-host-runtime",
            "--ticks",
            "0",
        ]
    )

    result = run_host_runtime(args)

    summary = result["summary"]
    assert summary["status"] == "PASS"
    assert summary["alive"] is True
    assert summary["core_ticks_registered"] is True
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "snapshots.jsonl").exists()
    assert (tmp_path / "events.jsonl").exists()
    assert (tmp_path / "report.md").exists()
    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == "unit-host-runtime"


def test_run_digital_life_wall_clock_soak_writes_incremental_checkpoints(tmp_path) -> None:
    args = build_wall_clock_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-wall-clock-soak",
            "--duration-seconds",
            "1",
            "--tick-interval-seconds",
            "0",
            "--max-ticks",
            "1",
        ]
    )

    result = run_wall_clock_soak(args)

    summary = result["summary"]
    assert summary["run_status"] == "COMPLETED"
    assert summary["status"] == "PASS"
    assert summary["idle_tick_count"] == 1
    assert summary["checkpoint_count"] >= 2
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "checkpoints.jsonl").exists()
    assert (tmp_path / "report.md").exists()
    saved = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == "unit-wall-clock-soak"


def test_validate_proactive_interaction_loop_writes_artifacts(tmp_path) -> None:
    args = build_proactive_interaction_arg_parser().parse_args(
        [
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-proactive-interaction-validation",
        ]
    )

    result = run_proactive_interaction_validation(args)

    summary = result["summary"]
    assert summary["status"] == "PASS"
    assert summary["proactive_outbox_queued_count"] == 1
    assert summary["proactive_episode_count"] == 1
    assert summary["proactive_episode_send_allowed"] is True
    assert summary["proactive_aftereffect_count"] == 1
    assert summary["proactive_aftereffect_outcome"] == "replied"
    assert summary["delivery_receipt_count"] == 1
    assert summary["interaction_count"] == 1
    assert summary["thalamus_reason_code"] == "THALAMUS_RULE_PROACTIVE_REPLY_CONTINUATION"
    assert summary["mapped_organ_count"] == 23
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "state" / "delivery_receipts.jsonl").exists()
    assert (tmp_path / "state" / "proactive_interactions.jsonl").exists()
    assert (tmp_path / "state" / "proactive_aftereffects.jsonl").exists()
