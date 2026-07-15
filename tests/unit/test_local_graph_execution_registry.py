from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

from host_brain.task_runtime import local_graph_execution_registry
from host_brain.task_runtime.local_graph_execution_registry import (
    LocalGraphExecutionRegistry,
    _bounded_timeout_seconds,
)


async def _wait_forever_under_track(
    registry: LocalGraphExecutionRegistry,
    *,
    graph_id: str,
    graph_revision: int,
    started: asyncio.Event,
) -> None:
    async with registry.track(
        graph_id=graph_id,
        graph_revision=graph_revision,
    ):
        started.set()
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_track_unregisters_in_finally_after_external_cancellation() -> None:
    registry = LocalGraphExecutionRegistry()
    started = asyncio.Event()
    task = asyncio.create_task(
        _wait_forever_under_track(
            registry,
            graph_id="graph-one",
            graph_revision=1,
            started=started,
        ),
        name="graph-one-worker",
    )
    await started.wait()

    pending = await registry.cancel_and_join(
        graph_id="graph-one",
        graph_revision=1,
        intent_ref="cancellation-intent://graph-one/1",
        timeout_seconds=1.0,
    )

    assert pending == ()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry._tasks == {}  # noqa: SLF001
    assert await registry.cancel_and_join(
        graph_id="graph-one",
        graph_revision=1,
        intent_ref="cancellation-intent://graph-one/2",
        timeout_seconds=1.0,
    ) == ()


@pytest.mark.asyncio
async def test_graph_id_and_revision_are_isolated() -> None:
    registry = LocalGraphExecutionRegistry()
    revision_one_started = asyncio.Event()
    revision_two_started = asyncio.Event()
    revision_one = asyncio.create_task(
        _wait_forever_under_track(
            registry,
            graph_id="graph-shared",
            graph_revision=1,
            started=revision_one_started,
        ),
        name="revision-one",
    )
    revision_two = asyncio.create_task(
        _wait_forever_under_track(
            registry,
            graph_id="graph-shared",
            graph_revision=2,
            started=revision_two_started,
        ),
        name="revision-two",
    )
    await asyncio.gather(
        revision_one_started.wait(),
        revision_two_started.wait(),
    )

    assert await registry.cancel_and_join(
        graph_id="graph-shared",
        graph_revision=1,
        intent_ref="cancellation-intent://graph-shared/1",
        timeout_seconds=1.0,
    ) == ()
    assert revision_one.cancelled()
    assert revision_two.done() is False

    assert await registry.cancel_and_join(
        graph_id="graph-shared",
        graph_revision=2,
        intent_ref="cancellation-intent://graph-shared/2",
        timeout_seconds=1.0,
    ) == ()
    assert revision_two.cancelled()


@pytest.mark.asyncio
async def test_cancel_excludes_current_task_and_nested_track_is_stable() -> None:
    registry = LocalGraphExecutionRegistry()
    current = asyncio.current_task()
    assert current is not None

    async with registry.track(graph_id="graph-current", graph_revision=1):
        async with registry.track(graph_id="graph-current", graph_revision=1):
            assert await registry.cancel_and_join(
                graph_id="graph-current",
                graph_revision=1,
                intent_ref="cancellation-intent://graph-current/1",
                timeout_seconds=1.0,
            ) == ()
            assert current.cancelling() == 0
        assert await registry.cancel_and_join(
            graph_id="graph-current",
            graph_revision=1,
            intent_ref="cancellation-intent://graph-current/2",
            timeout_seconds=1.0,
        ) == ()


@pytest.mark.asyncio
async def test_completed_task_is_not_cancelled_or_returned_as_pending() -> None:
    registry = LocalGraphExecutionRegistry()

    async def complete() -> None:
        async with registry.track(graph_id="graph-done", graph_revision=1):
            return

    task = asyncio.create_task(complete(), name="already-done")
    await task
    assert task.done() is True
    assert task.cancelled() is False
    assert registry._tasks == {}  # noqa: SLF001

    assert await registry.cancel_and_join(
        graph_id="graph-done",
        graph_revision=1,
        intent_ref="cancellation-intent://graph-done/1",
        timeout_seconds=1.0,
    ) == ()
    assert task.cancelled() is False


@pytest.mark.asyncio
async def test_track_unregisters_after_exception_and_async_generator_aclose() -> None:
    registry = LocalGraphExecutionRegistry()

    with pytest.raises(RuntimeError, match="tracked failure"):
        async with registry.track(graph_id="graph-error", graph_revision=1):
            raise RuntimeError("tracked failure")
    assert registry._tasks == {}  # noqa: SLF001

    async def stream():
        async with registry.track(graph_id="graph-stream", graph_revision=1):
            yield "started"
            await asyncio.Event().wait()

    events = stream()
    assert await anext(events) == "started"
    assert registry._tasks  # noqa: SLF001
    await events.aclose()
    assert registry._tasks == {}  # noqa: SLF001


@pytest.mark.asyncio
async def test_bounded_wait_returns_only_still_pending_task_names() -> None:
    registry = LocalGraphExecutionRegistry()
    started = asyncio.Event()
    release = asyncio.Event()

    async def resist_one_cancellation() -> None:
        async with registry.track(graph_id="graph-pending", graph_revision=1):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

    task = asyncio.create_task(
        resist_one_cancellation(),
        name="bounded-pending-worker",
    )
    await started.wait()

    pending = await registry.cancel_and_join(
        graph_id="graph-pending",
        graph_revision=1,
        intent_ref="cancellation-intent://graph-pending/1",
        timeout_seconds=0.001,
    )
    assert pending == ("bounded-pending-worker",)
    assert task.done() is False

    release.set()
    await task
    assert await registry.cancel_and_join(
        graph_id="graph-pending",
        graph_revision=1,
        intent_ref="cancellation-intent://graph-pending/2",
        timeout_seconds=1.0,
    ) == ()


def test_timeout_is_finite_positive_and_bounded() -> None:
    assert _bounded_timeout_seconds(0.001) == 0.1
    assert _bounded_timeout_seconds(2.5) == 2.5
    assert _bounded_timeout_seconds(999.0) == 5.0
    for invalid in (True, 0, -1, float("nan"), float("inf"), "invalid"):
        with pytest.raises(ValueError, match="positive finite"):
            _bounded_timeout_seconds(invalid)


@pytest.mark.asyncio
async def test_graph_identity_and_intent_are_fail_closed() -> None:
    registry = LocalGraphExecutionRegistry()
    with pytest.raises(ValueError, match="graph_id"):
        async with registry.track(graph_id="", graph_revision=1):
            pass
    with pytest.raises(ValueError, match="graph_revision"):
        async with registry.track(graph_id="graph", graph_revision=True):
            pass
    with pytest.raises(ValueError, match="intent_ref"):
        await registry.cancel_and_join(
            graph_id="graph",
            graph_revision=1,
            intent_ref="",
            timeout_seconds=1.0,
        )


def test_registry_has_no_runtime_authority_or_callback_dependency() -> None:
    module = local_graph_execution_registry
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        imported is not None
        and imported.startswith("host_brain")
        for imported in imported_modules
    )
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "Callable" not in imported_names
    assert LocalGraphExecutionRegistry.__slots__ == ("_lock", "_tasks")
    assert tuple(inspect.signature(LocalGraphExecutionRegistry).parameters) == ()

    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "LocalGraphExecutionRegistry"
    )
    methods = {
        node.name: node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods.keys() == {"__init__", "track", "cancel_and_join"}
    assert any(
        isinstance(decorator, ast.Name)
        and decorator.id == "asynccontextmanager"
        for decorator in methods["track"].decorator_list
    )

    cancel = methods["cancel_and_join"]
    lock_blocks = [
        node
        for node in ast.walk(cancel)
        if isinstance(node, ast.AsyncWith)
        and any(
            isinstance(item.context_expr, ast.Attribute)
            and isinstance(item.context_expr.value, ast.Name)
            and item.context_expr.value.id == "self"
            and item.context_expr.attr == "_lock"
            for item in node.items
        )
    ]
    assert len(lock_blocks) == 1
    locked_nodes = {id(node) for node in ast.walk(lock_blocks[0])}
    for call_name in ("cancel", "wait", "gather"):
        calls = [
            node
            for node in ast.walk(cancel)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == call_name
        ]
        assert calls
        assert all(id(call) not in locked_nodes for call in calls)

    cancel_source = ast.unparse(cancel)
    assert "task is not current" in cancel_source
    assert "not task.done()" in cancel_source
