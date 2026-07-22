"""One-way PlanningSearch -> immutable TaskGraph -> governed execution flow.

The service intentionally has no legacy plan adapter and no execution-time
planner callback.  A failed revision may only be replaced through the explicit
``prepare_replan`` entry, which compiles ``previous.revision + 1`` and admits it
under a distinct HostCommand request identity while preserving graph/root
lineage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import inspect
from typing import Any

from host_brain.agent_runtime.governed_production_payload import (
    FROZEN_NODE_EXECUTOR_PAYLOADS_KEY,
    build_governed_production_executor_payload,
)
from host_brain.agent_runtime.production_studio import AGENT_PRODUCTION_SKILL_IDS
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.orchestration.planning_admission import (
    GovernedPlanningAdmissionService,
    PlanningAdmissionReceipt,
)
from host_brain.orchestration.planning_graph_compiler import (
    CompiledPlanningGraph,
    PlanningCompilationContext,
    PlanningGraphCompiler,
)
from host_brain.planning_search.contracts import SearchRequest, SearchResult, SearchStatus
from host_brain.task_runtime.worker_registry import GovernedWorkerRoute
from host_brain.planning_search.service import PlanningSearchService
from natsume_contracts.task import HostCommandSurface


PLANNING_EXECUTION_INVALID = "PLANNING_EXECUTION_INVALID"
PLANNING_EXECUTION_SEARCH_FAILED = "PLANNING_EXECUTION_SEARCH_FAILED"
PLANNING_EXECUTION_REVISION_CONFLICT = "PLANNING_EXECUTION_REVISION_CONFLICT"
PLANNING_EXECUTION_DELEGATE_TARGET_UNAVAILABLE = (
    "PLANNING_EXECUTION_DELEGATE_TARGET_UNAVAILABLE"
)


class PlanningExecutionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or PLANNING_EXECUTION_INVALID)
        self.detail = str(detail or "governed planning execution failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class PlanningExecutionAuthority:
    """Frozen Host admission material shared by initial and replan revisions."""

    surface: HostCommandSurface
    base_command_request_id: str
    session_id: str
    principal_id: str
    input_ref: str
    input_sha256: str
    governor_grant_ref: str
    target_id: str
    ingress_claim_ref: str | None = None
    ingress_audit_ref: str | None = None

    def validate(self) -> None:
        if not isinstance(self.surface, HostCommandSurface):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "surface must be HostCommandSurface",
            )
        required = {
            "base_command_request_id": self.base_command_request_id,
            "session_id": self.session_id,
            "principal_id": self.principal_id,
            "input_ref": self.input_ref,
            "input_sha256": self.input_sha256,
            "governor_grant_ref": self.governor_grant_ref,
            "target_id": self.target_id,
        }
        missing = sorted(key for key, value in required.items() if not str(value or "").strip())
        if missing:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                f"planning authority is missing required fields: {missing}",
            )
        if bool(str(self.ingress_claim_ref or "").strip()) != bool(
            str(self.ingress_audit_ref or "").strip()
        ):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "ingress claim and audit references must be supplied together",
            )

    def command_request_id(self, revision: int) -> str:
        if int(revision) < 1:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "graph revision must be at least 1",
            )
        base = str(self.base_command_request_id).strip()
        return base if int(revision) == 1 else f"{base}.graph-revision-{int(revision)}"


@dataclass(frozen=True, slots=True)
class PreparedPlanningExecution:
    search_result: SearchResult
    compiled: CompiledPlanningGraph
    admission: PlanningAdmissionReceipt

    def validate(self) -> None:
        if self.search_result.status is not SearchStatus.SELECTED:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_SEARCH_FAILED,
                "prepared planning execution has no selected search path",
            )
        self.compiled.validate()
        self.admission.validate()
        if (
            self.admission.graph.to_dict() != self.compiled.task_graph.to_dict()
            or self.admission.work_order.to_dict() != self.compiled.work_order.to_dict()
        ):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "durable admission differs from the compiled selected path",
            )


class GovernedPlanningExecutionService:
    """Prepare and execute exactly one immutable planning graph revision."""

    def __init__(
        self,
        *,
        search: PlanningSearchService,
        compiler: PlanningGraphCompiler,
        admission: GovernedPlanningAdmissionService,
        command_application: Any,
        delegate_target_validator: Callable[[str], Any] | None = None,
        compilation_context_resolver: (
            Callable[[SearchResult], PlanningCompilationContext] | None
        ) = None,
    ) -> None:
        if not callable(getattr(command_application, "execute_stream", None)):
            raise ValueError("command_application.execute_stream is required")
        if not callable(
            getattr(command_application, "preflight_parent_governor_binding", None)
        ):
            raise ValueError(
                "command_application.preflight_parent_governor_binding is required"
            )
        self._search = search
        self._compiler = compiler
        self._admission = admission
        self._application = command_application
        self._delegate_target_validator = delegate_target_validator
        if (
            compilation_context_resolver is not None
            and not callable(compilation_context_resolver)
        ):
            raise ValueError("compilation_context_resolver must be callable")
        self._compilation_context_resolver = compilation_context_resolver

    async def prepare_initial(
        self,
        request: SearchRequest,
        *,
        authority: PlanningExecutionAuthority,
        created_at: int,
        compilation_context: PlanningCompilationContext | None = None,
    ) -> PreparedPlanningExecution:
        authority.validate()
        result = self._run_search(request)
        resolved_context = self._resolve_compilation_context(
            result,
            compilation_context=compilation_context,
        )
        self._validate_compilation_context_authority(
            resolved_context,
            authority=authority,
        )
        compiled = self._compiler.compile(
            result,
            created_at=int(created_at),
            compilation_context=resolved_context,
        )
        if compiled.task_graph.revision != 1:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "initial planning execution must compile revision 1",
            )
        compiled = self._freeze_selected_production_skill_payloads(
            compiled,
            authority=authority,
        )
        return await self._admit(result, compiled=compiled, authority=authority)

    async def prepare_replan(
        self,
        request: SearchRequest,
        *,
        authority: PlanningExecutionAuthority,
        previous_graph: TaskGraph,
        failure_evidence_refs: Sequence[str],
        created_at: int,
        compilation_context: PlanningCompilationContext | None = None,
    ) -> PreparedPlanningExecution:
        authority.validate()
        if not isinstance(previous_graph, TaskGraph):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "replan requires a canonical previous TaskGraph",
            )
        previous_snapshot = previous_graph.to_dict()
        evidence_refs = tuple(
            dict.fromkeys(str(value or "").strip() for value in failure_evidence_refs if str(value or "").strip())
        )
        if not evidence_refs:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "replan requires durable failure evidence",
            )
        result = self._run_search(request)
        resolved_context = self._resolve_compilation_context(
            result,
            compilation_context=compilation_context,
        )
        self._validate_compilation_context_authority(
            resolved_context,
            authority=authority,
        )
        compiled = self._compiler.replan(
            result,
            previous_graph=previous_graph,
            failure_evidence_refs=evidence_refs,
            created_at=int(created_at),
            compilation_context=resolved_context,
        )
        if previous_graph.to_dict() != previous_snapshot:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "the previous graph revision was mutated during replanning",
            )
        if (
            compiled.task_graph.revision != previous_graph.revision + 1
            or compiled.task_graph.graph_id != previous_graph.graph_id
            or compiled.task_graph.root_work_order_id != previous_graph.root_work_order_id
        ):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "replan did not preserve lineage with exact revision + 1",
            )
        compiled = self._freeze_selected_production_skill_payloads(
            compiled,
            authority=authority,
        )
        return await self._admit(result, compiled=compiled, authority=authority)

    async def execute_stream(
        self,
        prepared: PreparedPlanningExecution,
    ) -> AsyncIterator[dict[str, Any]]:
        if not isinstance(prepared, PreparedPlanningExecution):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "execute_stream requires PreparedPlanningExecution",
            )
        prepared.validate()
        receipt = prepared.admission
        async for raw in self._application.execute_stream(
            command=receipt.command,
            work_order=receipt.work_order,
            assignment=receipt.assignment,
            graph=receipt.graph,
        ):
            if not isinstance(raw, Mapping):
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "governed command application emitted a non-mapping event",
                )
            yield dict(raw)

    def _run_search(self, request: SearchRequest) -> SearchResult:
        if not isinstance(request, SearchRequest):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "planning execution requires SearchRequest",
            )
        result = self._search.search(request)
        if (
            not isinstance(result, SearchResult)
            or result.status is not SearchStatus.SELECTED
            or not result.selected_path
            or result.side_effects_executed
        ):
            status = result.status.value if isinstance(result, SearchResult) else "invalid"
            reason = result.stop_reason if isinstance(result, SearchResult) else "invalid_result"
            raise PlanningExecutionError(
                PLANNING_EXECUTION_SEARCH_FAILED,
                f"planning search did not select a side-effect-free path: {status}/{reason}",
            )
        return result

    def _resolve_compilation_context(
        self,
        result: SearchResult,
        *,
        compilation_context: PlanningCompilationContext | None,
    ) -> PlanningCompilationContext | None:
        """Bind selected candidates only after side-effect-free search.

        Some domains must reconstruct per-node execution input from the exact
        selected path.  The resolver runs after search and before compiler or
        admission, so it cannot observe a live worker or bypass immutable
        TaskGraph construction.
        """

        resolver = self._compilation_context_resolver
        if resolver is None:
            return compilation_context
        if compilation_context is not None:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compilation context cannot be supplied with a resolver",
            )
        try:
            resolved = resolver(result)
        except PlanningExecutionError:
            raise
        except Exception as exc:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "selected planning context could not be reconstructed",
            ) from exc
        if not isinstance(resolved, PlanningCompilationContext):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compilation context resolver returned an invalid context",
            )
        return resolved

    @staticmethod
    def _validate_compilation_context_authority(
        compilation_context: PlanningCompilationContext | None,
        *,
        authority: PlanningExecutionAuthority,
    ) -> None:
        """Keep root compilation authority bound to the admitted parent grant.

        Search candidates remain authority-free, but a caller could otherwise
        compile their selected path with a context approved by a different
        governor request.  Legacy callers may omit the context until their
        domain is migrated; supplied contexts must never cross that boundary.
        """

        if compilation_context is None:
            return
        if not isinstance(compilation_context, PlanningCompilationContext):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compilation_context must be PlanningCompilationContext",
            )
        if compilation_context.approval_ref != authority.governor_grant_ref:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compilation context approval_ref differs from governor grant",
            )

    @staticmethod
    def _freeze_selected_production_skill_payloads(
        compiled: CompiledPlanningGraph,
        *,
        authority: PlanningExecutionAuthority,
    ) -> CompiledPlanningGraph:
        """Bind Plan production Skills to node-specific adapter payloads at admission.

        Search and graph compilation remain domain-neutral.  Once the selected
        path is a canonical WorkOrderV3, this Host-bound step resolves the
        compatibility adapter for each registered production Skill and freezes
        it into the immutable revision.  Workers may only consume this payload;
        they never re-expand a profile or user-supplied plan step at execution
        time.
        """

        compiled.validate()
        graph = compiled.task_graph
        order = compiled.work_order
        input_pack = dict(order.input_pack or {})
        if FROZEN_NODE_EXECUTOR_PAYLOADS_KEY in input_pack:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "planning input may not supply frozen production executor payloads",
            )
        metadata = dict(order.metadata or {})
        planning_lineage = metadata.get("planning_lineage")
        if not isinstance(planning_lineage, Mapping):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compiled planning WorkOrder has no planning lineage",
            )
        raw_bindings = planning_lineage.get("node_bindings")
        if not isinstance(raw_bindings, list):
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compiled planning WorkOrder has no node binding list",
            )
        approved_inputs = input_pack.get("approved_candidate_inputs")

        expected_node_ids = {node.node_id for node in graph.nodes}
        seen_node_ids: set[str] = set()
        frozen_payloads: dict[str, dict[str, Any]] = {}
        request_id = authority.command_request_id(graph.revision)
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, Mapping):
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "compiled planning node binding is not a mapping",
                )
            node_id = str(raw_binding.get("node_id") or "").strip()
            candidate_id = str(raw_binding.get("candidate_id") or "").strip()
            route = str(raw_binding.get("worker_route") or "").strip().lower()
            target_id = str(raw_binding.get("target_id") or "").strip()
            objective = str(raw_binding.get("objective") or "").strip()
            if not node_id or node_id not in expected_node_ids or node_id in seen_node_ids:
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "compiled planning node binding has an invalid or duplicate node_id",
                )
            seen_node_ids.add(node_id)
            if route != "skill" or target_id not in AGENT_PRODUCTION_SKILL_IDS:
                continue
            if not isinstance(approved_inputs, Mapping):
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "compiled planning WorkOrder has no approved candidate inputs",
                )
            if not candidate_id or not objective:
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "production Skill binding requires candidate and objective lineage",
                )
            candidate_input = approved_inputs.get(candidate_id)
            if not isinstance(candidate_input, Mapping):
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "production Skill binding has no approved candidate input",
                )
            candidate_route = str(candidate_input.get("route") or "").strip().lower()
            candidate_target = str(candidate_input.get("target_id") or "").strip()
            candidate_objective = str(candidate_input.get("objective") or "").strip()
            intent_args = candidate_input.get("args")
            if (
                candidate_route != "skill"
                or candidate_target != target_id
                or candidate_objective != objective
                or not isinstance(intent_args, Mapping)
            ):
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "approved candidate input differs from the selected production Skill binding",
                )
            try:
                frozen_payloads[node_id] = build_governed_production_executor_payload(
                    target_id=target_id,
                    intent_args=intent_args,
                    request_id=request_id,
                    session_id=authority.session_id,
                    person_id=authority.principal_id,
                    user_message=objective,
                    work_order_id=order.root_work_order_id,
                    graph_id=graph.graph_id,
                    node_id=node_id,
                    graph_revision=graph.revision,
                    business_scope=order.business_idempotency_scope,
                    operation_fingerprint=order.operation_fingerprint,
                    approval_ref=str(order.approval_ref or ""),
                    review_ref=str(order.review_ref or ""),
                )
            except (TypeError, ValueError) as exc:
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "could not freeze the selected production Skill payload",
                ) from exc
        if seen_node_ids != expected_node_ids:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_INVALID,
                "compiled planning node bindings do not cover the immutable TaskGraph",
            )
        if not frozen_payloads:
            return compiled

        frozen_input_pack = {
            **input_pack,
            FROZEN_NODE_EXECUTOR_PAYLOADS_KEY: frozen_payloads,
        }
        frozen_order = replace(order, input_pack=frozen_input_pack)
        frozen_order.validate()
        return CompiledPlanningGraph.freeze(
            work_order=frozen_order,
            task_graph=graph,
            trace_digest=compiled.trace_digest,
            selected_candidate_ids=compiled.selected_candidate_ids,
            virtual_state_schema=compiled.virtual_state_schema,
            virtual_state_digest=compiled.virtual_state_digest,
            previous_revision=compiled.previous_revision,
            failure_evidence_refs=compiled.failure_evidence_refs,
        )

    async def _admit(
        self,
        result: SearchResult,
        *,
        compiled: CompiledPlanningGraph,
        authority: PlanningExecutionAuthority,
    ) -> PreparedPlanningExecution:
        graph = compiled.task_graph
        self._validate_delegate_targets(graph)
        # Planning admission owns the first HostCommand/TaskGraph write. Its
        # parent authority must be checked here, not only in execute_stream.
        # Replans bind the original Governor request rather than their
        # revision-suffixed command request identity.
        preflight = self._application.preflight_parent_governor_binding(
            parent_grant_ref=authority.governor_grant_ref,
            request_id=authority.base_command_request_id,
            session_id=authority.session_id,
            principal_id=authority.principal_id,
        )
        if inspect.isawaitable(preflight):
            await preflight
        receipt = await self._admission.admit(
            compiled,
            surface=authority.surface,
            command_request_id=authority.command_request_id(graph.revision),
            session_id=authority.session_id,
            principal_id=authority.principal_id,
            input_ref=authority.input_ref,
            input_sha256=authority.input_sha256,
            governor_grant_ref=authority.governor_grant_ref,
            target_id=authority.target_id,
            ingress_claim_ref=authority.ingress_claim_ref,
            ingress_audit_ref=authority.ingress_audit_ref,
        )
        prepared = PreparedPlanningExecution(
            search_result=result,
            compiled=compiled,
            admission=receipt,
        )
        prepared.validate()
        return prepared

    def _validate_delegate_targets(self, graph: TaskGraph) -> None:
        validator = self._delegate_target_validator
        if validator is None:
            return
        for node in graph.nodes:
            try:
                route = GovernedWorkerRoute.parse(node.route)
            except Exception as exc:
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    f"compiled TaskNode route is invalid: {node.node_id}",
                ) from exc
            if route.kind != "delegate" or route.target_id is None:
                continue
            try:
                verdict = validator(route.target_id)
            except Exception as exc:
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_DELEGATE_TARGET_UNAVAILABLE,
                    "compiled delegate target is not enabled: " + route.target_id,
                ) from exc
            if inspect.isawaitable(verdict):
                raise PlanningExecutionError(
                    PLANNING_EXECUTION_INVALID,
                    "delegate target validator must be synchronous before admission",
                )


__all__ = [
    "GovernedPlanningExecutionService",
    "PLANNING_EXECUTION_INVALID",
    "PLANNING_EXECUTION_DELEGATE_TARGET_UNAVAILABLE",
    "PLANNING_EXECUTION_REVISION_CONFLICT",
    "PLANNING_EXECUTION_SEARCH_FAILED",
    "PlanningExecutionAuthority",
    "PlanningExecutionError",
    "PreparedPlanningExecution",
]
