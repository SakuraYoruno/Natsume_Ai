from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.task_repository import (
    AcceptanceEffectStatus,
    DurableTaskStore,
    TASK_STORE_LEASE_CONFLICT,
    TaskStoreError,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    ACCEPTANCE_EFFECT_HANDLER_FAILED,
    ACCEPTANCE_EFFECT_HANDLER_MISSING,
    AcceptanceEffectDispatcher,
)


@dataclass
class MutableClock:
    value: int = 20_000

    def __call__(self) -> int:
        return self.value

    def advance(self, delta_ms: int) -> None:
        self.value += int(delta_ms)


def _store(tmp_path: Path, clock: MutableClock) -> DurableTaskStore:
    db_path = tmp_path / "gr06-dispatcher.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr06-acceptance-effect-dispatcher"
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(db_path, clock=clock)
    store.assert_acceptance_effect_intent_ready()
    return store


def _seed_completed_graph(store: DurableTaskStore, graph_id: str) -> tuple[str, str]:
    node_id = f"node-{graph_id}"
    attempt_id = f"attempt-{graph_id}"
    now = store._now()  # test fixture uses the same injected durable clock
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref, status,
                status_version, graph_json, source_kind, created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, ?, ?, 'COMPLETED', 2, '{}', 'CANONICAL', ?, ?)
            """,
            (
                graph_id,
                f"work-order::{graph_id}",
                f"planner://dispatcher/{graph_id}",
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, 1, ?, 'ACCEPTED', 7, '{}', ?, ?)
            """,
            (graph_id, node_id, now, now),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, created_ts_ms,
                completed_ts_ms, updated_ts_ms
            ) VALUES (?, ?, 1, ?, 1, 'SUCCEEDED', 3, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                graph_id,
                node_id,
                "worker-dispatcher-test",
                now,
                now,
                now,
            ),
        )
        conn.commit()
    return node_id, attempt_id


def _stage_effect(
    store: DurableTaskStore,
    *,
    graph_id: str,
    node_id: str,
    attempt_id: str,
    effect_type: str,
    opaque_order: str,
) -> dict[str, Any]:
    payload = {"effect_type": effect_type, "graph_id": graph_id}
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return store.stage_acceptance_effect_intent(
        effect_id=f"acceptance-effect://dispatcher/{graph_id}/{opaque_order}",
        graph_id=graph_id,
        graph_revision=1,
        node_id=node_id,
        attempt_id=attempt_id,
        commit_scope="GRAPH",
        effect_type=effect_type,
        payload_ref=f"effect-payload://dispatcher/{graph_id}/{opaque_order}",
        payload=payload,
        payload_sha256="sha256:"
        + hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        idempotency_key=f"dispatcher:{graph_id}:{effect_type}:{opaque_order}",
    )


def _stage_semantic_chain(
    store: DurableTaskStore,
    graph_id: str,
    *,
    include_unknown: bool = False,
) -> dict[str, dict[str, Any]]:
    node_id, attempt_id = _seed_completed_graph(store, graph_id)
    specs = [
        ("surface.reply_delivery", "a-delivery"),
        *([("unknown.audit", "b-unknown")] if include_unknown else []),
        ("life.foreground_completed", "m-life"),
        ("conversation.user_turn", "z-conversation"),
    ]
    return {
        effect_type: _stage_effect(
            store,
            graph_id=graph_id,
            node_id=node_id,
            attempt_id=attempt_id,
            effect_type=effect_type,
            opaque_order=opaque_order,
        )
        for effect_type, opaque_order in specs
    }


def _handlers(calls: list[tuple[str, str]]) -> dict[str, Any]:
    def build(effect_type: str):
        def handle(payload: dict[str, Any]) -> str:
            graph_id = str(payload["graph_id"])
            calls.append((graph_id, effect_type))
            return f"effect-receipt://dispatcher/{graph_id}/{effect_type}"

        return handle

    return {
        effect_type: build(effect_type)
        for effect_type in (
            "conversation.user_turn",
            "life.foreground_completed",
            "surface.reply_delivery",
        )
    }


@pytest.mark.asyncio
async def test_dispatch_graph_uses_semantic_order_and_exact_replay(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    effects = _stage_semantic_chain(store, "graph-dispatch-order", include_unknown=True)
    calls: list[tuple[str, str]] = []
    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers=_handlers(calls),
        owner_id="dispatcher-semantic-order",
    )

    results = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-order",
        graph_revision=1,
    )

    assert [row[1] for row in calls] == [
        "conversation.user_turn",
        "life.foreground_completed",
        "surface.reply_delivery",
    ]
    assert [row["effect_type"] for row in results] == [
        "conversation.user_turn",
        "life.foreground_completed",
        "surface.reply_delivery",
        "unknown.audit",
    ]
    assert [row["status"] for row in results] == [
        AcceptanceEffectStatus.COMMITTED.value,
        AcceptanceEffectStatus.COMMITTED.value,
        AcceptanceEffectStatus.COMMITTED.value,
        AcceptanceEffectStatus.FAILED.value,
    ]
    assert results[-1]["reason_code"] == ACCEPTANCE_EFFECT_HANDLER_MISSING

    replay = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-order",
        graph_revision=1,
    )
    assert len(calls) == 3
    assert [row["status"] for row in replay] == [
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
        "FAILED",
    ]
    assert await dispatcher.dispatch_effect(
        str(effects["conversation.user_turn"]["effect_id"])
    ) is None
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_handler_exception_fails_closed_before_life_and_delivery(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    effects = _stage_semantic_chain(store, "graph-dispatch-failure")
    calls: list[str] = []

    def fail_conversation(_payload: dict[str, Any]) -> str:
        calls.append("conversation.user_turn")
        raise RuntimeError("conversation persistence unavailable")

    def forbidden_later(_payload: dict[str, Any]) -> str:
        calls.append("later-effect")
        return "later-effect://must-not-run"

    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={
            "conversation.user_turn": fail_conversation,
            "life.foreground_completed": forbidden_later,
            "surface.reply_delivery": forbidden_later,
        },
        owner_id="dispatcher-fail-closed",
        max_attempts=1,
    )

    results = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-failure",
        graph_revision=1,
    )

    assert calls == ["conversation.user_turn"]
    assert len(results) == 1
    assert results[0]["status"] == AcceptanceEffectStatus.FAILED.value
    assert results[0]["reason_code"] == ACCEPTANCE_EFFECT_HANDLER_FAILED
    assert "conversation persistence unavailable" in results[0]["last_error"]
    assert store.get_acceptance_effect_intent(
        str(effects["life.foreground_completed"]["effect_id"])
    )["status"] == AcceptanceEffectStatus.PENDING.value
    assert store.get_acceptance_effect_intent(
        str(effects["surface.reply_delivery"]["effect_id"])
    )["status"] == AcceptanceEffectStatus.PENDING.value


@pytest.mark.asyncio
async def test_transient_handler_failure_is_rescheduled_then_commits_chain(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    effects = _stage_semantic_chain(store, "graph-dispatch-retry")
    calls: list[str] = []

    def flaky_conversation(_payload: dict[str, Any]) -> str:
        calls.append("conversation.user_turn")
        if calls.count("conversation.user_turn") == 1:
            raise RuntimeError("conversation store temporarily unavailable")
        return "conversation-receipt://dispatcher/recovered"

    def committed_later(payload: dict[str, Any]) -> str:
        effect_type = str(payload["effect_type"])
        calls.append(effect_type)
        return f"effect-receipt://dispatcher/retry/{effect_type}"

    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={
            "conversation.user_turn": flaky_conversation,
            "life.foreground_completed": committed_later,
            "surface.reply_delivery": committed_later,
        },
        owner_id="dispatcher-retry",
        max_attempts=3,
        retry_base_ms=10,
        retry_max_ms=10,
    )

    first = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-retry",
        graph_revision=1,
    )
    assert [row["status"] for row in first] == ["PENDING"]
    assert first[0]["attempt_count"] == 1
    assert first[0]["reason_code"] == ACCEPTANCE_EFFECT_HANDLER_FAILED
    assert calls == ["conversation.user_turn"]

    clock.advance(10)
    recovered = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-retry",
        graph_revision=1,
    )
    assert [row["status"] for row in recovered] == [
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
    ]
    assert calls == [
        "conversation.user_turn",
        "conversation.user_turn",
        "life.foreground_completed",
        "surface.reply_delivery",
    ]
    conversation = store.get_acceptance_effect_intent(
        str(effects["conversation.user_turn"]["effect_id"])
    )
    assert conversation["attempt_count"] == 2
    assert conversation["fencing_token"] == 2


@pytest.mark.asyncio
async def test_expired_lease_cannot_commit_and_recovery_uses_new_fence(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    node_id, attempt_id = _seed_completed_graph(store, "graph-dispatch-fence")
    effect = _stage_effect(
        store,
        graph_id="graph-dispatch-fence",
        node_id=node_id,
        attempt_id=attempt_id,
        effect_type="conversation.user_turn",
        opaque_order="only",
    )
    calls: list[str] = []

    def expire_lease(_payload: dict[str, Any]) -> str:
        calls.append("stale-owner")
        clock.advance(11)
        return "conversation-receipt://dispatcher/stale-owner"

    stale = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={"conversation.user_turn": expire_lease},
        owner_id="dispatcher-stale",
        lease_ms=10,
    )
    with pytest.raises(TaskStoreError) as lost_fence:
        await stale.dispatch_effect(str(effect["effect_id"]))
    assert lost_fence.value.reason_code == TASK_STORE_LEASE_CONFLICT
    claimed = store.get_acceptance_effect_intent(str(effect["effect_id"]))
    assert claimed["status"] == AcceptanceEffectStatus.CLAIMED.value
    assert claimed["fencing_token"] == 1
    assert claimed["reason_code"] is None

    def recover(_payload: dict[str, Any]) -> str:
        calls.append("recovery-owner")
        return "conversation-receipt://dispatcher/recovered"

    restarted = AcceptanceEffectDispatcher(
        task_store=DurableTaskStore(store.db_path, clock=clock),
        handlers={"conversation.user_turn": recover},
        owner_id="dispatcher-restarted",
        lease_ms=100,
    )
    committed = await restarted.dispatch_effect(str(effect["effect_id"]))
    assert committed is not None
    assert committed["status"] == AcceptanceEffectStatus.COMMITTED.value
    assert committed["fencing_token"] == 2
    assert committed["attempt_count"] == 2
    assert calls == ["stale-owner", "recovery-owner"]

    assert await restarted.dispatch_effect(str(effect["effect_id"])) is None
    assert calls == ["stale-owner", "recovery-owner"]


@pytest.mark.asyncio
async def test_drain_ready_preserves_each_graph_chain_and_limit(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    _stage_semantic_chain(store, "graph-drain-a")
    clock.advance(1)
    _stage_semantic_chain(store, "graph-drain-b")
    calls: list[tuple[str, str]] = []
    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers=_handlers(calls),
        owner_id="dispatcher-drain",
    )

    first = await dispatcher.drain_ready(limit=4)
    assert len(first) == 4
    assert calls == [
        ("graph-drain-a", "conversation.user_turn"),
        ("graph-drain-a", "life.foreground_completed"),
        ("graph-drain-a", "surface.reply_delivery"),
        ("graph-drain-b", "conversation.user_turn"),
    ]

    second = await dispatcher.drain_ready(limit=4)
    assert len(second) == 2
    assert calls[-2:] == [
        ("graph-drain-b", "life.foreground_completed"),
        ("graph-drain-b", "surface.reply_delivery"),
    ]
    assert await dispatcher.drain_ready(limit=4) == ()
    assert len(calls) == 6
