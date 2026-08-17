"""Host 受治理运行时的失败关闭 shutdown authority。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
from typing import Any, Callable


_RUNTIME_STATE_FIELDS = (
    "command_ingress_container",
    "memory_service_container",
    "governed_internal_runtime",
    "governed_scheduler_runtime",
    "governed_operator_runtime",
    "scheduler_container",
    "task_review_command_service",
    "task_graph_cancellation_command_service",
    "task_operation_reconciliation_command_service",
    "task_operation_reacquisition_continuation",
    "task_effect_reconciliation_command_service",
    "task_terminal_reconciliation_operator_enabled",
    "task_terminal_reconciliation_read_authority",
    "governed_host_runtime",
    "task_runtime_container",
    "service_catalog_rollout_store",
    "service_catalog_rollout_observer",
    "runtime_context_composition",
    "service_health_heartbeat",
)
_SHUTDOWN_REASON = "HOST_RUNTIME_SHUTDOWN_RETRY_REQUIRED"
_WORKSPACE_QUIESCE_ATTEMPTS = 2


@dataclass(frozen=True, slots=True)
class HostRuntimeShutdownFailure:
    component: str
    reason_code: str


class HostRuntimeShutdownError(RuntimeError):
    """稳定的关闭失败聚合，明确说明仍需保留 live authority 还是可安全释放。"""

    reason_code = _SHUTDOWN_REASON

    def __init__(
        self,
        failures: tuple[HostRuntimeShutdownFailure, ...],
        *,
        dependencies_preserved: bool,
    ) -> None:
        self.failures = failures
        self.dependencies_preserved = bool(dependencies_preserved)
        components = ",".join(failure.component for failure in failures)
        super().__init__(f"{self.reason_code}:{components}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "dependencies_preserved": self.dependencies_preserved,
            "retry_required": True,
            "failures": [
                {
                    "component": failure.component,
                    "reason_code": failure.reason_code,
                }
                for failure in self.failures
            ],
        }


def withdraw_published_runtime_state(application: Any) -> None:
    """仅在 owner 已关闭后撤销应用局部 provider，阻止新请求拿到已释放依赖。"""

    for field_name in _RUNTIME_STATE_FIELDS:
        setattr(application.state, field_name, None)


async def drain_obs_operator_admission(security: Any | None) -> None:
    """在撤销受治理执行前排空已准入 OBS command，先截断最外层 producer。"""

    composition = getattr(security, "obs_command_composition", None)
    if composition is not None:
        await composition.aclose()


async def shutdown_host_runtime_authority(
    *,
    application: Any,
    security: Any | None,
    scheduler: Any | None,
    task_runtime: Any | None,
    agent_workspace_container: Any | None,
    workspace_quiesce: Callable[..., Any],
    host_lifecycle: Any,
    logger: Any,
    embodiment_authority_task: Any | None,
    reset_life_command_authority: Callable[[], Any],
    embodiment_runtime: Any | None,
    close_governance_runtime: Callable[[], Any],
    close_authority_runtime: Callable[[], Any],
    withdraw_memory_service_container: Callable[[Any], Any],
    memory: Any | None,
    command_ingress: Any | None,
    tool_composition: Any,
    on_dependencies_released: Callable[[], Any],
) -> None:
    """关闭一个 Host epoch，但在不确定时不销毁重试所需 authority。

    OBS admission 是最外层 producer 边界，只有它排空后才可停止 live Task Runtime。
    runtime 的 store 与 effect service 是关闭/重试依赖，因此 live-runtime 关闭失败时
    必须保留它们和全部已发布引用。未发布或未激活的启动失败则无执行 authority 需保留，
    但仍会尽力关闭每个已构造资源，最后以稳定聚合错误报告。
    """

    failures: list[HostRuntimeShutdownFailure] = []
    first_error: BaseException | None = None
    runtime_live = _task_runtime_is_live(application, task_runtime)

    error = await _attempt(
        "obs_operator_admission",
        lambda: drain_obs_operator_admission(security),
        failures,
        logger,
    )
    first_error = first_error or error
    if error is not None and runtime_live:
        _raise_preserved_shutdown(application, failures, error)

    if scheduler is not None:
        error = await _attempt(
            "scheduler_clock",
            scheduler.stop_clock,
            failures,
            logger,
        )
        first_error = first_error or error
        if error is not None and runtime_live:
            _raise_preserved_shutdown(application, failures, error)

    error = await _attempt(
        "resource_manager",
        lambda: host_lifecycle.stop_resource_manager(logger=logger),
        failures,
        logger,
    )
    first_error = first_error or error
    if error is not None and runtime_live:
        _raise_preserved_shutdown(application, failures, error)

    if task_runtime is not None:
        error = await _attempt(
            "task_runtime",
            task_runtime.aclose,
            failures,
            logger,
        )
        first_error = first_error or error
        if error is not None and runtime_live:
            _raise_preserved_shutdown(application, failures, error)

    # The execution authority is now closed (or never became live).  Resource
    # release is best effort and exhaustive; no individual close may skip the
    # remaining partial-startup resources.
    workspace_error = await _attempt_with_retry(
        "agent_workspace",
        lambda: workspace_quiesce(
            application,
            agent_workspace_container,
            task_runtime,
        ),
        failures,
        logger,
        attempts=_WORKSPACE_QUIESCE_ATTEMPTS,
    )
    first_error = first_error or workspace_error
    if workspace_error is not None:
        # A cleanup worker that did not stop remains a live process-owned
        # resource even when Task Runtime activation never completed.  Never
        # discard its only retry handle during partial-startup rollback.
        _raise_preserved_shutdown(application, failures, workspace_error)

    pre_release_calls = (
        (
            "host_hard_state",
            lambda: host_lifecycle.stop_activity_monitor(logger=logger),
        ),
    )
    post_release_calls = (
        ("life_command_authority", reset_life_command_authority),
        (
            "embodiment_runtime",
            lambda: embodiment_runtime.close()
            if embodiment_runtime is not None
            else None,
        ),
        ("governance_runtime", close_governance_runtime),
        (
            "memory_publication",
            lambda: withdraw_memory_service_container(memory),
        ),
        ("memory", lambda: memory.close() if memory is not None else None),
        (
            "command_ingress",
            lambda: command_ingress.aclose()
            if command_ingress is not None
            else None,
        ),
        ("security", lambda: security.aclose() if security is not None else None),
        ("tool_composition", tool_composition.clear),
        ("authority_runtime", close_authority_runtime),
    )
    for component, action in pre_release_calls:
        error = await _attempt(component, action, failures, logger)
        first_error = first_error or error
    error = await _cancel_owned_task(
        embodiment_authority_task,
        component="embodiment_authority",
        failures=failures,
        logger=logger,
    )
    first_error = first_error or error
    for component, action in post_release_calls:
        error = await _attempt(component, action, failures, logger)
        first_error = first_error or error

    application.state.agent_workspace_container = None
    application.state.embodiment_runtime = None
    application.state.canon_container = None
    application.state.security_service_container = None
    withdraw_published_runtime_state(application)
    error = await _attempt(
        "process_reference_withdrawal",
        on_dependencies_released,
        failures,
        logger,
    )
    first_error = first_error or error

    if failures:
        shutdown_error = HostRuntimeShutdownError(
            tuple(failures),
            dependencies_preserved=False,
        )
        application.state.host_runtime_shutdown_error = shutdown_error.to_dict()
        raise shutdown_error from first_error
    application.state.host_runtime_shutdown_error = None


def _task_runtime_is_live(application: Any, task_runtime: Any | None) -> bool:
    """根据发布、激活或重试标记判断是否必须保留 runtime 依赖。"""
    if task_runtime is None:
        return False
    published = getattr(application.state, "task_runtime_container", None)
    return bool(
        published is task_runtime
        or getattr(task_runtime, "activated", False)
        or getattr(task_runtime, "_lifecycle_retry_required", False)
    )


async def _attempt(
    component: str,
    action: Callable[[], Any],
    failures: list[HostRuntimeShutdownFailure],
    logger: Any,
) -> BaseException | None:
    """执行一个关闭动作并收集失败，单项失败不阻止后续可独立资源回收。"""
    try:
        value = action()
        if inspect.isawaitable(value):
            await value
        return None
    except BaseException as exc:
        failures.append(
            HostRuntimeShutdownFailure(
                component=component,
                reason_code=f"{component.upper()}_CLOSE_FAILED",
            )
        )
        log_error = getattr(logger, "error", None)
        if callable(log_error):
            log_error(
                "HOST_RUNTIME_SHUTDOWN_COMPONENT_FAILED:%s",
                component,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        return exc


async def _cancel_owned_task(
    task: Any | None,
    *,
    component: str,
    failures: list[HostRuntimeShutdownFailure],
    logger: Any,
) -> BaseException | None:
    """取消 owner 创建的后台任务；正常 CancelledError 是预期关闭路径。"""
    if task is None:
        return None
    try:
        task.cancel()
        await task
    except asyncio.CancelledError:
        return None
    except BaseException as exc:
        failures.append(
            HostRuntimeShutdownFailure(
                component=component,
                reason_code=f"{component.upper()}_CLOSE_FAILED",
            )
        )
        log_error = getattr(logger, "error", None)
        if callable(log_error):
            log_error(
                "HOST_RUNTIME_SHUTDOWN_COMPONENT_FAILED:%s",
                component,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
        return exc
    return None


async def _attempt_with_retry(
    component: str,
    action: Callable[[], Any],
    failures: list[HostRuntimeShutdownFailure],
    logger: Any,
    *,
    attempts: int,
) -> BaseException | None:
    """对限定组件进行有界重试，耗尽后记录一次稳定的关闭失败。"""
    last_error: BaseException | None = None
    for attempt_index in range(max(1, int(attempts))):
        try:
            value = action()
            if inspect.isawaitable(value):
                await value
            return None
        except BaseException as exc:
            last_error = exc
            if attempt_index + 1 < attempts:
                log_warning = getattr(logger, "warning", None)
                if callable(log_warning):
                    log_warning(
                        "HOST_RUNTIME_SHUTDOWN_COMPONENT_RETRY:%s:%s",
                        component,
                        attempt_index + 1,
                    )
                continue
    assert last_error is not None
    failures.append(
        HostRuntimeShutdownFailure(
            component=component,
            reason_code=f"{component.upper()}_CLOSE_FAILED",
        )
    )
    log_error = getattr(logger, "error", None)
    if callable(log_error):
        log_error(
            "HOST_RUNTIME_SHUTDOWN_COMPONENT_FAILED:%s",
            component,
            exc_info=(
                type(last_error),
                last_error,
                last_error.__traceback__,
            ),
        )
    return last_error


def _raise_preserved_shutdown(
    application: Any,
    failures: list[HostRuntimeShutdownFailure],
    cause: BaseException,
) -> None:
    """把保留依赖的关闭失败写入应用状态，供下一次生命周期重试读取。"""
    shutdown_error = HostRuntimeShutdownError(
        tuple(failures),
        dependencies_preserved=True,
    )
    application.state.host_runtime_shutdown_error = shutdown_error.to_dict()
    raise shutdown_error from cause


__all__ = [
    "HostRuntimeShutdownError",
    "HostRuntimeShutdownFailure",
    "drain_obs_operator_admission",
    "shutdown_host_runtime_authority",
    "withdraw_published_runtime_state",
]
