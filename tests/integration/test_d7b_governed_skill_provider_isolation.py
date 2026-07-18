from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import time
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import (
    GOVERNED_HOST_RUNTIME_INACTIVE,
    GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE,
    GovernedHostAdapterError,
    build_governed_host_runtime as _build_governed_host_runtime,
)
from host_brain.runtime.degradation_authority import LegacyModuleGlobalDegradationAdapter


def build_governed_host_runtime(**kwargs):  # type: ignore[no-untyped-def]
    kwargs.setdefault(
        "degradation_capability_port",
        LegacyModuleGlobalDegradationAdapter(),
    )
    return _build_governed_host_runtime(**kwargs)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.capability_plane.broker import CapabilityBroker
from host_brain.capability_plane.policies import DescriptorCapabilityPolicy
from host_brain.composition.life import PROCESS_LIFE_PROVIDER
from host_brain.composition.task_runtime import build_process_task_runtime_container
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.governor.tokens import TokenService
from host_brain.skills.memory_registry import MemorySkillProvider
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.system_registry import SystemSkillProvider


_RUNTIME_MARKERS = {
    "A": "D7B_RUNTIME_ALPHA_ONLY",
    "B": "D7B_RUNTIME_BETA_ONLY",
}


@dataclass(frozen=True)
class _ResourceManager:
    marker: str
    calls: list[tuple[str, bool]]

    def get_status(self) -> dict[str, Any]:
        self.calls.append((self.marker, False))
        return {"running": True, "runtime_marker": self.marker}


class _EpisodicStore:
    def __init__(self, marker: str, calls: list[tuple[str, str]]) -> None:
        self._marker = marker
        self._calls = calls

    def search(self, *, query: str, top_k: int, threshold: float) -> list[dict[str, Any]]:
        self._calls.append((self._marker, query))
        return [
            {
                "memory_id": f"memory-{self._marker}",
                "content": f"episodic-{self._marker}",
                "score": max(0.99, threshold),
                "source": f"episodic-{self._marker}",
            }
        ][:top_k]


class _SemanticRecaller:
    def __init__(self, marker: str, calls: list[tuple[str, str]]) -> None:
        self._marker = marker
        self._calls = calls

    async def recall(self, query: str, **_kwargs: Any) -> SimpleNamespace:
        self._calls.append((self._marker, query))
        # An empty semantic result forces the explicit hybrid path to consume
        # the runtime-bound EpisodicStore as well.
        return SimpleNamespace(hits=[])


def _skill_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(SystemSkillProvider())
    registry.register_decorated(MemorySkillProvider())
    return registry


def _intent(
    *,
    marker: str,
    skill_id: str,
    seq: int,
    ts_ms: int,
) -> dict[str, Any]:
    request_id = f"request-d7b-{marker.lower()}-{skill_id}"
    args: dict[str, Any]
    if skill_id == "resource_manager_status":
        args = {"create_if_missing": False}
    else:
        args = {
            "query": f"query-{marker}",
            "memory_type": "all",
            "search_scope": "hybrid",
            "top_k": 5,
            "threshold": 0.6,
        }
    return {
        "schema_version": "Natsume.intent.v1",
        "id": f"intent-d7b-{marker.lower()}-{skill_id}",
        "request_id": request_id,
        "ts_ms": ts_ms,
        "session_id": f"session-d7b-{marker.lower()}",
        "seq": seq,
        "nonce": f"nonce-d7b-{marker.lower()}-{skill_id}",
        "type": "query",
        "action": skill_id,
        "target": "host",
        "route": "skill",
        "target_id": skill_id,
        "task": f"execute {skill_id} for runtime {marker}",
        "person_id": f"person-d7b-{marker.lower()}",
        "principal_id": f"person-d7b-{marker.lower()}",
        "args": args,
    }


def _approve(
    engine: IntentDecisionEngine,
    intent: dict[str, Any],
) -> dict[str, Any]:
    result = engine.evaluate_intent(
        raw_intent=intent,
        request_id=intent["request_id"],
        current_ms=intent["ts_ms"],
        governance_snapshot={"trust_level": 100, "energy_level": 100},
    )
    assert result["decision"] == "APPROVE", result
    return result


async def _execute(
    runtime: Any,
    *,
    intent: dict[str, Any],
    governor: dict[str, Any],
) -> dict[str, Any]:
    events = [
        event
        async for event in runtime.execute_stream(
            request_body=SimpleNamespace(),
            request_id=intent["request_id"],
            session_id=intent["session_id"],
            ts_ms=intent["ts_ms"],
            intent_draft=intent,
            governor_result=governor,
            person_id=intent["person_id"],
            sanitized_message=intent["task"],
            emotion_context={"emotion_tag": "NEUTRAL"},
            render_target_lang="zh",
        )
    ]
    assert events
    assert events[-1]["execution_receipt"]["status"] == "ACCEPTED", events[-1]
    return events[-1]


@pytest.mark.asyncio
async def test_two_governed_runtimes_keep_resource_and_memory_snapshots_isolated(
    tmp_path,
) -> None:
    PROCESS_LIFE_PROVIDER.clear()
    resource_calls: list[tuple[str, bool]] = []
    episodic_calls: list[tuple[str, str]] = []
    semantic_calls: list[tuple[str, str]] = []
    runtimes: dict[str, Any] = {}
    containers: dict[str, Any] = {}
    ledgers: dict[str, SQLiteLedger] = {}
    brokers: dict[str, CapabilityBroker] = {}
    engines: dict[str, IntentDecisionEngine] = {}
    base_ts = int(time.time() * 1000)

    try:
        for runtime_key, marker in _RUNTIME_MARKERS.items():
            db_path = tmp_path / f"governed-provider-isolation-{runtime_key.lower()}.sqlite3"
            ledger = SQLiteLedger(str(db_path), check_same_thread=False)
            container = build_process_task_runtime_container(
                ledger=ledger,
                canonical_db_path=db_path,
            )
            broker = CapabilityBroker(policy=DescriptorCapabilityPolicy())
            registry = _skill_registry()
            token_service = TokenService(
                approval_ttl_ms=600_000,
                confirm_ttl_ms=600_000,
                undo_ttl_ms=600_000,
            )
            manager = _ResourceManager(marker, resource_calls)
            episodic = _EpisodicStore(marker, episodic_calls)
            semantic = _SemanticRecaller(marker, semantic_calls)

            runtime = build_governed_host_runtime(
                task_runtime_container=container,
                ledger=ledger,
                skill_registry=registry,
                token_service=token_service,
                capability_broker=broker,
                model_attempt_service=object(),
                production_authority_verifier=lambda **_kwargs: True,
                resource_manager=manager,
                episodic_store=episodic,
                semantic_recaller=semantic,
                runtime_router=CanonicalRuntimeRouter(),
                owner_id=f"d7b-provider-isolation-{runtime_key.lower()}",
                allow_uncomposed_acceptance_effects=True,
            )
            await runtime.activate()
            runtimes[runtime_key] = runtime
            containers[runtime_key] = container
            ledgers[runtime_key] = ledger
            brokers[runtime_key] = broker
            engines[runtime_key] = IntentDecisionEngine(
                ledger=ledger,
                token_service=token_service,
                registry=registry,
            )

        intents = {
            ("A", "resource_manager_status"): _intent(
                marker=_RUNTIME_MARKERS["A"],
                skill_id="resource_manager_status",
                seq=1,
                ts_ms=base_ts + 1,
            ),
            ("B", "memory_search"): _intent(
                marker=_RUNTIME_MARKERS["B"],
                skill_id="memory_search",
                seq=1,
                ts_ms=base_ts + 2,
            ),
            ("A", "memory_search"): _intent(
                marker=_RUNTIME_MARKERS["A"],
                skill_id="memory_search",
                seq=2,
                ts_ms=base_ts + 3,
            ),
            ("B", "resource_manager_status"): _intent(
                marker=_RUNTIME_MARKERS["B"],
                skill_id="resource_manager_status",
                seq=2,
                ts_ms=base_ts + 4,
            ),
        }

        results: dict[tuple[str, str], dict[str, Any]] = {}
        for marker, skill_id in (
            ("A", "resource_manager_status"),
            ("B", "memory_search"),
            ("A", "memory_search"),
        ):
            intent = intents[(marker, skill_id)]
            results[(marker, skill_id)] = await _execute(
                runtimes[marker],
                intent=intent,
                governor=_approve(engines[marker], intent),
            )

        await runtimes["A"].aclose()
        intent = intents[("B", "resource_manager_status")]
        results[("B", "resource_manager_status")] = await _execute(
            runtimes["B"],
            intent=intent,
            governor=_approve(engines["B"], intent),
        )

        with pytest.raises(GovernedHostAdapterError) as inactive:
            intent = _intent(
                marker=_RUNTIME_MARKERS["A"],
                skill_id="resource_manager_status",
                seq=3,
                ts_ms=base_ts + 5,
            )
            await _execute(
                runtimes["A"],
                intent=intent,
                governor={"decision": "APPROVE"},
            )
        assert inactive.value.reason_code == GOVERNED_HOST_RUNTIME_INACTIVE

        assert resource_calls == [
            (_RUNTIME_MARKERS["A"], False),
            (_RUNTIME_MARKERS["B"], False),
        ]
        assert semantic_calls == [
            (_RUNTIME_MARKERS["B"], f"query-{_RUNTIME_MARKERS['B']}"),
            (_RUNTIME_MARKERS["A"], f"query-{_RUNTIME_MARKERS['A']}"),
        ]
        assert episodic_calls == semantic_calls
        for runtime_key, skill_id in results:
            narrative = results[(runtime_key, skill_id)]["narrative"]
            own_marker = _RUNTIME_MARKERS[runtime_key]
            other_marker = _RUNTIME_MARKERS["B" if runtime_key == "A" else "A"]
            assert own_marker in narrative
            assert other_marker not in narrative
    finally:
        for runtime in runtimes.values():
            await runtime.aclose()
        for container in containers.values():
            await container.aclose()
        for broker in brokers.values():
            broker.close()
        for ledger in ledgers.values():
            ledger.close()
        PROCESS_LIFE_PROVIDER.clear()


@pytest.mark.asyncio
async def test_governed_memory_search_without_backend_fails_before_skill_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    def forbidden_low_level_execution(**_kwargs):  # noqa: ANN003, ANN201
        pytest.fail("missing Skill dependency reached the low-level executor")

    monkeypatch.setattr(
        "host_brain.app.governed_task_adapter.execute_frozen_governed_skill",
        forbidden_low_level_execution,
    )
    PROCESS_LIFE_PROVIDER.clear()
    db_path = tmp_path / "governed-missing-memory-backend.sqlite3"
    ledger = SQLiteLedger(str(db_path), check_same_thread=False)
    container = None
    runtime = None
    broker = None
    try:
        container = build_process_task_runtime_container(
            ledger=ledger,
            canonical_db_path=db_path,
        )
        broker = CapabilityBroker(policy=DescriptorCapabilityPolicy())
        registry = _skill_registry()
        token_service = TokenService(
            approval_ttl_ms=600_000,
            confirm_ttl_ms=600_000,
            undo_ttl_ms=600_000,
        )
        engine = IntentDecisionEngine(
            ledger=ledger,
            token_service=token_service,
            registry=registry,
        )
        runtime = build_governed_host_runtime(
            task_runtime_container=container,
            ledger=ledger,
            skill_registry=registry,
            token_service=token_service,
            capability_broker=broker,
            model_attempt_service=object(),
            production_authority_verifier=lambda **_kwargs: True,
            resource_manager=None,
            episodic_store=None,
            semantic_recaller=None,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id="d7b-missing-memory-backend",
            allow_uncomposed_acceptance_effects=True,
        )
        await runtime.activate()
        intent = _intent(
            marker="D7B_RUNTIME_MISSING_BACKEND_ONLY",
            skill_id="memory_search",
            seq=1,
            ts_ms=int(time.time() * 1000),
        )
        intent["args"]["search_scope"] = "recent_episode"
        events = [
            event
            async for event in runtime.execute_stream(
                request_body=SimpleNamespace(),
                request_id=intent["request_id"],
                session_id=intent["session_id"],
                ts_ms=intent["ts_ms"],
                intent_draft=intent,
                governor_result=_approve(engine, intent),
                person_id=intent["person_id"],
                sanitized_message=intent["task"],
                emotion_context={"emotion_tag": "NEUTRAL"},
                render_target_lang="zh",
            )
        ]

        assert events
        receipt = events[-1]["execution_receipt"]
        assert receipt["accepted"] is False
        assert receipt["status"] == "FAILED"
        assert receipt["reason_code"] == GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE
        assert ledger.skill_exec_log == []
    finally:
        if runtime is not None:
            await runtime.aclose()
        if container is not None:
            await container.aclose()
        if broker is not None:
            broker.close()
        ledger.close()
        PROCESS_LIFE_PROVIDER.clear()
