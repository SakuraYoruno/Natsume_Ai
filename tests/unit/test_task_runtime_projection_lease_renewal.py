from __future__ import annotations

from pathlib import Path

from host_brain.app.observability_runtime.projection_runtime import (
    TASK_EXECUTION_KIND,
    refresh_task_projections,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.observability.projection_worker import QueryProjectionWorker


class _SlowTaskSource:
    def __init__(self, clock: dict[str, int], *, execution_count: int) -> None:
        self._clock = clock
        self._executions = [
            {
                "graph_id": f"graph-{index:03d}",
                "revision": 1,
                "updated_ts_ms": index + 1,
            }
            for index in range(execution_count)
        ]

    def list_executions(self, *, limit: int) -> dict[str, object]:
        return {
            "schema_version": "Natsume.operator.execution_index.v1",
            "executions": self._executions[:limit],
        }

    def get_execution(
        self,
        graph_id: str,
        revision: int,
        *,
        after_cursor: int,
        event_limit: int,
    ) -> dict[str, object]:
        assert revision == 1
        assert after_cursor == 0
        assert event_limit == 1000
        self._clock["now"] += 1_000
        index = int(graph_id.rsplit("-", 1)[-1])
        return {
            "schema_version": "Natsume.operator.execution_review.v1",
            "execution": {
                "graph_id": graph_id,
                "revision": revision,
                "updated_ts_ms": index + 1,
            },
            "reviews": [],
            "timeline": [],
        }


def test_task_projection_renews_fence_during_long_materialization(tmp_path: Path) -> None:
    database = tmp_path / "task-projection-lease.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="task-projection-lease-test",
    )
    assert migrated.success is True

    clock = {"now": 1_000}
    store = QueryProjectionStore(database, clock=lambda: clock["now"])
    lease = store.acquire_leader_lease("task-projection-test", lease_ms=30_000)
    assert lease is not None

    with store.bind_leader_lease(lease):
        cooperative_yields: list[float] = []
        result = refresh_task_projections(
            _SlowTaskSource(clock, execution_count=80),  # type: ignore[arg-type]
            QueryProjectionWorker(store),
            cooperative_write_yield=cooperative_yields.append,
        )

    assert result == {
        "index_count": 1,
        "indexed_execution_count": 80,
        "execution_count": 16,
        "deferred_execution_count": 64,
        "review_count": 0,
    }
    assert clock["now"] == 17_000
    assert len(store.list(TASK_EXECUTION_KIND, limit=100)) == 17
    assert cooperative_yields == [0.005] * 17
