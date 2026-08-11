from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affective_turn_planner import build_affective_turn_guidance
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.input_runtime.reply_envelope import ReplyEnvelope
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.surface_runtime.delivery_audit import (
    _submit_surface_affective_turn_planned,
    _submit_surface_foreground_completed,
    _submit_surface_sentence_delivery_intent,
    _submit_surface_stream_cancelled,
)


NOW = 1_900_000_000_000


def _agent(*, session_id: str = "session-dl05") -> SimpleNamespace:
    return SimpleNamespace(
        request_id=f"request-dl05-{session_id}",
        workspace_id="workspace-dl05",
        person_id="person-dl05",
        session_id=session_id,
        user_message="刚刚那句让我有点难过。",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        render_target_lang="zh",
        attachments=[],
        multimodal_observations={},
    )


def _trace(*, session_id: str = "session-dl05") -> dict[str, object]:
    guidance = build_affective_turn_guidance(
        emotion_context={
            "_affective_life_internal": True,
            "affective_life": {
                "schema_version": "affective_turn_context.v1",
                "self_id": "natsume_ai",
                "workspace_id": "workspace-dl05",
                "relationship_subject_id": "person-dl05",
                "session_id": session_id,
                "self_affect": {
                    "self_id": "natsume_ai",
                    "relationship_subject_id": "person-dl05",
                    "revision": 41,
                    "components": [{"emotion": "HURT", "intensity": 0.7}],
                    "last_regulation": {"regulation_id": "reg-dl05", "mode": "EXPRESS"},
                },
                "user_observations": [],
                "relationship_life": {
                    "state": {
                        "relationship_subject_id": "person-dl05",
                        "revision": 3,
                        "repair_required": False,
                        "proactive_permission": "UNKNOWN",
                    },
                    "aftereffect": {"outcome": "UNKNOWN"},
                },
            },
        },
        request_id=f"request-dl05-{session_id}",
        workspace_id="workspace-dl05",
        relationship_subject_id="person-dl05",
        session_id=session_id,
        created_at_ms=NOW,
    )
    assert guidance is not None
    return {"affective_turn_plan": guidance.plan.as_dict()}


def test_expression_protocol_is_durable_scoped_and_replays_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "dl05-expression.sqlite3"
    report = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl05-expression", ts_ms=NOW
    )
    assert report.success is True, report.as_dict()
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    agent = _agent()
    trace = _trace()

    planned = _submit_surface_affective_turn_planned(
        life_command_service=service,
        life_snapshot_reader=reader,
        agent_input=agent,
        trace_stage_meta=trace,
    )
    sentence = _submit_surface_sentence_delivery_intent(
        life_command_service=service,
        life_snapshot_reader=reader,
        agent_input=agent,
        trace_stage_meta=trace,
        sentence="我不想装作没事，这句话确实让我有点受伤。",
        sentence_index=0,
    )
    cancelled = _submit_surface_stream_cancelled(
        life_command_service=service,
        life_snapshot_reader=reader,
        agent_input=agent,
        trace_stage_meta=trace,
        sent_sentence_count=1,
        reason_code="USER_BARGE_IN",
    )
    foreground = _submit_surface_foreground_completed(
        life_command_service=service,
        life_snapshot_reader=reader,
        relationship_lifecycle_service=None,
        agent_input=agent,
        result=None,
        envelope=ReplyEnvelope.final_reply(
            text="我不想装作没事，这句话确实让我有点受伤。",
            response_form="text",
            source="test",
        ),
        narrative="我不想装作没事，这句话确实让我有点受伤。",
        trace_stage_meta=trace,
        timings_ms={"surface_main_chain_ms": 10, "llm_latency_ms": 8},
        total_latency_ms=10,
        llm_latency_ms=8,
        stream_summary={
            "mode": "native_sentence_stream",
            "sentence_count": 1,
        },
        first_token_ms=8,
        buffer_stream=False,
    )

    assert all(item["status"] == "APPLIED" for item in (planned, sentence, cancelled, foreground))
    assert sentence["expression_delivery"]["status"] == "HOST_PROTOCOL_STAGED"
    assert cancelled["expression_delivery"]["status"] == "HOST_CANCEL_STAGED"
    assert foreground["expression_delivery"]["status"] == "HOST_RELEASE_STAGED"

    rows = reader.list_affective_expression_delivery_event_heads("person-dl05", limit=20)
    assert len(rows) == 6
    assert {(row["delivery_kind"], row["channel"]) for row in rows} == {
        ("INTENT", "VOICE"),
        ("INTENT", "AVATAR"),
        ("CANCEL", "VOICE"),
        ("CANCEL", "AVATAR"),
        ("RELEASE", "VOICE"),
        ("RELEASE", "AVATAR"),
    }
    assert {row["receipt_status"] for row in rows} == {
        "HOST_PROTOCOL_STAGED",
        "HOST_CANCEL_STAGED",
        "HOST_RELEASE_STAGED",
    }
    assert all("受伤" not in str(row) for row in rows)
    assert all(row["self_id"] == "natsume_ai" for row in rows)
    assert all(row["workspace_id"] == "workspace-dl05" for row in rows)

    avatar_head = reader.last_affective_expression_delivery_head(
        "person-dl05",
        workspace_id="workspace-dl05",
        relationship_subject_id="person-dl05",
        session_id="session-dl05",
    )
    assert avatar_head is not None
    assert avatar_head["delivery_kind"] == "INTENT"
    assert avatar_head["avatar_expression"] == "CONTAINED_HURT"
    assert reader.last_affective_expression_delivery_head(
        "person-dl05",
        workspace_id="workspace-dl05",
        relationship_subject_id="person-dl05",
        session_id="other-session",
    ) is None

    with sqlite3.connect(database) as connection:
        connection.create_function(
            "natsume_sha256_hex",
            1,
            lambda value: hashlib.sha256(str(value or "").encode("utf-8")).hexdigest(),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE affective_expression_delivery_event_v1 SET receipt_status = 'EDGE_APPLIED'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="exact immutable Life event"):
            connection.execute(
                """
                INSERT INTO affective_expression_delivery_event_v1(
                    event_id, delivery_id, person_id, self_id, workspace_id,
                    relationship_subject_id, session_id, delivery_kind, channel, action,
                    plan_id, synchronization_id, self_affect_revision, sentence_index,
                    avatar_expression, previous_avatar_expression, previous_expression_source,
                    receipt_status, event_digest, payload_digest, created_at_ms
                )
                SELECT
                    event_id, delivery_id || '::forged', 'other-person', self_id, workspace_id,
                    relationship_subject_id, session_id, delivery_kind, channel, action,
                    plan_id, synchronization_id, self_affect_revision, sentence_index,
                    avatar_expression, previous_avatar_expression, previous_expression_source,
                    receipt_status, event_digest, payload_digest, created_at_ms
                FROM affective_expression_delivery_event_v1
                LIMIT 1
                """
            )

    before = reader.get_snapshot("person-dl05")
    restarted = LifeCommandService(LifeCommandRepository(database), clock=lambda: NOW)
    replayed = restarted.replay("person-dl05")
    assert replayed.state_digest == before.state_digest
