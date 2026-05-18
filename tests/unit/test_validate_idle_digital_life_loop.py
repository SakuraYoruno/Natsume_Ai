from __future__ import annotations

import json

from tools.validate_idle_digital_life_loop import (
    build_arg_parser,
    run_idle_digital_life_loop_validation,
)


def test_idle_digital_life_loop_fixture_validates_private_continuity(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "fixture",
            "--out-dir",
            str(tmp_path),
            "--run-id",
            "unit-idle",
            "--ticks",
            "4",
            "--tick-minutes",
            "360",
            "--min-hours",
            "24",
        ]
    )

    result = run_idle_digital_life_loop_validation(args)

    summary = result["summary"]
    details = result["details"]
    assert summary["passed"] is True
    assert summary["tick_count"] == 4
    assert summary["simulated_hours"] == 24
    assert summary["surface_reply_count"] == 0
    assert summary["checks"]["idle_loop_has_no_surface_replies"] is True
    assert summary["checks"]["default_mode_uses_model_or_waits_without_template"] is True
    assert summary["checks"]["inner_world_appends_new_private_event_each_tick"] is True
    assert summary["checks"]["persona_state_uses_canonical_self_id"] is True
    assert summary["checks"]["persona_state_has_no_reserved_default_system_user1_files"] is True
    assert details["persona_self_id"] == "natsume_ai"
    assert details["state_files"]["self_model_state"] == ["natsume_ai_self_model.json"]
    assert details["state_files"]["inner_world_state"] == ["natsume_ai_inner_world.json"]

    saved_summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved_summary["passed"] is True
