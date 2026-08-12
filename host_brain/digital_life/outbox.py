"""Digital Life 投递意图的租约/围栏调度器。

调度器先独占认领、持续续租，再调用传输适配器并写入不可变收据；重试只处理尚未完成的
意图，关系后效协调则从收据恢复，绝不能通过再次发送 Gateway 消息实现。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
from typing import Any

from host_brain.core.ids import now_ms
from natsume_contracts.life import LifeDispatchSummary, LifeOutboxMessage
from host_brain.digital_life.lease_heartbeat import (
    AsyncLifeLeaseHeartbeat,
    LifeLeaseHeartbeatError,
    SyncLifeLeaseHeartbeat,
)
from host_brain.digital_life.repository_port import LifeCommandRepositoryPort


class LifeOutboxDispatcher:
    """以 owner token 与租约串行化 Life outbox 的至少一次处理。

    收据存在即视为已投递，失败使用指数退避；处理期间心跳失效必须向上抛出，使上层
    不会在围栏不确定时伪造完成结果。
    """
    def __init__(
        self,
        repository: LifeCommandRepositoryPort,
        handler: Callable[
            [LifeOutboxMessage],
            Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None,
        ],
        *,
        owner_id: str = "life-outbox-dispatcher",
        clock: Callable[[], int] = now_ms,
        claim_lease_ms: int = 60_000,
        base_retry_ms: int = 1_000,
        max_retry_ms: int = 300_000,
        relationship_lifecycle_service_provider: Callable[[], Any] | None = None,
    ) -> None:
        self.repository = repository
        self.handler = handler
        self.owner_id = str(owner_id or "life-outbox-dispatcher")
        self._clock = clock
        self.claim_lease_ms = max(1, int(claim_lease_ms))
        self.base_retry_ms = max(1, int(base_retry_ms))
        self.max_retry_ms = max(self.base_retry_ms, int(max_retry_ms))
        self._relationship_lifecycle_service_provider = (
            relationship_lifecycle_service_provider
        )

    def dispatch_pending(
        self,
        *,
        limit: int = 100,
        dispatch_at_ms: int | None = None,
    ) -> LifeDispatchSummary:
        """同步认领并处理待投递意图；先恢复收据关联，再开始新的传输。"""
        claimed_at = int(
            dispatch_at_ms if dispatch_at_ms is not None else self._clock()
        )
        max_items = max(1, int(limit))
        # 每轮先从已完成回执补齐关系后效；恢复与新投递分离，确保崩溃后的语义
        # 缺口不会靠再次发送同一 Gateway 消息来“修复”。
        self.reconcile_completed_relationship_deliveries(limit=max_items)
        claimed = 0
        delivered = 0
        failed = 0
        for _index in range(max_items):
            messages = self.repository.claim_deliveries(
                owner_token=self.owner_id,
                now_ms=claimed_at,
                lease_ms=self.claim_lease_ms,
                limit=1,
            )
            if not messages:
                break
            message = messages[0]
            claimed += 1
            if self.repository.get_delivery_receipt(message.outbox_id) is not None:
                # 回执是外部投递已完成的不可变事实。即便租约重复领取，也只能计入
                # 已投递，绝不可再次调用 handler 造成对外重复动作。
                # 收据已是不可变事实；重复认领不能重新执行外部传输。
                delivered += 1
                continue
            self.repository.renew_delivery_claim(
                message,
                owner_token=self.owner_id,
                now_ms=max(claimed_at, int(self._clock())),
                lease_ms=self.claim_lease_ms,
            )

            def renew_claim() -> None:
                self.repository.renew_delivery_claim(
                    message,
                    owner_token=self.owner_id,
                    now_ms=max(claimed_at, int(self._clock())),
                    lease_ms=self.claim_lease_ms,
                )

            try:
                with SyncLifeLeaseHeartbeat(
                    renew_claim,
                    lease_ms=self.claim_lease_ms,
                ):
                    result = self.handler(message)
                    if inspect.isawaitable(result):
                        close = getattr(result, "close", None)
                        if callable(close):
                            close()
                        raise TypeError(
                            "async Life outbox handler requires dispatch_pending_async"
                        )
            except LifeLeaseHeartbeatError:
                # 心跳失效意味着当前 owner 已不能证明租约仍有效；异常必须上抛，
                # 不能把可能由另一 owner 接管的投递标记为本次失败。
                raise
            except Exception as exc:
                failed_at = int(self._clock())
                self.repository.fail_delivery(
                    message,
                    owner_token=self.owner_id,
                    error=f"{type(exc).__name__}: {exc}",
                    retry_at_ms=failed_at + self._retry_delay(message),
                    now_ms=failed_at,
                )
                failed += 1
                continue
            completed_at = int(self._clock())
            receipt = self.repository.complete_delivery(
                message,
                owner_token=self.owner_id,
                result=dict(result or {}),
                now_ms=completed_at,
            )
            # 投递在此已不可变。关系协调失败应向上传播以便观测，并稍后从收据恢复；绝不能
            # 将 outbox 标记失败或再次发送 Gateway。
            self._reconcile_completed_delivery(message, receipt)
            delivered += 1
        return LifeDispatchSummary(
            claimed=claimed,
            delivered=delivered,
            failed=failed,
        )

    async def dispatch_pending_async(
        self,
        *,
        limit: int = 100,
        dispatch_at_ms: int | None = None,
    ) -> LifeDispatchSummary:
        """异步等价投递路径；同步仓储操作移出事件循环，租约心跳仍独立运行。"""
        claimed_at = int(
            dispatch_at_ms if dispatch_at_ms is not None else self._clock()
        )
        max_items = max(1, int(limit))
        await asyncio.to_thread(
            self.reconcile_completed_relationship_deliveries,
            limit=max_items,
        )
        # 仓储操作移出事件循环；租约心跳仍围绕实际 handler 执行，避免慢速同步
        # 存储或网络投递阻塞实时会话任务并导致租约被错误抢占。
        claimed = 0
        delivered = 0
        failed = 0
        for _index in range(max_items):
            messages = await asyncio.to_thread(
                self.repository.claim_deliveries,
                owner_token=self.owner_id,
                now_ms=claimed_at,
                lease_ms=self.claim_lease_ms,
                limit=1,
            )
            if not messages:
                break
            message = messages[0]
            claimed += 1
            if await asyncio.to_thread(
                self.repository.get_delivery_receipt,
                message.outbox_id,
            ) is not None:
                delivered += 1
                continue
            await asyncio.to_thread(
                self.repository.renew_delivery_claim,
                message,
                owner_token=self.owner_id,
                now_ms=max(claimed_at, int(self._clock())),
                lease_ms=self.claim_lease_ms,
            )

            def renew_claim() -> None:
                self.repository.renew_delivery_claim(
                    message,
                    owner_token=self.owner_id,
                    now_ms=max(claimed_at, int(self._clock())),
                    lease_ms=self.claim_lease_ms,
                )

            try:
                async with AsyncLifeLeaseHeartbeat(
                    renew_claim,
                    lease_ms=self.claim_lease_ms,
                ):
                    result = await _invoke_delivery_handler_async(
                        self.handler,
                        message,
                    )
            except LifeLeaseHeartbeatError:
                raise
            except Exception as exc:
                failed_at = int(self._clock())
                await asyncio.to_thread(
                    self.repository.fail_delivery,
                    message,
                    owner_token=self.owner_id,
                    error=f"{type(exc).__name__}: {exc}",
                    retry_at_ms=failed_at + self._retry_delay(message),
                    now_ms=failed_at,
                )
                failed += 1
                continue
            completed_at = int(self._clock())
            receipt = await asyncio.to_thread(
                self.repository.complete_delivery,
                message,
                owner_token=self.owner_id,
                result=dict(result or {}),
                now_ms=completed_at,
            )
            await asyncio.to_thread(
                self._reconcile_completed_delivery,
                message,
                receipt,
            )
            delivered += 1
        return LifeDispatchSummary(
            claimed=claimed,
            delivered=delivered,
            failed=failed,
        )

    def retry_delivery(
        self,
        outbox_id: str,
        *,
        retry_at_ms: int | None = None,
    ) -> bool:
        # 此处只重置尚未完成的 outbox 意图；已产生回执的消息由仓储拒绝重试，
        # 因此人工或调度器重复调用也不能突破 at-least-once 的完成边界。
        return self.repository.retry_delivery(
            outbox_id,
            now_ms=int(retry_at_ms if retry_at_ms is not None else self._clock()),
        )

    def reconcile_completed_relationship_deliveries(
        self,
        *,
        limit: int = 100,
    ) -> int:
        """只从收据恢复关系片段，绝不重放传输。

        兼容调用方可不提供 callback；生产组合一旦注入，方法缺失就是配置错误，不能成为
        丢弃持久化投递语义的理由。
        """

        provider = self._relationship_lifecycle_service_provider
        if provider is None:
            return 0
        service = provider()
        method = getattr(service, "reconcile_completed_proactive_deliveries", None)
        if not callable(method):
            raise RuntimeError(
                "COMPANION_RELATIONSHIP_RECONCILER_NOT_INJECTED"
            )
        result = method(limit=max(1, int(limit)))
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise TypeError(
                "relationship delivery reconciler must be synchronous"
            )
        return max(0, int(result or 0))

    def _reconcile_completed_delivery(
        self,
        message: LifeOutboxMessage,
        receipt: Any,
    ) -> None:
        provider = self._relationship_lifecycle_service_provider
        if provider is None:
            return
        service = provider()
        method = getattr(service, "provision_delivered_proactive", None)
        if not callable(method):
            raise RuntimeError(
                "COMPANION_RELATIONSHIP_RECONCILER_NOT_INJECTED"
            )
        result = method(message=message, delivery_receipt=receipt)
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if callable(close):
                close()
            raise TypeError(
                "relationship delivery reconciler must be synchronous"
            )

    def _retry_delay(self, message: LifeOutboxMessage) -> int:
        # 指数退避设上限，既避免持续故障时的热循环，也防止尝试次数异常导致等待
        # 时间无界增长而让可恢复消息永久搁置。
        return min(
            self.max_retry_ms,
            self.base_retry_ms
            * (2 ** min(20, max(0, message.attempt_count - 1))),
        )


async def _invoke_delivery_handler_async(
    handler: Callable[
        [LifeOutboxMessage],
        Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None,
    ],
    message: LifeOutboxMessage,
) -> Mapping[str, Any] | None:
    """在不阻塞实时聊天事件循环的前提下运行同步传输适配器。"""

    if inspect.iscoroutinefunction(handler):
        result = handler(message)
    else:
        result = await asyncio.to_thread(handler, message)
    if inspect.isawaitable(result):
        result = await result
    return result


__all__ = ["LifeOutboxDispatcher"]
