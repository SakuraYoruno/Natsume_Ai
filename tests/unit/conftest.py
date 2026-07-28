"""测试共享辅助工具。"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.surface_runtime.ports import SurfaceRuntimePorts


@pytest.fixture()
def surface_runtime_ports_factory():
    """Build explicit, side-effect-free Surface ports for unit tests."""

    def _model_attempt_service() -> Any:
        from host_brain.composition.process_services import (  # noqa: PLC0415
            get_model_attempt_service,
        )

        return get_model_attempt_service()

    def _build(**overrides: Any) -> SurfaceRuntimePorts:
        values: dict[str, Any] = {
            "get_ledger": lambda: SimpleNamespace(canonical_db_path=":memory:"),
            "get_life_command_service": lambda: SimpleNamespace(),
            "get_life_snapshot_reader": lambda: SimpleNamespace(),
            # Unit callers that only exercise the legacy direct-Surface seam
            # deliberately omit the new accepted-response lifecycle.  The
            # production composition always injects the real service; the
            # DL-03 integration tests cover that durable path explicitly.
            "get_relationship_lifecycle_service": lambda: None,
            "get_persistence_settings": lambda: SimpleNamespace(canonical_db_path=":memory:"),
            "build_surface_context_packet": lambda **_kwargs: {},
            "build_user_preference_pack": lambda **_kwargs: {},
            "get_conversation_history": lambda: SimpleNamespace(
                fetch_for_messages=lambda **_kwargs: []
            ),
            "build_conversation_experience_read_pack": lambda **_kwargs: {
                "previous_belief": {},
                "experience_lessons": [],
            },
            "build_conversation_understanding_pack": lambda **_kwargs: {},
            "build_memory_context_debug": lambda **_kwargs: {},
            "resolve_surface_reply_llm_runtime": lambda **kwargs: {
                **dict(kwargs.get("base_runtime") or {}),
                "agent_role": "surface_reply",
            },
            "model_attempt_service": _model_attempt_service,
        }
        values.update(overrides)
        return SurfaceRuntimePorts(**values)

    return _build


@pytest.fixture()
def pentest_guard():
    """为需要 side-effect executor 的测试安装一个宽松的全局 ScopeGuard。

    测试结束时自动 reset_guard()，保证单测隔离。
    """
    from host_brain.pentest.executor_guard import init_guard, reset_guard
    from host_brain.pentest.rate_limiter import RateLimiter
    from host_brain.pentest.scope import ScopeGuard

    scope = ScopeGuard()
    scope.set_scope(["127.0.0.1", "127.0.0.2", "10.0.0.0/8", "192.168.0.0/16"])
    init_guard(scope, RateLimiter(), strict=False)
    yield
    reset_guard()


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
