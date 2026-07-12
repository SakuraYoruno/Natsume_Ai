from __future__ import annotations

from tools.validate_digital_life_background_runtime import (
    build_arg_parser,
    run_background_runtime_validation,
)


def test_retired_private_background_validator_fails_closed(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--out-dir", str(tmp_path), "--run-id", "unit"])

    result = run_background_runtime_validation(args)

    summary = result["summary"]
    checks = summary["checks"]
    assert summary["passed"] is False
    assert summary["evaluation_mode"] == "retired_standalone_scheduler_validation"
    assert summary["reason_code"] == "GOVERNED_SCHEDULER_SERVICE_REQUIRED"
    assert summary["user_id"] == "natsume_ai"
    assert summary["persona_self_id"] == "natsume_ai"
    assert summary["core_task_count"] == 5
    assert summary["registered_task_count"] == 0
    assert summary["is_advancing"] is False
    assert summary["private_thought_count"] == 0
    assert summary["self_update_count"] == 0
    assert checks == {
        "legacy_scheduler_retired": True,
        "governed_scheduler_service_required": True,
    }
