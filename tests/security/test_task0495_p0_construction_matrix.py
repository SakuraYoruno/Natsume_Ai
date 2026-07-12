"""TASK-0495 provider construction and fault-injection negative matrix.

Every case uses an Entry Gate recording harness.  A dependency fault must
produce one stable terminal rejection/disabled state and zero attempted
network, process or physical effect; no case may select a fake provider.
"""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
import multiprocessing
import os
from pathlib import Path
import socket
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edge_client.gateway.webhook_security import (
    GW_VERIFICATION_FAILED,
    RawWebhookEnvelope,
    VerifierRegistry,
    WebhookVerificationError,
)
from host_brain.observability import command_composition as obs_composition
from host_brain.pentest.lab_isolation import PentestEffectAction
from host_brain.pentest.secure_execution import (
    EffectState,
    SecureExecutionReasonCode,
    SecurePentestExecutionService,
)
from host_brain.pentest.secure_run import (
    SecureRunRequest,
    canonical_effect_args_digest,
)
from host_brain.skills.supply_chain import SupplyChainActivationMode
from host_brain.skills.supply_chain_service import SkillSupplyChainService
from host_brain.skills.supply_chain_store import BundleKind
from tests.fixtures.m48b_p0_validation import (
    M48BForbiddenIOAttempt,
    M48B_P0_ISOLATED_ONLY,
    M48B_P0_VALIDATION_PROFILE_ENV,
    build_m48b_p0_validation_run,
    resolve_m48b_owned_state_root,
    resolve_m48b_p0_validation_profile,
)
from tools import mateengine_bridge_server as mate_bridge


MATRIX_TERMINAL_REASONS = {
    "gateway": GW_VERIFICATION_FAILED,
    "obs": obs_composition.OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED,
    "pentest": SecureExecutionReasonCode.EXECUTOR_NOT_FOUND.value,
    "skill_provider": "SUPPLY_CHAIN_VERIFIER_FAILED",
    "bridge": "BRIDGE_CONSTRUCTION_FAILED",
}


class _ExplodingGatewayVerifier:
    platform = "qq"
    profile_id = "task0495-exploding-verifier"

    def verify(self, _envelope: RawWebhookEnvelope, *, now_ms: int) -> None:
        del now_ms
        raise RuntimeError("sensitive-provider-detail-must-not-escape")


class _ExplodingSkillVerifier:
    configuration_valid = True
    mode = SupplyChainActivationMode.STAGED

    def verify_bundle(self, _source: object) -> None:
        raise RuntimeError("untrusted verifier failure")


class _ForbiddenDownstream:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"faulted provider called downstream port: {name}")


def _run(tmp_path: Path):
    return build_m48b_p0_validation_run(state_root=tmp_path / "m48b-p0")


def test_profile_is_immutable_isolated_only_and_rejects_live_aliases(
    tmp_path: Path,
) -> None:
    profile = resolve_m48b_p0_validation_profile({})
    assert profile.name == M48B_P0_ISOLATED_ONLY
    with pytest.raises(FrozenInstanceError):
        profile.name = "LIVE"  # type: ignore[misc]
    for invalid in ("LIVE", "PRODUCTION", "SHADOW", ""):
        source = {M48B_P0_VALIDATION_PROFILE_ENV: invalid}
        if invalid == "":
            # An empty value has the same safe meaning as an absent value.
            assert resolve_m48b_p0_validation_profile(source).name == M48B_P0_ISOLATED_ONLY
        else:
            with pytest.raises(ValueError, match="M48B_P0_VALIDATION_PROFILE_INVALID"):
                resolve_m48b_p0_validation_profile(source)

    run = _run(tmp_path)
    run.assert_safe()
    assert tuple(sorted(MATRIX_TERMINAL_REASONS)) == run.profile.domains


def test_no_real_io_guard_blocks_records_and_restores_socket_api(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    original_connect = socket.socket.connect
    original_getaddrinfo = socket.getaddrinfo
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with run.no_real_io_guard():
            with pytest.raises(M48BForbiddenIOAttempt) as caught:
                probe.connect(("192.0.2.1", 9))
            assert caught.value.operation == "socket.connect"
            with pytest.raises(M48BForbiddenIOAttempt) as dns_caught:
                socket.getaddrinfo("example.invalid", 443)
            assert dns_caught.value.operation == "socket.getaddrinfo"
            with pytest.raises(M48BForbiddenIOAttempt) as pair_caught:
                socket.socketpair()
            assert pair_caught.value.operation == "socket.socketpair"
            with pytest.raises(M48BForbiddenIOAttempt) as send_caught:
                probe.send(b"must-not-leave")
            assert send_caught.value.operation == "socket.send"
            child = multiprocessing.Process(target=lambda: None)
            with pytest.raises(M48BForbiddenIOAttempt) as process_caught:
                child.start()
            assert (
                process_caught.value.operation
                == "multiprocessing.Process.start"
            )
            with pytest.raises(M48BForbiddenIOAttempt) as exec_caught:
                os.execv("task0495-must-not-exist", ["task0495"])
            assert exec_caught.value.operation == "os.execv"
    finally:
        probe.close()

    assert socket.socket.connect is original_connect
    assert socket.getaddrinfo is original_getaddrinfo
    assert run.forbidden_io_attempts == (
        "socket.connect",
        "socket.getaddrinfo",
        "socket.socketpair",
        "socket.send",
        "multiprocessing.Process.start",
        "os.execv",
    )
    with pytest.raises(
        ValueError,
        match="M48B_P0_VALIDATION_FORBIDDEN_IO_ATTEMPT",
    ):
        run.assert_isolated()


def test_expected_recording_receipts_are_distinct_from_forbidden_io(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    run.harness("gateway").sink.emit(
        "host.graph.dispatch",
        {"request": "task0495-recording-only"},
    )

    run.assert_isolated({"gateway": 1})
    assert run.forbidden_io_attempts == ()


def test_domain_state_root_cannot_escape_its_owned_harness(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    gateway = run.harness("gateway")

    assert resolve_m48b_owned_state_root(
        harness=gateway,
        state_root=gateway.state_root / "owned",
    ).is_dir()
    with pytest.raises(ValueError, match="M48B_P0_VALIDATION_STATE_ESCAPE"):
        resolve_m48b_owned_state_root(
            harness=gateway,
            state_root=run.state_root / "obs" / "not-gateway-owned",
        )


def test_asyncio_control_socket_is_not_misclassified_as_domain_io(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)

    with run.no_real_io_guard():
        assert asyncio.run(asyncio.sleep(0, result="in-process")) == "in-process"

    run.assert_isolated()


def test_in_process_asgi_transport_stays_inside_real_io_guard(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "in-process"}

    with run.no_real_io_guard():
        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "in-process"}

    run.assert_isolated()


def test_gateway_verifier_exception_is_sanitized_without_fallback(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    harness = run.harness("gateway")
    registry = VerifierRegistry(
        (_ExplodingGatewayVerifier(),),
        now_ms_fn=lambda: 1_900_000_000_000,
    )
    envelope = RawWebhookEnvelope(
        platform="qq",
        method="POST",
        path="/v1/gateway/qq/webhook",
        raw_body=b'{"message":"recording-only"}',
        received_at_ms=1_900_000_000_000,
        peer="127.0.0.1",
    )

    with run.no_real_io_guard():
        with pytest.raises(WebhookVerificationError) as caught:
            registry.verify(envelope)

    assert caught.value.reason_code == MATRIX_TERMINAL_REASONS["gateway"]
    assert "sensitive-provider-detail" not in str(caught.value)
    assert harness.sink.effects == []
    run.assert_isolated()


def test_obs_store_constructor_fault_returns_disabled_no_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(tmp_path)
    harness = run.harness("obs")

    def fail_store(_path: object) -> None:
        raise RuntimeError("sensitive-obs-store-provider-detail")

    monkeypatch.setattr(obs_composition, "SQLiteSecureIngressStore", fail_store)
    with run.no_real_io_guard():
        composition = obs_composition.build_obs_command_composition(
            env={
                "HOST_OBS_COMMAND_RELOCATION_MODE": "QUERY_ONLY",
                "HOST_SECURE_INGRESS_MODE": "ENFORCE",
                "HOST_SECURE_INGRESS_HMAC_SECRETS_JSON": json.dumps(
                    {"task0495.obs.key": harness.test_secret}
                ),
            },
            db_path=harness.state_root / "central.sqlite3",
            outcome_journal_path=harness.state_root / "outcomes.jsonl",
        )

    assert composition.enabled is False
    assert composition.execution_enabled is False
    assert composition.reason_code == MATRIX_TERMINAL_REASONS["obs"]
    assert "sensitive-obs-store-provider-detail" not in composition.reason_code
    assert composition.router is None
    assert composition.store is None
    assert harness.sink.effects == []
    run.assert_isolated()


def test_skill_verifier_fault_never_selects_quarantine_or_store_fallback(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    harness = run.harness("skill_provider")
    service = SkillSupplyChainService(
        verifier=_ExplodingSkillVerifier(),
        quarantine=_ForbiddenDownstream(),
        store=_ForbiddenDownstream(),
    )

    with run.no_real_io_guard():
        decision = service.intake_signed_bundle(
            harness.state_root / "untrusted-provider",
            kind=BundleKind.PROVIDER,
            publisher="publisher.task0495",
            idempotency="task0495.skill-provider.fault",
        )

    assert decision.reason_code == MATRIX_TERMINAL_REASONS["skill_provider"]
    assert decision.stored is False
    assert decision.quarantined is False
    assert decision.staged is False
    assert decision.import_performed is False
    assert decision.activation_performed is False
    assert harness.sink.effects == []
    run.assert_isolated()


def test_pentest_invalid_executor_provider_does_not_load_default_map(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    harness = run.harness("pentest")
    target = "127.0.0.1"
    executor_args = {"target": target}
    request = SecureRunRequest(
        principal_id=harness.principal,
        tenant_id="tenant.task0495",
        run_id="ptrun_task0495_provider_fault",
        tool_id="task0495.invalid-provider",
        target=target,
        action=PentestEffectAction.HTTP_REQUEST.value,
        intent_id="intent.task0495",
        session_id="session.task0495",
        seq=49_500,
        effect_args_digest=canonical_effect_args_digest(executor_args),
    )
    service = SecurePentestExecutionService(
        governor_tokens=object(),  # type: ignore[arg-type]
        run_store=object(),  # type: ignore[arg-type]
        executors={request.tool_id: object()},  # explicitly invalid; no default map
        clock=lambda: 1_900_000_000_000,
    )

    with run.no_real_io_guard():
        decision = service.execute(request, None, executor_args)

    assert decision.reason_code.value == MATRIX_TERMINAL_REASONS["pentest"]
    assert decision.effect_state is EffectState.NOT_EXECUTED
    assert decision.side_effects_executed is False
    assert harness.sink.effects == []
    run.assert_isolated()


def test_bridge_audit_constructor_fault_precedes_any_handler_or_device(
    tmp_path: Path,
) -> None:
    run = _run(tmp_path)
    harness = run.harness("bridge")
    audit_directory = harness.state_root / "audit-is-directory"
    audit_directory.mkdir()
    config = mate_bridge.build_security_config(
        {
            "NATSUME_BRIDGE_EXPOSURE_MODE": "LOOPBACK",
            "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID": "task0495.mate.ingress",
            "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET": harness.test_secret,
            "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS": harness.principal,
            "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES": mate_bridge.SCOPE_SAY,
            "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH": str(audit_directory),
            "NATSUME_MATEENGINE_BRIDGE_REPLAY_LEDGER_PATH": str(
                harness.state_root / "replay.sqlite3"
            ),
            "NATSUME_MATEENGINE_PLUGIN_HMAC_KEY_ID": "task0495.mate.plugin",
            "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET": (
                harness.test_secret + ".plugin-distinct"
            ),
            "MATEENGINE_BRIDGE_HTTP_HOST": "127.0.0.1",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
            "MATEENGINE_BRIDGE_UDP_ENABLED": "false",
            "MATEENGINE_SIGNAL_HOST": "127.0.0.1",
            "MATEENGINE_PLUGIN_HOST": "127.0.0.1",
        }
    )

    with run.no_real_io_guard():
        with pytest.raises(mate_bridge.BridgeConstructionError) as caught:
            mate_bridge.build_bridge_security_runtime(config)

    assert caught.value.reason_code == MATRIX_TERMINAL_REASONS["bridge"]
    assert str(caught.value) == MATRIX_TERMINAL_REASONS["bridge"]
    assert "audit-is-directory" not in str(caught.value)
    assert harness.sink.effects == []
    run.assert_isolated()


def test_bridge_main_sanitizes_preconstruction_configuration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_detail = "private-bridge-config-path-must-not-escape"

    def fail_config() -> None:
        raise OSError(private_detail)

    monkeypatch.setattr(mate_bridge, "build_security_config", fail_config)
    with pytest.raises(SystemExit) as caught:
        mate_bridge.main()

    assert mate_bridge.BRIDGE_CONSTRUCTION_FAILED in str(caught.value)
    assert private_detail not in str(caught.value)
