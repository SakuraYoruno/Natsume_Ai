"""测试共享辅助工具。"""
from __future__ import annotations

import json
from typing import Any


def parse_sse_commit(response) -> dict[str, Any]:
    """从 SSE 流响应中解析最后一个 COMMIT 事件的 JSON 数据。"""
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
    """Mock resolve_host_llm_runtime 和 astream_llm_chunks，避免真实 LLM 调用。

    - resolve_host_llm_runtime 返回 mock runtime dict
    - astream_llm_chunks 返回固定的 mock 文本片段（JSON 格式，符合 airlock 解析要求）
    """
    import host_brain.app.chat_stream_handler as stream_mod

    def _fake_runtime(**kwargs):  # noqa: ANN001, ANN003
        return {
            "provider_name": "mock",
            "model_id": "mock-chat-direct",
            "timeout_ms": 5000,
            "api_key": None,
            "available": True,
            "provider_fn": None,
            "route_target": "host",
            "route_reason_code": "MOCK_PROVIDER",
            "route_detail": "mock provider for testing",
            "sys_mode": "normal",
        }

    # mock 文本：符合 airlock quarantine_ingress 期望的 JSON 格式
    _MOCK_LLM_TEXT = '{"narrative": "hello from natsume", "intent_draft": "IDLE", "meta": {"draft_only": true}}'

    async def _fake_astream(**kwargs):  # noqa: ANN001, ANN003
        for char in _MOCK_LLM_TEXT:
            yield char

    monkeypatch.setattr(stream_mod, "resolve_host_llm_runtime", _fake_runtime)
    monkeypatch.setattr(stream_mod, "astream_llm_chunks", _fake_astream)
