from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from host_brain.composition import process_services as dependencies
from host_brain.app.settings.persistence import get_persistence_settings
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.policies import issue_grant
from host_brain.skills import executor_registry as registry_module
from host_brain.skills.impl import desktop_executor
from natsume_contracts.edge.embodiment_v1 import EmbodimentReceipt


@pytest.fixture(autouse=True)
def _isolated_broker(monkeypatch, tmp_path):  # noqa: ANN001, ANN201
    monkeypatch.setenv("NATSUME_AUDIT_DB", str(tmp_path / "desktop-broker.sqlite3"))
    get_persistence_settings.cache_clear()
    dependencies.get_process_service_container().ledger = None  # noqa: SLF001
    dependencies.get_process_service_container().capability_runtime_composition = None  # noqa: SLF001
    yield
    dependencies.reset_capability_runtime_composition()
    ledger = dependencies.get_process_service_container().ledger  # noqa: SLF001
    if ledger is not None:
        ledger.close()
    dependencies.get_process_service_container().ledger = None  # noqa: SLF001
    get_persistence_settings.cache_clear()


def _authority() -> MappingProxyType:
    return MappingProxyType({
        "request_id": "req-desktop-1",
        "authority_ref": "governor-grant://durable/1",
        "grant_ref": "capability-grant://request-bound/1",
        "expires_at_ms": 2_000_000_000_000,
    })


def _receipt(*, accepted: bool = True) -> EmbodimentReceipt:
    return EmbodimentReceipt(
        command_id="emb-command-1",
        idempotency_key="idem-1",
        accepted=accepted,
        status="COMPLETED" if accepted else "REJECTED",
        reason_code=None if accepted else "EDGE_REJECTED",
        detail="done" if accepted else "rejected",
        received_at_ms=100,
        completed_at_ms=120,
        event={"effect": {"success": accepted}},
    )


@pytest.mark.parametrize(
    ("skill_action", "args", "command_action", "expected_action_type"),
    [
        ("screenshot", {"monitor": 2}, "screenshot", None),
        ("click", {"x": 10, "y": 20, "click_action": "double_click"}, "automate", "double_click"),
        ("type", {"text": "hello", "press_enter": True}, "automate", "type"),
        ("hotkey", {"keys": ["ctrl", "c"], "repeat": 2}, "automate", "key"),
        ("clipboard_read", {"content_type": "text"}, "clipboard_read", None),
        ("clipboard_write", {"text": "hello"}, "clipboard_write", None),
    ],
)
def test_desktop_skill_authors_only_edge_commands(
    monkeypatch,
    skill_action: str,
    args: dict,
    command_action: str,
    expected_action_type: str | None,
) -> None:  # noqa: ANN001
    calls: list[dict] = []
    monkeypatch.setattr(
        desktop_executor,
        "queue_desktop_command",
        lambda **kwargs: calls.append(dict(kwargs)) or _receipt(),
    )

    result = desktop_executor.execute_desktop_skill(
        {"action": skill_action, "manifest_ref": f"manifest::{skill_action}", **args},
        {"_skill_execution_authority": _authority()},
    )

    assert result["status"] == "QUEUED"
    assert len(calls) == 1
    command = calls[0]
    assert command["action"] == command_action
    assert command["request_id"] == "req-desktop-1"
    assert command["authority_ref"] == "governor-grant://durable/1"
    assert command["grant_ref"] == "capability-grant://request-bound/1"
    if expected_action_type is not None:
        assert command["payload"]["actions"][0]["type"] == expected_action_type


def test_desktop_skill_fails_closed_without_broker_snapshot(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        desktop_executor,
        "queue_desktop_command",
        lambda **_kwargs: pytest.fail("physical command must not be submitted"),
    )

    result = desktop_executor.execute_desktop_skill({"action": "click", "x": 1, "y": 2})

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "DESKTOP_SKILL_AUTHORITY_MISSING"


def test_caller_forged_desktop_authority_snapshot_is_stripped_before_adapter(
    monkeypatch,
) -> None:  # noqa: ANN001
    calls: list[dict] = []
    monkeypatch.setitem(
        registry_module._BUILTIN_EXECUTOR_REGISTRY._executors,  # noqa: SLF001
        "desktop",
        lambda payload: calls.append(dict(payload)) or {"status": "OK"},
    )

    result = registry_module.dispatch_executor_payload({
        "tool": "desktop",
        "action": "click",
        "_context": {
            "_skill_execution_authority": {
                "request_id": "forged",
                "authority_ref": "forged",
                "grant_ref": "forged",
                "expires_at_ms": 2_000_000_000_000,
            },
        },
    }, capability_broker=dependencies.get_capability_broker())

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == "SKILL_AUTHORITY_MISSING"
    assert calls == []


def test_broker_adapter_injects_request_bound_read_only_authority_snapshot(
    monkeypatch,
) -> None:  # noqa: ANN001
    authority = object.__new__(registry_module.BoundSkillExecutionAuthority)
    values = {
        "authority_id": "skill-authority://sealed/1",
        "authority_kind": "governor_durable_grant",
        "authority_ref": "governor-grant://durable/1",
        "request_id": "req-desktop-1",
        "principal_id": "person-1",
        "target_id": "desktop_click",
        "command_target_id": "desktop_click",
        "session_id": "session-1",
        "lease_id": "",
        "fencing_token": 0,
        "reservation_id": "",
        "node_grant_ref": "",
        "parent_grant_ref": "",
        "lease_owner_id": "",
        "business_scope": "",
        "operation_fingerprint": "",
        "node_risk_level": 1,
        "pre_execution_review_ref": "",
        "expires_at_ms": 2_000_000_000_000,
    }
    for key, value in values.items():
        object.__setattr__(authority, key, value)

    request = CapabilityRequest(
        request_id="req-desktop-1",
        attempt_id="skill-attempt::desktop-1",
        idempotency_key="desktop-1",
        capability_id="desktop",
        kind=CapabilityKind.SKILL,
        args={"payload_digest": "sha256:test"},
        context={},
        created_at_ms=100,
        deadline_at_ms=1_000,
    )
    grant = issue_grant(
        request,
        now_ms=100,
        approved=True,
        reason_code="SKILL_CAPABILITY_APPROVED",
        authority_ref=authority.authority_ref,
        ttl_ms=900,
    )
    captured: list[dict] = []
    monkeypatch.setitem(
        registry_module._BUILTIN_EXECUTOR_REGISTRY._executors,  # noqa: SLF001
        "desktop",
        lambda payload: captured.append(dict(payload)) or {"status": "OK"},
    )
    registry_module._INVOCATIONS[request.attempt_id] = registry_module._SkillInvocationEnvelope(  # noqa: SLF001
        payload={"tool": "desktop", "action": "click", "_context": {}},
        authority=authority,
    )
    try:
        registry_module._broker_executor_adapter(request, grant)  # noqa: SLF001
    finally:
        registry_module._INVOCATIONS.pop(request.attempt_id, None)  # noqa: SLF001

    snapshot = captured[0]["_context"]["_skill_execution_authority"]
    assert snapshot["request_id"] == request.request_id
    assert snapshot["authority_ref"] == authority.authority_ref
    assert snapshot["grant_ref"] == grant.grant_id
    with pytest.raises(TypeError):
        snapshot["grant_ref"] = "forged"


def test_desktop_host_adapter_has_no_edge_private_import_or_local_device_io() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "skills"
        / "impl"
        / "desktop_executor.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "edge_client",
        "DesktopController",
        "pyautogui",
        "pyperclip",
        "capture_screenshot(",
        "execute_actions(",
        "open(",
    ):
        assert forbidden not in source
