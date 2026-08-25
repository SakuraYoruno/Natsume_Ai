"""Graph 配置构建模块。

负责 Chat 图的配置构建。意图识别与场景判定统一由 Thalamus 承接。
"""

from __future__ import annotations

from typing import Any

from host_brain.integrations.llm_gateway import (
    PROVIDER_CODEX,
    PROVIDER_DEEPSEEK,
    PROVIDER_DISABLED,
    PROVIDER_GEMINI,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
)
from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
    resolve_execution_profile,
)
from host_brain.model_runtime.prompt_builder_utils import (
    CHAT_DIRECT_NODE_ID as _CHAT_DIRECT_NODE_ID,
    CHAT_RENDER_NODE_ID as _CHAT_RENDER_NODE_ID,
    SCENE_CHAT as _SCENE_CHAT,
    SCENE_EXEC_INTENT as _SCENE_EXEC_INTENT,
    SCENE_WORK_CODE as _SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE as _SCENE_WORK_TRANSLATE,
)

# ---------------------------------------------------------------------------
# Graph topology constants used by the builder.
# ---------------------------------------------------------------------------
from host_brain.app.graph_config_constants import (  # noqa: E402
    CHAT_GRAPH_NODE_ORDER,
    SUPERVISOR_DISPATCH_NODE_ID,
    SUPERVISOR_DISPATCH_HANDLER_ID,
    SURFACE_REPLY_HANDLER_ID,
    TRANSLATE_AGENT_HANDLER_ID,
    INTENT_AGENT_HANDLER_ID,
    INTENT_AGENT_ENTRY_NODE_ID,
    INTENT_AGENT_ENTRY_HANDLER_ID,
    JUDGE_AGENT_NODE_ID,
    JUDGE_AGENT_HANDLER_ID,
    RETRIEVAL_AGENT_HANDLER_ID,
    REPAIR_AGENT_HANDLER_ID,
    PENTEST_AGENT_HANDLER_ID,
    AGENT_DISPATCH_NODE_ID,
)

def resolve_scene_node_sequence(scene_type: str) -> list[str]:
    """Return the model-node sequence for explicit legacy graph profiles."""
    scene = str(scene_type or _SCENE_CHAT).strip().upper()
    if scene == _SCENE_EXEC_INTENT:
        return [
            "core_reasoner_primary",
            "planner_constraints",
            "tool_planner",
            "safety_verifier",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    if scene == _SCENE_WORK_TRANSLATE:
        return [
            "core_reasoner_primary",
            "workbench_translate",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    if scene == _SCENE_WORK_CODE:
        return [
            "core_reasoner_primary",
            "workbench_code_assist",
            "consistency_verifier",
            "renderer_ja_c_style",
        ]
    return [_CHAT_DIRECT_NODE_ID]


def build_chat_graph_config(
    *,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    scene_type: str = _SCENE_CHAT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_available: bool | None = None,
    execution_profile: str | None = None,
) -> dict[str, Any]:
    """按 execution_profile 选图；未传时由 scene_type 解析（兼容旧调用）。"""
    profile = (
        str(execution_profile or "").strip()
        or resolve_execution_profile(scene_type)
    )
    return _build_graph_config_by_profile(
        execution_profile=profile,
        provider_name=provider_name,
        model_id=model_id,
        timeout_ms=timeout_ms,
        available=available,
        scene_type=scene_type,
        scene_type_source=scene_type_source,
        render_target_lang=render_target_lang,
        cloud_provider_name=cloud_provider_name,
        cloud_model_id=cloud_model_id,
        cloud_timeout_ms=cloud_timeout_ms,
        cloud_available=cloud_available,
    )


def _build_graph_config_by_profile(
    *,
    execution_profile: str,
    provider_name: str,
    model_id: str,
    timeout_ms: int,
    available: bool,
    scene_type: str = _SCENE_CHAT,
    scene_type_source: str = "explicit",
    render_target_lang: str | None = None,
    cloud_provider_name: str | None = None,
    cloud_model_id: str | None = None,
    cloud_timeout_ms: int | None = None,
    cloud_available: bool | None = None,
) -> dict[str, Any]:
    """Build a secret-free graph projection from already-routed metadata."""
    resolved_scene_type = str(scene_type or _SCENE_CHAT).strip().upper() or _SCENE_CHAT
    is_chat_fast_path = execution_profile == PROFILE_CHAT_FAST
    use_supervised = execution_profile == PROFILE_WORKFLOW_SUPERVISED
    is_intent = execution_profile == PROFILE_INTENT_EXEC
    resolved_render_lang = str(render_target_lang or "").strip().lower()
    if resolved_render_lang not in {"zh", "ja"}:
        resolved_render_lang = "ja"

    resolved_chat_provider = str(provider_name or PROVIDER_DEEPSEEK).strip().lower() or PROVIDER_DEEPSEEK
    resolved_chat_model_id = str(model_id or "").strip() or "deepseek-v4-flash"
    resolved_chat_timeout_ms = max(800, int(timeout_ms))
    resolved_chat_available = bool(available and resolved_chat_provider != PROVIDER_DISABLED)

    resolved_cloud_provider = (
        str(cloud_provider_name or resolved_chat_provider or PROVIDER_DEEPSEEK)
        .strip()
        .lower()
        or PROVIDER_DEEPSEEK
    )
    if resolved_cloud_provider not in {
        PROVIDER_OPENAI,
            PROVIDER_GEMINI,
        PROVIDER_DEEPSEEK,
        PROVIDER_CODEX,
        PROVIDER_MOCK,
        PROVIDER_DISABLED,
    }:
        resolved_cloud_provider = PROVIDER_DEEPSEEK
    resolved_cloud_timeout_ms = max(800, int(cloud_timeout_ms or resolved_chat_timeout_ms))
    resolved_cloud_model_id = str(cloud_model_id or "").strip() or resolved_chat_model_id
    resolved_cloud_available_flag = (
        bool(cloud_available)
        if cloud_available is not None
        else bool(resolved_cloud_provider != PROVIDER_DISABLED)
    )
    resolved_cloud_available = bool(resolved_cloud_available_flag)

    base_timeout_ms = max(800, int(resolved_cloud_timeout_ms))
    node_model_ids: dict[str, str] = {
        "core_reasoner_primary": "deepseek-v4-flash",
        "core_reasoner_secondary": "deepseek-v4-flash",
        "planner_constraints": "deepseek-v4-flash",
        "tool_planner": "deepseek-v4-flash",
        "safety_verifier": "deepseek-v4-flash",
        "consistency_verifier": "deepseek-v4-flash",
        "renderer_ja_c_style": "deepseek-v4-flash",
        "workbench_translate": "deepseek-v4-flash",
        "workbench_code_assist": "deepseek-v4-flash",
    }
    node_timeouts: dict[str, int] = {
        "core_reasoner_primary": min(base_timeout_ms, 12000),
        "core_reasoner_secondary": min(base_timeout_ms, 9000),
        "planner_constraints": min(base_timeout_ms, 8000),
        "tool_planner": min(base_timeout_ms, 8000),
        "safety_verifier": min(base_timeout_ms, 7000),
        "consistency_verifier": min(base_timeout_ms, 7000),
        "renderer_ja_c_style": min(base_timeout_ms, 9000),
        "workbench_translate": min(base_timeout_ms, 10000),
        "workbench_code_assist": min(base_timeout_ms, 10000),
    }
    if resolved_cloud_provider != PROVIDER_DEEPSEEK:
        for node_id in (
            "core_reasoner_primary",
            "core_reasoner_secondary",
            "planner_constraints",
            "tool_planner",
            "safety_verifier",
            "consistency_verifier",
            "renderer_ja_c_style",
            "workbench_translate",
            "workbench_code_assist",
        ):
            node_model_ids[node_id] = resolved_cloud_model_id
    if is_chat_fast_path:
        node_model_ids[_CHAT_DIRECT_NODE_ID] = resolved_chat_model_id
        # 修复：使用 max 而非 min，确保超时不低于 10,000ms（需求 1.1、1.2）
        node_timeouts[_CHAT_DIRECT_NODE_ID] = max(resolved_chat_timeout_ms, 10_000)
        if resolved_cloud_provider == PROVIDER_DEEPSEEK:
            node_model_ids["core_reasoner_primary"] = "deepseek-v4-flash"
        node_timeouts["core_reasoner_primary"] = min(base_timeout_ms, 5000)
        node_timeouts["core_reasoner_secondary"] = min(base_timeout_ms, 3000)
        node_timeouts["renderer_ja_c_style"] = min(base_timeout_ms, 4000)
    elif use_supervised:
        # AUTO 路由下保留 chat_direct 快速回复模型，复杂任务仍走 primary 链路。
        node_model_ids[_CHAT_DIRECT_NODE_ID] = resolved_chat_model_id
        node_timeouts[_CHAT_DIRECT_NODE_ID] = max(resolved_chat_timeout_ms, 10_000)
        node_timeouts["core_reasoner_secondary"] = min(base_timeout_ms, 9000)

    model_node_overrides: dict[str, dict[str, Any]] = {}
    for node_id in CHAT_GRAPH_NODE_ORDER:
        is_chat_node = node_id == _CHAT_DIRECT_NODE_ID
        node_provider = resolved_chat_provider if is_chat_node else resolved_cloud_provider
        fallback_model_id = resolved_chat_model_id if is_chat_node else resolved_cloud_model_id
        node_model_id = str(node_model_ids.get(node_id) or fallback_model_id).strip() or fallback_model_id
        node_timeout = int(
            node_timeouts.get(node_id, resolved_chat_timeout_ms if is_chat_node else resolved_cloud_timeout_ms)
        )
        node_available = resolved_chat_available if is_chat_node else resolved_cloud_available
        node_override: dict[str, Any] = {
            "provider": node_provider,
            "model_id": node_model_id,
            "timeout_ms": node_timeout,
            "available": bool(node_available),
            # Transport callables and credentials are intentionally absent.
            # GraphExecutor crosses the canonical ModelAttemptService port.
        }
        # 需求 2.1：CHAT 快速路径下，chat_direct_ja 节点限制 max_output_tokens=200
        if (is_chat_fast_path or use_supervised) and node_id == _CHAT_DIRECT_NODE_ID:
            node_override["max_output_tokens"] = 200
        model_node_overrides[node_id] = node_override

    if is_intent:
        # P4：Intent 图主图仅 4 节点；子图节点仅入 node_bindings 供 entry handler 调用，不入 scene_node_sequence/edges
        scene_node_sequence = [
            INTENT_AGENT_ENTRY_NODE_ID,
            "safety_verifier",
            JUDGE_AGENT_NODE_ID,
            _CHAT_RENDER_NODE_ID,
        ]
        node_bindings = [
            {
                "node_id": INTENT_AGENT_ENTRY_NODE_ID,
                "handler_id": INTENT_AGENT_ENTRY_HANDLER_ID,
                "task": "intent_agent_entry",
                "require_approval_token": False,
                # P6-2：局部循环边界配置（P6-1 建议默认值）
                "max_local_steps": 10,
                "max_retry_count": 2,
                "local_loop_timeout_ms": 60_000,
            },
            {"node_id": "safety_verifier", "model_node_id": "safety_verifier", "task": "safety_verifier", "require_approval_token": False},
            {
                "node_id": JUDGE_AGENT_NODE_ID,
                "handler_id": JUDGE_AGENT_HANDLER_ID,
                "task": "judge_agent",
                "require_approval_token": False,
            },
            {"node_id": _CHAT_RENDER_NODE_ID, "model_node_id": _CHAT_RENDER_NODE_ID, "task": _CHAT_RENDER_NODE_ID, "require_approval_token": False},
            {"node_id": "core_reasoner_primary", "model_node_id": "core_reasoner_primary", "task": "core_reasoner_primary", "require_approval_token": False},
            {"node_id": "planner_constraints", "model_node_id": "planner_constraints", "task": "planner_constraints", "require_approval_token": False},
            {"node_id": "tool_planner", "model_node_id": "tool_planner", "task": "tool_planner", "require_approval_token": False},
        ]
        entry_node_id = INTENT_AGENT_ENTRY_NODE_ID
        edges = [
            {"from": INTENT_AGENT_ENTRY_NODE_ID, "to": "safety_verifier"},
            {"from": "safety_verifier", "to": JUDGE_AGENT_NODE_ID},
            {"from": JUDGE_AGENT_NODE_ID, "to": _CHAT_RENDER_NODE_ID},
        ]
        fallback_edges = []
        chain_budget_ms = sum(
            int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence
        )
    elif use_supervised:
        # Supervised 图：supervisor_dispatch → agent_dispatch → judge_agent → renderer
        scene_node_sequence = [
            SUPERVISOR_DISPATCH_NODE_ID,
            AGENT_DISPATCH_NODE_ID,
            JUDGE_AGENT_NODE_ID,
            _CHAT_RENDER_NODE_ID,
        ]
        # P3.5：allowlist 由图配置提供；workbench_* 为 handler 可调用的 model 节点，不入边序列
        # P16：allowlist 对齐 _AGENT_HANDLER_MAP（graph_executor 内的全量 agent）
        _dispatch_allowlist = [
            SURFACE_REPLY_HANDLER_ID,
            TRANSLATE_AGENT_HANDLER_ID,
            INTENT_AGENT_HANDLER_ID,
            JUDGE_AGENT_HANDLER_ID,
            RETRIEVAL_AGENT_HANDLER_ID,
            REPAIR_AGENT_HANDLER_ID,
            PENTEST_AGENT_HANDLER_ID,
        ]
        node_bindings = [
            {
                "node_id": SUPERVISOR_DISPATCH_NODE_ID,
                "handler_id": SUPERVISOR_DISPATCH_HANDLER_ID,
                "task": "supervisor_dispatch",
                "require_approval_token": False,
                # P5-2：与 agent_dispatch 使用同一 allowlist，供 Supervisor 校验 proposal
                "agent_dispatch_allowlist": _dispatch_allowlist,
            },
            {
                "node_id": AGENT_DISPATCH_NODE_ID,
                "handler_id": "",  # P16：不再绑定 handler，由 graph_executor 直接分派
                "task": "agent_dispatch",
                "require_approval_token": False,
                "agent_dispatch_allowlist": _dispatch_allowlist,
            },
            {
                "node_id": JUDGE_AGENT_NODE_ID,
                "handler_id": JUDGE_AGENT_HANDLER_ID,
                "task": "judge_agent",
                "require_approval_token": False,
            },
            {"node_id": _CHAT_RENDER_NODE_ID, "model_node_id": _CHAT_RENDER_NODE_ID, "task": _CHAT_RENDER_NODE_ID, "require_approval_token": False},
            {"node_id": "workbench_code_assist", "model_node_id": "workbench_code_assist", "task": "workbench_code_assist", "require_approval_token": False},
            {"node_id": "workbench_translate", "model_node_id": "workbench_translate", "task": "workbench_translate", "require_approval_token": False},
        ]
        entry_node_id = SUPERVISOR_DISPATCH_NODE_ID
        edges = [
            {"from": SUPERVISOR_DISPATCH_NODE_ID, "to": AGENT_DISPATCH_NODE_ID},
            {"from": AGENT_DISPATCH_NODE_ID, "to": JUDGE_AGENT_NODE_ID},
            {"from": JUDGE_AGENT_NODE_ID, "to": _CHAT_RENDER_NODE_ID},
        ]
        fallback_edges = []
        chain_budget_ms = sum(
            int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence
        )
    else:
        # Chat 图：chat_direct → renderer（仅快路径两节点）
        # P3：code/translate 已迁入 Supervised 图，此处不再用旧长链做主路由；误入时强制快路径
        use_chat_sequence = is_chat_fast_path or (
            str(scene_type or "").strip().upper() in (_SCENE_WORK_CODE, _SCENE_WORK_TRANSLATE)
        )
        if use_chat_sequence:
            scene_node_sequence = [_CHAT_DIRECT_NODE_ID, _CHAT_RENDER_NODE_ID]
        else:
            scene_node_sequence = resolve_scene_node_sequence(scene_type)
        fallback_only_nodes = [] if use_chat_sequence else ["core_reasoner_secondary"]
        all_registered_nodes = scene_node_sequence + [
            n for n in fallback_only_nodes if n not in scene_node_sequence
        ]
        node_bindings = [
            {
                "node_id": node_id,
                "model_node_id": node_id,
                "task": node_id,
                "require_approval_token": False,
            }
            for node_id in all_registered_nodes
        ]
        edges = [
            {"from": scene_node_sequence[i], "to": scene_node_sequence[i + 1]}
            for i in range(len(scene_node_sequence) - 1)
        ]
        entry_node_id = scene_node_sequence[0]
        fallback_target_node = _CHAT_RENDER_NODE_ID if use_chat_sequence else (
            "consistency_verifier"
            if "consistency_verifier" in scene_node_sequence
            else _CHAT_RENDER_NODE_ID
        )
        fallback_edges = (
            []
            if use_chat_sequence
            else [
                {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"},
                {"from": "core_reasoner_secondary", "to": fallback_target_node},
            ]
        )
        chain_budget_ms = (
            sum(int(node_timeouts.get(node_id, base_timeout_ms)) for node_id in scene_node_sequence)
            + (
                int(node_timeouts.get("core_reasoner_secondary", base_timeout_ms))
                if not use_chat_sequence
                else 0
            )
        )

    policy_timeout_ms = chain_budget_ms + 5_000
    if is_chat_fast_path or (not use_supervised and not is_intent and use_chat_sequence):
        policy_timeout_ms = max(8_000, min(int(policy_timeout_ms), 12_000))
    elif use_supervised:
        policy_timeout_ms = max(30_000, min(int(policy_timeout_ms), 240_000))
    else:
        policy_timeout_ms = max(90_000, min(int(policy_timeout_ms), 240_000))

    return {
        "graph_id": "host.chat.realtime",
        "version": "v1",
        "policy_timeout_ms": policy_timeout_ms,
        "entry_node": entry_node_id,
        "model_profile_id": "chat_ja_24x7_v1",
        "node_bindings": node_bindings,
        "edges": edges,
        "fallback_edges": fallback_edges,
        "model_node_overrides": model_node_overrides,
        "graph_meta": {
            "execution_profile": execution_profile,
            "online_chain": (
                "intent_agent + safety_verifier + judge + renderer"
                if is_intent
                else (
                    "supervisor_dispatch + agent_dispatch + judge + renderer"
                    if use_supervised
                    else (
                        "chat_direct_single_node + scene_routed_langgraph"
                        if is_chat_fast_path
                        else "six_recall + scene_routed_langgraph + targeted_renderer"
                    )
                )
            ),
            "provider": resolved_chat_provider,
            "chat_provider": resolved_chat_provider,
            "chat_model_id": resolved_chat_model_id,
            "cloud_provider": resolved_cloud_provider,
            "cloud_model_id": resolved_cloud_model_id,
            "scene_type": resolved_scene_type,
            "scene_type_source": scene_type_source,
            "render_target_lang": resolved_render_lang,
            "active_node_count": len(scene_node_sequence),
            "active_nodes": list(scene_node_sequence),
        },
    }
