from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from host_brain.app import app_factory, asgi
from host_brain.app.obs_route_ports import ObsRoutePorts
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.app.chat_immune_gate import (
    log_chat_immune_gate_event,
    schedule_chat_immune_gate_event,
)
from host_brain.app.chat_routes import handle_v1_chat_route
from host_brain.app.runtime_metrics import RuntimeMetrics, build_metrics_response
from host_brain.app.schemas import ChatRequest
from host_brain.app.server_config import resolve_uvicorn_access_log_enabled
from host_brain.app.sse_response import sse_immediate_events


class _Logger:
    def __init__(self) -> None:
        self.debug_messages: list[str] = []
        self.warning_messages: list[str] = []
        self.info_messages: list[str] = []

    def debug(self, message: str, *args) -> None:  # noqa: ANN002
        self.debug_messages.append(message % args if args else message)

    def warning(self, message: str, *args) -> None:  # noqa: ANN002
        self.warning_messages.append(message % args if args else message)

    def info(self, message: str, *args) -> None:  # noqa: ANN002
        self.info_messages.append(message % args if args else message)


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/v1/chat", "headers": [], "state": {}})


def test_server_config_access_log_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_FASTAPI_APP_UVICORN_ACCESS_LOG_ENABLED", raising=False)
    assert resolve_uvicorn_access_log_enabled() is False

    monkeypatch.setenv("HOST_FASTAPI_APP_UVICORN_ACCESS_LOG_ENABLED", "yes")
    assert resolve_uvicorn_access_log_enabled() is True

    monkeypatch.setenv("HOST_FASTAPI_APP_UVICORN_ACCESS_LOG_ENABLED", "off")
    assert resolve_uvicorn_access_log_enabled() is False


def test_sse_immediate_events_serializes_events() -> None:
    response = sse_immediate_events([{"phase": "ACK", "text": "你好"}, {"phase": "DONE"}])

    assert response.media_type == "text/event-stream"
    body = response.body.decode("utf-8")
    assert 'data: {"phase": "ACK", "text": "你好"}' in body
    assert 'data: {"phase": "DONE"}' in body


def test_runtime_metrics_error_counter_is_reported() -> None:
    metrics = RuntimeMetrics()
    metrics.inc_request()
    metrics.inc_chat()
    metrics.inc_rejection("BLOCKED")
    metrics.inc_error()

    response = build_metrics_response(metrics)

    assert response.total_errors == 1
    assert response.total_requests == 1
    assert response.reason_code_distribution == {"BLOCKED": 1}


def test_immune_audit_handles_missing_or_failing_ledger() -> None:
    logger = _Logger()
    request = ChatRequest(message="hello")

    log_chat_immune_gate_event(
        request_id="req-1",
        request_body=request,
        immune_result={"decision": "REPORT"},
        get_ledger=lambda: None,
        logger=logger,  # type: ignore[arg-type]
    )
    log_chat_immune_gate_event(
        request_id="req-2",
        request_body=request,
        immune_result={"decision": "REPORT"},
        get_ledger=lambda: (_ for _ in ()).throw(RuntimeError("ledger down")),
        logger=logger,  # type: ignore[arg-type]
    )

    assert logger.debug_messages == []


def test_immune_audit_report_status_and_log_failure() -> None:
    logger = _Logger()
    events: list[dict] = []

    class _Ledger:
        def log_voice_event(self, event: dict) -> None:
            events.append(event)

    log_chat_immune_gate_event(
        request_id="req-report",
        request_body=ChatRequest(message="hello", session_id="s1", person_id="p1"),
        immune_result={"decision": "REPORT", "risk_level": "medium", "score": 0.6, "reason_codes": ["RISK"]},
        get_ledger=lambda: _Ledger(),
        logger=logger,  # type: ignore[arg-type]
    )
    assert events[-1]["status"] == "DEGRADED"
    assert events[-1]["reason_code"] is None

    class _FailingLedger:
        def log_voice_event(self, _event: dict) -> None:
            raise RuntimeError("write failed")

    log_chat_immune_gate_event(
        request_id="req-fail",
        request_body=ChatRequest(message="hello"),
        immune_result={"decision": "BLOCK"},
        get_ledger=lambda: _FailingLedger(),
        logger=logger,  # type: ignore[arg-type]
    )
    assert any("audit skipped" in message for message in logger.debug_messages)


def test_schedule_immune_audit_logs_schedule_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = _Logger()

    def _raise_to_thread(*_args, **_kwargs):
        raise RuntimeError("loop closed")

    monkeypatch.setattr(asyncio, "to_thread", _raise_to_thread)
    schedule_chat_immune_gate_event(
        request_id="req",
        request_body=ChatRequest(message="hello"),
        immune_result={"decision": "ALLOW"},
        get_ledger=lambda: None,
        logger=logger,  # type: ignore[arg-type]
    )

    assert any("schedule skipped" in message for message in logger.debug_messages)


@pytest.mark.asyncio
async def test_chat_route_allows_when_notify_and_immune_evaluate_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = _Logger()
    scheduled: list[dict] = []
    captured: dict = {}

    def _notify_raises() -> None:
        raise RuntimeError("notify unavailable")

    async def _handle_chat(**kwargs):  # noqa: ANN003
        captured.update(kwargs)

        async def _events():
            yield 'data: {"phase":"COMMIT","decision":"APPROVE"}\n\n'

        return StreamingResponse(_events(), media_type="text/event-stream")

    monkeypatch.setattr("host_brain.composition.process_services.notify_user_interaction", _notify_raises)

    response = await handle_v1_chat_route(
        request_body=ChatRequest(message="hello"),
        request=_request(),
        metrics=RuntimeMetrics(),
        logger=logger,  # type: ignore[arg-type]
        evaluate_immune_gate_fn=lambda _body: (_ for _ in ()).throw(RuntimeError("immune down")),
        schedule_immune_gate_fn=lambda **kwargs: scheduled.append(dict(kwargs)),
        handle_chat_fn=_handle_chat,
    )

    assert response.media_type == "text/event-stream"
    assert captured["request_body"].message == "hello"
    trace = dict(getattr(captured["request"], "state").bio_ops_runtime_trace or {})
    assert trace["proposal_only"] is True
    assert trace["active_silence"]["silence_policy"] in {"normal_presence", "quiet_presence"}
    assert scheduled[-1]["immune_result"]["decision"] == "ALLOW"
    assert scheduled[-1]["immune_result"]["reason_codes"] == ["IMMUNE_EVALUATE_DEGRADED"]


@pytest.mark.asyncio
async def test_chat_route_fails_closed_when_immune_agent_port_is_missing() -> None:
    logger = _Logger()
    scheduled: list[dict] = []

    async def _must_not_handle_chat(**_kwargs):  # noqa: ANN003
        raise AssertionError("missing immune authority must reject before chat")

    response = await handle_v1_chat_route(
        request_body=ChatRequest(message="hello"),
        request=_request(),
        metrics=RuntimeMetrics(),
        logger=logger,  # type: ignore[arg-type]
        evaluate_immune_gate_fn=lambda _body: (_ for _ in ()).throw(
            RuntimeError("HOST_TRIBE_IMMUNE_AGENT_NOT_INJECTED")
        ),
        schedule_immune_gate_fn=lambda **kwargs: scheduled.append(dict(kwargs)),
        handle_chat_fn=_must_not_handle_chat,
    )

    body = "".join([chunk async for chunk in response.body_iterator])
    assert '"reason_code": "IMMUNE_GATE_UNAVAILABLE"' in body
    assert scheduled[-1]["immune_result"] == {
        "decision": "BLOCK",
        "reason_codes": ["IMMUNE_GATE_UNAVAILABLE"],
    }


@pytest.mark.asyncio
async def test_app_factory_exception_handler_uses_injected_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    logger = _Logger()
    seen: dict = {}

    async def _global_exception_handler(*, request: Request, exc: Exception, logger) -> JSONResponse:  # noqa: ANN001
        seen["logger"] = logger
        seen["exc"] = exc
        return JSONResponse({"ok": False}, status_code=500)

    monkeypatch.setattr(app_factory, "_global_exception_handler_fn", _global_exception_handler)

    app = app_factory.create_host_fastapi_app(
        lifespan=None,
        metrics=RuntimeMetrics(),
        logger=logger,  # type: ignore[arg-type]
        get_ledger=lambda: None,
        get_tool_broker=lambda: None,
        get_tool_catalog=lambda: None,
        get_bond_engine=lambda: None,
        chat_endpoint=lambda _body, _request: Response(),
        edge_events_endpoint=lambda _body: None,  # type: ignore[arg-type]
        root_endpoint=lambda: Response(),
        canon_service_provider=lambda: object(),
        obs_route_ports=ObsRoutePorts(
            query_projection_store_provider=lambda: type(
                "EmptyProjectionStore",
                (),
                {"get": lambda *_args, **_kwargs: None},
            )(),
        ),
        input_runtime_ports=build_input_runtime_application_ports(),
    )
    handler = app.exception_handlers[Exception]
    request = Request({"type": "http", "method": "GET", "path": "/boom", "headers": []})
    response = await handler(request, RuntimeError("boom"))

    assert response.status_code == 500
    assert seen["logger"] is logger
    assert isinstance(seen["exc"], RuntimeError)


@pytest.mark.asyncio
async def test_asgi_entrypoint_is_clean_production_app() -> None:
    route_paths = {getattr(route, "path", "") for route in asgi.app.routes}

    assert "/v1/chat" in route_paths
    assert "/v1/edge/events" in route_paths
    assert "/metrics" in route_paths
    assert set(asgi.__all__) == {"app", "create_app"}

    response = await asgi.root_endpoint()
    assert response.status_code == 307
