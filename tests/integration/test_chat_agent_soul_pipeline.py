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
from edge_client.gateway.models import MsgType, Platform, UnifiedMessage, UnifiedReply
from edge_client.gateway.normalizer import MessageNormalizer
from edge_client.gateway.outbound import OutboundDispatcher
from edge_client.gateway.router import GatewayRouter
from edge_client.gateway.server import create_gateway_app
from edge_client.rpc.event_reporter import EdgeEventReporter
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


def _wait_for(condition, *, timeout_s: float = 1.0, interval_s: float = 0.02) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if condition():
            return
        time.sleep(interval_s)
    raise AssertionError("condition not satisfied before timeout")


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
    import host_brain.app.system_handler as system_handler

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")
    monkeypatch.setenv("HOST_DISPATCH_LLM_ROUTE_ENABLED", "false")
    system_handler._EDGE_ASR_BRIDGE_RECENT.clear()
    system_handler._EDGE_ASR_BRIDGE_LAST_TEXT.clear()
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
    system_handler._EDGE_ASR_BRIDGE_RECENT.clear()
    system_handler._EDGE_ASR_BRIDGE_LAST_TEXT.clear()
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


def _patch_chat_agent_runtime_keep_post_process(
    monkeypatch: pytest.MonkeyPatch,
    *,
    expected_prompt_markers_by_call: list[list[str]],
    narratives_by_call: list[str],
) -> dict[str, Any]:
    captured: dict[str, Any] = {"calls": []}

    async def _fake_chunks(**kwargs):  # noqa: ANN001
        call_index = len(captured["calls"])
        prompt_text = str(kwargs.get("prompt_text") or "")
        system_prompt = str(kwargs.get("system_prompt") or "")
        assert system_prompt.strip()
        markers = expected_prompt_markers_by_call[min(call_index, len(expected_prompt_markers_by_call) - 1)]
        for marker in markers:
            assert marker in prompt_text
        captured["calls"].append(dict(kwargs))
        narrative = narratives_by_call[min(call_index, len(narratives_by_call) - 1)]
        yield json.dumps(
            {
                "narrative": {
                    "text": narrative,
                },
            },
            ensure_ascii=False,
        )

    def _passthrough_response_intelligence(**kwargs):  # noqa: ANN001
        return kwargs["narrative"], {"reflection": {"enabled": False}}

    monkeypatch.setattr("host_brain.integrations.llm_helpers.astream_llm_chunks", _fake_chunks)
    monkeypatch.setattr(
        "host_brain.app.intelligent_runtime.apply_response_intelligence",
        _passthrough_response_intelligence,
    )
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


class _GatewayWebhookAdapter:
    platform_id = Platform.WECHAT

    def __init__(self) -> None:
        self.parsed_payloads: list[dict[str, Any]] = []
        self.sent_replies: list[UnifiedReply] = []

    def parse_inbound(self, raw_payload: dict[str, Any]) -> UnifiedMessage | None:
        payload = dict(raw_payload)
        self.parsed_payloads.append(payload)
        return UnifiedMessage(
            msg_id=str(payload.get("msg_id") or f"wx-msg-{uuid.uuid4().hex[:8]}"),
            platform=Platform.WECHAT,
            from_id=str(payload.get("from_id") or "wxid_user"),
            to_id=str(payload.get("to_id") or "wxid_bot"),
            conversation_id=str(payload.get("conversation_id") or "conv_default"),
            msg_type=MsgType(str(payload.get("msg_type") or "text").strip().lower() or "text"),
            content=str(payload.get("content") or ""),
            ts_ms=int(payload.get("ts_ms") or time.time() * 1000),
            is_group=bool(payload.get("is_group", False)),
            sender_name=str(payload.get("sender_name") or "").strip() or None,
            media_url=str(payload.get("media_url") or "").strip() or None,
            reply_to_msg_id=str(payload.get("reply_to_msg_id") or "").strip() or None,
            raw_extra=dict(payload.get("raw_extra") or {}),
        )

    def send_reply(self, reply: UnifiedReply) -> bool:
        self.sent_replies.append(reply)
        return True

    def health(self) -> dict[str, Any]:
        return {"platform": self.platform_id.value, "status": "ok"}


class _HostInputReporterBridge:
    def __init__(self, *, host_client: TestClient) -> None:
        self._host_client = host_client
        self.sent_payloads: list[dict[str, Any]] = []

    def _send(self, payload: dict[str, Any], _source: Any) -> Any:
        self.sent_payloads.append(dict(payload))
        response = self._host_client.post("/v1/host/input", json=payload)
        data = response.json()
        return SimpleNamespace(
            success=(response.status_code == 200 and bool(data.get("success"))),
            cached=False,
            status_code=response.status_code,
            detail=data,
        )


def _patch_http_lifespan_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ledger: Any,
    degraded_startup: bool = False,
) -> dict[str, Any]:
    capture: dict[str, Any] = {
        "startup_reports": [],
        "bootstrap_logs": [],
        "heartbeat_started": [],
        "heartbeat_stopped": 0,
        "registered_loops": [],
        "activity_started": 0,
        "activity_stopped": 0,
        "activity_set": 0,
        "bond_queue_drained": 0,
        "bond_queue_shutdown": 0,
        "world_sync_calls": 0,
        "scheduler_started": 0,
        "scheduler_task_count_at_start": 0,
        "scheduler_task_ids_at_start": [],
        "tool_registrations": 0,
        "handler_registrations": 0,
    }

    class _FakeStateArbiter:
        def __init__(self) -> None:
            self._sources: dict[str, Any] = {}

        def register_source(self, source: Any, provider: Any) -> None:
            self._sources[str(source)] = provider

    class _FakeRegistry:
        def __init__(self) -> None:
            self._tasks: dict[str, Any] = {}

        def get(self, task_id: str) -> Any:
            return self._tasks.get(task_id)

        def register(self, task: Any) -> None:
            self._tasks[str(getattr(task, "task_id", f"task_{len(self._tasks)}"))] = task

    class _FakeScheduler:
        def __init__(self) -> None:
            self.tasks: list[Any] = []

        def register(self, task: Any) -> None:
            self.tasks.append(task)

    class _FakeSchedulerHealthReporter:
        def __init__(self, registry: Any) -> None:
            self.registry = registry

    class _FakeNatsumeScheduler:
        def __init__(self, **_kwargs: Any) -> None:
            self.registry = _kwargs.get("registry")

        def start(self) -> None:
            capture["scheduler_started"] += 1
            task_ids = list(getattr(self.registry, "_tasks", {}).keys())
            capture["scheduler_task_count_at_start"] = len(task_ids)
            capture["scheduler_task_ids_at_start"] = task_ids

    class _FakeActivityMonitor:
        def __init__(self, **_kwargs: Any) -> None:
            self._gaming_interval = 30
            self._idle_interval = 45

        def start(self) -> None:
            capture["activity_started"] += 1

        def stop(self) -> None:
            capture["activity_stopped"] += 1

        def notify_user_interaction(self) -> None:
            return None

    class _FakeModeTransitionService:
        def __init__(self, **_kwargs: Any) -> None:
            return None

    class _FakeBondQueue:
        def drain(self, timeout_s: float = 0.0) -> bool:
            _ = timeout_s
            capture["bond_queue_drained"] += 1
            return True

        def shutdown(self) -> None:
            capture["bond_queue_shutdown"] += 1

    class _FakeToolCatalog:
        def register(self, _tool_meta: Any) -> None:
            capture["tool_registrations"] += 1

    class _FakeToolBroker:
        def register_handler(self, _tool_id: str, _handler: Any) -> None:
            capture["handler_registrations"] += 1

    class _FakeSkillRegistry:
        def get(self, skill_id: str) -> Any:
            return SimpleNamespace(handler=lambda _args, _ctx, _skill_id=skill_id: {"skill_id": _skill_id})

    async def _probe_edge_rpc_health() -> dict[str, Any]:
        if degraded_startup:
            return {
                "available": False,
                "status": "DOWN",
                "detail": "edge probe unavailable during startup test",
                "latency_ms": 0,
            }
        return {
            "available": True,
            "status": "OK",
            "detail": "edge probe ok",
            "latency_ms": 7,
            "base_url": "http://127.0.0.1:19000",
        }

    def _build_edge_dependency_probe_statuses(probe: dict[str, Any]) -> dict[str, Any]:
        return {
            "edge.rpc.gateway": {
                "available": bool(probe.get("available", False)),
                "detail": str(probe.get("detail") or ""),
            }
        }

    def _build_startup_report(**kwargs: Any) -> dict[str, Any]:
        runtime_statuses = dict(kwargs.get("runtime_statuses") or {})
        dependency_probes = dict(kwargs.get("dependency_probes") or {})
        degraded = any(str(value).upper().startswith(("DEGRADED", "FAIL")) for value in runtime_statuses.values())
        if any(not bool((row or {}).get("available", True)) for row in dependency_probes.values()):
            degraded = True
        report = {
            "status": ("DEGRADED" if degraded else "OK"),
            "summary": {"degraded": degraded},
            "runtime_statuses": runtime_statuses,
            "dependency_probes": dependency_probes,
            "request_id": kwargs.get("request_id"),
            "ts_ms": kwargs.get("ts_ms"),
        }
        capture["startup_reports"].append(report)
        return report

    def _emit_runtime_bootstrap_logs(*, startup_report: dict[str, Any]) -> None:
        capture["bootstrap_logs"].append(dict(startup_report))

    def _register_main_loop(loop: Any) -> None:
        capture["registered_loops"].append(loop)

    def _set_activity_monitor(monitor: Any) -> None:
        capture["activity_set"] += 1
        deps._activity_monitor_ref = monitor

    def _start_heartbeat(interval_sec: float = 30.0) -> object:
        capture["heartbeat_started"].append(interval_sec)
        return object()

    async def _stop_heartbeat() -> None:
        capture["heartbeat_stopped"] += 1

    def _sync_world_canon_index_on_startup(**_kwargs: Any) -> str:
        capture["world_sync_calls"] += 1
        return "OK (synced=0, changed=0)"

    fake_state_arbiter = _FakeStateArbiter()
    fake_registry = _FakeRegistry()
    fake_scheduler = _FakeScheduler()
    fake_tool_catalog = _FakeToolCatalog()
    fake_tool_broker = _FakeToolBroker()
    fake_skill_registry = _FakeSkillRegistry()
    fake_bond_queue = _FakeBondQueue()

    monkeypatch.setattr("host_brain.app.fastapi_app._ledger", ledger, raising=False)
    monkeypatch.setattr("host_brain.app.dependencies._ledger", ledger, raising=False)
    monkeypatch.setattr("host_brain.app.http_hooks._sync_world_canon_index_on_startup", _sync_world_canon_index_on_startup)
    monkeypatch.setattr("host_brain.app.http_hooks.build_startup_report", _build_startup_report)
    monkeypatch.setattr("host_brain.app.http_hooks.emit_runtime_bootstrap_logs", _emit_runtime_bootstrap_logs)
    monkeypatch.setattr("host_brain.app.dependencies.get_recall_pack_builder", lambda: object())
    monkeypatch.setattr("host_brain.app.dependencies.get_memory_guard", lambda: object())
    monkeypatch.setattr("host_brain.app.dependencies.get_state_arbiter", lambda: fake_state_arbiter)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_distillation_engine",
        lambda: SimpleNamespace(run_batch=lambda: None),
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_semantic_recaller", lambda: object())
    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: object())
    monkeypatch.setattr("host_brain.app.dependencies.probe_edge_rpc_health", _probe_edge_rpc_health)
    monkeypatch.setattr(
        "host_brain.app.dependencies.build_edge_dependency_probe_statuses",
        _build_edge_dependency_probe_statuses,
    )
    monkeypatch.setattr("host_brain.app.automation.task_scheduler.get_scheduler", lambda: fake_scheduler)
    monkeypatch.setattr(
        "host_brain.memory.long_term_distiller.make_distillation_task_config",
        lambda: SimpleNamespace(task_id="long_term_distill_test", trigger_mode="CRON"),
    )
    monkeypatch.setattr(
        "host_brain.scheduler.task_registry.SchedulerTaskRegistry.get_instance",
        classmethod(lambda cls: fake_registry),
    )
    monkeypatch.setattr("host_brain.scheduler.health.SchedulerHealthReporter", _FakeSchedulerHealthReporter)
    monkeypatch.setattr("host_brain.scheduler.scheduler.NatsumeScheduler", _FakeNatsumeScheduler)
    monkeypatch.setattr(
        "host_brain.app.vision.proactive_push_client.build_proactive_push_client",
        lambda: SimpleNamespace(push=lambda _message: None),
    )
    monkeypatch.setattr("host_brain.audit.retention.RetentionPolicy", lambda: SimpleNamespace())
    monkeypatch.setattr("host_brain.app.dependencies.get_tool_catalog", lambda: fake_tool_catalog)
    monkeypatch.setattr("host_brain.app.dependencies.get_tool_broker", lambda: fake_tool_broker)
    monkeypatch.setattr("host_brain.app.dependencies.get_skill_registry", lambda: fake_skill_registry)
    monkeypatch.setattr(
        "host_brain.skills.personality_registry.build_personality_skill_registry",
        lambda: fake_skill_registry,
    )
    monkeypatch.setattr(
        "host_brain.skills.scheduler_registry.build_scheduler_skill_registry",
        lambda: fake_skill_registry,
    )
    monkeypatch.setattr("host_brain.channels.vts.register_main_loop", _register_main_loop)
    monkeypatch.setattr("host_brain.core.activity_monitor.ActivityMonitor", _FakeActivityMonitor)
    monkeypatch.setattr("host_brain.core.mode_transition.ModeTransitionService", _FakeModeTransitionService)
    monkeypatch.setattr("host_brain.app.dependencies.set_activity_monitor", _set_activity_monitor)
    monkeypatch.setattr("host_brain.observability.service_health_heartbeat.start_heartbeat", _start_heartbeat)
    monkeypatch.setattr("host_brain.observability.service_health_heartbeat.stop_heartbeat", _stop_heartbeat)
    monkeypatch.setattr("host_brain.app.dependencies.get_bond_update_queue", lambda: fake_bond_queue)

    return capture


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


def _capture_input_route_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import host_brain.app.input_routes as input_routes

    captured: dict[str, Any] = {}
    original = input_routes._run_top_agent_pipeline

    async def _wrapped(**kwargs):  # noqa: ANN001
        result = await original(**kwargs)
        captured.update(result)
        return result

    monkeypatch.setattr(input_routes, "_run_top_agent_pipeline", _wrapped)
    return captured


def _capture_input_route_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    def _record(**kwargs):  # noqa: ANN001
        captured.append(dict(kwargs))

    monkeypatch.setattr("host_brain.app.input_routes._log_input_event", _record)
    return captured


def _patch_shared_host_ledger(monkeypatch: pytest.MonkeyPatch, ledger: Any) -> None:
    monkeypatch.setattr("host_brain.app.fastapi_app._ledger", ledger, raising=False)
    monkeypatch.setattr("host_brain.app.dependencies._ledger", ledger, raising=False)
    from host_brain.app.obs_routes_core import set_ledger_provider as _set_obs_ledger_provider

    _set_obs_ledger_provider(lambda: ledger)


def _build_edge_events_transport(*, host_client: TestClient):
    def _transport(events: list[dict[str, Any]]) -> dict[str, Any]:
        response = host_client.post("/v1/edge/events", json={"events": events})
        assert response.status_code == 200, response.text
        return response.json()

    return _transport


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
def test_http_chat_route_multi_turn_writeback_rehydrates_next_round_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from host_brain.memory.episodic import EpisodicStore
    from host_brain.memory.memory_worth_evaluator import DECISION_WRITE, MemoryWorthResult
    from host_brain.persona.inner_world import OfflineInnerWorld
    from tests.fakes.chroma_client import FakeChromaClient

    monkeypatch.setenv("HOST_STREAM_REFLECTION_BUFFER_ENABLED", "false")
    monkeypatch.setenv("HOST_STREAM_TTS_ENABLED", "false")
    monkeypatch.setenv("HOST_TTS_SENTENCE_STREAM_ENABLED", "false")

    persona_user_id = "persona-user-http-readback"
    current_ts = [1_000]
    bond_engine = BondStageEngine()
    inner_world = OfflineInnerWorld(state_dir=str(tmp_path / "inner_world"))
    ledger = SQLiteLedger(db_path=tmp_path / "audit.sqlite3", check_same_thread=False)
    store = EpisodicStore(
        ledger=ledger,
        chroma_client=FakeChromaClient(collection_profiles={"episodic": 3}),
        embedding_fn=lambda _text: ([0.1, 0.2, 0.3], "fake-embed"),
        embedding_model_id_hint="fake-embed",
        embedding_provider_hint="test",
    )

    class _MemoryWorthEvaluator:
        def evaluate(self, *_args, **_kwargs):  # noqa: ANN001
            return MemoryWorthResult(
                decision=DECISION_WRITE,
                worth_score=0.91,
                tags=["new_info", "continuity"],
                reason="integration_test_forced_write",
            )

    class _PredictiveState:
        def record_emotion(self, *_args, **_kwargs):  # noqa: ANN001, ANN003
            return None

        def predict_current(self, *_args, **_kwargs):  # noqa: ANN001, ANN003
            return SimpleNamespace(confidence=0.0, as_dict=lambda: {})

    class _MetaLoop:
        def record(self, *_args, **_kwargs):  # noqa: ANN001, ANN003
            return None

    class _MemoryGuard:
        def check_episodic_write(self, _record):  # noqa: ANN001
            return None

        def aggregate_emotion_tags(self, _session_id, _tags):  # noqa: ANN001
            return None

    class _GraphNode:
        def as_dict(self) -> dict[str, Any]:
            return {"memory_id": "mem-node"}

    class _GraphWalk:
        def __init__(self) -> None:
            self.chain = ["mem-node"]

        def as_dict(self) -> dict[str, Any]:
            return {"chain": list(self.chain)}

    class _MemoryGraph:
        def add_memory(self, **_kwargs):  # noqa: ANN003
            return _GraphNode()

        def walk(self, *_args, **_kwargs):  # noqa: ANN001, ANN003
            return _GraphWalk()

    class _ConsolidationDecision:
        should_trigger = False
        memory_count_24h = 0

        def as_dict(self) -> dict[str, Any]:
            return {
                "should_trigger": False,
                "memory_count_24h": 0,
                "reason_code": "NOT_NEEDED",
            }

    class _ConsolidationTrigger:
        def on_memory_written(self, **_kwargs):  # noqa: ANN003
            return None

        def check(self) -> _ConsolidationDecision:
            return _ConsolidationDecision()

    class _BondWriter:
        @staticmethod
        def classify_interaction(*_args, **_kwargs):  # noqa: ANN001, ANN003
            return "NOOP"

        def __init__(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return None

        def apply_update(self, **_kwargs):  # noqa: ANN003
            return None

    async def _noop_vts(**_kwargs):  # noqa: ANN001
        return None

    async def _immediate_to_thread(func, /, *args, **kwargs):  # noqa: ANN001
        return func(*args, **kwargs)

    def _fake_now_ms() -> int:
        return current_ts[0]

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=bond_engine,
    )
    _disable_dispatch_plan(monkeypatch)
    _patch_top_agent_http_runtime(monkeypatch)
    captured = _patch_chat_agent_runtime_keep_post_process(
        monkeypatch,
        expected_prompt_markers_by_call=[
            [],
            [
                "- offline_hours: 6.5",
                "- offline_awakening_hint:",
                "- offline_anticipation_topics:",
                "那个项目",
            ],
        ],
        narratives_by_call=[
            "我们继续把那个项目收口。",
            "好，我们接着把那个项目收口。",
        ],
    )

    _patch_shared_host_ledger(monkeypatch, ledger)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    monkeypatch.setattr("host_brain.app.dependencies._episodic_store", store, raising=False)
    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_store", lambda: store)
    monkeypatch.setattr("host_brain.app.dependencies.get_memory_worth_evaluator", lambda: _MemoryWorthEvaluator())
    monkeypatch.setattr("host_brain.app.dependencies.get_predictive_state_engine", lambda: _PredictiveState())
    monkeypatch.setattr("host_brain.app.dependencies.get_meta_learning_loop", lambda: _MetaLoop())
    monkeypatch.setattr("host_brain.app.dependencies.get_memory_guard", lambda: _MemoryGuard())
    monkeypatch.setattr("host_brain.app.dependencies.get_episodic_memory_graph", lambda: _MemoryGraph())
    monkeypatch.setattr("host_brain.app.dependencies.get_consolidation_trigger", lambda: _ConsolidationTrigger())
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_distillation_engine",
        lambda: SimpleNamespace(run_batch=lambda: SimpleNamespace(status="OK", processed_count=0)),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_bond_update_queue",
        lambda: SimpleNamespace(enqueue=lambda _payload: {"status": "QUEUED"}),
    )
    monkeypatch.setattr("host_brain.bond.bond_dynamics.BondDynamicsWriter", _BondWriter)
    monkeypatch.setattr("host_brain.app.chat_handler.resolve_person_id", lambda *_args, **_kwargs: persona_user_id)
    monkeypatch.setattr("host_brain.app.top_agent.resolve_person_id", lambda *_args, **_kwargs: persona_user_id)
    monkeypatch.setattr("host_brain.app.chat_context_builder.get_canonical_bond_user_id", lambda: persona_user_id)
    monkeypatch.setattr("host_brain.bond.user_identity.get_canonical_bond_user_id", lambda: persona_user_id)
    monkeypatch.setattr("host_brain.bond.user_identity.resolve_bond_user_id", lambda *_args, **_kwargs: persona_user_id)
    monkeypatch.setattr("host_brain.persona.inner_world.get_inner_world", lambda: inner_world)
    monkeypatch.setattr("host_brain.persona.inner_world._now_ms", _fake_now_ms)
    monkeypatch.setattr("host_brain.airlock.quarantine.classify_ingress", lambda _text: "FACT")
    monkeypatch.setattr("host_brain.app.vts_emotion_driver._drive_stream_vts_emotion", _noop_vts)
    monkeypatch.setattr("host_brain.app.top_agent.asyncio.to_thread", _immediate_to_thread)

    client = TestClient(fastapi_app)
    try:
        response1 = client.post(
            "/v1/chat",
            json={
                "message": "那个项目今天还没说完，我们继续收口吧。",
                "session_id": f"session-http-writeback-{uuid.uuid4().hex[:8]}",
                "reply_channel": "WEB",
                "suppress_edge_tts": True,
                "suppress_edge_vts": True,
            },
        )

        assert response1.status_code == 200
        commit1 = _parse_http_sse_commit(response1.text)
        assert commit1["decision"] == "APPROVE"
        assert commit1["narrative"] == "我们继续把那个项目收口。"

        _wait_for(
            lambda: len(
                [
                    row for row in ledger.memory_ops_log
                    if row.get("op_kind") == "WRITE" and row.get("status") == "SUCCESS"
                ]
            ) >= 1,
            timeout_s=2.0,
        )
        _wait_for(
            lambda: bool(inner_world.get_state(persona_user_id)["anticipation_topics"]),
            timeout_s=2.0,
        )
        state_after_first = inner_world.get_state(persona_user_id)
        assert state_after_first["anticipation_topics"]
        assert "那个项目" in state_after_first["anticipation_topics"][0]

        current_ts[0] += int(6.5 * 3600 * 1000)

        response2 = client.post(
            "/v1/chat",
            json={
                "message": "我回来了，继续那个项目。",
                "session_id": f"session-http-readback-{uuid.uuid4().hex[:8]}",
                "reply_channel": "WEB",
                "suppress_edge_tts": True,
                "suppress_edge_vts": True,
            },
        )

        assert response2.status_code == 200
        commit2 = _parse_http_sse_commit(response2.text)
        assert commit2["decision"] == "APPROVE"
        assert commit2["narrative"] == "好，我们接着把那个项目收口。"

        _wait_for(
            lambda: len(
                [
                    row for row in ledger.memory_ops_log
                    if row.get("op_kind") == "WRITE" and row.get("status") == "SUCCESS"
                ]
            ) >= 2,
            timeout_s=2.0,
        )

        assert len(captured["calls"]) == 2
        second_prompt = str(captured["calls"][1]["prompt_text"] or "")
        assert "- offline_hours: 6.5" in second_prompt
        assert "- offline_awakening_hint:" in second_prompt
        assert "- offline_anticipation_topics:" in second_prompt
        assert "那个项目" in second_prompt

        write_rows = [
            row for row in ledger.memory_ops_log
            if row.get("op_kind") == "WRITE" and row.get("status") == "SUCCESS"
        ]
        assert len(write_rows) == 2
        assert all(any(str(ref).startswith("chat://request/") for ref in row.get("evidence_refs") or []) for row in write_rows)
    finally:
        client.close()
        ledger.close()


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
def test_host_input_gateway_route_keeps_retrieval_recall_soul_pipeline_and_delivers_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://host-input-gateway/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more grounded when replying through the gateway ingress path",
            "desire": "keep the gateway reply concise but still rooted in shared memory",
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
        reason="host_input_gateway_growth",
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
                    "content": "you once said my quiet words made you feel safe",
                    "snippet": "you once said my quiet words made you feel safe",
                    "evidence_ref": "evd://episodic/host-input/1",
                    "source_ref": "memory://episodic/host-input/1",
                    "score": 0.91,
                    "confidence": 0.91,
                },
                {
                    "kind": "world_canon",
                    "content": "Gateway reunion replies should stay sincere and not overstate certainty.",
                    "snippet": "Gateway reunion replies should stay sincere and not overstate certainty.",
                    "citation": "canon://relationship/gateway-reunion",
                    "evidence_ref": "evd://world/host-input/1",
                    "source_ref": "canon://relationship/gateway-reunion",
                    "score": 0.87,
                    "confidence": 0.87,
                },
            ],
        )

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "gentle_gateway_affection",
            },
            {
                "kind": "bond_graph",
                "content": "the relationship is warming and ready for a gentle gateway reply",
                "snippet": "the relationship is warming and ready for a gentle gateway reply",
                "evidence_ref": "evd://bond/host-input/1",
                "source_ref": "bond://graph/host-input/1",
                "score": 0.88,
                "confidence": 0.88,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "Gateway reunion replies should stay sincere and not overstate certainty.",
            "episodic_vector": "shared gateway memory reached the final prompt",
        },
        capture=recall_capture,
    )
    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_fake_retrieval_with_context,
        state_arbiter_fn=_fake_state_arbiter_with_bond,
        recall_pack_builder=recall_builder,
    )

    expected_narrative = "I still remember the quiet warmth between us, and I want to answer you gently."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more grounded when replying through the gateway ingress path",
            "desire: keep the gateway reply concise but still rooted in shared memory",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "Gateway reunion replies should stay sincere and not overstate certainty.",
            "EPISODIC_VECTOR_RECALL:",
            "shared gateway memory reached the final prompt",
            "you once said my quiet words made you feel safe",
        ],
        expected_narrative=expected_narrative,
    )

    gateway_replies: list[dict[str, Any]] = []

    async def _fake_post_gateway_reply(**kwargs):  # noqa: ANN001
        gateway_replies.append(dict(kwargs))
        return SimpleNamespace(success=True, status="OK", latency_ms=18, reason_code=None, detail="")

    monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_gateway_reply", _fake_post_gateway_reply)

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/host/input",
        json={
            "source": "GATEWAY",
            "person_id": user_id,
            "platform": "wechat",
            "conversation_id": f"conv-host-input-{uuid.uuid4().hex[:8]}",
            "from_id": "wxid_user_a",
            "msg_id": f"msg-{uuid.uuid4().hex[:6]}",
            "msg_type": "text",
            "content": "I came back. Can you answer gently through this gateway?",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "ACCEPTED"
    _wait_for(lambda: len(gateway_replies) == 1 and captured.get("model_id") == "mock-soul-runtime")

    assert gateway_replies[0]["platform"] == "wechat"
    assert gateway_replies[0]["conversation_id"].startswith("conv-host-input-")
    assert gateway_replies[0]["content"] == expected_narrative
    assert gateway_replies[0]["msg_type"] == "text"
    assert recall_capture["task_type"] == "chat"
    assert "world_canon" in recall_capture["recall_results"]
    assert "bond_graph" in recall_capture["recall_results"]
    assert "episodic_vector" in recall_capture["recall_results"]


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_host_input_gateway_route_retrieval_init_failure_still_delivers_soul_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://host-input-init-fail/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more stable when the gateway ingress path starts without retrieval",
            "desire": "keep the answer soft even if retrieval initialization fails early",
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
        reason="host_input_gateway_init_fail_growth",
        ts_ms=old_ts,
    )

    def _bad_retrieval(**_kwargs):  # noqa: ANN001
        raise RuntimeError("retrieval init boom")

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "steady_gateway_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "the bond still feels safe even when retrieval never starts",
                "snippet": "the bond still feels safe even when retrieval never starts",
                "evidence_ref": "evd://bond/host-input/init-fail/1",
                "source_ref": "bond://graph/host-input/init-fail/1",
                "score": 0.86,
                "confidence": 0.86,
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

    expected_narrative = "Even if remembering does not start right away, I still want to answer you gently."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more stable when the gateway ingress path starts without retrieval",
            "desire: keep the answer soft even if retrieval initialization fails early",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "检索状态: SKIPPED",
            "the bond still feels safe even when retrieval never starts",
        ],
        expected_narrative=expected_narrative,
    )

    gateway_replies: list[dict[str, Any]] = []

    async def _fake_post_gateway_reply(**kwargs):  # noqa: ANN001
        gateway_replies.append(dict(kwargs))
        return SimpleNamespace(success=True, status="OK", latency_ms=21, reason_code=None, detail="")

    monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_gateway_reply", _fake_post_gateway_reply)

    client = TestClient(fastapi_app)
    response = client.post(
        "/v1/host/input",
        json={
            "source": "GATEWAY",
            "person_id": user_id,
            "platform": "wechat",
            "conversation_id": f"conv-host-input-init-fail-{uuid.uuid4().hex[:8]}",
            "from_id": "wxid_user_b",
            "msg_id": f"msg-{uuid.uuid4().hex[:6]}",
            "msg_type": "text",
            "content": "I came back. Can you still answer softly if remembering starts slowly?",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "ACCEPTED"
    _wait_for(lambda: len(gateway_replies) == 1 and captured.get("model_id") == "mock-soul-runtime")

    assert gateway_replies[0]["platform"] == "wechat"
    assert gateway_replies[0]["conversation_id"].startswith("conv-host-input-init-fail-")
    assert gateway_replies[0]["content"] == expected_narrative
    assert gateway_replies[0]["msg_type"] == "text"
    assert recall_capture["task_type"] == "chat"
    assert recall_capture["recall_results"] == {
        "bond_graph": "the bond still feels safe even when retrieval never starts",
    }


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_edge_gateway_webhook_bridge_keeps_soul_pipeline_and_sends_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://edge-webhook-reunion/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more coherent when the reply begins from the edge gateway webhook",
            "desire": "keep the wechat reply gentle, grounded, and memory-backed",
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
        reason="edge_gateway_webhook_growth",
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
                    "content": "you once said my calm voice made the chat feel like home",
                    "snippet": "you once said my calm voice made the chat feel like home",
                    "evidence_ref": "evd://episodic/edge-webhook/1",
                    "source_ref": "memory://episodic/edge-webhook/1",
                    "score": 0.92,
                    "confidence": 0.92,
                },
                {
                    "kind": "world_canon",
                    "content": "WeChat reunion replies should stay sincere and softly grounded.",
                    "snippet": "WeChat reunion replies should stay sincere and softly grounded.",
                    "citation": "canon://relationship/wechat-reunion",
                    "evidence_ref": "evd://world/edge-webhook/1",
                    "source_ref": "canon://relationship/wechat-reunion",
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
                "capability_hint": "wechat_soft_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "the relationship is warming and ready for a soft wechat reply",
                "snippet": "the relationship is warming and ready for a soft wechat reply",
                "evidence_ref": "evd://bond/edge-webhook/1",
                "source_ref": "bond://graph/edge-webhook/1",
                "score": 0.89,
                "confidence": 0.89,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "WeChat reunion replies should stay sincere and softly grounded.",
            "episodic_vector": "shared wechat memory reached the final prompt",
        },
        capture=recall_capture,
    )
    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_fake_retrieval_with_context,
        state_arbiter_fn=_fake_state_arbiter_with_bond,
        recall_pack_builder=recall_builder,
    )

    expected_narrative = "I still remember the calm warmth between us, and I want this WeChat reply to feel gentle."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more coherent when the reply begins from the edge gateway webhook",
            "desire: keep the wechat reply gentle, grounded, and memory-backed",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "WeChat reunion replies should stay sincere and softly grounded.",
            "EPISODIC_VECTOR_RECALL:",
            "shared wechat memory reached the final prompt",
            "you once said my calm voice made the chat feel like home",
        ],
        expected_narrative=expected_narrative,
    )

    host_client = TestClient(fastapi_app)
    reporter = _HostInputReporterBridge(host_client=host_client)
    adapter = _GatewayWebhookAdapter()
    gateway_app = create_gateway_app(
        router=GatewayRouter(reporter=reporter),
        normalizer=MessageNormalizer(),
        adapters={"wechat": adapter},
        outbound=OutboundDispatcher({Platform.WECHAT: adapter}),
    )
    gateway_client = TestClient(gateway_app)

    async def _post_gateway_reply_via_local_app(**kwargs):  # noqa: ANN001
        response = gateway_client.post(
            "/v1/gateway/reply",
            json={
                "platform": kwargs.get("platform"),
                "conversation_id": kwargs.get("conversation_id"),
                "content": kwargs.get("content"),
                "msg_type": kwargs.get("msg_type", "text"),
                "wait_result": kwargs.get("wait_result", True),
                "timeout_ms": kwargs.get("timeout_ms", 12000),
            },
        )
        body = response.json()
        return SimpleNamespace(
            success=(response.status_code == 200 and str(body.get("status") or "").lower() == "sent"),
            status=str(body.get("status") or "failed").upper(),
            reason_code=None,
            detail=str(body.get("detail") or ""),
            latency_ms=0,
        )

    monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_gateway_reply", _post_gateway_reply_via_local_app)

    webhook_payload = {
        "msg_id": f"wx-msg-{uuid.uuid4().hex[:8]}",
        "from_id": "wxid_user_a",
        "to_id": "wxid_bot",
        "conversation_id": f"conv-edge-webhook-{uuid.uuid4().hex[:8]}",
        "content": "I came back through WeChat. Can you answer me gently?",
        "msg_type": "text",
        "sender_name": "Alice",
        "ts_ms": now_ms,
    }
    response = gateway_client.post("/v1/gateway/wechat/webhook", json=webhook_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    _wait_for(lambda: len(adapter.sent_replies) == 1 and captured.get("model_id") == "mock-soul-runtime")

    assert reporter.sent_payloads[0]["source"] == "GATEWAY"
    assert reporter.sent_payloads[0]["platform"] == "wechat"
    assert reporter.sent_payloads[0]["conversation_id"] == webhook_payload["conversation_id"]
    assert recall_capture["task_type"] == "chat"
    assert "world_canon" in recall_capture["recall_results"]
    assert "bond_graph" in recall_capture["recall_results"]
    assert "episodic_vector" in recall_capture["recall_results"]
    assert adapter.sent_replies[0].content == expected_narrative
    assert adapter.sent_replies[0].msg_type is MsgType.TEXT
    assert adapter.sent_replies[0].conversation_id == webhook_payload["conversation_id"]


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_edge_gateway_webhook_bridge_retrieval_init_failure_still_sends_soul_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://edge-webhook-init-fail/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more stable when the gateway bridge starts without retrieval",
            "desire": "keep the reply soft even if retrieval fails before the bridge settles",
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
        reason="edge_gateway_webhook_init_fail_growth",
        ts_ms=old_ts,
    )

    def _bad_retrieval(**_kwargs):  # noqa: ANN001
        raise RuntimeError("retrieval init boom")

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "steady_bridge_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "the bond still feels gentle even when the bridge starts without retrieval",
                "snippet": "the bond still feels gentle even when the bridge starts without retrieval",
                "evidence_ref": "evd://bond/edge-webhook/init-fail/1",
                "source_ref": "bond://graph/edge-webhook/init-fail/1",
                "score": 0.87,
                "confidence": 0.87,
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

    expected_narrative = "Even if remembering does not start yet, I still want to answer you softly."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more stable when the gateway bridge starts without retrieval",
            "desire: keep the reply soft even if retrieval fails before the bridge settles",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "检索状态: SKIPPED",
            "the bond still feels gentle even when the bridge starts without retrieval",
        ],
        expected_narrative=expected_narrative,
    )

    host_client = TestClient(fastapi_app)
    reporter = _HostInputReporterBridge(host_client=host_client)
    adapter = _GatewayWebhookAdapter()
    gateway_app = create_gateway_app(
        router=GatewayRouter(reporter=reporter),
        normalizer=MessageNormalizer(),
        adapters={"wechat": adapter},
        outbound=OutboundDispatcher({Platform.WECHAT: adapter}),
    )
    gateway_client = TestClient(gateway_app)

    async def _post_gateway_reply_via_local_app(**kwargs):  # noqa: ANN001
        response = gateway_client.post(
            "/v1/gateway/reply",
            json={
                "platform": kwargs.get("platform"),
                "conversation_id": kwargs.get("conversation_id"),
                "content": kwargs.get("content"),
                "msg_type": kwargs.get("msg_type", "text"),
                "wait_result": kwargs.get("wait_result", True),
                "timeout_ms": kwargs.get("timeout_ms", 12000),
            },
        )
        body = response.json()
        return SimpleNamespace(
            success=(response.status_code == 200 and str(body.get("status") or "").lower() == "sent"),
            status=str(body.get("status") or "failed").upper(),
            reason_code=None,
            detail=str(body.get("detail") or ""),
            latency_ms=0,
        )

    monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_gateway_reply", _post_gateway_reply_via_local_app)

    webhook_payload = {
        "msg_id": f"wx-msg-{uuid.uuid4().hex[:8]}",
        "from_id": "wxid_user_b",
        "to_id": "wxid_bot",
        "conversation_id": f"conv-edge-webhook-init-fail-{uuid.uuid4().hex[:8]}",
        "content": "I came back through WeChat. Can you still answer softly if remembering starts slowly?",
        "msg_type": "text",
        "sender_name": "Bob",
        "ts_ms": now_ms,
    }
    response = gateway_client.post("/v1/gateway/wechat/webhook", json=webhook_payload)

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    _wait_for(lambda: len(adapter.sent_replies) == 1 and captured.get("model_id") == "mock-soul-runtime")

    assert reporter.sent_payloads[0]["source"] == "GATEWAY"
    assert reporter.sent_payloads[0]["platform"] == "wechat"
    assert reporter.sent_payloads[0]["conversation_id"] == webhook_payload["conversation_id"]
    assert adapter.sent_replies[0].content == expected_narrative
    assert adapter.sent_replies[0].msg_type is MsgType.TEXT
    assert adapter.sent_replies[0].conversation_id == webhook_payload["conversation_id"]
    assert recall_capture["task_type"] == "chat"
    assert recall_capture["recall_results"] == {
        "bond_graph": "the bond still feels gentle even when the bridge starts without retrieval",
    }


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_fastapi_lifespan_wraps_edge_gateway_bridge_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")

    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    capture_lifespan = _patch_http_lifespan_runtime(
        monkeypatch,
        ledger=ledger,
        degraded_startup=False,
    )
    try:
        user_id = get_canonical_bond_user_id()
        now_ms = int(time.time() * 1000)
        old_ts = now_ms - 8 * 24 * 3600 * 1000

        engine = BondStageEngine()
        for index in range(3):
            engine.apply_event(
                user_id=user_id,
                update_kind="DEEP_TALK",
                evidence_refs=[f"evd://lifespan-webhook/{index}"],
                ts_ms=old_ts + index * 60_000,
                emotion_intensity=0.9,
            )

        _install_runtime_chain(
            monkeypatch,
            tmp_path=tmp_path,
            bond_engine=engine,
            growth_awareness={
                "recent_change": "more reliable when the full app lifecycle wraps the gateway bridge",
                "desire": "keep the startup-to-reply path gentle and memory-backed",
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
            reason="lifespan_gateway_bridge_growth",
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
                        "content": "you once said my calm replies made the whole bridge feel safe",
                        "snippet": "you once said my calm replies made the whole bridge feel safe",
                        "evidence_ref": "evd://episodic/lifespan/1",
                        "source_ref": "memory://episodic/lifespan/1",
                        "score": 0.92,
                        "confidence": 0.92,
                    },
                    {
                        "kind": "world_canon",
                        "content": "Lifecycle-wrapped gateway replies should stay sincere and grounded.",
                        "snippet": "Lifecycle-wrapped gateway replies should stay sincere and grounded.",
                        "citation": "canon://relationship/lifecycle-gateway",
                        "evidence_ref": "evd://world/lifespan/1",
                        "source_ref": "canon://relationship/lifecycle-gateway",
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
                    "capability_hint": "lifecycle_soft_reunion",
                },
                {
                    "kind": "bond_graph",
                    "content": "the bond stays warm across startup, ingress, and reply delivery",
                    "snippet": "the bond stays warm across startup, ingress, and reply delivery",
                    "evidence_ref": "evd://bond/lifespan/1",
                    "source_ref": "bond://graph/lifespan/1",
                    "score": 0.89,
                    "confidence": 0.89,
                },
            )

        recall_capture: dict[str, Any] = {}
        recall_builder = _CapturingTopAgentRecallPackBuilder(
            slots={
                "world_canon": "Lifecycle-wrapped gateway replies should stay sincere and grounded.",
                "episodic_vector": "shared lifecycle memory reached the prompt",
            },
            capture=recall_capture,
        )
        _patch_top_agent_http_runtime(
            monkeypatch,
            retrieval_fn=_fake_retrieval_with_context,
            state_arbiter_fn=_fake_state_arbiter_with_bond,
            recall_pack_builder=recall_builder,
        )

        expected_narrative = "I still remember the calm warmth between us, and I want this full-path reply to feel gentle."
        captured = _patch_chat_agent_runtime(
            monkeypatch,
            expected_prompt_markers=[
                "SELF_REFLECTION_PULSE:",
                "growth_awareness:",
                "recent_change: more reliable when the full app lifecycle wraps the gateway bridge",
                "desire: keep the startup-to-reply path gentle and memory-backed",
                "emotion_reunion_type: reunion",
                "BOND_AWARE_STYLE:",
                "Lifecycle-wrapped gateway replies should stay sincere and grounded.",
                "EPISODIC_VECTOR_RECALL:",
                "shared lifecycle memory reached the prompt",
                "you once said my calm replies made the whole bridge feel safe",
            ],
            expected_narrative=expected_narrative,
        )

        with TestClient(fastapi_app) as host_client:
            assert capture_lifespan["startup_reports"][-1]["status"] == "OK"
            assert capture_lifespan["startup_reports"][-1]["runtime_statuses"]["proactive_talk_scheduler"].startswith("OK")
            assert "proactive_talk_periodic[enabled]" in capture_lifespan["startup_reports"][-1]["runtime_statuses"]["personality_tick_registry"]
            assert "inner_world_tick[enabled]" in capture_lifespan["startup_reports"][-1]["runtime_statuses"]["personality_tick_registry"]
            assert capture_lifespan["heartbeat_started"] == [30.0]
            assert capture_lifespan["activity_started"] == 1
            assert capture_lifespan["activity_set"] == 1
            assert capture_lifespan["scheduler_started"] == 1
            assert {
                "proactive_talk_periodic",
                "inner_world_tick",
                "silence_tick",
                "body_sense_tick",
                "gaming_companion_tick",
                "emergent_growth_weekly",
            }.issubset(set(capture_lifespan["scheduler_task_ids_at_start"]))
            assert len(capture_lifespan["registered_loops"]) == 1

            reporter = _HostInputReporterBridge(host_client=host_client)
            adapter = _GatewayWebhookAdapter()
            gateway_app = create_gateway_app(
                router=GatewayRouter(reporter=reporter),
                normalizer=MessageNormalizer(),
                adapters={"wechat": adapter},
                outbound=OutboundDispatcher({Platform.WECHAT: adapter}),
            )
            gateway_client = TestClient(gateway_app)

            async def _post_gateway_reply_via_local_app(**kwargs):  # noqa: ANN001
                response = gateway_client.post(
                    "/v1/gateway/reply",
                    json={
                        "platform": kwargs.get("platform"),
                        "conversation_id": kwargs.get("conversation_id"),
                        "content": kwargs.get("content"),
                        "msg_type": kwargs.get("msg_type", "text"),
                        "wait_result": kwargs.get("wait_result", True),
                        "timeout_ms": kwargs.get("timeout_ms", 12000),
                    },
                )
                body = response.json()
                return SimpleNamespace(
                    success=(response.status_code == 200 and str(body.get("status") or "").lower() == "sent"),
                    status=str(body.get("status") or "failed").upper(),
                    reason_code=None,
                    detail=str(body.get("detail") or ""),
                    latency_ms=0,
                )

            monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_gateway_reply", _post_gateway_reply_via_local_app)

            response = gateway_client.post(
                "/v1/gateway/wechat/webhook",
                json={
                    "msg_id": f"wx-msg-{uuid.uuid4().hex[:8]}",
                    "from_id": "wxid_user_lifecycle",
                    "to_id": "wxid_bot",
                    "conversation_id": f"conv-lifecycle-{uuid.uuid4().hex[:8]}",
                    "content": "I came back. Can this full bridge still answer gently?",
                    "msg_type": "text",
                    "sender_name": "LifecycleUser",
                    "ts_ms": now_ms,
                },
            )

            assert response.status_code == 200
            assert response.json()["status"] == "accepted"
            _wait_for(lambda: len(adapter.sent_replies) == 1 and captured.get("model_id") == "mock-soul-runtime")
            assert reporter.sent_payloads[0]["source"] == "GATEWAY"
            assert adapter.sent_replies[0].content == expected_narrative
            assert recall_capture["task_type"] == "chat"

        assert capture_lifespan["heartbeat_stopped"] == 1
        assert capture_lifespan["activity_stopped"] == 1
        assert capture_lifespan["bond_queue_drained"] == 1
        assert capture_lifespan["bond_queue_shutdown"] == 1
        assert capture_lifespan["bootstrap_logs"]
    finally:
        ledger.close()


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_fastapi_lifespan_degraded_startup_still_allows_edge_gateway_bridge_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")

    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    capture_lifespan = _patch_http_lifespan_runtime(
        monkeypatch,
        ledger=ledger,
        degraded_startup=True,
    )
    try:
        user_id = get_canonical_bond_user_id()
        now_ms = int(time.time() * 1000)
        old_ts = now_ms - 8 * 24 * 3600 * 1000

        engine = BondStageEngine()
        for index in range(3):
            engine.apply_event(
                user_id=user_id,
                update_kind="DEEP_TALK",
                evidence_refs=[f"evd://lifespan-degraded/{index}"],
                ts_ms=old_ts + index * 60_000,
                emotion_intensity=0.9,
            )

        _install_runtime_chain(
            monkeypatch,
            tmp_path=tmp_path,
            bond_engine=engine,
            growth_awareness={
                "recent_change": "more stable even when startup already begins degraded",
                "desire": "keep the answer soft when startup probes and retrieval both wobble",
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
            reason="lifespan_degraded_gateway_growth",
            ts_ms=old_ts,
        )

        def _bad_retrieval(**_kwargs):  # noqa: ANN001
            raise RuntimeError("retrieval init boom")

        async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
            return (
                {
                    "bond_stage": "FRIEND",
                    "trend": "warming",
                    "capability_hint": "degraded_lifecycle_reunion",
                },
                {
                    "kind": "bond_graph",
                    "content": "the bond stays gentle even when startup is degraded and retrieval never starts",
                    "snippet": "the bond stays gentle even when startup is degraded and retrieval never starts",
                    "evidence_ref": "evd://bond/lifespan/degraded/1",
                    "source_ref": "bond://graph/lifespan/degraded/1",
                    "score": 0.87,
                    "confidence": 0.87,
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

        expected_narrative = "Even when startup and remembering both wobble, I still want to answer you softly."
        captured = _patch_chat_agent_runtime(
            monkeypatch,
            expected_prompt_markers=[
                "SELF_REFLECTION_PULSE:",
                "growth_awareness:",
                "recent_change: more stable even when startup already begins degraded",
                "desire: keep the answer soft when startup probes and retrieval both wobble",
                "emotion_reunion_type: reunion",
                "BOND_AWARE_STYLE:",
                "检索状态: SKIPPED",
                "the bond stays gentle even when startup is degraded and retrieval never starts",
            ],
            expected_narrative=expected_narrative,
        )

        with TestClient(fastapi_app) as host_client:
            assert capture_lifespan["startup_reports"][-1]["status"] == "DEGRADED"
            assert capture_lifespan["startup_reports"][-1]["runtime_statuses"]["proactive_talk_scheduler"].startswith("OK")
            assert "silence_tick[enabled]" in capture_lifespan["startup_reports"][-1]["runtime_statuses"]["personality_tick_registry"]
            assert capture_lifespan["scheduler_started"] == 1
            assert {
                "proactive_talk_periodic",
                "inner_world_tick",
                "silence_tick",
                "body_sense_tick",
                "gaming_companion_tick",
                "emergent_growth_weekly",
            }.issubset(set(capture_lifespan["scheduler_task_ids_at_start"]))
            reporter = _HostInputReporterBridge(host_client=host_client)
            adapter = _GatewayWebhookAdapter()
            gateway_app = create_gateway_app(
                router=GatewayRouter(reporter=reporter),
                normalizer=MessageNormalizer(),
                adapters={"wechat": adapter},
                outbound=OutboundDispatcher({Platform.WECHAT: adapter}),
            )
            gateway_client = TestClient(gateway_app)

            async def _post_gateway_reply_via_local_app(**kwargs):  # noqa: ANN001
                response = gateway_client.post(
                    "/v1/gateway/reply",
                    json={
                        "platform": kwargs.get("platform"),
                        "conversation_id": kwargs.get("conversation_id"),
                        "content": kwargs.get("content"),
                        "msg_type": kwargs.get("msg_type", "text"),
                        "wait_result": kwargs.get("wait_result", True),
                        "timeout_ms": kwargs.get("timeout_ms", 12000),
                    },
                )
                body = response.json()
                return SimpleNamespace(
                    success=(response.status_code == 200 and str(body.get("status") or "").lower() == "sent"),
                    status=str(body.get("status") or "failed").upper(),
                    reason_code=None,
                    detail=str(body.get("detail") or ""),
                    latency_ms=0,
                )

            monkeypatch.setattr("host_brain.channels.edge_rpc_client.post_gateway_reply", _post_gateway_reply_via_local_app)

            response = gateway_client.post(
                "/v1/gateway/wechat/webhook",
                json={
                    "msg_id": f"wx-msg-{uuid.uuid4().hex[:8]}",
                    "from_id": "wxid_user_degraded",
                    "to_id": "wxid_bot",
                    "conversation_id": f"conv-lifecycle-degraded-{uuid.uuid4().hex[:8]}",
                    "content": "I came back. Can you still answer softly when startup is already shaky?",
                    "msg_type": "text",
                    "sender_name": "DegradedUser",
                    "ts_ms": now_ms,
                },
            )

            assert response.status_code == 200
            assert response.json()["status"] == "accepted"
            _wait_for(lambda: len(adapter.sent_replies) == 1 and captured.get("model_id") == "mock-soul-runtime")
            assert reporter.sent_payloads[0]["source"] == "GATEWAY"
            assert adapter.sent_replies[0].content == expected_narrative
            assert recall_capture["recall_results"] == {
                "bond_graph": "the bond stays gentle even when startup is degraded and retrieval never starts",
            }

        assert capture_lifespan["heartbeat_stopped"] == 1
        assert capture_lifespan["activity_stopped"] == 1
        assert capture_lifespan["bond_queue_drained"] == 1
        assert capture_lifespan["bond_queue_shutdown"] == 1
        assert any(
            str(value).upper().startswith("DEGRADED")
            for value in capture_lifespan["startup_reports"][-1]["runtime_statuses"].values()
        )
    finally:
        ledger.close()


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


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_edge_events_asr_bridge_keeps_retrieval_recall_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setenv("HOST_STREAM_VTS_EMOTION_ENABLED", "false")
    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", "0")

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://edge-events-asr/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more coherent when the soul pipeline begins from edge ASR events",
            "desire": "keep ASR reunion answers warm, grounded, and memory-backed",
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
        reason="edge_events_asr_growth",
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
                    "content": "you once said hearing my voice again felt like coming home",
                    "snippet": "you once said hearing my voice again felt like coming home",
                    "evidence_ref": "evd://episodic/edge-events/1",
                    "source_ref": "memory://episodic/edge-events/1",
                    "score": 0.93,
                    "confidence": 0.93,
                },
                {
                    "kind": "world_canon",
                    "content": "ASR reunion replies should stay sincere and avoid overstating certainty.",
                    "snippet": "ASR reunion replies should stay sincere and avoid overstating certainty.",
                    "citation": "canon://relationship/asr-reunion",
                    "evidence_ref": "evd://world/edge-events/1",
                    "source_ref": "canon://relationship/asr-reunion",
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
                "capability_hint": "voice_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "the relationship is warming and ready for a careful spoken reunion",
                "snippet": "the relationship is warming and ready for a careful spoken reunion",
                "evidence_ref": "evd://bond/edge-events/1",
                "source_ref": "bond://graph/edge-events/1",
                "score": 0.89,
                "confidence": 0.89,
            },
        )

    recall_capture: dict[str, Any] = {}
    recall_builder = _CapturingTopAgentRecallPackBuilder(
        slots={
            "world_canon": "ASR reunion replies should stay sincere and avoid overstating certainty.",
            "episodic_vector": "shared ASR memory reached the final prompt",
        },
        capture=recall_capture,
    )
    _patch_top_agent_http_runtime(
        monkeypatch,
        retrieval_fn=_fake_retrieval_with_context,
        state_arbiter_fn=_fake_state_arbiter_with_bond,
        recall_pack_builder=recall_builder,
    )

    expected_narrative = "I still remember the warmth in your voice, and I want to answer you gently."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more coherent when the soul pipeline begins from edge ASR events",
            "desire: keep ASR reunion answers warm, grounded, and memory-backed",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "ASR reunion replies should stay sincere and avoid overstating certainty.",
            "EPISODIC_VECTOR_RECALL:",
            "shared ASR memory reached the final prompt",
            "you once said hearing my voice again felt like coming home",
        ],
        expected_narrative=expected_narrative,
    )

    pipeline_capture = _capture_input_route_pipeline(monkeypatch)
    input_events = _capture_input_route_events(monkeypatch)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_bond_update_queue",
        lambda: SimpleNamespace(enqueue=lambda _payload: {"status": "QUEUED"}),
    )

    client = TestClient(fastapi_app)
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/v1/edge/events",
        json={
            "events": [
                {
                    "event_type": "ASR_FINAL",
                    "source": "edge_client",
                    "request_id": f"req-edge-events-{suffix}",
                    "session_id": f"session-edge-events-{suffix}",
                    "ts_ms": now_ms,
                    "payload": {
                        "text": "I came back. Can you answer gently from the ASR path?",
                        "is_final": True,
                        "final": True,
                        "confidence": 0.95,
                        "seq": 7,
                        "platform": "wechat",
                        "conversation_id": f"conv-edge-events-{suffix}",
                        "from_id": "wxid_asr_user",
                        "msg_type": "text",
                        "emotion_tag": "LOVING",
                        "emotion_intensity": 0.82,
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["accepted"] == 1
    assert body["rejected"] == 0
    _wait_for(
        lambda: captured.get("model_id") == "mock-soul-runtime"
        and pipeline_capture.get("narrative") == expected_narrative
        and any(item.get("phase") == "COMPLETED" and item.get("source") == "ASR" for item in input_events)
    )

    assert pipeline_capture["decision"] == "APPROVE"
    assert pipeline_capture["commit_event"]["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert "world_canon" in recall_capture["recall_results"]
    assert "bond_graph" in recall_capture["recall_results"]
    assert "episodic_vector" in recall_capture["recall_results"]


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_edge_events_asr_bridge_retrieval_init_failure_still_keeps_soul_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setenv("HOST_STREAM_VTS_EMOTION_ENABLED", "false")
    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", "0")

    user_id = get_canonical_bond_user_id()
    now_ms = int(time.time() * 1000)
    old_ts = now_ms - 8 * 24 * 3600 * 1000

    engine = BondStageEngine()
    for index in range(3):
        engine.apply_event(
            user_id=user_id,
            update_kind="DEEP_TALK",
            evidence_refs=[f"evd://edge-events-init-fail/{index}"],
            ts_ms=old_ts + index * 60_000,
            emotion_intensity=0.9,
        )

    _install_runtime_chain(
        monkeypatch,
        tmp_path=tmp_path,
        bond_engine=engine,
        growth_awareness={
            "recent_change": "more stable when edge ASR bridge starts without retrieval",
            "desire": "keep the spoken reunion answer soft even if retrieval initialization fails",
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
        reason="edge_events_init_fail_growth",
        ts_ms=old_ts,
    )

    def _bad_retrieval(**_kwargs):  # noqa: ANN001
        raise RuntimeError("retrieval init boom")

    async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
        return (
            {
                "bond_stage": "FRIEND",
                "trend": "warming",
                "capability_hint": "steady_voice_reunion",
            },
            {
                "kind": "bond_graph",
                "content": "the bond still feels gentle even when spoken retrieval never starts",
                "snippet": "the bond still feels gentle even when spoken retrieval never starts",
                "evidence_ref": "evd://bond/edge-events/init-fail/1",
                "source_ref": "bond://graph/edge-events/init-fail/1",
                "score": 0.86,
                "confidence": 0.86,
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

    expected_narrative = "Even if remembering does not start, I still want to answer you softly."
    captured = _patch_chat_agent_runtime(
        monkeypatch,
        expected_prompt_markers=[
            "SELF_REFLECTION_PULSE:",
            "growth_awareness:",
            "recent_change: more stable when edge ASR bridge starts without retrieval",
            "desire: keep the spoken reunion answer soft even if retrieval initialization fails",
            "emotion_reunion_type: reunion",
            "BOND_AWARE_STYLE:",
            "the bond still feels gentle even when spoken retrieval never starts",
        ],
        expected_narrative=expected_narrative,
    )

    pipeline_capture = _capture_input_route_pipeline(monkeypatch)
    input_events = _capture_input_route_events(monkeypatch)
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_bond_update_queue",
        lambda: SimpleNamespace(enqueue=lambda _payload: {"status": "QUEUED"}),
    )

    client = TestClient(fastapi_app)
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/v1/edge/events",
        json={
            "events": [
                {
                    "event_type": "ASR_FINAL",
                    "source": "edge_client",
                    "request_id": f"req-edge-events-init-fail-{suffix}",
                    "session_id": f"session-edge-events-init-fail-{suffix}",
                    "ts_ms": now_ms,
                    "payload": {
                        "text": "I came back. Can you still answer softly if remembering starts slowly?",
                        "is_final": True,
                        "final": True,
                        "confidence": 0.93,
                        "seq": 9,
                        "platform": "wechat",
                        "conversation_id": f"conv-edge-events-init-fail-{suffix}",
                        "from_id": "wxid_asr_user_b",
                        "msg_type": "text",
                        "emotion_tag": "LOVING",
                        "emotion_intensity": 0.8,
                    },
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["accepted"] == 1
    assert body["rejected"] == 0
    _wait_for(
        lambda: captured.get("model_id") == "mock-soul-runtime"
        and pipeline_capture.get("narrative") == expected_narrative
        and any(item.get("phase") == "COMPLETED" and item.get("source") == "ASR" for item in input_events)
    )

    assert pipeline_capture["decision"] == "APPROVE"
    assert pipeline_capture["commit_event"]["audit_refs"]["path"] == "chat_agent:direct"
    assert recall_capture["task_type"] == "chat"
    assert recall_capture["recall_results"] == {
        "bond_graph": "the bond still feels gentle even when spoken retrieval never starts",
    }


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_edge_event_reporter_asr_bridge_exposes_trace_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setenv("HOST_STREAM_VTS_EMOTION_ENABLED", "false")
    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", "0")

    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    _patch_shared_host_ledger(monkeypatch, ledger)
    reporter: EdgeEventReporter | None = None

    try:
        user_id = get_canonical_bond_user_id()
        now_ms = int(time.time() * 1000)
        old_ts = now_ms - 8 * 24 * 3600 * 1000

        engine = BondStageEngine()
        for index in range(3):
            engine.apply_event(
                user_id=user_id,
                update_kind="DEEP_TALK",
                evidence_refs=[f"evd://edge-reporter-trace/{index}"],
                ts_ms=old_ts + index * 60_000,
                emotion_intensity=0.9,
            )

        _install_runtime_chain(
            monkeypatch,
            tmp_path=tmp_path,
            bond_engine=engine,
            growth_awareness={
                "recent_change": "more coherent when edge reporter and host trace stay aligned",
                "desire": "keep reported ASR reunion answers warm, grounded, and traceable",
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
            reason="edge_reporter_trace_growth",
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
                        "content": "you once said hearing from me again felt like a reunion worth keeping gentle",
                        "snippet": "you once said hearing from me again felt like a reunion worth keeping gentle",
                        "evidence_ref": "evd://episodic/edge-reporter/1",
                        "source_ref": "memory://episodic/edge-reporter/1",
                        "score": 0.91,
                        "confidence": 0.91,
                    },
                    {
                        "kind": "world_canon",
                        "content": "Reporter-driven reunion replies should stay sincere and softly grounded.",
                        "snippet": "Reporter-driven reunion replies should stay sincere and softly grounded.",
                        "citation": "canon://relationship/reporter-reunion",
                        "evidence_ref": "evd://world/edge-reporter/1",
                        "source_ref": "canon://relationship/reporter-reunion",
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
                    "capability_hint": "traceable_voice_reunion",
                },
                {
                    "kind": "bond_graph",
                    "content": "the relationship is warming and stays traceable through the ASR bridge",
                    "snippet": "the relationship is warming and stays traceable through the ASR bridge",
                    "evidence_ref": "evd://bond/edge-reporter/1",
                    "source_ref": "bond://graph/edge-reporter/1",
                    "score": 0.89,
                    "confidence": 0.89,
                },
            )

        recall_capture: dict[str, Any] = {}
        recall_builder = _CapturingTopAgentRecallPackBuilder(
            slots={
                "world_canon": "Reporter-driven reunion replies should stay sincere and softly grounded.",
                "episodic_vector": "shared reporter memory reached the final prompt",
            },
            capture=recall_capture,
        )
        _patch_top_agent_http_runtime(
            monkeypatch,
            retrieval_fn=_fake_retrieval_with_context,
            state_arbiter_fn=_fake_state_arbiter_with_bond,
            recall_pack_builder=recall_builder,
        )

        expected_narrative = "I still remember the gentle way we found each other again, and I want to answer softly."
        captured = _patch_chat_agent_runtime(
            monkeypatch,
            expected_prompt_markers=[
                "SELF_REFLECTION_PULSE:",
                "growth_awareness:",
                "recent_change: more coherent when edge reporter and host trace stay aligned",
                "desire: keep reported ASR reunion answers warm, grounded, and traceable",
                "emotion_reunion_type: reunion",
                "BOND_AWARE_STYLE:",
                "Reporter-driven reunion replies should stay sincere and softly grounded.",
                "EPISODIC_VECTOR_RECALL:",
                "shared reporter memory reached the final prompt",
            ],
            expected_narrative=expected_narrative,
        )

        pipeline_capture = _capture_input_route_pipeline(monkeypatch)
        input_events = _capture_input_route_events(monkeypatch)
        monkeypatch.setattr(
            "host_brain.app.dependencies.get_bond_update_queue",
            lambda: SimpleNamespace(enqueue=lambda _payload: {"status": "QUEUED"}),
        )

        host_client = TestClient(fastapi_app)
        reporter = EdgeEventReporter(
            transport_fn=_build_edge_events_transport(host_client=host_client),
            max_retry=1,
        )
        request_id = f"req-edge-reporter-trace-{uuid.uuid4().hex[:8]}"
        session_id = f"session-edge-reporter-trace-{uuid.uuid4().hex[:8]}"
        report = reporter.report(
            [
                {
                    "event_type": "ASR_FINAL",
                    "ts_ms": now_ms,
                    "request_id": request_id,
                    "session_id": session_id,
                    "source": "edge_client",
                    "direction": "EDGE_TO_HOST",
                    "seq": 1,
                    "status": "RECEIVED",
                    "stage": "S_ASR_CAPTURE",
                    "payload": {
                        "text": "I came back. Can you answer me gently and keep the trace coherent?",
                        "is_final": True,
                        "final": True,
                        "confidence": 0.94,
                        "platform": "wechat",
                        "conversation_id": session_id,
                        "from_id": "wxid_reporter_user",
                        "msg_type": "text",
                        "emotion_tag": "LOVING",
                        "emotion_intensity": 0.83,
                    },
                }
            ]
        )

        assert report.success is True
        assert report.accepted == 1
        _wait_for(
            lambda: captured.get("model_id") == "mock-soul-runtime"
            and pipeline_capture.get("narrative") == expected_narrative
            and any(item.get("phase") == "COMPLETED" and item.get("source") == "ASR" for item in input_events)
        )

        trace_resp = host_client.post("/observability/trace", json={"request_id": request_id})
        assert trace_resp.status_code == 200
        trace_payload = trace_resp.json()
        assert trace_payload["request_id"] == request_id
        assert trace_payload["timeline_total"] >= 1
        assert trace_payload["sources"].get("voice_event_log", 0) >= 1

        detailed_trace_resp = host_client.get(f"/obs/trace/{request_id}")
        assert detailed_trace_resp.status_code == 200
        detailed_trace = detailed_trace_resp.json()
        assert detailed_trace["request_id"] == request_id
        assert detailed_trace["node_count"] >= 1
        assert recall_capture["task_type"] == "chat"
        assert "world_canon" in recall_capture["recall_results"]
        assert "bond_graph" in recall_capture["recall_results"]
        assert "episodic_vector" in recall_capture["recall_results"]
    finally:
        if reporter is not None:
            reporter._retry_executor.shutdown(wait=False, cancel_futures=True)
        ledger.close()


@pytest.mark.skipif(not _HAS_TEST_CLIENT, reason="fastapi.testclient not available")
def test_edge_event_reporter_degraded_asr_bridge_still_exposes_trace_endpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from host_brain.audit.sqlite_ledger import SQLiteLedger

    _enable_prod_edge_env(monkeypatch)
    monkeypatch.setenv("HOST_INPUT_DISPATCH_IN_THREAD", "false")
    monkeypatch.setenv("HOST_STREAM_VTS_EMOTION_ENABLED", "false")
    monkeypatch.setenv("HOST_EDGE_EVENT_ASR_AUTO_BRIDGE_DEDUP_WINDOW_MS", "0")

    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    _patch_shared_host_ledger(monkeypatch, ledger)
    reporter: EdgeEventReporter | None = None

    try:
        user_id = get_canonical_bond_user_id()
        now_ms = int(time.time() * 1000)
        old_ts = now_ms - 8 * 24 * 3600 * 1000

        engine = BondStageEngine()
        for index in range(3):
            engine.apply_event(
                user_id=user_id,
                update_kind="DEEP_TALK",
                evidence_refs=[f"evd://edge-reporter-trace-degraded/{index}"],
                ts_ms=old_ts + index * 60_000,
                emotion_intensity=0.9,
            )

        _install_runtime_chain(
            monkeypatch,
            tmp_path=tmp_path,
            bond_engine=engine,
            growth_awareness={
                "recent_change": "more stable when edge reporter starts before retrieval is ready",
                "desire": "keep the traceable spoken reunion answer soft during degraded retrieval",
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
            reason="edge_reporter_trace_degraded_growth",
            ts_ms=old_ts,
        )

        def _bad_retrieval(**_kwargs):  # noqa: ANN001
            raise RuntimeError("retrieval init boom")

        async def _fake_state_arbiter_with_bond(**_kwargs):  # noqa: ANN001
            return (
                {
                    "bond_stage": "FRIEND",
                    "trend": "warming",
                    "capability_hint": "steady_traceable_reunion",
                },
                {
                    "kind": "bond_graph",
                    "content": "the bond still feels gentle and traceable even when retrieval never starts",
                    "snippet": "the bond still feels gentle and traceable even when retrieval never starts",
                    "evidence_ref": "evd://bond/edge-reporter/degraded/1",
                    "source_ref": "bond://graph/edge-reporter/degraded/1",
                    "score": 0.86,
                    "confidence": 0.86,
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

        expected_narrative = "Even if remembering is not ready, I still want to answer you softly and stay here."
        captured = _patch_chat_agent_runtime(
            monkeypatch,
            expected_prompt_markers=[
                "SELF_REFLECTION_PULSE:",
                "growth_awareness:",
                "recent_change: more stable when edge reporter starts before retrieval is ready",
                "desire: keep the traceable spoken reunion answer soft during degraded retrieval",
                "emotion_reunion_type: reunion",
                "BOND_AWARE_STYLE:",
                "the bond still feels gentle and traceable even when retrieval never starts",
            ],
            expected_narrative=expected_narrative,
        )

        pipeline_capture = _capture_input_route_pipeline(monkeypatch)
        input_events = _capture_input_route_events(monkeypatch)
        monkeypatch.setattr(
            "host_brain.app.dependencies.get_bond_update_queue",
            lambda: SimpleNamespace(enqueue=lambda _payload: {"status": "QUEUED"}),
        )

        host_client = TestClient(fastapi_app)
        reporter = EdgeEventReporter(
            transport_fn=_build_edge_events_transport(host_client=host_client),
            max_retry=1,
        )
        request_id = f"req-edge-reporter-trace-degraded-{uuid.uuid4().hex[:8]}"
        session_id = f"session-edge-reporter-trace-degraded-{uuid.uuid4().hex[:8]}"
        report = reporter.report(
            [
                {
                    "event_type": "ASR_FINAL",
                    "ts_ms": now_ms,
                    "request_id": request_id,
                    "session_id": session_id,
                    "source": "edge_client",
                    "direction": "EDGE_TO_HOST",
                    "seq": 1,
                    "status": "RECEIVED",
                    "stage": "S_ASR_CAPTURE",
                    "payload": {
                        "text": "I came back. Can you still answer softly if remembering is slow?",
                        "is_final": True,
                        "final": True,
                        "confidence": 0.92,
                        "platform": "wechat",
                        "conversation_id": session_id,
                        "from_id": "wxid_reporter_user_b",
                        "msg_type": "text",
                        "emotion_tag": "LOVING",
                        "emotion_intensity": 0.8,
                    },
                }
            ]
        )

        assert report.success is True
        assert report.accepted == 1
        _wait_for(
            lambda: captured.get("model_id") == "mock-soul-runtime"
            and pipeline_capture.get("narrative") == expected_narrative
            and any(item.get("phase") == "COMPLETED" and item.get("source") == "ASR" for item in input_events)
        )

        trace_resp = host_client.post("/observability/trace", json={"request_id": request_id})
        assert trace_resp.status_code == 200
        trace_payload = trace_resp.json()
        assert trace_payload["request_id"] == request_id
        assert trace_payload["timeline_total"] >= 1
        assert trace_payload["sources"].get("voice_event_log", 0) >= 1

        detailed_trace_resp = host_client.get(f"/obs/trace/{request_id}")
        assert detailed_trace_resp.status_code == 200
        detailed_trace = detailed_trace_resp.json()
        assert detailed_trace["request_id"] == request_id
        assert detailed_trace["node_count"] >= 1
        assert recall_capture["task_type"] == "chat"
        assert recall_capture["recall_results"] == {
            "bond_graph": "the bond still feels gentle and traceable even when retrieval never starts",
        }
    finally:
        if reporter is not None:
            reporter._retry_executor.shutdown(wait=False, cancel_futures=True)
        ledger.close()
