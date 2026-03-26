from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, patch

from host_brain.app import input_routes


def _run(coro):
    return asyncio.run(coro)


def test_run_main_chain_dispatch_thread_mode_uses_to_thread() -> None:
    main_chain_mock = AsyncMock(return_value=None)
    to_thread_called = {"count": 0}

    async def _fake_to_thread(func, /, *args, **kwargs):
        to_thread_called["count"] += 1
        errors: list[BaseException] = []

        def _target() -> None:
            try:
                func(*args, **kwargs)
            except BaseException as exc:  # pragma: no cover - defensive propagation
                errors.append(exc)

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(timeout=5)
        if worker.is_alive():
            raise TimeoutError("thread dispatch did not finish")
        if errors:
            raise errors[0]

    with patch("host_brain.app.graph.main_chain.run_main_chain_from_input", new=main_chain_mock):
        with patch("asyncio.to_thread", new=_fake_to_thread):
            _run(
                input_routes._run_main_chain_dispatch(
                    request_id="req_test_thread",
                    session_id="sess_test_thread",
                    source="GATEWAY",
                    text="hello",
                    payload={"source": "GATEWAY", "content": "hello"},
                    ts_ms=1,
                    run_in_thread=True,
                )
            )

    assert to_thread_called["count"] == 1
    assert main_chain_mock.await_count == 1


def test_run_main_chain_dispatch_inline_mode_skips_to_thread() -> None:
    main_chain_mock = AsyncMock(return_value=None)

    async def _fail_to_thread(*_args, **_kwargs):
        raise AssertionError("asyncio.to_thread should not be used in inline mode")

    with patch("host_brain.app.graph.main_chain.run_main_chain_from_input", new=main_chain_mock):
        with patch("asyncio.to_thread", new=_fail_to_thread):
            _run(
                input_routes._run_main_chain_dispatch(
                    request_id="req_test_inline",
                    session_id="sess_test_inline",
                    source="ASR",
                    text="ping",
                    payload={"source": "ASR", "text": "ping"},
                    ts_ms=1,
                    run_in_thread=False,
                )
            )

    assert main_chain_mock.await_count == 1
