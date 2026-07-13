"""Lease/fencing dispatcher for Digital Life delivery intents."""

from __future__ import annotations

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
    ) -> None:
        self.repository = repository
        self.handler = handler
        self.owner_id = str(owner_id or "life-outbox-dispatcher")
        self._clock = clock
        self.claim_lease_ms = max(1, int(claim_lease_ms))
        self.base_retry_ms = max(1, int(base_retry_ms))
        self.max_retry_ms = max(self.base_retry_ms, int(max_retry_ms))

    def dispatch_pending(
        self,
        *,
        limit: int = 100,
        dispatch_at_ms: int | None = None,
    ) -> LifeDispatchSummary:
        claimed_at = int(
            dispatch_at_ms if dispatch_at_ms is not None else self._clock()
        )
        max_items = max(1, int(limit))
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
            self.repository.complete_delivery(
                message,
                owner_token=self.owner_id,
                result=dict(result or {}),
                now_ms=completed_at,
            )
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
        claimed_at = int(
            dispatch_at_ms if dispatch_at_ms is not None else self._clock()
        )
        max_items = max(1, int(limit))
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
                async with AsyncLifeLeaseHeartbeat(
                    renew_claim,
                    lease_ms=self.claim_lease_ms,
                ):
                    result = self.handler(message)
                    if inspect.isawaitable(result):
                        result = await result
            except LifeLeaseHeartbeatError:
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
            self.repository.complete_delivery(
                message,
                owner_token=self.owner_id,
                result=dict(result or {}),
                now_ms=completed_at,
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
        return self.repository.retry_delivery(
            outbox_id,
            now_ms=int(retry_at_ms if retry_at_ms is not None else self._clock()),
        )

    def _retry_delay(self, message: LifeOutboxMessage) -> int:
        return min(
            self.max_retry_ms,
            self.base_retry_ms
            * (2 ** min(20, max(0, message.attempt_count - 1))),
        )


__all__ = ["LifeOutboxDispatcher"]
