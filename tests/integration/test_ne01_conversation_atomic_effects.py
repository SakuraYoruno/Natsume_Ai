from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from host_brain.app import acceptance_effect_handlers
from host_brain.app import user_preference_store as user_preference_store_module
from host_brain.app.acceptance_effect_handlers import (
    build_production_acceptance_effect_handlers,
)
from host_brain.app.user_preference_store import UserPreferenceStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.life import (
    PROCESS_LIFE_PROVIDER,
    build_process_life_container,
)
from host_brain.contracts.conversation_acceptance_effects import (
    LIFE_CONVERSATION_PROJECTION_SCHEMA_V1,
    PREFERENCE_SIGNAL_SCHEMA_V1,
    conversation_life_command_id,
    preference_operation_id,
)
from host_brain.digital_life.application_boundary import submit_life_command
from host_brain.digital_life.projection import LifeProjectionDispatcher
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.persistence.context_preference_repository import (
    PREFERENCE_EFFECT_MIGRATION_ASSET_KEY,
    PREFERENCE_EFFECT_MIGRATION_SHA256,
)
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)


NOW = 1_735_000_000_000


def _ledger(tmp_path, name: str) -> tuple[Any, SQLiteLedger]:
    db_path = tmp_path / name
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne01-conversation-effects::{name}",
        ts_ms=NOW,
    )
    assert result.success, result.as_dict()
    return db_path, SQLiteLedger(str(db_path), check_same_thread=False)


def _turn_fields(*, request_id: str, content: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "session_id": "session-ne01-conversation-effects",
        "person_id": "person-ne01-conversation-effects",
        "content": content,
        "ts_ms": NOW + 100,
        "lang": "zh",
        "metadata": {
            "input_source": "integration",
            "reply_channel": "qq",
            "reply_target": "conversation-ne01-effects",
            "attachments": [],
            "multimodal_observations": {},
            "attachment_summary": "",
            "source": "governed_task_acceptance",
        },
    }


def _preference_payload(*, request_id: str, content: str) -> dict[str, Any]:
    return {
        "schema_version": PREFERENCE_SIGNAL_SCHEMA_V1,
        "operation_id": preference_operation_id(
            graph_id="graph-ne01-conversation-effects",
            graph_revision=1,
            node_id="node-ne01-conversation-effects",
            attempt_id=f"attempt::{request_id}",
            request_id=request_id,
        ),
        **_turn_fields(request_id=request_id, content=content),
    }


def _life_payload(*, request_id: str, content: str) -> dict[str, Any]:
    return {
        "schema_version": LIFE_CONVERSATION_PROJECTION_SCHEMA_V1,
        "command_id": conversation_life_command_id(request_id),
        **_turn_fields(request_id=request_id, content=content),
    }


def _ack_life_projection(message) -> dict[str, Any]:  # noqa: ANN001
    return {
        "status": "PROJECTED_FROM_LIFE_JOURNAL",
        "target": message.target,
        "intent_id": message.intent_id,
    }


def test_0118_is_catalogued_and_receipt_tables_are_immutable(tmp_path) -> None:
    db_path, ledger = _ledger(tmp_path, "migration.sqlite3")
    migration = (
        Path(__file__).resolve().parents[2]
        / "host_brain/audit/migrations/0118_conversation_acceptance_effects.sql"
    )
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == (
        PREFERENCE_EFFECT_MIGRATION_SHA256
    )
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            """
            SELECT checksum, disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (PREFERENCE_EFFECT_MIGRATION_ASSET_KEY,),
        ).fetchone() == (PREFERENCE_EFFECT_MIGRATION_SHA256, "APPLIED")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "conversation_preference_effect_receipt",
            "conversation_preference_profile_version",
        } <= tables
    ledger.close()


def test_preference_response_loss_reopens_deterministic_events_and_profile(
    tmp_path,
    monkeypatch,
) -> None:
    db_path, ledger = _ledger(tmp_path, "preference-response-loss.sqlite3")
    store = UserPreferenceStore(ledger=ledger)
    store.repository.assert_signal_effect_schema_ready()
    payload = _preference_payload(
        request_id="request-preference-response-loss",
        content="我喜欢奇幻冒险小说",
    )
    real_commit = store.repository.commit_signal_effect
    calls = 0

    def commit_then_lose(**kwargs):  # noqa: ANN003, ANN202
        nonlocal calls
        calls += 1
        real_commit(**kwargs)
        raise TimeoutError("private preference writer response")

    monkeypatch.setattr(store.repository, "commit_signal_effect", commit_then_lose)
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]

    recovered = handler(payload)

    assert recovered["status"] == "APPLIED"
    assert recovered["receipt_ref"].startswith("preference-effect://id/")
    assert len(recovered["event_bindings"]) == 1
    assert recovered["event_bindings"][0]["event_id"].startswith("prefevt_")
    assert len(recovered["event_bindings"][0]["event_id"]) == 72
    assert calls == 1
    first = recovered["receipt_ref"]
    ledger.close()

    restarted_ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    restarted_store = UserPreferenceStore(ledger=restarted_ledger)
    restarted = build_production_acceptance_effect_handlers(
        preference_store=restarted_store,
    )["conversation.preference_signal"]
    exact = restarted.reconcile(payload)

    assert exact["receipt_ref"] == first
    assert exact["event_bindings"] == recovered["event_bindings"]
    assert calls == 1
    restarted_ledger.close()


def test_preference_no_signal_has_real_durable_receipt(tmp_path) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-no-signal.sqlite3")
    store = UserPreferenceStore(ledger=ledger)
    payload = _preference_payload(
        request_id="request-preference-no-signal",
        content="今天天气怎么样",
    )
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]

    result = handler(payload)
    reopened = handler.reconcile(payload)

    assert result["status"] == "NO_SIGNAL"
    assert result["event_bindings"] == []
    assert reopened["receipt_digest"] == result["receipt_digest"]
    assert reopened["receipt_ref"].startswith("preference-effect://id/")
    ledger.close()


@pytest.mark.parametrize("configured_value", ["false", "not-a-boolean"])
def test_preference_privacy_gate_commits_disabled_receipt_without_personal_data(
    tmp_path,
    monkeypatch,
    configured_value,
) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-disabled.sqlite3")
    monkeypatch.setenv("HOST_USER_PREFERENCE_ENABLED", configured_value)
    store = UserPreferenceStore(ledger=ledger)
    payload = _preference_payload(
        request_id="request-preference-disabled",
        content="我喜欢奇幻冒险小说",
    )
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]

    result = handler(payload)
    reopened = handler.reconcile(payload)

    assert result["status"] == "DISABLED"
    assert result["event_bindings"] == []
    assert result["profile_receipts"] == []
    assert reopened["receipt_digest"] == result["receipt_digest"]
    with sqlite3.connect(store.repository.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM user_preference_event").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM user_preference_profile").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM conversation_preference_profile_version"
        ).fetchone()[0] == 0
    ledger.close()


def test_preference_privacy_gate_composition_drift_fails_closed(tmp_path) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-gate-drift.sqlite3")
    store = UserPreferenceStore(ledger=ledger)

    class DriftedStore:
        repository = store.repository
        event_to_dict = store.event_to_dict

    payload = _preference_payload(
        request_id="request-preference-gate-drift",
        content="我喜欢奇幻冒险小说",
    )
    handler = build_production_acceptance_effect_handlers(
        preference_store=DriftedStore(),
    )["conversation.preference_signal"]

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        handler(payload)
    with sqlite3.connect(store.repository.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM user_preference_event").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM conversation_preference_effect_receipt"
        ).fetchone()[0] == 0
    ledger.close()


def test_recommendation_feedback_binds_only_exact_pre_admission_delivery(
    tmp_path,
) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-delivery-boundary.sqlite3")
    store = UserPreferenceStore(ledger=ledger)

    def record(*, person: str, session: str, conversation: str, title: str, at: int):
        return store.record_delivery(
            person_id=person,
            session_id=session,
            domain="lightnovel_recommendation",
            platform="qq",
            conversation_id=conversation,
            request_id=f"request::{title}",
            job_id=f"job::{title}",
            recommendations_ref=f"artifact://{title}",
            ranked_recommendations_ref=f"artifact://ranked/{title}",
            items=[{"recommendation_rank": 1, "title": title, "series_key": title}],
            created_at_ms=at,
        )

    exact = record(
        person="person-ne01-conversation-effects",
        session="session-ne01-conversation-effects",
        conversation="conversation-ne01-effects",
        title="exact-before",
        at=NOW + 50,
    )
    record(
        person="person-ne01-conversation-effects",
        session="session-ne01-conversation-effects",
        conversation="conversation-ne01-effects",
        title="future-exact",
        at=NOW + 200,
    )
    record(
        person="person-ne01-conversation-effects",
        session="other-session",
        conversation="conversation-ne01-effects",
        title="cross-session",
        at=NOW + 90,
    )
    record(
        person="person-ne01-conversation-effects",
        session="session-ne01-conversation-effects",
        conversation="other-conversation",
        title="cross-conversation",
        at=NOW + 90,
    )
    record(
        person="other-person",
        session="session-ne01-conversation-effects",
        conversation="conversation-ne01-effects",
        title="cross-person",
        at=NOW + 90,
    )
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]
    payload = _preference_payload(
        request_id="request-preference-exact-delivery",
        content="第一本我喜欢",
    )

    result = handler(payload)

    assert result["status"] == "APPLIED"
    assert exact is not None
    event_id = result["event_bindings"][0]["event_id"]
    with sqlite3.connect(store.repository.db_path) as conn:
        evidence, attributes = conn.execute(
            """
            SELECT evidence_refs_json, attributes_json
            FROM user_preference_event WHERE event_id = ?
            """,
            (event_id,),
        ).fetchone()
    delivery_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            exact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert f"recommendation_delivery://{exact['delivery_id']}" in evidence
    assert json.loads(attributes)["delivery_sha256"] == delivery_digest
    assert json.loads(attributes)["delivery_id"] == exact["delivery_id"]
    assert result["event_bindings"][0]["event_sha256"].startswith("sha256:")
    assert "future-exact" not in evidence
    assert "cross-session" not in evidence
    assert "cross-conversation" not in evidence
    assert "cross-person" not in evidence
    ledger.close()


def test_preference_receipt_seals_profile_set_and_reconcile_rejects_tamper(
    tmp_path,
) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-profile-tamper.sqlite3")
    store = UserPreferenceStore(ledger=ledger)
    payload = _preference_payload(
        request_id="request-preference-profile-tamper",
        content="我喜欢奇幻冒险小说",
    )
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]
    handler(payload)
    extra_profile_json = (
        '{"domain":"general","person_id":"tampered-person",'
        '"updated_at_ms":1735000000100,"version":1}'
    )
    extra_digest = "sha256:" + hashlib.sha256(
        extra_profile_json.encode("utf-8")
    ).hexdigest()
    insert = """
        INSERT INTO conversation_preference_profile_version(
            operation_id, person_id, domain, version, profile_json,
            profile_sha256, updated_at_ms
        ) VALUES (?, 'tampered-person', 'general', 1, ?, ?, ?)
    """
    with sqlite3.connect(store.repository.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="profile set is sealed"):
            conn.execute(
                insert,
                (payload["operation_id"], extra_profile_json, extra_digest, NOW + 100),
            )
        conn.execute("DROP TRIGGER conversation_preference_profile_version_parent_sealed")
        conn.execute(
            insert,
            (payload["operation_id"], extra_profile_json, extra_digest, NOW + 100),
        )
        conn.commit()

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        handler.reconcile(payload)
    ledger.close()


def test_preference_effect_transaction_rolls_back_when_profile_build_fails(
    tmp_path,
    monkeypatch,
) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-rollback.sqlite3")
    store = UserPreferenceStore(ledger=ledger)
    payload = _preference_payload(
        request_id="request-preference-rollback",
        content="我喜欢奇幻冒险小说",
    )

    def fail_profile_build(**_kwargs):
        raise RuntimeError("private profile build failure")

    monkeypatch.setattr(
        user_preference_store_module,
        "build_profile_from_events",
        fail_profile_build,
    )
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        handler(payload)
    with sqlite3.connect(store.repository.db_path) as conn:
        for table in (
            "user_preference_event",
            "user_preference_profile",
            "conversation_preference_profile_version",
            "conversation_preference_effect_receipt",
        ):
            assert conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
    ledger.close()


def test_old_preference_receipt_survives_newer_profile_version(tmp_path) -> None:
    _db_path, ledger = _ledger(tmp_path, "preference-profile-history.sqlite3")
    store = UserPreferenceStore(ledger=ledger)
    handler = build_production_acceptance_effect_handlers(
        preference_store=store,
    )["conversation.preference_signal"]
    first_payload = _preference_payload(
        request_id="request-preference-history-1",
        content="我喜欢奇幻冒险小说",
    )
    second_payload = _preference_payload(
        request_id="request-preference-history-2",
        content="我喜欢校园恋爱小说",
    )

    first = handler(first_payload)
    second = handler(second_payload)
    reopened_first = handler.reconcile(first_payload)

    assert first["profile_receipts"][0]["version"] == 1
    assert second["profile_receipts"][0]["version"] == 2
    assert reopened_first["receipt_digest"] == first["receipt_digest"]
    ledger.close()


@pytest.mark.asyncio
async def test_life_conversation_response_loss_reopens_exact_projection_barrier(
    tmp_path,
    monkeypatch,
) -> None:
    db_path, ledger = _ledger(tmp_path, "life-conversation-response-loss.sqlite3")
    life = build_process_life_container(ledger=ledger, adopted_at_ms=1)
    payload = _life_payload(
        request_id="request-life-conversation-response-loss",
        content="把生命投影拆成独立验收效果",
    )
    real_submit = submit_life_command
    calls = 0

    def commit_then_lose(**kwargs):  # noqa: ANN003, ANN202
        nonlocal calls
        calls += 1
        real_submit(**kwargs)
        raise TimeoutError("private Life writer response")

    monkeypatch.setattr(PROCESS_LIFE_PROVIDER, "require", lambda: life)
    monkeypatch.setattr(
        acceptance_effect_handlers,
        "submit_life_command",
        commit_then_lose,
    )
    projection_dispatcher = LifeProjectionDispatcher(
        life.repository,
        _ack_life_projection,
    )
    handler = build_production_acceptance_effect_handlers(
        life_receipt_reader_provider=lambda: life.snapshot_reader,
        life_projection_dispatcher=projection_dispatcher,
    )["life.conversation_turn_recorded"]

    recovered = await handler(payload)

    assert recovered["status"] == "APPLIED"
    assert recovered["command_id"] == payload["command_id"]
    assert recovered["receipt_ref"].startswith(
        "life-projection-barrier://sha256/"
    )
    assert {
        receipt["result"]["target"]
        for receipt in recovered["projection_receipts"]
    } == {"life.experience", "experience"}
    assert calls == 1
    receipt_ref = recovered["receipt_ref"]
    ledger.close()

    restarted_reader = LifeSnapshotReader(LifeCommandRepository(db_path))
    restarted = build_production_acceptance_effect_handlers(
        life_receipt_reader_provider=lambda: restarted_reader,
    )["life.conversation_turn_recorded"]
    exact = restarted.reconcile(payload)

    assert exact["receipt_ref"] == receipt_ref
    assert exact["command_hash"] == recovered["command_hash"]
    assert calls == 1


@pytest.mark.asyncio
async def test_life_projection_barrier_rejects_durable_receipt_tamper(
    tmp_path,
    monkeypatch,
) -> None:
    db_path, ledger = _ledger(tmp_path, "life-projection-tamper.sqlite3")
    life = build_process_life_container(ledger=ledger, adopted_at_ms=1)
    payload = _life_payload(
        request_id="request-life-projection-tamper",
        content="拒绝被篡改的本地投影回执",
    )
    monkeypatch.setattr(PROCESS_LIFE_PROVIDER, "require", lambda: life)
    handler = build_production_acceptance_effect_handlers(
        life_receipt_reader_provider=lambda: life.snapshot_reader,
        life_projection_dispatcher=LifeProjectionDispatcher(
            life.repository,
            _ack_life_projection,
        ),
    )["life.conversation_turn_recorded"]
    committed = await handler(payload)
    intent_id = committed["projection_receipts"][0]["intent_id"]

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER life_projection_receipt_no_update")
        conn.execute(
            """
            UPDATE life_projection_receipt
            SET result_json = ?
            WHERE intent_id = ?
            """,
            (
                json.dumps(
                    {
                        "status": "PROJECTED_FROM_LIFE_JOURNAL",
                        "target": "experience",
                        "intent_id": "tampered-intent",
                    },
                    separators=(",", ":"),
                ),
                intent_id,
            ),
        )
        conn.commit()

    with pytest.raises(AcceptanceEffectOutcomeUnknown):
        handler.reconcile(payload)
    ledger.close()
