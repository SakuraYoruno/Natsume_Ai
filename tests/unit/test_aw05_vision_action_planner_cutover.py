from __future__ import annotations

import pytest

from host_brain.app.vision import action_planner


@pytest.mark.asyncio
async def test_vision_action_planner_model_entry_is_retired() -> None:
    with pytest.raises(
        RuntimeError,
        match=action_planner.VISUAL_AGENT_GOVERNED_ADMISSION_REQUIRED,
    ):
        await action_planner.generate_action_plan(
            request_id="req-action-aw05",
            session_id="session-action-aw05",
            goal="点击登录按钮",
            vision_context={"screen_description": "登录按钮位于右下角"},
            llm_runtime={
                "provider_name": "forged",
                "llm_api_key": "secret",
                "available": True,
                "timeout_ms": 5_000,
            },
        )
