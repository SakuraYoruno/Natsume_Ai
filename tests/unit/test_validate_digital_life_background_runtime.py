from __future__ import annotations

from tools.validate_digital_life_background_runtime import (
    build_arg_parser,
    run_background_runtime_validation,
)


def test_validate_digital_life_background_runtime_fixture(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--out-dir", str(tmp_path), "--run-id", "unit"])

    result = run_background_runtime_validation(args)

    summary = result["summary"]
    checks = summary["checks"]
    assert summary["passed"] is True
    assert summary["evaluation_mode"] == "contract_validation_no_text_quality_scoring"
    assert summary["user_id"] == "natsume_ai"
    assert summary["persona_self_id"] == "natsume_ai"
    assert summary["core_task_count"] == 5
    assert summary["registered_task_count"] == 5
    assert summary["is_advancing"] is True
    assert summary["private_thought_count"] >= 1
    assert summary["self_update_count"] >= 1
    assert checks["heavy_background_disabled"] is True
    assert checks["persona_state_uses_canonical_self_id"] is True
    assert checks["persona_state_has_no_reserved_default_system_user1_files"] is True
    assert checks["continuous_core_ticks_enabled"] is True
    assert checks["core_only_scheduler_registry"] is True
    assert checks["heavy_background_tasks_absent"] is True
    assert checks["private_thought_source_is_llm"] is True
    assert checks["default_mode_inline_tick_ran"] is True
    assert checks["self_model_updated_by_background_tick"] is True
