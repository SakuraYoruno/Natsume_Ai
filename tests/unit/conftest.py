"""测试共享辅助工具。"""
from __future__ import annotations

import json
from typing import Any


def parse_sse_commit(response) -> dict[str, Any]:
    """从 SSE 响应中解析最后一个 COMMIT 事件。"""
    content_type = str(response.headers.get("content-type", "")).lower()
    if "text/event-stream" in content_type or response.text.startswith("data:"):
        last_commit: dict[str, Any] = {}
        for line in response.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                    if isinstance(event, dict):
                        if event.get("phase") == "COMMIT":
                            last_commit = event
                        elif not last_commit:
                            last_commit = event
                except (json.JSONDecodeError, ValueError):
                    continue
        return last_commit
    try:
        return response.json()
    except Exception:
        return {}


def mock_llm_runtime(monkeypatch) -> None:
    """Mock LLM runtime helpers and a deterministic stream."""
    import host_brain.app.graph_config_builder as graph_mod
    import host_brain.integrations.llm_helpers as llm_mod

    def _fake_runtime(**kwargs):  # noqa: ANN001, ANN003
        return {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat-direct",
            "timeout_ms": 5000,
            "llm_api_key": None,
            "base_url_override": None,
            "provider_fn": None,
            "route_target": "host",
            "route_reason_code": "MOCK_PROVIDER",
            "route_detail": "mock provider for testing",
            "sys_mode": "normal",
        }

    mock_text = '{"narrative": "hello from natsume", "intent_draft": "IDLE", "meta": {"draft_only": true}}'

    async def _fake_astream(**kwargs):  # noqa: ANN001, ANN003
        for char in mock_text:
            yield char

    monkeypatch.setattr(graph_mod, "resolve_host_llm_runtime_async", _fake_runtime)
    monkeypatch.setattr(graph_mod, "resolve_host_llm_runtime", _fake_runtime)
    monkeypatch.setattr(llm_mod, "astream_llm_chunks", _fake_astream)
