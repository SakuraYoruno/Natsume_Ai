from __future__ import annotations

import asyncio
import copy
import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.app import digital_life_routes
from host_brain.composition.life import PROCESS_LIFE_PROVIDER, ProcessLifeContainer
from host_brain.digital_life.application_boundary import (
    project_inner_life_snapshot,
    project_life_director_pack,
    project_surface_organ_context,
    submit_life_command,
)
from host_brain.app.reply_envelope import ReplyEnvelope
from host_brain.app import surface_main_chain_adapter
from host_brain.surface_runtime import delivery_audit
from natsume_contracts.life import LifeCommand
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.orchestration.governor_grant import (
    canonical_proactive_intent_args,
    governor_grant_ref,
)
from host_brain.persistence.life_command_repository import (
    LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
    LifeCommandRepositoryError,
)


class _FakeReader:
    def __init__(
        self,
        snapshot: dict[str, Any],
        *,
        receipt: dict[str, Any] | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.receipt = receipt
        self.events = list(events or [])
        self.snapshot_calls: list[str] = []
        self.receipt_calls: list[str] = []

    def get_snapshot(self, person_id: str) -> dict[str, Any]:
        self.snapshot_calls.append(person_id)
        return copy.deepcopy(self.snapshot)

    def get_receipt(self, command_id: str) -> dict[str, Any] | None:
        self.receipt_calls.append(command_id)
        return copy.deepcopy(self.receipt)

    def list_events(
        self,
        person_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        del person_id
        return [
            copy.deepcopy(row)
            for row in self.events
            if int(row.get("sequence") or 0) > after_sequence
        ][:limit]


class _FakeService:
    def __init__(self) -> None:
        self.commands: list[Any] = []

    def submit(self, command: Any) -> dict[str, Any]:
        self.commands.append(command)
        return {
            "command_id": command.command_id,
            "command_hash": command.command_hash,
            "person_id": command.person_id,
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_unit",
            "state_version": command.expected_version + 1,
            "state_digest": "digest-after",
            "outbox_count": len(list(command.payload.get("deliveries") or [])),
            "created_at_ms": command.created_at_ms,
            "replayed": False,
        }


class _FakeNextService(_FakeService):
    def __init__(self, *, version: int) -> None:
        super().__init__()
        self.version = version
        self.submit_next_calls: list[str] = []

    def submit_next(self, person_id: str, command_factory) -> dict[str, Any]:  # noqa: ANN001
        self.submit_next_calls.append(person_id)
        command = command_factory(SimpleNamespace(version=self.version))
        return self.submit(command)


def _snapshot(*, version: int = 4) -> dict[str, Any]:
    return {
        "person_id": "person-aw04",
        "version": version,
        "state_digest": "digest-before",
        "last_event_id": "life_evt_before" if version else None,
        "last_event_digest": "event-digest-before" if version else None,
        "updated_at_ms": 40 if version else 0,
        "state": {
            "person_id": "person-aw04",
            "version": version,
            "runtime": {"alive_status": "alive", "last_integrated_ms": 30},
            "affect": {"mood": "warm", "energy": 0.7, "tension": 0.2},
            "needs": {"contact": 0.4, "rest": 0.1},
            "life_pressure": {"relationship": 0.6, "work": 0.0},
            "inner_world": {
                "attention_target": "user_contact",
                "current_preoccupation": "承接当前对话",
                "dialogue_bias": "自然承接",
            },
            "embodiment": {"emotion_tag": "HAPPY", "voice_tone": "warm"},
            "organ_runtime": {"organ_count": 2, "top_organs": [{"organ_id": "limbic"}]},
        },
    }


@pytest.fixture
def composed_life() -> ProcessLifeContainer:
    container = ProcessLifeContainer(
        repository=object(),
        command_service=object(),
        snapshot_reader=object(),
    )
    PROCESS_LIFE_PROVIDER.clear()
    PROCESS_LIFE_PROVIDER.install(container)
    try:
        yield container
    finally:
        PROCESS_LIFE_PROVIDER.clear(container)


def test_snapshot_projections_are_pure_and_legacy_compatible() -> None:
    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)

    inner = project_inner_life_snapshot(snapshot)
    director = project_life_director_pack(snapshot)
    organ = project_surface_organ_context(snapshot)

    assert snapshot == before
    assert inner["attention"]["target"] == "user_contact"
    assert director["state_version"] == 4
    assert director["scene"] == "idle"
    assert organ["tribe_context_source"] == "life_snapshot_reader"
    assert organ["life_snapshot"]["version"] == 4


def test_submit_uses_snapshot_version_and_one_service_call() -> None:
    reader = _FakeReader(_snapshot(version=6))
    service = _FakeService()

    receipt = submit_life_command(
        command_type="tick",
        person_id="person-aw04",
        command_id="life::tick::unit",
        payload={"state_patch": {"runtime": {"alive_status": "alive"}}},
        created_at_ms=100,
        reader=reader,
        service=service,
    )

    assert receipt["status"] == "APPLIED"
    assert reader.snapshot_calls == ["person-aw04"]
    assert len(service.commands) == 1
    assert service.commands[0].expected_version == 6
    assert service.commands[0].command_type == "tick"


def test_submit_next_serializes_version_selection_without_snapshot_read() -> None:
    reader = _FakeReader(_snapshot(version=99))
    service = _FakeNextService(version=8)

    receipt = submit_life_command(
        command_type="advance",
        person_id="person-aw04",
        command_id="life::advance::serialized",
        payload={},
        created_at_ms=101,
        reader=reader,
        service=service,
    )

    assert receipt["status"] == "APPLIED"
    assert reader.receipt_calls == ["life::advance::serialized"]
    assert reader.snapshot_calls == []
    assert service.submit_next_calls == ["person-aw04"]
    assert service.commands[0].expected_version == 8


def test_explicit_expected_version_preserves_occ_conflict_receipt() -> None:
    reader = _FakeReader(_snapshot(version=6))

    class ConflictService(_FakeNextService):
        def submit(self, command: Any) -> dict[str, Any]:
            self.commands.append(command)
            return {
                "command_id": command.command_id,
                "command_hash": command.command_hash,
                "person_id": command.person_id,
                "status": "CONFLICT",
                "reason_code": "LIFE_COMMAND_VERSION_CONFLICT",
                "event_id": None,
                "state_version": 6,
                "state_digest": "digest-current",
                "outbox_count": 0,
                "created_at_ms": command.created_at_ms,
            }

    service = ConflictService(version=99)

    receipt = submit_life_command(
        command_type="advance",
        person_id="person-aw04",
        command_id="life::advance::stale-explicit",
        payload={},
        expected_version=2,
        created_at_ms=1,
        reader=reader,
        service=service,
    )

    assert receipt["status"] == "CONFLICT"
    assert receipt["reason_code"] == "LIFE_COMMAND_VERSION_CONFLICT"
    assert service.submit_next_calls == []
    assert service.commands[0].expected_version == 2
    assert service.commands[0].created_at_ms == _snapshot(version=6)["updated_at_ms"]


def test_deterministic_retry_returns_receipt_before_reading_latest_snapshot() -> None:
    replay_payload = {"assistant_reply": "same retry"}
    durable_command = LifeCommand(
        command_id="life::foreground_completed::req-retry",
        person_id="person-aw04",
        command_type="foreground_completed",
        payload=replay_payload,
        expected_version=8,
        created_at_ms=90,
    )
    existing = {
        "command_id": durable_command.command_id,
        "command_hash": durable_command.command_hash,
        "person_id": "person-aw04",
        "status": "APPLIED",
        "reason_code": None,
        "event_id": "life_evt_existing",
        "state_version": 9,
        "state_digest": "digest-existing",
        "outbox_count": 1,
        "created_at_ms": 90,
        "replayed": False,
    }
    reader = _FakeReader(_snapshot(version=99), receipt=existing)
    service = _FakeService()

    receipt = submit_life_command(
        command_type="foreground_completed",
        person_id="person-aw04",
        command_id=existing["command_id"],
        payload=replay_payload,
        reader=reader,
        service=service,
    )

    assert receipt["event_id"] == "life_evt_existing"
    assert receipt["replayed"] is True
    assert reader.receipt_calls == [existing["command_id"]]
    assert reader.snapshot_calls == []
    assert service.commands == []


def test_deterministic_retry_with_changed_payload_fails_closed() -> None:
    durable = LifeCommand(
        command_id="life::foreground_completed::req-conflict",
        person_id="person-aw04",
        command_type="foreground_completed",
        payload={"assistant_reply": "original"},
        expected_version=4,
        created_at_ms=90,
    )
    reader = _FakeReader(
        _snapshot(version=99),
        receipt={
            "command_id": durable.command_id,
            "command_hash": durable.command_hash,
            "person_id": durable.person_id,
            "status": "APPLIED",
            "state_version": 5,
            "state_digest": "digest",
            "outbox_count": 0,
            "created_at_ms": 90,
        },
    )

    with pytest.raises(LifeCommandRepositoryError) as exc_info:
        submit_life_command(
            command_type="foreground_completed",
            person_id="person-aw04",
            command_id=durable.command_id,
            payload={"assistant_reply": "changed"},
            reader=reader,
            service=_FakeService(),
        )

    assert exc_info.value.reason_code == LIFE_COMMAND_IDEMPOTENCY_CONFLICT


@pytest.mark.asyncio
async def test_digital_life_get_is_pure_and_post_submits_command(
    monkeypatch,
    composed_life,
) -> None:  # noqa: ANN001
    del composed_life
    snapshot = _snapshot(version=3)
    submit_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        digital_life_routes,
        "read_life_snapshot",
        lambda _person_id, **_: copy.deepcopy(snapshot),
    )
    monkeypatch.setattr(digital_life_routes, "read_life_events", lambda *_args, **_kwargs: [])

    def _submit(**kwargs):  # noqa: ANN003
        submit_calls.append(kwargs)
        return {
            "command_id": "life::tick::http",
            "person_id": "person-aw04",
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_http",
            "state_version": 4,
            "state_digest": "digest-http",
            "outbox_count": 0,
            "replayed": False,
        }

    monkeypatch.setattr(digital_life_routes, "submit_life_command", _submit)

    before = copy.deepcopy(snapshot)
    state = await digital_life_routes.digital_life_state("person-aw04")
    inner = await digital_life_routes.digital_life_inner_drive_current("person-aw04", force=True)
    assert submit_calls == []
    assert snapshot == before
    assert state["version"] == 3
    assert inner["state_version_before"] == inner["state_version_after"] == 3
    assert inner["compatibility"]["query_advances_state"] is False

    receipt = await digital_life_routes.digital_life_tick(
        {
            "person_id": "person-aw04",
            "command_id": "http",
            "created_at_ms": 1,
            "state_patch": {"relationship": {"trust": 1.0}},
            "projection_intents": [{"target": "forged"}],
            "organ_intents": [{"target": "forged"}],
            "deliveries": [{"destination": "edge.proactive"}],
        }
    )
    assert receipt["status"] == "APPLIED"
    assert len(submit_calls) == 1
    assert submit_calls[0]["command_type"] == "tick"
    assert "state_patch" not in submit_calls[0]["payload"]
    assert submit_calls[0]["payload"]["projection_intents"] == []
    assert submit_calls[0]["payload"]["organ_intents"] == []
    assert submit_calls[0]["payload"]["deliveries"] == []
    assert submit_calls[0]["created_at_ms"] != 1

    proactive = await digital_life_routes.digital_life_proactive_attempt(
        {
            "person_id": "person-aw04",
            "request_id": "http-no-grant",
            "platform": "qq",
            "conversation_id": "chat-1",
            "content": "unauthorized",
        }
    )
    assert proactive["status"] == "APPLIED"
    assert submit_calls[-1]["payload"]["deliveries"] == []

    source = inspect.getsource(digital_life_routes._submit_route_command)
    assert source.index("await asyncio.to_thread(\n        _command_payload") < source.index(
        "await asyncio.to_thread(\n        submit_life_command"
    )


@pytest.mark.asyncio
async def test_http_proactive_rebuilds_authority_from_durable_grant(
    monkeypatch,
    composed_life,
) -> None:
    del composed_life
    request_id = "http-proactive-authorized"
    intent_args = canonical_proactive_intent_args(
        person_id="person-aw04",
        platform="qq",
        conversation_id="chat-1",
        content="authorized",
        source_kind="manual_proactive_attempt",
    )
    token_ref = "approval::intent-http-proactive"
    binding = build_governed_action_binding(
        intent_envelope={
            "schema_version": "Natsume.intent.v1",
            "id": "intent-http-proactive",
            "session_id": "session-http-proactive",
            "seq": 1,
            "type": "COMMAND",
            "action": "proactive_attempt",
            "target": "proactive.gateway",
            "args": intent_args,
            "nonce": "nonce-http-proactive",
            "person_id": "person-aw04",
        },
        approval_token_ref=token_ref,
        risk_level=3,
        issued_at_ms=123,
        expires_at_ms=4_000_000_000_000,
    )
    decision = {
        "decision": "APPROVE",
        "intent_id": "intent-http-proactive",
        "tokens": {"approval_token_ref": token_ref},
        "approved_action_binding": binding,
        "ts_ms": 123,
    }
    grant_ref = governor_grant_ref(request_id, decision)

    class Ledger:
        def trace_request(self, traced_request_id: str) -> dict[str, Any]:
            assert traced_request_id == request_id
            return {"governor_log": [decision]}

    calls: list[dict[str, Any]] = []

    def submit(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return {
            "status": "APPLIED",
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "state_version": 1,
            "outbox_count": len(kwargs["payload"]["deliveries"]),
        }

    monkeypatch.setattr(digital_life_routes, "get_ledger", lambda: Ledger())
    monkeypatch.setattr(digital_life_routes, "submit_life_command", submit)

    response = await digital_life_routes.digital_life_proactive_attempt(
        {
            "person_id": "person-aw04",
            "request_id": request_id,
            "platform": "qq",
            "conversation_id": "chat-1",
            "content": "authorized",
            "delivery_authorization": {
                "request_id": request_id,
                "governor_grant_ref": grant_ref,
                "authority_kind": "surface_governor",
                "authority_principal_id": "forged-client-principal",
            },
        }
    )

    assert response["outbox_count"] == 1
    authorization = calls[0]["payload"]["deliveries"][0]["authorization"]
    assert authorization == {
        "request_id": request_id,
        "governor_grant_ref": grant_ref,
        "authority_kind": "governed_life_http",
        "authority_principal_id": "person-aw04",
    }


def test_surface_foreground_completion_is_one_governed_command(monkeypatch) -> None:  # noqa: ANN001
    calls: list[dict[str, Any]] = []

    def _submit(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return {
            "command_id": kwargs["command_id"],
            "person_id": kwargs["person_id"],
            "status": "APPLIED",
            "reason_code": None,
            "event_id": "life_evt_foreground",
            "state_version": 5,
            "state_digest": "digest-foreground",
            "outbox_count": 1,
            "created_at_ms": kwargs["created_at_ms"],
            "replayed": False,
        }

    monkeypatch.setattr(delivery_audit, "submit_life_command", _submit)
    result = SimpleNamespace(
        selected_text="收到",
        natsume_emotion_tag="HAPPY",
        user_emotion_tag="NEUTRAL",
        vts_expression_id="smile",
        observation_codes=(),
        runtime=SimpleNamespace(emotion_confidence=0.6, generation_trace={}, latency_ms=5),
        compiled=SimpleNamespace(trace={}),
    )
    governor_result = {
        "decision": "APPROVE",
        "intent_id": "intent-foreground",
        "ts_ms": 123,
    }
    authorization = {
        "request_id": "req-foreground",
        "governor_grant_ref": governor_grant_ref(
            "req-foreground",
            governor_result,
        ),
        "authority_kind": "surface_governor",
        "authority_principal_id": "person-aw04",
    }
    agent_input = SimpleNamespace(
        request_id="req-foreground",
        person_id="person-aw04",
        session_id="sess-aw04",
        user_message="在吗",
        ts_ms=123,
        reply_channel="qq",
        reply_target="conversation-aw04",
        bond_summary={},
        llm_runtime={"provider_name": "mock", "model_id": "mock-chat"},
        emotion_context={},
        emotion_snapshot={},
        render_policy={"task_mode": "daily_chat"},
        governor_result=governor_result,
        delivery_authorization=authorization,
    )
    envelope = ReplyEnvelope.final_reply(text="收到", source="surface_main_chain")

    receipt = surface_main_chain_adapter._submit_surface_foreground_completed(
        life_command_service=object(),
        life_snapshot_reader=object(),
        agent_input=agent_input,
        result=result,
        envelope=envelope,
        narrative="收到",
        trace_stage_meta={
            "life_director": {"embodiment_target": {"emotion_tag": "HAPPY"}},
            "surface_model_attempt": {
                "final_model": "mock-chat",
                "final_provider": "mock",
            },
        },
        timings_ms={"surface_main_chain_ms": 5},
        total_latency_ms=5,
        llm_latency_ms=4,
    )

    assert receipt["status"] == "APPLIED"
    assert len(calls) == 1
    command = calls[0]
    assert command["command_id"] == "life::foreground_completed::req-foreground"
    assert command["command_type"] == "foreground_completed"
    assert len(command["payload"]["projection_intents"]) == 1
    projection = command["payload"]["projection_intents"][0]
    assert projection["target"] == "surface.post_process"
    assert projection["payload"]["request_id"] == "req-foreground"
    assert projection["payload"]["person_id"] == "person-aw04"
    assert projection["payload"]["session_id"] == "sess-aw04"
    assert projection["payload"]["user_message"] == "在吗"
    assert projection["payload"]["narrative"] == "收到"
    assert projection["payload"]["reply_envelope"]["kind"] == "final_reply"
    assert projection["payload"]["model_audit"] == {
        "model_id": "mock-chat",
        "provider_name": "mock",
    }
    assert projection["payload"]["timings_ms"] == {
        "surface_main_chain_ms": 5
    }
    assert [row["destination"] for row in command["payload"]["deliveries"]] == [
        "vts.emotion",
        "mateengine.state",
    ]
    assert command["payload"]["deliveries"][0]["payload"]["request_id"] == "req-foreground:vts"
    assert command["payload"]["deliveries"][1]["payload"]["request_id"] == "req-foreground:mateengine"
    assert all(
        row["authorization"] == authorization
        for row in command["payload"]["deliveries"]
    )


def test_surface_without_official_grant_commits_foreground_without_embodiment(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        delivery_audit,
        "submit_life_command",
        lambda **kwargs: calls.append(kwargs) or {"status": "APPLIED"},
    )
    result = SimpleNamespace(
        selected_text="收到",
        natsume_emotion_tag="HAPPY",
        user_emotion_tag="NEUTRAL",
        vts_expression_id="smile",
        observation_codes=(),
        runtime=SimpleNamespace(emotion_confidence=0.6, generation_trace={}, latency_ms=5),
        compiled=SimpleNamespace(trace={}),
    )
    agent_input = SimpleNamespace(
        request_id="req-no-grant",
        person_id="person-aw04",
        session_id="sess-aw04",
        user_message="在吗",
        ts_ms=123,
        reply_channel="qq",
        reply_target="conversation-aw04",
        bond_summary={},
        llm_runtime={},
        emotion_context={},
        emotion_snapshot={},
        render_policy={},
        governor_result={},
    )

    surface_main_chain_adapter._submit_surface_foreground_completed(
        life_command_service=object(),
        life_snapshot_reader=object(),
        agent_input=agent_input,
        result=result,
        envelope=ReplyEnvelope.final_reply(text="收到", source="surface_main_chain"),
        narrative="收到",
        trace_stage_meta={
            "life_director": {"embodiment_target": {"emotion_tag": "HAPPY"}}
        },
        timings_ms={"surface_main_chain_ms": 5},
        total_latency_ms=5,
        llm_latency_ms=4,
    )

    assert len(calls) == 1
    assert calls[0]["payload"]["projection_intents"]
    assert calls[0]["payload"]["deliveries"] == []
    assert calls[0]["metadata"]["delivery_authorized"] is False
    assert calls[0]["payload"]["projection_intents"][0]["payload"]["gov_decision"] == {}


def test_frozen_surface_defers_complete_life_command_until_task_acceptance(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        delivery_audit,
        "submit_life_command",
        lambda **kwargs: calls.append(kwargs) or {"status": "APPLIED"},
    )
    result = SimpleNamespace(
        selected_text="已完成",
        natsume_emotion_tag="CALM",
        user_emotion_tag="NEUTRAL",
        vts_expression_id="calm",
        observation_codes=(),
        runtime=SimpleNamespace(emotion_confidence=0.8, generation_trace={}, latency_ms=5),
        compiled=SimpleNamespace(trace={}),
    )
    agent_input = SimpleNamespace(
        request_id="req-deferred",
        person_id="person-aw04",
        session_id="sess-aw04",
        user_message="执行受治理回复",
        ts_ms=123,
        reply_channel="qq",
        reply_target="conversation-aw04",
        bond_summary={},
        llm_runtime={},
        emotion_context={},
        emotion_snapshot={},
        render_policy={},
        governor_result={},
        defer_effects_to_task_runtime=True,
    )

    receipt = surface_main_chain_adapter._submit_surface_foreground_completed(
        life_command_service=object(),
        life_snapshot_reader=object(),
        agent_input=agent_input,
        result=result,
        envelope=ReplyEnvelope.final_reply(text="已完成", source="surface_main_chain"),
        narrative="已完成",
        trace_stage_meta={},
        timings_ms={"surface_main_chain_ms": 5},
        total_latency_ms=5,
        llm_latency_ms=4,
    )

    assert calls == []
    assert receipt["status"] == "DEFERRED_TO_TASK_ACCEPTANCE"
    assert len(receipt["effect_intents"]) == 1
    effect = receipt["effect_intents"][0]
    assert effect["effect_type"] == "life.foreground_completed"
    assert effect["commit_scope"] == "NODE"
    assert effect["payload"]["command_id"] == "life::foreground_completed::req-deferred"
    assert effect["payload"]["payload"]["projection_intents"][0]["target"] == "surface.post_process"


def test_app_life_paths_have_no_legacy_mutator_or_surface_create_task() -> None:
    forbidden = (
        "get_life_state",
        "advance_life_to_now",
        "advance_inner_life_to_now",
        "record_dialogue_aftereffect",
        "record_work_environment_event",
        "run_life_tick",
        "attempt_life_proactive_push",
    )
    modules = (
        digital_life_routes,
        surface_main_chain_adapter,
    )
    for module in modules:
        source = inspect.getsource(module)
        for symbol in forbidden:
            assert symbol not in source
    assert "asyncio.create_task" not in inspect.getsource(surface_main_chain_adapter)
