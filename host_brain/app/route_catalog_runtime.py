"""Compatibility projection from the canonical secret-free RouteCatalog."""

from __future__ import annotations

from typing import Any


def _project(route: dict[str, Any], *, cloud: dict[str, Any] | None = None) -> dict[str, Any]:
    cloud_route = dict(cloud or route)
    return {
        **route,
        "chat_provider_name": route["provider_name"],
        "chat_model_id": route["model_id"],
        "chat_timeout_ms": route["timeout_ms"],
        "chat_available": route["available"],
        "cloud_provider_name": cloud_route["provider_name"],
        "cloud_model_id": cloud_route["model_id"],
        "cloud_timeout_ms": cloud_route["timeout_ms"],
        "cloud_available": cloud_route["available"],
    }


def resolve_host_llm_runtime(
    *,
    catalog: Any,
    scene_type: str | None = None,
) -> dict[str, Any]:
    """Project one explicitly owned RouteCatalog into the Host LLM shape."""

    scene = str(scene_type or "CHAT").strip().upper() or "CHAT"
    if not callable(getattr(catalog, "resolve", None)):
        raise RuntimeError("HOST_MODEL_ROUTE_CATALOG_UNAVAILABLE")
    route = catalog.resolve(scene_type=scene, cognitive_tier="B", prompt_id="")
    cloud = catalog.resolve(scene_type="MULTIMODAL", cognitive_tier="B", prompt_id="")
    return _project(route, cloud=cloud)


def resolve_surface_reply_llm_runtime(
    *,
    catalog: Any,
    base_runtime: dict,
) -> dict[str, Any]:
    runtime_path = str(
        dict(base_runtime or {}).get("runtime_path") or ""
    ).strip().lower()
    scene_type = (
        "CHAT_SURFACE_REALTIME"
        if runtime_path == "realtime_companion"
        else "CHAT_SURFACE"
    )
    route = _resolve_catalog_route(
        catalog=catalog,
        scene_type=scene_type,
        cognitive_tier="B",
        prompt_id="surface.main_chain.live.text",
    )
    return {**_project(route), "agent_role": "surface_reply"}


def resolve_executive_router_llm_runtime(
    *,
    catalog: Any,
) -> dict[str, Any]:
    route = _resolve_catalog_route(
        catalog=catalog,
        scene_type="EXEC_INTENT",
        cognitive_tier="C",
        prompt_id="thalamus.intent_router.v1",
    )
    return {**_project(route), "agent_role": "executive_router"}


def resolve_subagent_llm_runtime(
    *,
    catalog: Any,
) -> dict[str, Any]:
    route = _resolve_catalog_route(
        catalog=catalog,
        scene_type="SUBAGENT",
        cognitive_tier="B",
        prompt_id="agent.subagent.gateway.text",
    )
    return {**_project(route), "agent_role": "sub_agent"}


def _resolve_catalog_route(
    *,
    catalog: Any,
    scene_type: str,
    cognitive_tier: str,
    prompt_id: str,
) -> dict[str, Any]:
    if not callable(getattr(catalog, "resolve", None)):
        raise RuntimeError("HOST_MODEL_ROUTE_CATALOG_UNAVAILABLE")
    return catalog.resolve(
        scene_type=scene_type,
        cognitive_tier=cognitive_tier,
        prompt_id=prompt_id,
    )


__all__ = [
    "resolve_executive_router_llm_runtime",
    "resolve_host_llm_runtime",
    "resolve_subagent_llm_runtime",
    "resolve_surface_reply_llm_runtime",
]
