"""生产 Domain Pack 的规范持久执行与生命周期入口。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.capability_plane.broker import (
    CapabilityBroker,
    CapabilityDescriptor,
    CapabilityInvocationError,
)
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.policies import PredicateCapabilityPolicy
from host_brain.core.ids import now_ms
from host_brain.domain_packs.production_composition import (
    ProductionDomainPackRuntime,
    build_production_domain_pack_runtime,
)
from host_brain.domain_packs.registry import DomainPackResolutionError

from host_brain.domain_packs.production_jobs import (
    AgentProductionJob, GOVERNED_PRODUCTION_EXECUTION_MODE, STATUS_COMPLETED, STATUS_FAILED,
    STATUS_QUEUED, STATUS_RUNNING, _persist_job, _purchase_confirmation_missing,
    _read_json_file, _result_contract, _validate_production_boundary,
)
from host_brain.domain_packs.lightnovel.delivery import (
    _maybe_push_browser_research_to_gateway, _maybe_push_lightnovel_epub_to_gateway,
    _maybe_push_lightnovel_recommendations_to_gateway,
)
from host_brain.domain_packs.lightnovel import research as lightnovel_research
from host_brain.domain_packs.lightnovel.research import _recover_lightnovel_recommender_result


_PRODUCTION_AUTHORITY_VERIFIER_OVERRIDE: Callable[
    [Mapping[str, str], AgentWorkOrder], str | None
] | None = None

def _verify_durable_production_authority(
    authority: Mapping[str, str],
    order: AgentWorkOrder,
    *,
    authority_verifier: Callable[[Mapping[str, str], AgentWorkOrder], str | None],
) -> str | None:
    """Delegate validation to the explicitly injected application adapter."""

    return authority_verifier(authority, order)


def _production_policy(
    request: CapabilityRequest,
    _descriptor: CapabilityDescriptor,
    *,
    authority_verifier: Callable[[Mapping[str, str], AgentWorkOrder], str | None] | None = None,
) -> tuple[bool, str, str]:
    # 生产 Pack 只接受 Task Runtime 已持久化的 Governor 授权。命令、尝试、图节点、
    # 租约与 fencing token 必须同时绑定，Broker 不得为自己签发这份子能力授权。
    try:
        order = AgentWorkOrder.from_mapping(dict(request.args.get("work_order") or {}))
        order.validate()
        _validate_production_boundary(order)
    except Exception as exc:
        return False, f"AGENT_PRODUCTION_POLICY_INVALID:{type(exc).__name__}", ""
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id != request.capability_id or skill_id not in AGENT_PRODUCTION_SKILL_IDS:
        return False, "AGENT_PRODUCTION_CAPABILITY_UNKNOWN", ""
    authority = dict(request.context.get("governed_execution") or {})
    if not authority:
        return False, "AGENT_PRODUCTION_GOVERNOR_GRANT_MISSING", ""
    if (
        str(authority.get("execution_mode") or "").strip() != GOVERNED_PRODUCTION_EXECUTION_MODE
        or not str(authority.get("command_id") or "").strip()
        or not str(authority.get("attempt_id") or "").strip()
        or not str(authority.get("request_id") or "").strip()
        or not str(authority.get("principal_id") or "").strip()
        or not str(authority.get("target_id") or "").strip()
        or not str(authority.get("work_order_id") or "").strip()
        or not str(authority.get("graph_id") or "").strip()
        or int(authority.get("graph_revision") or 0) < 1
        or not str(authority.get("node_id") or "").strip()
        or not str(authority.get("lease_id") or "").strip()
        or int(authority.get("fencing_token") or 0) < 1
    ):
        return False, "AGENT_PRODUCTION_GRANT_BINDING_INVALID", ""
    expected_request_id = str(order.metadata.get("request_id") or order.work_order_id).strip()
    if (
        str(authority.get("request_id") or "").strip() != expected_request_id
        or str(authority.get("target_id") or "").strip() != skill_id
    ):
        return False, "AGENT_PRODUCTION_GRANT_IDENTITY_MISMATCH", ""
    verifier = _PRODUCTION_AUTHORITY_VERIFIER_OVERRIDE or authority_verifier
    if verifier is None:
        return False, "AGENT_PRODUCTION_AUTHORITY_VERIFIER_MISSING", ""
    durable_error = verifier(authority, order)
    if durable_error is not None:
        return False, durable_error, ""
    # These IDs are created by the durable AW02 Task Runtime only after its
    # Governor grant has been resolved. They bind this child capability grant
    # to that exact command attempt; the broker cannot self-authorize it.
    authority_ref = f"{authority['command_id']}#{authority['attempt_id']}"
    if _purchase_confirmation_missing(order):
        return False, "AGENT_PRODUCTION_CONFIRMATION_REQUIRED", ""
    return True, "AGENT_PRODUCTION_CAPABILITY_APPROVED", authority_ref


def _production_adapter(
    request: CapabilityRequest,
    grant: Any,
    *,
    domain_pack_runtime: ProductionDomainPackRuntime,
) -> dict[str, Any]:
    try:
        return domain_pack_runtime.dispatch(
            request=request,
            grant=grant,
        )
    except DomainPackResolutionError as exc:
        raise CapabilityInvocationError(exc.reason_code, str(exc)) from exc


def _compose_production_capabilities(
    *,
    capability_broker: CapabilityBroker,
    model_attempt_service: Any,
    authority_verifier: Callable[[Mapping[str, str], AgentWorkOrder], str | None],
) -> tuple[CapabilityBroker, ProductionDomainPackRuntime]:
    """将 Pack 描述符绑定到显式注入的进程权威。"""

    if capability_broker is None:
        raise ValueError("production runtime requires the process CapabilityBroker")
    if model_attempt_service is None:
        raise ValueError("production runtime requires an explicit model attempt service")
    broker = capability_broker
    runtime = build_production_domain_pack_runtime(
        capability_broker=broker,
        model_attempt_service=model_attempt_service,
        lightnovel_recommender_handler_provider=(
            lambda: lightnovel_research.execute_lightnovel_recommender_work_order
        ),
        lifecycle_task_handlers={
            "research.browser.gateway_delivery": _run_browser_research_delivery_stage,
            "media.lightnovel_recommender.postprocess": _run_lightnovel_postprocess_stage,
            "media.lightnovel_recommender.recover": _run_lightnovel_recovery_stage,
            "media.lightnovel_recommender.gateway_delivery": _run_lightnovel_delivery_stage,
            "media.epub_builder.gateway_delivery": _run_epub_delivery_stage,
        },
    )
    policy = PredicateCapabilityPolicy(
        lambda request, descriptor: _production_policy(
            request,
            descriptor,
            authority_verifier=authority_verifier,
        )
    )
    for skill_id in sorted(AGENT_PRODUCTION_SKILL_IDS):
        broker.register(
            CapabilityDescriptor(
                capability_id=skill_id,
                kind=CapabilityKind.DOMAIN,
                version="1",
                risk_level=5,
                adapter=(
                    lambda request, grant, _runtime=runtime: _production_adapter(
                        request,
                        grant,
                        domain_pack_runtime=_runtime,
                    )
                ),
                authorization_policy=policy,
            ),
            replace_existing=True,
        )
    return broker, runtime


def _execute_worker(
    order: AgentWorkOrder,
    *,
    store: AgentArtifactStore,
    run_dir: Path,
    capability_broker: CapabilityBroker | None = None,
    domain_pack_runtime: ProductionDomainPackRuntime | None = None,
) -> dict[str, Any]:
    # request 的幂等键以 command/attempt/work order/skill 组成：同一受治理尝试
    # 可稳定重放，新尝试才可有意重试过去失败的作业。
    if capability_broker is None or domain_pack_runtime is None:
        raise RuntimeError("production worker requires an explicitly composed Domain Pack runtime")
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    authority = dict(order.metadata.get("_capability_authority") or {})
    request = CapabilityRequest(
        request_id=str(order.metadata.get("request_id") or order.work_order_id),
        attempt_id=str(authority.get("attempt_id") or f"production:{order.work_order_id}"),
        # Replay is stable within one governed Task attempt, while a new
        # command/attempt may deliberately retry a previously failed order.
        idempotency_key=(
            "agent-production::"
            f"{str(authority.get('command_id') or 'missing-command')}::"
            f"{str(authority.get('attempt_id') or 'missing-attempt')}::"
            f"{order.work_order_id}::{skill_id}"
        ),
        capability_id=skill_id or "unknown",
        kind=CapabilityKind.DOMAIN,
        args={
            "work_order": order.to_dict(),
            "artifacts_root": str(store.runs_root),
            "run_dir": str(run_dir),
        },
        context={"governed_execution": authority},
        created_at_ms=now_ms(),
    )
    receipt = capability_broker.execute(request)
    if not receipt.success:
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": receipt.reason_code or "AGENT_PRODUCTION_CAPABILITY_REJECTED",
            "detail": receipt.detail,
            "capability_receipt": receipt.to_dict(),
        }
    output = dict(receipt.output) if isinstance(receipt.output, Mapping) else {"status": "EXECUTED", "output": receipt.output}
    output["capability_receipt"] = receipt.to_dict()
    return output


def _decode_lifecycle_task(payload: Mapping[str, Any]) -> tuple[str, AgentWorkOrder, dict[str, Any], Path]:
    order = AgentWorkOrder.from_mapping(dict(payload.get("work_order") or {}))
    run_dir = Path(str(payload.get("run_dir") or "")).expanduser()
    if not str(payload.get("run_dir") or "").strip():
        raise ValueError("production lifecycle task requires run_dir")
    result_ref = Path(str(payload.get("result_ref") or run_dir / "result.json")).expanduser()
    result = _read_json_file(result_ref)
    if not isinstance(result, Mapping):
        raise ValueError("production lifecycle task requires a persisted result")
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("production lifecycle task requires job_id")
    return job_id, order, dict(result), run_dir


def _run_browser_research_delivery_stage(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    job_id, order, result, run_dir = _decode_lifecycle_task(payload)
    output = _maybe_push_browser_research_to_gateway(
        job_id=job_id,
        order=order,
        result=result,
        run_dir=run_dir,
    )
    return _require_delivery_stage_outcome(output)


def _run_lightnovel_postprocess_stage(payload: Mapping[str, Any]) -> dict[str, Any]:
    _job_id, order, result, run_dir = _decode_lifecycle_task(payload)
    return _recover_lightnovel_recommender_result(order=order, result=result, run_dir=run_dir)


def _run_lightnovel_recovery_stage(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _run_lightnovel_postprocess_stage(payload)


def _run_lightnovel_delivery_stage(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    job_id, order, result, run_dir = _decode_lifecycle_task(payload)
    output = _maybe_push_lightnovel_recommendations_to_gateway(
        job_id=job_id,
        order=order,
        result=result,
        run_dir=run_dir,
    )
    return _require_delivery_stage_outcome(output)


def _run_epub_delivery_stage(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    job_id, order, result, run_dir = _decode_lifecycle_task(payload)
    output = _maybe_push_lightnovel_epub_to_gateway(
        job_id=job_id,
        order=order,
        result=result,
        run_dir=run_dir,
    )
    return _require_delivery_stage_outcome(output)


def _require_delivery_stage_outcome(output: dict[str, Any] | None) -> dict[str, Any] | None:
    if isinstance(output, Mapping) and output.get("success") is False:
        raise CapabilityInvocationError(
            str(output.get("reason_code") or "AGENT_PRODUCTION_DELIVERY_FAILED"),
            str(output.get("detail") or "user-visible delivery failed"),
        )
    return output


def _stable_lifecycle_identity(
    *,
    order: AgentWorkOrder,
    stage_id: str,
    phase: str,
) -> tuple[str, str | None]:
    root_request_id = str(order.metadata.get("request_id") or order.work_order_id).strip()
    encoded = "\x1f".join(
        (root_request_id, order.work_order_id, str(order.metadata.get("skill_id") or ""), phase, stage_id)
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if phase == "delivery":
        delivery_id = f"delivery://agent-production/{digest}"
        return delivery_id, delivery_id
    return f"production-stage://{digest}", None


def _execute_lifecycle_stage(
    *,
    job_id: str,
    order: AgentWorkOrder,
    store: AgentArtifactStore,
    run_dir: Path,
    stage: Any,
    capability_broker: CapabilityBroker,
) -> tuple[Any, str | None]:
    stage_id = str(stage.stage_id)
    phase = str(stage.phase)
    request_id, delivery_id = _stable_lifecycle_identity(
        order=order,
        stage_id=stage_id,
        phase=phase,
    )
    authority = dict(order.metadata.get("_capability_authority") or {})
    lifecycle_binding = {
        "stage_id": stage_id,
        "phase": phase,
        "task_id": str(stage.task_id),
        "delivery_id": delivery_id,
    }
    stable_run_dir = run_dir.resolve()
    stable_artifacts_root = Path(store.runs_root).resolve()
    request = CapabilityRequest(
        request_id=request_id,
        attempt_id=f"production-stage-attempt:{request_id.rsplit('/', 1)[-1]}",
        idempotency_key=f"agent-production-stage::{request_id}",
        capability_id=str(order.metadata.get("skill_id") or "unknown").strip(),
        kind=CapabilityKind.DOMAIN,
        args={
            "work_order": order.to_dict(),
            "artifacts_root": str(stable_artifacts_root),
            "run_dir": str(stable_run_dir),
            "result_ref": str(stable_run_dir / "result.json"),
            "job_id": job_id,
            "lifecycle_stage": lifecycle_binding,
        },
        context={
            "governed_execution": authority,
            "lifecycle_stage": lifecycle_binding,
        },
        created_at_ms=now_ms(),
    )
    return capability_broker.execute(request), delivery_id


def _run_declared_lifecycle_phase(
    *,
    phase: str,
    job_id: str,
    order: AgentWorkOrder,
    store: AgentArtifactStore,
    run_dir: Path,
    result: Mapping[str, Any],
    capability_broker: CapabilityBroker | None = None,
    domain_pack_runtime: ProductionDomainPackRuntime | None = None,
) -> dict[str, Any]:
    if capability_broker is None or domain_pack_runtime is None:
        raise RuntimeError("production lifecycle requires an explicitly composed Domain Pack runtime")
    current = dict(result)
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    resolution = domain_pack_runtime.registry.resolve(skill_id)
    stages = tuple(stage for stage in resolution.lifecycle_stages if str(stage.phase) == phase)
    for stage in stages:
        store.write_json(run_dir / "result.json", current)
        receipt, delivery_id = _execute_lifecycle_stage(
            job_id=job_id,
            order=order,
            store=store,
            run_dir=run_dir,
            stage=stage,
            capability_broker=capability_broker,
        )
        receipts = dict(current.get("lifecycle_receipts") or {})
        receipts[str(stage.stage_id)] = receipt.to_dict()
        if not receipt.success:
            risk_flags = list(dict.fromkeys([
                *(str(flag) for flag in current.get("risk_flags", []) if str(flag).strip()),
                f"lifecycle_stage_failed:{stage.stage_id}",
            ]))
            current["risk_flags"] = risk_flags
            current["lifecycle_receipts"] = receipts
            if bool(stage.required):
                current.update({
                    "status": "FAILED",
                    "success": False,
                    "reason_code": receipt.reason_code or "AGENT_PRODUCTION_STAGE_FAILED",
                    "detail": receipt.detail,
                })
                break
            continue

        output = receipt.output
        if phase in {"postprocess", "recovery"} and isinstance(output, Mapping):
            current = dict(output)
        current["lifecycle_receipts"] = receipts
        if phase == "delivery":
            delivery_record = (
                dict(output)
                if isinstance(output, Mapping)
                else {"status": "SKIPPED", "success": False, "output": output}
            )
            delivery_record.update({
                "delivery_id": delivery_id,
                "request_id": receipt.request_id,
                "capability_receipt": receipt.to_dict(),
            })
            deliveries = dict(current.get("deliveries") or {})
            deliveries[str(stage.stage_id)] = delivery_record
            current["deliveries"] = deliveries
            if bool(stage.user_visible) and output is not None:
                current["gateway_delivery"] = delivery_record
        store.write_json(run_dir / "result.json", current)
    return current


def _execute_durable_job(
    *,
    job: AgentProductionJob,
    order: AgentWorkOrder,
    store: AgentArtifactStore,
    run_dir: Path,
    capability_broker: CapabilityBroker,
    domain_pack_runtime: ProductionDomainPackRuntime,
) -> AgentProductionJob:
    """Execute one canonical Task attempt synchronously and seal its artifacts."""

    job.status = STATUS_RUNNING
    job.detail = "Canonical production Domain Pack running."
    job.updated_ts_ms = now_ms()
    _persist_job(store, run_dir, job)
    store.write_json(
        run_dir / "result_contract.json",
        _result_contract(
            status="RUNNING",
            order=order,
            run_dir=run_dir,
            detail=job.detail,
        ).to_dict(),
    )
    try:
        result = _execute_worker(
            order,
            store=store,
            run_dir=run_dir,
            capability_broker=capability_broker,
            domain_pack_runtime=domain_pack_runtime,
        )
        store.write_json(run_dir / "result.json", result)
        result = _run_declared_lifecycle_phase(
            phase="postprocess",
            job_id=job.job_id,
            order=order,
            store=store,
            run_dir=run_dir,
            result=result,
            capability_broker=capability_broker,
            domain_pack_runtime=domain_pack_runtime,
        )
        if str(result.get("status") or "").upper() == "EXECUTED":
            result = _run_declared_lifecycle_phase(
                phase="delivery",
                job_id=job.job_id,
                order=order,
                store=store,
                run_dir=run_dir,
                result=result,
                capability_broker=capability_broker,
                domain_pack_runtime=domain_pack_runtime,
            )
        store.write_json(run_dir / "result.json", result)
        completed = str(result.get("status") or "").upper() == "EXECUTED"
        job.status = STATUS_COMPLETED if completed else STATUS_FAILED
        job.detail = str(result.get("summary") or "Production Domain Pack finished.")
        job.result = dict(result)
        job.error = None if completed else str(
            result.get("detail") or result.get("reason_code") or ""
        )
    except Exception as exc:  # pragma: no cover - terminal fail-closed guard
        job.status = STATUS_FAILED
        job.detail = "Canonical production Domain Pack failed."
        job.error = str(exc)
        job.result = {
            "status": "FAILED",
            "success": False,
            "reason_code": "AGENT_PRODUCTION_EXECUTION_FAILED",
            "detail": str(exc),
        }
        store.write_json(run_dir / "result.json", job.result)
    job.updated_ts_ms = now_ms()
    job.finished_ts_ms = job.updated_ts_ms
    _persist_job(store, run_dir, job)
    store.write_json(
        run_dir / "result_contract.json",
        _result_contract(
            status="EXECUTED" if job.status == STATUS_COMPLETED else "FAILED",
            order=order,
            run_dir=run_dir,
            detail=job.detail,
            error=job.error,
            result=job.result,
        ).to_dict(),
    )
    return job


def recover_interrupted_production_jobs(
    *,
    artifacts_root: str | Path,
    capability_broker: CapabilityBroker,
    model_attempt_service: Any,
    authority_verifier: Callable[[Mapping[str, str], AgentWorkOrder], str | None],
    limit: int = 3,
    max_age_hours: int = 72,
    push_deliveries: bool = True,
    skill_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """Recover durable artifacts through declared Pack lifecycle stages only."""

    root_value = str(artifacts_root or "").strip()
    if not root_value:
        return {
            "status": "SKIPPED",
            "reason_code": "PRODUCTION_RECOVERY_ROOT_MISSING",
            "recovered": 0,
        }
    root = Path(root_value).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    if not root.is_dir():
        return {
            "status": "SKIPPED",
            "reason_code": "PRODUCTION_RECOVERY_ROOT_NOT_FOUND",
            "root": str(root),
            "recovered": 0,
        }
    capability_broker, domain_pack_runtime = _compose_production_capabilities(
        capability_broker=capability_broker,
        model_attempt_service=model_attempt_service,
        authority_verifier=authority_verifier,
    )
    selected = frozenset(
        str(item or "").strip() for item in (skill_ids or ()) if str(item or "").strip()
    )
    store = AgentArtifactStore(root)
    now_value = now_ms()
    max_age_ms = max(1, int(max_age_hours)) * 60 * 60 * 1000
    job_paths = sorted(
        root.glob("*/production_job.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    recovered = 0
    scanned = 0
    results: list[dict[str, Any]] = []
    for job_path in job_paths:
        if recovered >= max(1, int(limit)):
            break
        run_dir = job_path.parent
        job_payload = _read_json_file(job_path)
        if not isinstance(job_payload, Mapping):
            continue
        skill_id = str(job_payload.get("skill_id") or "").strip()
        if selected and skill_id not in selected:
            continue
        if str(job_payload.get("status") or "").upper() not in {
            STATUS_QUEUED,
            STATUS_RUNNING,
            "RECOVERING",
        }:
            continue
        updated_at = int(
            job_payload.get("updated_ts_ms") or job_payload.get("created_ts_ms") or 0
        )
        if updated_at and now_value - updated_at > max_age_ms:
            continue
        result_payload = _read_json_file(run_dir / "result.json")
        order_payload = _read_json_file(run_dir / "work_order.json")
        if not isinstance(result_payload, Mapping) or not isinstance(order_payload, Mapping):
            continue
        scanned += 1
        try:
            order = AgentWorkOrder.from_mapping(dict(order_payload))
            job_id = str(
                job_payload.get("job_id") or f"agent_job_{order.work_order_id}"
            ).strip()
            result = _run_declared_lifecycle_phase(
                phase="recovery",
                job_id=job_id,
                order=order,
                store=store,
                run_dir=run_dir,
                result=result_payload,
                capability_broker=capability_broker,
                domain_pack_runtime=domain_pack_runtime,
            )
            if push_deliveries and str(result.get("status") or "").upper() == "EXECUTED":
                result = _run_declared_lifecycle_phase(
                    phase="delivery",
                    job_id=job_id,
                    order=order,
                    store=store,
                    run_dir=run_dir,
                    result=result,
                    capability_broker=capability_broker,
                    domain_pack_runtime=domain_pack_runtime,
                )
            completed = str(result.get("status") or "").upper() == "EXECUTED"
            record = AgentProductionJob(
                job_id=job_id,
                work_order_id=order.work_order_id,
                skill_id=skill_id,
                status=STATUS_COMPLETED if completed else STATUS_FAILED,
                artifacts_dir=str(run_dir),
                result_contract_ref=str(run_dir / "result_contract.json"),
                request_id=str(job_payload.get("request_id") or "").strip() or None,
                detail=str(result.get("summary") or "Recovered durable Pack lifecycle."),
                result=dict(result),
                error=None if completed else str(
                    result.get("detail") or result.get("reason_code") or ""
                ),
                created_ts_ms=int(job_payload.get("created_ts_ms") or now_value),
                updated_ts_ms=now_ms(),
                finished_ts_ms=now_ms(),
            )
            _persist_job(store, run_dir, record)
            store.write_json(run_dir / "result.json", result)
            store.write_json(
                run_dir / "result_contract.json",
                _result_contract(
                    status="EXECUTED" if completed else "FAILED",
                    order=order,
                    run_dir=run_dir,
                    detail=record.detail,
                    error=record.error,
                    result=result,
                ).to_dict(),
            )
            recovered += 1
            results.append(record.to_dict())
        except Exception as exc:
            results.append(
                {
                    "job_id": str(job_payload.get("job_id") or ""),
                    "skill_id": skill_id,
                    "status": STATUS_FAILED,
                    "reason_code": "PRODUCTION_INTERRUPTED_JOB_RECOVERY_FAILED",
                    "detail": str(exc)[:240],
                    "artifacts_dir": str(run_dir),
                }
            )
    return {
        "status": "OK",
        "root": str(root),
        "scanned": scanned,
        "recovered": recovered,
        "results": results,
    }


def execute_agent_production_payload(
    payload: dict[str, Any],
    *,
    governed_execution_context: Mapping[str, Any] | None,
    capability_broker: CapabilityBroker,
    model_attempt_service: Any,
    authority_verifier: Callable[[Mapping[str, str], AgentWorkOrder], str | None],
) -> dict[str, Any]:
    """同步执行恰好一个规范的受治理 Task 尝试。"""

    # 入参中的 WorkOrder 与上游 governed context 必须双向匹配；这阻止调用方把
    # 某次已获准的租约移植到另一技能、另一工作单或另一请求。
    if not isinstance(governed_execution_context, Mapping):
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_GOVERNED_CONTEXT_REQUIRED",
            "detail": "production Domain Packs run only inside canonical Task attempts",
        }
    governed = dict(governed_execution_context)
    required = (
        "command_id",
        "attempt_id",
        "request_id",
        "principal_id",
        "target_id",
        "work_order_id",
        "graph_id",
        "node_id",
        "lease_id",
    )
    if (
        str(governed.get("execution_mode") or "")
        != GOVERNED_PRODUCTION_EXECUTION_MODE
        or any(not str(governed.get(name) or "").strip() for name in required)
        or int(governed.get("graph_revision") or 0) < 1
        or int(governed.get("fencing_token") or 0) < 1
    ):
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_GOVERNED_CONTEXT_INVALID",
            "detail": "governed production requires a bound command, lease and attempt",
        }
    raw_order = dict(payload.get("work_order") or {})
    if not raw_order:
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_WORKORDER_REQUIRED",
            "detail": "payload.work_order is required",
        }
    raw_metadata = dict(raw_order.get("metadata") or {})
    expected_request = str(
        raw_metadata.get("request_id") or raw_order.get("work_order_id") or ""
    ).strip()
    expected_target = str(raw_metadata.get("skill_id") or "").strip()
    if (
        str(governed.get("request_id") or "").strip() != expected_request
        or str(governed.get("target_id") or "").strip() != expected_target
        or str(governed.get("work_order_id") or "").strip()
        != str(raw_order.get("work_order_id") or "").strip()
    ):
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_GOVERNED_CONTEXT_BINDING_INVALID",
            "detail": "governed identity does not match the WorkOrder",
        }
    artifacts_root = str(
        payload.get("artifacts_root")
        or payload.get("runs_root")
        or raw_metadata.get("artifacts_root")
        or ""
    ).strip()
    store = AgentArtifactStore(artifacts_root or None)
    order = AgentWorkOrder.from_mapping(raw_order)
    order.metadata["_capability_authority"] = {
        "execution_mode": GOVERNED_PRODUCTION_EXECUTION_MODE,
        **{name: governed.get(name) for name in required},
        "graph_revision": int(governed.get("graph_revision") or 0),
        "fencing_token": int(governed.get("fencing_token") or 0),
    }
    run_dir = store.prepare_run_dir(order.work_order_id)
    try:
        order.validate()
        _validate_production_boundary(order)
    except Exception as exc:
        store.write_json(run_dir / "work_order.json", order.to_dict())
        store.write_json(
            run_dir / "result_contract.json",
            _result_contract(
                status="FAILED",
                order=order,
                run_dir=run_dir,
                detail=str(exc),
                error=str(exc),
            ).to_dict(),
        )
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_CONTRACT_INVALID",
            "detail": str(exc),
            "work_order_id": order.work_order_id,
            "artifacts_dir": str(run_dir),
        }
    if _purchase_confirmation_missing(order):
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_CONFIRMATION_REQUIRED",
            "detail": "explicit local purchase confirmation is required",
            "work_order_id": order.work_order_id,
            "artifacts_dir": str(run_dir),
            "start_background": False,
        }
    broker, domain_runtime = _compose_production_capabilities(
        capability_broker=capability_broker,
        model_attempt_service=model_attempt_service,
        authority_verifier=authority_verifier,
    )
    job_id = str(payload.get("job_id") or f"agent_job_{order.work_order_id}").strip()
    job = AgentProductionJob(
        job_id=job_id,
        work_order_id=order.work_order_id,
        skill_id=str(order.metadata.get("skill_id") or "").strip(),
        status=STATUS_RUNNING,
        artifacts_dir=str(run_dir),
        result_contract_ref=str(run_dir / "result_contract.json"),
        request_id=str(order.metadata.get("request_id") or "").strip() or None,
        detail="Canonical production Task attempt accepted.",
    )
    store.write_json(run_dir / "work_order.json", order.to_dict())
    job = _execute_durable_job(
        job=job,
        order=order,
        store=store,
        run_dir=run_dir,
        capability_broker=broker,
        domain_pack_runtime=domain_runtime,
    )
    result = dict(job.result or {})
    completed = job.status == STATUS_COMPLETED
    return {
        **result,
        "status": str(result.get("status") or "EXECUTED") if completed else "FAILED",
        "result_code": (
            str(result.get("result_code") or "AGENT_PRODUCTION_COMPLETED")
            if completed
            else str(result.get("reason_code") or "AGENT_PRODUCTION_EXECUTION_FAILED")
        ),
        "reason_code": None if completed else str(
            result.get("reason_code") or "AGENT_PRODUCTION_EXECUTION_FAILED"
        ),
        "job_id": job.job_id,
        "work_order_id": order.work_order_id,
        "skill_id": job.skill_id,
        "queued": False,
        "agent_background": False,
        "start_background": False,
        "artifacts_dir": str(run_dir),
        "result_contract_ref": str(run_dir / "result_contract.json"),
        "governed_execution": {
            "command_id": str(governed.get("command_id") or ""),
            "attempt_id": str(governed.get("attempt_id") or ""),
        },
    }


__all__ = [
    "GOVERNED_PRODUCTION_EXECUTION_MODE",
    "execute_agent_production_payload",
    "recover_interrupted_production_jobs",
]
