from __future__ import annotations

from pathlib import Path
import inspect
import time

import pytest

from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS
from host_brain.domain_packs.production_runtime import _production_adapter
from host_brain.capability_plane.broker import (
    CapabilityBroker,
    CapabilityDescriptor,
    CapabilityInvocationError,
)
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.policies import PredicateCapabilityPolicy, issue_grant
from host_brain.domain_packs.contracts import (
    DomainCapabilityManifest,
    DomainLifecycleStageManifest,
    DomainPack,
    DomainPackManifest,
    DomainPackRequest,
)
from host_brain.domain_packs.media_translation import build_media_translation_pack
from host_brain.domain_packs.pentest import build_pentest_pack
from host_brain.domain_packs.ports import DomainPackPlatformPorts
from host_brain.domain_packs.registry import DomainPackRegistry, DomainPackResolutionError
from host_brain.domain_packs.research_browser import build_research_browser_pack


class _TaskPort:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, task_id, payload):  # noqa: ANN001
        self.calls.append((task_id, dict(payload)))
        return {"status": "EXECUTED", "task_id": task_id}


class _CustomHandler:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.calls = 0

    def execute(self, request, ports):  # noqa: ANN001, ANN201
        self.calls += 1
        return ports.require("task_port").execute(self.task_id, request.payload)


def _registry() -> DomainPackRegistry:
    registry = DomainPackRegistry()
    registry.install(build_research_browser_pack())
    registry.install(build_media_translation_pack())
    registry.install(build_pentest_pack())
    return registry


def _broker_binding(request: DomainPackRequest):  # noqa: ANN201
    now_ms = int(time.time() * 1000)
    capability_request = CapabilityRequest(
        request_id=request.request_id,
        attempt_id=f"attempt::{request.request_id}",
        idempotency_key=f"pack::{request.request_id}::{request.capability_id}",
        capability_id=request.capability_id,
        kind=CapabilityKind.DOMAIN,
        args=request.payload,
        context={},
        created_at_ms=now_ms,
        deadline_at_ms=now_ms + 30_000,
    )
    return capability_request, issue_grant(
        capability_request,
        now_ms=now_ms,
        approved=True,
        reason_code="TEST_APPROVED",
        authority_ref=f"test-authority://{request.request_id}",
    )


def test_first_three_domain_packs_cover_every_production_capability() -> None:
    registry = _registry()
    owners = {
        capability_id: registry.resolve(capability_id).pack_id
        for capability_id in AGENT_PRODUCTION_SKILL_IDS
    }
    assert set(owners) == set(AGENT_PRODUCTION_SKILL_IDS)
    assert owners["browser_research"] == "research-browser"
    assert owners["agent_epub_builder"] == "media-translation"
    assert owners["pentest_autonomous"] == "pentest"
    assert {row["pack_id"] for row in registry.snapshot()} == {
        "research-browser", "media-translation", "pentest",
    }


def test_pack_manifests_declare_generic_postprocess_delivery_and_recovery_stages() -> None:
    registry = _registry()
    research = registry.resolve("browser_research")
    lightnovel = registry.resolve("agent_lightnovel_recommender")
    epub = registry.resolve("agent_epub_builder")
    pentest = registry.resolve("pentest_autonomous")

    assert [(stage.phase, stage.user_visible) for stage in research.lifecycle_stages] == [
        ("delivery", True),
    ]
    assert [stage.phase for stage in lightnovel.lifecycle_stages] == [
        "postprocess",
        "recovery",
        "delivery",
    ]
    assert [(stage.phase, stage.user_visible) for stage in epub.lifecycle_stages] == [
        ("delivery", True),
    ]
    assert pentest.lifecycle_stages == ()
    for resolution in (research, lightnovel, epub, pentest):
        assert {stage.task_id for stage in resolution.lifecycle_stages}.issubset(
            set(resolution.allowed_task_ids)
        )


def test_registry_dispatches_declared_delivery_stage_through_scoped_task_port() -> None:
    registry = _registry()
    task_port = _TaskPort()
    stage_binding = {
        "stage_id": "research.browser.gateway-delivery",
        "phase": "delivery",
        "task_id": "research.browser.gateway-delivery",
        "delivery_id": "delivery://stable/research",
    }
    request = DomainPackRequest(
        request_id="delivery://stable/research",
        capability_id="browser_research",
        payload={"lifecycle_stage": stage_binding},
    )
    capability_request, grant = _broker_binding(request)

    result = registry.dispatch(
        request,
        ports=DomainPackPlatformPorts(task_port=task_port, capability_broker=object()),
        capability_request=capability_request,
        capability_grant=grant,
    )

    assert result["task_id"] == "research.browser.gateway-delivery"
    assert task_port.calls == [
        (
            "research.browser.gateway-delivery",
            {"lifecycle_stage": stage_binding, "domain_capability_id": "browser_research"},
        )
    ]

    missing_delivery = DomainPackRequest(
        request_id="delivery://missing-id",
        capability_id="browser_research",
        payload={"lifecycle_stage": {**stage_binding, "delivery_id": None}},
    )
    missing_request, missing_grant = _broker_binding(missing_delivery)
    with pytest.raises(DomainPackResolutionError) as exc_info:
        registry.dispatch(
            missing_delivery,
            ports=DomainPackPlatformPorts(task_port=task_port, capability_broker=object()),
            capability_request=missing_request,
            capability_grant=missing_grant,
        )
    assert exc_info.value.reason_code == "DOMAIN_DELIVERY_ID_REQUIRED"


def test_lifecycle_stage_task_must_be_in_capability_allowlist() -> None:
    with pytest.raises(ValueError, match="included in allowed_task_ids"):
        DomainCapabilityManifest(
            capability_id="custom_stage",
            handler_id="custom.stage",
            description="invalid stage declaration",
            risk_level=1,
            allowed_task_ids=("custom.worker",),
            lifecycle_stages=(
                DomainLifecycleStageManifest(
                    stage_id="custom.delivery",
                    phase="delivery",
                    task_id="custom.delivery",
                    user_visible=True,
                ),
            ),
        )
def test_common_production_adapter_has_no_domain_dispatch_branch() -> None:
    source = inspect.getsource(_production_adapter)
    assert "request.capability_id ==" not in source
    assert "execute_browser_research_work_order" not in source
    assert "execute_lightnovel_epub_work_order" not in source
    assert "AgentRuntimeExecutor" not in source


def test_pack_enable_disable_version_and_rollback_are_fail_closed() -> None:
    registry = DomainPackRegistry()
    registry.install(build_research_browser_pack(version="1.0.0"))
    registry.install(build_research_browser_pack(version="1.1.0"), activate=True)
    assert registry.resolve("browser_research").version == "1.1.0"
    assert registry.rollback("research-browser") == "1.0.0"
    assert registry.resolve("browser_research").version == "1.0.0"

    registry.disable("research-browser")
    with pytest.raises(DomainPackResolutionError) as disabled:
        registry.resolve("browser_research")
    assert disabled.value.reason_code == "DOMAIN_PACK_DISABLED"
    with pytest.raises(DomainPackResolutionError) as unknown:
        registry.resolve("unregistered_capability")
    assert unknown.value.reason_code == "DOMAIN_CAPABILITY_UNKNOWN"
    registry.enable("research-browser", version="1.1.0")
    assert registry.resolve("browser_research").version == "1.1.0"


def test_pack_dispatch_uses_only_explicit_platform_ports() -> None:
    registry = _registry()
    task_port = _TaskPort()
    request = DomainPackRequest(
        request_id="req-pack",
        capability_id="browser_research",
        payload={"work_order": {"work_order_id": "wo-pack"}},
    )
    capability_request, grant = _broker_binding(request)
    result = registry.dispatch(
        request,
        ports=DomainPackPlatformPorts(task_port=task_port, capability_broker=object()),
        capability_request=capability_request,
        capability_grant=grant,
    )
    assert result == {"status": "EXECUTED", "task_id": "research.browser.worker"}
    assert task_port.calls[0][1]["domain_capability_id"] == "browser_research"

    with pytest.raises(RuntimeError, match="DOMAIN_PACK_PORT_MISSING:capability_broker"):
        missing_port_request = DomainPackRequest("req-pentest", "pentest_autonomous", {})
        missing_capability_request, missing_grant = _broker_binding(missing_port_request)
        registry.dispatch(
            missing_port_request,
            ports=DomainPackPlatformPorts(task_port=task_port),
            capability_request=missing_capability_request,
            capability_grant=missing_grant,
        )


def test_pack_modules_do_not_import_platform_owners() -> None:
    root = Path(__file__).resolve().parents[2] / "host_brain" / "domain_packs"
    forbidden = (
        "host_brain.scheduler",
        "host_brain.governor",
        "event_bus",
        "SQLite",
        "get_ledger",
    )
    for name in ("research_browser.py", "media_translation.py", "pentest.py"):
        source = root.joinpath(name).read_text(encoding="utf-8")
        assert all(token not in source for token in forbidden), name


def test_low_risk_custom_pack_cannot_bypass_broker_grant_or_task_allowlist() -> None:
    handler = _CustomHandler("custom.forbidden.worker")
    registry = DomainPackRegistry()
    registry.install(DomainPack(
        manifest=DomainPackManifest(
            pack_id="custom-low-risk",
            version="1.0.0",
            domain="custom-low-risk",
            capabilities=(DomainCapabilityManifest(
                capability_id="custom_safe",
                handler_id="custom.safe",
                description="Regression fixture.",
                risk_level=1,
                allowed_task_ids=("custom.allowed.worker",),
            ),),
            required_ports=("task_port",),
        ),
        handlers={"custom.safe": handler},
    ))
    request = DomainPackRequest("req-custom", "custom_safe", {})
    task_port = _TaskPort()
    with pytest.raises(DomainPackResolutionError) as missing:
        registry.dispatch(
            request,
            ports=DomainPackPlatformPorts(task_port=task_port),
            capability_request=None,  # type: ignore[arg-type]
            capability_grant=None,  # type: ignore[arg-type]
        )
    assert missing.value.reason_code == "DOMAIN_PACK_GRANT_MISSING"
    assert handler.calls == 0

    capability_request, grant = _broker_binding(request)
    forged_request = DomainPackRequest("req-custom-forged", "custom_safe", {"forged": True})
    _forged_capability_request, forged_grant = _broker_binding(forged_request)
    with pytest.raises(DomainPackResolutionError) as mismatched:
        registry.dispatch(
            request,
            ports=DomainPackPlatformPorts(task_port=task_port),
            capability_request=capability_request,
            capability_grant=forged_grant,
        )
    assert mismatched.value.reason_code == "DOMAIN_PACK_GRANT_INVALID"
    assert handler.calls == 0

    with pytest.raises(DomainPackResolutionError) as forbidden:
        registry.dispatch(
            request,
            ports=DomainPackPlatformPorts(task_port=task_port),
            capability_request=capability_request,
            capability_grant=grant,
        )
    assert forbidden.value.reason_code == "DOMAIN_TASK_NOT_ALLOWED"
    assert handler.calls == 1
    assert task_port.calls == []


def test_disabled_pack_still_fails_inside_capability_broker() -> None:
    registry = _registry()
    registry.disable("research-browser")
    task_port = _TaskPort()
    ports = DomainPackPlatformPorts(task_port=task_port, capability_broker=object())

    def _dispatch(request, grant):  # noqa: ANN001
        try:
            return registry.dispatch(
                DomainPackRequest(request.request_id, request.capability_id, request.args),
                ports=ports,
                capability_request=request,
                capability_grant=grant,
            )
        except DomainPackResolutionError as exc:
            raise CapabilityInvocationError(exc.reason_code, str(exc)) from exc

    broker = CapabilityBroker(policy=PredicateCapabilityPolicy(
        lambda request, _descriptor: (True, "APPROVED", f"test-authority://{request.request_id}")
    ))
    broker.register(CapabilityDescriptor(
        capability_id="browser_research",
        kind=CapabilityKind.DOMAIN,
        version="1",
        risk_level=2,
        adapter=_dispatch,
    ))
    receipt = broker.execute(CapabilityRequest(
        request_id="req-disabled-pack",
        attempt_id="attempt-disabled-pack",
        idempotency_key="disabled-pack-key",
        capability_id="browser_research",
        kind=CapabilityKind.DOMAIN,
        args={},
        context={},
    ))
    assert receipt.success is False
    assert receipt.reason_code == "DOMAIN_PACK_DISABLED"
    assert task_port.calls == []
