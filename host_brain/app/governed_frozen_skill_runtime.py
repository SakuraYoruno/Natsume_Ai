"""Frozen Skill worker and durable projection policy for governed Host tasks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
import hashlib
import time
from typing import Any

from host_brain.agent_runtime.governed_production_payload import (
    FROZEN_NODE_EXECUTOR_PAYLOADS_KEY as _FROZEN_NODE_EXECUTOR_PAYLOADS_KEY,
    GOVERNED_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA as _FROZEN_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA,
    sha256_json as _sha256_json,
)
from host_brain.app.governed_skill_execution_ports import GovernedSkillExecutionPorts
from host_brain.app.governed_skill_execution import (
    execute_governed_skill,
    record_governed_skill_execution,
)
from host_brain.skills.governed_execution_support import (
    GovernedSkillExecutionOutcome,
)
from host_brain.digital_life.governed_companion_presentation import (
    GovernedCompanionPresentationError,
    build_governed_companion_expression_intents,
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
from host_brain.contracts.agent_runtime.review_policy import (
    pre_execution_review_required,
)
from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS
from host_brain.agent_runtime.governed_skill_work_order import (
    WRITE_AGENT_RUNTIME_SKILL_IDS,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
    canonical_workspace_artifact_digest,
)
from host_brain.core.errors import MCP_TASK_AUTHORITY_REQUIRED
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    GovernorGrantError,
)
from host_brain.orchestration.node_grants import DurableNodeGrantVerifier
from host_brain.persistence.review_continuation_binding import (
    parse_task_review_subject,
)
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


def _skill_companion_presentation_allowed(command: HostCommand) -> bool:
    """Return whether this Skill result is itself visible to the user.

    Every Skill node inside an atomic Plan is an intermediate dependency.  The
    Plan's unique Chat result node owns conversation/Life/delivery effects, so
    presenting an intermediate Skill would both leak internal output and
    create duplicate ``foreground_completed`` effects for one request.
    """

    return str(command.route_type or "").strip().lower() != "plan"


def _frozen_node_executor_payload(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    node_id: str,
    target_id: str,
) -> dict[str, Any] | None:
    """Return one admission-frozen production payload or reject its drift."""

    all_payloads = _mapping(
        dict(work_order.input_pack or {}).get(_FROZEN_NODE_EXECUTOR_PAYLOADS_KEY)
    )
    payload = _mapping(all_payloads.get(node_id))
    if target_id not in AGENT_PRODUCTION_SKILL_IDS:
        if payload:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "non-production Skill has an unexpected frozen executor payload",
            )
        return None
    if not payload:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "production Skill has no admission-frozen executor payload",
        )
    supplied_digest = str(payload.get("payload_sha256") or "").strip()
    digest_subject = {
        key: value for key, value in payload.items() if key != "payload_sha256"
    }
    adapter_order = _mapping(payload.get("work_order"))
    metadata = _mapping(adapter_order.get("metadata"))
    expected_lineage = {
        "root_work_order_id": work_order.root_work_order_id,
        "task_graph_id": work_order.task_graph_id,
        "task_node_id": node_id,
        "graph_revision": work_order.graph_revision,
        "business_scope": work_order.business_idempotency_scope,
        "operation_fingerprint": work_order.operation_fingerprint,
        "approval_ref": work_order.approval_ref,
        "review_ref": work_order.review_ref,
    }
    if (
        payload.get("schema_version")
        != _FROZEN_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA
        or payload.get("target_id") != target_id
        or payload.get("tool") != "codex_production"
        or payload.get("action") != "enqueue"
        or payload.get("start_background") is not False
        or supplied_digest != _sha256_json(digest_subject)
        or str(adapter_order.get("work_order_id") or "").strip()
        != work_order.work_order_id
        or str(metadata.get("skill_id") or "").strip() != target_id
        or str(metadata.get("request_id") or "").strip() != command.request_id
        or _mapping(metadata.get("governed_lineage")) != expected_lineage
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "frozen production executor payload differs from the admitted TaskGraph",
        )
    return payload


def _prepare_frozen_skill_input(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    node_id: str,
) -> tuple[str, dict[str, Any]]:
    """Resolve and validate immutable Skill input without executing effects."""

    if str(command.route_type or "").strip().lower() == "skill":
        _require_fail_closed_direct_skill_work_order(work_order)
        _assert_work_order_command_binding(work_order, command)
    else:
        _require_fail_closed_governed_work_order(work_order, command=command)
    pack = dict(work_order.input_pack or {})
    binding = _planning_node_binding(work_order, node_id=node_id)
    target = str(binding.get("target_id") or "").strip()
    route = str(binding.get("worker_route") or "").strip().lower()
    direct_intent = _mapping(pack.get("intent_draft"))
    if (
        route == "skill"
        and str(direct_intent.get("route") or "").strip().lower() == "skill"
    ):
        intent_draft = direct_intent
    else:
        approved_inputs = _mapping(pack.get("approved_candidate_inputs"))
        candidate_id = str(binding.get("candidate_id") or "").strip()
        candidate_input = _mapping(approved_inputs.get(candidate_id))
        intent_draft = {
            "route": route,
            "target_id": target,
            "task": str(
                candidate_input.get("objective")
                or binding.get("objective")
                or ""
            ).strip(),
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
    return target, _mapping(intent_draft.get("args"))


def _memory_store_proposal_outcome(
    *,
    request_id: str,
    execution_args: Mapping[str, Any],
) -> GovernedSkillExecutionOutcome:
    """Return an effect-free proposal; the accepted handler owns the write."""

    content = str(execution_args.get("content") or "").strip()
    digest = hashlib.sha256(
        f"{str(request_id or '').strip()}\nmemory_store".encode("utf-8")
    ).hexdigest()
    receipt_ref = f"skillreceipt://sha256/{digest}"
    if not content:
        return GovernedSkillExecutionOutcome(
            route_type="skill",
            target_id="memory_store",
            skill_family="memory",
            success=False,
            result_text="",
            error_text="MEMORY_CONTENT_REQUIRED",
            latency_ms=0,
            payload={
                "result": {
                    "status": "REJECTED",
                    "reason_code": "MEMORY_CONTENT_REQUIRED",
                },
                "receipt_ref": receipt_ref,
            },
        )
    return GovernedSkillExecutionOutcome(
        route_type="skill",
        target_id="memory_store",
        skill_family="memory",
        success=True,
        result_text="好，我记住了。",
        error_text="",
        latency_ms=0,
        payload={
            "result": {
                "status": "OK",
                "action": "store",
                "detail": {
                    "content_length": len(content),
                    "accepted_effect_pending": True,
                },
            },
            "receipt_ref": receipt_ref,
        },
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
        review_store: Any | None = None,
    ) -> None:
        self._ledger = ledger
        self._execution_ports = execution_ports
        self._node_grant_verifier = node_grant_verifier
        self._projections = projections
        self._action_preview_service = action_preview_service
        self._review_store = review_store

    def prepare_execution(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        node_id: str,
    ) -> None:
        """Fail deterministic Skill preparation before Operation reservation.

        Dependency availability and frozen production payload validation are
        side-effect-free.  Running them after reserving the external Operation
        incorrectly turns a known local configuration error into an unknown
        effect that requires reconciliation.
        """

        target, execution_args = _prepare_frozen_skill_input(
            command=command,
            work_order=work_order,
            node_id=node_id,
        )
        _require_frozen_skill_dependencies(
            ports=self._execution_ports,
            target=target,
            args=execution_args,
        )
        _frozen_node_executor_payload(
            command=command,
            work_order=work_order,
            node_id=node_id,
            target_id=target,
        )

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        node_id = str(execution_context.get("node_id") or "").strip()
        target, execution_args = _prepare_frozen_skill_input(
            command=command,
            work_order=work_order,
            node_id=node_id,
        )
        pack = dict(work_order.input_pack or {})
        binding = _planning_node_binding(work_order, node_id=node_id)
        execution_context = dict(execution_context)
        direct_intent = _mapping(pack.get("intent_draft"))
        if str(direct_intent.get("route") or "").strip().lower() == "skill":
            intent_draft = direct_intent
        else:
            approved_inputs = _mapping(pack.get("approved_candidate_inputs"))
            candidate_id = str(binding.get("candidate_id") or "").strip()
            candidate_input = _mapping(approved_inputs.get(candidate_id))
            intent_draft = {
                "route": "skill",
                "target_id": target,
                "task": str(candidate_input.get("objective") or binding.get("objective") or "").strip(),
                "args": _mapping(candidate_input.get("args")),
            }
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
        admitted_risk_level = int(
            execution_context.get("node_risk_level") or 0
        )
        review_required = pre_execution_review_required(
            work_order,
            node_id=node_id,
        )
        if (
            admitted_risk_level >= 4
            or target in WRITE_AGENT_RUNTIME_SKILL_IDS
        ) and not review_required:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "high-risk Skill admission lacks durable Review policy",
            )
        if review_required:
            execution_context["pre_execution_review_ref"] = (
                self._consumed_pre_execution_review_ref(
                    command=command,
                    work_order=work_order,
                    node_id=node_id,
                    risk_level=admitted_risk_level,
                )
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
        preview = (
            None
            if target == "memory_store"
            else self._preview_effectful_skill(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                target=target,
                execution_args=execution_args,
                verified_grant=verified_grant.to_dict(),
            )
        )
        _require_frozen_skill_dependencies(
            ports=self._execution_ports,
            target=target,
            args=execution_args,
        )
        frozen_executor_payload = _frozen_node_executor_payload(
            command=command,
            work_order=work_order,
            node_id=node_id,
            target_id=target,
        )
        if target == "memory_store":
            outcome = _memory_store_proposal_outcome(
                request_id=command.request_id,
                execution_args=execution_args,
            )
        else:
            outcome = await asyncio.to_thread(
                execute_governed_skill,
                ports=self._execution_ports,
                durable_authority_ledger=self._ledger,
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
                execution_args=execution_args,
                verified_node_grant=verified_grant.to_dict(),
                frozen_executor_payload=frozen_executor_payload,
            )
        payload = _mapping(getattr(outcome, "payload", None))
        receipt_ref = str(payload.get("receipt_ref") or "").strip()
        skill_version_binding = _mapping(
            payload.get("skill_version_binding")
        )
        skill_version_bundle_id = str(
            skill_version_binding.get("bundle_id") or ""
        ).strip()
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
            manifest_ref=skill_version_bundle_id or None,
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
                **(
                    {"skill_version_binding": skill_version_binding}
                    if skill_version_binding
                    else {}
                ),
                "node_grant_ref": node_grant_ref,
                "node_grant_digest": _sha256_json(verified_grant.to_dict()),
                **(
                    {
                        "action_preview_receipt_ref": preview["receipt_ref"],
                        "action_preview_event_id": preview["event_id"],
                    }
                    if preview is not None
                    else {
                        "action_preview_status": (
                            "LOCAL_ACCEPTANCE_PROPOSAL"
                            if target == "memory_store"
                            else "LEGACY_CONTEXT_NOT_APPLICABLE"
                        )
                    }
                ),
            },
        }
        if success and _skill_companion_presentation_allowed(command):
            request_body = _mapping(pack.get("request_body"))
            try:
                expression_intents = build_governed_companion_expression_intents(
                    request_id=command.request_id,
                    session_id=command.session_id,
                    workspace_id=str(pack.get("workspace_id") or "").strip(),
                    relationship_subject_id=str(pack.get("subject_id") or "").strip(),
                    runtime_context_schema=str(
                        pack.get("runtime_context_schema") or ""
                    ).strip(),
                    input_source=str(request_body.get("input_source") or ""),
                    suppress_edge_tts=bool(request_body.get("suppress_edge_tts")),
                    suppress_edge_vts=bool(request_body.get("suppress_edge_vts")),
                    emotion_context=_mapping(
                        pack.get("prepared_emotion_context")
                        or pack.get("emotion_context")
                    ),
                    narrative=narrative,
                    decision="APPROVE",
                    task_mode=str(
                        _mapping(pack.get("surface_task_control")).get("task_mode")
                        or ""
                    ).strip(),
                    created_at_ms=command.created_at_ms,
                    metadata_source="governed_skill_companion_presentation",
                )
            except GovernedCompanionPresentationError as exc:
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    str(exc),
                ) from exc
            if expression_intents:
                commit["acceptance_effect_intents"] = list(expression_intents)
        raw_agent_result = payload.get("result")
        protected_stage = (
            raw_agent_result.get("_protected_stage")
            if isinstance(raw_agent_result, Mapping)
            else None
        )
        if target in WRITE_AGENT_RUNTIME_SKILL_IDS:
            if not isinstance(protected_stage, Mapping):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "governed Agent write has no sealed protected artifact",
                )
            protected = dict(protected_stage)
            if (
                set(protected) != {"schema_version", "ref", "digest"}
                or protected.get("schema_version")
                != "Natsume.agent.governed_artifact_stage.v1"
            ):
                raise GovernedHostAdapterError(
                    GOVERNED_HOST_AUTHORITY_INVALID,
                    "governed Agent protected artifact descriptor drifted",
                )
            public_agent_result = dict(raw_agent_result)
            public_agent_result.pop("_protected_stage", None)
            if success:
                commit.setdefault("acceptance_effect_intents", []).append(
                    {
                        "effect_type": AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
                        "commit_scope": "NODE",
                        "payload": {
                            "schema_version": (
                                AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA
                            ),
                            "work_order_id": work_order.work_order_id,
                            "target_id": target,
                            "operation_fingerprint": (
                                str(
                                    execution_context.get(
                                        "operation_fingerprint"
                                    )
                                    or ""
                                ).strip()
                            ),
                            "protected_artifact": protected,
                            "result_digest": (
                                canonical_workspace_artifact_digest(
                                    public_agent_result
                                )
                            ),
                        },
                    }
                )
            else:
                # A failed write-capable Agent still owns a real, sealed
                # Workspace stage.  Persist only its bounded descriptor in
                # the immutable worker projection so terminal reconciliation
                # can abandon the exact artifact after the canonical Task
                # failure.  Raw files never cross this boundary and no
                # Acceptance effect is proposed.
                commit["audit_refs"] = {
                    **dict(commit["audit_refs"]),
                    "terminal_workspace_artifact": protected,
                    "terminal_workspace_result_digest": (
                        canonical_workspace_artifact_digest(
                            public_agent_result
                        )
                    ),
                }
        elif protected_stage is not None:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "non-Agent execution attempted to transfer a protected artifact",
            )
        worker_budget = build_worker_budget_usage(
            usage_id=stable_worker_usage_id(
                graph_id=str(execution_context.get("graph_id") or ""),
                graph_revision=int(execution_context.get("graph_revision") or 0),
                node_id=node_id,
                attempt_id=str(execution_context.get("attempt_id") or ""),
                route="skill",
            ),
            observations=_trusted_skill_budget_observations(
                payload,
                execution_context=execution_context,
            ),
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
            # The public COMMIT mirrors the exact canonical projection body.
            # The coordinator must never receive an identity-weaker Skill
            # event and infer graph/node/attempt/grant fields on its behalf.
            commit = {
                **projection_body,
                "receipt_ref": projection_id,
            }
        yield commit


    def _consumed_pre_execution_review_ref(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        node_id: str,
        risk_level: int,
    ) -> str:
        list_reviews = getattr(self._review_store, "list_reviews", None)
        if not callable(list_reviews):
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "high-risk Skill has no app-local durable Review authority",
            )
        rows = list_reviews(
            task_graph_id=command.graph_id,
            graph_revision=command.graph_revision,
            work_order_id=work_order.work_order_id,
            limit=100,
        )
        matches: list[dict[str, Any]] = []
        for raw in tuple(rows or ()):
            record = dict(raw) if isinstance(raw, Mapping) else {}
            review_raw = record.get("review")
            review = dict(review_raw) if isinstance(review_raw, Mapping) else {}
            subject = parse_task_review_subject(review.get("subject_ref"))
            if (
                record.get("task_graph_id") == command.graph_id
                and record.get("work_order_id") == work_order.work_order_id
                and record.get("task_node_id") == node_id
                and subject is not None
                and subject.graph_id == command.graph_id
                and subject.graph_revision == command.graph_revision
                and subject.node_id == node_id
                and subject.attempt_id is None
                and review.get("review_type") == "PRE_EXECUTION_APPROVAL"
                and review.get("resume_phase") == "PRE_EXECUTION"
                and review.get("status") == "CONSUMED"
                and review.get("requester") == command.principal_id
                and review.get("risk_level") == int(risk_level)
                and review.get("side_effects")
                == list(work_order.allowed_actions)
                and int(review.get("resume_count") or 0) == 1
                and str(review.get("decision_actor") or "").strip()
                and str(review.get("token_ref") or "").startswith(
                    "review-token://"
                )
                and str(review.get("resume_result_ref") or "").strip()
            ):
                matches.append(review)
        if len(matches) != 1:
            raise GovernedHostAdapterError(
                GOVERNED_HOST_AUTHORITY_INVALID,
                "high-risk Skill requires one exact consumed pre-execution Review",
            )
        return f"review://{str(matches[0]['review_id']).strip()}"

    def _preview_effectful_skill(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        target: str,
        execution_args: Mapping[str, Any],
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


def _trusted_skill_budget_observations(
    value: Any,
    *,
    execution_context: Mapping[str, Any],
) -> Mapping[str, Any]:
    payload = value if isinstance(value, Mapping) else {}
    result = payload.get("result") if isinstance(payload, Mapping) else {}
    metadata = result.get("metadata") if isinstance(result, Mapping) else None
    observations: dict[str, Any] = {}
    if (
        isinstance(metadata, Mapping)
        and str(metadata.get("execution_authority") or "").strip()
        == "canonical_agent_executor"
    ):
        # Capability Broker output is sanitized for presentation and may replace
        # token-shaped values with ``[REDACTED]``.  Its trace is still useful for
        # counting tool calls, but it is not a budget authority.  Exact model
        # usage comes only from the task-bound attestation below.
        observations = {
            "trace": dict(metadata.get("trace") or {})
            if isinstance(metadata.get("trace"), Mapping)
            else {},
        }

    attestation = payload.get(
        "governed_skill_model_budget_attestation"
    )
    if attestation is None:
        return observations
    from host_brain.model_runtime.governed_skill_budget import (  # noqa: PLC0415
        GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION,
    )

    if not isinstance(attestation, Mapping):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "governed Skill model budget attestation is not an object",
        )
    proof = dict(attestation)
    expected_keys = {
        "schema_version",
        "task_request_id",
        "budget_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "model_attempts",
    }
    budget = execution_context.get("budget")
    budget_id = (
        str(budget.get("budget_id") or "").strip()
        if isinstance(budget, Mapping)
        else ""
    )
    model_attempts = proof.get("model_attempts")
    try:
        proof_revision = int(proof.get("graph_revision") or 0)
        context_revision = int(
            execution_context.get("graph_revision") or 0
        )
    except (TypeError, ValueError) as exc:
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "governed Skill model budget attestation revision is invalid",
        ) from exc
    if (
        set(proof) != expected_keys
        or proof.get("schema_version")
        != GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION
        or str(proof.get("task_request_id") or "").strip()
        != str(execution_context.get("request_id") or "").strip()
        or str(proof.get("budget_id") or "").strip() != budget_id
        or str(proof.get("graph_id") or "").strip()
        != str(execution_context.get("graph_id") or "").strip()
        or proof_revision != context_revision
        or str(proof.get("node_id") or "").strip()
        != str(execution_context.get("node_id") or "").strip()
        or str(proof.get("attempt_id") or "").strip()
        != str(execution_context.get("attempt_id") or "").strip()
        or not isinstance(model_attempts, list)
        or not model_attempts
        or any(not isinstance(item, Mapping) for item in model_attempts)
    ):
        raise GovernedHostAdapterError(
            GOVERNED_HOST_AUTHORITY_INVALID,
            "governed Skill model budget attestation binding drifted",
        )
    observations["model_attempts"] = [
        *(dict(item) for item in model_attempts),
    ]
    return observations


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
