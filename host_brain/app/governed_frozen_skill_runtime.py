"""Frozen Skill worker and durable projection policy for governed Host tasks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import time
from typing import Any

from host_brain.app.governed_host_contracts import (
    _ACCEPTANCE_CRITERION,
    _sha256_json,
    _stable_id,
)
from host_brain.app.governed_skill_execution_ports import (
    GovernedSkillExecutionPorts,
    execute_frozen_governed_skill,
    record_governed_skill_execution,
)
from host_brain.app.governed_task_authority import (
    GOVERNED_HOST_AUTHORITY_INVALID,
    GovernedHostAdapterError,
    assert_work_order_command_binding as _assert_work_order_command_binding,
    mapping as _mapping,
    planning_node_binding as _planning_node_binding,
    require_fail_closed_direct_skill_work_order as _require_fail_closed_direct_skill_work_order,
    require_fail_closed_governed_work_order as _require_fail_closed_governed_work_order,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.orchestration import (
    DurableGovernorGrantResolver,
    GovernorGrantError,
)
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)
from host_brain.runtime.action_preview_resolver import (
    action_preview_scene_identity,
    build_node_authority_context,
)
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    build_worker_budget_usage,
    stable_worker_usage_id,
)
from natsume_contracts.task import HostCommand


GOVERNED_HOST_ROUTE_UNSUPPORTED = "GOVERNED_HOST_ROUTE_UNSUPPORTED"
GOVERNED_HOST_GOVERNOR_GRANT_MISSING = "GOVERNED_HOST_GOVERNOR_GRANT_MISSING"
GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE = (
    "GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE"
)

_MEMORY_SEARCH_SCOPES = frozenset(
    {"recent_episode", "long_term", "line", "hybrid"}
)
_MEMORY_SEARCH_SCOPE_ALIASES = {
    "recent": "recent_episode",
    "recent_episodic": "recent_episode",
    "episode": "recent_episode",
    "episodic": "recent_episode",
    "semantic": "long_term",
    "longterm": "long_term",
    "long_term_semantic": "long_term",
    "quote": "line",
    "quotes": "line",
    "dialogue": "line",
    "lines": "line",
    "line_semantic": "line",
    "mixed": "hybrid",
    "auto": "",
}
_MEMORY_RECENT_EPISODE_TAGS = frozenset(
    {"conversation", "event", "task", "reminder", "dream", "diary"}
)
_MEMORY_LINE_SEMANTIC_TAGS = frozenset(
    {"quote", "line", "dialogue", "style"}
)
_MEMORY_LONG_TERM_TAGS = frozenset(
    {"fact", "preference", "bond", "emotion"}
)


class _FrozenSkillWorker:
    def __init__(
        self,
        *,
        ledger: Any,
        execution_ports: GovernedSkillExecutionPorts,
        node_grant_verifier: DurableNodeGrantVerifier,
        projections: Any | None = None,
        action_preview_service: Any | None = None,
    ) -> None:
        self._ledger = ledger
        self._execution_ports = execution_ports
        self._node_grant_verifier = node_grant_verifier
        self._projections = projections
        self._action_preview_service = action_preview_service

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if str(command.route_type or "").strip().lower() == "skill":
            _require_fail_closed_direct_skill_work_order(work_order)
            _assert_work_order_command_binding(work_order, command)
        else:
            _require_fail_closed_governed_work_order(work_order, command=command)
        pack = dict(work_order.input_pack or {})
        node_id = str(execution_context.get("node_id") or "").strip()
        binding = _planning_node_binding(work_order, node_id=node_id)
        target = str(binding.get("target_id") or "").strip()
        route = str(binding.get("worker_route") or "").strip().lower()
        direct_intent = _mapping(pack.get("intent_draft"))
        if route == "skill" and str(direct_intent.get("route") or "").strip().lower() == "skill":
            intent_draft = direct_intent
        else:
            approved_inputs = _mapping(pack.get("approved_candidate_inputs"))
            candidate_id = str(binding.get("candidate_id") or "").strip()
            candidate_input = _mapping(approved_inputs.get(candidate_id))
            intent_draft = {
                "route": route,
                "target_id": target,
                "task": str(candidate_input.get("objective") or binding.get("objective") or "").strip(),
                "args": _mapping(candidate_input.get("args")),
            }
        if str(intent_draft.get("route") or "").strip().lower() != "skill":
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                "frozen worker accepts only a direct skill intent",
            )
        if not target or str(intent_draft.get("target_id") or "").strip() != target:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_ROUTE_UNSUPPORTED,
                "durable skill intent target differs from its frozen TaskNode binding",
            )
        if target.startswith("mcp::"):
            # Stale durable graphs must fail before Preview issuance, grant
            # reopening or registry/handler access. MCP provider execution is
            # legal only as a governed Tool node with an immutable binding.
            raise GovernedHostAdapterError(
                MCP_TASK_AUTHORITY_REQUIRED,
                "retired MCP Skill target requires a governed Tool Task node",
            )
        if target not in set(work_order.capability_requirements):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "direct skill target is absent from WorkOrderV3 capability lineage",
            )
        execution_args = _mapping(intent_draft.get("args"))
        node_grant_ref = str(execution_context.get("node_grant_ref") or "").strip()
        if not node_grant_ref:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "frozen skill execution has no node-bound child grant",
            )
        verified_grant = self._node_grant_verifier.verify(
            grant_ref=node_grant_ref,
            parent_grant_ref=str(command.governor_grant_ref or "").strip(),
            request_id=command.request_id,
            session_id=command.session_id,
            principal_id=command.principal_id,
            graph_id=str(execution_context.get("graph_id") or ""),
            graph_revision=int(execution_context.get("graph_revision") or 0),
            root_work_order_id=command.root_work_order_id,
            node_id=node_id,
            route="skill",
            target_id=target,
            attempt_id=str(execution_context.get("attempt_id") or "").strip(),
            lease_id=str(execution_context.get("lease_id") or "").strip(),
            lease_owner_id=str(execution_context.get("lease_owner_id") or "").strip(),
            fencing_token=execution_context.get("fencing_token"),
        )
        if verified_grant.grant_ref != node_grant_ref:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "durable verifier returned a different node grant",
            )
        governor_result = _load_governor_grant(
            ledger=self._ledger,
            request_id=command.request_id,
            expected_ref=node_grant_ref,
        )
        child_intent = _mapping(_mapping(governor_result.get("snapshot_refs")).get("node_intent"))
        child_intent_id = str(child_intent.get("id") or "").strip()
        if child_intent_id != verified_grant.intent_id:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "node grant child intent differs from its durable verifier projection",
            )
        child_intent = {
            **child_intent,
            "scene_decision_v2": pack.get("scene_decision_v2"),
            "scene_identity_v2": pack.get("scene_identity_v2"),
        }
        preview = self._preview_effectful_skill(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
            target=target,
            execution_args=execution_args,
            governor_result=governor_result,
            verified_grant=verified_grant.to_dict(),
        )
        _require_frozen_skill_dependencies(
            ports=self._execution_ports,
            target=target,
            args=execution_args,
        )
        outcome = await asyncio.to_thread(
            execute_frozen_governed_skill,
            ports=self._execution_ports,
            durable_authority_ledger=self._ledger,
            request_body={"args": execution_args},
            request_id=command.request_id,
            session_id=command.session_id,
            person_id=str(pack.get("person_id") or command.principal_id),
            intent_id=child_intent_id,
            skill_id=target,
            task_text=str(intent_draft.get("task") or binding.get("objective") or work_order.objective),
            user_message=str(pack.get("user_message") or work_order.objective),
            governor_result=governor_result,
            intent_draft=child_intent,
            emotion_context=_mapping(pack.get("emotion_context")),
            render_target_lang=str(pack.get("render_target_lang") or "zh"),
            governed_execution_context=execution_context,
            execution_args_override=execution_args,
            verified_node_grant=verified_grant.to_dict(),
        )
        payload = _mapping(getattr(outcome, "payload", None))
        receipt_ref = str(payload.get("receipt_ref") or "").strip()
        narrative = str(getattr(outcome, "result_text", "") or "").strip()
        success = bool(getattr(outcome, "success", False))
        error_text = str(getattr(outcome, "error_text", "") or "").strip()
        tokens = governor_result.get("tokens")
        approval_ref = (
            str(tokens.get("approval_token_ref") or "").strip()
            if isinstance(tokens, Mapping)
            else ""
        )
        record_governed_skill_execution(
            ledger=self._ledger,
            request_id=command.request_id,
            intent_id=child_intent_id,
            skill_name=target,
            result_status="SUCCESS" if success else "FAILED",
            result_code=None if success else (error_text or "GOVERNED_SKILL_EXECUTION_FAILED"),
            result_digest=narrative or error_text,
            approval_token_ref=approval_ref or None,
            ts_ms=int(time.time() * 1000),
            evidence_ref=receipt_ref,
        )
        durable_projection = self._ledger.get_event(receipt_ref)
        durable_status = (
            str(durable_projection.get("result_status") or "").strip().upper()
            if isinstance(durable_projection, Mapping)
            else ""
        )
        expected_status = "SUCCESS" if success else "FAILED"
        if durable_status != expected_status:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "skill result has no matching durable receipt projection",
            )
        commit = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE" if success else "REJECT",
            "reason_code": None if success else (
                error_text or "GOVERNED_SKILL_EXECUTION_FAILED"
            ),
            "request_id": command.request_id,
            "narrative": narrative,
            "route_type": "skill",
            "target_id": target,
            "receipt_ref": receipt_ref,
            "audit_refs": {
                "path": "governed_task_runtime:frozen_skill",
                "command_id": command.command_id,
                "attempt_id": str(execution_context.get("attempt_id") or ""),
                "skill_receipt_ref": receipt_ref,
                "node_grant_ref": node_grant_ref,
                "node_grant_digest": _sha256_json(verified_grant.to_dict()),
                **(
                    {
                        "action_preview_receipt_ref": preview["receipt_ref"],
                        "action_preview_event_id": preview["event_id"],
                    }
                    if preview is not None
                    else {"action_preview_status": "LEGACY_CONTEXT_NOT_APPLICABLE"}
                ),
            },
        }
        worker_budget = build_worker_budget_usage(
            usage_id=stable_worker_usage_id(
                graph_id=str(execution_context.get("graph_id") or ""),
                graph_revision=int(execution_context.get("graph_revision") or 0),
                node_id=node_id,
                attempt_id=str(execution_context.get("attempt_id") or ""),
                route="skill",
            ),
            observations=_trusted_skill_budget_observations(payload.get("result")),
            include_agent_tool_calls=True,
            evidence_refs=((receipt_ref,) if receipt_ref else ()),
        )
        if worker_budget is not None:
            commit["budget_usage"] = worker_budget.to_payload()
        if self._projections is not None:
            projection_body = {
                **commit,
                "command_id": command.command_id,
                "graph_id": str(execution_context.get("graph_id") or ""),
                "graph_revision": int(execution_context.get("graph_revision") or 0),
                "node_id": node_id,
                "attempt_id": str(execution_context.get("attempt_id") or ""),
                "node_grant_ref": node_grant_ref,
            }
            projection_body.pop("receipt_ref", None)
            payload_digest = _sha256_json(projection_body)
            projection_id = f"worker-projection://skill/{payload_digest.split(':', 1)[1]}"
            durable = self._projections.stage(
                projection_id=projection_id,
                projection={
                    **projection_body,
                    "projection_id": projection_id,
                    "payload_digest": payload_digest,
                    "schema_version": "Natsume.task_runtime.worker_projection.v1",
                },
            )
            if (
                not isinstance(durable, Mapping)
                or str(durable.get("projection_id") or "") != projection_id
                or str(durable.get("payload_digest") or "") != payload_digest
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "skill result could not be frozen in the canonical worker projection journal",
                )
            commit["receipt_ref"] = projection_id
        yield commit

    def _preview_effectful_skill(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        target: str,
        execution_args: Mapping[str, Any],
        governor_result: Mapping[str, Any],
        verified_grant: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        pack = dict(work_order.input_pack or {})
        if pack.get("runtime_context_schema") != "natsume.runtime-context.request.v1":
            if self._action_preview_service is not None:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "legacy WorkOrder is quarantined: effectful Skill has no canonical Runtime Context",
                )
            return None
        service = self._action_preview_service
        if service is None:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "effectful governed Skill has no app-local ActionPreview authority",
            )
        try:
            risk_level = int(execution_context.get("node_risk_level") or 0)
            kwargs = {
                "action_kind": target,
                "action_scope": target,
                "risk_level": risk_level,
                "action_args": dict(execution_args),
                "context_pack_digest": context_pack_digest_from_work_order(work_order),
                "scene_identity": action_preview_scene_identity(
                    _mapping(pack.get("scene_identity_v2"))
                ),
                "task_graph_id": str(execution_context.get("graph_id") or ""),
                "graph_revision": int(execution_context.get("graph_revision") or 0),
                "task_node_id": str(execution_context.get("node_id") or ""),
                "parent_governor_grant_ref": command.governor_grant_ref,
                "node_grant_ref": str(verified_grant.get("grant_ref") or ""),
                "node_grant": dict(verified_grant),
                "guardian_capability_id": target.lower(),
                "authority_context": build_node_authority_context(
                    command=command,
                    execution_context=execution_context,
                    purpose="skill.execute",
                ),
            }
            issued = service.issue_receipt(**kwargs)
            service.require_execution(event_id=issued["event_id"], **{
                key: value
                for key, value in kwargs.items()
                if key != "guardian_capability_id"
            })
        except ActionPreviewAuthorityError as exc:
            raise GovernedHostAdapterError(exc.reason_code, exc.detail) from exc
        return dict(issued)


def _require_frozen_skill_dependencies(
    *,
    ports: GovernedSkillExecutionPorts,
    target: str,
    args: Mapping[str, Any],
) -> None:
    skill_id = str(target or "").strip().lower()
    missing_dependency = ""
    if skill_id == "resource_manager_status":
        if ports.resource_manager is None:
            missing_dependency = "resource_manager"
    elif skill_id == "memory_store":
        if ports.episodic_store is None:
            missing_dependency = "episodic_store"
    elif skill_id == "memory_forget":
        if not bool(args.get("dry_run", False)) and ports.episodic_store is None:
            missing_dependency = "episodic_store"
    elif skill_id == "memory_search":
        scope = _frozen_memory_search_scope(args)
        if scope == "recent_episode" and ports.episodic_store is None:
            missing_dependency = "episodic_store"
        elif scope in {"long_term", "line"} and ports.semantic_recaller is None:
            missing_dependency = "semantic_recaller"
        elif (
            scope == "hybrid"
            and ports.episodic_store is None
            and ports.semantic_recaller is None
        ):
            missing_dependency = "episodic_store_or_semantic_recaller"
    if missing_dependency:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE,
            f"frozen Skill {skill_id} requires composed {missing_dependency}",
        )


def _frozen_memory_search_scope(args: Mapping[str, Any]) -> str:
    raw_scope = str(args.get("search_scope") or args.get("scope") or "").strip().lower()
    scope = _MEMORY_SEARCH_SCOPE_ALIASES.get(raw_scope, raw_scope)
    if scope in _MEMORY_SEARCH_SCOPES:
        return scope
    memory_type = str(args.get("memory_type") or "all").strip().lower()
    if memory_type not in {"episodic", "semantic", "working", "all"}:
        memory_type = "all"
    tags_raw = args.get("tags")
    tags = (
        {str(tag or "").strip().lower() for tag in tags_raw if str(tag or "").strip()}
        if isinstance(tags_raw, list)
        else set()
    )
    if memory_type == "semantic":
        return "long_term"
    if memory_type in {"episodic", "working"}:
        return "recent_episode"
    if tags & _MEMORY_LINE_SEMANTIC_TAGS:
        return "line"
    if tags & _MEMORY_RECENT_EPISODE_TAGS:
        return "recent_episode"
    if tags & _MEMORY_LONG_TERM_TAGS:
        return "long_term"
    return "recent_episode"


def _trusted_skill_budget_observations(value: Any) -> Mapping[str, Any]:
    result = value if isinstance(value, Mapping) else {}
    metadata = result.get("metadata") if isinstance(result, Mapping) else None
    if not isinstance(metadata, Mapping):
        return {}
    if str(metadata.get("execution_authority") or "").strip() != "canonical_agent_executor":
        return {}
    return {
        "model_attempts": list(metadata.get("model_attempts") or []),
        "trace": dict(metadata.get("trace") or {})
        if isinstance(metadata.get("trace"), Mapping)
        else {},
    }


class _DurableSkillProjectionReader:
    def __init__(self, ledger: Any) -> None:
        self._ledger = ledger

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        event = self._ledger.get_event(str(receipt_ref or "").strip())
        if not isinstance(event, Mapping):
            return None
        status = str(event.get("result_status") or "").strip().upper()
        if status not in {"SUCCESS", "IDEMPOTENT_REPLAY"}:
            return None
        return {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "request_id": str(event.get("request_id") or "").strip(),
            "narrative": str(event.get("result_digest") or "").strip(),
            "route_type": "skill",
            "target_id": str(event.get("skill_name") or "").strip(),
            "receipt_ref": str(event.get("evidence_ref") or receipt_ref).strip(),
            "audit_refs": {
                "path": "governed_task_runtime:durable_projection",
                "skill_receipt_ref": str(event.get("evidence_ref") or receipt_ref).strip(),
            },
        }


class _CompositeProjectionReader:
    def __init__(self, *, skill: _DurableSkillProjectionReader, workers: Any | None) -> None:
        self._skill = skill
        self._workers = workers

    def load(self, receipt_ref: str) -> Mapping[str, Any] | None:
        ref = str(receipt_ref or "").strip()
        if ref.startswith("worker-projection://") and self._workers is not None:
            value = self._workers.load(ref)
            return dict(value) if isinstance(value, Mapping) else None
        return self._skill.load(ref)


class _WorkerCommitVerifier:
    def verify(
        self,
        *,
        command: HostCommand,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        accepted = (
            str(worker_commit.get("decision") or "").strip().upper() == "APPROVE"
            and str(worker_commit.get("target_id") or "").strip()
            == str(execution_context.get("target_id") or command.target_id).strip()
            and bool(str(worker_commit.get("receipt_ref") or "").strip())
            and bool(str(worker_commit.get("narrative") or "").strip())
        )
        reason = None if accepted else "GOVERNED_WORKER_PROJECTION_INVALID"
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": _stable_id("verify", command.command_id),
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "required": True,
            "status": "PASS" if accepted else "FAIL",
            "reason_code": reason,
            "evidence_refs": list(evidence_refs),
            "checks": [{
                "check_id": "durable-worker-projection",
                "status": "PASS" if accepted else "FAIL",
                "reason_code": reason,
            }],
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class _WorkerCommitAcceptance:
    def judge(
        self,
        *,
        command: HostCommand,
        execution_context: Mapping[str, Any],
        evidence_refs: tuple[str, ...] | list[str],
        **_: Any,
    ) -> dict[str, Any]:
        refs = list(evidence_refs)
        raw_criteria = execution_context.get("node_acceptance_criteria")
        criteria_texts = tuple(
            str(value or "").strip()
            for value in (
                raw_criteria
                if isinstance(raw_criteria, (list, tuple))
                else (_ACCEPTANCE_CRITERION,)
            )
            if str(value or "").strip()
        ) or (_ACCEPTANCE_CRITERION,)
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": _stable_id("accept", command.command_id),
            "work_order_id": execution_context["work_order_id"],
            "task_graph_id": execution_context["graph_id"],
            "task_node_id": execution_context["node_id"],
            "attempt_id": execution_context["attempt_id"],
            "decision": "ACCEPT",
            "reason_code": None,
            "evidence_refs": refs,
            "criteria": [
                {
                    "criterion_id": f"criterion-governed-durable-projection-{index}",
                    "criterion_text": criterion_text,
                    "verdict": "PASS",
                    "evidence_refs": refs,
                    "deterministic": True,
                    "reason_code": None,
                    "critic_verdict": None,
                }
                for index, criterion_text in enumerate(criteria_texts, start=1)
            ],
            "critic_candidate": None,
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


def _load_governor_grant(
    *,
    ledger: Any,
    request_id: str,
    expected_ref: str | None,
) -> dict[str, Any]:
    if not expected_ref:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
            "HostCommand has no durable Governor grant reference",
        )
    try:
        return DurableGovernorGrantResolver(ledger).require(
            request_id=request_id,
            expected_ref=expected_ref,
        )
    except GovernorGrantError as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_GOVERNOR_GRANT_MISSING,
            exc.detail,
        ) from exc


__all__ = [
    "GOVERNED_HOST_GOVERNOR_GRANT_MISSING",
    "GOVERNED_HOST_ROUTE_UNSUPPORTED",
    "GOVERNED_HOST_SKILL_DEPENDENCY_UNAVAILABLE",
]
