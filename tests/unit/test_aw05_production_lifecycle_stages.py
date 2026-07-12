from __future__ import annotations

import inspect
import time
from pathlib import Path
from types import SimpleNamespace

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.production_executor import (
    _production_policy,
    _run_background_job,
    _run_declared_lifecycle_phase,
    _verify_durable_production_authority,
    execute_agent_production_payload,
    recover_interrupted_production_jobs,
)
from host_brain.agent_runtime.production_studio import build_production_work_order
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.capability_plane.broker import CapabilityBroker, CapabilityDescriptor
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.journal import InMemoryCapabilityExecutionJournal
from host_brain.capability_plane.policies import PredicateCapabilityPolicy
from host_brain.domain_packs.contracts import DomainLifecycleStageManifest


def _governed_browser_order() -> AgentWorkOrder:
    order = AgentWorkOrder.from_mapping(build_production_work_order(
        {"task": "deliver governed research", "artifacts_root": "ignored"},
        skill_id="browser_research",
    ))
    order.metadata["request_id"] = "request-production-lifecycle"
    order.metadata["_capability_authority"] = {
        "execution_mode": "governed_task_runtime",
        "command_id": "host-command://production-lifecycle",
        "attempt_id": "attempt-production-lifecycle",
        "request_id": "request-production-lifecycle",
        "principal_id": "principal-production-lifecycle",
        "target_id": "browser_research",
        "work_order_id": order.work_order_id,
        "graph_id": "graph-production-lifecycle",
        "graph_revision": 2,
        "node_id": "node-production-lifecycle",
        "lease_id": "lease-production-lifecycle",
        "fencing_token": 7,
    }
    return order


def test_user_visible_delivery_has_stable_broker_request_and_replays_receipt(monkeypatch, tmp_path: Path) -> None:
    import host_brain.agent_runtime.production_executor as production_executor

    stage = DomainLifecycleStageManifest(
        stage_id="research.browser.gateway-delivery",
        phase="delivery",
        task_id="research.browser.gateway-delivery",
        required=False,
        user_visible=True,
    )

    class _Registry:
        def resolve(self, capability_id: str):  # noqa: ANN201
            assert capability_id == "browser_research"
            return SimpleNamespace(lifecycle_stages=(stage,))

    adapter_calls = []

    def _delivery_adapter(request, _grant):  # noqa: ANN001, ANN202
        adapter_calls.append(request)
        assert Path(request.args["result_ref"]).exists()
        return {
            "status": "DELIVERED",
            "success": True,
            "platform": "test-gateway",
            "conversation_id": "conversation-stable",
        }

    journal = InMemoryCapabilityExecutionJournal()
    broker = CapabilityBroker(
        policy=PredicateCapabilityPolicy(
            lambda request, _descriptor: (
                True,
                "TEST_DELIVERY_APPROVED",
                f"test-authority://{request.request_id}",
            )
        ),
        execution_journal=journal,
    )
    broker.register(CapabilityDescriptor(
        capability_id="browser_research",
        kind=CapabilityKind.DOMAIN,
        version="1",
        risk_level=2,
        adapter=_delivery_adapter,
    ))
    monkeypatch.setattr(production_executor, "_PRODUCTION_CAPABILITY_BROKER", broker)
    monkeypatch.setattr(
        production_executor,
        "_PRODUCTION_DOMAIN_PACK_RUNTIME",
        SimpleNamespace(registry=_Registry()),
    )

    store = AgentArtifactStore(tmp_path / "runs")
    run_dir = tmp_path / "runs" / "run-lifecycle"
    run_dir.mkdir(parents=True)
    order = _governed_browser_order()
    initial = {"status": "EXECUTED", "summary": "research complete"}

    first = _run_declared_lifecycle_phase(
        phase="delivery",
        job_id="job-production-lifecycle",
        order=order,
        store=store,
        run_dir=run_dir,
        result=initial,
    )
    replay = _run_declared_lifecycle_phase(
        phase="delivery",
        job_id="job-production-lifecycle",
        order=order,
        store=store,
        run_dir=run_dir,
        result=first,
    )

    assert len(adapter_calls) == 1
    request = adapter_calls[0]
    assert request.kind is CapabilityKind.DOMAIN
    assert request.request_id.startswith("delivery://agent-production/")
    assert request.args["lifecycle_stage"]["delivery_id"] == request.request_id
    assert request.context["lifecycle_stage"]["delivery_id"] == request.request_id
    assert "result" not in request.args
    assert journal.lookup(request.idempotency_key) is not None
    assert first["gateway_delivery"]["delivery_id"] == request.request_id
    assert replay["gateway_delivery"]["request_id"] == request.request_id
    assert replay["gateway_delivery"]["capability_receipt"]["replayed"] is True
    assert replay["lifecycle_receipts"][stage.stage_id]["replayed"] is True

    # Simulate a process crash after the external side effect and durable
    # receipt, but before the production job projection recorded delivery.
    store.write_json(run_dir / "work_order.json", order.to_dict())
    store.write_json(run_dir / "result.json", initial)
    store.write_json(run_dir / "production_job.json", {
        "job_id": "job-production-lifecycle",
        "work_order_id": order.work_order_id,
        "skill_id": "browser_research",
        "status": "RUNNING",
        "artifacts_dir": str(run_dir),
        "result_contract_ref": str(run_dir / "result_contract.json"),
        "request_id": "request-production-lifecycle",
        "created_ts_ms": 0,
        "updated_ts_ms": 0,
    })
    monkeypatch.setattr(production_executor, "_persist_production_job_memory", lambda **_kwargs: None)

    recovered = recover_interrupted_production_jobs(
        artifacts_root=store.runs_root,
        push_deliveries=True,
    )

    assert recovered["recovered"] == 1
    assert len(adapter_calls) == 1
    recovered_result = production_executor._read_json_file(run_dir / "result.json")
    assert recovered_result["gateway_delivery"]["delivery_id"] == request.request_id
    assert recovered_result["gateway_delivery"]["capability_receipt"]["replayed"] is True


def test_central_execution_and_recovery_only_traverse_declared_pack_stages() -> None:
    background_source = inspect.getsource(_run_background_job)
    recovery_source = inspect.getsource(recover_interrupted_production_jobs)
    lifecycle_source = inspect.getsource(_run_declared_lifecycle_phase)
    domain_helpers = (
        "_recover_lightnovel_recommender_result",
        "_maybe_push_browser_research_to_gateway",
        "_maybe_push_lightnovel_recommendations_to_gateway",
        "_maybe_push_lightnovel_epub_to_gateway",
    )

    assert "phase=\"postprocess\"" in background_source
    assert "phase=\"delivery\"" in background_source
    assert "phase=\"recovery\"" in recovery_source
    assert "phase=\"delivery\"" in recovery_source
    assert all(name not in background_source for name in domain_helpers)
    assert all(name not in recovery_source for name in domain_helpers)
    assert "lightnovel" not in recovery_source.lower()
    assert "browser_research" not in lifecycle_source
    assert "agent_epub_builder" not in lifecycle_source
    assert "pentest" not in lifecycle_source


def test_production_policy_fails_closed_when_durable_authority_rejects(monkeypatch) -> None:
    import host_brain.agent_runtime.production_executor as production_executor

    order = _governed_browser_order()
    authority = dict(order.metadata["_capability_authority"])
    request = CapabilityRequest(
        request_id="request-production-lifecycle",
        attempt_id="attempt-capability-policy",
        idempotency_key="policy-production-lifecycle",
        capability_id="browser_research",
        kind=CapabilityKind.DOMAIN,
        args={"work_order": order.to_dict()},
        context={"governed_execution": authority},
    )
    descriptor = CapabilityDescriptor(
        capability_id="browser_research",
        kind=CapabilityKind.DOMAIN,
        version="1",
        risk_level=2,
        adapter=lambda *_args: None,
    )
    captured = []

    def _reject_durable(candidate, candidate_order):  # noqa: ANN001, ANN202
        captured.append((dict(candidate), candidate_order.work_order_id))
        return "AGENT_PRODUCTION_DURABLE_COMMAND_BINDING_MISMATCH"

    monkeypatch.setattr(
        production_executor,
        "_PRODUCTION_AUTHORITY_VERIFIER_OVERRIDE",
        _reject_durable,
    )

    approved, reason_code, authority_ref = _production_policy(request, descriptor)

    assert approved is False
    assert reason_code == "AGENT_PRODUCTION_DURABLE_COMMAND_BINDING_MISMATCH"
    assert authority_ref == ""
    assert captured == [(authority, order.work_order_id)]
    verifier_source = inspect.getsource(_verify_durable_production_authority)
    assert "HostCommandRepository" in verifier_source
    assert "DurableTaskStore" in verifier_source
    assert "DurableGovernorGrantResolver" in verifier_source
    assert '"RUNNING"' in verifier_source


def test_raw_governed_context_cannot_change_request_or_target_identity(monkeypatch, tmp_path: Path) -> None:
    work_order = build_production_work_order(
        {
            "task": "research",
            "request_id": "request-bound-production",
            "artifacts_root": str(tmp_path),
        },
        skill_id="browser_research",
    )
    monkeypatch.setattr(
        "host_brain.agent_runtime.production_executor._run_background_job",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("forged authority reached worker")),
    )

    result = execute_agent_production_payload(
        {"work_order": work_order, "artifacts_root": str(tmp_path)},
        governed_execution_context={
            "execution_mode": "governed_task_runtime",
            "command_id": "host-command://forged",
            "attempt_id": "attempt-forged",
            "request_id": "request-other",
            "principal_id": "principal-forged",
            "target_id": "agent_epub_builder",
            "work_order_id": work_order["work_order_id"],
            "graph_id": "graph-forged",
            "graph_revision": 1,
            "node_id": "node-forged",
            "lease_id": "lease-forged",
            "fencing_token": 1,
        },
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == "AGENT_PRODUCTION_GOVERNED_CONTEXT_BINDING_INVALID"


def _patch_production_authority_rows(
    monkeypatch,
    *,
    order: AgentWorkOrder,
    attempt_node: str,
    lease_id: str,
    fence: int,
) -> None:  # noqa: ANN001
    import host_brain.orchestration as orchestration
    import host_brain.persistence.host_command_repository as command_module
    import host_brain.persistence.task_repository as task_module
    from host_brain.app import dependencies

    class _CommandRepo:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def get(self, _command_id):  # noqa: ANN001, ANN201
            return {
                "request_id": "request-production-lifecycle",
                "principal_id": "principal-production-lifecycle",
                "target_id": "browser_research",
                "route_type": "skill",
                "root_work_order_id": order.work_order_id,
                "graph_id": "graph-production-lifecycle",
                "graph_revision": 2,
                "governor_grant_ref": "grant://production-lifecycle",
            }

    class _TaskStore:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def get_attempt(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": "graph-production-lifecycle",
                "graph_revision": 2,
                "node_id": attempt_node,
                "status": "RUNNING",
            }
        def get_node(self, graph_id, revision, node_id):  # noqa: ANN001, ANN201
            return {
                "graph_id": graph_id,
                "graph_revision": revision,
                "node_id": node_id,
                "status": "RUNNING",
            }
        def get_active_lease(self, _attempt_id):  # noqa: ANN001, ANN201
            return {
                "lease_id": lease_id,
                "attempt_id": "attempt-production-lifecycle",
                "fencing_token": fence,
                "status": "ACTIVE",
                "expires_ts_ms": int(time.time() * 1000) + 60_000,
            }

    class _GrantResolver:
        def __init__(self, *_args, **_kwargs) -> None: pass
        def require(self, **_kwargs): return {"decision": "APPROVE"}  # noqa: ANN003, ANN201

    monkeypatch.setattr(
        dependencies,
        "get_ledger",
        lambda: SimpleNamespace(canonical_db_path="authority.sqlite3"),
    )
    monkeypatch.setattr(command_module, "HostCommandRepository", _CommandRepo)
    monkeypatch.setattr(task_module, "DurableTaskStore", _TaskStore)
    monkeypatch.setattr(orchestration, "DurableGovernorGrantResolver", _GrantResolver)


def test_production_authority_rejects_running_attempt_from_other_node(monkeypatch) -> None:
    order = _governed_browser_order()
    _patch_production_authority_rows(
        monkeypatch,
        order=order,
        attempt_node="node-other",
        lease_id="lease-production-lifecycle",
        fence=7,
    )

    assert _verify_durable_production_authority(
        order.metadata["_capability_authority"],
        order,
    ) == "AGENT_PRODUCTION_DURABLE_ATTEMPT_BINDING_MISMATCH"


def test_production_authority_rejects_superseded_lease_and_fence(monkeypatch) -> None:
    order = _governed_browser_order()
    _patch_production_authority_rows(
        monkeypatch,
        order=order,
        attempt_node="node-production-lifecycle",
        lease_id="lease-new",
        fence=8,
    )

    assert _verify_durable_production_authority(
        order.metadata["_capability_authority"],
        order,
    ) == "AGENT_PRODUCTION_DURABLE_LEASE_BINDING_MISMATCH"
