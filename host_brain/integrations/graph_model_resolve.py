"""Graph model resolution: model plane profile loading and node resolution.

Resolves model nodes from inline config or model plane YAML profiles.
Separated from graph_contract to keep each module under 300 lines.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from host_brain.integrations.graph_contract import build_model_param_hash


def resolve_model_nodes(
    *,
    graph_config: Mapping[str, Any],
    node_bindings: Mapping[str, dict[str, Any]],
    model_profile_id: str | None,
    model_plane_dir: Path,
) -> dict[str, dict[str, Any]]:
    """Resolve model nodes from inline config or model plane profiles."""
    explicit = _normalize_model_nodes(graph_config.get("model_nodes"))
    overrides = _normalize_model_nodes(graph_config.get("model_node_overrides"))
    if explicit:
        if not overrides:
            return explicit
        merged: dict[str, dict[str, Any]] = {}
        for key, row in explicit.items():
            merged_row = dict(row)
            override_row = overrides.get(key)
            if isinstance(override_row, Mapping):
                merged_row.update(dict(override_row))
            merged[key] = merged_row
        return merged
    if not model_profile_id:
        return {}

    env_name = str(graph_config.get("env") or "").strip().lower() or None
    allowlist = {
        str(item).strip()
        for item in (graph_config.get("node_allowlist") or [])
        if str(item).strip()
    }

    profiles_payload = _read_yaml(model_plane_dir / "model_profiles.yaml")
    registry_payload = _read_yaml(model_plane_dir / "node_registry.yaml")
    profiles = dict(profiles_payload.get("profiles") or {}) if isinstance(profiles_payload, Mapping) else {}
    registry_nodes = dict(registry_payload.get("nodes") or {}) if isinstance(registry_payload, Mapping) else {}

    profile = dict(profiles.get(model_profile_id) or {})
    if not profile:
        raise KeyError(f"model profile missing: {model_profile_id}")

    resolved: dict[str, dict[str, Any]] = {}
    for node_id, binding in node_bindings.items():
        handler_id = str(binding.get("handler_id") or "").strip()
        if handler_id:
            continue

        route = _pick_profile_route(profile=profile, binding=binding)
        candidates = [
            str(item).strip()
            for item in (route.get("chain") or [])
            if str(item).strip() and str(item).strip() in registry_nodes
        ]
        explicit_model_node_id = str(binding.get("model_node_id") or "").strip()
        if explicit_model_node_id:
            selected_model_node_id = explicit_model_node_id
            if explicit_model_node_id not in candidates:
                candidates = [explicit_model_node_id, *candidates]
        else:
            selected_model_node_id = _pick_candidate_node(candidates=candidates, allowlist=allowlist)

        if not selected_model_node_id:
            raise KeyError(f"model node candidate missing for node={node_id}")

        row = dict(registry_nodes.get(selected_model_node_id) or {})
        if not row:
            raise KeyError(f"model node missing in registry: {selected_model_node_id}")
        provider = str(row.get("provider") or "").strip().lower()
        model_id = str(row.get("model_id") or "").strip()
        if not provider or not model_id:
            raise KeyError(f"model node missing provider/model_id: {selected_model_node_id}")

        timeout_ms = _resolve_timeout_ms(
            profile=profile,
            binding=binding,
            model_node_id=selected_model_node_id,
            route=route,
        )
        expected_param_hash = build_model_param_hash(
            provider=provider,
            model_id=model_id,
            temperature=row.get("temperature"),
            max_output_tokens=row.get("max_output_tokens"),
            timeout_ms=timeout_ms,
        )
        node_config = {
            "provider": provider,
            "model_id": model_id,
            "temperature": row.get("temperature"),
            "max_output_tokens": row.get("max_output_tokens"),
            "timeout_ms": timeout_ms,
            "expected_param_hash": expected_param_hash,
            "model_profile_id": model_profile_id,
            "source": "model_plane",
            "selected_node": selected_model_node_id,
            "candidate_nodes": candidates,
            "selector_env": env_name,
        }
        _apply_node_overrides(
            node_config=node_config,
            overrides=overrides,
            candidate_ids=[selected_model_node_id, node_id],
        )
        resolved[selected_model_node_id] = dict(node_config)
        if node_id != selected_model_node_id:
            resolved[node_id] = dict(node_config)
    return resolved


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_model_nodes(raw: Any) -> dict[str, dict[str, Any]]:
    if isinstance(raw, Mapping):
        out: dict[str, dict[str, Any]] = {}
        for key, row in raw.items():
            if not isinstance(row, Mapping):
                continue
            out[str(key)] = dict(row)
        return out
    return {}


def _pick_profile_route(*, profile: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    routing = dict(profile.get("routing") or {})
    task = str(binding.get("task") or binding.get("node_id") or "").strip()
    route = routing.get(task)
    if route is None:
        route = routing.get(task.upper())
    if isinstance(route, Mapping):
        return dict(route)
    return {}


def _pick_candidate_node(*, candidates: list[str], allowlist: set[str]) -> str | None:
    if allowlist:
        for item in candidates:
            if item in allowlist:
                return item
        return None
    return candidates[0] if candidates else None


@lru_cache(maxsize=8)
def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    return dict(payload or {}) if isinstance(payload, Mapping) else {}


def _resolve_timeout_ms(
    *,
    profile: Mapping[str, Any],
    binding: Mapping[str, Any],
    model_node_id: str,
    route: Mapping[str, Any] | None = None,
) -> int | None:
    resolved_route = dict(route or _pick_profile_route(profile=profile, binding=binding) or {})
    timeout_map = dict(resolved_route.get("timeouts_ms") or {})
    raw = timeout_map.get(model_node_id)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _apply_node_overrides(
    *,
    node_config: dict[str, Any],
    overrides: Mapping[str, dict[str, Any]],
    candidate_ids: list[str],
) -> None:
    for candidate_id in candidate_ids:
        override_row = overrides.get(candidate_id)
        if not isinstance(override_row, Mapping):
            continue
        node_config.update(dict(override_row))
