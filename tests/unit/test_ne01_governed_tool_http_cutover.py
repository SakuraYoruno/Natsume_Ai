from __future__ import annotations

import hashlib
import inspect
import json
from types import SimpleNamespace

import pytest

from host_brain.app.governed_tool_runtime import (
    FrozenToolRouteExecutor,
    GovernedToolExecutionError,
    GovernedToolHttpApplication,
)
from host_brain.app.tool_routes import execute_tool
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.orchestration.governor_grant import governor_grant_ref
from host_brain.secure_ingress.authority import SecureIngressAuthority
from host_brain.skills.capability_scorecard_provider import (
    FederatedCapabilityScorecardProvider,
)
from host_brain.task_runtime.governed_workers import (
    FrozenNodeInput,
    GovernedFrozenNodeWorker,
)
from host_brain.tools.broker import ToolExecutionResult
from host_brain.tools.catalog import ToolCatalog


def _binding() -> dict:
    return {
        "schema_version": "Natsume.tool.execution_binding.v1",
        "tool_id": "read_status",
        "tool_version": "1",
        "schema_hash": "sha256:" + "a" * 64,
        "side_effect_level": "READ",
        "auth_level": "NONE",
        "handler_adapter_id": "tool-handler://sha256/" + "b" * 64,
        "execution_safety": "SAFE_LOCAL_REPLAY",
        "binding_digest": "sha256:" + "c" * 64,
    }


def _catalog() -> ToolCatalog:
    catalog = ToolCatalog()
    catalog.register(
        {
            "id": "read_status",
            "name": "read status",
            "desc": "read a status",
            "schema_hash": "sha256:" + "a" * 64,
            "side_effect_level": "READ",
            "auth_level": "NONE",
            "domain_tags": ["test"],
            "version": "1",
        }
    )
    return catalog


def _terminal() -> dict:
    return {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "audit_refs": {
            "governed_tool_execution": {
                "status": "SUCCESS",
                "result_code": "TOOL_EXECUTED",
                "tool_id": "read_status",
                "evidence_ref": "evidence::read-status",
                "output": {"status": "ok"},
            }
        },
        "execution_receipt": {
            "accepted": True,
            "status": "ACCEPTED",
            "receipt_ref": "receipt://tool/read-status",
            "command_id": "command-tool-read-status",
        },
    }


class _Ingress:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, *, raw_body: bytes, **_kwargs):  # noqa: ANN001, ANN201
        self.calls += 1
        return SecureIngressAuthority(
            allowed=self.calls == 1,
            duplicate=self.calls > 1,
            reason_code="ALLOW" if self.calls == 1 else "IDEMPOTENCY_DUPLICATE",
            request_id="request-tool-cutover",
            principal_id="principal-tool-cutover",
            claim_ref="secure-ingress-claim://tool-cutover",
            audit_ref="secure-ingress-audit://tool-cutover",
            body_sha256=hashlib.sha256(raw_body).hexdigest(),
            existing_result_ref=(
                None if self.calls == 1 else "receipt://tool/read-status"
            ),
        )


class _Engine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate_intent(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(dict(kwargs))
        intent = kwargs["raw_intent"]
        return {
            "request_id": kwargs["request_id"],
            "intent_id": intent["id"],
            "decision": "APPROVE",
            "approved_action_binding": {"risk_level": 1},
            "tokens": {"approval_token_ref": "approval::parent"},
            "ts_ms": kwargs["current_ms"],
            "committed_route_token_ref": "GOV_OK_RULE",
            "committed_route_token": {"token": "GOV_OK_RULE"},
        }


class _Runtime:
    def __init__(self) -> None:
        self.execute_calls: list[dict] = []
        self.replay_calls: list[dict] = []

    async def execute_stream(self, **kwargs):  # noqa: ANN003, ANN201
        self.execute_calls.append(dict(kwargs))
        yield _terminal()

    async def replay_tool_stream(self, **kwargs):  # noqa: ANN003, ANN201
        self.replay_calls.append(dict(kwargs))
        yield _terminal()


class _IngressStore:
    def __init__(self) -> None:
        self.completed: list[dict] = []

    def complete_claim(self, **kwargs):  # noqa: ANN003, ANN201
        self.completed.append(dict(kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ("RECOVERY_REQUIRED", "RETRY_PENDING"))
async def test_nonterminal_runtime_status_never_seals_ingress_claim(
    terminal_status: str,
) -> None:
    class OpenClaimIngress:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, *, raw_body: bytes, **_kwargs):  # noqa: ANN001, ANN201
            self.calls += 1
            return SecureIngressAuthority(
                allowed=self.calls == 1,
                duplicate=self.calls > 1,
                reason_code="ALLOW" if self.calls == 1 else "IDEMPOTENCY_DUPLICATE",
                request_id="request-tool-open-claim",
                principal_id="principal-tool-cutover",
                claim_ref="secure-ingress-claim://tool-open-claim",
                audit_ref="secure-ingress-audit://tool-open-claim",
                body_sha256=hashlib.sha256(raw_body).hexdigest(),
                existing_result_ref=None,
            )

    class NonterminalRuntime(_Runtime):
        @staticmethod
        def terminal() -> dict:
            return {
                "phase": "TERMINAL",
                "done": True,
                "decision": "REJECT",
                "reason_code": "GOVERNED_EXECUTION_IN_PROGRESS",
                "execution_receipt": {
                    "accepted": False,
                    "status": terminal_status,
                    "receipt_ref": None,
                    "command_id": "command-tool-open-claim",
                },
            }

        async def execute_stream(self, **kwargs):  # noqa: ANN003, ANN201
            self.execute_calls.append(dict(kwargs))
            yield self.terminal()

        async def replay_tool_stream(self, **kwargs):  # noqa: ANN003, ANN201
            self.replay_calls.append(dict(kwargs))
            yield self.terminal()

    raw = b'{"tool_id":"read_status","open":true}'
    runtime = NonterminalRuntime()
    engine = _Engine()
    store = _IngressStore()
    application = GovernedToolHttpApplication(
        catalog=_catalog(),
        governed_runtime=runtime,
        intent_engine=engine,
        ingress_adapter=OpenClaimIngress(),
        ingress_store=store,
        tool_binding_resolver=lambda _tool_id: _binding(),
        governance_snapshot_builder=lambda **_kwargs: {},
        clock_ms=lambda: 100,
    )
    body = SimpleNamespace(
        tool_id="read_status",
        intent_id="client-intent",
        session_id="session-tool-cutover",
        seq=1,
        args={},
        context={},
        thread_id=None,
        turn_id=None,
    )

    for _ in range(2):
        with pytest.raises(GovernedToolExecutionError):
            await application.execute(body=body, request=object(), raw_body=raw)

    assert store.completed == []
    assert len(engine.calls) == 1
    assert len(runtime.execute_calls) == 1
    assert len(runtime.replay_calls) == 1


@pytest.mark.asyncio
async def test_secure_ingress_duplicate_replays_task_without_second_governor() -> None:
    raw = b'{"tool_id":"read_status"}'
    ingress = _Ingress()
    engine = _Engine()
    runtime = _Runtime()
    store = _IngressStore()
    application = GovernedToolHttpApplication(
        catalog=_catalog(),
        governed_runtime=runtime,
        intent_engine=engine,
        ingress_adapter=ingress,
        ingress_store=store,
        tool_binding_resolver=lambda _tool_id: _binding(),
        governance_snapshot_builder=lambda **_kwargs: {
            "trust_level": 100,
            "energy_level": 100,
        },
        clock_ms=lambda: 100,
    )
    body = SimpleNamespace(
        tool_id="read_status",
        intent_id="client-intent",
        session_id="session-tool-cutover",
        seq=1,
        args={"query": "safe"},
        context={"workspace": "isolated"},
        thread_id=None,
        turn_id=None,
    )

    first = await application.execute(body=body, request=object(), raw_body=raw)
    replay = await application.execute(body=body, request=object(), raw_body=raw)

    assert first == replay
    assert len(engine.calls) == 1
    assert engine.calls[0]["registry_override"].get("read_status").skill_name == "read_status"
    assert len(runtime.execute_calls) == 1
    assert len(runtime.replay_calls) == 1
    assert runtime.execute_calls[0]["intent_draft"]["args"] == {"query": "safe"}
    assert runtime.execute_calls[0]["command_authority"]["ingress_claim_ref"].startswith(
        "secure-ingress-claim://"
    )
    assert store.completed[0]["result_ref"] == "receipt://tool/read-status"


@pytest.mark.asyncio
async def test_denied_ingress_never_reaches_governor_or_runtime() -> None:
    class DeniedIngress:
        def evaluate(self, **_kwargs):  # noqa: ANN003, ANN201
            return SecureIngressAuthority.denied("DENIED")

    engine = _Engine()
    runtime = _Runtime()
    application = GovernedToolHttpApplication(
        catalog=_catalog(),
        governed_runtime=runtime,
        intent_engine=engine,
        ingress_adapter=DeniedIngress(),
        ingress_store=_IngressStore(),
        tool_binding_resolver=lambda _tool_id: _binding(),
        governance_snapshot_builder=lambda **_kwargs: {},
    )
    with pytest.raises(GovernedToolExecutionError) as raised:
        await application.execute(
            body=SimpleNamespace(
                tool_id="read_status",
                intent_id="client-intent",
                session_id="session",
            ),
            request=object(),
            raw_body=b"{}",
        )
    assert raised.value.status_code == 403
    assert engine.calls == []
    assert runtime.execute_calls == []
    assert runtime.replay_calls == []


def test_http_adapter_contains_no_direct_tool_broker_execution() -> None:
    source = inspect.getsource(execute_tool)
    assert "broker.execute" not in source
    assert "ToolBroker" not in source
    assert "governed_tool_application_provider" in source


def test_local_tool_registration_never_bootstraps_maturity_above_l2() -> None:
    provider = FederatedCapabilityScorecardProvider(
        skill_registry=SimpleNamespace(get=lambda _key: None, list_skill_names=lambda: []),
        tool_catalog=_catalog(),
        clock=lambda: 100,
    )
    card = provider("read_status")
    assert card is not None
    assert card.maturity_level <= 2


def _frozen(*, grant_ref: str, input_pack: dict, risk: int = 1) -> FrozenNodeInput:
    return FrozenNodeInput(
        command_id="command-tool",
        request_id="request-tool",
        session_id="session-tool",
        principal_id="principal-tool",
        work_order_id="work-order-tool",
        root_work_order_id="work-order-tool",
        graph_id="graph-tool",
        graph_revision=1,
        node_id="node-tool",
        attempt_id="attempt-tool",
        lease_id="lease-tool",
        lease_owner_id="worker-tool",
        fencing_token=1,
        node_risk_level=risk,
        route="tool",
        target_id="read_status",
        objective="read status",
        input_refs=("input://tool",),
        _input_pack_json=json.dumps(input_pack, sort_keys=True),
        _input_artifacts_json="[]",
        node_grant_ref=grant_ref,
        parent_grant_ref="governor-decision://parent",
        _budget_json=json.dumps(
            {
                "budget_id": "budget-tool",
                "status": "ACTIVE",
                "status_version": 1,
                "remaining": {
                    "token_count": 0,
                    "time_ms": 100,
                    "retry_count": 0,
                    "model_cost_micros": 0,
                },
            }
        ),
    )


@pytest.mark.asyncio
async def test_frozen_executor_restart_reconciles_before_second_handler_call() -> None:
    child_intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "node-grant-intent::tool",
        "ts_ms": 100,
        "session_id": "session-tool",
        "seq": 1_000_001,
        "person_id": "principal-tool",
        "principal_id": "principal-tool",
        "type": "query",
        "action": "tool:read_status",
        "target": "host",
        "args": {},
        "nonce": "nonce::node-tool",
    }
    binding = build_governed_action_binding(
        intent_envelope=child_intent,
        approval_token_ref="approval::durable-child",
        risk_level=1,
        issued_at_ms=100,
        expires_at_ms=10_000,
    )
    node = {
        "request_id": "request-tool",
        "graph_id": "graph-tool",
        "graph_revision": 1,
        "root_work_order_id": "work-order-tool",
        "node_id": "node-tool",
        "route": "tool",
        "target_id": "read_status",
        "action_kind": "tool:read_status",
        "token_subject": "read_status",
    }
    decision = {
        "request_id": "request-tool",
        "intent_id": child_intent["id"],
        "decision": "APPROVE",
        "tokens": {"approval_token_ref": "approval::durable-child"},
        "approved_action_binding": binding,
        "snapshot_refs": {"node_grant": node, "node_intent": child_intent},
        "ts_ms": 100,
    }
    grant_ref = governor_grant_ref("request-tool", decision)

    class Ledger:
        def __init__(self) -> None:
            self.skill_exec_log: list[dict] = []

        def trace_request(self, request_id):  # noqa: ANN001, ANN201
            assert request_id == "request-tool"
            return {
                "governor_log": [decision],
                "skill_exec_log": list(self.skill_exec_log),
            }

        def log_skill_exec(self, row):  # noqa: ANN001, ANN201
            self.skill_exec_log.append(dict(row))

    class Broker:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def resolve_execution_binding(self, tool_id):  # noqa: ANN001, ANN201
            assert tool_id == "read_status"
            return _binding()

    class Port:
        def __init__(self, broker, ledger):  # noqa: ANN001
            self._broker = broker
            self._ledger = ledger

        def execute_bound(self, **kwargs):  # noqa: ANN003, ANN201
            assert isinstance(kwargs.pop("task_authority"), FrozenNodeInput)
            assert kwargs.pop("expected_binding") == _binding()
            before_handler = kwargs.pop("before_handler")
            assert self._ledger.skill_exec_log == []
            before_handler()
            assert self._ledger.skill_exec_log[0]["result_status"] == "PENDING"
            self._broker.calls.append(dict(kwargs))
            return ToolExecutionResult(
                status="SUCCESS",
                result_code="TOOL_EXECUTED",
                tool_id="read_status",
                intent_id=kwargs["intent_id"],
                evidence_ref="evidence::tool",
                detail={"result_digest": "sha256:" + "b" * 64},
                output={"status": "ok"},
            )

    broker = Broker()
    ledger = Ledger()
    executor = FrozenToolRouteExecutor(
        binding_resolver=broker.resolve_execution_binding,
        execution_port=Port(broker, ledger),
        catalog=_catalog(),
        ledger=ledger,
    )
    frozen = _frozen(
        grant_ref=grant_ref,
        input_pack={
            "intent_draft": {
                "route": "tool",
                "target_id": "read_status",
                "args": {"query": "safe"},
                "context": {"workspace": "isolated"},
                "tool_binding": _binding(),
                "client_intent_id": "client-intent",
            }
        },
    )
    events = [event async for event in executor.execute_stream(frozen)]
    assert events[0]["decision"] == "APPROVE"
    assert broker.calls[0]["approval_token_ref"] == "approval::durable-child"
    assert broker.calls[0]["confirm_token_ref"] is None
    assert broker.calls[0]["intent_id"] == child_intent["id"]
    assert broker.calls[0]["args"] == {"query": "safe"}
    assert ledger.skill_exec_log[0]["result_status"] == "PENDING"
    # Simulate operation commit followed by projection loss/process restart:
    # the new executor observes the durable PENDING dispatch marker and must
    # reconcile instead of invoking the handler a second time.
    restarted_executor = FrozenToolRouteExecutor(
        binding_resolver=broker.resolve_execution_binding,
        execution_port=Port(broker, ledger),
        catalog=_catalog(),
        ledger=ledger,
    )
    with pytest.raises(GovernedToolExecutionError) as replay_blocked:
        _ = [event async for event in restarted_executor.execute_stream(frozen)]
    assert replay_blocked.value.reason_code == "GOVERNED_TOOL_RECONCILIATION_REQUIRED"
    assert len(broker.calls) == 1


def test_high_risk_tool_preview_binds_exact_args_and_context(monkeypatch) -> None:
    import host_brain.task_runtime.governed_workers as workers

    monkeypatch.setattr(workers, "context_pack_digest_from_work_order", lambda _order: "sha256:" + "c" * 64)
    monkeypatch.setattr(workers, "action_preview_scene_identity", lambda value: dict(value))
    monkeypatch.setattr(workers, "build_node_authority_context", lambda **_kwargs: {"authority": "node"})

    class Preview:
        def __init__(self) -> None:
            self.issued: dict | None = None

        def issue_receipt(self, **kwargs):  # noqa: ANN003, ANN201
            self.issued = dict(kwargs)
            return {"receipt_ref": "preview://tool", "event_id": "event-tool"}

        def require_execution(self, **_kwargs):  # noqa: ANN003, ANN201
            return {"allowed": True}

    preview = Preview()
    worker = GovernedFrozenNodeWorker(
        route_kind="tool",
        executor=SimpleNamespace(execute_stream=lambda _frozen: None),
        node_grants=SimpleNamespace(verify=lambda **_kwargs: {}),
        projections=SimpleNamespace(stage=lambda **_kwargs: {}),
        action_preview_service=preview,
    )
    pack = {
        "runtime_context_schema": "natsume.runtime-context.request.v1",
        "scene_identity_v2": {"scene_id": "scene-tool"},
        "intent_draft": {
            "route": "tool",
            "target_id": "read_status",
            "args": {"query": "exact"},
            "context": {"workspace": "workspace-exact"},
            "tool_binding": _binding(),
        },
    }
    frozen = _frozen(grant_ref="grant-tool", input_pack=pack, risk=4)
    result = worker._preview_action(  # noqa: SLF001
        command=SimpleNamespace(command_id="command-tool"),
        work_order=SimpleNamespace(input_pack=pack),
        frozen=frozen,
        verified_grant={"grant": "verified"},
    )
    assert result["receipt_ref"] == "preview://tool"
    assert preview.issued is not None
    assert preview.issued["action_args"] == {
        "tool_id": "read_status",
        "args": {"query": "exact"},
        "context": {"workspace": "workspace-exact"},
        "tool_binding": _binding(),
    }
