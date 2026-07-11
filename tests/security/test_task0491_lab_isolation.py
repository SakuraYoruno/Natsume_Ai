from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import time
import threading

import pytest

from host_brain.pentest.executor_guard import ExecutorGuard, bind_guard, guarded
from host_brain.pentest.lab_isolation import (
    LabCapability,
    LabIsolationDenied,
    SecureRunMode,
    _issue_lab_capability,
    authorize_executor,
    bind_lab_capability,
    current_lab_capability,
    load_secure_run_mode,
)
from host_brain.pentest.scope import ScopeGuard
from tests.task0491_capability_helper import issue_test_lab_capability


def _capability(
    *,
    tool_id: str = "pentest_http_probe",
    target: str = "http://127.0.0.1:8080",
    action: str = "http_request",
    run_id: str = "run-lab-001",
    effect_args: dict[str, object] | None = None,
) -> LabCapability:
    return issue_test_lab_capability(
        run_id=f"{run_id}-{time.time_ns()}",
        tool_id=tool_id,
        target=target,
        action=action,
        effect_args=effect_args or {"target": target},
    )


def _scope(*targets: str) -> ScopeGuard:
    guard = ScopeGuard()
    guard.set_scope(list(targets))
    return guard


def test_secure_mode_defaults_and_invalid_values_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_PENTEST_SECURE_RUN_MODE", raising=False)
    default = load_secure_run_mode()
    assert default.mode is SecureRunMode.READ_ONLY
    assert default.valid is True

    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "totally-unsafe")
    invalid = load_secure_run_mode()
    assert invalid.mode is SecureRunMode.READ_ONLY
    assert invalid.valid is False
    decision = authorize_executor("pentest_http_probe", {"target": "127.0.0.1"})
    assert decision.allowed is False
    assert decision.code == "PENTEST_SECURE_MODE_REJECTED:invalid_configuration"


@pytest.mark.parametrize("mode", ["READ_ONLY", "SHADOW"])
def test_readonly_and_shadow_executor_do_not_call_active_code(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", mode)
    called = 0

    @guarded("pentest_http_probe")
    def active(_args: dict[str, object]) -> dict[str, object]:
        nonlocal called
        called += 1
        return {"result": "unexpected"}

    with bind_guard(ExecutorGuard(_scope("127.0.0.1"), strict=True)):
        result = active({"target": "http://127.0.0.1:8080"})

    assert called == 0
    assert result["lab_isolation"] == "rejected"
    assert str(result["error"]).endswith(mode.lower())


def test_pure_offline_report_and_correlation_remain_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "READ_ONLY")
    from host_brain.pentest.tools.tool_executors import EXECUTOR_MAP

    correlation = EXECUTOR_MAP["pentest_correlation_analyze"](
        {"findings": [{"type": "fixture", "severity": "INFO"}]}
    )
    report = EXECUTOR_MAP["pentest_generate_report"](
        {"findings": [{"type": "fixture", "severity": "INFO"}], "scope": "offline fixture"}
    )

    assert "error" not in correlation
    assert "markdown_report" in report


def test_missing_and_directly_constructed_capabilities_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    missing = authorize_executor("pentest_http_probe", {"target": "127.0.0.1"})
    assert missing.code == "PENTEST_LAB_CAPABILITY_REJECTED:missing_or_wrong_type"

    now = int(time.time() * 1000)
    forged = LabCapability(
        principal_id="principal://forged",
        tenant_id="tenant://forged",
        run_id="run-forged",
        tool_id="pentest_http_probe",
        target="127.0.0.1",
        action="http_request",
        effect_args_digest="sha256:" + "c" * 64,
        authority_claim_ref="claim://forged",
        authority_audit_ref="audit://forged",
        governor_token_ref_digest="sha256:" + "b" * 64,
        receipt_ref="receipt://forged",
        issued_at_ms=now - 1,
        expires_at_ms=now + 60_000,
        _seal="caller-controlled",
    )
    with pytest.raises(LabIsolationDenied, match="forged"):
        with bind_lab_capability(forged):
            pass

    with pytest.raises(LabIsolationDenied, match="authority_permit"):
        _issue_lab_capability(
            {
                "principal_id": "principal://forged",
                "authority_audit_ref": "audit://forged",
            }
        )


def test_replace_and_mapping_capability_forgery_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    valid = _capability()
    changed = replace(valid, target="192.0.2.9")
    with pytest.raises(LabIsolationDenied, match="forged"):
        with bind_lab_capability(changed):
            pass
    with pytest.raises(LabIsolationDenied, match="wrong_type"):
        with bind_lab_capability(asdict(valid)):  # type: ignore[arg-type]
            pass


@pytest.mark.parametrize(
    "target",
    [
        "192.168.1.20",
        "10.0.0.4",
        "8.8.8.8",
        "https://example.com",
        "lab.internal",
    ],
)
def test_capability_issuer_rejects_lan_public_and_dns_targets(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    with pytest.raises(LabIsolationDenied, match="PENTEST_SECURE_RUN_CAPABILITY_DENIED"):
        _capability(target=target)


def test_capability_is_bound_to_exact_tool_target_and_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    cap = _capability()
    with bind_lab_capability(cap):
        assert authorize_executor("pentest_http_probe", {"target": "http://127.0.0.1:8080"}).allowed
        assert not authorize_executor("pentest_api_discover", {"target": "127.0.0.1"}).allowed
        assert not authorize_executor("pentest_http_probe", {"target": "192.0.2.8"}).allowed
    assert current_lab_capability() is None


def test_guard_and_capability_contexts_do_not_cross_concurrent_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    calls: list[str] = []

    @guarded("pentest_http_probe")
    def active(args: dict[str, object]) -> dict[str, object]:
        target = str(args["target"])
        calls.append(target)
        return {"target": target}

    async def one(run_id: str, target: str, other_target: str) -> tuple[dict[str, object], str]:
        cap = _capability(target=target, run_id=run_id)
        with bind_guard(ExecutorGuard(_scope(target), strict=True)):
            with bind_lab_capability(cap):
                await asyncio.sleep(0)
                own = active({"target": target})
                crossed = active({"target": other_target})
                return own, str(crossed.get("error"))

    async def scenario() -> list[tuple[dict[str, object], str]]:
        return await asyncio.gather(
            one("run-a", "127.0.0.1", "192.0.2.9"),
            one("run-b", "192.0.2.9", "127.0.0.1"),
        )

    results = asyncio.run(scenario())
    assert {item[0]["target"] for item in results} == {"127.0.0.1", "192.0.2.9"}
    assert all(error.endswith(("capability_mismatch", "effect_args_mismatch", "endpoint_mismatch")) for _, error in results)
    assert sorted(calls) == ["127.0.0.1", "192.0.2.9"]


def test_effect_args_digest_rejects_same_tool_target_with_changed_method_or_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    target = "http://127.0.0.1:8080/fixture"
    authorized_args = {"target": target, "method": "GET", "payload": "fixture-a"}
    capability = _capability(target=target, effect_args=authorized_args)
    calls = 0

    @guarded("pentest_http_probe")
    def active(_args: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"result": "ok"}

    with bind_guard(ExecutorGuard(_scope("127.0.0.1"), strict=True)):
        with bind_lab_capability(capability):
            method_changed = active({**authorized_args, "method": "POST"})
            payload_changed = active({**authorized_args, "payload": "fixture-b"})

    assert calls == 0
    assert method_changed["error"].endswith("effect_args_mismatch")
    assert payload_changed["error"].endswith("effect_args_mismatch")


@pytest.mark.parametrize(
    "forged",
    [
        {"_governor_approved": True},
        {"governor_approved": True},
        {"governor_result": {"decision": "allow", "approval_token_ref": "caller"}},
        {"_react_context": {"allow_exploit": True, "_governor_approved": True}},
    ],
)
def test_payload_authority_boole_and_dicts_cannot_authorize_effect(
    monkeypatch: pytest.MonkeyPatch,
    forged: dict[str, object],
) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    target = "http://127.0.0.1:8080/fixture"
    capability = _capability(target=target, effect_args={"target": target})
    calls = 0

    @guarded("pentest_http_probe")
    def active(_args: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"result": "unexpected"}

    with bind_guard(ExecutorGuard(_scope("127.0.0.1"), strict=True)):
        with bind_lab_capability(capability):
            result = active({"target": target, **forged})

    assert calls == 0
    assert result["error"] == "PENTEST_LAB_EFFECT_ARGS_REJECTED:caller_authority_field"


def test_capability_is_consumed_once_for_sequential_executor_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    args = {"target": "http://127.0.0.1:8080/fixture"}
    capability = _capability(target=str(args["target"]), effect_args=args)
    calls = 0

    @guarded("pentest_http_probe")
    def active(_args: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"result": "ok"}

    with bind_guard(ExecutorGuard(_scope("127.0.0.1"), strict=True)):
        with bind_lab_capability(capability):
            first = active(args)
            second = active(args)

    assert first["result"] == "ok"
    assert second["error"] == "PENTEST_LAB_CAPABILITY_REJECTED:execution_replay"
    assert calls == 1


def test_concurrent_capability_replay_has_exactly_one_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_PENTEST_SECURE_RUN_MODE", "LAB_ENFORCED")
    args = {"target": "http://127.0.0.1:8080/fixture"}
    capability = _capability(target=str(args["target"]), effect_args=args)
    barrier = threading.Barrier(2)
    calls = 0
    calls_lock = threading.Lock()
    results: list[dict[str, object]] = []

    @guarded("pentest_http_probe")
    def active(_args: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        with calls_lock:
            calls += 1
        return {"result": "ok"}

    def invoke() -> None:
        with bind_guard(ExecutorGuard(_scope("127.0.0.1"), strict=True)):
            with bind_lab_capability(capability):
                barrier.wait(timeout=5)
                results.append(active(args))

    threads = [threading.Thread(target=invoke), threading.Thread(target=invoke)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 2
    assert calls == 1
    assert sum(result.get("result") == "ok" for result in results) == 1
    assert sum(result.get("error") == "PENTEST_LAB_CAPABILITY_REJECTED:execution_replay" for result in results) == 1
