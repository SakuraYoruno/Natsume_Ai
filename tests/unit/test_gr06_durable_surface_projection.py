from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.postprocess import _async_post_process
from host_brain.app import acceptance_effect_handlers
from host_brain.composition.life import PROCESS_LIFE_PROVIDER
from host_brain.observability import runtime_audit
from natsume_contracts.life import LifeCommand


class _MemoryService:
    def __init__(self, receipt: dict) -> None:
        self.receipt = dict(receipt)
        self.calls = 0

    def admit_chat(self, _candidate):  # noqa: ANN001
        self.calls += 1
        return dict(self.receipt)


def _ports(
    *,
    assistant_result: dict,
    memory_receipt: dict,
    user_result: dict | None = None,
) -> SimpleNamespace:
    memory = _MemoryService(memory_receipt)

    async def persist_post_process_audit(**_kwargs):  # noqa: ANN003
        return None

    return SimpleNamespace(
        record_user_turn=lambda **_kwargs: dict(user_result or {"status": "OK"}),
        record_assistant_turn=lambda **_kwargs: dict(assistant_result),
        persist_post_process_audit=persist_post_process_audit,
        life_memory_service=lambda: memory,
        memory=memory,
    )


async def _run(
    ports: SimpleNamespace,
    *,
    user_message: str = "accepted user turn",
) -> None:
    await _async_post_process(
        application_ports=ports,
        request_id="request-gr06-durable-projection",
        person_id="person-gr06-durable-projection",
        session_id="session-gr06-durable-projection",
        user_message=user_message,
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
async def test_durable_projection_rejects_soft_failed_user_turn_before_assistant() -> None:
    assistant_calls = 0
    ports = _ports(
        user_result={
            "status": "DEGRADED",
            "reason_code": "SOFT_FAIL_USER_TURN",
        },
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
        },
    )

    def record_assistant(**_kwargs):  # noqa: ANN003, ANN202
        nonlocal assistant_calls
        assistant_calls += 1
        return {"status": "OK"}

    ports.record_assistant_turn = record_assistant
    with pytest.raises(RuntimeError, match="SOFT_FAIL_USER_TURN"):
        await _run(ports)

    assert assistant_calls == 0
    assert ports.memory.calls == 0


@pytest.mark.asyncio
async def test_durable_projection_accepts_idempotent_replays() -> None:
    ports = _ports(
        user_result={
            "status": "SKIPPED",
            "reason_code": "DUPLICATE_VISIBLE_TURN",
        },
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
async def test_durable_projection_records_user_before_assistant() -> None:
    calls: list[tuple[str, dict]] = []
    ports = _ports(
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
        },
    )

    def record_user(**kwargs):  # noqa: ANN003, ANN202
        calls.append(("user", dict(kwargs)))
        return {"status": "OK"}

    def record_assistant(**kwargs):  # noqa: ANN003, ANN202
        calls.append(("assistant", dict(kwargs)))
        return {"status": "OK"}

    ports.record_user_turn = record_user
    ports.record_assistant_turn = record_assistant
    await _run(ports)

    assert [role for role, _payload in calls] == ["user", "assistant"]
    assert calls[0][1]["content"] == "accepted user turn"
    assert calls[0][1]["ts_ms"] == 100
    assert calls[0][1]["metadata"]["source"] == "surface_post_process"
    assert calls[1][1]["content"] == "accepted assistant turn"


@pytest.mark.asyncio
async def test_durable_projection_keeps_empty_multimodal_turn_on_existing_path() -> None:
    user_calls = 0
    ports = _ports(
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
        },
    )

    def record_user(**_kwargs):  # noqa: ANN003, ANN202
        nonlocal user_calls
        user_calls += 1
        return {"status": "OK"}

    ports.record_user_turn = record_user
    await _run(ports, user_message="")

    assert user_calls == 0
    assert ports.memory.calls == 1


@pytest.mark.asyncio
async def test_durable_projection_assistant_writer_does_not_block_event_loop() -> None:
    entered = threading.Event()
    release = threading.Event()
    ports = _ports(
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
        },
    )

    def blocking_record_assistant_turn(**_kwargs):  # noqa: ANN003, ANN202
        entered.set()
        if not release.wait(timeout=1.0):
            raise AssertionError("assistant projection blocked the event loop")
        return {"status": "OK"}

    ports.record_assistant_turn = blocking_record_assistant_turn
    task = asyncio.create_task(_run(ports))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        await asyncio.sleep(0.01)
        release.set()
        await task
    finally:
        release.set()


@pytest.mark.asyncio
async def test_durable_projection_user_writer_does_not_block_event_loop() -> None:
    entered = threading.Event()
    release = threading.Event()
    ports = _ports(
        assistant_result={"status": "OK"},
        memory_receipt={
            "status": "COMMITTED",
            "request_id": "request-gr06-durable-projection",
        },
    )

    def blocking_record_user_turn(**_kwargs):  # noqa: ANN003, ANN202
        entered.set()
        if not release.wait(timeout=1.0):
            raise AssertionError("user projection blocked the event loop")
        return {"status": "OK"}

    ports.record_user_turn = blocking_record_user_turn
    task = asyncio.create_task(_run(ports))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        await asyncio.sleep(0.01)
        release.set()
        await task
    finally:
        release.set()


@pytest.mark.asyncio
async def test_diagnostic_trace_persistence_does_not_block_event_loop(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_persist(**_kwargs):  # noqa: ANN003, ANN202
        entered.set()
        if not release.wait(timeout=1.0):
            raise AssertionError("diagnostic trace blocked the event loop")

    monkeypatch.setattr(
        runtime_audit,
        "_persist_post_process_audit_sync",
        blocking_persist,
    )
    task = asyncio.create_task(
        runtime_audit._persist_post_process_audit(request_id="request-audit-offload")
    )
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        await asyncio.sleep(0.01)
        release.set()
        await task
    finally:
        release.set()


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
