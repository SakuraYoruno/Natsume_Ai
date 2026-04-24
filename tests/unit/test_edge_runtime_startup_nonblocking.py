from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from edge_client.runtime.bootstrap import shutdown, startup


class _SlowComponent:
    def __init__(self, delay_s: float = 0.25) -> None:
        self.delay_s = delay_s
        self.started = threading.Event()
        self.stopped = False

    def startup(self) -> None:
        time.sleep(self.delay_s)
        self.started.set()

    def shutdown(self) -> None:
        self.stopped = True


def test_edge_runtime_startup_schedules_heavy_components_in_background() -> None:
    runtime = SimpleNamespace(
        tts=_SlowComponent(),
        vts=_SlowComponent(),
        asr=_SlowComponent(),
        resource_gate=None,
        rpc_client=None,
        startup_threads=[],
        close=lambda: {},
    )

    started = time.perf_counter()
    asyncio.run(startup(runtime))
    elapsed = time.perf_counter() - started

    assert elapsed < 0.15
    assert len(runtime.startup_threads) == 3
    assert any(thread.is_alive() for thread in runtime.startup_threads)

    assert runtime.tts.started.wait(1.0)
    assert runtime.vts.started.wait(1.0)
    assert runtime.asr.started.wait(1.0)

    asyncio.run(shutdown(runtime))
    assert runtime.tts.stopped is True
    assert runtime.vts.stopped is True
    assert runtime.asr.stopped is True
