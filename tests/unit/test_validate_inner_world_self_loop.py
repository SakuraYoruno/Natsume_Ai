from __future__ import annotations

import asyncio

from tools.validate_inner_world_self_loop import (
    build_arg_parser,
    run_inner_world_self_loop_validation,
)


def test_validate_inner_world_self_loop_fixture(tmp_path) -> None:
    args = build_arg_parser().parse_args(["--out-dir", str(tmp_path), "--run-id", "unit"])

    result = asyncio.run(run_inner_world_self_loop_validation(args))

    summary = result["summary"]
    assert summary["passed"] is False
    assert summary["evaluation_mode"] == "contract_validation_no_text_quality_scoring"
    assert summary["turn_count"] == 4
    assert summary["private_thought_count"] == 4
    checks = summary["checks"]
    assert checks["breaker_opens_when_model_unavailable"] is True
    assert checks["inner_world_uses_llm_source_only"] is True
    assert checks["surface_selection_is_direct_no_score"] is True
    assert checks["surface_uses_recalled_memory"] is False
