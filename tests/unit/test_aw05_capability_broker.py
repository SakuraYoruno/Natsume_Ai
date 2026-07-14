from __future__ import annotations

from types import SimpleNamespace
import threading
import time

from host_brain.capability_plane.broker import (
    CapabilityBroker,
    CapabilityDescriptor,
    InMemoryCapabilityAuditSink,
)
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest, ReceiptStatus
from host_brain.capability_plane.policies import DescriptorCapabilityPolicy, PredicateCapabilityPolicy
from host_brain.integrations.graph_governed_tool_node import GovernedToolNode


def _request(*, key: str = "key-1", args: dict | None = None, deadline: int | None = None) -> CapabilityRequest:
    return CapabilityRequest(
        request_id="req-1",
        attempt_id="attempt-1",
        idempotency_key=key,
        capability_id="demo",
        kind=CapabilityKind.TOOL,
        args=args or {},
        context={},
        created_at_ms=1,
        deadline_at_ms=deadline,
    )


def _allow_policy() -> PredicateCapabilityPolicy:
    return PredicateCapabilityPolicy(lambda request, _descriptor: (True, "OK", f"policy://{request.request_id}"))


def _composed_broker() -> CapabilityBroker:
    return CapabilityBroker(policy=DescriptorCapabilityPolicy())


def test_broker_owns_grant_invocation_idempotency_and_audit() -> None:
    calls: list[dict] = []
    audit = InMemoryCapabilityAuditSink()
    broker = CapabilityBroker(policy=_allow_policy(), audit_sink=audit)
    broker.register(CapabilityDescriptor(
        capability_id="demo",
        kind=CapabilityKind.TOOL,
        version="1",
        risk_level=1,
        adapter=lambda request, grant: calls.append(dict(request.args)) or {"grant_id": grant.grant_id},
    ))

    first = broker.execute(_request(args={"value": 1}))
    replay = broker.execute(_request(args={"value": 1}))

    assert first.status is ReceiptStatus.SUCCEEDED
    assert replay.success and replay.replayed is True
    assert len(calls) == 1
    assert len(audit.records) == 1
    assert first.grant_id
    assert broker._claim_locks == {}  # noqa: SLF001


def test_broker_rejects_unknown_and_idempotency_collision() -> None:
    broker = CapabilityBroker(policy=_allow_policy())
    unknown = broker.execute(_request())
    assert unknown.reason_code == "CAPABILITY_NOT_REGISTERED"

    broker.register(CapabilityDescriptor("demo", CapabilityKind.TOOL, "1", 1, lambda *_: "ok"))
    first = broker.execute(_request(key="bound", args={"value": 1}))
    collision = broker.execute(_request(key="bound", args={"value": 2}))
    assert first.success
    assert collision.reason_code == "CAPABILITY_IDEMPOTENCY_CONFLICT"


def test_broker_fails_closed_when_policy_returns_no_grant() -> None:
    class _BrokenPolicy:
        def authorize(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return None

    broker = CapabilityBroker(policy=_BrokenPolicy())
    broker.register(CapabilityDescriptor("demo", CapabilityKind.TOOL, "1", 1, lambda *_: "must not run"))
    receipt = broker.execute(_request())
    assert receipt.status is ReceiptStatus.REJECTED
    assert receipt.reason_code == "CAPABILITY_GRANT_MISSING"


def test_graph_tool_node_fails_closed_without_governor_or_on_governor_error() -> None:
    calls: list[str] = []
    tool = SimpleNamespace(name="demo", func=lambda **_kwargs: calls.append("called"))
    missing = GovernedToolNode(tools=[tool], capability_broker=_composed_broker())({
        "request_id": "req-missing-governor",
        "tool_calls": [{"tool": "demo", "args": {}}],
    })

    class _BrokenGovernor:
        def evaluate_tool_call(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("down")

    broken = GovernedToolNode(
        tools=[tool],
        governor=_BrokenGovernor(),
        capability_broker=_composed_broker(),
    )({
        "request_id": "req-broken-governor",
        "tool_calls": [{"tool": "demo", "args": {}}],
    })
    assert calls == []
    assert missing["committed_route_token"] == "GOVERNOR_MISSING"
    assert broken["committed_route_token"].startswith("GOVERNOR_UNAVAILABLE")


def test_concurrent_same_idempotency_key_is_single_flight() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def _adapter(*_args):  # noqa: ANN002
        calls.append(1)
        entered.set()
        assert release.wait(timeout=2)
        return {"ok": True}

    broker = CapabilityBroker(policy=_allow_policy())
    broker.register(CapabilityDescriptor("demo", CapabilityKind.TOOL, "1", 1, _adapter))
    receipts = []
    first = threading.Thread(target=lambda: receipts.append(broker.execute(_request())))
    second = threading.Thread(target=lambda: receipts.append(broker.execute(_request())))
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    time.sleep(0.03)
    assert calls == [1]
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(receipts) == 2
    assert all(item.success for item in receipts)
    assert sum(bool(item.replayed) for item in receipts) == 1
    assert calls == [1]


def test_audit_projection_failure_does_not_repeat_completed_side_effect() -> None:
    class _BrokenAudit:
        def record(self, *_args):  # noqa: ANN002
            raise RuntimeError("projection down")

    calls: list[int] = []
    broker = CapabilityBroker(policy=_allow_policy(), audit_sink=_BrokenAudit())
    broker.register(CapabilityDescriptor(
        "demo", CapabilityKind.TOOL, "1", 1,
        lambda *_args: calls.append(1) or {"ok": True},
    ))
    first = broker.execute(_request())
    replay = broker.execute(_request())
    assert first.success and replay.success and replay.replayed
    assert calls == [1]


def test_execution_journal_failure_fails_closed_before_side_effect() -> None:
    class _BrokenJournal:
        def lookup(self, _key):
            raise RuntimeError("database unavailable")

    calls: list[int] = []
    broker = CapabilityBroker(policy=_allow_policy(), execution_journal=_BrokenJournal())
    broker.register(CapabilityDescriptor(
        "demo",
        CapabilityKind.TOOL,
        "1",
        1,
        lambda *_args: calls.append(1),
    ))

    receipt = broker.execute(_request())

    assert receipt.status is ReceiptStatus.IN_DOUBT
    assert receipt.reason_code == "CAPABILITY_JOURNAL_UNAVAILABLE"
    assert calls == []


def test_hung_capability_pool_cannot_starve_unrelated_capability() -> None:
    entered = threading.Event()
    release = threading.Event()
    fast_calls: list[int] = []
    broker = CapabilityBroker(policy=_allow_policy(), workers=1)

    def _hung(*_args):
        entered.set()
        release.wait(timeout=2)
        return "late"

    broker.register(CapabilityDescriptor("hung", CapabilityKind.TOOL, "1", 1, _hung))
    broker.register(CapabilityDescriptor(
        "fast",
        CapabilityKind.TOOL,
        "1",
        1,
        lambda *_args: fast_calls.append(1) or "fast",
    ))

    now = int(time.time() * 1000)
    hung_request = CapabilityRequest(
        request_id="hung-request",
        attempt_id="hung-attempt",
        idempotency_key="hung-key",
        capability_id="hung",
        kind=CapabilityKind.TOOL,
        created_at_ms=now,
        deadline_at_ms=now + 30,
    )
    fast_request = CapabilityRequest(
        request_id="fast-request",
        attempt_id="fast-attempt",
        idempotency_key="fast-key",
        capability_id="fast",
        kind=CapabilityKind.TOOL,
        created_at_ms=now,
        deadline_at_ms=now + 500,
    )

    hung_receipt = broker.execute(hung_request)
    assert entered.is_set()
    fast_receipt = broker.execute(fast_request)
    release.set()

    assert hung_receipt.status is ReceiptStatus.IN_DOUBT
    assert fast_receipt.success
    assert fast_calls == [1]
    broker.close()


def test_production_capability_cannot_self_sign_without_governed_attempt(tmp_path) -> None:  # noqa: ANN001
    from host_brain.agent_runtime.artifact_store import AgentArtifactStore
    from host_brain.domain_packs.production_runtime import _execute_worker
    from host_brain.agent_runtime.production_studio import build_production_work_order
    from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder

    order = AgentWorkOrder.from_mapping(build_production_work_order(
        {"task": "research", "artifacts_root": str(tmp_path)},
        skill_id="browser_research",
    ))
    from host_brain.domain_packs.production_runtime import _compose_production_capabilities

    process_broker = CapabilityBroker(policy=DescriptorCapabilityPolicy())
    broker, runtime = _compose_production_capabilities(
        capability_broker=process_broker,
        model_attempt_service=SimpleNamespace(),
        authority_verifier=lambda _authority, _order: None,
    )
    receipt = _execute_worker(
        order,
        store=AgentArtifactStore(str(tmp_path)),
        run_dir=tmp_path / "run",
        capability_broker=broker,
        domain_pack_runtime=runtime,
    )
    assert receipt["status"] == "FAILED"
    assert receipt["reason_code"] == "AGENT_PRODUCTION_GOVERNOR_GRANT_MISSING"
    broker.close()
