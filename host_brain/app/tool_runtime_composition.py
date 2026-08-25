"""App-local composition owner for the governed Tool HTTP application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from host_brain.app.emotion_context import build_chat_governance_snapshot
from host_brain.app.governed_task_adapter import (
    GovernedHostRuntime,
    build_governed_host_runtime,
)
from host_brain.app.governed_tool_runtime import GovernedToolHttpApplication
from host_brain.app.model_provider_composition import ApplicationModelAttemptServicePort
from host_brain.app.production_authority import (
    build_durable_production_authority_verifier,
)
from host_brain.composition.command_ingress import ProcessCommandIngressContainer
from host_brain.composition.task_runtime import ProcessTaskRuntimeContainer
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.core.ids import now_ms
from host_brain.governor.action_preview import ActionPreviewService
from host_brain.runtime.context_application import RuntimeContextApplicationService
from host_brain.runtime.intent_admission import DurableIntentAdmissionVerifier
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectDispatcher,
)
from host_brain.contracts.task_runtime_feature_canary import (
    require_bounded_fanout_canary,
)
from host_brain.secure_ingress.authority import SecureIngressHttpAuthorityAdapter
from host_brain.secure_ingress.surfaces import IngressSurface
from host_brain.secure_ingress.graph_cancellation_authority import (
    SecureIngressGraphCancellationAuthorityVerifier,
)
from host_brain.tools.broker import (
    ToolBroker,
    ToolRuntimeBoundary,
    build_tool_runtime_boundary,
)
from host_brain.tools.catalog import ToolCatalog


@dataclass(frozen=True, slots=True)
class GovernedHostToolRuntimeDependencies:
    """Typed process-service providers supplied by the ASGI composition root."""

    skill_registry: Callable[[], Any]
    token_service: Callable[[], Any]
    capability_broker: Callable[[], Any]
    resource_manager: Callable[..., Any]
    ledger: Callable[[], Any]
    tool_catalog: Callable[[], ToolCatalog]
    intent_engine: Callable[[], IntentDecisionEngine]
    model_attempt_service: Callable[[], Any]
    knowledge_graph_neo4j_client: Callable[[], Any | None]
    pentest_secure_run_composition: Callable[[], Any | None] | None = None


@dataclass(slots=True)
class GovernedHostToolRuntimeComposition:
    """Own the governed Host and Tool applications for one app lifespan."""

    dependencies: GovernedHostToolRuntimeDependencies
    _boundary: ToolRuntimeBoundary | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _runtime: GovernedHostRuntime | None = field(default=None, init=False, repr=False)
    _application: GovernedToolHttpApplication | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def provide_runtime(self) -> GovernedHostRuntime | None:
        return self._runtime

    def provide_tool_application(self) -> GovernedToolHttpApplication | None:
        return self._application

    def _require_boundary(self) -> ToolRuntimeBoundary:
        if self._boundary is None:
            dependencies = self.dependencies
            self._boundary = build_tool_runtime_boundary(
                catalog=dependencies.tool_catalog(),
                token_service=dependencies.token_service(),
                ledger=dependencies.ledger(),
            )
        return self._boundary

    def provide_tool_broker(self) -> ToolBroker:
        """Expose metadata/registration only; execution stays app-local."""

        return self._require_boundary().broker

    def activate(
        self,
        *,
        task_runtime: ProcessTaskRuntimeContainer,
        ledger: Any,
        command_ingress: ProcessCommandIngressContainer,
        owner_id: str,
        runtime_settings: Any,
        runtime_context_application: RuntimeContextApplicationService,
        cancellation_authority_verifier: (
            SecureIngressGraphCancellationAuthorityVerifier
        ),
        action_preview_service: ActionPreviewService,
        acceptance_effect_dispatcher: AcceptanceEffectDispatcher,
        episodic_store: Any | None,
        semantic_recaller: Any | None,
        agent_artifact_store: Any | None = None,
        agent_workspace_runtime: Any | None = None,
        surface_runtime_ports: Any | None = None,
    ) -> GovernedHostRuntime:
        context = runtime_context_application.composition
        dependencies = self.dependencies
        boundary = self._require_boundary()
        broker = boundary.broker
        catalog = broker.catalog
        fanout_event_buffer_size = int(
            getattr(
                runtime_settings,
                "task_runtime_fanout_event_buffer_size",
                32,
            )
        )
        bounded_fanout_requested = bool(
            getattr(
                runtime_settings,
                "task_runtime_bounded_fanout_enabled",
                False,
            )
        )
        migration_summary = (
            dict(getattr(task_runtime.migration_result, "summary", {}) or {})
            if bounded_fanout_requested
            else {}
        )
        bounded_fanout_enabled = require_bounded_fanout_canary(
            requested=bounded_fanout_requested,
            receipt_ref=str(
                getattr(runtime_settings, "task_runtime_canary_receipt", "")
                or ""
            ),
            build_commit_sha=str(
                getattr(runtime_settings, "task_runtime_build_commit_sha", "")
                or ""
            ),
            runtime_migration_version=int(
                migration_summary.get("latest_version") or 0
            ),
            atomic_graph_success_enabled=bool(
                getattr(
                    runtime_settings,
                    "task_runtime_atomic_graph_success_enabled",
                    True,
                )
            ),
            fanout_event_buffer_size=fanout_event_buffer_size,
            receipt_store=getattr(task_runtime, "feature_canary_store", None),
            signature_verifier=getattr(
                task_runtime,
                "feature_canary_signature_verifier",
                None,
            ),
            now_ms=now_ms(),
        )
        from host_brain.pentest.governed_skill_runtime import (
            build_pentest_skill_execution_port,
        )

        pentest_composition = (
            dependencies.pentest_secure_run_composition()
            if callable(dependencies.pentest_secure_run_composition)
            else None
        )
        pentest_skill_runtime = build_pentest_skill_execution_port(
            pentest_composition
        )
        runtime = build_governed_host_runtime(
            task_runtime_container=task_runtime,
            ledger=ledger,
            skill_registry=dependencies.skill_registry(),
            token_service=dependencies.token_service(),
            capability_broker=dependencies.capability_broker(),
            model_attempt_service=ApplicationModelAttemptServicePort(
                dependencies.model_attempt_service,
            ),
            production_authority_verifier=(
                build_durable_production_authority_verifier(
                    ledger_provider=lambda: ledger,
                )
            ),
            resource_manager=dependencies.resource_manager(create_if_missing=False),
            episodic_store=episodic_store,
            semantic_recaller=semantic_recaller,
            agent_artifact_store=agent_artifact_store,
            agent_workspace_runtime=agent_workspace_runtime,
            tool_binding_resolver=broker.resolve_execution_binding,
            tool_execution_port_factory=boundary.bind_execution_port,
            tool_catalog=catalog,
            cancellation_authority_verifier=cancellation_authority_verifier,
            runtime_router=CanonicalRuntimeRouter(),
            owner_id=owner_id,
            bounded_fanout_enabled=bounded_fanout_enabled,
            fanout_event_buffer_size=fanout_event_buffer_size,
            atomic_graph_success_enabled=(
                runtime_settings.task_runtime_atomic_graph_success_enabled
            ),
            runtime_context_application=runtime_context_application,
            intent_admission_verifier=DurableIntentAdmissionVerifier(
                authority_port=context.intent_authority_port,
                intent_arbiter=context.intent_arbiter,
                event_store=context.event_store,
                review_store=task_runtime.review_store,
            ),
            degradation_capability_port=context.guardian,
            action_preview_service=action_preview_service,
            acceptance_effect_dispatcher=acceptance_effect_dispatcher,
            surface_runtime_ports=surface_runtime_ports,
            knowledge_graph_neo4j_client_provider=(
                dependencies.knowledge_graph_neo4j_client
            ),
            pentest_skill_runtime=pentest_skill_runtime,
        )
        application = GovernedToolHttpApplication(
            catalog=catalog,
            governed_runtime=runtime,
            intent_engine=dependencies.intent_engine(),
            ingress_adapter=SecureIngressHttpAuthorityAdapter(
                evaluator=command_ingress.evaluator,
                surface=IngressSurface.TOOL,
            ),
            ingress_store=command_ingress.store,
            tool_binding_resolver=(
                broker.resolve_execution_binding
            ),
            governance_snapshot_builder=build_chat_governance_snapshot,
            clock_ms=now_ms,
        )
        self._runtime = runtime
        self._application = application
        return runtime

    def clear(self) -> None:
        self._runtime = None
        self._application = None
        self._boundary = None


__all__ = [
    "GovernedHostToolRuntimeComposition",
    "GovernedHostToolRuntimeDependencies",
]
