from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime, timedelta
from pathlib import Path


class _EmptyProjectionStore:
    @staticmethod
    def get(_kind, _key):
        return None

    @staticmethod
    def list(_kind):
        return []


def test_eight_host_gets_do_not_open_canonical_sources(monkeypatch) -> None:
    from host_brain.composition import process_services as dependencies
    from host_brain.app import digital_life_routes
    from host_brain.app import health_routes
    from host_brain.app import memory_routes
    from host_brain.app import web_ui

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET opened a canonical source")

    monkeypatch.setattr(
        dependencies, "peek_query_projection_store", lambda: _EmptyProjectionStore()
    )
    monkeypatch.setattr(dependencies, "get_ledger", forbidden)
    monkeypatch.setattr(digital_life_routes, "get_ledger", forbidden)
    monkeypatch.setattr(health_routes, "get_ledger", forbidden)
    monkeypatch.setattr(memory_routes, "get_ledger", forbidden)
    monkeypatch.setattr(web_ui, "get_ledger", forbidden)

    world = asyncio.run(
        digital_life_routes.digital_life_world_snapshot(
            person_id="default", session_id="session-1", fact_key=[]
        )
    )
    canon = asyncio.run(health_routes.canon_sync_health_check())
    startup = asyncio.run(health_routes.startup_report())
    context = asyncio.run(
        memory_routes.memory_context_debug(
            session_id="session-1",
            person_id="default",
            user_message="PRIVATE_QUERY_BODY",
            current_user_message="",
            limit=8,
            _authority=None,
        )
    )
    memory_health = asyncio.run(memory_routes.memory_health_route(person_id="default"))
    events = asyncio.run(
        memory_routes.memory_events_recent(
            person_id="default", session_id="", visibility="", limit=50
        )
    )
    now = datetime.now()
    status = web_ui.get_status_distribution(
        start_time=now - timedelta(hours=1),
        end_time=now,
        request_type=None,
        limit=100,
        offset=0,
    )
    topology = web_ui.get_link_topology(
        start_time=now - timedelta(hours=1),
        end_time=now,
        min_connections=1,
        limit=100,
        offset=0,
    )

    values = (world, canon.details, startup, context, memory_health, events, status, topology)
    for value in values:
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        elif hasattr(value, "dict"):
            value = value.dict()
        assert int(value["projection_version"]) == 0
        assert int(value["cursor"]) == 0
        assert int(value["freshness_ts_ms"]) == 0
        assert value["degraded_state"] == "REBUILDING"
    assert "PRIVATE_QUERY_BODY" not in str(context)
    assert context["content_withheld"] is True
    assert events["items"] == []


def test_assigned_get_handlers_are_projection_only_by_source_contract() -> None:
    from host_brain.app import digital_life_routes
    from host_brain.app import health_routes
    from host_brain.app import memory_routes
    from host_brain.app import web_ui

    handlers = (
        digital_life_routes.digital_life_world_snapshot,
        health_routes.canon_sync_health_check,
        health_routes.startup_report,
        memory_routes.memory_context_debug,
        memory_routes.memory_health_route,
        memory_routes.memory_events_recent,
        web_ui.get_status_distribution,
        web_ui.get_link_topology,
    )
    forbidden = (
        "get_ledger(",
        "_get_ledger(",
        "fetch_by_time_range(",
        "build_world_snapshot(",
        "build_memory_context_debug(",
        "memory_health(",
        "get_recent_memory_events(",
        "detect_world_version_drift(",
        "build_startup_report(",
        "collect_dependency_probes(",
        "refresh_remaining_query_projections(",
    )
    for handler in handlers:
        source = inspect.getsource(handler)
        for marker in forbidden:
            assert marker not in source, f"{handler.__name__} contains {marker}"


def test_all_app_decorated_gets_are_free_of_canonical_ledger_calls() -> None:
    app_root = Path(__file__).resolve().parents[2] / "host_brain" / "app"
    hits: list[str] = []
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance((decorator.func if isinstance(decorator, ast.Call) else decorator), ast.Attribute)
                and (decorator.func if isinstance(decorator, ast.Call) else decorator).attr == "get"
                for decorator in node.decorator_list
            ):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                callee = call.func
                name = (
                    callee.id
                    if isinstance(callee, ast.Name)
                    else callee.attr
                    if isinstance(callee, ast.Attribute)
                    else ""
                )
                if name in {"get_ledger", "_get_ledger"}:
                    hits.append(f"{path}:{node.name}:{call.lineno}:{name}")
    assert hits == []


def test_memory_context_debug_is_bound_to_existing_operator_scope() -> None:
    from host_brain.observability.operator_query_gateway import (
        required_operator_query_scope,
    )

    assert (
        required_operator_query_scope(
            method="GET", path="/v1/memory/context/debug"
        )
        == "obs:memory:read"
    )
