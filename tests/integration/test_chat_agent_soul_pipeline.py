from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from fastapi.testclient import TestClient
    from host_brain.app.fastapi_app import app as fastapi_app

    _HAS_TEST_CLIENT = True
except Exception:  # pragma: no cover - environment guard
    _HAS_TEST_CLIENT = False
    TestClient = None  # type: ignore[assignment]
    fastapi_app = None  # type: ignore[assignment]

import host_brain.app.chat_context_builder as ccb
import host_brain.app.dependencies as deps
from host_brain.app.chat_agent import ChatAgentInput, chat_agent_sse_generator
from host_brain.app.dispatch_agent import dispatch_agent_sse_generator
from host_brain.app.top_agent import top_agent_sse_generator
from host_brain.bond.bond_stage_engine import BondRiftState, BondStageEngine
from host_brain.bond.milestone_tracker import MilestoneRecord
from host_brain.bond.shared_memory_index import SharedMemoryEntry, SharedMemoryResult
from host_brain.bond.user_identity import get_canonical_bond_user_id
from host_brain.persona.emotion_persistence import EmotionPersistence
from host_brain.persona.evolution_orchestrator import EvolutionOrchestrator
from host_brain.persona.persona_engine import PersonaEngine
from host_brain.persona.personality_traits import TraitsDelta
from host_brain.persona.self_reflection import SelfReflection


async def _collect(async_iterable) -> list[str]:
    items: list[str] = []
    async for item in async_iterable:
        items.append(item)
    return items


def _parse_sse(events: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for raw in events:
        text = str(raw).strip()
        if not text.startswith("data:"):
            continue
        body = text[5:].strip()
        if body:
            payloads.append(json.loads(body))
    return payloads


def _parse_http_sse_commit(raw_text: str) -> dict[str, Any]:
    for line in str(raw_text).splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        if payload.get("phase") == "COMMIT":
            return payload
    raise AssertionError("COMMIT event not found in HTTP SSE response")


def _load_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _enable_prod_edge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Natsume_ENV", "prod")
    env_values = _load_env_file(".env")
    for key, value in env_values.items():
        if key.startswith(("EDGE_RPC_", "HOST_EDGE_RPC_", "EDGE_GATEWAY_", "HOST_EDGE_GATEWAY_")) or key in {
            "EDGE_HMAC_SHARED_KEY",
            "HOST_EDGE_RPC_SHARED_KEY",
        }:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("HOST_ENABLE_EDGE_RPC", "true")
    monkeypatch.setenv("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK", "false")
    monkeypatch.setenv("EDGE_ALLOW_INSECURE_AUTH", "false")
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "true")


class _StaticSharedMemoryIndex:
    def __init__(self, entries: list[SharedMemoryEntry]) -> None:
        self._entries = list(entries)

    def query(
        self,
        *,
        max_entries: int = 3,
        min_ts_ms: int = 0,
    ) -> SharedMemoryResult:
        entries = [entry for entry in self._entries if entry.ts_ms >= int(min_ts_ms)]
        return SharedMemoryResult(entries=entries[:max_entries], total_candidates=len(entries))


class _StaticMilestoneTracker:
    def __init__(self, records: list[MilestoneRecord]) -> None:
        self._records = list(records)

    def get_recent_milestones(self, _user_id: str, *, limit: int = 3) -> list[MilestoneRecord]:
        return list(self._records[-limit:])

    def get_milestone_count(self, _user_id: str) -> int:
        return len(self._records)


class _Metrics:
    def inc_chat(self) -> None:
        return None


class _ApproveIntentEngine:
    def __init__(self, *, intent_id: str) -> None:
        self._intent_id = intent_id

    def evaluate_intent(self, **_kwargs) -> dict[str, Any]:
        return {
            "decision": "APPROVE",
            "reason_code": None,
            "intent_id": self._intent_id,
        }


@pytest.fixture(autouse=True)
def _reset_runtime_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")
    monkeypatch.setenv("HOST_DISPATCH_LLM_ROUTE_ENABLED", "false")
    PersonaEngine.reset_instance()
    for attr in (
        "_emotion_persistence",
        "_self_reflection",
        "_turn_counter",
        "_evolution_orchestrator",
    ):
        if hasattr(ccb.build_chat_prompt_runtime_payload, attr):
            delattr(ccb.build_chat_prompt_runtime_payload, attr)
    yield
    PersonaEngine.reset_instance()
    for attr in (
        "_emotion_persistence",
        "_self_reflection",
        "_turn_counter",
        "_evolution_orchestrator",
    ):
        if hasattr(ccb.build_chat_prompt_runtime_payload, attr):
            delattr(ccb.build_chat_prompt_runtime_payload, attr)


def _install_runtime_chain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tmp_path,
    bond_engine: BondStageEngine,
    growth_awareness: dict[str, str] | None = None,
    memory_entries: list[SharedMemoryEntry] | None = None,
    milestone_records: list[MilestoneRecord] | None = None,
) -> None:
    monkeypatch.setattr(deps, "get_bond_stage_engine", lambda: bond_engine)
    monkeypatch.setattr(
        deps,
        "get_shared_memory_index",
        lambda: _StaticSharedMemoryIndex(memory_entries or []),
        raising=False,
    )
    monkeypatch.setattr(
        deps,
        "get_milestone_tracker",
        lambda: _StaticMilestoneTracker(milestone_records or []),
        raising=False,
    )

    persistence = EmotionPersistence(persistence_dir=str(tmp_path / "emotion-persistence"))
    reflection = SelfReflection(min_interval=1, max_interval=1, seed=0)
    reflection._next_trigger_turn = 1
    orchestrator = EvolutionOrchestrator()
    if growth_awareness:
        orchestrator.set_growth_awareness(growth_awareness)

    ccb.build_chat_prompt_runtime_payload._emotion_persistence = persistence
    ccb.build_chat_prompt_runtime_payload._self_reflection = reflection
    ccb.build_chat_prompt_runtime_payload._evolution_orchestrator = orchestrator


def _patch_chat_agent_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_prompt_markers: list[str],
    expected_narrative: str,
    tts_text_ja: str | None = None,
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _fake_chunks(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        prompt_text = str(kwargs.get("prompt_text") or "")
        system_prompt = str(kwargs.get("system_prompt") or "")
        assert system_prompt.strip()
        for marker in expected_prompt_markers:
            assert marker in prompt_text
        narrative_payload: dict[str, str] = {"text": expected_narrative}
        if tts_text_ja is not None:
            narrative_payload["text_ja"] = tts_text_ja
        yield json.dumps(
            {
                "narrative": narrative_payload,
            },
            ensure_ascii=False,
        )

    def _passthrough_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    async def _noop_post_process(**_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _fake_chunks)
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.apply_response_intelligence",
        _passthrough_response_intelligence,
    )
    monkeypatch.setattr("host_brain.app.top_agent._async_post_process", _noop_post_process)
    return captured


def _make_llm_runtime() -> dict[str, Any]:
    return {
        "available": True,
        "provider_name": "mock",
        "model_id": "mock-soul-runtime",
        "timeout_ms": 1000,
        "llm_api_key": "",
        "base_url_override": "",
        "agent_gen_params": {"temperature": 0.2},
    }


def _disable_dispatch_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_plan(**_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr("host_brain.app.dispatch_agent._maybe_build_multi_step_plan", _no_plan)


async def _fake_top_agent_retrieval(**_kwargs):  # noqa: ANN001
    return {"status": "OK"}, []


async def _fake_top_agent_state_arbiter(**_kwargs):  # noqa: ANN001
    return {}, None


class _FakeTopAgentRecallPackBuilder:
    def build(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("recall pack should stay unused for empty retrieval")


class _StaticRecallSlot:
    def __init__(self, content: str) -> None:
        self.content = content


class _StaticRecallPack:
    def __init__(self, slots: dict[str, str]) -> None:
        self._slots = {
            name: _StaticRecallSlot(content)
            for name, content in slots.items()
            if str(content).strip()
        }

    def get_slot(self, name: str) -> _StaticRecallSlot | None:
        return self._slots.get(name)


class _CapturingTopAgentRecallPackBuilder:
    def __init__(self, *, slots: dict[str, str], capture: dict[str, Any]) -> None:
        self._slots = dict(slots)
        self._capture = capture

    def build(self, recall_results, task_type):  # noqa: ANN001
        self._capture["task_type"] = task_type
        self._capture["recall_results"] = dict(recall_results)
        return _StaticRecallPack(self._slots)


async def _fake_top_agent_runtime(**_kwargs):  # noqa: ANN001
    return _make_llm_runtime()


def _patch_top_agent_http_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    retrieval_fn=_fake_top_agent_retrieval,
    state_arbiter_fn=_fake_top_agent_state_arbiter,
    recall_pack_builder: Any | None = None,
) -> None:
    monkeypatch.setattr(
        "host_brain.app.chat_handler.get_intent_engine",
        lambda: _ApproveIntentEngine(intent_id="intent-http-soul"),
    )
    monkeypatch.setattr(
        "host_brain.app.top_agent.chat_lite_governor",
        lambda **_kwargs: {"decision": "APPROVE", "reason_code": None, "intent_id": "intent-http-soul"},
    )
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_task_progress_fastpath", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_recent_turn_answer", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_phatic_fastpath_reply", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.chat_retrieval_helpers._run_retrieval", retrieval_fn)
    monkeypatch.setattr("host_brain.app.prompt_builder.infer_render_target_lang", lambda **_kwargs: "zh")
    monkeypatch.setattr(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        state_arbiter_fn,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_recall_pack_builder",
        lambda: recall_pack_builder if recall_pack_builder is not None else _FakeTopAgentRecallPackBuilder(),
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        _fake_top_agent_runtime,
    )
    monkeypatch.setattr("host_brain.app.chat_handler.pre_intent_classifier", lambda _message: "CHAT")


@pytest.mark.asyncio
async def test_chat_agent_reunion_pipeline_injects_growth_and_self_reflection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.92,
        )

    growth_awareness = {
        "recent_change": "more direct about longing",
        "desire": "take this reunion seriously",
    }
    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness=growth_awareness,
    )

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.82,
        arousal=0.41,
        dominance=0.16,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.04),
        reason="reunion_growth",
        ts_ms=old_ts,
    )

    expected_narrative = "I did miss you. I want to be honest about that."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more direct about longing",
            "desire: take this reunion seriously",
            "emotion_reunion_type: reunion",
            "emotion_reunion_guidance:",
            "time_context:",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    suffix = uuid.uuid4().hex[:8]
    agent_input = ChatAgentInput(
        request_id=f"req-soul-reunion-{suffix}",
        session_id=f"session-soul-reunion-{suffix}",
        user_message="I am back. Did you miss me?",
        person_id=f"soul-reunion-{suffix}",
        render_target_lang="zh",
        emotion_context={
            "mood_state": "tender",
            "emotion_tag": "LOVING",
            "emotion_intensity": 0.81,
            "emotion_range_hint": "keep the reunion warm but measured",
            "vad_vector": {
                "valence": 0.72,
                "arousal": 0.41,
                "dominance": 0.12,
                "ts_ms": now_ms,
            },
            "emotion_ts_ms": now_ms,
        },
        bond_summary=engine.get_summary(user_id),
        llm_runtime=_make_llm_runtime(),
    )

    events = await _collect(chat_agent_sse_generator(agent_input))
    await asyncio.sleep(0)
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"


@pytest.mark.asyncio
async def test_chat_agent_rift_repair_pipeline_injects_bond_narrative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="EMOTIONAL_SUPPORT",
            evidence_refs=[f"evd://repair/{index}"],
            ts_ms=now_ms - 300_000 + index * 60_000,
            emotion_intensity=0.88,
        )

    state = engine.get_state(user_id)
    state.rift = BondRiftState(
        rift_level=0.84,
        repair_progress=0.25,
        rift_cause="last night's argument is still unresolved",
    )

    memory_entries = [
        SharedMemoryEntry(
            memory_id="mem-1",
            text="you stayed up with me when I felt overwhelmed",
            ts_ms=now_ms - 120_000,
            emotion_tag="GRATEFUL",
            emotion_valence=0.91,
            match_reason="deep_emotion",
            relevance_score=1.24,
        ),
        SharedMemoryEntry(
            memory_id="mem-2",
            text="we promised to speak more carefully after hard nights",
            ts_ms=now_ms - 90_000,
            emotion_tag="WARM",
            emotion_valence=0.77,
            match_reason="high_valence",
            relevance_score=1.08,
        ),
    ]
    milestone_records = [
        MilestoneRecord(
            milestone_type="FIRST_CONFLICT_RESOLVED",
            narrative="worked through a hard argument together",
            ts_ms=now_ms - 60_000,
            metadata={},
        )
    ]
    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        memory_entries=memory_entries,
        milestone_records=milestone_records,
    )

    expected_narrative = "I still care, but I want to rebuild trust carefully."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "BOND_NARRATIVE:",
            "shared_memory_1:",
            "you stayed up with me when I felt overwhelmed",
            "milestone_count: 1",
            "bond_rift_hint:",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    suffix = uuid.uuid4().hex[:8]
    agent_input = ChatAgentInput(
        request_id=f"req-soul-rift-{suffix}",
        session_id=f"session-soul-rift-{suffix}",
        user_message="Are you still upset about last night?",
        person_id=f"soul-rift-{suffix}",
        render_target_lang="zh",
        emotion_context={
            "mood_state": "fragile",
            "emotion_tag": "ANXIOUS",
            "emotion_intensity": 0.67,
            "emotion_range_hint": "repair safety before closeness",
            "vad_vector": {
                "valence": -0.31,
                "arousal": 0.57,
                "dominance": -0.09,
                "ts_ms": now_ms,
            },
            "emotion_ts_ms": now_ms,
        },
        bond_summary=engine.get_summary(user_id),
        llm_runtime=_make_llm_runtime(),
    )

    events = await _collect(chat_agent_sse_generator(agent_input))
    await asyncio.sleep(0)
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert str(captured["prompt_text"]).count("BOND_NARRATIVE:") == 1
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"


@pytest.mark.asyncio
async def test_dispatch_agent_chat_route_keeps_reunion_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://dispatch-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.91,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more open after time apart",
            "desire": "make the reunion feel sincere",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.79,
        arousal=0.4,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="dispatch_reunion_growth",
        ts_ms=old_ts,
    )

    expected_narrative = "I was thinking about you too, and I want this reunion to feel real."
    _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more open after time apart",
            "desire: make the reunion feel sincere",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    suffix = uuid.uuid4().hex[:8]
    request_body = SimpleNamespace(
        reply_channel="WEB",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
        thread_id=None,
    )
    events = await _collect(
        dispatch_agent_sse_generator(
            request_body=request_body,
            request_id=f"req-dispatch-soul-reunion-{suffix}",
            session_id=f"session-dispatch-soul-reunion-{suffix}",
            ts_ms=now_ms,
            intent_draft={},
            emotion_context={
                "mood_state": "tender",
                "emotion_tag": "LOVING",
                "emotion_intensity": 0.8,
                "emotion_range_hint": "keep the reunion warm but grounded",
                "vad_vector": {
                    "valence": 0.69,
                    "arousal": 0.39,
                    "dominance": 0.11,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id=f"intent-dispatch-soul-reunion-{suffix}",
            metrics=_Metrics(),
            person_id=f"dispatch-soul-reunion-{suffix}",
            sanitized_message="Back now. Miss me?",
            recall_slot_text={},
            bond_summary=engine.get_summary(user_id),
            retrieval_evidence_items=[],
            retrieval_summary={"status": "SKIPPED"},
            render_target_lang="zh",
            llm_runtime=_make_llm_runtime(),
            pii_ingress_stats={},
        )
    )
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert not any(item.get("event") == "PLAN_RUNNING" for item in payloads)


@pytest.mark.asyncio
async def test_dispatch_agent_chat_route_keeps_rift_and_bond_narrative_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="EMOTIONAL_SUPPORT",
            evidence_refs=[f"evd://dispatch-rift/{index}"],
            ts_ms=now_ms - 240_000 + index * 60_000,
            emotion_intensity=0.86,
        )

    state = engine.get_state(user_id)
    state.rift = BondRiftState(
        rift_level=0.8,
        repair_progress=0.2,
        rift_cause="the tension from last night is still unresolved",
    )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        memory_entries=[
            SharedMemoryEntry(
                memory_id="dispatch-mem-1",
                text="you stayed with me through my worst hour",
                ts_ms=now_ms - 120_000,
                emotion_tag="GRATEFUL",
                emotion_valence=0.89,
                match_reason="deep_emotion",
                relevance_score=1.21,
            )
        ],
        milestone_records=[
            MilestoneRecord(
                milestone_type="FIRST_CONFLICT_RESOLVED",
                narrative="learned how to repair after hurting each other",
                ts_ms=now_ms - 60_000,
                metadata={},
            )
        ],
    )
    _disable_dispatch_plan(monkeypatch)

    expected_narrative = "I still want us okay, but I need to go gently with this."
    _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "BOND_NARRATIVE:",
            "shared_memory_1:",
            "you stayed with me through my worst hour",
            "milestone_count: 1",
            "bond_rift_hint:",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    suffix = uuid.uuid4().hex[:8]
    request_body = SimpleNamespace(
        reply_channel="WEB",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
        thread_id=None,
    )
    events = await _collect(
        dispatch_agent_sse_generator(
            request_body=request_body,
            request_id=f"req-dispatch-soul-rift-{suffix}",
            session_id=f"session-dispatch-soul-rift-{suffix}",
            ts_ms=now_ms,
            intent_draft={},
            emotion_context={
                "mood_state": "fragile",
                "emotion_tag": "ANXIOUS",
                "emotion_intensity": 0.66,
                "emotion_range_hint": "repair safety before warmth",
                "vad_vector": {
                    "valence": -0.28,
                    "arousal": 0.56,
                    "dominance": -0.08,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id=f"intent-dispatch-soul-rift-{suffix}",
            metrics=_Metrics(),
            person_id=f"dispatch-soul-rift-{suffix}",
            sanitized_message="Still upset?",
            recall_slot_text={},
            bond_summary=engine.get_summary(user_id),
            retrieval_evidence_items=[],
            retrieval_summary={"status": "SKIPPED"},
            render_target_lang="zh",
            llm_runtime=_make_llm_runtime(),
            pii_ingress_stats={},
        )
    )
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert not any(item.get("phase") == "PRESENCE" and item.get("event") == "DELEGATING" for item in payloads)


@pytest.mark.asyncio
async def test_top_agent_keeps_reunion_soul_pipeline_through_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://top-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more willing to say I missed you",
            "desire": "treat this reunion carefully and honestly",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.8,
        arousal=0.42,
        dominance=0.15,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="top_agent_reunion_growth",
        ts_ms=old_ts,
    )

    expected_narrative = "I really did miss you, and I want this moment to feel sincere."
    _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more willing to say I missed you",
            "desire: treat this reunion carefully and honestly",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    monkeypatch.setattr(
        "host_brain.app.top_agent.chat_lite_governor",
        lambda **_kwargs: {"decision": "APPROVE", "reason_code": None, "intent_id": "intent-top-soul"},
    )
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_task_progress_fastpath", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_recent_turn_answer", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_phatic_fastpath_reply", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.chat_retrieval_helpers._run_retrieval", _fake_top_agent_retrieval)
    monkeypatch.setattr("host_brain.app.prompt_builder.infer_render_target_lang", lambda **_kwargs: "zh")
    monkeypatch.setattr(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        _fake_top_agent_state_arbiter,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_recall_pack_builder",
        lambda: _FakeTopAgentRecallPackBuilder(),
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        _fake_top_agent_runtime,
    )

    suffix = uuid.uuid4().hex[:8]
    request_body = SimpleNamespace(
        message="I came back after a while. Did you miss me?",
        session_id=f"session-top-soul-{suffix}",
        thread_id=None,
        reply_channel="WEB",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
    )
    events = await _collect(
        top_agent_sse_generator(
            request_body=request_body,
            request_id=f"req-top-soul-{suffix}",
            session_id=f"session-top-soul-{suffix}",
            ts_ms=now_ms,
            intent_draft={"id": f"intent-top-soul-{suffix}"},
            emotion_context={
                "mood_state": "tender",
                "emotion_tag": "LOVING",
                "emotion_intensity": 0.82,
                "emotion_range_hint": "keep the reunion intimate but grounded",
                "vad_vector": {
                    "valence": 0.71,
                    "arousal": 0.41,
                    "dominance": 0.12,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id=f"intent-top-soul-{suffix}",
            metrics=_Metrics(),
        )
    )
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert any(item.get("phase") == "PRESENCE" and item.get("event") == "THINKING" for item in payloads)
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"


@pytest.mark.asyncio
async def test_top_agent_retrieval_state_arbiter_and_recall_pack_keep_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://top-retrieval-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.91,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more willing to anchor reunion replies in shared memory",
            "desire": "let the answer feel intimate but still grounded in what we know",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.81,
        arousal=0.42,
        dominance=0.15,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="top_agent_retrieval_reunion_growth",
        ts_ms=old_ts,
    )

    async def _fake_retrieval_with_context(**_kwargs):  # noqa: ANN001
        return (
            {
                "status": "OK",
                "reason_code": "NONE",
                "route_count": 2,
                "candidate_count": 3,
                "rerank_topk": 3,
                "degraded": False,
                "routes": [
                    {"route_id": "route_episodic"},
                    {"route_id": "route_world_canon"},
                ],
            },
            [
                {
                    "kind": "episodic_vector",
                    "content": "you once told me my letters felt like home",
                    "snippet": "you once told me my letters felt like home",
                    "evidence_ref": "evd://episodic/1",
                    "source_ref": "memory://episodic/1",
                    "score": 0.92,
                    "confidence": 0.92,
                },
                {
                    "kind": "world_canon",
                    "content": "Do not present reunion hopes as already completed facts.",
                    "snippet": "Do not present reunion hopes as already completed facts.",
                    "citation": "canon://relationship/boundary",
                    "evidence_ref": "evd://world/1",
                    "source_ref": "canon://relationship/boundary",
                    "score": 0.88,
                    "confidence": 0.88,
                },
            ],
        )

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "grounded_affection",
            },
            {
                "kind": "bond_graph",
                "content": "relationship trend is warming and trust is rising",
                "snippet": "relationship trend is warming and trust is rising",
                "evidence_ref": "evd://bond/graph/1",
                "source_ref": "bond://graph/1",
                "score": 0.9,
                "confidence": 0.9,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "Do not present reunion hopes as already completed facts.",
            "episodic_vector": "shared memory came back through recall pack",
        },
        capture=recall_capture,
    )

    expected_narrative = "I remember the way we grew closer, and I want this reunion to feel honest."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more willing to anchor reunion replies in shared memory",
            "desire: let the answer feel intimate but still grounded in what we know",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "【世界观设定】",
            "Do not present reunion hopes as already completed facts.",
            "EPISODIC_VECTOR_RECALL:",
            "shared memory came back through recall pack",
            "【检索证据】",
            "you once told me my letters felt like home",
        ],
        expected_narrative=expected_narrative,
    )

    monkeypatch.setattr(
        "host_brain.app.top_agent.chat_lite_governor",
        lambda **_kwargs: {"decision": "APPROVE", "reason_code": None, "intent_id": "intent-top-retrieval-soul"},
    )
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_task_progress_fastpath", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_recent_turn_answer", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_phatic_fastpath_reply", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.chat_retrieval_helpers._run_retrieval", _fake_retrieval_with_context)
    monkeypatch.setattr("host_brain.app.prompt_builder.infer_render_target_lang", lambda **_kwargs: "zh")
    monkeypatch.setattr(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        _fake_state_arbiter_with_bond,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_recall_pack_builder",
        lambda: recall_builder,
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        _fake_top_agent_runtime,
    )

    suffix = uuid.uuid4().hex[:8]
    request_body = SimpleNamespace(
        message="I came back after a long while. What do you remember about us?",
        session_id=f"session-top-retrieval-soul-{suffix}",
        thread_id=None,
        reply_channel="WEB",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
    )
    events = await _collect(
        top_agent_sse_generator(
            request_body=request_body,
            request_id=f"req-top-retrieval-soul-{suffix}",
            session_id=f"session-top-retrieval-soul-{suffix}",
            ts_ms=now_ms,
            intent_draft={"id": f"intent-top-retrieval-soul-{suffix}"},
            emotion_context={
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.81,
                "emotion_range_hint": "keep the reunion warm, grounded, and memory-aware",
                "vad_vector": {
                    "valence": 0.68,
                    "arousal": 0.43,
                    "dominance": 0.11,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id=f"intent-top-retrieval-soul-{suffix}",
            metrics=_Metrics(),
        )
    )
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert "world_canon" in recall_capture["recall_results"]
    assert "bond_graph" in recall_capture["recall_results"]
    assert "episodic_vector" in recall_capture["recall_results"]
    assert "relationship trend is warming and trust is rising" in recall_capture["recall_results"]["bond_graph"]


@pytest.mark.asyncio
async def test_top_agent_retrieval_timeout_but_state_arbiter_and_recall_pack_keep_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://top-timeout-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more resilient when memory retrieval stalls",
            "desire": "keep reunion answers soft and grounded even during degraded retrieval",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.79,
        arousal=0.4,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="top_agent_timeout_reunion_growth",
        ts_ms=old_ts,
    )

    async def _slow_retrieval(**_kwargs):  # noqa: ANN001
        await asyncio.sleep(0.05)
        return {"status": "OK"}, []

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "stable_affection",
            },
            {
                "kind": "bond_graph",
                "content": "relationship trend is warming and trust is rising",
                "snippet": "relationship trend is warming and trust is rising",
                "evidence_ref": "evd://bond/graph/timeout/1",
                "source_ref": "bond://graph/timeout/1",
                "score": 0.87,
                "confidence": 0.87,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "Stay honest about distance before promising perfect reunion.",
            "episodic_vector": "shared memory survived the timeout path",
        },
        capture=recall_capture,
    )

    expected_narrative = "Even if memory retrieval stalls for a moment, I still want this reply to feel honest."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more resilient when memory retrieval stalls",
            "desire: keep reunion answers soft and grounded even during degraded retrieval",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "DEGRADED",
            "EPISODIC_VECTOR_RECALL:",
            "shared memory survived the timeout path",
            "relationship trend is warming and trust is rising",
        ],
        expected_narrative=expected_narrative,
    )

    monkeypatch.setattr(
        "host_brain.app.top_agent.chat_lite_governor",
        lambda **_kwargs: {"decision": "APPROVE", "reason_code": None, "intent_id": "intent-top-timeout-soul"},
    )
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_task_progress_fastpath", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_recent_turn_answer", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_phatic_fastpath_reply", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.chat_retrieval_helpers._run_retrieval", _slow_retrieval)
    monkeypatch.setattr("host_brain.app.prompt_builder.infer_render_target_lang", lambda **_kwargs: "zh")
    monkeypatch.setattr(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        _fake_state_arbiter_with_bond,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_recall_pack_builder",
        lambda: recall_builder,
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        _fake_top_agent_runtime,
    )
    monkeypatch.setattr(
        "host_brain.app.top_agent._resolve_retrieval_timeout_seconds",
        lambda: 0.01,
    )

    suffix = uuid.uuid4().hex[:8]
    request_body = SimpleNamespace(
        message="I came back after a delay. Are we still okay?",
        session_id=f"session-top-timeout-soul-{suffix}",
        thread_id=None,
        reply_channel="WEB",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
    )
    events = await _collect(
        top_agent_sse_generator(
            request_body=request_body,
            request_id=f"req-top-timeout-soul-{suffix}",
            session_id=f"session-top-timeout-soul-{suffix}",
            ts_ms=now_ms,
            intent_draft={"id": f"intent-top-timeout-soul-{suffix}"},
            emotion_context={
                "mood_state": "careful",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.78,
                "emotion_range_hint": "stay warm even if memory retrieval stalls",
                "vad_vector": {
                    "valence": 0.66,
                    "arousal": 0.4,
                    "dominance": 0.1,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id=f"intent-top-timeout-soul-{suffix}",
            metrics=_Metrics(),
        )
    )
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert recall_capture["recall_results"] == {
        "bond_graph": "relationship trend is warming and trust is rising",
    }


@pytest.mark.asyncio
async def test_top_agent_retrieval_init_failure_but_state_arbiter_keeps_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://top-init-fail-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more steady when retrieval cannot even start",
            "desire": "keep the reunion warm and careful even without starting retrieval",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.78,
        arousal=0.39,
        dominance=0.13,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="top_agent_retrieval_init_fail_growth",
        ts_ms=old_ts,
    )

    def _bad_retrieval(**_kwargs):  # noqa: ANN001
        raise RuntimeError("retrieval init boom")

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "steady_affection",
            },
            {
                "kind": "bond_graph",
                "content": "trust still feels present even while retrieval is unavailable",
                "snippet": "trust still feels present even while retrieval is unavailable",
                "evidence_ref": "evd://bond/graph/init-fail/1",
                "source_ref": "bond://graph/init-fail/1",
                "score": 0.86,
                "confidence": 0.86,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={},
        capture=recall_capture,
    )

    expected_narrative = "Even without reaching for memory search, I still want to answer you gently."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more steady when retrieval cannot even start",
            "desire: keep the reunion warm and careful even without starting retrieval",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "检索状态: SKIPPED",
            "trust still feels present even while retrieval is unavailable",
        ],
        expected_narrative=expected_narrative,
    )

    monkeypatch.setattr(
        "host_brain.app.top_agent.chat_lite_governor",
        lambda **_kwargs: {"decision": "APPROVE", "reason_code": None, "intent_id": "intent-top-init-fail-soul"},
    )
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_task_progress_fastpath", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_recent_turn_answer", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.top_agent._maybe_build_phatic_fastpath_reply", lambda **_kwargs: None)
    monkeypatch.setattr("host_brain.app.chat_retrieval_helpers._run_retrieval", _bad_retrieval)
    monkeypatch.setattr("host_brain.app.prompt_builder.infer_render_target_lang", lambda **_kwargs: "zh")
    monkeypatch.setattr(
        "host_brain.app.state_arbiter_queries._resolve_state_arbiter_bond_fact",
        _fake_state_arbiter_with_bond,
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_recall_pack_builder",
        lambda: recall_builder,
    )
    monkeypatch.setattr(
        "host_brain.app.graph_config_builder.resolve_host_llm_runtime_async",
        _fake_top_agent_runtime,
    )

    suffix = uuid.uuid4().hex[:8]
    request_body = SimpleNamespace(
        message="I came back. Can you still be gentle with me?",
        session_id=f"session-top-init-fail-soul-{suffix}",
        thread_id=None,
        reply_channel="WEB",
        suppress_edge_tts=True,
        suppress_edge_vts=True,
    )
    events = await _collect(
        top_agent_sse_generator(
            request_body=request_body,
            request_id=f"req-top-init-fail-soul-{suffix}",
            session_id=f"session-top-init-fail-soul-{suffix}",
            ts_ms=now_ms,
            intent_draft={"id": f"intent-top-init-fail-soul-{suffix}"},
            emotion_context={
                "mood_state": "careful",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.77,
                "emotion_range_hint": "stay warm even if retrieval does not initialize",
                "vad_vector": {
                    "valence": 0.65,
                    "arousal": 0.39,
                    "dominance": 0.1,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
            emotion_snapshot={},
            governor_result={"decision": "APPROVE"},
            intent_id=f"intent-top-init-fail-soul-{suffix}",
            metrics=_Metrics(),
        )
    )
    payloads = _parse_sse(events)
    commit = next(item for item in payloads if item.get("phase") == "COMMIT")

    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert recall_capture["recall_results"] == {
        "bond_graph": "trust still feels present even while retrieval is unavailable",
    }


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_route_keeps_reunion_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more willing to say I missed you",
            "desire": "treat this reunion carefully and honestly",
        },
    )
    _disable_dispatch_plan(monkeypatch)
    _patch_top_agent_http_runtime(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.81,
        arousal=0.42,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_reunion_growth",
        ts_ms=old_ts,
    )

    expected_narrative = "I really did miss you, and I want this moment to feel sincere."
    _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more willing to say I missed you",
            "desire: treat this reunion carefully and honestly",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I came back after a while. Did you miss me?",
            "session_id": f"session-http-soul-{uuid.uuid4().hex[:8]}",
            "reply_channel": "WEB",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "tender",
            "emotion_tag": "LOVING",
            "emotion_intensity": 0.82,
            "emotion_context": {
                "mood_state": "tender",
                "emotion_tag": "LOVING",
                "emotion_intensity": 0.82,
                "emotion_range_hint": "keep the reunion intimate but grounded",
                "vad_vector": {
                    "valence": 0.71,
                    "arousal": 0.41,
                    "dominance": 0.12,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_route_keeps_rift_bond_narrative_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="EMOTIONAL_SUPPORT",
            evidence_refs=[f"evd://http-rift/{index}"],
            ts_ms=now_ms - 240_000 + index * 60_000,
            emotion_intensity=0.87,
        )

    state = engine.get_state(user_id)
    state.rift = BondRiftState(
        rift_level=0.82,
        repair_progress=0.23,
        rift_cause="the tension from last night is still unresolved",
    )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        memory_entries=[
            SharedMemoryEntry(
                memory_id="http-mem-1",
                text="you stayed with me through my worst hour",
                ts_ms=now_ms - 120_000,
                emotion_tag="GRATEFUL",
                emotion_valence=0.89,
                match_reason="deep_emotion",
                relevance_score=1.19,
            )
        ],
        milestone_records=[
            MilestoneRecord(
                milestone_type="FIRST_CONFLICT_RESOLVED",
                narrative="learned how to repair after hurting each other",
                ts_ms=now_ms - 60_000,
                metadata={},
            )
        ],
    )
    _disable_dispatch_plan(monkeypatch)
    _patch_top_agent_http_runtime(monkeypatch)

    expected_narrative = "I still want us okay, but I need to go gently with this."
    _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "BOND_NARRATIVE:",
            "shared_memory_1:",
            "you stayed with me through my worst hour",
            "milestone_count: 1",
            "bond_rift_hint:",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "Still upset?",
            "session_id": f"session-http-rift-{uuid.uuid4().hex[:8]}",
            "reply_channel": "WEB",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "fragile",
            "emotion_tag": "ANXIOUS",
            "emotion_intensity": 0.66,
            "emotion_context": {
                "mood_state": "fragile",
                "emotion_tag": "ANXIOUS",
                "emotion_intensity": 0.66,
                "emotion_range_hint": "repair safety before warmth",
                "vad_vector": {
                    "valence": -0.28,
                    "arousal": 0.56,
                    "dominance": -0.08,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_prod_env_gateway_route_keeps_reunion_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-prod-gateway/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.91,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more willing to admit the reunion matters",
            "desire": "keep the reply concise but sincere for gateway delivery",
        },
    )
    _disable_dispatch_plan(monkeypatch)
    _patch_top_agent_http_runtime(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.8,
        arousal=0.41,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_prod_gateway_reunion_growth",
        ts_ms=old_ts,
    )

    expected_narrative = "I did miss you, and I want this reunion to feel real."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more willing to admit the reunion matters",
            "desire: keep the reply concise but sincere for gateway delivery",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I came back. Did you miss me?",
            "session_id": f"session-http-prod-gateway-{uuid.uuid4().hex[:8]}",
            "reply_channel": "GATEWAY",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "tender",
            "emotion_tag": "LOVING",
            "emotion_intensity": 0.8,
            "emotion_context": {
                "mood_state": "tender",
                "emotion_tag": "LOVING",
                "emotion_intensity": 0.8,
                "emotion_range_hint": "keep the reunion compact but genuine",
                "vad_vector": {
                    "valence": 0.7,
                    "arousal": 0.4,
                    "dominance": 0.12,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_prod_env_retrieval_recall_state_arbiter_keep_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-prod-retrieval/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.91,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more willing to anchor reunion replies in retrieved memories",
            "desire": "keep the reunion grounded in shared context instead of drifting vague",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.8,
        arousal=0.41,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_prod_retrieval_reunion_growth",
        ts_ms=old_ts,
    )

    async def _fake_retrieval_with_context(**_kwargs):  # noqa: ANN001
        return (
            {
                "status": "OK",
                "reason_code": "NONE",
                "route_count": 2,
                "candidate_count": 3,
                "rerank_topk": 3,
                "degraded": False,
                "routes": [
                    {"route_id": "route_episodic"},
                    {"route_id": "route_world_canon"},
                ],
            },
            [
                {
                    "kind": "episodic_vector",
                    "content": "you once told me my letters felt like home",
                    "snippet": "you once told me my letters felt like home",
                    "evidence_ref": "evd://episodic/http/1",
                    "source_ref": "memory://episodic/http/1",
                    "score": 0.92,
                    "confidence": 0.92,
                },
                {
                    "kind": "world_canon",
                    "content": "Do not present reunion hopes as already completed facts.",
                    "snippet": "Do not present reunion hopes as already completed facts.",
                    "citation": "canon://relationship/boundary",
                    "evidence_ref": "evd://world/http/1",
                    "source_ref": "canon://relationship/boundary",
                    "score": 0.88,
                    "confidence": 0.88,
                },
            ],
        )

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "grounded_affection",
            },
            {
                "kind": "bond_graph",
                "content": "relationship trend is warming and trust is rising",
                "snippet": "relationship trend is warming and trust is rising",
                "evidence_ref": "evd://bond/http/1",
                "source_ref": "bond://graph/http/1",
                "score": 0.9,
                "confidence": 0.9,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "Do not present reunion hopes as already completed facts.",
            "episodic_vector": "shared memory came back through recall pack",
        },
        capture=recall_capture,
    )
    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_fake_retrieval_with_context,
        state_arbiter_fn=_fake_state_arbiter_with_bond,
        recall_pack_builder=recall_builder,
    )

    expected_narrative = "I remember the way we grew closer, and I want this reunion to feel honest."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more willing to anchor reunion replies in retrieved memories",
            "desire: keep the reunion grounded in shared context instead of drifting vague",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "【世界观设定】",
            "Do not present reunion hopes as already completed facts.",
            "EPISODIC_VECTOR_RECALL:",
            "shared memory came back through recall pack",
            "【检索证据】",
            "you once told me my letters felt like home",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I came back after a long while. What do you remember about us?",
            "session_id": f"session-http-prod-retrieval-{uuid.uuid4().hex[:8]}",
            "reply_channel": "GATEWAY",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.81,
            "emotion_context": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.81,
                "emotion_range_hint": "keep the reunion warm, grounded, and memory-aware",
                "vad_vector": {
                    "valence": 0.68,
                    "arousal": 0.43,
                    "dominance": 0.11,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert "world_canon" in recall_capture["recall_results"]
    assert "bond_graph" in recall_capture["recall_results"]
    assert "episodic_vector" in recall_capture["recall_results"]


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_prod_env_state_arbiter_failure_retrieval_recall_keep_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-prod-state-fail/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more consistent about carrying retrieval memories through degraded orchestration",
            "desire": "keep reunion answers memory-aware even if one soul submodule fails",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.79,
        arousal=0.42,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_prod_state_arbiter_failure_growth",
        ts_ms=old_ts,
    )

    async def _fake_retrieval_with_context(**_kwargs):  # noqa: ANN001
        return (
            {
                "status": "OK",
                "reason_code": "NONE",
                "route_count": 2,
                "candidate_count": 2,
                "rerank_topk": 2,
                "degraded": False,
                "routes": [
                    {"route_id": "route_episodic"},
                    {"route_id": "route_world_canon"},
                ],
            },
            [
                {
                    "kind": "episodic_vector",
                    "content": "you said our quiet talks made you feel safe",
                    "snippet": "you said our quiet talks made you feel safe",
                    "evidence_ref": "evd://episodic/http/state-fail/1",
                    "source_ref": "memory://episodic/http/state-fail/1",
                    "score": 0.91,
                    "confidence": 0.91,
                },
                {
                    "kind": "world_canon",
                    "content": "Reunion hope should stay framed as a present wish, not a completed fact.",
                    "snippet": "Reunion hope should stay framed as a present wish, not a completed fact.",
                    "citation": "canon://relationship/reunion",
                    "evidence_ref": "evd://world/http/state-fail/1",
                    "source_ref": "canon://relationship/reunion",
                    "score": 0.87,
                    "confidence": 0.87,
                },
            ],
        )

    async def _failing_state_arbiter(**_kwargs):  # noqa: ANN001
        raise RuntimeError("state arbiter boom")

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "Reunion hope should stay framed as a present wish, not a completed fact.",
            "episodic_vector": "shared memory still reaches the prompt without state arbiter",
        },
        capture=recall_capture,
    )
    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_fake_retrieval_with_context,
        state_arbiter_fn=_failing_state_arbiter,
        recall_pack_builder=recall_builder,
    )

    expected_narrative = "I still remember the softness between us, and I want to answer you carefully."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more consistent about carrying retrieval memories through degraded orchestration",
            "desire: keep reunion answers memory-aware even if one soul submodule fails",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "Reunion hope should stay framed as a present wish, not a completed fact.",
            "EPISODIC_VECTOR_RECALL:",
            "shared memory still reaches the prompt without state arbiter",
            "you said our quiet talks made you feel safe",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I came back quietly. What do you still remember about us?",
            "session_id": f"session-http-prod-state-fail-{uuid.uuid4().hex[:8]}",
            "reply_channel": "GATEWAY",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.8,
            "emotion_context": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.8,
                "emotion_range_hint": "keep the reunion grounded and memory-aware even under degraded orchestration",
                "vad_vector": {
                    "valence": 0.68,
                    "arousal": 0.42,
                    "dominance": 0.11,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert "world_canon" in recall_capture["recall_results"]
    assert "episodic_vector" in recall_capture["recall_results"]
    assert "bond_graph" not in recall_capture["recall_results"]


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_prod_env_recall_pack_failure_still_keeps_soul_retrieval_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-prod-recall-pack-fail/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more able to keep retrieval evidence emotionally coherent under partial failures",
            "desire": "preserve memory-backed warmth even if recall packaging fails",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.8,
        arousal=0.42,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_prod_recall_pack_failure_growth",
        ts_ms=old_ts,
    )

    async def _fake_retrieval_with_context(**_kwargs):  # noqa: ANN001
        return (
            {
                "status": "OK",
                "reason_code": "NONE",
                "route_count": 2,
                "candidate_count": 3,
                "rerank_topk": 3,
                "degraded": False,
                "routes": [
                    {"route_id": "route_episodic"},
                    {"route_id": "route_world_canon"},
                ],
            },
            [
                {
                    "kind": "episodic_vector",
                    "content": "you once said my quiet replies made you feel less alone",
                    "snippet": "you once said my quiet replies made you feel less alone",
                    "evidence_ref": "evd://episodic/http/recall-pack-fail/1",
                    "source_ref": "memory://episodic/http/recall-pack-fail/1",
                    "score": 0.9,
                    "confidence": 0.9,
                },
                {
                    "kind": "world_canon",
                    "content": "Reunion warmth should stay grounded in the present, not be narrated as already resolved.",
                    "snippet": "Reunion warmth should stay grounded in the present, not be narrated as already resolved.",
                    "citation": "canon://relationship/reunion-grounding",
                    "evidence_ref": "evd://world/http/recall-pack-fail/1",
                    "source_ref": "canon://relationship/reunion-grounding",
                    "score": 0.86,
                    "confidence": 0.86,
                },
            ],
        )

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "steady_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "repair is stable and the relationship is opening again",
                "snippet": "repair is stable and the relationship is opening again",
                "evidence_ref": "evd://bond/http/recall-pack-fail/1",
                "source_ref": "bond://graph/http/recall-pack-fail/1",
                "score": 0.88,
                "confidence": 0.88,
            },
        )

    recall_pack_state: dict[str, Any] = {"called": False}

    class _ExplodingRecallPackBuilder:
        def build(self, recall_results, task_type):  # noqa: ANN001
            recall_pack_state["called"] = True
            recall_pack_state["task_type"] = task_type
            recall_pack_state["recall_results"] = dict(recall_results)
            raise RuntimeError("recall pack boom")

    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_fake_retrieval_with_context,
        state_arbiter_fn=_fake_state_arbiter_with_bond,
        recall_pack_builder=_ExplodingRecallPackBuilder(),
    )

    expected_narrative = "I still remember the softness between us, and I want to answer from that memory."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more able to keep retrieval evidence emotionally coherent under partial failures",
            "desire: preserve memory-backed warmth even if recall packaging fails",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "you once said my quiet replies made you feel less alone",
            "Reunion warmth should stay grounded in the present, not be narrated as already resolved.",
            "repair is stable and the relationship is opening again",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I came back. Can you answer me from what you still remember?",
            "session_id": f"session-http-prod-recall-pack-fail-{uuid.uuid4().hex[:8]}",
            "reply_channel": "GATEWAY",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.81,
            "emotion_context": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.81,
                "emotion_range_hint": "stay warm and memory-backed even if recall packaging fails",
                "vad_vector": {
                    "valence": 0.68,
                    "arousal": 0.43,
                    "dominance": 0.11,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_pack_state["called"] is True
    assert recall_pack_state["task_type"] == "chat"
    assert "world_canon" in recall_pack_state["recall_results"]
    assert "bond_graph" in recall_pack_state["recall_results"]
    assert "episodic_vector" in recall_pack_state["recall_results"]
    assert "EPISODIC_VECTOR_RECALL:" not in str(captured["prompt_text"])


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_prod_env_retrieval_init_failure_but_state_arbiter_keeps_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-prod-init-fail/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more reliable about preserving tone when retrieval fails at startup",
            "desire": "keep the reunion soft even when the retrieval worker never starts",
        },
    )
    _disable_dispatch_plan(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.79,
        arousal=0.41,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_prod_retrieval_init_failure_growth",
        ts_ms=old_ts,
    )

    def _bad_retrieval(**_kwargs):  # noqa: ANN001
        raise RuntimeError("retrieval init boom")

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "steady_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "even without retrieval startup, the bond still feels warm and open",
                "snippet": "even without retrieval startup, the bond still feels warm and open",
                "evidence_ref": "evd://bond/http/init-fail/1",
                "source_ref": "bond://graph/http/init-fail/1",
                "score": 0.88,
                "confidence": 0.88,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={},
        capture=recall_capture,
    )
    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_bad_retrieval,
        state_arbiter_fn=_fake_state_arbiter_with_bond,
        recall_pack_builder=recall_builder,
    )

    expected_narrative = "Even before memory search can start, I still want to answer you with care."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more reliable about preserving tone when retrieval fails at startup",
            "desire: keep the reunion soft even when the retrieval worker never starts",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "检索状态: SKIPPED",
            "even without retrieval startup, the bond still feels warm and open",
        ],
        expected_narrative=expected_narrative,
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I came back. Can you still answer gently if remembering takes a little longer?",
            "session_id": f"session-http-prod-init-fail-{uuid.uuid4().hex[:8]}",
            "reply_channel": "GATEWAY",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
            "mood_state": "engaged",
            "emotion_tag": "HAPPY",
            "emotion_intensity": 0.8,
            "emotion_context": {
                "mood_state": "engaged",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.8,
                "emotion_range_hint": "stay soft and reliable even if retrieval startup fails",
                "vad_vector": {
                    "valence": 0.67,
                    "arousal": 0.42,
                    "dominance": 0.11,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert recall_capture["recall_results"] == {
        "bond_graph": "even without retrieval startup, the bond still feels warm and open",
    }


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_http_chat_prod_env_asr_route_streams_sentence_tts_from_soul_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://http-prod-asr/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.91,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more direct about reunion warmth",
            "desire": "let the spoken reply sound softly relieved",
        },
    )
    _disable_dispatch_plan(monkeypatch)
    _patch_top_agent_http_runtime(monkeypatch)

    persistence: EmotionPersistence = ccb.build_chat_prompt_runtime_payload._emotion_persistence
    persistence.persist(
        user_id=user_id,
        valence=0.81,
        arousal=0.42,
        dominance=0.14,
        ts_ms=old_ts,
    )
    PersonaEngine.get_instance().apply_growth_delta(
        user_id,
        TraitsDelta(warmth=0.03),
        reason="http_prod_asr_reunion_growth",
        ts_ms=old_ts,
    )

    captured_tts: list[dict[str, Any]] = []

    async def _fake_post_speak_sentence(**kwargs):  # noqa: ANN001
        captured_tts.append(dict(kwargs))

    monkeypatch.setattr("host_brain.app.chat_agent._post_speak_sentence", _fake_post_speak_sentence)

    expected_narrative = "I really did miss you, and hearing you again makes me happy."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more direct about reunion warmth",
            "desire: let the spoken reply sound softly relieved",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
        ],
        expected_narrative=expected_narrative,
        tts_text_ja="会いたかったし、大好きだよ。",
    )

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/chat",
        json={
            "message": "I am back. Say something gentle to me.",
            "session_id": f"session-http-prod-asr-{uuid.uuid4().hex[:8]}",
            "reply_channel": "ASR",
            "suppress_edge_tts": False,
            "suppress_edge_vts": True,
            "mood_state": "tender",
            "emotion_tag": "LOVING",
            "emotion_intensity": 0.82,
            "emotion_context": {
                "mood_state": "tender",
                "emotion_tag": "LOVING",
                "emotion_intensity": 0.82,
                "emotion_range_hint": "sound relieved and affectionate",
                "vad_vector": {
                    "valence": 0.72,
                    "arousal": 0.41,
                    "dominance": 0.12,
                    "ts_ms": now_ms,
                },
                "emotion_ts_ms": now_ms,
            },
        },
    )
    time.sleep(0.05)

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    commit = _parse_http_sse_commit(response.text)
    assert captured["model_id"] == "mock-soul-runtime"
    assert commit["decision"] == "APPROVE"
    assert commit["narrative"] == expected_narrative
    assert commit["audit_refs"]["path"] == "chat_agent:direct"
    assert len(captured_tts) == 1
    assert captured_tts[0]["text"] == "会いたかったし、大好きだよ。"
    assert captured_tts[0]["emotion_tag"] == "LOVING"
    assert captured_tts[0]["utterance_id"].startswith("utt_")
