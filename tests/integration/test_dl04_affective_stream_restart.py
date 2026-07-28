from __future__ import annotations

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
)


NOW = 1_900_000_000_000


def _agent_input() -> SimpleNamespace:
    return SimpleNamespace(
        request_id="dl04-restart-request",
        workspace_id="workspace-dl04",
        person_id="person-dl04",
        session_id="session-dl04",
        user_message="我今天真的有点撑不住。",
        emotion_context={},
        emotion_snapshot={},
        bond_summary={},
        retrieval_summary={"status": "SKIPPED"},
        render_target_lang="zh",
        attachments=[],
        multimodal_observations={},
    )


def _plan_trace() -> dict[str, object]:
    context = {
        "_affective_life_internal": True,
        "affective_life": {
            "schema_version": "affective_turn_context.v1",
            "self_id": "natsume_ai",
            "workspace_id": "workspace-dl04",
            "relationship_subject_id": "person-dl04",
            "session_id": "session-dl04",
            "self_affect": {
                "self_id": "natsume_ai",
                "relationship_subject_id": "person-dl04",
                "revision": 17,
                "components": [{"emotion": "CARE", "intensity": 0.72}],
                "last_regulation": {"regulation_id": "reg-dl04", "mode": "EXPRESS"},
            },
            "user_observations": [
                {
                    "modality": "TEXT",
                    "emotion_distribution": {"SAD": 0.9},
                    "valence": -0.8,
                    "confidence": 0.7,
                }
            ],
            "relationship_life": {
                "state": {
                    "relationship_subject_id": "person-dl04",
                    "revision": 2,
                    "repair_required": False,
                    "proactive_permission": "UNKNOWN",
                },
                "aftereffect": {"outcome": "UNKNOWN"},
            },
        },
    }
    guidance = build_affective_turn_guidance(
        emotion_context=context,
        request_id="dl04-restart-request",
        workspace_id="workspace-dl04",
        relationship_subject_id="person-dl04",
        session_id="session-dl04",
        created_at_ms=NOW,
    )
    assert guidance is not None
    return {"affective_turn_plan": guidance.plan.as_dict()}


def test_affective_stream_projection_replays_without_exposing_sentence_text(
    tmp_path: Path,
) -> None:
    database = tmp_path / "dl04-affective-stream.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl04-affective-stream",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    agent = _agent_input()
    trace = _plan_trace()

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
        sentence="先别硬撑，靠我这边缓一缓也行。",
        sentence_index=0,
    )
    foreground = _submit_surface_foreground_completed(
        life_command_service=service,
        life_snapshot_reader=reader,
        relationship_lifecycle_service=None,
        agent_input=agent,
        result=None,
        envelope=ReplyEnvelope.final_reply(
            text="先别硬撑，靠我这边缓一缓也行。",
            response_form="text",
            source="test",
        ),
        narrative="先别硬撑，靠我这边缓一缓也行。",
        trace_stage_meta=trace,
        timings_ms={"surface_main_chain_ms": 12, "llm_latency_ms": 8},
        total_latency_ms=12,
        llm_latency_ms=8,
        stream_summary={
            "mode": "native_sentence_stream",
            "sentence_count": 1,
            "plan_id": trace["affective_turn_plan"]["plan_id"],
            "synchronization_id": trace["affective_turn_plan"]["expression_plan"]["synchronization_id"],
            "self_affect_revision": 17,
            "first_token_ms": 8,
        },
        first_token_ms=8,
        buffer_stream=False,
    )

    assert planned["status"] == sentence["status"] == foreground["status"] == "APPLIED"
    rows = reader.list_affective_turn_delivery_event_heads("person-dl04", limit=10)
    assert {row["delivery_kind"] for row in rows} == {"PLAN", "SENTENCE", "FOREGROUND"}
    assert all("缓一缓" not in str(row) for row in rows)
    assert all(row["self_id"] == "natsume_ai" for row in rows)
    assert len({row["plan_id"] for row in rows}) == 1
    assert len({row["synchronization_id"] for row in rows}) == 1

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE affective_turn_delivery_event_v1 SET plan_id = 'tampered'"
            )

    before = reader.get_snapshot("person-dl04")
    restarted = LifeCommandService(LifeCommandRepository(database), clock=lambda: NOW)
    replayed = restarted.replay("person-dl04")
    assert replayed.state_digest == before.state_digest
