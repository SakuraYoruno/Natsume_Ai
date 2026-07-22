"""Compatibility projection from the canonical secret-free RouteCatalog."""

from __future__ import annotations

from typing import Any


def _catalog():
    # Lazy import avoids the app dependency/graph-builder import cycle while
    # still consuming the exact catalog installed by production composition.
    from host_brain.composition.process_services import get_model_route_catalog  # noqa: PLC0415

    return get_model_route_catalog()


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
    rollout_key: str | None = None,
    scene_type: str | None = None,
) -> dict[str, Any]:
    """Resolve the legacy compatibility projection through the process catalog."""

    return resolve_host_llm_runtime_from_catalog(
        catalog=_catalog(),
        rollout_key=rollout_key,
        scene_type=scene_type,
    )


def resolve_host_llm_runtime_from_catalog(
    *,
    catalog: Any,
    rollout_key: str | None = None,
    scene_type: str | None = None,
) -> dict[str, Any]:
    """Project one explicitly owned RouteCatalog into the Host LLM shape."""

    del rollout_key
    scene = str(scene_type or "CHAT").strip().upper() or "CHAT"
    if not callable(getattr(catalog, "resolve", None)):
        raise RuntimeError("HOST_MODEL_ROUTE_CATALOG_UNAVAILABLE")
    route = catalog.resolve(scene_type=scene, cognitive_tier="B", prompt_id="")
    cloud = catalog.resolve(scene_type="MULTIMODAL", cognitive_tier="B", prompt_id="")
    return _project(route, cloud=cloud)


async def resolve_host_llm_runtime_async(
    *,
    rollout_key: str | None = None,
    scene_type: str | None = None,
) -> dict[str, Any]:
    return resolve_host_llm_runtime(rollout_key=rollout_key, scene_type=scene_type)


async def resolve_host_llm_runtime_async_from_catalog(
    *,
    catalog: Any,
    rollout_key: str | None = None,
    scene_type: str | None = None,
) -> dict[str, Any]:
    """Async adapter for an app-local RouteCatalog provider."""

    return resolve_host_llm_runtime_from_catalog(
        catalog=catalog,
        rollout_key=rollout_key,
        scene_type=scene_type,
    )


def resolve_surface_reply_llm_runtime(*, base_runtime: dict) -> dict[str, Any]:
    del base_runtime
    route = _catalog().resolve(
        scene_type="CHAT_SURFACE",
        cognitive_tier="B",
        prompt_id="surface.main_chain.live.text",
    )
    return {**_project(route), "agent_role": "surface_reply"}


def resolve_executive_router_llm_runtime(*, base_runtime: dict) -> dict[str, Any]:
    del base_runtime
    route = _catalog().resolve(
        scene_type="EXEC_INTENT",
        cognitive_tier="C",
        prompt_id="thalamus.intent_router.v1",
    )
    return {**_project(route), "agent_role": "executive_router"}


def resolve_subagent_llm_runtime(*, base_runtime: dict) -> dict[str, Any]:
    del base_runtime
    route = _catalog().resolve(
        scene_type="SUBAGENT",
        cognitive_tier="B",
        prompt_id="agent.subagent.gateway.text",
    )
    return {**_project(route), "agent_role": "sub_agent"}


__all__ = [
    "resolve_executive_router_llm_runtime",
    "resolve_host_llm_runtime",
    "resolve_host_llm_runtime_async",
    "resolve_host_llm_runtime_async_from_catalog",
    "resolve_host_llm_runtime_from_catalog",
    "resolve_subagent_llm_runtime",
    "resolve_surface_reply_llm_runtime",
]
