from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.postprocess import _async_post_process
from host_brain.app import acceptance_effect_handlers
from host_brain.composition.life import PROCESS_LIFE_PROVIDER
from natsume_contracts.life import LifeCommand


class _MemoryService:
    def __init__(self, receipt: dict) -> None:
        self.receipt = dict(receipt)
        self.calls = 0

    def admit_chat(self, _candidate):  # noqa: ANN001
        self.calls += 1
        return dict(self.receipt)


def _ports(*, assistant_result: dict, memory_receipt: dict) -> SimpleNamespace:
    memory = _MemoryService(memory_receipt)

    async def persist_post_process_audit(**_kwargs):  # noqa: ANN003
        return None

    return SimpleNamespace(
        record_assistant_turn=lambda **_kwargs: dict(assistant_result),
        persist_post_process_audit=persist_post_process_audit,
        life_memory_service=lambda: memory,
        memory=memory,
    )


async def _run(ports: SimpleNamespace) -> None:
    await _async_post_process(
        application_ports=ports,
        request_id="request-gr06-durable-projection",
        person_id="person-gr06-durable-projection",
        session_id="session-gr06-durable-projection",
        user_message="accepted user turn",
        narrative="accepted assistant turn",
        emotion_context={"emotion_tag": "NEUTRAL"},
        bond_summary={},
        ts_ms=100,
        render_target_lang="zh",
        trace_stage_meta={
            "life_projection_mode": True,
            "surface_main_chain_used": True,
            "skip_heavy_persona_postprocess": True,
        },
    )


@pytest.mark.asyncio
async def test_durable_projection_rejects_soft_failed_assistant_turn() -> None:
    ports = _ports(
        assistant_result={
            "status": "DEGRADED",
            "reason_code": "SOFT_FAIL_ASSISTANT_TURN",
        },
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
        },
    )

    with pytest.raises(RuntimeError, match="SOFT_FAIL_ASSISTANT_TURN"):
        await _run(ports)

    assert ports.memory.calls == 0


@pytest.mark.asyncio
async def test_durable_projection_accepts_idempotent_replays() -> None:
    ports = _ports(
        assistant_result={
            "status": "SKIPPED",
            "reason_code": "DUPLICATE_CONVERSATION_TURN",
        },
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
            "replayed": True,
        },
    )

    await _run(ports)

    assert ports.memory.calls == 1


@pytest.mark.asyncio
async def test_durable_projection_rejects_missing_memory_receipt() -> None:
    ports = _ports(
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "DEGRADED",
            "reason_code": "MEMORY_STORE_UNAVAILABLE",
            "request_id": "request-gr06-durable-projection",
        },
    )

    with pytest.raises(RuntimeError, match="MEMORY_STORE_UNAVAILABLE"):
        await _run(ports)

    assert ports.memory.calls == 1


@pytest.mark.asyncio
async def test_post_process_rejects_legacy_direct_mode_before_any_write() -> None:
    ports = _ports(
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-direct-retired",
        },
    )

    with pytest.raises(RuntimeError, match="LEGACY_DIRECT_POST_PROCESS_RETIRED"):
        await _async_post_process(
            application_ports=ports,
            request_id="request-gr06-direct-retired",
            person_id="person-gr06-direct-retired",
            session_id="session-gr06-direct-retired",
            user_message="legacy direct input",
            narrative="legacy direct output",
            emotion_context={"emotion_tag": "HAPPY"},
            bond_summary={},
            ts_ms=100,
            render_target_lang="zh",
            trace_stage_meta={},
        )

    assert ports.memory.calls == 0


def test_durable_projection_has_no_legacy_conversation_experience_writer() -> None:
    import inspect

    from host_brain.agent_runtime import postprocess

    source = inspect.getsource(postprocess._async_post_process)
    assert "get_conversation_experience_store" not in source
    assert "update_assistant_reply" not in source
    assert "apply_surface_emotion_bond_flow" not in source
    assert "post_process_bond_update" not in source
    assert "drive_stream_vts_emotion" not in source
    assert "submit_voice_command" not in source
    assert "run_persona_drift_cycle" not in source
    assert "run_growth_feedback_cycle" not in source


def test_post_process_has_no_legacy_bond_writer_path() -> None:
    import inspect

    from host_brain.agent_runtime import postprocess

    source = inspect.getsource(postprocess._async_post_process)
    assert "apply_surface_emotion_bond_flow(" not in source
    assert "post_process_bond_update(" not in source
    assert "second authority and duplicate effects" in source


def test_post_process_has_no_legacy_tribe_or_direct_embodiment_path() -> None:
    import inspect

    from host_brain.agent_runtime import postprocess

    source = inspect.getsource(postprocess._async_post_process)
    assert "emit_conversation_ended(" not in source
    assert "drive_stream_vts_emotion(" not in source
    assert "submit_voice_command(" not in source
    assert "second authority and duplicate effects" in source


def test_life_acceptance_handler_requires_applied_receipt(monkeypatch) -> None:
    monkeypatch.setattr(
        PROCESS_LIFE_PROVIDER,
        "require",
        lambda: SimpleNamespace(command_service=object(), snapshot_reader=object()),
    )
    monkeypatch.setattr(
        acceptance_effect_handlers,
        "submit_life_command",
        lambda **_kwargs: {
            "status": "CONFLICT",
            "reason_code": "LIFE_COMMAND_VERSION_CONFLICT",
        },
    )

    with pytest.raises(RuntimeError, match="LIFE_COMMAND_VERSION_CONFLICT"):
        acceptance_effect_handlers._submit_accepted_life_foreground(
            {
                "command_type": "foreground_completed",
                "person_id": "person-gr06-durable-projection",
                "command_id": "life::foreground_completed::request-gr06",
                "correlation_id": "request-gr06",
                "payload": {},
                "created_at_ms": 100,
                "metadata": {},
            }
        )


def test_life_acceptance_handler_accepts_applied_replay(monkeypatch) -> None:
    command = LifeCommand(
        command_id="life::foreground_completed::request-gr06",
        person_id="person-gr06-durable-projection",
        command_type="foreground_completed",
        payload={},
        expected_version=0,
        created_at_ms=100,
        correlation_id="request-gr06",
        metadata={},
    )
    monkeypatch.setattr(
        PROCESS_LIFE_PROVIDER,
        "require",
        lambda: SimpleNamespace(command_service=object(), snapshot_reader=object()),
    )
    monkeypatch.setattr(
        acceptance_effect_handlers,
        "submit_life_command",
        lambda **_kwargs: {
            "command_id": command.command_id,
            "command_hash": command.command_hash,
            "person_id": command.person_id,
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_gr06_durable_projection",
            "state_version": 1,
            "state_digest": "sha256:" + "a" * 64,
            "outbox_count": 0,
            "created_at_ms": 100,
            "replayed": True,
        },
    )

    receipt = acceptance_effect_handlers._submit_accepted_life_foreground(
        {
            "command_type": "foreground_completed",
            "person_id": "person-gr06-durable-projection",
            "command_id": "life::foreground_completed::request-gr06",
            "correlation_id": "request-gr06",
            "payload": {},
            "created_at_ms": 100,
            "metadata": {},
        }
    )

    assert receipt["status"] == "APPLIED"
    assert receipt["replayed"] is True
    assert (
        receipt["receipt_ref"]
        == "life-event://id/life_evt_gr06_durable_projection"
    )
