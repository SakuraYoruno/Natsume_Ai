"""Lease/fencing dispatcher for immutable Digital Life projection intents."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import inspect
from typing import Any

from host_brain.core.ids import now_ms
from natsume_contracts.life import (
    LifeDispatchSummary,
    LifeProjectionMessage,
    LifeProjectionReceipt,
)
from host_brain.digital_life.lease_heartbeat import (
    AsyncLifeLeaseHeartbeat,
    LifeLeaseHeartbeatError,
    SyncLifeLeaseHeartbeat,
)
from host_brain.digital_life.repository_port import LifeCommandRepositoryPort


ProjectionHandler = Callable[
    [LifeProjectionMessage],
    Mapping[str, Any] | Awaitable[Mapping[str, Any] | None] | None,
]


class LifeProjectionDispatcher:
    def __init__(
        self,
        repository: LifeCommandRepositoryPort,
        handler: ProjectionHandler,
        *,
        owner_id: str = "life-projection-dispatcher",
        clock: Callable[[], int] = now_ms,
        claim_lease_ms: int = 60_000,
        base_retry_ms: int = 1_000,
        max_retry_ms: int = 300_000,
    ) -> None:
        self.repository = repository
        self.handler = handler
        self.owner_id = str(owner_id or "life-projection-dispatcher")
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
        max_items = max(1, int(limit))
        claimed = 0
        delivered = 0
        failed = 0
        for _index in range(max_items):
            messages = self._claim(limit=1, dispatch_at_ms=dispatch_at_ms)
            if not messages:
                break
            message = messages[0]
            claimed += 1
            renew_at = max(
                int(self._clock()),
                int(dispatch_at_ms or 0),
            )
            self.repository.renew_projection_claim(
                message,
                owner_token=self.owner_id,
                now_ms=renew_at,
                lease_ms=self.claim_lease_ms,
            )

            def renew_claim() -> None:
                self.repository.renew_projection_claim(
                    message,
                    owner_token=self.owner_id,
                    now_ms=max(int(self._clock()), int(dispatch_at_ms or 0)),
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
                            "async Life projection handler requires "
                            "dispatch_pending_async"
                        )
            except LifeLeaseHeartbeatError:
                raise
            except Exception as exc:
                self._fail(message, exc)
                failed += 1
                continue
            self.repository.complete_projection(
                message,
                owner_token=self.owner_id,
                result=dict(result or {}),
                now_ms=int(self._clock()),
            )
            delivered += 1
        return LifeDispatchSummary(claimed, delivered, failed)

    async def dispatch_pending_async(
        self,
        *,
        limit: int = 100,
        dispatch_at_ms: int | None = None,
    ) -> LifeDispatchSummary:
        max_items = max(1, int(limit))
        claimed = 0
        delivered = 0
        failed = 0
        for _index in range(max_items):
            messages = self._claim(limit=1, dispatch_at_ms=dispatch_at_ms)
            if not messages:
                break
            message = messages[0]
            claimed += 1
            renew_at = max(
                int(self._clock()),
                int(dispatch_at_ms or 0),
            )
            self.repository.renew_projection_claim(
                message,
                owner_token=self.owner_id,
                now_ms=renew_at,
                lease_ms=self.claim_lease_ms,
            )

            def renew_claim() -> None:
                self.repository.renew_projection_claim(
                    message,
                    owner_token=self.owner_id,
                    now_ms=max(int(self._clock()), int(dispatch_at_ms or 0)),
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
                self._fail(message, exc)
                failed += 1
                continue
            self.repository.complete_projection(
                message,
                owner_token=self.owner_id,
                result=dict(result or {}),
                now_ms=int(self._clock()),
            )
            delivered += 1
        return LifeDispatchSummary(claimed, delivered, failed)

    async def dispatch_intent_async(
        self,
        intent_id: str,
        *,
        dispatch_at_ms: int | None = None,
    ) -> LifeProjectionReceipt | None:
        """Dispatch one exact intent without draining unrelated queue items."""

        exact_intent = str(intent_id or "").strip()
        if not exact_intent:
            raise ValueError("intent_id is required")
        existing = self.repository.get_projection_receipt(exact_intent)
        if existing is not None:
            return existing
        messages = self._claim(
            limit=1,
            dispatch_at_ms=dispatch_at_ms,
            intent_id=exact_intent,
        )
        if not messages:
            return self.repository.get_projection_receipt(exact_intent)
        message = messages[0]
        renew_at = max(
            int(self._clock()),
            int(dispatch_at_ms or 0),
        )
        self.repository.renew_projection_claim(
            message,
            owner_token=self.owner_id,
            now_ms=renew_at,
            lease_ms=self.claim_lease_ms,
        )

        def renew_claim() -> None:
            self.repository.renew_projection_claim(
                message,
                owner_token=self.owner_id,
                now_ms=max(int(self._clock()), int(dispatch_at_ms or 0)),
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
            self._fail(message, exc)
            raise
        return self.repository.complete_projection(
            message,
            owner_token=self.owner_id,
            result=dict(result or {}),
            now_ms=int(self._clock()),
        )

    def retry_projection(
        self,
        intent_id: str,
        *,
        retry_at_ms: int | None = None,
    ) -> bool:
        return self.repository.retry_projection(
            intent_id,
            now_ms=int(retry_at_ms if retry_at_ms is not None else self._clock()),
        )

    def _claim(
        self,
        *,
        limit: int,
        dispatch_at_ms: int | None,
        intent_id: str | None = None,
    ) -> list[LifeProjectionMessage]:
        claimed_at = int(
            dispatch_at_ms if dispatch_at_ms is not None else self._clock()
        )
        return self.repository.claim_projection_intents(
            owner_token=self.owner_id,
            now_ms=claimed_at,
            lease_ms=self.claim_lease_ms,
            limit=max(1, int(limit)),
            intent_id=intent_id,
        )

    def _fail(self, message: LifeProjectionMessage, exc: Exception) -> None:
        failed_at = int(self._clock())
        retry_delay = min(
            self.max_retry_ms,
            self.base_retry_ms
            * (2 ** min(20, max(0, message.attempt_count - 1))),
        )
        self.repository.fail_projection(
            message,
            owner_token=self.owner_id,
            error=f"{type(exc).__name__}: {exc}",
            retry_at_ms=failed_at + retry_delay,
            now_ms=failed_at,
        )


__all__ = ["LifeProjectionDispatcher", "ProjectionHandler"]
