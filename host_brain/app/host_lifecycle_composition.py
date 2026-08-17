"""Host 生命周期服务的应用局部所有权。

组合根只解析一次进程协作者，随后仅发布一个 FastAPI application lifespan 所拥有的
服务。请求与 worker 路径不得经由可变的进程 service locator 重新发现
ActivityMonitor 或 SystemResourceManager，以免多个应用实例交叉接管资源。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

from host_brain.app.host_hard_state import HostHardStateOwner
from host_brain.app.host_mode_policy import HostModePolicyOwner
from host_brain.contracts.host_hard_state import (
    HostHardStateCommitV1,
    HostHardStateJournalPort,
)
from host_brain.contracts.host_lifecycle import (
    HostLifecycleApplicationPorts,
    HostLifecycleScopeError,
    KeywordFactory,
)
from host_brain.contracts.host_mode_policy import HostModePolicyJournalPort
from host_brain.core.ids import now_ms


@dataclass(slots=True)
class HostLifecycleContainer:
    """拥有一个 ASGI 应用的 ActivityMonitor 与 SystemResourceManager。

    ``start()`` 前即暂存候选实例，因此部分启动失败仍能回收；仅在启动成功后才向请求
    路径发布。``aclose`` 在等待清理前撤销发布，并确保每个 ``begin`` epoch 只清理
    一次，防止并发关闭复用已释放的服务。
    """

    ports: HostLifecycleApplicationPorts
    initial_sys_mode: str = "normal"
    require_durable_hard_state: bool = False
    endocrine_resolver: KeywordFactory | None = field(default=None, repr=False)
    _epoch: int = field(default=0, init=False, repr=False)
    _open: bool = field(default=False, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)
    _mode_transition_service: Any | None = field(default=None, init=False, repr=False)
    _owned_activity_monitor: Any | None = field(default=None, init=False, repr=False)
    _activity_monitor: Any | None = field(default=None, init=False, repr=False)
    _owned_resource_manager: Any | None = field(default=None, init=False, repr=False)
    _resource_manager: Any | None = field(default=None, init=False, repr=False)
    _hard_state_owner: HostHardStateOwner = field(init=False, repr=False)
    _mode_policy_owner: HostModePolicyOwner = field(init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        """先建立 HardState 与模式策略所有者，后续 producer 才能共享同一权威快照。"""
        bootstrap_kwargs: dict[str, Any] = {
            "initial_sys_mode": self.initial_sys_mode,
            "require_durable": self.require_durable_hard_state,
        }
        if self.endocrine_resolver is not None:
            bootstrap_kwargs["endocrine_resolver"] = self.endocrine_resolver
        self._hard_state_owner = HostHardStateOwner.bootstrap(**bootstrap_kwargs)
        self._mode_policy_owner = HostModePolicyOwner.bootstrap(
            initial_last_interaction_ts_ms=now_ms(),
            require_durable=self.require_durable_hard_state,
        )

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def is_open(self) -> bool:
        with self._lock:
            return self._open

    def begin(self) -> int:
        """开启不继承旧实例的新 lifespan epoch，并拒绝脏状态重入。"""

        with self._lock:
            if self._open:
                raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_ALREADY_OPEN")
            if self._closing:
                raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_CLOSING")
            if any(
                service is not None
                for service in (
                    self._mode_transition_service,
                    self._owned_activity_monitor,
                    self._activity_monitor,
                    self._owned_resource_manager,
                    self._resource_manager,
                )
            ):
                raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_NOT_CLEAN")
            self._epoch += 1
            self._open = True
            return self._epoch

    def _require_open(self) -> None:
        if not self._open:
            raise HostLifecycleScopeError("HOST_LIFECYCLE_SCOPE_NOT_OPEN")

    def start_activity_monitor(self, *, ledger: Any) -> Any:
        """构造、启动并发布本应用的活动监控器。

        若 mode service 或 monitor 启动失败，候选实例在锁内撤销并尽力停止，避免请求
        看到半启动 producer 或后续 epoch 继承其资源。
        """

        with self._lock:
            self._require_open()
            if self._owned_activity_monitor is not None:
                raise HostLifecycleScopeError("ACTIVITY_MONITOR_ALREADY_OWNED")
            monitor: Any | None = None
            try:
                mode_service = self.ports.mode_transition_service_factory(
                    get_hard_state=self.get_hard_state,
                    set_sys_mode=self.set_sys_mode,
                    history_provider=self.get_sys_mode_history,
                    exact_history_provider=self.reopen_sys_mode_transition,
                    mode_policy=self._mode_policy_owner,
                    ledger=ledger,
                )
                monitor = self.ports.activity_monitor_factory(
                    mode_transition_service=mode_service,
                )
                self._owned_activity_monitor = monitor
                monitor.start()
            except Exception:
                self._owned_activity_monitor = None
                if monitor is not None:
                    try:
                        monitor.stop()
                    except Exception:
                        pass
                raise
            self._mode_transition_service = mode_service
            self._activity_monitor = monitor
            return monitor

    def start_resource_manager(
        self,
        *,
        status_journal: Any,
        degradation_state_provider: Callable[[], Mapping[str, Any] | None] | None,
        run_immediately: bool,
    ) -> Any:
        """构造、启动并发布资源管理器；状态 journal 缺失时失败关闭。"""

        if status_journal is None:
            raise HostLifecycleScopeError(
                "RESOURCE_MANAGER_STATUS_JOURNAL_NOT_INJECTED"
            )
        with self._lock:
            self._require_open()
            if self._owned_resource_manager is not None:
                raise HostLifecycleScopeError("RESOURCE_MANAGER_ALREADY_OWNED")
            manager = self.ports.resource_manager_factory(
                model_attempt_service=self.ports.model_attempt_service(),
                degradation_state_provider=degradation_state_provider,
                status_journal=status_journal,
            )
            self._owned_resource_manager = manager
            try:
                manager.start(run_immediately=bool(run_immediately))
            except Exception:
                self._owned_resource_manager = None
                try:
                    manager.stop()
                except Exception:
                    pass
                raise
            self._resource_manager = manager
            return manager

    def get_activity_monitor(self, *args: Any, **kwargs: Any) -> Any | None:
        """Return only the successfully started app-local monitor."""

        del args, kwargs
        with self._lock:
            return self._activity_monitor

    def get_resource_manager(self, *args: Any, **kwargs: Any) -> Any | None:
        """Return only the successfully started app-local manager."""

        del args, kwargs
        with self._lock:
            return self._resource_manager

    def get_mode_transition_service(self) -> Any | None:
        with self._lock:
            return self._mode_transition_service

    def get_hard_state(self) -> Any:
        """Return this application's immutable HardState snapshot."""

        return self._hard_state_owner.get_hard_state()

    def set_sys_mode(
        self,
        target_mode: str,
        **metadata: Any,
    ) -> dict[str, Any]:
        """Atomically switch this application's live system mode."""

        return self._hard_state_owner.set_sys_mode(target_mode, **metadata)

    def bind_hard_state_journal(
        self, journal: HostHardStateJournalPort
    ) -> dict[str, object]:
        """绑定并恢复本 lifespan 的权威 HardState journal，再公开其摘要。"""

        snapshot = self._hard_state_owner.bind_durable_journal(journal)
        return {
            "runtime_scope_id": snapshot.runtime_scope_id,
            "revision": snapshot.revision,
            "sys_mode": snapshot.sys_mode,
            "event_id": snapshot.event_id,
            "event_digest": snapshot.event_digest,
            "updated_at_ms": snapshot.updated_at_ms,
        }

    def bind_mode_policy_journal(
        self,
        journal: HostModePolicyJournalPort,
    ) -> dict[str, object]:
        """在 producer 启动前绑定并恢复权威自动规则，避免冷启动规则漂移。"""

        snapshot = self._mode_policy_owner.bind_durable_journal(journal)
        return {
            "runtime_scope_id": snapshot.runtime_scope_id,
            "revision": snapshot.revision,
            "auto_rules": dict(snapshot.auto_rules),
            "last_interaction_ts_ms": snapshot.last_interaction_ts_ms,
            "operation_id": snapshot.operation_id,
            "event_digest": snapshot.event_digest,
            "updated_at_ms": snapshot.updated_at_ms,
        }

    def get_mode_policy_snapshot(self) -> Any:
        return self._mode_policy_owner.snapshot()

    def get_sys_mode_history(self, *, limit: int = 100) -> list[dict[str, object]]:
        return self._hard_state_owner.durable_history(limit=limit)

    def reopen_sys_mode_transition(
        self,
        *,
        event_id: str,
        target_mode: str,
        source: str,
        reason: str,
    ) -> HostHardStateCommitV1 | None:
        """经当前应用 scope 重开精确历史提交，不能按模糊条件重放模式切换。"""

        return self._hard_state_owner.reopen_sys_mode_transition(
            event_id=event_id,
            target_mode=target_mode,
            source=source,
            reason=reason,
        )

    def get_hard_state_durability_status(self) -> dict[str, object]:
        return self._hard_state_owner.durability_status()

    def get_endocrine_state(
        self,
        *,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        """Resolve endocrine projection from this application's HardState."""

        return self._hard_state_owner.get_endocrine_state(
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )

    def notify_user_interaction(self) -> None:
        with self._lock:
            monitor = self._activity_monitor
            if monitor is not None:
                monitor.notify_user_interaction()

    def stop_activity_monitor(self, *, logger: Any) -> None:
        """Stop mode producers before the Host runtime fence is released."""

        with self._lock:
            monitor = self._owned_activity_monitor
            self._owned_activity_monitor = None
            self._activity_monitor = None
            self._mode_transition_service = None
        if monitor is not None:
            try:
                monitor.stop()
                logger.info("ActivityMonitor 已停止")
            except Exception as exc:
                logger.warning("ActivityMonitor 停止失败: %s", exc)
        self._hard_state_owner.unbind_durable_journal()
        self._mode_policy_owner.unbind_durable_journal()

    def stop_resource_manager(self, *, logger: Any) -> None:
        """Stop the status writer while Task Runtime and Host lease are live."""

        with self._lock:
            manager = self._owned_resource_manager
        if manager is None:
            return
        manager.stop()
        with self._lock:
            if self._owned_resource_manager is manager:
                self._owned_resource_manager = None
                self._resource_manager = None
        logger.info("SystemResourceManager 已停止")

    async def aclose(self, *, logger: Any) -> None:
        """撤销发布并为当前 epoch 精确一次地停止所有服务。

        先清空可见引用再调用外部 ``stop``，让并发请求立刻失去已关闭服务的入口；清理
        单项失败只记录日志，仍继续回收其余服务并最终解除 closing 栅栏。
        """

        with self._lock:
            if not self._open:
                return
            self._open = False
            self._closing = True
            activity_monitor = self._owned_activity_monitor
            resource_manager = self._owned_resource_manager
            self._mode_transition_service = None
            self._owned_activity_monitor = None
            self._activity_monitor = None
            self._owned_resource_manager = None
            self._resource_manager = None
            self._hard_state_owner.unbind_durable_journal()
            self._mode_policy_owner.unbind_durable_journal()
        try:
            try:
                if resource_manager is not None:
                    resource_manager.stop()
                    logger.info("SystemResourceManager 已停止")
            except Exception as exc:
                logger.warning("SystemResourceManager 停止失败: %s", exc)
            try:
                if activity_monitor is not None:
                    activity_monitor.stop()
                    logger.info("ActivityMonitor 已停止")
            except Exception as exc:
                logger.warning("ActivityMonitor 停止失败: %s", exc)
        finally:
            with self._lock:
                self._closing = False


def build_host_lifecycle_container(
    *,
    initial_sys_mode: str,
    ports: HostLifecycleApplicationPorts,
    require_durable_hard_state: bool = True,
    endocrine_resolver: KeywordFactory | None = None,
) -> HostLifecycleContainer:
    """从显式冻结的应用端口构建一个 Host 生命周期所有者。

    遗留进程级服务到生产依赖的映射属于 ASGI 组合根。本模块故意没有 locator 回退，
    因而 Host、worker 或测试 scope 都必须声明自己确实拥有的依赖。
    """

    if not isinstance(ports, HostLifecycleApplicationPorts):
        raise TypeError("ports must be HostLifecycleApplicationPorts")
    return HostLifecycleContainer(
        initial_sys_mode=str(initial_sys_mode or "normal"),
        require_durable_hard_state=bool(require_durable_hard_state),
        endocrine_resolver=endocrine_resolver,
        ports=ports,
    )


__all__ = [
    "HostLifecycleApplicationPorts",
    "HostLifecycleContainer",
    "HostLifecycleScopeError",
    "build_host_lifecycle_container",
]
