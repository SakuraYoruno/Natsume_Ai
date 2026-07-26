"""Promote one governed Agent Workspace artifact after Task Acceptance."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import inspect
import re
import secrets
from typing import Any

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.governed_skill_work_order import (
    WRITE_AGENT_RUNTIME_SKILL_IDS,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ACTION_PREVIEW_CONTEXT_SCHEMA,
    AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA,
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
    canonical_workspace_artifact_digest,
)
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import (
    WorkOrderV3,
    canonicalize_work_order,
)
from host_brain.contracts.agent_runtime.workspace_lifecycle import (
    WorkspaceLifecycleState,
)
from host_brain.persistence.task_record_codec import (
    build_acceptance_effect_payload_digest,
)
from host_brain.persistence.worker_projection_repository import (
    validate_worker_projection_payload,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from natsume_contracts.task import HostCommand, HostCommandSurface


ACCEPTED_AGENT_WORKSPACE_ARTIFACT_INVALID = (
    "ACCEPTED_AGENT_WORKSPACE_ARTIFACT_INVALID"
)

_PRIVATE_PREVIEW_FIELD = "_action_preview_context"
_PRIVATE_TERMINAL_PROOF_FIELD = "_action_preview_terminal_proof"
_CANONICAL_FIELDS = frozenset(
    {
        "schema_version",
        "command_id",
        "request_id",
        "work_order_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "target_id",
        "operation_fingerprint",
        "worker_projection_ref",
        "protected_artifact",
        "result_digest",
    }
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DURABLE_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")


class AcceptedAgentWorkspaceArtifactError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = ACCEPTED_AGENT_WORKSPACE_ARTIFACT_INVALID
        self.detail = str(detail or "accepted Agent Workspace artifact is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class AcceptedAgentWorkspaceArtifactHandler:
    """Validate and promote one exact sealed Skill/Codex artifact."""

    def __init__(
        self,
        *,
        command_repository: Any,
        projection_store: Any,
        artifact_store: AgentArtifactStore,
        workspace_runtime: DurableWorkspaceRuntime,
        task_store: Any,
    ) -> None:
        for owner, method in (
            (command_repository, "get"),
            (projection_store, "load"),
            (task_store, "get_node"),
            (task_store, "get_attempt"),
            (task_store, "get_lease"),
            (task_store, "get_node_artifact"),
            (task_store, "get_acceptance_effect_intent"),
            (task_store, "resolve_agent_completion_lineage"),
        ):
            if not callable(getattr(owner, method, None)):
                raise TypeError(f"{method} is required")
        self._commands = command_repository
        self._projections = projection_store
        self._artifacts = artifact_store
        self._workspace = workspace_runtime
        self._tasks = task_store

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return await self._promote(payload, allow_start=True)

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Continue only a durably started exact promotion."""

        return await self._promote(payload, allow_start=False)

    async def _promote(
        self,
        payload: Mapping[str, Any],
        *,
        allow_start: bool,
    ) -> Mapping[str, Any]:
        canonical, preview = _accepted_payload(payload)
        command, work_order = await self._reopen_admission(canonical)
        await self._validate_worker_projection(canonical)
        descriptor = dict(canonical["protected_artifact"])
        seal = self._artifacts.inspect_governed_stage(
            stage_ref=str(descriptor["ref"]),
            seal_digest=str(descriptor["digest"]),
        )
        authority = dict(seal.execution_authority)
        _validate_stage_authority(
            authority,
            canonical=canonical,
            command=command,
        )
        sealed_order = canonicalize_work_order(
            self._artifacts.read_sealed_json(seal, "work_order.json")
        )
        sealed_result = self._artifacts.read_sealed_json(seal, "result.json")
        if (
            sealed_order.work_order_id != canonical["work_order_id"]
            or sealed_order.root_work_order_id != canonical["work_order_id"]
            or sealed_order.task_graph_id != canonical["graph_id"]
            or sealed_order.graph_revision != canonical["graph_revision"]
            or sealed_order.task_node_id != canonical["node_id"]
            or sealed_order.operation_fingerprint
            != canonical["operation_fingerprint"]
            or sealed_order.review_ref
            != str(
                sealed_order.review_policy.get("review_ref") or ""
            ).strip()
            or not str(sealed_order.review_ref or "").startswith("review://")
            or canonical_workspace_artifact_digest(sealed_result)
            != canonical["result_digest"]
        ):
            _invalid("sealed Agent WorkOrder/result differs from accepted lineage")

        effect_ref, verification_ref, acceptance_ref = (
            await self._validate_durable_acceptance(
                canonical=canonical,
                preview=preview,
                authority=authority,
                command=command,
                work_order=work_order,
            )
        )
        try:
            write_candidate = self._artifacts.read_workspace_write_candidate(seal)
            promotion_args = {
                "workspace_id": seal.workspace_id,
                "execution_authority": authority,
                "verification_ref": verification_ref,
                "acceptance_ref": acceptance_ref,
                "effect_ref": effect_ref,
                "allow_start": allow_start,
                "artifact_promoter": (
                    lambda: self._artifacts.promote_governed_stage(
                        stage_ref=seal.stage_ref,
                        seal_digest=seal.seal_digest,
                        acceptance_ref=acceptance_ref,
                    )
                ),
            }
            if write_candidate is None:
                receipt = self._workspace.promote_no_change_artifact(
                    **promotion_args
                )
            else:
                manifest, patch = write_candidate
                receipt = self._workspace.promote_write_artifact(
                    **promotion_args,
                    write_manifest=manifest,
                    write_patch=patch,
                )
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            if await self._promotion_completely_unstarted(
                workspace_id=seal.workspace_id,
                stage_ref=seal.stage_ref,
                seal_digest=seal.seal_digest,
            ):
                raise
            raise AcceptanceEffectOutcomeUnknown(
                "Agent Workspace promotion crossed or may have crossed a "
                f"durable boundary: {_opaque_exception(exc)}"
            ) from exc
        return {
            "receipt_ref": (
                f"workspace-promotion://{receipt.promotion_event_id}"
            ),
            "artifact_ref": receipt.artifact_ref,
            "workspace_id": receipt.workspace_id,
            "replayed": bool(receipt.replayed),
        }

    async def _reopen_admission(
        self,
        canonical: Mapping[str, Any],
    ) -> tuple[HostCommand, WorkOrderV3]:
        raw = await _resolve(self._commands.get(canonical["command_id"]))
        if not isinstance(raw, Mapping):
            _invalid("accepted Agent command is missing")
        command = raw.get("command")
        work_order = raw.get("work_order")
        graph = raw.get("graph")
        assignment = raw.get("assignment")
        if (
            not isinstance(command, HostCommand)
            or not isinstance(work_order, WorkOrderV3)
            or not isinstance(graph, TaskGraph)
            or assignment is None
        ):
            _invalid("accepted Agent admission is incomplete or untyped")
        node = next(
            (
                candidate
                for candidate in graph.nodes
                if candidate.node_id == canonical["node_id"]
            ),
            None,
        )
        expected = (
            (command.command_id, canonical["command_id"]),
            (command.request_id, canonical["request_id"]),
            (command.root_work_order_id, canonical["work_order_id"]),
            (command.graph_id, canonical["graph_id"]),
            (command.graph_revision, canonical["graph_revision"]),
            (command.route_type, "skill"),
            (command.target_id, canonical["target_id"]),
            (
                command.operation_fingerprint,
                canonical["operation_fingerprint"],
            ),
            (work_order.work_order_id, canonical["work_order_id"]),
            (work_order.task_graph_id, canonical["graph_id"]),
            (work_order.graph_revision, canonical["graph_revision"]),
            (work_order.task_node_id, canonical["node_id"]),
            (
                work_order.operation_fingerprint,
                canonical["operation_fingerprint"],
            ),
            (graph.graph_id, canonical["graph_id"]),
            (graph.revision, canonical["graph_revision"]),
            (graph.result_node_id, canonical["node_id"]),
            (getattr(node, "node_id", None), canonical["node_id"]),
        )
        review_policy = dict(work_order.review_policy or {})
        if (
            any(actual != wanted for actual, wanted in expected)
            or command.surface is HostCommandSurface.INTERNAL
            or canonical["target_id"] not in WRITE_AGENT_RUNTIME_SKILL_IDS
            or int(work_order.risk_level) < 4
            or review_policy.get("review_required") is not True
            or review_policy.get("durable_review_required") is not True
        ):
            _invalid("accepted Agent admission identity or Review policy drifted")
        return command, work_order

    async def _validate_worker_projection(
        self,
        canonical: Mapping[str, Any],
    ) -> None:
        raw = await _resolve(
            self._projections.load(canonical["worker_projection_ref"])
        )
        if not isinstance(raw, Mapping):
            _invalid("accepted Agent worker projection is missing")
        projection = validate_worker_projection_payload(
            raw,
            expected_projection_id=canonical["worker_projection_ref"],
        )
        descriptors = projection.get("acceptance_effect_intents")
        candidates = [
            dict(item)
            for item in (descriptors if isinstance(descriptors, list) else [])
            if isinstance(item, Mapping)
            and item.get("effect_type")
            == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
        ]
        if len(candidates) != 1:
            _invalid("Agent worker projection has no unique artifact proposal")
        descriptor = candidates[0]
        proposed = descriptor.get("payload")
        proposed = dict(proposed) if isinstance(proposed, Mapping) else {}
        if (
            descriptor.get("commit_scope") != "NODE"
            or proposed.get("schema_version")
            != AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA
            or proposed.get("work_order_id") != canonical["work_order_id"]
            or proposed.get("target_id") != canonical["target_id"]
            or proposed.get("operation_fingerprint")
            != canonical["operation_fingerprint"]
            or proposed.get("protected_artifact")
            != canonical["protected_artifact"]
            or proposed.get("result_digest") != canonical["result_digest"]
            or projection.get("command_id") != canonical["command_id"]
            or projection.get("graph_id") != canonical["graph_id"]
            or projection.get("graph_revision")
            != canonical["graph_revision"]
            or projection.get("node_id") != canonical["node_id"]
            or projection.get("attempt_id") != canonical["attempt_id"]
            or projection.get("target_id") != canonical["target_id"]
            or projection.get("decision") != "APPROVE"
        ):
            _invalid("Agent worker projection differs from accepted proposal")

    async def _validate_durable_acceptance(
        self,
        *,
        canonical: Mapping[str, Any],
        preview: Mapping[str, Any],
        authority: Mapping[str, Any],
        command: HostCommand,
        work_order: WorkOrderV3,
    ) -> tuple[str, str, str]:
        boundary = preview["origin_boundary"]
        authority_context = boundary["authority_context"]
        effect_ref = str(authority_context["effect_id"])
        node = await _resolve(
            self._tasks.get_node(
                canonical["graph_id"],
                canonical["graph_revision"],
                canonical["node_id"],
            )
        )
        attempt = await _resolve(
            self._tasks.get_attempt(canonical["attempt_id"])
        )
        lease = await _resolve(
            self._tasks.get_lease(str(authority.get("lease_id") or ""))
        )
        completion_lease = lease
        if isinstance(lease, Mapping) and lease.get("status") == "EXPIRED":
            lineage = await _resolve(
                self._tasks.resolve_agent_completion_lineage(
                    graph_id=canonical["graph_id"],
                    graph_revision=canonical["graph_revision"],
                    node_id=canonical["node_id"],
                    attempt_id=canonical["attempt_id"],
                    historical_lease_id=str(authority.get("lease_id") or ""),
                    historical_lease_owner=str(
                        authority.get("lease_owner_id") or ""
                    ),
                    historical_fencing_token=int(
                        authority.get("fencing_token") or 0
                    ),
                )
            )
            completion_lease = (
                lineage.get("completion_lease")
                if isinstance(lineage, Mapping)
                else None
            )
        node_artifact = await _resolve(
            self._tasks.get_node_artifact(
                canonical["graph_id"],
                canonical["graph_revision"],
                canonical["node_id"],
            )
        )
        effect = await _resolve(
            self._tasks.get_acceptance_effect_intent(effect_ref)
        )
        if not all(
            isinstance(value, Mapping)
            for value in (
                node,
                attempt,
                lease,
                completion_lease,
                node_artifact,
                effect,
            )
        ):
            _invalid("Agent promotion lacks durable Task acceptance records")
        full_effect_payload = effect.get("payload")
        if not isinstance(full_effect_payload, Mapping):
            _invalid("Agent Acceptance Effect payload is invalid")
        canonical_effect = deepcopy(dict(full_effect_payload))
        durable_preview = canonical_effect.pop(_PRIVATE_PREVIEW_FIELD, None)
        _validate_durable_preview(
            durable_preview,
            canonical=canonical,
            preview=preview,
            command=command,
            work_order=work_order,
        )
        payload_digest = build_acceptance_effect_payload_digest(
            full_effect_payload
        )
        if (
            node.get("status") != "ACCEPTED"
            or attempt.get("status") != "SUCCEEDED"
            or attempt.get("attempt_id") != canonical["attempt_id"]
            or attempt.get("graph_id") != canonical["graph_id"]
            or attempt.get("graph_revision") != canonical["graph_revision"]
            or attempt.get("node_id") != canonical["node_id"]
            or lease.get("lease_id") != authority.get("lease_id")
            or lease.get("attempt_id") != canonical["attempt_id"]
            or lease.get("owner_id") != authority.get("lease_owner_id")
            or lease.get("fencing_token") != authority.get("fencing_token")
            or lease.get("status") not in {"RELEASED", "EXPIRED"}
            or completion_lease.get("attempt_id") != canonical["attempt_id"]
            or completion_lease.get("owner_id") != attempt.get("owner_id")
            or completion_lease.get("status") != "RELEASED"
            or type(completion_lease.get("fencing_token")) is not int
            or int(completion_lease["fencing_token"])
            < int(authority.get("fencing_token") or 0)
            or (
                lease.get("status") == "EXPIRED"
                and int(completion_lease["fencing_token"])
                <= int(authority.get("fencing_token") or 0)
            )
            or (
                lease.get("status") == "RELEASED"
                and completion_lease.get("lease_id")
                != authority.get("lease_id")
            )
            or node_artifact.get("graph_id") != canonical["graph_id"]
            or node_artifact.get("graph_revision")
            != canonical["graph_revision"]
            or node_artifact.get("node_id") != canonical["node_id"]
            or node_artifact.get("attempt_id") != canonical["attempt_id"]
            or not _DURABLE_REF.fullmatch(
                str(node_artifact.get("artifact_ref") or "")
            )
            or not _DURABLE_REF.fullmatch(
                str(node_artifact.get("receipt_ref") or "")
            )
            or effect.get("graph_id") != canonical["graph_id"]
            or effect.get("graph_revision") != canonical["graph_revision"]
            or effect.get("node_id") != canonical["node_id"]
            or effect.get("attempt_id") != canonical["attempt_id"]
            or effect.get("effect_type")
            != AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
            or effect.get("commit_scope") != "GRAPH"
            or canonical_effect != dict(canonical)
            or effect.get("payload_sha256") != payload_digest
            or effect.get("status") not in {"CLAIMED", "COMMITTED"}
            or effect.get("lease_owner")
            != authority_context.get("lease_owner_id")
            or effect.get("fencing_token")
            != authority_context.get("fencing_token")
        ):
            _invalid("Agent promotion lacks exact Node/Attempt/Effect acceptance")
        return (
            effect_ref,
            str(node_artifact["receipt_ref"]),
            str(node_artifact["artifact_ref"]),
        )

    async def _promotion_completely_unstarted(
        self,
        *,
        workspace_id: str,
        stage_ref: str,
        seal_digest: str,
    ) -> bool:
        try:
            lifecycle = await _resolve(
                self._workspace.repository.get_lifecycle(workspace_id)
            )
            if lifecycle.state is not WorkspaceLifecycleState.ACTIVE:
                return False
            stage = self._artifacts.inspect_governed_stage(
                stage_ref=stage_ref,
                seal_digest=seal_digest,
            )
            return (
                stage.accepted_dir is None
                and not (stage.stage_dir / "promotion.json").exists()
            )
        except Exception:
            return False


def _accepted_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, Mapping):
        _invalid("accepted Agent Workspace payload must be a mapping")
    value = dict(payload)
    preview_raw = value.pop(_PRIVATE_PREVIEW_FIELD, None)
    terminal_proof = value.pop(_PRIVATE_TERMINAL_PROOF_FIELD, None)
    if frozenset(value) != _CANONICAL_FIELDS or value.get(
        "schema_version"
    ) != AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA:
        _invalid("accepted Agent Workspace payload fields drifted")
    canonical = deepcopy(value)
    for field in _CANONICAL_FIELDS - {
        "graph_revision",
        "protected_artifact",
    }:
        if not str(canonical.get(field) or "").strip():
            _invalid(f"accepted Agent Workspace {field} is required")
    if (
        type(canonical.get("graph_revision")) is not int
        or int(canonical["graph_revision"]) < 1
        or canonical["target_id"] not in WRITE_AGENT_RUNTIME_SKILL_IDS
        or not _SHA256.fullmatch(
            str(canonical.get("operation_fingerprint") or "")
        )
        or not _SHA256.fullmatch(str(canonical.get("result_digest") or ""))
        or not str(canonical.get("worker_projection_ref") or "").startswith(
            "worker-projection://"
        )
    ):
        _invalid("accepted Agent Workspace canonical identity is invalid")
    protected = canonical.get("protected_artifact")
    if (
        not isinstance(protected, Mapping)
        or set(protected) != {"schema_version", "ref", "digest"}
        or protected.get("schema_version")
        != "Natsume.agent.governed_artifact_stage.v1"
        or not str(protected.get("ref") or "").startswith("agent-stage://")
        or not _SHA256.fullmatch(str(protected.get("digest") or ""))
    ):
        _invalid("accepted Agent protected artifact descriptor is invalid")
    canonical["protected_artifact"] = dict(protected)
    preview = _validate_live_preview(
        preview_raw,
        terminal_proof=terminal_proof,
        canonical=canonical,
    )
    return canonical, preview


def _validate_live_preview(
    value: Any,
    *,
    terminal_proof: Any,
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("Agent Workspace promotion lacks live Preview context")
    preview = deepcopy(dict(value))
    boundary = preview.get("origin_boundary")
    if (
        set(preview)
        != {
            "schema_version",
            "origin_authority_kind",
            "origin_event_id",
            "origin_receipt_ref",
            "origin_boundary",
        }
        or preview.get("schema_version")
        != AGENT_WORKSPACE_ACTION_PREVIEW_CONTEXT_SCHEMA
        or str(preview.get("origin_authority_kind") or "").upper()
        != "ACCEPTANCE_EFFECT"
        or not _DURABLE_REF.fullmatch(
            str(preview.get("origin_event_id") or "")
        )
        or not _DURABLE_REF.fullmatch(
            str(preview.get("origin_receipt_ref") or "")
        )
        or not isinstance(boundary, Mapping)
    ):
        _invalid("Agent Workspace live Preview context drifted")
    boundary = dict(boundary)
    authority = boundary.get("authority_context")
    node_grant = boundary.get("node_grant")
    if (
        boundary.get("action_kind")
        != f"acceptance-effect.{AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE}"
        or boundary.get("action_args") != dict(canonical)
        or boundary.get("task_graph_id") != canonical["graph_id"]
        or boundary.get("graph_revision") != canonical["graph_revision"]
        or boundary.get("task_node_id") != canonical["node_id"]
        or type(boundary.get("risk_level")) is not int
        or int(boundary["risk_level"]) < 4
        or not _SHA256.fullmatch(
            str(boundary.get("context_pack_digest") or "")
        )
        or not isinstance(boundary.get("scene_identity"), Mapping)
        or not isinstance(authority, Mapping)
        or not isinstance(node_grant, Mapping)
        or authority.get("effect_id") != boundary.get("action_scope")
        or type(authority.get("fencing_token")) is not int
        or int(authority["fencing_token"]) < 1
        or not str(authority.get("lease_owner_id") or "").strip()
        or node_grant.get("grant_ref") != boundary.get("node_grant_ref")
        or not _SHA256.fullmatch(
            str(node_grant.get("binding_digest") or "")
        )
    ):
        _invalid("Agent Workspace live Preview boundary drifted")
    if terminal_proof is not None:
        if (
            not isinstance(terminal_proof, Mapping)
            or terminal_proof.get("event_id")
            != preview["origin_event_id"]
            or terminal_proof.get("boundary") != boundary
        ):
            _invalid("Agent Workspace terminal Preview proof drifted")
    return {**preview, "origin_boundary": boundary}


def _validate_durable_preview(
    value: Any,
    *,
    canonical: Mapping[str, Any],
    preview: Mapping[str, Any],
    command: HostCommand,
    work_order: WorkOrderV3,
) -> None:
    if not isinstance(value, Mapping):
        _invalid("Agent Workspace durable Preview context is missing")
    context = dict(value)
    boundary = preview["origin_boundary"]
    node_grant = boundary["node_grant"]
    if (
        context.get("schema_version")
        != "Natsume.acceptance_effect.action_preview_context.v1"
        or context.get("context_pack_digest")
        != boundary.get("context_pack_digest")
        or context.get("scene_identity") != boundary.get("scene_identity")
        or context.get("task_graph_id") != canonical["graph_id"]
        or context.get("graph_revision") != canonical["graph_revision"]
        or context.get("task_node_id") != canonical["node_id"]
        or context.get("parent_governor_grant_ref")
        != command.governor_grant_ref
        or context.get("parent_governor_grant_ref")
        != boundary.get("parent_governor_grant_ref")
        or context.get("node_grant_ref") != boundary.get("node_grant_ref")
        or context.get("node_grant_digest")
        != node_grant.get("binding_digest")
        or work_order.task_graph_id != canonical["graph_id"]
        or work_order.task_node_id != canonical["node_id"]
    ):
        _invalid("Agent Workspace durable Preview authority drifted")


def _validate_stage_authority(
    authority: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any],
    command: HostCommand,
) -> None:
    expected = (
        (authority.get("root_work_order_id"), canonical["work_order_id"]),
        (authority.get("graph_id"), canonical["graph_id"]),
        (authority.get("graph_revision"), canonical["graph_revision"]),
        (authority.get("node_id"), canonical["node_id"]),
        (authority.get("attempt_id"), canonical["attempt_id"]),
        (authority.get("command_id"), canonical["command_id"]),
        (authority.get("principal_id"), command.principal_id),
    )
    if any(actual != wanted for actual, wanted in expected):
        _invalid("sealed Agent stage authority differs from accepted lineage")


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _opaque_exception(exc: BaseException) -> str:
    return (
        f"error_type={type(exc).__module__}.{type(exc).__qualname__} "
        f"incident=sha256:{secrets.token_hex(32)}"
    )


def _invalid(detail: str) -> None:
    raise AcceptedAgentWorkspaceArtifactError(detail)


__all__ = [
    "ACCEPTED_AGENT_WORKSPACE_ARTIFACT_INVALID",
    "AcceptedAgentWorkspaceArtifactError",
    "AcceptedAgentWorkspaceArtifactHandler",
]
