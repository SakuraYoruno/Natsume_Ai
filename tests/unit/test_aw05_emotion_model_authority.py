from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.app.emotion_llm_inference import _llm_emotion_call


@pytest.mark.asyncio
async def test_emotion_inference_uses_async_canonical_attempt_without_model_override(monkeypatch) -> None:
    captured = {}

    async def _complete(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(success=True, content='{"user_emotion":"CALM"}', error_code=None)

    monkeypatch.setattr(
        "host_brain.model_runtime.invocation.complete_compiled_prompt",
        _complete,
    )

    text = await _llm_emotion_call(
        system_prompt="system",
        user_prompt="user",
        timeout_ms=321,
    )

    assert text == '{"user_emotion":"CALM"}'
    assert captured["scene_type"] == "EMOTION"
    assert captured["deadline_ms"] == 321
    assert "model_id" not in captured
    assert "provider" not in captured
    assert "max_attempts" not in captured
