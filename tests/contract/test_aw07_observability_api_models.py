from __future__ import annotations

import ast
from pathlib import Path

from fastapi.routing import APIRoute

from host_brain.app import (
    obs_routes_core,
    obs_routes_health,
    obs_routes_trace,
)
from host_brain.app.obs_fastapi_app import app
from host_brain.observability import api_models


_ROUTE_MODULES = (
    obs_routes_core,
    obs_routes_health,
    obs_routes_trace,
)

_MODEL_EXPORTS = {
    obs_routes_core: (
        "GraphSchemaEdge",
        "GraphSchemaNode",
        "GraphSchemaResponse",
        "LiveTraceFeedItem",
        "LlmNodeItem",
        "LlmNodesResponse",
        "MetricsTimeseriesResponse",
        "SessionItem",
        "SessionsResponse",
        "StreamEventItem",
        "TimeseriesPoint",
        "TraceEdge",
        "TraceLiveFeedDelta",
        "TraceLiveFeedDeltaItem",
        "TraceLiveFeedNodeDelta",
        "TraceLiveFeedSnapshot",
        "TraceNode",
        "TraceResponse",
        "TraceStreamEventsResponse",
        "TribeEventItem",
        "TribeEventsResponse",
    ),
    obs_routes_health: (
        "ServiceHealthItem",
        "ServicesHealthResponse",
        "LlmNodeItem",
        "LlmNodesResponse",
    ),
    obs_routes_trace: (
        "GraphSchemaEdge",
        "GraphSchemaNode",
        "GraphSchemaResponse",
        "LiveTraceFeedItem",
        "StreamEventItem",
        "TraceEdge",
        "TraceLiveFeedDelta",
        "TraceLiveFeedDeltaItem",
        "TraceLiveFeedNodeDelta",
        "TraceLiveFeedSnapshot",
        "TraceNode",
        "TraceResponse",
        "TraceStreamEventsResponse",
        "TribeEventItem",
        "TribeEventsResponse",
    ),
}


def test_observability_dtos_have_one_class_owner_and_legacy_reexports() -> None:
    class_owners: dict[str, list[str]] = {}
    modules = (*_ROUTE_MODULES, api_models)
    for module in modules:
        source_path = Path(module.__file__ or "")
        syntax = ast.parse(source_path.read_text(encoding="utf-8-sig"))
        for node in syntax.body:
            if isinstance(node, ast.ClassDef):
                class_owners.setdefault(node.name, []).append(module.__name__)

    duplicates = {name: owners for name, owners in class_owners.items() if len(owners) > 1}
    assert duplicates == {}

    for route_module, names in _MODEL_EXPORTS.items():
        for name in names:
            assert getattr(route_module, name) is getattr(api_models, name)


def test_observability_route_keys_and_response_model_owners_are_unique() -> None:
    route_keys = [
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    ]
    assert len(route_keys) == len(set(route_keys))

    response_models = {
        route.path: route.response_model
        for route in app.routes
        if isinstance(route, APIRoute) and "GET" in (route.methods or set())
    }
    assert response_models["/obs/services/health"] is api_models.ServicesHealthResponse
    assert response_models["/obs/llm/nodes"] is api_models.LlmNodesResponse
    assert response_models["/obs/sessions"] is api_models.SessionsResponse
    assert response_models["/obs/metrics/timeseries"] is api_models.MetricsTimeseriesResponse
    assert response_models["/obs/trace/{request_id}"] is api_models.TraceResponse
    assert (
        response_models["/obs/trace/{request_id}/stream_events"]
        is api_models.TraceStreamEventsResponse
    )
    assert response_models["/obs/trace-feed/snapshot"] is api_models.TraceLiveFeedSnapshot
    assert response_models["/obs/tribe/events"] is api_models.TribeEventsResponse
    assert response_models["/obs/graph/schema"] is api_models.GraphSchemaResponse
