from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from host_brain.app.emotion_context import (
    HostEmotionRuntime,
    normalize_chat_emotion_context,
    resolve_chat_emotion_context,
)
from host_brain.app.input_runtime_composition import (
    build_input_runtime_application_ports,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository


NOW = 1_900_000_000_000


def _runtime(tmp_path: Path) -> tuple[AffectFusionService, LifeSnapshotReader, LifeCommandService]:
    database = tmp_path / "dl02-affect-fusion.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="dl02-affect-fusion",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    repository = LifeCommandRepository(database)
    command_service = LifeCommandService(repository, clock=lambda: NOW)
    reader = LifeSnapshotReader(repository)
    return (
        AffectFusionService(
            command_service_provider=lambda: command_service,
            snapshot_reader_provider=lambda: reader,
            clock_ms=lambda: NOW,
        ),
        reader,
        command_service,
    )


def _request(
    *,
    message: str,
    workspace_id: str = "workspace-a",
    person_id: str = "person-a",
    session_id: str = "session-a",
    emotion_context: dict[str, object] | None = None,
    emotion_tag: str | None = None,
    emotion_intensity: float | None = None,
    multimodal_observations: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        workspace_id=workspace_id,
        person_id=person_id,
        session_id=session_id,
        emotion_context=dict(emotion_context or {}),
        emotion_tag=emotion_tag,
        emotion_intensity=emotion_intensity,
        multimodal_observations=dict(multimodal_observations or {}),
        mood_state=None,
        emotion_range_hint=None,
    )


def test_text_distress_is_observed_then_appraised_not_mirrored_into_self(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(message="我真的很难受，什么都不想做。")

    result = fusion.resolve_request(request_body=request, request_id="request-sad")
    state = reader.get_snapshot(result.scope.aggregate_id).state

    assert result.path == "DEEP"
    assert result.observations[0].modality == "TEXT"
    assert result.observations[0].confidence <= 0.4
    assert result.observations[0].emotion_distribution["SAD"] > 0
    # The user can be sad while Natsume's own immediate response is care,
    # not an unexamined copy of the user's label.
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CONCERN",
    }
    assert state["identity"] == {
        "self_id": "natsume_ai",
        "relationship_subject_id": "person-a",
    }


def test_fused_context_does_not_advance_legacy_host_emotion_runtime(tmp_path: Path) -> None:
    fusion, _, _ = _runtime(tmp_path)
    host_runtime = HostEmotionRuntime(clock_ms=lambda: NOW, hour_provider=lambda: 12)

    context = resolve_chat_emotion_context(
        request_body=_request(message="我有点焦虑，脑子停不下来。"),
        runtime=host_runtime,
        affect_fusion_service=fusion,
        request_id="request-anxious",
    )

    assert context["emotion_inference_source"] == "affective_life_fusion"
    assert context["emotion_tag"] == "CALM"
    assert context["affective_life"]["self_affect"]["components"][0]["emotion"] == "CARE"
    # The old runtime is deliberately not used as an alternate self-affect
    # writer by the fused production path.
    assert host_runtime.user_emotion_profile().to_summary()["total_interactions"] == 0


def test_conflicting_modalities_preserve_conflict_and_choose_uncertainty(tmp_path: Path) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(
        message="我没事。",
        emotion_context={
            "user_affect_observations": [
                {
                    "modality": "ACOUSTIC",
                    "emotion_distribution": {"SAD": 0.9},
                    "confidence": 0.82,
                },
                {
                    "modality": "VISUAL",
                    "emotion_distribution": {"HAPPY": 0.9},
                    "confidence": 0.8,
                },
            ]
        },
    )

    result = fusion.resolve_request(request_body=request, request_id="request-conflict")
    state = reader.get_snapshot(result.scope.aggregate_id).state

    assert result.conflict is True
    assert result.path == "DEEP"
    assert result.candidate_count <= 2
    assert result.retry_count <= 1
    assert all(item.conflict_refs for item in result.observations)
    assert {item["emotion"] for item in state["affective"]["components"]} == {
        "CARE",
        "CAUTION",
    }


def test_sensor_ttls_are_bounded_and_raw_sensor_material_is_not_journaled(
    tmp_path: Path,
) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    raw_audio = "private-audio-payload-must-never-persist"
    request = _request(
        message="嗯。",
        emotion_context={
            "user_affect_observations": [
                {
                    "modality": "ACOUSTIC",
                    "emotion_tag": "ANXIOUS",
                    "confidence": 0.74,
                    "raw_audio": raw_audio,
                },
                {
                    "modality": "VISUAL",
                    "emotion_tag": "SAD",
                    "confidence": 0.68,
                    "expires_at_ms": NOW + 99_999,
                },
            ]
        },
    )

    result = fusion.resolve_request(request_body=request, request_id="request-privacy")
    events = reader.list_events(result.scope.aggregate_id, limit=20)

    assert "RAW_MODALITY_OMITTED" in result.reason_codes
    visual = next(item for item in result.observations if item.modality == "VISUAL")
    assert visual.expires_at_ms == NOW + 3_000
    assert raw_audio not in str([event.payload for event in events])
    assert "raw_audio" not in str([event.payload for event in events])


def test_workspace_and_session_scope_prevent_same_subject_leak(tmp_path: Path) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    first = fusion.resolve_request(
        request_body=_request(
            message="今天好开心。",
            workspace_id="workspace-a",
            person_id="same-person",
        ),
        request_id="request-workspace-a",
    )
    second = fusion.resolve_request(
        request_body=_request(
            message="我很难受。",
            workspace_id="workspace-b",
            person_id="same-person",
        ),
        request_id="request-workspace-b",
    )
    third = fusion.resolve_request(
        request_body=_request(
            message="我有点烦。",
            workspace_id="workspace-a",
            person_id="same-person",
            session_id="session-b",
        ),
        request_id="request-session-b",
    )

    assert first.scope.aggregate_id != second.scope.aggregate_id
    assert first.scope.aggregate_id != third.scope.aggregate_id
    first_state = reader.get_snapshot(first.scope.aggregate_id).state
    second_state = reader.get_snapshot(second.scope.aggregate_id).state
    assert first_state["identity"]["relationship_subject_id"] == "same-person"
    assert second_state["identity"]["relationship_subject_id"] == "same-person"
    assert first_state["affective"]["last_observation"]["workspace_id"] == "workspace-a"
    assert second_state["affective"]["last_observation"]["workspace_id"] == "workspace-b"
    assert first_state["affective"]["last_observation"]["session_id"] == "session-a"
    assert reader.get_snapshot(third.scope.aggregate_id).state["affective"]["last_observation"]["session_id"] == "session-b"


def test_duplicate_request_reopens_exact_receipts_without_duplicate_events(tmp_path: Path) -> None:
    fusion, reader, _ = _runtime(tmp_path)
    request = _request(
        message="我现在有点烦。",
        emotion_tag="FRUSTRATED",
        emotion_intensity=0.7,
    )

    first = fusion.resolve_request(request_body=request, request_id="request-exact")
    second = fusion.resolve_request(request_body=request, request_id="request-exact")
    events = reader.list_events(first.scope.aggregate_id, limit=20)

    assert first.scope.aggregate_id == second.scope.aggregate_id
    assert [item.command_id for item in first.receipts] == [item.command_id for item in second.receipts]
    assert len(events) == len(first.receipts)


def test_input_runtime_composition_does_not_resolve_host_emotion_runtime_when_fused(
    tmp_path: Path,
) -> None:
    _, reader, command_service = _runtime(tmp_path)

    def legacy_runtime_must_not_be_resolved() -> HostEmotionRuntime:
        raise AssertionError("canonical fusion must not resolve HostEmotionRuntime")

    providers = {
        "intent_engine": lambda: object(),
        "ledger": lambda: object(),
        "life_snapshot_reader": lambda: reader,
        "multimodal_observer": lambda: object(),
        "multimodal_processor": lambda: object(),
        "emotion_context_runtime": legacy_runtime_must_not_be_resolved,
    }
    host_services = SimpleNamespace(
        require_provider=lambda name: providers[name],
        evolution_activation_service=None,
        evolution_scope=None,
    )
    ports = build_input_runtime_application_ports(
        host_services=host_services,
        life_command_service_provider=lambda: command_service,
    )

    result = ports.resolve_chat_emotion_context(
        request_body=_request(message="我真的很难受。"),
        request_id="request-composition",
    )

    assert result["emotion_inference_source"] == "affective_life_fusion"
    assert result["affective_life"]["workspace_id"] == "workspace-a"


def test_external_affective_life_payload_cannot_spoof_canonical_self_affect() -> None:
    normalized = normalize_chat_emotion_context(
        {
            "_affective_life_internal": False,
            "affective_life": {
                "self_affect": {"components": [{"emotion": "HURT", "intensity": 1.0}]}
            },
            "emotion_tag": "SAD",
        }
    )

    assert "affective_life" not in normalized
