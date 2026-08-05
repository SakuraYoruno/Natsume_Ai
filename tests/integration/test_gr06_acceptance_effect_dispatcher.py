from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.node_artifact import (
    NODE_ARTIFACT_SCHEMA_VERSION,
    artifact_digest,
    build_task_result_artifact_ref,
    canonical_artifact_json,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.persistence.task_repository import (
    AcceptanceEffectStatus,
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_LEASE_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    ACCEPTANCE_EFFECT_HANDLER_FAILED,
    ACCEPTANCE_EFFECT_HANDLER_MISSING,
    ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED,
    AcceptanceEffectDispatcher,
    AcceptanceEffectOutcomeUnknown,
    AcceptanceEffectRuntime,
)
from tests.task_runtime_authority_fixtures import (
    seed_raw_task_graph_for_test,
    seed_raw_acceptance_effect_for_test,
)


@dataclass
class MutableClock:
    value: int = 20_000

    def __call__(self) -> int:
        return self.value

    def advance(self, delta_ms: int) -> None:
        self.value += int(delta_ms)


class _TestActionPreviewAuthority:
    """Narrow preview authority double; dispatcher semantics are under test."""

    def issue_receipt(self, **kwargs: Any) -> dict[str, Any]:
        effect_id = str(dict(kwargs.get("authority_context") or {}).get("effect_id") or "")
        return {"event_id": f"action-preview://dispatcher/{effect_id.rsplit('/', 1)[-1]}"}

    def require_execution(self, **_kwargs: Any) -> None:
        return None


def _typed_test_action_preview_authority() -> ActionPreviewService:
    service = ActionPreviewService(
        runtime_event_store=object(),
        guardian=SimpleNamespace(evaluate_capability=lambda **_kwargs: {}),
        authority_resolver=SimpleNamespace(resolve=lambda **_kwargs: None),
    )
    delegate = _TestActionPreviewAuthority()
    service.issue_receipt = delegate.issue_receipt  # type: ignore[method-assign]
    service.require_execution = delegate.require_execution  # type: ignore[method-assign]
    return service


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
    lease_id = f"lease-{graph_id}"
    receipt_ref = f"worker-projection://dispatcher/{graph_id}/receipt"
    operation_scope = f"dispatcher:{graph_id}"
    operation_fingerprint = "sha256:" + hashlib.sha256(
        operation_scope.encode("utf-8")
    ).hexdigest()
    now = store._now()  # test fixture uses the same injected durable clock
    output_contract = {
        "type": "object",
        "required": ["receipt_ref", "result"],
        "properties": {
            "receipt_ref": {"type": "string"},
            "result": {"type": "string"},
        },
    }
    artifact_payload = {
        "receipt_ref": receipt_ref,
        "result": "dispatcher-fixture-completed",
    }
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=1,
        planner_ref=f"planner://dispatcher/{graph_id}",
        status=TaskGraphStatus.COMPLETED,
        created_at=now,
        result_node_id=node_id,
        nodes=(
            TaskNode(
                node_id=node_id,
                dependencies=(),
                route="chat:dispatcher-fixture",
                input_refs=(f"input://dispatcher/{graph_id}",),
                output_contract=output_contract,
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=(),
                status=TaskNodeStatus.ACCEPTED,
                status_version=7,
            ),
        ),
    )
    seed_raw_task_graph_for_test(
        store,
        graph,
        outbox=OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph_id,
            aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            graph_revision=1,
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph_id,
                aggregate_version=0,
                graph_revision=1,
            ),
            idempotency_key=f"dispatcher:{graph_id}:graph-created",
            outbox_id=f"outbox::dispatcher::{graph_id}::created",
        ),
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms,
                completed_ts_ms, updated_ts_ms
            ) VALUES (?, ?, 1, ?, 1, 'SUCCEEDED', 3, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_id,
                graph_id,
                node_id,
                "worker-dispatcher-test",
                operation_scope,
                operation_fingerprint,
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_lease(
                lease_id, attempt_id, owner_id, fencing_token,
                acquired_ts_ms, heartbeat_ts_ms, expires_ts_ms,
                released_ts_ms, status, status_version
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'RELEASED', 1)
            """,
            (
                lease_id,
                attempt_id,
                "worker-dispatcher-test",
                now,
                now,
                now + 60_000,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_idempotency_reservation(
                reservation_id, business_scope, operation_fingerprint,
                attempt_id, lease_id, owner_id, fencing_token, expires_ts_ms,
                status, status_version, receipt_ref, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'COMMITTED', 1, ?, ?, ?)
            """,
            (
                f"reservation::{graph_id}",
                operation_scope,
                operation_fingerprint,
                attempt_id,
                lease_id,
                "worker-dispatcher-test",
                now + 60_000,
                receipt_ref,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_node_artifact(
                artifact_ref, schema_version, graph_id, graph_revision,
                node_id, attempt_id, receipt_ref, output_contract_json,
                output_contract_sha256, payload_json, payload_sha256,
                created_ts_ms
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_task_result_artifact_ref(graph_id, 1, node_id),
                NODE_ARTIFACT_SCHEMA_VERSION,
                graph_id,
                node_id,
                attempt_id,
                receipt_ref,
                canonical_artifact_json(output_contract),
                artifact_digest(output_contract),
                canonical_artifact_json(artifact_payload),
                artifact_digest(artifact_payload),
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
    business_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        **(
            dict(business_payload)
            if business_payload is not None
            else {"effect_type": effect_type, "graph_id": graph_id}
        ),
        "_action_preview_context": {
            "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
            "context_pack_digest": "sha256:" + ("1" * 64),
            "scene_identity": {
                "revision": 1,
                "digest": "sha256:" + ("2" * 64),
            },
            "parent_governor_grant_ref": f"grant://dispatcher/{graph_id}/parent",
            "node_grant_ref": f"grant://dispatcher/{graph_id}/{node_id}",
            "node_grant_digest": "sha256:" + ("3" * 64),
        },
    }
    payload_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return seed_raw_acceptance_effect_for_test(
        store,
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
        allow_terminal_graph_insert_for_test=True,
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
        ("life.conversation_turn_recorded", "n-life-conversation"),
        ("conversation.preference_signal", "y-preference"),
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
            "conversation.preference_signal",
            "life.conversation_turn_recorded",
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
        action_preview_service=_TestActionPreviewAuthority(),
    )

    results = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-order",
        graph_revision=1,
    )

    assert [row[1] for row in calls] == [
        "conversation.user_turn",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
        "conversation.preference_signal",
    ]
    assert [row["effect_type"] for row in results] == [
        "conversation.user_turn",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
        "conversation.preference_signal",
        "unknown.audit",
    ]
    assert [row["status"] for row in results] == [
        AcceptanceEffectStatus.COMMITTED.value,
        AcceptanceEffectStatus.COMMITTED.value,
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
    assert len(calls) == 5
    assert [row["status"] for row in replay] == [
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
        "FAILED",
    ]
    assert await dispatcher.dispatch_effect(
        str(effects["conversation.user_turn"]["effect_id"])
    ) is None
    assert len(calls) == 5


@pytest.mark.asyncio
async def test_interactive_dispatch_commits_reply_before_preference_projection(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    _stage_semantic_chain(store, "graph-interactive-cutoff")
    calls: list[tuple[str, str]] = []
    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers=_handlers(calls),
        owner_id="dispatcher-interactive-cutoff",
        action_preview_service=_TestActionPreviewAuthority(),
    )

    snapshot = await dispatcher.dispatch_interactive_graph(
        graph_id="graph-interactive-cutoff",
        graph_revision=1,
    )

    assert [row[1] for row in calls] == [
        "conversation.user_turn",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
    ]
    assert [row["effect_type"] for row in snapshot] == [
        "conversation.user_turn",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
        "conversation.preference_signal",
    ]
    assert [row["status"] for row in snapshot] == [
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
        "COMMITTED",
        "PENDING",
    ]

    drained = await dispatcher.drain_ready(limit=100)
    assert [row["effect_type"] for row in drained] == [
        "conversation.preference_signal",
    ]
    assert all(row["status"] == "COMMITTED" for row in drained)


@pytest.mark.asyncio
async def test_unrelated_effects_do_not_share_one_process_lock(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    effects_a = _stage_semantic_chain(store, "graph-concurrent-a")
    effects_b = _stage_semantic_chain(store, "graph-concurrent-b")
    both_started = asyncio.Event()
    release = asyncio.Event()
    started: list[str] = []

    async def handle(payload: dict[str, Any]) -> str:
        started.append(str(payload["graph_id"]))
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return f"effect-receipt://dispatcher/{payload['graph_id']}/concurrent"

    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={"conversation.user_turn": handle},
        owner_id="dispatcher-per-effect-locks",
        action_preview_service=_TestActionPreviewAuthority(),
    )
    tasks = (
        asyncio.create_task(
            dispatcher.dispatch_effect(
                str(effects_a["conversation.user_turn"]["effect_id"])
            )
        ),
        asyncio.create_task(
            dispatcher.dispatch_effect(
                str(effects_b["conversation.user_turn"]["effect_id"])
            )
        ),
    )
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1.0)
        release.set()
        results = await asyncio.gather(*tasks)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    assert set(started) == {"graph-concurrent-a", "graph-concurrent-b"}
    assert all(
        result is not None and result["status"] == "COMMITTED"
        for result in results
    )


@pytest.mark.asyncio
async def test_local_acceptance_projection_reuses_node_lineage_without_terminal_preview(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    node_id, attempt_id = _seed_completed_graph(store, "graph-local-projection")
    effect = _stage_effect(
        store,
        graph_id="graph-local-projection",
        node_id=node_id,
        attempt_id=attempt_id,
        effect_type="conversation.user_turn",
        opaque_order="local",
    )

    class ForbiddenPreview:
        def issue_receipt(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("passive local projection must not issue Preview")

        def require_execution(self, **_kwargs: Any) -> None:
            raise AssertionError("passive local projection must not reopen Preview")

    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={
            "conversation.user_turn": lambda payload: (
                "effect-receipt://dispatcher/local-projection"
                if payload["graph_id"] == "graph-local-projection"
                else ""
            )
        },
        owner_id="dispatcher-local-projection",
        action_preview_service=ForbiddenPreview(),
    )

    settled = await dispatcher.dispatch_effect(str(effect["effect_id"]))

    assert settled is not None
    assert settled["status"] == AcceptanceEffectStatus.COMMITTED.value


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
            "conversation.preference_signal": forbidden_later,
            "life.conversation_turn_recorded": forbidden_later,
            "life.foreground_completed": forbidden_later,
            "surface.reply_delivery": forbidden_later,
        },
        owner_id="dispatcher-fail-closed",
        max_attempts=1,
        action_preview_service=_TestActionPreviewAuthority(),
    )

    results = await dispatcher.dispatch_graph(
        graph_id="graph-dispatch-failure",
        graph_revision=1,
    )

    assert calls == ["conversation.user_turn"]
    assert len(results) == 1
    assert results[0]["status"] == AcceptanceEffectStatus.FAILED.value
    assert results[0]["reason_code"] == ACCEPTANCE_EFFECT_HANDLER_FAILED
    assert "conversation persistence unavailable" not in results[0]["last_error"]
    assert "error_type=builtins.RuntimeError" in results[0]["last_error"]
    assert "incident=sha256:" in results[0]["last_error"]
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
            "conversation.preference_signal": committed_later,
            "life.conversation_turn_recorded": committed_later,
            "life.foreground_completed": committed_later,
            "surface.reply_delivery": committed_later,
        },
        owner_id="dispatcher-retry",
        max_attempts=3,
        retry_base_ms=10,
        retry_max_ms=10,
        action_preview_service=_TestActionPreviewAuthority(),
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
        "COMMITTED",
        "COMMITTED",
    ]
    assert calls == [
        "conversation.user_turn",
        "conversation.user_turn",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
        "conversation.preference_signal",
    ]
    conversation = store.get_acceptance_effect_intent(
        str(effects["conversation.user_turn"]["effect_id"])
    )
    assert conversation["attempt_count"] == 2
    assert conversation["fencing_token"] == 2


@pytest.mark.asyncio
async def test_unknown_handler_outcome_survives_retry_limit_and_restart_reconciles(
    tmp_path: Path,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    graph_id = "graph-sys-mode-unknown-outcome"
    node_id, attempt_id = _seed_completed_graph(store, graph_id)
    event_id = "obs-sys-mode:" + "7" * 64
    frozen_business_payload = {
        "operator_command_id": "sys_mode.switch",
        "request_id": "request-sys-mode-unknown-outcome",
        "target_mode": "focus",
        "source": "manual",
        "reason": "reconcile response loss",
        "event_id": event_id,
        "runtime_scope_id": "runtime-scope-unknown-outcome",
    }
    effect = _stage_effect(
        store,
        graph_id=graph_id,
        node_id=node_id,
        attempt_id=attempt_id,
        effect_type="host.sys_mode_transition",
        opaque_order="sys-mode",
        business_payload=frozen_business_payload,
    )
    durable_hard_state_events: dict[str, dict[str, Any]] = {}
    calls: list[tuple[str, str]] = []

    class ExactEventHandler:
        @staticmethod
        def _exact(payload: dict[str, Any]) -> dict[str, Any]:
            return {key: payload[key] for key in frozen_business_payload}

        def __call__(self, payload: dict[str, Any]) -> str:
            exact_payload = self._exact(payload)
            exact_event_id = str(exact_payload["event_id"])
            calls.append(("invoke", exact_event_id))
            # Simulate HardState committing before its caller loses the
            # response/reopen attribution boundary.
            durable_hard_state_events[exact_event_id] = exact_payload
            raise AcceptanceEffectOutcomeUnknown(
                f"HardState commit {exact_event_id} requires exact reopen"
            )

        def reconcile(self, payload: dict[str, Any]) -> str:
            exact_payload = self._exact(payload)
            exact_event_id = str(exact_payload["event_id"])
            calls.append(("reconcile", exact_event_id))
            assert durable_hard_state_events.get(exact_event_id) == exact_payload
            return "host-sys-mode-replayed://sha256/" + "8" * 64

    handler = ExactEventHandler()

    first_dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={"host.sys_mode_transition": handler},
        owner_id="dispatcher-sys-mode-before-restart",
        max_attempts=1,
        retry_base_ms=10,
        retry_max_ms=10,
        action_preview_service=_typed_test_action_preview_authority(),
    )

    first = await first_dispatcher.dispatch_effect(str(effect["effect_id"]))

    assert first is not None
    assert first["status"] == AcceptanceEffectStatus.PENDING.value, json.dumps(
        first, sort_keys=True, default=str
    )
    assert first["reason_code"] == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
    assert first["side_effect_state"] == "UNKNOWN"
    assert first["dispatch_state"] == "RECOVERY_PENDING"
    assert first["reconciliation_state"] == "RECONCILIATION_REQUIRED"
    assert first["reconciliation_required"] is True
    durable_pending = store.get_acceptance_effect_intent(str(effect["effect_id"]))
    assert durable_pending["status"] == AcceptanceEffectStatus.PENDING.value
    assert (
        durable_pending["reason_code"]
        == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
    )
    assert durable_pending["attempt_count"] == 1
    assert durable_hard_state_events[event_id] == frozen_business_payload

    # Before the durable retry time the exact effect stays quarantined, while
    # the rest of the personal Host remains available and recovery continues
    # in the single background owner.
    pending_runtime = AcceptanceEffectRuntime(
        dispatcher=first_dispatcher,
        poll_seconds=0.01,
    )
    await pending_runtime.activate()
    assert pending_runtime.active is True
    await pending_runtime.aclose()

    clock.advance(10)
    restarted_store = DurableTaskStore(store.db_path, clock=clock)
    restarted_dispatcher = AcceptanceEffectDispatcher(
        task_store=restarted_store,
        handlers={"host.sys_mode_transition": handler},
        owner_id="dispatcher-sys-mode-after-restart",
        max_attempts=1,
        retry_base_ms=10,
        retry_max_ms=10,
        action_preview_service=_typed_test_action_preview_authority(),
    )
    restarted_runtime = AcceptanceEffectRuntime(
        dispatcher=restarted_dispatcher,
        poll_seconds=0.01,
    )

    await restarted_runtime.activate()
    try:
        assert restarted_runtime.active is True
        committed = restarted_store.get_acceptance_effect_intent(
            str(effect["effect_id"])
        )
        assert committed["status"] == AcceptanceEffectStatus.COMMITTED.value
        assert committed["receipt_ref"] == (
            "host-sys-mode-replayed://sha256/" + "8" * 64
        )
        assert committed["attempt_count"] == 2
        assert calls == [("invoke", event_id), ("reconcile", event_id)]
        assert len(durable_hard_state_events) == 1
    finally:
        await restarted_runtime.aclose()


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
        action_preview_service=_TestActionPreviewAuthority(),
    )
    with pytest.raises(TaskStoreError) as lost_fence:
        await stale.dispatch_effect(str(effect["effect_id"]))
    assert lost_fence.value.reason_code == TASK_STORE_LEASE_CONFLICT
    claimed = store.get_acceptance_effect_intent(str(effect["effect_id"]))
    assert claimed["status"] == AcceptanceEffectStatus.CLAIMED.value
    assert claimed["fencing_token"] == 1
    assert claimed["reason_code"] is None

    class Recover:
        def __call__(self, _payload: dict[str, Any]) -> str:
            calls.append("forbidden-ordinary-replay")
            raise AssertionError("reclaimed effect must not invoke ordinary handler")

        def reconcile(self, _payload: dict[str, Any]) -> str:
            calls.append("recovery-reconcile")
            return "conversation-receipt://dispatcher/recovered"

    restarted = AcceptanceEffectDispatcher(
        task_store=DurableTaskStore(store.db_path, clock=clock),
        handlers={"conversation.user_turn": Recover()},
        owner_id="dispatcher-restarted",
        lease_ms=100,
        action_preview_service=_TestActionPreviewAuthority(),
    )
    committed = await restarted.dispatch_effect(str(effect["effect_id"]))
    assert committed is not None
    assert committed["status"] == AcceptanceEffectStatus.COMMITTED.value
    assert committed["fencing_token"] == 2
    assert committed["attempt_count"] == 2
    assert calls == ["stale-owner", "recovery-reconcile"]

    assert await restarted.dispatch_effect(str(effect["effect_id"])) is None
    assert calls == ["stale-owner", "recovery-reconcile"]


@pytest.mark.asyncio
@pytest.mark.parametrize("registered_handler", [True, False])
async def test_reclaimed_generic_effect_without_reconciler_stays_unknown(
    tmp_path: Path,
    registered_handler: bool,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    node_id, attempt_id = _seed_completed_graph(store, "graph-generic-no-reconciler")
    effect = _stage_effect(
        store,
        graph_id="graph-generic-no-reconciler",
        node_id=node_id,
        attempt_id=attempt_id,
        effect_type="conversation.user_turn",
        opaque_order="only",
    )
    calls: list[str] = []

    def expire_after_effect(_payload: dict[str, Any]) -> str:
        calls.append("initial-invocation")
        clock.advance(11)
        return "conversation-receipt://dispatcher/unknown-boundary"

    stale = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={"conversation.user_turn": expire_after_effect},
        owner_id="dispatcher-generic-stale",
        lease_ms=10,
        action_preview_service=_TestActionPreviewAuthority(),
    )
    with pytest.raises(TaskStoreError):
        await stale.dispatch_effect(str(effect["effect_id"]))

    def forbidden_replay(_payload: dict[str, Any]) -> str:
        calls.append("forbidden-replay")
        return "conversation-receipt://dispatcher/duplicate"

    restarted = AcceptanceEffectDispatcher(
        task_store=DurableTaskStore(store.db_path, clock=clock),
        handlers=(
            {"conversation.user_turn": forbidden_replay}
            if registered_handler
            else {"unrelated.effect": forbidden_replay}
        ),
        owner_id="dispatcher-generic-restarted",
        lease_ms=100,
        retry_base_ms=10,
        retry_max_ms=10,
        action_preview_service=_TestActionPreviewAuthority(),
    )

    pending = await restarted.dispatch_effect(str(effect["effect_id"]))

    assert pending is not None
    assert pending["status"] == AcceptanceEffectStatus.PENDING.value
    assert pending["reason_code"] == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
    assert pending["side_effect_state"] == "UNKNOWN"
    assert pending["reconciliation_state"] == "RECONCILIATION_REQUIRED"
    assert calls == ["initial-invocation"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_outcome",
    [None, {}, {"event_id": "event://not-a-receipt"}, {"command_id": "command://not-a-receipt"}],
)
async def test_handler_without_explicit_durable_receipt_fails_closed(
    tmp_path: Path,
    handler_outcome: Any,
) -> None:
    clock = MutableClock()
    store = _store(tmp_path, clock)
    node_id, attempt_id = _seed_completed_graph(store, "graph-missing-receipt")
    effect = _stage_effect(
        store,
        graph_id="graph-missing-receipt",
        node_id=node_id,
        attempt_id=attempt_id,
        effect_type="conversation.user_turn",
        opaque_order="only",
    )
    dispatcher = AcceptanceEffectDispatcher(
        task_store=store,
        handlers={"conversation.user_turn": lambda _payload: handler_outcome},
        owner_id="dispatcher-missing-receipt",
        action_preview_service=_TestActionPreviewAuthority(),
    )

    failed = await dispatcher.dispatch_effect(str(effect["effect_id"]))

    assert failed is not None
    assert failed["status"] == AcceptanceEffectStatus.FAILED.value
    assert failed["reason_code"] == ACCEPTANCE_EFFECT_HANDLER_FAILED
    assert failed["receipt_ref"] is None
    assert failed["last_error"].startswith(
        "error_type=host_brain.task_runtime.acceptance_effect_dispatcher."
        "AcceptanceEffectDispatchError incident=sha256:"
    )
    assert "authoritative durable receipt_ref" not in failed["last_error"]


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
        action_preview_service=_TestActionPreviewAuthority(),
    )

    first = await dispatcher.drain_ready(limit=4)
    assert len(first) == 4
    assert calls == [
        ("graph-drain-a", "conversation.user_turn"),
        ("graph-drain-a", "life.conversation_turn_recorded"),
        ("graph-drain-a", "life.foreground_completed"),
        ("graph-drain-a", "surface.reply_delivery"),
    ]

    second = await dispatcher.drain_ready(limit=4)
    assert len(second) == 4
    assert calls[-4:] == [
        ("graph-drain-a", "conversation.preference_signal"),
        ("graph-drain-b", "conversation.user_turn"),
        ("graph-drain-b", "life.conversation_turn_recorded"),
        ("graph-drain-b", "life.foreground_completed"),
    ]

    third = await dispatcher.drain_ready(limit=4)
    assert len(third) == 2
    assert calls[-2:] == [
        ("graph-drain-b", "surface.reply_delivery"),
        ("graph-drain-b", "conversation.preference_signal"),
    ]
    assert await dispatcher.drain_ready(limit=4) == ()
    assert len(calls) == 10
