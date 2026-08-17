"""World、Scene、Intent 与 Guardian 的聚焦应用局部组合接线。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from host_brain.composition.task_runtime import ProcessTaskRuntimeContainer
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.app.resource_manager_status_journal import (
    RuntimeEventResourceManagerStatusJournal,
)
from host_brain.persistence.hard_state_journal import SQLiteHardStateJournal
from host_brain.persistence.host_mode_policy_journal import (
    SQLiteHostModePolicyJournal,
)
from host_brain.persistence.long_term_goal_repository import (
    SQLiteLongTermGoalAuthority,
)
from host_brain.persistence.world_observation_journal import (
    SQLiteWorldObservationJournal,
)
from host_brain.runtime.action_preview_resolver import (
    ProductionActionPreviewAuthorityResolver,
)
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.runtime.intent_authority import IntentPolicyIdentityV1
from host_brain.runtime.production_composition import build_runtime_context_composition
from host_brain.runtime.production_intent_authority import (
    ProductionIntentAuthorityPort,
)
from host_brain.runtime.world_source_verifier import RuntimeEventWorldSourceVerifier


@dataclass(frozen=True, slots=True)
class ServiceCatalogGuardianObservation:
    """为 ASGI lifespan 组合、但仍由其拥有的轮询工作。

    观察器不拥有 rollout store 或 guardian；它只等待一个确定 operation 的终态收据，
    被取消时直接传播取消，让生命周期统一负责后台任务回收。
    """

    store: Any
    observer: Any
    operation_id: str
    logger: Any

    async def observe_terminal_receipt(
        self,
        *,
        on_activation_started: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """有界轮询终态收据，并仅在激活确实开始时回调；超时只记录，不伪造结果。"""
        try:
            for _attempt in range(240):
                receipt = await asyncio.to_thread(
                    self.store.terminal_receipt,
                    self.operation_id,
                )
                if receipt is not None:
                    await asyncio.to_thread(
                        self.observer.observe_operation,
                        self.operation_id,
                    )
                    if (
                        str(receipt.get("event_type") or "")
                        == "ACTIVATION_STARTED"
                        and on_activation_started is not None
                    ):
                        await on_activation_started()
                    return
                await asyncio.sleep(0.25)
            self.logger.error(
                "HOST_SERVICE_CATALOG_TERMINAL_RECEIPT_TIMEOUT",
                extra={"operation_id": self.operation_id},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.error(
                "HOST_SERVICE_CATALOG_GUARDIAN_OBSERVE_FAILED",
                extra={"operation_id": self.operation_id},
                exc_info=True,
            )


def compose_service_catalog_guardian_observation(
    *,
    canonical_db_path: str,
    guardian: Any,
    operation_id: str,
    logger: Any,
) -> ServiceCatalogGuardianObservation:
    """把一个 staged Catalog operation 绑定给应用局部 Guardian authority。"""

    from host_brain.persistence.service_catalog_rollout_repository import (  # noqa: PLC0415
        SQLiteServiceCatalogRolloutRepository,
    )
    from host_brain.service_catalog.rollout_authority import (  # noqa: PLC0415
        ServiceCatalogRolloutGuardianObserver,
    )

    store = SQLiteServiceCatalogRolloutRepository(canonical_db_path)
    return ServiceCatalogGuardianObservation(
        store=store,
        observer=ServiceCatalogRolloutGuardianObserver(
            store=store,
            guardian=guardian,
        ),
        operation_id=str(operation_id),
        logger=logger,
    )


def compose_host_runtime_context(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    embodiment_runtime: Any,
    environment_observation_provider: Callable[..., Mapping[str, Any]],
) -> RuntimeContextApplicationService:
    """构建唯一的应用局部 World/Scene/Intent/Guardian 对象图。

    每个耐久 adapter 都通过 embodiment runtime 的续期 authority 附带同一 scope，防止
    已过期 Host 或平行实例向 canonical DB 写入 observation、目标或 intent 决策。
    """

    runtime_scope_id = str(
        getattr(embodiment_runtime, "runtime_scope_id", "") or ""
    ).strip()
    if not runtime_scope_id:
        raise RuntimeError("HOST_RUNTIME_CONTEXT_SCOPE_UNAVAILABLE")

    def runtime_authority_provider() -> dict[str, Any]:
        renew = getattr(embodiment_runtime, "renew_authority", None)
        if not callable(renew):
            raise RuntimeError("HOST_RUNTIME_CONTEXT_AUTHORITY_UNAVAILABLE")
        claim = renew()
        if not isinstance(claim, Mapping):
            raise RuntimeError("HOST_RUNTIME_CONTEXT_AUTHORITY_INVALID")
        return {**dict(claim), "runtime_scope_id": runtime_scope_id}

    event_store = getattr(task_runtime, "event_store", None)
    command_repository = getattr(task_runtime, "command_repository", None)
    policy_version = "natsume.intent-arbiter.production.v1"
    policy_identity = IntentPolicyIdentityV1(
        policy_ref="policy://intent-arbiter/production-v1",
        policy_digest=hashlib.sha256(policy_version.encode("utf-8")).hexdigest(),
    )
    observation_journal = SQLiteWorldObservationJournal(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
        source_event_verifier=RuntimeEventWorldSourceVerifier(event_store),
    )
    long_term_goal_authority = SQLiteLongTermGoalAuthority(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    long_term_goal_authority.assert_schema_ready()
    composition = build_runtime_context_composition(
        event_store=event_store,
        observation_port=observation_journal,
        long_term_goal_authority=long_term_goal_authority,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
        intent_policy_identity=policy_identity,
        intent_authority_port_factory=(
            lambda scene_resolver: ProductionIntentAuthorityPort(
                command_repository=command_repository,
                scene_resolver=scene_resolver,
                governor_ledger=ledger,
                policy_identity=policy_identity,
                long_term_goal_authority=long_term_goal_authority,
            )
        ),
        environment_observation_provider=environment_observation_provider,
    )
    return RuntimeContextApplicationService(composition)


def compose_action_preview_service(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    runtime_context_application: RuntimeContextApplicationService,
) -> ActionPreviewService:
    """将 Preview 校验绑定到同一耐久 Runtime Context authority，不能脱离 node grant 单独预览。"""

    composition = runtime_context_application.composition
    authority_resolver = ProductionActionPreviewAuthorityResolver(
        command_repository=task_runtime.command_repository,
        task_store=task_runtime.task_store,
        node_grant_verifier=DurableNodeGrantVerifier(
            ledger=ledger,
            review_store=task_runtime.review_continuation_store,
        ),
        runtime_context_application=runtime_context_application,
    )
    return ActionPreviewService(
        runtime_event_store=composition.event_store,
        guardian=composition.guardian,
        authority_resolver=authority_resolver,
    )


def observe_runtime_startup(
    *,
    runtime_context_application: RuntimeContextApplicationService,
    startup_probe_statuses: Mapping[str, Any],
    observed_at_ms: int,
) -> None:
    """向 Guardian journal 追加确定性的启动观测；source ref 绑定当前快照与时间。"""

    guardian = runtime_context_application.composition.guardian
    guardian.observe_startup_batch(
        startup_probe_statuses,
        source_ref=(
            "startup-probe://host/"
            + hashlib.sha256(
                (
                    f"{guardian.snapshot().snapshot_digest}:"
                    f"{int(observed_at_ms)}"
                ).encode("utf-8")
            ).hexdigest()
        ),
        observed_at_ms=int(observed_at_ms),
    )


def bind_host_hard_state_runtime(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    embodiment_runtime: Any,
    host_lifecycle: Any,
) -> Mapping[str, object]:
    """仅在迁移完成且 Host fence 已激活后绑定 HardState 与模式策略 journal。

    两个 journal 使用同一 runtime scope/renewing authority：任一缺失或绑定结果无效都
    立即中止启动，不能发布只恢复一半的模式控制面。
    """

    runtime_scope_id = str(
        getattr(embodiment_runtime, "runtime_scope_id", "") or ""
    ).strip()
    if not runtime_scope_id:
        raise RuntimeError("HOST_HARD_STATE_SCOPE_UNAVAILABLE")

    def runtime_authority_provider() -> dict[str, Any]:
        renew = getattr(embodiment_runtime, "renew_authority", None)
        if not callable(renew):
            raise RuntimeError("HOST_HARD_STATE_AUTHORITY_UNAVAILABLE")
        claim = renew()
        if not isinstance(claim, Mapping):
            raise RuntimeError("HOST_HARD_STATE_AUTHORITY_INVALID")
        return {**dict(claim), "runtime_scope_id": runtime_scope_id}

    journal = SQLiteHardStateJournal(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    bind = getattr(host_lifecycle, "bind_hard_state_journal", None)
    if not callable(bind):
        raise RuntimeError("HOST_HARD_STATE_COMPOSITION_UNAVAILABLE")
    result = bind(journal)
    if not isinstance(result, Mapping):
        raise RuntimeError("HOST_HARD_STATE_BIND_RESULT_INVALID")
    policy_journal = SQLiteHostModePolicyJournal(
        task_runtime.canonical_db_path,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )
    bind_policy = getattr(host_lifecycle, "bind_mode_policy_journal", None)
    if not callable(bind_policy):
        raise RuntimeError("HOST_MODE_POLICY_COMPOSITION_UNAVAILABLE")
    policy_result = bind_policy(policy_journal)
    if not isinstance(policy_result, Mapping):
        raise RuntimeError("HOST_MODE_POLICY_BIND_RESULT_INVALID")
    return {
        **dict(result),
        "mode_policy_revision": policy_result.get("revision"),
        "mode_policy_event_digest": policy_result.get("event_digest"),
    }


def compose_resource_manager_status_journal(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    embodiment_runtime: Any,
) -> RuntimeEventResourceManagerStatusJournal:
    """把 ResourceManager 状态写入绑定到精确 live Host runtime fence。"""

    runtime_scope_id = str(
        getattr(embodiment_runtime, "runtime_scope_id", "") or ""
    ).strip()
    if not runtime_scope_id:
        raise RuntimeError("HOST_RESOURCE_MANAGER_SCOPE_UNAVAILABLE")

    def runtime_authority_provider() -> dict[str, Any]:
        renew = getattr(embodiment_runtime, "renew_authority", None)
        if not callable(renew):
            raise RuntimeError("HOST_RESOURCE_MANAGER_AUTHORITY_UNAVAILABLE")
        claim = renew()
        if not isinstance(claim, Mapping):
            raise RuntimeError("HOST_RESOURCE_MANAGER_AUTHORITY_INVALID")
        return {**dict(claim), "runtime_scope_id": runtime_scope_id}

    event_store = getattr(task_runtime, "event_store", None)
    if event_store is None:
        raise RuntimeError("HOST_RESOURCE_MANAGER_EVENT_STORE_UNAVAILABLE")
    return RuntimeEventResourceManagerStatusJournal(
        event_store=event_store,
        runtime_scope_id=runtime_scope_id,
        runtime_authority_provider=runtime_authority_provider,
    )


def start_host_runtime_context_services(
    *,
    task_runtime: ProcessTaskRuntimeContainer,
    ledger: Any,
    embodiment_runtime: Any,
    host_lifecycle: Any,
    startup_probe_statuses: dict[str, str],
    runtime_settings: Any,
    scheduler_settings: Any,
    observed_at_ms: int,
) -> tuple[RuntimeContextApplicationService, ActionPreviewService]:
    """先组合 Context，再启动受 fence 约束的 ResourceManager writer。

    activity monitor 先启动以提供模式环境输入；资源管理器只能在 guardian/context 已可用后
    写状态。禁用开关被记录为显式 SKIPPED，避免把未启动当作健康。
    """

    activity_monitor = host_lifecycle.start_activity_monitor(ledger=ledger)
    startup_probe_statuses["activity_monitor"] = (
        f"OK (gaming_check={activity_monitor._gaming_interval}s, "
        f"idle_check={activity_monitor._idle_interval}s, "
        "hard_state_journal=bound)"
    )
    application = compose_host_runtime_context(
        task_runtime=task_runtime,
        ledger=ledger,
        embodiment_runtime=embodiment_runtime,
        environment_observation_provider=runtime_environment_observation_provider(
            host_lifecycle.get_mode_transition_service()
        ),
    )
    preview = compose_action_preview_service(
        task_runtime=task_runtime,
        ledger=ledger,
        runtime_context_application=application,
    )
    if not bool(scheduler_settings.background_tasks_enabled):
        startup_probe_statuses["system_resource_manager"] = (
            "SKIPPED: HOST_BACKGROUND_TASKS_ENABLED=false"
        )
    elif not bool(runtime_settings.resource_manager_enabled):
        startup_probe_statuses["system_resource_manager"] = (
            "SKIPPED: HOST_RESOURCE_MANAGER_ENABLED=false"
        )
    else:
        manager = host_lifecycle.start_resource_manager(
            status_journal=compose_resource_manager_status_journal(
                task_runtime=task_runtime,
                embodiment_runtime=embodiment_runtime,
            ),
            degradation_state_provider=(
                application.composition.guardian.read_degradation_state
            ),
            run_immediately=bool(
                runtime_settings.resource_manager_run_on_startup
            ),
        )
        status = manager.get_status()
        durability = dict(status.get("durability") or {})
        startup_probe_statuses["system_resource_manager"] = (
            "OK "
            f"(interval={status.get('interval_s')}s, "
            f"revision={durability.get('revision')}, "
            f"durability={durability.get('status')})"
        )
    observe_runtime_startup(
        runtime_context_application=application,
        startup_probe_statuses=startup_probe_statuses,
        observed_at_ms=observed_at_ms,
    )
    return application, preview


def runtime_environment_observation_provider(
    mode_transition_service: Any,
) -> Callable[..., Mapping[str, Any]]:
    """冻结 Runtime Context 图的一个应用局部环境读取器。

    reader 将 Host sys_mode 映射为规范 activity state；未知或错误的模式状态失败关闭，
    不能以默认 NORMAL 继续影响 intent/guardian 决策。
    """

    status_reader = getattr(mode_transition_service, "get_current_status", None)
    if not callable(status_reader):
        raise RuntimeError("HOST_RUNTIME_CONTEXT_MODE_AUTHORITY_UNAVAILABLE")

    def observe(*, now_ms: int) -> Mapping[str, Any]:
        from host_brain.runtime.environment_state import (  # noqa: PLC0415
            build_environment_snapshot,
        )

        snapshot = dict(build_environment_snapshot(now_ms=int(now_ms)))
        raw_status = status_reader()
        if not isinstance(raw_status, Mapping):
            raise RuntimeError("HOST_RUNTIME_CONTEXT_MODE_AUTHORITY_INVALID")
        mode = str(raw_status.get("sys_mode") or "").strip().lower()
        activity = {
            "normal": "NORMAL",
            "focus": "FOCUS",
            "gaming": "GAMING",
            "sleep": "SLEEP",
            "maintenance": "MAINTENANCE",
            "locked": "MAINTENANCE",
        }.get(mode)
        if activity is None:
            raise RuntimeError("HOST_RUNTIME_CONTEXT_MODE_UNSUPPORTED")
        snapshot["activity_state"] = activity
        snapshot["mode_identity"] = mode
        return snapshot

    return observe


__all__ = [
    "ServiceCatalogGuardianObservation",
    "bind_host_hard_state_runtime",
    "compose_action_preview_service",
    "compose_host_runtime_context",
    "compose_resource_manager_status_journal",
    "compose_service_catalog_guardian_observation",
    "observe_runtime_startup",
    "runtime_environment_observation_provider",
    "start_host_runtime_context_services",
]
