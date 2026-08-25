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

    from host_brain.app.model_provider_composition import (
        invoke_model_provider_transport,
        resolve_model_provider_base_url,
        resolve_model_provider_credential,
    )
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from host_brain.model_runtime.production_composition import (
        build_default_production_model_runtime,
    )

    model_ledger: SQLiteLedger | None = None
    model_runtime: Any | None = None

    def _model_attempt_service() -> Any:
        nonlocal model_ledger, model_runtime
        if model_runtime is None:
            model_ledger = SQLiteLedger(db_path=":memory:")
            model_runtime = build_default_production_model_runtime(
                ledger=model_ledger,
                credential_resolver=resolve_model_provider_credential,
                base_url_resolver=resolve_model_provider_base_url,
                transport=invoke_model_provider_transport,
            )
        return model_runtime.attempt_service

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
            "resolve_expression_preferences": lambda **_kwargs: None,
            "resolve_active_identity": lambda **_kwargs: None,
        }
        values.update(overrides)
        return SurfaceRuntimePorts(**values)

    try:
        yield _build
    finally:
        if model_runtime is not None:
            model_runtime.attempt_service.clear_cache()
        if model_ledger is not None:
            model_ledger.close()


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
    import host_brain.app.route_catalog_runtime as route_mod
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

    monkeypatch.setattr(route_mod, "resolve_host_llm_runtime", _fake_runtime)
    monkeypatch.setattr(llm_mod, "astream_llm_chunks", _fake_astream)
