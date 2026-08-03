from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from host_brain.app import host_obs_source_composition as source_composition
from host_brain.app.host_obs_read_routes import (
    HostObsReadSourceUnavailable,
    create_host_obs_read_router,
)
from host_brain.app.host_obs_source_composition import (
    HostObsSourceEpoch,
    HostObsSourceRuntimePorts,
    build_host_obs_read_ports,
)


NOW = 1_750_000_000_000


class _ModeService:
    def get_current_status(self):
        return {
            "sys_mode": "focus",
            "ts_ms": NOW - 10,
            "last_interaction_ts_ms": NOW - 20,
            "objective": "private-mode-objective",
        }

    def get_history(self, *, limit: int):
        assert limit == 100
        return [
            {
                "event_id": "mode-event-1",
                "from_mode": "normal",
                "to_mode": "focus",
                "reason": "manual",
                "ts_ms": NOW - 30,
            }
        ]


class _Telemetry:
    def snapshot(self, **_kwargs):
        return {
            "timestamp": NOW - 40,
            "detected_total": 1,
            "objective": "private-gap-objective",
        }


class _Forge:
    def runtime_match_stats(self):
        return {"timestamp": NOW - 41, "counters": {"total": 1}}

    def runtime_tuning_stats(self, *, limit: int):
        assert limit == 200
        return {"timestamp": NOW - 42, "summary": {}}

    def runtime_quality_stats(self, *, limit: int):
        assert limit == 200
        return {"timestamp": NOW - 43, "summary": {}}


class _SemanticRecaller:
    def public_runtime_stats(self, *, observed_at_ms: int):
        return {
            "status": "MOCK_READY",
            "use_mock": True,
            "mock_doc_count": 2,
            "timestamp": observed_at_ms,
        }


class _Scorecards:
    def list_all(self):
        return []


class _ClockAdvancingScorecards:
    def __init__(self, clock_values: list[int]) -> None:
        self._clock_values = clock_values

    def list_all(self):
        # Mirrors the production scorecard provider, which reads the clock
        # while materializing its read-only evidence.
        self._clock_values.append(self._clock_values[-1] + 1)
        return []


class _Ledger:
    def get_latest_capability_forge_proposals(self, *, limit: int):
        assert limit == 200
        return [
            {
                "payload": {
                    "proposal_id": "proposal-1",
                    "created_ts_ms": NOW - 50,
                    "updated_ts_ms": NOW - 45,
                    "status": "PENDING_APPROVAL",
                    "governor_gate": {
                        "approval_token_ref": "approval::private-secret"
                    },
                }
            }
        ]


@dataclass
class _Clock:
    def health(self):
        return {"last_tick_ms": NOW - 60, "active": True}


@dataclass
class _Scheduler:
    clock: _Clock = field(default_factory=_Clock)

    def health(self):
        return {"status": "OK"}


def _runtime(**overrides) -> HostObsSourceRuntimePorts:
    epoch = HostObsSourceEpoch("host-composition-test")
    values = {
        "mode_service_provider": lambda: _ModeService(),
        "capability_gap_telemetry_provider": lambda: _Telemetry(),
        "capability_forge_provider": lambda: _Forge(),
        "semantic_recaller_provider": lambda: _SemanticRecaller(),
        "capability_scorecard_provider": lambda: _Scorecards(),
        "degradation_state_provider": lambda: {
            "status": "READY",
            "updated_at_ms": NOW - 5,
        },
        "pentest_environment_provider": lambda: {
            "lab_mode": False,
            "allow_exploit": False,
            "domain_mode": "domain",
        },
        "ledger_provider": lambda: _Ledger(),
        "scheduler_container_provider": lambda: _Scheduler(),
        "tribe_state_reader": lambda: {
            "executive": {
                "updated_at_ms": NOW - 70,
                "state": {"running": True},
            }
        },
        "source_epoch_provider": lambda: epoch,
        "clock_ms": lambda: NOW,
    }
    values.update(overrides)
    return HostObsSourceRuntimePorts(**values)


def test_build_is_lazy_and_each_reader_uses_only_explicit_runtime_ports() -> None:
    calls: list[str] = []
    runtime = _runtime(
        mode_service_provider=lambda: calls.append("mode") or _ModeService(),
        capability_gap_telemetry_provider=(
            lambda: calls.append("telemetry") or _Telemetry()
        ),
        capability_forge_provider=lambda: calls.append("forge") or _Forge(),
        semantic_recaller_provider=(
            lambda: calls.append("semantic") or _SemanticRecaller()
        ),
        capability_scorecard_provider=(
            lambda: calls.append("scorecards") or _Scorecards()
        ),
        degradation_state_provider=lambda: calls.append("degradation")
        or {"status": "READY", "updated_at_ms": NOW - 5},
        pentest_environment_provider=lambda: calls.append("pentest") or {},
        ledger_provider=lambda: calls.append("ledger") or _Ledger(),
        scheduler_container_provider=(
            lambda: calls.append("scheduler") or _Scheduler()
        ),
        tribe_state_reader=lambda: calls.append("tribe")
        or {"executive": {"updated_at_ms": NOW - 70}},
    )

    ports = build_host_obs_read_ports(
        runtime,
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )
    assert calls == []

    assert ports.mode_source_reader().evidence_ts_ms == NOW - 10
    assert ports.capability_forge_source_reader().evidence_ts_ms == NOW - 40
    assert ports.runtime_source_reader().evidence_ts_ms == NOW - 60
    assert calls == [
        "mode",
        "telemetry",
        "forge",
        "ledger",
        "semantic",
        "scorecards",
        "degradation",
        "pentest",
        "scheduler",
        "tribe",
    ]


def test_host_source_payload_is_public_before_it_crosses_the_route_boundary() -> None:
    ports = build_host_obs_read_ports(
        _runtime(),
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )

    mode = ports.mode_source_reader().as_dict()
    capability = ports.capability_forge_source_reader().as_dict()
    rendered = repr({"mode": mode, "capability": capability})

    assert "private-mode-objective" not in rendered
    assert "private-gap-objective" not in rendered
    assert "approval::private-secret" not in rendered
    assert mode["status"]["sys_mode"] == "focus"
    assert capability["proposals"][0]["proposal_id"] == "proposal-1"


def test_missing_runtime_service_fails_closed_without_fallback_construction() -> None:
    ports = build_host_obs_read_ports(
        _runtime(mode_service_provider=lambda: None),
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )

    with pytest.raises(HostObsReadSourceUnavailable) as raised:
        ports.mode_source_reader()

    assert raised.value.reason_code == "HOST_OBS_MODE_RUNTIME_NOT_READY"


def test_forge_failure_does_not_block_semantic_or_governance_sources() -> None:
    ports = build_host_obs_read_ports(
        _runtime(
            capability_gap_telemetry_provider=lambda: None,
            capability_forge_provider=lambda: None,
            ledger_provider=lambda: None,
        ),
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )

    with pytest.raises(HostObsReadSourceUnavailable) as forge_unavailable:
        ports.capability_forge_source_reader()
    semantic = ports.semantic_recall_source_reader()
    governance = ports.governance_source_reader()

    assert forge_unavailable.value.reason_code == (
        "HOST_OBS_CAPABILITY_RUNTIME_NOT_READY"
    )
    assert semantic.stats["status"] == "MOCK_READY"
    assert semantic.evidence_ts_ms == NOW
    assert governance.governance["schema_version"] == (
        "Natsume.governance.snapshot.v1"
    )
    assert governance.evidence_ts_ms == NOW

    app = FastAPI()
    app.include_router(create_host_obs_read_router(ports))
    client = TestClient(app)
    headers = {"X-Internal-Token": "dedicated-token"}
    assert client.get(
        "/internal/obs-sources/capability-forge",
        headers=headers,
    ).status_code == 503
    assert client.get(
        "/internal/obs-sources/semantic-recall",
        headers=headers,
    ).status_code == 200
    assert client.get(
        "/internal/obs-sources/governance",
        headers=headers,
    ).status_code == 200


def test_child_failures_degrade_compat_fields_without_blocking_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_child_failure():
        raise RuntimeError("child authority failed")

    monkeypatch.setattr(
        source_composition,
        "_governance_snapshot",
        lambda *_args, **_kwargs: raise_child_failure(),
    )

    ports = build_host_obs_read_ports(
        _runtime(
            semantic_recaller_provider=raise_child_failure,
        ),
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )

    capability = ports.capability_forge_source_reader()

    assert capability.gap["detected_total"] == 1
    assert capability.proposals[0]["proposal_id"] == "proposal-1"
    assert capability.semantic_recall == {
        "available": False,
        "reason_code": "HOST_SEMANTIC_RECALL_RUNTIME_NOT_READY",
        "status": "SOURCE_UNAVAILABLE",
        "timestamp": NOW,
    }
    assert capability.governance == {
        "available": False,
        "reason_code": "HOST_OBS_GOVERNANCE_RUNTIME_NOT_READY",
        "status": "SOURCE_UNAVAILABLE",
        "timestamp": NOW,
    }

    app = FastAPI()
    app.include_router(create_host_obs_read_router(ports))
    response = TestClient(app).get(
        "/internal/obs-sources/capability-forge",
        headers={"X-Internal-Token": "dedicated-token"},
    )
    assert response.status_code == 200
    assert response.json()["semantic_recall"]["status"] == "SOURCE_UNAVAILABLE"
    assert response.json()["governance"]["status"] == "SOURCE_UNAVAILABLE"


def test_governance_input_provider_failure_degrades_without_503() -> None:
    def raise_provider_failure():
        raise RuntimeError("scorecard provider failed")

    ports = build_host_obs_read_ports(
        _runtime(capability_scorecard_provider=raise_provider_failure),
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )

    snapshot = ports.governance_source_reader()
    assert snapshot.governance["schema_version"] == (
        "Natsume.governance.snapshot.v1"
    )
    assert snapshot.governance["capabilities"]["status"] == "UNAVAILABLE"
    assert snapshot.governance["capabilities"]["reason_code"] == (
        "CAPABILITY_SCORECARDS_UNAVAILABLE"
    )

    app = FastAPI()
    app.include_router(create_host_obs_read_router(ports))
    response = TestClient(app).get(
        "/internal/obs-sources/governance",
        headers={"X-Internal-Token": "dedicated-token"},
    )
    assert response.status_code == 200


def test_governance_envelope_is_observed_after_live_providers_finish() -> None:
    ticks = [NOW]

    def clock_ms() -> int:
        return ticks[-1]

    ports = build_host_obs_read_ports(
        _runtime(
            clock_ms=clock_ms,
            capability_scorecard_provider=lambda: _ClockAdvancingScorecards(ticks),
        ),
        environ={"HOST_OBS_READ_TOKEN": "dedicated-token"},
    )

    governance = ports.governance_source_reader()

    assert governance.observed_at_ms == NOW + 1
    assert governance.governance["generated_at_ms"] == NOW
