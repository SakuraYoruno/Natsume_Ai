"""内置 Domain Pack 的平台组合。

只有本模块知道具体遗留 worker；Pack 模块只获得窄化 TaskPort，不能导入
scheduler、Governor、事件总线或持久化实现。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.browser_researcher import execute_browser_research_work_order
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.lightnovel_epub_builder import execute_lightnovel_epub_work_order
from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.pentest.agent.attack_planner import AttackPlanner
from host_brain.capability_plane.contracts import CapabilityGrant, CapabilityRequest

from .contracts import DomainPackRequest
from .media_translation import build_media_translation_pack
from .pentest import build_pentest_pack
from .ports import DomainPackPlatformPorts, DomainTaskPort
from .registry import DomainPackRegistry
from .research_browser import build_research_browser_pack


TaskHandler = Callable[[Mapping[str, Any]], Any]


class ProductionDomainTaskPort(DomainTaskPort):
    """精确任务处理器映射；缺失任务不能回退或热加载。"""

    def __init__(self, handlers: Mapping[str, TaskHandler]) -> None:
        self._handlers = dict(handlers)

    def execute(self, task_id: str, payload: Mapping[str, Any]) -> Any:
        # Pack 声明之外的 task_id 不会按名称动态解析，避免领域载荷借由字符串
        # 触及平台中的任意 worker。
        handler = self._handlers.get(str(task_id or "").strip())
        if handler is None:
            raise RuntimeError(f"DOMAIN_TASK_UNKNOWN:{task_id}")
        return handler(payload)


@dataclass(frozen=True, slots=True)
class ProductionDomainPackRuntime:
    registry: DomainPackRegistry
    ports: DomainPackPlatformPorts

    def dispatch(self, *, request: CapabilityRequest, grant: CapabilityGrant) -> Any:
        return self.registry.dispatch(
            DomainPackRequest(
                request_id=request.request_id,
                capability_id=request.capability_id,
                payload=request.args,
            ),
            ports=self.ports,
            capability_request=request,
            capability_grant=grant,
        )


def build_production_domain_pack_runtime(
    *,
    capability_broker: Any,
    model_attempt_service: Any,
    lightnovel_recommender_handler_provider: Callable[[], Callable[..., dict[str, Any]]],
    lifecycle_task_handlers: Mapping[str, TaskHandler] | None = None,
) -> ProductionDomainPackRuntime:
    registry = DomainPackRegistry()
    # 安装的是已知内置 Pack；真正执行仍需 Broker 在后续 dispatch 中签发并绑定
    # 本次请求的 grant，registry 本身不提供任何能力授权。
    for pack in (
        build_research_browser_pack(),
        build_media_translation_pack(),
        build_pentest_pack(),
    ):
        registry.install(pack)

    for capability_id in AGENT_PRODUCTION_SKILL_IDS:
        registry.resolve(capability_id)

    def _decode(payload: Mapping[str, Any]) -> tuple[AgentWorkOrder, AgentArtifactStore, Path]:
        order = AgentWorkOrder.from_mapping(dict(payload.get("work_order") or {}))
        store = AgentArtifactStore(str(payload.get("artifacts_root") or "") or None)
        run_dir = Path(str(payload.get("run_dir") or "")).expanduser()
        if not str(payload.get("run_dir") or "").strip():
            raise ValueError("domain task requires run_dir")
        return order, store, run_dir

    def _browser(payload: Mapping[str, Any]) -> dict[str, Any]:
        order, _store, run_dir = _decode(payload)
        return execute_browser_research_work_order(order, run_dir=run_dir)

    def _epub(payload: Mapping[str, Any]) -> dict[str, Any]:
        order, _store, run_dir = _decode(payload)
        return execute_lightnovel_epub_work_order(order, run_dir=run_dir)

    def _lightnovel(payload: Mapping[str, Any]) -> dict[str, Any]:
        order, _store, run_dir = _decode(payload)
        try:
            return lightnovel_recommender_handler_provider()(order, run_dir=run_dir)
        finally:
            close_after = str(os.environ.get("HOST_LIGHTNOVEL_RECOMMENDER_BROWSER_CLOSE_AFTER_RUN") or "").strip().lower()
            if close_after in {"1", "true", "yes", "on"}:
                try:
                    from host_brain.agent_runtime.browser_automation_service import get_browser_automation_service  # noqa: PLC0415

                    get_browser_automation_service(session_id="natsume_lightnovel").close(target="browser")
                except Exception:
                    pass

    def _agent(payload: Mapping[str, Any]) -> dict[str, Any]:
        order, store, _run_dir = _decode(payload)
        return AgentRuntimeExecutor(
            artifact_store=store,
            model_attempt_service=model_attempt_service,
            prompt_registry=getattr(model_attempt_service, "prompt_registry", None),
        ).execute(order)

    def _pentest_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
        order, _store, _run_dir = _decode(payload)
        # Pack 执行刻意只走确定性规划路径：计划不包含工具调用，后续任何动作仍要
        # 返回 CapabilityBroker 重新授权，不能把领域包当作 pentest 执行旁路。
        plan = AttackPlanner(replan_interval=5)._fallback_plan(  # noqa: SLF001
            graph=None,
            goal=order.objective,
            budget=None,
        )
        return {
            "status": "EXECUTED",
            "summary": "Pentest attack plan prepared; no tool was executed.",
            "result_text": plan.reasoning,
            "network_io": False,
            "process_io": False,
            "metadata": {
                "domain_pack": "pentest",
                "planner": "AttackPlanner",
                "plan": asdict(plan),
                "model_plan": dict(payload.get("model_plan") or {}),
            },
        }

    task_handlers: dict[str, TaskHandler] = {
        "research.browser.worker": _browser,
        "research.agent.worker": _agent,
        "media.lightnovel_recommender.worker": _lightnovel,
        "media.epub_builder.worker": _epub,
        "pentest.attack_planner": _pentest_plan,
    }
    lifecycle_handlers = dict(lifecycle_task_handlers or {})
    overlap = set(task_handlers) & set(lifecycle_handlers)
    # 生命周期阶段与普通 worker 同名会让同一声明对应两种副作用语义，因此组合期
    # 直接拒绝，而不是依赖后注册者覆盖前注册者。
    if overlap:
        raise ValueError(f"lifecycle task handler collides with worker task: {sorted(overlap)}")
    task_handlers.update(lifecycle_handlers)
    task_port = ProductionDomainTaskPort(task_handlers)
    return ProductionDomainPackRuntime(
        registry=registry,
        ports=DomainPackPlatformPorts(
            capability_broker=capability_broker,
            model_attempt_service=model_attempt_service,
            task_port=task_port,
        ),
    )


__all__ = [
    "ProductionDomainPackRuntime", "ProductionDomainTaskPort", "build_production_domain_pack_runtime",
]
