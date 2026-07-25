from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app import acceptance_effect_handlers
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.life import (
    PROCESS_LIFE_PROVIDER,
    build_process_life_container,
)
from host_brain.conversation.memory_spine import append_visible_conversation_turn
from host_brain.digital_life.application_boundary import submit_life_command
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.persistence.context_memory_repository import MemoryEventRepository
from host_brain.persistence.context_repository import ContextRepository
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED,
    AcceptanceEffectOutcomeUnknown,
    _receipt_ref,
)
from natsume_contracts.edge.transport_v1 import EdgeTransportResult


def _agent_runtime_ports(
    record_user_turn,
) -> AgentRuntimeApplicationPorts:  # noqa: ANN001
    async def _empty_async(**_kwargs):  # noqa: ANN003, ANN202
        return None

    return AgentRuntimeApplicationPorts(
        record_user_turn=record_user_turn,
        record_assistant_turn=lambda **_kwargs: {},
        run_retrieval=_empty_async,
        filter_retrieval_evidence_items_for_prompt=lambda **_kwargs: [],
        build_user_preference_pack=lambda **_kwargs: {},
        resolve_host_llm_runtime_async=_empty_async,
        resolve_surface_reply_llm_runtime=lambda **kwargs: dict(
            kwargs.get("base_runtime") or {}
        ),
        resolve_state_arbiter_bond_fact=_empty_async,
        apply_surface_emotion_bond_flow=lambda **_kwargs: {},
        build_conversation_experience_read_pack=lambda **_kwargs: {},
        drive_stream_vts_emotion=_empty_async,
        resolve_tts_route=lambda **_kwargs: None,
        conversation_history=lambda: None,
        life_memory_service=lambda: None,
        audit_ledger=lambda: None,
        prompt_registry=lambda: None,
        model_attempt_service=lambda: None,
        context_read_ports=ConversationContextReadPorts(
            conversation_history=lambda: None,
            conversation_turn_store=lambda: None,
            session_topic_ledger_store=lambda: None,
            session_tactic_ledger_store=lambda: None,
        ),
        task_status_projection=lambda: None,
        persist_post_process_audit=_empty_async,
    )

def _conversation_payload() -> dict[str, Any]:
    return {
        "request_id": "request-ne01-conversation-response-loss",
        "session_id": "session-ne01-conversation-response-loss",
        "person_id": "person-ne01-conversation-response-loss",
        "content": "真实 SQLite 写入后丢失响应",
        "ts_ms": 1_725_000_001_000,
        "lang": "zh",
        "metadata": {
            "input_source": "integration",
            "reply_channel": "qq",
            "reply_target": "conversation-ne01",
            "attachments": [],
            "multimodal_observations": {},
            "attachment_summary": "",
            "source": "governed_task_acceptance",
        },
    }


def _life_payload() -> dict[str, Any]:
    return {
        "command_type": "foreground_completed",
        "person_id": "person-ne01-life-response-loss",
        "command_id": (
            "life::foreground_completed::request-ne01-life-response-loss"
        ),
        "correlation_id": "request-ne01-life-response-loss",
        "payload": {
            "request_id": "request-ne01-life-response-loss",
            "session_id": "session-ne01-life-response-loss",
            "person_id": "person-ne01-life-response-loss",
            "user_message": "input",
            "assistant_reply": "accepted output",
            "completed_at_ms": 1_725_000_002_000,
            "state_patch": {},
            "emotion_context": {},
            "bond_summary": {},
            "projection_intents": [],
            "deliveries": [],
        },
        "created_at_ms": 1_725_000_002_000,
        "metadata": {
            "source": "governed_task_acceptance",
            "reply_sendable": True,
            "reply_persistable": True,
            "delivery_authorized": False,
        },
    }


def _surface_payload() -> dict[str, Any]:
    value = {
        "request_id": "request-ne01-surface-response-loss",
        "session_id": "session-ne01-surface-response-loss",
        "person_id": "person-ne01-surface-response-loss",
        "reply_channel": "qq",
        "reply_target": "conversation-ne01-surface",
        "decision": "APPROVE",
        "narrative": "accepted reply",
        "authority": {
            "request_id": "request-ne01-surface-response-loss",
            "governor_grant_ref": "governor-grant://ne01/surface",
            "authority_kind": "governed_task_acceptance",
            "authority_principal_id": "person-ne01-surface-response-loss",
        },
    }
    effect_id = "effect-ne01-surface-response-loss"
    return {
        **value,
        "_action_preview_terminal_proof": {
            "service": SimpleNamespace(require_execution=lambda **_kwargs: object()),
            "event_id": "runtimeevent://ne01/surface-response-loss",
            "boundary": {
                "action_kind": "acceptance-effect.surface.reply_delivery",
                "action_scope": effect_id,
                "risk_level": 3,
                "action_args": value,
                "context_pack_digest": "sha256:" + "1" * 64,
                "scene_identity": "scene://ne01/surface-response-loss",
                "task_graph_id": "graph-ne01-surface-response-loss",
                "graph_revision": 1,
                "task_node_id": "node-ne01-surface-response-loss",
                "parent_governor_grant_ref": "governor-grant://ne01/surface",
                "node_grant_ref": "node-grant://ne01/surface",
                "node_grant": {
                    "grant_ref": "node-grant://ne01/surface",
                    "binding_digest": "sha256:" + "2" * 64,
                },
                "authority_context": {
                    "schema_version": (
                        "Natsume.action_preview."
                        "acceptance_effect_authority_request.v1"
                    ),
                    "effect_id": effect_id,
                    "lease_owner_id": "acceptance-effect-owner",
                    "fencing_token": 1,
                },
            },
        },
    }


def _migrated_ledger(db_path) -> SQLiteLedger:  # noqa: ANN001
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-generic-effect-reconciliation",
        ts_ms=1_725_000_000_000,
    )
    assert migrated.success, migrated.as_dict()
    return SQLiteLedger(str(db_path), check_same_thread=False)


def test_conversation_response_loss_reopens_exact_sqlite_rows_after_restart(
    tmp_path,
) -> None:
    db_path = tmp_path / "conversation-response-loss.sqlite3"
    ledger = _migrated_ledger(db_path)
    life = build_process_life_container(ledger=ledger, adopted_at_ms=1)
    turn_reader = ContextRepository.from_ledger(ledger)
    event_reader = MemoryEventRepository.from_ledger(ledger)
    payload = _conversation_payload()
    writer_calls = 0

    def write_then_lose_response(**kwargs):  # noqa: ANN003, ANN202
        nonlocal writer_calls
        writer_calls += 1
        append_visible_conversation_turn(
            ledger=ledger,
            life_service=life.command_service,
            life_reader=life.snapshot_reader,
            request_id=kwargs["request_id"],
            session_id=kwargs["session_id"],
            person_id=kwargs["person_id"],
            role="user",
            content=kwargs["content"],
            ts_ms=kwargs["ts_ms"],
            lang=kwargs["lang"],
            metadata=kwargs["metadata"],
        )
        # ValueError is deliberately used here: once the writer has crossed
        # SQLite commit, exception type cannot be used to classify this as a
        # safe ordinary retry.
        raise ValueError("simulated response loss after SQLite commit")

    handler = build_production_acceptance_effect_handlers(
        conversation_turn_reader=turn_reader,
        conversation_event_reader=event_reader,
        agent_runtime_ports=_agent_runtime_ports(write_then_lose_response),
    )["conversation.user_turn"]

    recovered = handler(payload)

    assert recovered["status"] == "REPLAYED"
    assert recovered["receipt_ref"].startswith("memory-event://id/memevt_")
    assert recovered["turn_id"].startswith("turn::sha256:")
    assert _receipt_ref(recovered) == recovered["receipt_ref"]
    assert writer_calls == 1
    first_receipt_ref = recovered["receipt_ref"]
    ledger.close()

    # A replacement process builds fresh read repositories from the same
    # canonical DB.  Reconciliation must not invoke the writer again.
    restarted_ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    restarted = build_production_acceptance_effect_handlers(
        conversation_turn_reader=ContextRepository.from_ledger(restarted_ledger),
        conversation_event_reader=MemoryEventRepository.from_ledger(
            restarted_ledger
        ),
    )["conversation.user_turn"]

    exact = restarted.reconcile(payload)

    assert exact["receipt_ref"] == first_receipt_ref
    assert exact["status"] == "REPLAYED"
    assert writer_calls == 1
    restarted_ledger.close()


def test_conversation_success_reopens_authority_and_preserves_exact_payload(
    tmp_path,
) -> None:
    db_path = tmp_path / "conversation-read-after-write.sqlite3"
    ledger = _migrated_ledger(db_path)
    life = build_process_life_container(ledger=ledger, adopted_at_ms=1)
    payload = {
        **_conversation_payload(),
        "content": "exact content with spacing",
    }
    writer_calls = 0

    def write_with_untrusted_projection(**kwargs):  # noqa: ANN003, ANN202
        nonlocal writer_calls
        writer_calls += 1
        result = append_visible_conversation_turn(
            ledger=ledger,
            life_service=life.command_service,
            life_reader=life.snapshot_reader,
            request_id=kwargs["request_id"],
            session_id=kwargs["session_id"],
            person_id=kwargs["person_id"],
            role="user",
            content=kwargs["content"],
            ts_ms=kwargs["ts_ms"],
            lang=kwargs["lang"],
            metadata=kwargs["metadata"],
        )
        # Writer-returned identifiers are not Acceptance authority.
        return {
            **result,
            "memory_event_id": "memory-event-untrusted",
            "turn": {"turn_id": "turn-untrusted"},
        }

    turn_reader = ContextRepository.from_ledger(ledger)
    handler = build_production_acceptance_effect_handlers(
        conversation_turn_reader=turn_reader,
        conversation_event_reader=MemoryEventRepository.from_ledger(ledger),
        agent_runtime_ports=_agent_runtime_ports(write_with_untrusted_projection),
    )["conversation.user_turn"]

    result = handler(payload)
    stored = turn_reader.find_turn(
        session_id=payload["session_id"],
        request_id=payload["request_id"],
        role="user",
    )

    assert result["status"] == "APPLIED"
    assert result["memory_event_id"] != "memory-event-untrusted"
    assert result["turn_id"] != "turn-untrusted"
    assert stored is not None and stored["content"] == payload["content"]
    assert writer_calls == 1
    ledger.close()


@pytest.mark.parametrize(
    "drift",
    [
        {"content": "x" * 4_001},
        {"ts_ms": -1},
        {"ts_ms": "1725000001000"},
        {"session_id": " session-drift "},
        {"content": "  noncanonical\ncontent  "},
    ],
)
def test_conversation_invalid_payload_fails_before_writer(drift) -> None:
    calls = 0

    def forbidden_writer(**_kwargs):  # noqa: ANN003, ANN202
        nonlocal calls
        calls += 1
        raise AssertionError("writer must not be invoked")

    handler = build_production_acceptance_effect_handlers(
        agent_runtime_ports=_agent_runtime_ports(forbidden_writer),
    )["conversation.user_turn"]

    with pytest.raises(ValueError):
        handler({**_conversation_payload(), **drift})

    assert calls == 0


def test_life_response_loss_reopens_command_receipt_after_restart(
    tmp_path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "life-response-loss.sqlite3"
    ledger = _migrated_ledger(db_path)
    life = build_process_life_container(ledger=ledger, adopted_at_ms=1)
    payload = _life_payload()
    real_submit = submit_life_command
    writer_calls = 0

    def write_then_lose_response(**kwargs):  # noqa: ANN003, ANN202
        nonlocal writer_calls
        writer_calls += 1
        real_submit(**kwargs)
        raise ValueError("simulated response loss after Life receipt commit")

    monkeypatch.setattr(
        PROCESS_LIFE_PROVIDER,
        "require",
        lambda: life,
    )
    monkeypatch.setattr(
        acceptance_effect_handlers,
        "submit_life_command",
        write_then_lose_response,
    )
    handler = build_production_acceptance_effect_handlers(
        life_receipt_reader_provider=lambda: life.snapshot_reader,
    )["life.foreground_completed"]

    recovered = handler(payload)

    assert recovered["status"] == "APPLIED"
    assert recovered["replayed"] is True
    assert recovered["receipt_ref"].startswith("life-event://id/life_evt_")
    assert _receipt_ref(recovered) == recovered["receipt_ref"]
    assert writer_calls == 1
    first_receipt_ref = recovered["receipt_ref"]
    ledger.close()

    # Recreate the repository/reader exactly as a replacement Host would.
    restarted_reader = LifeSnapshotReader(LifeCommandRepository(db_path))
    restarted = build_production_acceptance_effect_handlers(
        life_receipt_reader_provider=lambda: restarted_reader,
    )["life.foreground_completed"]

    exact = restarted.reconcile(payload)

    assert exact["receipt_ref"] == first_receipt_ref
    assert exact["replayed"] is True
    assert writer_calls == 1


@pytest.mark.asyncio
async def test_surface_response_loss_and_restart_never_resend_without_query_port(
    monkeypatch,
) -> None:
    calls = 0

    async def lose_response(**_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("sensitive transport response detail")

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        lose_response,
    )
    first = build_production_acceptance_effect_handlers()[
        "surface.reply_delivery"
    ]

    with pytest.raises(AcceptanceEffectOutcomeUnknown) as uncertain:
        await first(_surface_payload())

    assert uncertain.value.reason_code == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED
    assert "sensitive transport response detail" not in uncertain.value.detail
    assert "TimeoutError[incident:" in uncertain.value.detail
    assert calls == 1

    # Rebuilding the handler represents dispatcher/Host restart.  There is no
    # authenticated Gateway read API yet, so it must stay unresolved and must
    # never invoke POST /reply again.
    restarted = build_production_acceptance_effect_handlers()[
        "surface.reply_delivery"
    ]
    with pytest.raises(AcceptanceEffectOutcomeUnknown, match="no authenticated"):
        await restarted.reconcile(_surface_payload())

    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["enqueued_then_500", "lost_body"])
async def test_surface_non_success_without_explicit_no_effect_stays_unknown(
    monkeypatch,
    response_kind: str,
) -> None:
    calls = 0
    payload = _surface_payload()
    event = (
        {
            "status": "queued",
            "outbox_id": "gateway-outbox-response-lost",
            "delivery_id": acceptance_effect_handlers._surface_delivery_id(payload),
            "platform": payload["reply_channel"],
            "conversation_id": payload["reply_target"],
                "payload_digest": (
                    acceptance_effect_handlers._surface_gateway_payload_digest(
                        payload,
                        effect_id=str(
                            payload["_action_preview_terminal_proof"]["boundary"][
                                "action_scope"
                            ]
                        ),
                    )
                ),
        }
        if response_kind == "enqueued_then_500"
        else {}
    )

    async def non_success(**_kwargs):  # noqa: ANN003, ANN202
        nonlocal calls
        calls += 1
        return EdgeTransportResult(
            success=False,
            status="IN_DOUBT" if response_kind == "enqueued_then_500" else "REJECTED",
            reason_code="GATEWAY_HTTP_RESPONSE_INVALID",
            detail="provider-secret-must-not-persist",
            event=event,
            latency_ms=5,
        )

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        non_success,
    )
    handler = build_production_acceptance_effect_handlers()["surface.reply_delivery"]

    with pytest.raises(AcceptanceEffectOutcomeUnknown) as uncertain:
        await handler(payload)
    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        await handler.reconcile(payload)

    assert "provider-secret-must-not-persist" not in uncertain.value.detail
    assert calls == 1


@pytest.mark.asyncio
async def test_surface_explicit_boolean_false_is_the_only_retryable_failure(
    monkeypatch,
) -> None:
    calls = 0

    async def rejected_before_effect(**_kwargs):  # noqa: ANN003, ANN202
        nonlocal calls
        calls += 1
        return EdgeTransportResult(
            success=False,
            status="REJECTED",
            reason_code="PRE_ENQUEUE_REJECTED",
            detail="sensitive validation context",
            event={"external_side_effects": False},
            latency_ms=0,
        )

    monkeypatch.setattr(
        "host_brain.channels.edge_transport.submit_gateway_reply",
        rejected_before_effect,
    )
    handler = build_production_acceptance_effect_handlers()["surface.reply_delivery"]

    with pytest.raises(RuntimeError) as failed:
        await handler(_surface_payload())

    assert not isinstance(failed.value, AcceptanceEffectOutcomeUnknown)
    assert "sensitive validation context" not in str(failed.value)
    assert calls == 1

def test_conversation_reconciliation_rejects_payload_drift_without_writer(
    tmp_path,
) -> None:
    db_path = tmp_path / "conversation-drift.sqlite3"
    ledger = _migrated_ledger(db_path)
    handler = build_production_acceptance_effect_handlers(
        conversation_turn_reader=ContextRepository.from_ledger(ledger),
        conversation_event_reader=MemoryEventRepository.from_ledger(ledger),
    )["conversation.user_turn"]
    drifted = {**_conversation_payload(), "content": "different content"}

    with pytest.raises(AcceptanceEffectOutcomeUnknown, match="exact read-only"):
        handler.reconcile(drifted)

    ledger.close()


def test_unknown_detail_persists_only_opaque_failure_digest() -> None:
    secret = "provider-secret=do-not-persist"

    def fail_with_sensitive_detail(**_kwargs):  # noqa: ANN003, ANN202
        raise OSError(secret)

    handler = build_production_acceptance_effect_handlers(
        agent_runtime_ports=_agent_runtime_ports(fail_with_sensitive_detail),
    )["conversation.user_turn"]

    with pytest.raises(AcceptanceEffectOutcomeUnknown) as uncertain:
        handler(_conversation_payload())

    assert secret not in uncertain.value.detail
    assert "OSError[incident:" in uncertain.value.detail
    assert uncertain.value.reason_code == ACCEPTANCE_EFFECT_RECONCILIATION_REQUIRED


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("command_type", "FOREGROUND_COMPLETED"),
        ("person_id", " person-ne01-life-response-loss"),
        ("correlation_id", "request-ne01-life-response-loss "),
        ("created_at_ms", "1725000002000"),
        ("created_at_ms", -1),
        ("created_at_ms", True),
    ),
)
def test_life_effect_rejects_post_acceptance_normalization(
    field: str,
    value: Any,
) -> None:
    payload = {**_life_payload(), field: value}

    with pytest.raises(ValueError):
        acceptance_effect_handlers._accepted_life_request(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_id", " request-ne01-surface-response-loss"),
        ("reply_channel", "QQ"),
        ("reply_target", "conversation-ne01-surface "),
        ("decision", "approve"),
        ("narrative", 42),
    ),
)
def test_surface_effect_rejects_post_acceptance_normalization(
    field: str,
    value: Any,
) -> None:
    payload = {**_surface_payload(), field: value}

    with pytest.raises(ValueError):
        acceptance_effect_handlers._accepted_surface_request(payload)
