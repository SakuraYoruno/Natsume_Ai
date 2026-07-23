"""Retired direct sub-agent protocol compatibility contracts.

Only request/response codecs and a read-only registry view remain here.  The
historical dispatcher used a module-global handler map, constructed model
gateways, and emitted an independent execution log before Task Runtime
verification.  That is no longer an executable authority: callers must enter
``HostCommand -> immutable TaskGraph`` and, for the currently enabled
``pentest_agent`` delegate, use ``GovernedDelegateDispatcher``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SubAgentRequest:
    """Cortex Orchestrator → 子 Agent 的调度请求。"""
    agent_id: str                          # "translate_agent" / "intent_agent" / "vision_agent" / "repair_agent"
    task: str                              # 自然语言任务描述
    user_message: str = ""                 # 用户原始消息
    request_id: str = ""
    session_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 30_000


@dataclass
class SubAgentResponse:
    """子 Agent → Cortex Orchestrator 的执行结果。"""
    success: bool
    agent_id: str
    result: str = ""                       # 技术产出（代码/翻译/执行结果）
    error: str | None = None
    audit_refs: dict[str, Any] = field(default_factory=dict)  # 三联画审计摘要
    latency_ms: int = 0


LEGACY_SUB_AGENT_DISPATCH_RETIRED = "LEGACY_SUB_AGENT_DISPATCH_RETIRED"


# ---------------------------------------------------------------------------
# Agent ID 常量
# ---------------------------------------------------------------------------

AGENT_TRANSLATE = "translate_agent"
AGENT_INTENT = "intent_agent"
AGENT_VISION = "vision_agent"
AGENT_REPAIR = "repair_agent"
AGENT_PENTEST = "pentest_agent"

ALL_AGENT_IDS: frozenset[str] = frozenset({
    AGENT_TRANSLATE, AGENT_INTENT, AGENT_VISION, AGENT_REPAIR, AGENT_PENTEST,
})

# 子 Agent 能力描述（注入 Cortex Orchestrator prompt，供 LLM 判断调度）
AGENT_CAPABILITIES: dict[str, str] = {
    AGENT_TRANSLATE: "历史翻译能力描述；旧直调已退役，未授予 Delegate 执行权限",
    AGENT_INTENT: "历史意图能力描述；旧直调已退役，未授予工具或 Delegate 执行权限",
    AGENT_VISION: "历史视觉能力描述；旧直调已退役，未授予 Delegate 执行权限",
    AGENT_REPAIR: "历史环境修复能力描述；旧直调已退役，未授予安装或执行权限",
    AGENT_PENTEST: "仅允许通过受治理 Delegate 进行只读 Domain Pack/SecureRun 交接；不执行网络或攻击工具",
}


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------

def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_contract_json(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


_DEFAULT_AGENT_CAPABILITIES = dict(AGENT_CAPABILITIES)


def get_agent_capabilities() -> dict[str, str]:
    templates = _load_template_mapping("sub_agent_protocol.agent_capabilities")
    capabilities = dict(_DEFAULT_AGENT_CAPABILITIES)
    for agent_id in ALL_AGENT_IDS:
        resolved = str(templates.get(agent_id) or capabilities.get(agent_id) or "").strip()
        if resolved:
            capabilities[agent_id] = resolved
    return capabilities


AGENT_CAPABILITIES = get_agent_capabilities()


SUB_AGENT_REGISTRY_VIEW_SCHEMA_VERSION = "Natsume.agent.sub_agent_registry_view.v1"


@dataclass(frozen=True, slots=True)
class SubAgentRegistryEntry:
    """Read-only compatibility view over the app-level sub-agent registry."""

    agent_id: str
    capability: str
    default_timeout_ms: int = 30_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "capability": self.capability,
            "default_timeout_ms": self.default_timeout_ms,
        }


def get_sub_agent_registry_view() -> dict[str, Any]:
    """Return a deterministic view; it does not load handlers or dispatch work."""

    capabilities = get_agent_capabilities()
    entries = [
        SubAgentRegistryEntry(
            agent_id=agent_id,
            capability=str(capabilities.get(agent_id) or "").strip(),
        )
        for agent_id in sorted(ALL_AGENT_IDS)
    ]
    return {
        "schema_version": SUB_AGENT_REGISTRY_VIEW_SCHEMA_VERSION,
        "agents": [entry.to_dict() for entry in entries],
    }


def resolve_sub_agent_registry_entry(agent_id: str) -> SubAgentRegistryEntry:
    """Resolve an app agent without exposing mutable dispatcher internals."""

    normalized = str(agent_id or "").strip()
    view = get_sub_agent_registry_view()
    for payload in list(view.get("agents") or []):
        if str(payload.get("agent_id") or "").strip() == normalized:
            return SubAgentRegistryEntry(
                agent_id=normalized,
                capability=str(payload.get("capability") or "").strip(),
                default_timeout_ms=int(payload.get("default_timeout_ms") or 30_000),
            )
    from host_brain.contracts.agent_runtime.contract_errors import (  # noqa: PLC0415
        SUB_AGENT_ADAPTER_INVALID,
        ContractViolation,
    )

    raise ContractViolation(
        SUB_AGENT_ADAPTER_INVALID,
        f"sub-agent is not present in registry view: {normalized}",
    )


def build_sub_agent_request_from_child_work_order(
    *,
    child_work_order: Any,
    agent_id: str,
    request_id: str,
    session_id: str = "",
    context_refs: tuple[str, ...] | list[str] = (),
    timeout_ms: int | None = None,
    context: Mapping[str, Any] | None = None,
) -> SubAgentRequest:
    """Build a read-only legacy compatibility projection from a child WorkOrder.

    This codec preserves lineage for diagnostics and migration checks only.
    It cannot authorize ``SubAgentDispatcher.dispatch``; executable delegates
    must be admitted as governed TaskGraph nodes.
    """

    from host_brain.agent_runtime.sub_agent_contract import (  # noqa: PLC0415
        SubAgentRequestAdapter,
        SubAgentSpec,
        validate_planner_target_binding,
    )
    from host_brain.contracts.agent_runtime.work_order import WorkOrderV3  # noqa: PLC0415

    child = (
        child_work_order
        if isinstance(child_work_order, WorkOrderV3)
        else WorkOrderV3.from_mapping(
            child_work_order if isinstance(child_work_order, Mapping) else {}
        )
    )
    entry = resolve_sub_agent_registry_entry(agent_id)
    if not entry.capability:
        from host_brain.contracts.agent_runtime.contract_errors import (  # noqa: PLC0415
            SUB_AGENT_ADAPTER_INVALID,
            ContractViolation,
        )

        raise ContractViolation(
            SUB_AGENT_ADAPTER_INVALID,
            f"sub-agent registry capability is empty: {entry.agent_id}",
        )
    validate_planner_target_binding(
        child,
        agent_id=entry.agent_id,
        require_binding=True,
    )
    resolved_timeout_ms = int(
        entry.default_timeout_ms if timeout_ms is None else timeout_ms
    )
    resolved_timeout_ms = max(1, resolved_timeout_ms)
    spec = SubAgentSpec(
        agent_id=entry.agent_id,
        capability_requirements=tuple(child.capability_requirements),
        allowed_actions=tuple(child.allowed_actions),
        timeout_ms=resolved_timeout_ms,
        max_attempts=1,
        context_contract={
            "task_source": "child_work_order",
            "requires_lineage": True,
        },
    )
    resolved_context_refs_list: list[str] = []
    for item in (*tuple(child.input_refs), *tuple(context_refs or ())):
        normalized = str(item or "").strip()
        if normalized and normalized not in resolved_context_refs_list:
            resolved_context_refs_list.append(normalized)
    resolved_context_refs = tuple(resolved_context_refs_list)
    contract = SubAgentRequestAdapter.adapt(
        child_work_order=child,
        spec=spec,
        request_id=request_id,
        session_id=session_id,
        context_refs=resolved_context_refs,
    )
    legacy = contract.to_legacy_mapping()
    protected_context = dict(legacy.get("context") or {})
    merged_context = dict(context or {})
    merged_context.update(protected_context)
    merged_context["canonical_sub_agent_request"] = contract.to_dict()
    return SubAgentRequest(
        agent_id=str(legacy.get("agent_id") or "").strip(),
        task=str(legacy.get("task") or "").strip(),
        user_message=str(legacy.get("user_message") or "").strip(),
        request_id=str(legacy.get("request_id") or "").strip(),
        session_id=str(legacy.get("session_id") or "").strip(),
        context=merged_context,
        timeout_ms=int(legacy.get("timeout_ms") or resolved_timeout_ms),
    )


class SubAgentDispatcher:
    """Fail-closed compatibility gate for the retired direct dispatcher.

    ``dispatch`` deliberately does not load a handler, construct a gateway,
    call a model, or write the old ``skill_exec_log``.  It owns no mutable
    handler map, gateway, ledger, or private reactivation helper.
    """

    __slots__ = ()

    async def dispatch(self, req: SubAgentRequest) -> SubAgentResponse:
        """Reject the retired direct route before any handler can load."""
        return SubAgentResponse(
            success=False,
            agent_id=str(getattr(req, "agent_id", "") or "").strip(),
            error=LEGACY_SUB_AGENT_DISPATCH_RETIRED,
            audit_refs={
                "status": "REJECTED",
                "reason_code": LEGACY_SUB_AGENT_DISPATCH_RETIRED,
                "required_path": (
                    "HostCommand -> immutable TaskGraph -> "
                    "GovernedDelegateDispatcher"
                ),
                "side_effects_executed": False,
            },
            latency_ms=0,
        )
