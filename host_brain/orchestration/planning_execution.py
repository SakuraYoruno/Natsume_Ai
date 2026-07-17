"""One-way PlanningSearch -> immutable TaskGraph -> governed execution flow.

The service intentionally has no legacy plan adapter and no execution-time
planner callback.  A failed revision may only be replaced through the explicit
``prepare_replan`` entry, which compiles ``previous.revision + 1`` and admits it
under a distinct HostCommand request identity while preserving graph/root
lineage.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
import inspect
from typing import Any

from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.orchestration.planning_admission import (
    GovernedPlanningAdmissionService,
    PlanningAdmissionReceipt,
)
from host_brain.orchestration.planning_graph_compiler import (
    CompiledPlanningGraph,
    PlanningGraphCompiler,
)
from host_brain.planning_search.contracts import SearchRequest, SearchResult, SearchStatus
from host_brain.planning_search.service import PlanningSearchService
from natsume_contracts.task import HostCommandSurface


PLANNING_EXECUTION_INVALID = "PLANNING_EXECUTION_INVALID"
PLANNING_EXECUTION_SEARCH_FAILED = "PLANNING_EXECUTION_SEARCH_FAILED"
PLANNING_EXECUTION_REVISION_CONFLICT = "PLANNING_EXECUTION_REVISION_CONFLICT"


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

    async def prepare_initial(
        self,
        request: SearchRequest,
        *,
        authority: PlanningExecutionAuthority,
        created_at: int,
    ) -> PreparedPlanningExecution:
        authority.validate()
        result = self._run_search(request)
        compiled = self._compiler.compile(result, created_at=int(created_at))
        if compiled.task_graph.revision != 1:
            raise PlanningExecutionError(
                PLANNING_EXECUTION_REVISION_CONFLICT,
                "initial planning execution must compile revision 1",
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
        compiled = self._compiler.replan(
            result,
            previous_graph=previous_graph,
            failure_evidence_refs=evidence_refs,
            created_at=int(created_at),
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

    async def _admit(
        self,
        result: SearchResult,
        *,
        compiled: CompiledPlanningGraph,
        authority: PlanningExecutionAuthority,
    ) -> PreparedPlanningExecution:
        graph = compiled.task_graph
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


__all__ = [
    "GovernedPlanningExecutionService",
    "PLANNING_EXECUTION_INVALID",
    "PLANNING_EXECUTION_REVISION_CONFLICT",
    "PLANNING_EXECUTION_SEARCH_FAILED",
    "PlanningExecutionAuthority",
    "PlanningExecutionError",
    "PreparedPlanningExecution",
]
