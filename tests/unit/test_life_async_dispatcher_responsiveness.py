from __future__ import annotations

import asyncio
import threading

import pytest

from host_brain.digital_life.outbox import LifeOutboxDispatcher
from host_brain.digital_life.projection import LifeProjectionDispatcher


class _BlockingProjectionRepository:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def claim_projection_intents(self, **_kwargs):  # noqa: ANN003, ANN202
        self.entered.set()
        if not self.release.wait(timeout=1.0):
            raise AssertionError("projection repository blocked the event loop")
        return []


class _BlockingOutboxRepository:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def claim_deliveries(self, **_kwargs):  # noqa: ANN003, ANN202
        self.entered.set()
        if not self.release.wait(timeout=1.0):
            raise AssertionError("outbox repository blocked the event loop")
        return []


@pytest.mark.asyncio
async def test_async_projection_claim_does_not_block_realtime_event_loop() -> None:
    repository = _BlockingProjectionRepository()
    dispatcher = LifeProjectionDispatcher(repository, lambda _message: {})
    task = asyncio.create_task(dispatcher.dispatch_pending_async(limit=1))
    try:
        assert await asyncio.to_thread(repository.entered.wait, 0.5)
        await asyncio.sleep(0.01)
        repository.release.set()
        result = await task
    finally:
        repository.release.set()
    assert result.claimed == 0


@pytest.mark.asyncio
async def test_async_outbox_claim_does_not_block_realtime_event_loop() -> None:
    repository = _BlockingOutboxRepository()
    dispatcher = LifeOutboxDispatcher(repository, lambda _message: {})
    task = asyncio.create_task(dispatcher.dispatch_pending_async(limit=1))
    try:
        assert await asyncio.to_thread(repository.entered.wait, 0.5)
        await asyncio.sleep(0.01)
        repository.release.set()
        result = await task
    finally:
        repository.release.set()
    assert result.claimed == 0
