from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations import llm_helpers as helpers


def test_complete_llm_with_pool_falls_back_and_uses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_TIER_A", "gemini:gemini-pro,minimax:minimax-her")
    monkeypatch.setenv("HOST_LLM_POOL_CACHE_ENABLED", "true")
    monkeypatch.setenv("HOST_LLM_POOL_CACHE_TTL_MS", "60000")
    monkeypatch.setenv("HOST_LLM_POOL_RATE_LIMIT_COOLDOWN_MS", "12345")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")

    helpers._POOL_COMPLETION_CACHE.clear()
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    ledger = SQLiteLedger(":memory:")
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)

    calls: list[tuple[str, str]] = []

    def _fake_invoke(**kwargs):
        provider_name = str(kwargs.get("provider_name") or "")
        model_id = str(kwargs.get("model_id") or "")
        calls.append((provider_name, model_id))
        if provider_name == "gemini":
            raise RuntimeError("429 rate limit")
        return '{"route":"chat","confidence":0.8}'

    monkeypatch.setattr(helpers, "invoke_llm_text", _fake_invoke)

    result = helpers.complete_llm_with_pool_sync(
        request_id="req-pool-1",
        scene_type="CHAT",
        cognitive_tier="A",
        timeout_ms=1500,
        messages=[{"role": "user", "content": "hello"}],
        response_format="json_object",
    )

    assert result.pool_exhausted is False
    assert result.final_provider == "minimax"
    assert result.cached is False
    assert len(result.attempts) == 2
    assert result.attempts[0].reason_code == "POOL_RATE_LIMITED"
    assert (
        helpers._get_cooldown_remaining_ms(
            provider_name="gemini",
            model_id="gemini-pro",
            scene_type="CHAT",
    )
        > 0
    )
    assert ledger.llm_pool_call_log[-1]["final_provider"] == "minimax"
    llm_node_rows = ledger.fetch_by_request_id("llm_node_call_log", "req-pool-1")
    assert any(str(row.get("node_id")) == "llm_pool:chat" for row in llm_node_rows)

    monkeypatch.setattr(
        helpers,
        "invoke_llm_text",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError(f"unexpected provider call: {kwargs}")),
    )

    cached = helpers.complete_llm_with_pool_sync(
        request_id="req-pool-1",
        scene_type="CHAT",
        cognitive_tier="A",
        timeout_ms=1500,
        messages=[{"role": "user", "content": "hello"}],
        response_format="json_object",
    )

    assert cached.cached is True
    assert cached.final_provider == "minimax"
    assert calls == [("gemini", "gemini-pro"), ("minimax", "minimax-her")]


@pytest.mark.asyncio
async def test_astream_llm_chunks_with_pool_switches_before_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_LLM_POOL_TIER_A", "gemini:gemini-pro,minimax:minimax-her")
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-key")

    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    ledger = SQLiteLedger(":memory:")
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)

    async def _fake_astream(**kwargs):
        provider_name = str(kwargs.get("provider_name") or "")
        if provider_name == "gemini":
            raise RuntimeError("timeout waiting upstream")
        yield "he"
        yield "llo"

    monkeypatch.setattr(helpers, "astream_llm_chunks", _fake_astream)

    chunks: list[str] = []
    async for chunk in helpers.astream_llm_chunks_with_pool(
        request_id="req-stream-1",
        scene_type="CHAT",
        cognitive_tier="A",
        timeout_ms=1500,
        messages=[{"role": "user", "content": "hello"}],
    ):
        chunks.append(chunk)

    assert "".join(chunks) == "hello"
    assert ledger.llm_pool_call_log[-1]["final_provider"] == "minimax"
    llm_node_rows = ledger.fetch_by_request_id("llm_node_call_log", "req-stream-1")
    assert any(str(row.get("node_id")) == "llm_pool:chat" for row in llm_node_rows)


def test_pool_cooldown_is_scoped_per_scene_type() -> None:
    helpers._POOL_COOLDOWN_UNTIL_MS.clear()

    helpers._set_cooldown_ms(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        scene_type="HIPPOCAMPUS_RECALL",
        cooldown_ms=30_000,
    )

    assert (
        helpers._get_cooldown_remaining_ms(
            provider_name="gemini",
            model_id="gemini-2.5-pro",
            scene_type="HIPPOCAMPUS_RECALL",
        )
        > 0
    )
    assert (
        helpers._get_cooldown_remaining_ms(
            provider_name="gemini",
            model_id="gemini-2.5-pro",
            scene_type="CHAT",
        )
        == 0
    )
