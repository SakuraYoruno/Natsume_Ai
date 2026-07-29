from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.requests import Request

from host_brain.app.chat_handler import (
    _resolve_injected_sys_mode,
    handle_chat,
)
from host_brain.app.lifecycle_runtime_composition import (
    build_lifecycle_runtime_application_ports,
)
from host_brain.app.schemas import ChatRequest
from host_brain.lifecycle_runtime.ports import LifecycleRuntimeApplicationPorts
from host_brain.lifecycle_runtime.runtime_snapshot import (
    build_digital_life_runtime_snapshot,
)


ROOT = Path(__file__).resolve().parents[2]


class _Metrics:
    def inc_chat(self) -> None:
        return None


class _EmptyEmotionTimeline:
    def rebuild_summary(self, *, window_ms: int) -> dict[str, Any]:
        del window_ms
        return {}

    def build_daily_diary(self, *, window_ms: int) -> dict[str, Any]:
        del window_ms
        return {}


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat",
            "headers": [],
            "state": {},
        }
    )


def test_lifecycle_composition_preserves_injected_hard_state_owner() -> None:
    hard_state = SimpleNamespace(sys_mode="focus")

    def hard_state_provider() -> Any:
        return hard_state

    def endocrine_state_provider(**_kwargs: Any) -> dict[str, Any]:
        return {"source": "app-owner"}

    ports = build_lifecycle_runtime_application_ports(
        hard_state_provider=hard_state_provider,
        endocrine_state_provider=endocrine_state_provider,
    )

    assert ports.get_hard_state is hard_state_provider
    assert ports.get_endocrine_state is endocrine_state_provider
    assert ports.get_hard_state() is hard_state
    assert ports.require_canonical_life_snapshot is False


def test_missing_hard_state_owner_is_compatible_to_compose_but_fails_closed() -> None:
    ports = build_lifecycle_runtime_application_ports()

    with pytest.raises(RuntimeError, match="HOST_HARD_STATE_OWNER_NOT_INJECTED"):
        ports.get_hard_state()
    with pytest.raises(RuntimeError, match="HOST_HARD_STATE_OWNER_NOT_INJECTED"):
        ports.get_endocrine_state()


def test_chat_mode_resolver_rejects_missing_invalid_and_failed_owner() -> None:
    assert _resolve_injected_sys_mode(None) == (
        "maintenance",
        "SYS_MODE_OWNER_UNAVAILABLE",
    )
    assert _resolve_injected_sys_mode(lambda: {"sys_mode": "invalid"}) == (
        "maintenance",
        "SYS_MODE_OWNER_INVALID",
    )

    def failed_owner() -> Any:
        raise RuntimeError("owner unavailable")

    assert _resolve_injected_sys_mode(failed_owner) == (
        "maintenance",
        "SYS_MODE_OWNER_UNAVAILABLE",
    )


@pytest.mark.asyncio
async def test_chat_locked_gate_reads_injected_owner_not_live_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_SYS_MODE", "normal")
    response = await handle_chat(
        request_body=ChatRequest(message="hello"),
        request=_request(),
        metrics=_Metrics(),
        hard_state_provider=lambda: SimpleNamespace(sys_mode="locked"),
    )
    chunk = await response.body_iterator.__anext__()
    text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
    event = json.loads(
        next(line[6:] for line in text.splitlines() if line.startswith("data: "))
    )

    assert event["decision"] == "REJECT"
    assert event["reason_code"] == "SYS_MODE_LOCKED"


def test_runtime_snapshot_has_no_environment_fallback() -> None:
    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("not available")

    ports = LifecycleRuntimeApplicationPorts(
        get_activity_monitor=lambda: None,
        get_endocrine_state=unavailable,
        get_episodic_store=unavailable,
        get_hard_state=unavailable,
        get_ledger=unavailable,
        get_life_memory_store=unavailable,
        get_model_attempt_service=unavailable,
        emotion_timeline=_EmptyEmotionTimeline(),
        get_user_emotion_profile=lambda: {},
    )

    snapshot = build_digital_life_runtime_snapshot(ports=ports)

    assert snapshot["sys_mode"] == "maintenance"
    assert snapshot["slot_status"]["hard_state"] == "degraded"
    source = inspect.getsource(build_digital_life_runtime_snapshot)
    assert "HOST_SYS_MODE" not in source
    assert "_os.environ" not in source


def test_production_composition_uses_one_host_owner_for_all_consumers() -> None:
    asgi_source = (ROOT / "host_brain/app/asgi.py").read_text(encoding="utf-8")
    tribe_source = (ROOT / "host_brain/app/tribe_composition.py").read_text(
        encoding="utf-8"
    )
    lifecycle_source = (
        ROOT / "host_brain/app/lifecycle_runtime_composition.py"
    ).read_text(encoding="utf-8")

    assert "hard_state_provider=host_lifecycle.get_hard_state" in asgi_source
    assert "endocrine_state_provider=host_lifecycle.get_endocrine_state" in asgi_source
    assert "return str(host_lifecycle.get_hard_state().sys_mode)" in asgi_source
    assert "hard_state=process_services.get_hard_state" not in tribe_source
    assert "get_hard_state=hard_state_provider or process_services" not in lifecycle_source
    assert "get_endocrine_state=process_services" not in lifecycle_source


def test_obs_life_body_only_consumes_durable_runtime_projection() -> None:
    source = (ROOT / "host_brain/app/obs_routes_life_body.py").read_text(
        encoding="utf-8"
    )
    helper_source = (
        ROOT / "host_brain/app/http_hooks_middleware.py"
    ).read_text(encoding="utf-8")

    assert "build_lifecycle_runtime_application_ports()" not in source
    assert "runtime_document, runtime_projection = read_process_projection(" in source
    assert "def build_scheduler_emotion_state" not in helper_source
