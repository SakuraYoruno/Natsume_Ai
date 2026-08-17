"""图运行时的模型节点解析与控制面清洗。

节点可来自图内显式配置或版本化 model-plane YAML profile；无论来源如何，都先投影
到无密钥、无 transport、可序列化的控制面 schema。传输凭据和 SDK 能力仍留在独立
供应商平面，避免图配置同时获得调度和执行权限。
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
    """将外部字段名归一化，以统一白名单及安全字段识别。"""
    return str(value or "").strip().lower().replace("-", "_")


def _transport_field(name: Any) -> bool:
    """判断字段是否暗示传输、端点或凭据；命中即不允许进入图状态。"""
    normalized = _normalized_field_name(name)
    return any(
        normalized == token
        or normalized.startswith(f"{token}_")
        or normalized.endswith(f"_{token}")
        for token in _TRANSPORT_FIELD_TOKENS
    )


def _reject_unsafe_value(value: Any, *, path: str) -> None:
    """递归拒绝可调用对象与 transport 相关键，防止嵌套配置绕过控制面边界。"""
    # 图模型节点是可投影控制面，不能夹带可调用对象、凭据、端点或 transport；递归拒绝
    # 防止这些值藏在 overrides/profile 的嵌套字段后绕过单层白名单。
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
    """复制允许的 JSON 标量/列表，使哈希、审计与恢复依赖稳定的纯数据。"""
    # 只保留 JSON 标量与列表，节点配置不会持有任意对象引用；这让参数哈希、审计与
    # 恢复能够基于稳定的可序列化状态工作。
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
    """把单个图模型节点投影为无秘密的控制面 schema。

未知字段、传输配置、凭据与可调用对象一律拒绝，不能被复制进可执行图状态。
    """

    # 清洗是从 YAML/内联图配置进入执行平面的唯一门槛：未知字段与 transport 信息一律
    # fail-closed，随后返回新 dict 而不是原始可变 Mapping。
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
    """从图内配置或 model-plane profile 解析模型节点，并保持来源优先级明确。

显式图节点优先，profile 仅在未显式声明时提供候选；最终每个选中身份都必须通过
中央图语义表与清洗边界，不能因配置回退而获得任意模型执行权。
    """
    # 解析优先使用图内显式节点；否则才从版本化 model plane profile 选择候选，二者
    # 都必须通过 graph_model_semantic 绑定提示词/场景/tier 的中央语义。
    explicit = _normalize_model_nodes(graph_config.get("model_nodes"))
    overrides = _normalize_model_nodes(graph_config.get("model_node_overrides"))
    if explicit:
        for model_node_id in explicit:
            graph_model_semantic(model_node_id)
        if not overrides:
            # 无 override 时直接返回已清洗快照，不读取外部 profile，使显式图配置的
            # 可复现性不受部署目录热更新影响。
            return explicit
        merged: dict[str, dict[str, Any]] = {}
        for key, row in explicit.items():
            # override 只合并到同名显式节点，并在合并后再次清洗；不能借 override
            # 新增未登记 node 或恢复被禁止的字段。
            merged_row = dict(row)
            override_row = overrides.get(key)
            if isinstance(override_row, Mapping):
                merged_row.update(dict(override_row))
            merged[key] = sanitize_model_node_spec(key, merged_row)
        return merged
    if not model_profile_id:
        # 没有显式节点亦没有 profile 时返回空配置，交由图编译/验证阶段决定节点是否
        # 需要模型；此函数不会擅自挑选默认 provider。
        return {}

    env_name = str(graph_config.get("env") or "").strip().lower() or None
    allowlist = {
        str(item).strip()
        for item in (graph_config.get("node_allowlist") or [])
        if str(item).strip()
    }

    # Profile 与 registry 分开读取：profile 只描述任务路由链，registry 才拥有
    # provider/model 参数，避免单个 YAML 同时成为调度与 transport 权威。
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
            # 纯 handler/虚拟节点不应被分配模型配置，防止 graph executor 将本地分派
            # 误解释为需要外部模型调用。
            continue

        route = _pick_profile_route(profile=profile, binding=binding)
        candidates = [
            str(item).strip()
            for item in (route.get("chain") or [])
            if str(item).strip() and str(item).strip() in registry_nodes
        ]
        explicit_model_node_id = str(binding.get("model_node_id") or "").strip()
        if explicit_model_node_id:
            # 绑定显式指定模型节点时保留它在候选首位，但仍要求它出现在中央语义表，
            # 不能用任意字符串越过图模型身份校验。
            selected_model_node_id = explicit_model_node_id
            if explicit_model_node_id not in candidates:
                candidates = [explicit_model_node_id, *candidates]
        else:
            selected_model_node_id = _pick_candidate_node(candidates=candidates, allowlist=allowlist)

        if not selected_model_node_id:
            # allowlist 不匹配时拒绝而非退回候选第一项，确保环境/图约束真正限制
            # 可执行模型节点集合。
            raise KeyError(f"model node candidate missing for node={node_id}")
        graph_model_semantic(selected_model_node_id)

        row = dict(registry_nodes.get(selected_model_node_id) or {})
        if not row:
            raise KeyError(f"model node missing in registry: {selected_model_node_id}")
        provider = str(row.get("provider") or "").strip().lower()
        model_id = str(row.get("model_id") or "").strip()
        if not provider or not model_id:
            raise KeyError(f"model node missing provider/model_id: {selected_model_node_id}")

        # 参数哈希覆盖 provider/model/温度/输出上限/超时，后续 node 执行可检测 profile
        # 或 override 漂移，而不只信任节点名称。
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
        # override 可能改变任何参与传输的有限字段，因此合并后必须重算哈希并再次清洗；
        # 不允许保留旧哈希来伪装为原始 profile 参数。
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
    """仅接受映射型节点定义，并让每行复用相同的控制面清洗。"""
    # 非 Mapping 或非 Mapping 行直接不形成节点，避免配置解析器把标量/列表自动包装为
    # 可执行模型定义；每行均走相同清洗逻辑。
    if isinstance(raw, Mapping):
        out: dict[str, dict[str, Any]] = {}
        for key, row in raw.items():
            if not isinstance(row, Mapping):
                continue
            out[str(key)] = sanitize_model_node_spec(str(key), row)
        return out
    return {}


def _pick_profile_route(*, profile: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    """按绑定任务读取 profile 路由，大小写兼容仅用于配置键而非节点授权。"""
    routing = dict(profile.get("routing") or {})
    task = str(binding.get("task") or binding.get("node_id") or "").strip()
    route = routing.get(task)
    if route is None:
        route = routing.get(task.upper())
    if isinstance(route, Mapping):
        return dict(route)
    return {}


def _pick_candidate_node(*, candidates: list[str], allowlist: set[str]) -> str | None:
    """从 profile 顺序中选首个允许节点；有白名单而无交集时拒绝回退。"""
    # 候选顺序由 profile 声明，allowlist 存在时只返回第一个交集；它是收紧器，
    # 而不是允许 profile 回退到 allowlist 外节点的提示。
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
    """按 mtime 缓存 YAML 快照，热更新后重读且从不复用缺失文件的旧内容。"""
    # 以 mtime 缓存 YAML 快照，热更新后自动重读；缺文件安全地呈现为空，让上层按
    # profile/registry 缺失的显式错误处理，而不是复用过期配置。
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
    """从已选路由取得节点超时；非法值交由上游默认而非扩大执行窗口。"""
    # timeout 只来自已选择的 profile route，格式异常返回 None 交由上游规范默认值；
    # 不从 binding/request 自由读取时间参数扩大节点执行窗口。
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
    """按模型节点再图节点的顺序叠加既有覆盖，外层仍负责重新清洗与哈希。"""
    # 按“选中模型节点 → 图节点”顺序应用已有 override，后者可做图局部收紧；调用者
    # 仍须在外层重新哈希/清洗，故此助手不承担安全放行。
    for candidate_id in candidate_ids:
        override_row = overrides.get(candidate_id)
        if not isinstance(override_row, Mapping):
            continue
        node_config.update(dict(override_row))


__all__ = ["resolve_model_nodes", "sanitize_model_node_spec"]
