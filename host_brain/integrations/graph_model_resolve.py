"""Graph model resolution: model plane profile loading and node resolution.

Resolves model nodes from inline config or model plane YAML profiles.
Separated from graph_contract to keep each module under 300 lines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from host_brain.integrations.graph_contract import build_model_param_hash
from host_brain.model_runtime.graph_semantics import graph_model_semantic


_MODEL_NODE_ALLOWED_FIELDS = frozenset(
    {
        "available",
        "candidate_nodes",
        "expected_param_hash",
        "max_output_tokens",
        "model_id",
        "model_profile_id",
        "provider",
        "selected_node",
        "selector_env",
        "source",
        "temperature",
        "timeout_ms",
        "top_k",
        "top_p",
    }
)
_TRANSPORT_FIELD_TOKENS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "base_url",
        "base_url_override",
        "bearer",
        "client",
        "credential",
        "credentials",
        "endpoint",
        "provider_fn",
        "secret",
        "token",
        "transport",
    }
)


def _normalized_field_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _transport_field(name: Any) -> bool:
    normalized = _normalized_field_name(name)
    return any(
        normalized == token
        or normalized.startswith(f"{token}_")
        or normalized.endswith(f"_{token}")
        for token in _TRANSPORT_FIELD_TOKENS
    )


def _reject_unsafe_value(value: Any, *, path: str) -> None:
    if callable(value):
        raise ValueError(f"graph model node callable forbidden: {path}")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            key_name = str(key)
            nested_path = f"{path}.{key_name}"
            if _transport_field(key_name):
                raise ValueError(f"graph model node transport field forbidden: {nested_path}")
            _reject_unsafe_value(nested, path=nested_path)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, nested in enumerate(value):
            _reject_unsafe_value(nested, path=f"{path}[{index}]")


def _copy_serializable_value(value: Any, *, path: str) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [
            _copy_serializable_value(nested, path=f"{path}[{index}]")
            for index, nested in enumerate(value)
        ]
    raise ValueError(
        f"graph model node value must be JSON-serializable scalar/list: {path}"
    )


def sanitize_model_node_spec(
    model_node_id: str,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one graph model node onto the secret-free control-plane schema.

    Unknown fields, transport configuration, credentials and callables are
    rejected instead of being copied into executable graph state.
    """

    normalized_id = str(model_node_id or "").strip()
    if not normalized_id:
        raise ValueError("graph model node id must be non-empty")
    _reject_unsafe_value(spec, path=f"model_nodes.{normalized_id}")
    unknown = {
        str(key)
        for key in spec
        if _normalized_field_name(key) not in _MODEL_NODE_ALLOWED_FIELDS
    }
    if unknown:
        raise ValueError(
            f"graph model node fields not allowed for {normalized_id}: "
            + ", ".join(sorted(unknown))
        )
    return {
        _normalized_field_name(key): _copy_serializable_value(
            value,
            path=f"model_nodes.{normalized_id}.{key}",
        )
        for key, value in spec.items()
    }


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
        for model_node_id in explicit:
            graph_model_semantic(model_node_id)
        if not overrides:
            return explicit
        merged: dict[str, dict[str, Any]] = {}
        for key, row in explicit.items():
            merged_row = dict(row)
            override_row = overrides.get(key)
            if isinstance(override_row, Mapping):
                merged_row.update(dict(override_row))
            merged[key] = sanitize_model_node_spec(key, merged_row)
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
        # handler 节点（含 agent_dispatch_node 等 handler_id="" 的虚拟节点）跳过 model node 解析；
        # 虚拟节点由 graph_executor 直接分派，不需要 model node。
        # 判断：binding 中显式设置了 handler_id 字段（含空字符串），且没有 model_node_id，则跳过。
        if "handler_id" in binding and not str(binding.get("model_node_id") or "").strip():
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
        graph_model_semantic(selected_model_node_id)

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
        node_config["expected_param_hash"] = build_model_param_hash(
            provider=str(node_config.get("provider") or ""),
            model_id=str(node_config.get("model_id") or ""),
            temperature=node_config.get("temperature"),
            max_output_tokens=node_config.get("max_output_tokens"),
            timeout_ms=node_config.get("timeout_ms"),
        )
        node_config = sanitize_model_node_spec(selected_model_node_id, node_config)
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
            out[str(key)] = sanitize_model_node_spec(str(key), row)
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


# 修复：原 @lru_cache 以 Path 对象为 key，YAML 文件热更新后缓存永远不失效，
# 导致 model_profiles.yaml / node_registry.yaml 修改后需要重启进程才能生效，
# 与 graph_runtime.update_route_config 热更新设计相矛盾。
# 改为基于文件 mtime 的缓存失效机制：mtime 变化时自动重新加载。
_yaml_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _yaml_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with path.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    result = dict(payload or {}) if isinstance(payload, Mapping) else {}
    _yaml_cache[path] = (mtime, result)
    return result


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


__all__ = ["resolve_model_nodes", "sanitize_model_node_spec"]
