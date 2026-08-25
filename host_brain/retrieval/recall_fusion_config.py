"""召回融合运行时配置的加载与路由选择。

从 recall_fusion.py 拆分，包含运行时配置加载和路由解析逻辑：
  - load_recall_runtime_config：从环境变量加载召回运行时配置
  - resolve_recall_routes：按 node_id 解析激活路由列表
  - ``_parse_routes`` / ``_coerce_routes`` / ``_coerce_route``：路由配置解析工具

环境值或局部覆盖只被解析为受支持的纯数据路由；格式错误会作为显式问题返回，
而不是由检索层猜测存储、集合或选择器，避免配置漂移扩大召回范围。
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from host_brain.core.errors import (
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
)
from host_brain.retrieval.recall_schemas import (
    RecallConfigIssue,
    RecallConfigLoadResult,
    RecallRouteConfig,
    RecallRuntimeConfig,
)
from host_brain.retrieval.recall_fusion_utils import _parse_bool

S_RET_RECALL_CONFIG = "S_RET_RECALL_CONFIG"

DEFAULT_RECALL_ROUTE = "default"
DEFAULT_RECALL_COLLECTION = "episodic"
DEFAULT_RECALL_STORE_ID = "episodic_vector"
DEFAULT_RECALL_TOP_K = 8
DEFAULT_RECALL_SCORE_WEIGHT = 1.0
DEFAULT_NODE_SELECTOR = "default"
DEFAULT_RENDER_TARGET_LANG_SELECTOR = "*"

SUPPORTED_RECALL_STORE_IDS: frozenset[str] = frozenset({
    "sqlite_ledger",
    "life_episode",
    "life_memory_collection",
    "life_memory_entity",
    "neo4j_bond",
    "knowledge_graph",
    "episodic_vector",
    "semantic_memory",
    "world_canon",
    "artifact_vault",
})



def load_recall_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> RecallConfigLoadResult:
    """从环境与显式局部覆盖构建完整路由快照，收集错误而不静默半加载。"""
    merged: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[RecallConfigIssue] = []
    enabled = _parse_bool(merged.get("RETR_RECALL_ENABLED"), default=True)
    default_route = str(merged.get("RETR_RECALL_DEFAULT_ROUTE") or DEFAULT_RECALL_ROUTE).strip()
    routes = _parse_routes(merged=merged, default_route=default_route, issues=issues)

    if not default_route:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_REQUIRED_MISSING,
            detail="RETR_RECALL_DEFAULT_ROUTE is required",
            field="RETR_RECALL_DEFAULT_ROUTE",
        ))
    if default_route and default_route not in routes:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_REQUIRED_MISSING,
            detail=f"default route not found in routes: {default_route}",
            field="RETR_RECALL_DEFAULT_ROUTE",
        ))
    if not routes:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_REQUIRED_MISSING,
            detail="recall routes are required",
            field="RETR_RECALL_ROUTES_JSON",
        ))

    if issues:
        return RecallConfigLoadResult(
            success=False,
            stage=S_RET_RECALL_CONFIG,
            reason_code=issues[0].reason_code,
            config=None,
            issues=tuple(issues),
        )

    config = RecallRuntimeConfig(
        enabled=bool(enabled),
        default_route=default_route,
        routes=routes,
    )
    return RecallConfigLoadResult(
        success=True,
        stage=S_RET_RECALL_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def resolve_recall_routes(
    config: RecallRuntimeConfig,
    *,
    node_id: str | None,
    render_target_lang: str | None = None,
) -> list[RecallRouteConfig]:
    """按节点与渲染语言选择激活路由，保持回退范围可解释。

    先用节点选择器收窄候选，再按语言过滤；若语言过滤清空了已匹配节点的候选，
    回到该节点候选而非跨节点猜测默认存储，只有节点没有匹配时才使用默认路由。
    """
    # 第一步：按 node_id 过滤；未指定节点时才保留全部候选。
    if node_id is not None and str(node_id).strip():
        node = str(node_id).strip()
        node_matched = [
            route
            for _, route in sorted(config.routes.items())
            if _node_selector_match(route.node_selector, node)
        ]
    else:
        node_matched = [route for _, route in sorted(config.routes.items())]

    # 第二步：语言是进一步收窄条件，不改变节点选择器已做出的边界。
    lang_matched = [
        route
        for route in node_matched
        if _render_target_lang_selector_match(route.render_target_lang_selector, render_target_lang)
    ]

    if lang_matched:
        return lang_matched
    # 第三步：语言无交集时保留同节点候选；完全没有节点候选才允许默认路由。
    if node_matched:
        return node_matched
    return [config.routes[config.default_route]]


def _parse_routes(
    *,
    merged: Mapping[str, Any],
    default_route: str,
    issues: list[RecallConfigIssue],
) -> dict[str, RecallRouteConfig]:
    """解析 JSON 多路由配置；缺失时构造单条显式默认路由再走同一校验。"""
    routes_raw = merged.get("RETR_RECALL_ROUTES_JSON")
    if routes_raw is None or not str(routes_raw).strip():
        fallback = {
            default_route: {
                "store_id": str(merged.get("RETR_RECALL_STORE_ID") or DEFAULT_RECALL_STORE_ID),
                "collection": str(merged.get("RETR_RECALL_COLLECTION") or DEFAULT_RECALL_COLLECTION),
                "top_k": merged.get("RETR_RECALL_TOP_K", DEFAULT_RECALL_TOP_K),
                "score_weight": merged.get("RETR_RECALL_SCORE_WEIGHT", DEFAULT_RECALL_SCORE_WEIGHT),
                "node_selector": str(merged.get("RETR_RECALL_NODE_SELECTOR") or DEFAULT_NODE_SELECTOR),
            }
        }
        return _coerce_routes(route_dict=fallback, field_name="RETR_RECALL_ROUTES_JSON", issues=issues)

    try:
        payload = json.loads(str(routes_raw))
    except json.JSONDecodeError:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail="RETR_RECALL_ROUTES_JSON must be JSON object mapping route->config",
            field="RETR_RECALL_ROUTES_JSON",
        ))
        return {}
    return _coerce_routes(route_dict=payload, field_name="RETR_RECALL_ROUTES_JSON", issues=issues)


def _coerce_routes(
    *,
    route_dict: Any,
    field_name: str,
    issues: list[RecallConfigIssue],
) -> dict[str, RecallRouteConfig]:
    """校验路由映射的外层形状，并逐条保留可用路由与问题清单。"""
    if not isinstance(route_dict, dict):
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=f"{field_name} must be JSON object mapping route->config",
            field=field_name,
        ))
        return {}

    routes: dict[str, RecallRouteConfig] = {}
    for raw_route_id, raw_route in route_dict.items():
        route_id = str(raw_route_id or "").strip()
        if not route_id:
            issues.append(RecallConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} contains empty route id",
                field=field_name,
            ))
            continue
        route = _coerce_route(route_id=route_id, raw_route=raw_route, field_name=field_name, issues=issues)
        if route is not None:
            routes[route_id] = route
    return routes


def _coerce_route(
    *,
    route_id: str,
    raw_route: Any,
    field_name: str,
    issues: list[RecallConfigIssue],
) -> RecallRouteConfig | None:
    """将单条外部路由收敛为受支持存储和正数预算；非法项不会进入运行时。"""
    if not isinstance(raw_route, Mapping):
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=f"{field_name}.{route_id} must be object",
            field=f"{field_name}.{route_id}",
        ))
        return None

    store_raw = raw_route.get("store_id")
    store_id = str(store_raw or "").strip().lower()
    collection_raw = raw_route.get("collection")
    collection = str(collection_raw).strip() if collection_raw is not None else ""
    node_selector = str(raw_route.get("node_selector") or "").strip()
    render_target_lang_selector = str(
        raw_route.get("render_target_lang_selector") or DEFAULT_RENDER_TARGET_LANG_SELECTOR
    ).strip()
    top_k = _coerce_positive_int(
        raw_value=raw_route.get("top_k"),
        field=f"{field_name}.{route_id}.top_k",
        issues=issues,
    )
    score_weight = _coerce_positive_float(
        raw_value=raw_route.get("score_weight"),
        field=f"{field_name}.{route_id}.score_weight",
        issues=issues,
    )

    if not store_id:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_REQUIRED_MISSING,
            detail=f"{field_name}.{route_id}.store_id is required",
            field=f"{field_name}.{route_id}.store_id",
        ))
    if store_id not in SUPPORTED_RECALL_STORE_IDS:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=(
                f"{field_name}.{route_id}.store_id must be one of "
                f"{sorted(SUPPORTED_RECALL_STORE_IDS)}"
            ),
            field=f"{field_name}.{route_id}.store_id",
        ))
    if store_id == "episodic_vector" and not collection:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_REQUIRED_MISSING,
            detail=f"{field_name}.{route_id}.collection is required for store_id=episodic_vector",
            field=f"{field_name}.{route_id}.collection",
        ))
    if not node_selector:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_REQUIRED_MISSING,
            detail=f"{field_name}.{route_id}.node_selector is required",
            field=f"{field_name}.{route_id}.node_selector",
        ))
    if (
        top_k is None
        or score_weight is None
        or not node_selector
        or not store_id
        or store_id not in SUPPORTED_RECALL_STORE_IDS
    ):
        return None

    normalized_collection = collection if collection else store_id
    return RecallRouteConfig(
        route_id=route_id,
        store_id=store_id,
        collection=normalized_collection,
        top_k=top_k,
        score_weight=score_weight,
        node_selector=node_selector,
        render_target_lang_selector=render_target_lang_selector,
    )


def _node_selector_match(selector: str, node_id: str) -> bool:
    """精确匹配逗号分隔节点选择器，通配符仅在显式声明时放行。"""
    tokens = [item.strip().lower() for item in str(selector).split(",") if item.strip()]
    if not tokens:
        return False
    node = str(node_id).strip().lower()
    for token in tokens:
        if token in {"*", "all"}:
            return True
        if token == node:
            return True
    return False


def _render_target_lang_selector_match(selector: str, render_target_lang: str | None) -> bool:
    """匹配语言选择器；调用方未提供语言时不凭空排除已配置路由。"""
    tokens = [item.strip().lower() for item in str(selector or "").split(",") if item.strip()]
    if not tokens:
        return True
    for token in tokens:
        if token in {"*", "all", "any"}:
            return True
    lang = str(render_target_lang or "").strip().lower()
    # FIX: 当调用方未指定 render_target_lang 时，不过滤语言限定路由（返回 True）。
    # 原实现在 lang 为空时返回 False，导致带语言 selector 的路由被全量裁掉。
    if not lang:
        return True
    return lang in tokens


def _coerce_positive_int(
    *,
    raw_value: Any,
    field: str,
    issues: list[RecallConfigIssue],
) -> int | None:
    """解析严格正整数配额，错误写入问题列表而非用零或负数继续运行。"""
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=f"{field} must be integer > 0",
            field=field,
        ))
        return None
    if value <= 0:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=f"{field} must be integer > 0",
            field=field,
        ))
        return None
    return value


def _coerce_positive_float(
    *,
    raw_value: Any,
    field: str,
    issues: list[RecallConfigIssue],
) -> float | None:
    """解析严格正浮点权重，错误写入问题列表避免倒置或失效排序。"""
    try:
        value = float(str(raw_value).strip())
    except (TypeError, ValueError):
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=f"{field} must be float > 0",
            field=field,
        ))
        return None
    if value <= 0.0:
        issues.append(RecallConfigIssue(
            reason_code=CONFIG_VALUE_INVALID,
            detail=f"{field} must be float > 0",
            field=field,
        ))
        return None
    return float(value)
