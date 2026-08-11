from __future__ import annotations

import pytest

from host_brain.observability.degradation_state import (
    DEGRADATION_HEALTH_EVIDENCE_INVALID,
    DegradationStateError,
    normalize_service_observations,
    normalize_startup_observations,
)


def test_service_observations_are_pure_grouped_and_order_independent() -> None:
    rows = [
        {
            "service_id": "llm_gateway",
            "status": "UP",
            "last_check_ts": 99,
            "side": "host",
        },
        {
            "service_id": "model_provider",
            "status": "DOWN",
            "last_check_ts": 100,
            "side": "host",
        },
        {
            "service_id": "tts",
            "status": "DEFERRED",
            "last_check_ts": 100,
            "side": "edge",
        },
        {
            "service_id": "recall_fusion",
            "status": "UP",
            "detail": "lazy init",
        },
        {"service_id": "not_registered", "status": "DOWN"},
    ]

    first = normalize_service_observations(
        rows,
        source_ref="service-heartbeat://runtime/100",
        now_ms=100,
    )
    second = normalize_service_observations(
        list(reversed(rows)),
        source_ref="service-heartbeat://runtime/100",
        now_ms=100,
    )

    assert first == second
    assert len(first) == 1
    assert first[0]["dependency_id"] == "MODEL"
    assert first[0]["status"] == "DOWN"
    assert first[0]["observed_at_ms"] == 100
    assert first[0]["source_ref"] == "service-heartbeat://runtime/100"
    assert first[0]["evidence_refs"][0].startswith(
        "health-observation://sha256/"
    )


def test_startup_observations_share_the_same_normalized_contract() -> None:
    observations = normalize_startup_observations(
        {
            "chat_llm_runtime_warmup": "DEGRADED: provider unavailable",
            "retrieval_runtime_warmup": "OK (lazy)",
            "emotion_timeline": "SKIPPED (lazy init)",
            "unregistered_component": "FAILED",
        },
        source_ref="startup-probe://runtime/200",
        now_ms=200,
    )

    assert [row["dependency_id"] for row in observations] == ["MEMORY", "MODEL"]
    assert {row["dependency_id"]: row["status"] for row in observations} == {
        "MEMORY": "UP",
        "MODEL": "DEGRADED",
    }
    assert all(row["observed_at_ms"] == 200 for row in observations)


@pytest.mark.parametrize("value", [True, -1, 1.5, "100"])
def test_normalization_rejects_non_exact_observation_time(value: object) -> None:
    with pytest.raises(DegradationStateError) as exc_info:
        normalize_service_observations(
            [{"service_id": "llm_gateway", "status": "DOWN"}],
            source_ref="probe://model/invalid-time",
            now_ms=value,  # type: ignore[arg-type]
        )

    assert exc_info.value.reason_code == DEGRADATION_HEALTH_EVIDENCE_INVALID
