"""Single isolated M48-B P0 golden/replay wave for TASK-0495.

The wave deliberately runs twice over fresh state roots and compares only a
sanitized result projection.  All external effect ports are recording-only,
while a process/socket guard turns any attempted escape into a hard failure.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.fixtures.m48b_p0_validation import build_m48b_p0_validation_run
from tests.fixtures.task0495_bridge_golden import run_bridge_golden
from tests.fixtures.task0495_gateway_host_golden import run_gateway_host_golden
from tests.fixtures.task0495_obs_golden import run_obs_golden
from tests.fixtures.task0495_skill_pentest_golden import (
    run_pentest_golden,
    run_skill_golden,
)


PROJECTION_KEYS = frozenset(
    {
        "status",
        "reason_code",
        "effect_count",
        "durable_claim_count",
        "audit_count",
        "uncertain",
    }
)

EXPECTED_PROJECTION = {
    "gateway_host": {
        "status": "COMPLETED",
        "reason_code": "GATEWAY_HOST_GOLDEN_COMPLETED",
        "effect_count": 1,
        "durable_claim_count": 2,
        "audit_count": 2,
        "uncertain": False,
    },
    "obs": {
        "status": "COMPLETED",
        "reason_code": "OBS_COMMAND_COMPLETED",
        "effect_count": 1,
        "durable_claim_count": 1,
        "audit_count": 5,
        "uncertain": False,
    },
    "skill_provider": {
        "status": "COMPLETED",
        "reason_code": "SUPPLY_CHAIN_INTAKE_STAGED",
        "effect_count": 0,
        "durable_claim_count": 1,
        "audit_count": 2,
        "uncertain": False,
    },
    "pentest": {
        "status": "COMPLETED",
        "reason_code": "PENTEST_SECURE_EXECUTION_COMPLETED",
        "effect_count": 1,
        "durable_claim_count": 2,
        "audit_count": 3,
        "uncertain": False,
    },
    "bridge": {
        "status": "COMPLETED",
        "reason_code": "BRIDGE_GOLDEN_COMPLETED",
        "effect_count": 2,
        "durable_claim_count": 2,
        "audit_count": 7,
        "uncertain": False,
    },
}


def test_task0495_all_p0_domains_are_repeatable_fail_closed_and_isolated(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Keep the Windows path below MAX_PATH even after content-addressed
    # quarantine adds a digest plus atomic-publish suffix.
    run = build_m48b_p0_validation_run(
        state_root=tmp_path_factory.mktemp("m48b49"),
    )
    now_ms = int(time.time() * 1_000)

    # Even accidental environment lookup cannot recover a real cloud secret.
    for env_name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "MINIMAX_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    projections: list[dict[str, dict[str, object]]] = []
    with run.no_real_io_guard():
        for cycle in ("fresh-a", "fresh-b"):
            gateway = run.harness("gateway")
            obs = run.harness("obs")
            skill = run.harness("skill_provider")
            pentest = run.harness("pentest")
            bridge = run.harness("bridge")
            projection = {
                "gateway_host": run_gateway_host_golden(
                    state_root=gateway.state_root / cycle,
                    harness=gateway,
                    now_ms=now_ms,
                    monkeypatch=monkeypatch,
                ),
                "obs": run_obs_golden(
                    state_root=obs.state_root / cycle,
                    harness=obs,
                    now_ms=now_ms,
                ),
                "skill_provider": run_skill_golden(
                    state_root=skill.state_root / cycle,
                    harness=skill,
                    now_ms=now_ms,
                ),
                "pentest": run_pentest_golden(
                    state_root=pentest.state_root / cycle,
                    harness=pentest,
                    now_ms=now_ms,
                ),
                "bridge": run_bridge_golden(
                    state_root=bridge.state_root / cycle,
                    harness=bridge,
                    now_ms=now_ms,
                ),
            }
            assert all(set(item) == PROJECTION_KEYS for item in projection.values())
            assert all(
                item["status"] == "COMPLETED"
                for item in projection.values()
            )
            assert all(item["uncertain"] is False for item in projection.values())
            assert projection == EXPECTED_PROJECTION
            projections.append(projection)

    assert projections[0] == projections[1]
    serialized = json.dumps(projections, sort_keys=True)
    for harness in run.harnesses:
        assert harness.test_secret not in serialized

    run.assert_isolated(
        {
            "gateway": 2,
            "obs": 2,
            "skill_provider": 0,
            "pentest": 2,
            "bridge": 4,
        }
    )


__all__ = [
    "test_task0495_all_p0_domains_are_repeatable_fail_closed_and_isolated",
]
