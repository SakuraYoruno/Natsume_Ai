"""App-local composition for canonical Agent Runtime application ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from threading import RLock
from typing import Any

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.app.chat_retrieval_helpers import (
    _build_surface_memory_recall_service,
    _run_retrieval,
    filter_retrieval_evidence_items_for_prompt,
)
from host_brain.app.context.conversation_experience_store import (
    build_conversation_experience_read_pack,
)
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.app.conversation_writer import record_assistant_turn, record_user_turn
from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.app.route_catalog_runtime import (
    resolve_host_llm_runtime,
    resolve_surface_reply_llm_runtime,
)
from host_brain.app.route_execution_ports import (
    RealtimeCompanionExecutionPorts,
    build_realtime_companion_execution_ports,
    stream_realtime_chat_with_context,
)
from host_brain.app.surface_runtime_composition import build_surface_runtime_ports
from host_brain.app.user_preference_store import build_user_preference_pack
from host_brain.observability.runtime_audit import _persist_post_process_audit
from host_brain.persistence.durable_task_status_projection import DurableTaskStatusProjection
from host_brain.tribe.prefrontal_runtime_policy import (
    resolve_prefrontal_persona_id,
    resolve_prefrontal_persona_workspace_id,
)


class AgentRuntimeCompositionError(RuntimeError):
    """Raised when a Host tries to use Agent Runtime outside its app scope."""


LifeAuthorityProvider = Callable[[], tuple[Any, Any]]


@dataclass(slots=True)
class AgentRuntimeApplicationScope:
    """Own the Agent Runtime port bundle for one Host ASGI lifespan."""

    host_services: HostLifecycleApplicationPorts
    _ports: AgentRuntimeApplicationPorts | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def provider(self) -> AgentRuntimeApplicationPorts | None:
        """Expose only the currently published app-local bundle."""

        return self._ports

    def initialize(
        self,
        *,
        life_authority_provider: LifeAuthorityProvider,
    ) -> AgentRuntimeApplicationPorts:
        """Create and publish the bundle once after Life authority is ready."""

        if self._ports is not None:
            raise AgentRuntimeCompositionError("HOST_AGENT_RUNTIME_PORTS_ALREADY_INITIALIZED")
        self._ports = build_agent_runtime_application_ports(
            host_services=self.host_services,
            life_authority_provider=life_authority_provider,
        )
        return self._ports

    def clear(self) -> None:
        """Withdraw the bundle before its owning collaborators are closed."""

        self._ports = None


def build_agent_runtime_application_ports(
    *,
    host_services: HostLifecycleApplicationPorts,
    life_authority_provider: LifeAuthorityProvider,
) -> AgentRuntimeApplicationPorts:
    """Bind the Agent Runtime to one Host application's explicit services."""

    if not callable(life_authority_provider):
        raise AgentRuntimeCompositionError("HOST_AGENT_RUNTIME_LIFE_AUTHORITY_MISSING")

    ledger = host_services.require_provider("ledger")
    life_memory_store = host_services.require_provider("life_memory_store")
    episodic_store = host_services.require_provider("episodic_store")
    life_memory_service = host_services.require_provider("life_memory_service")
    conversation_history = host_services.require_provider("conversation_history")
    conversation_turn_store = host_services.require_provider(
        "conversation_turn_store"
    )
    prompt_registry = host_services.require_provider("prompt_registry")
    model_attempt_service = host_services.require_provider("model_attempt_service")
    model_route_catalog = host_services.require_provider("model_route_catalog")
    session_topic_ledger_store = host_services.require_provider(
        "session_topic_ledger_store"
    )
    session_tactic_ledger_store = host_services.require_provider(
        "session_tactic_ledger_store"
    )
    knowledge_graph_neo4j_client = host_services.require_provider(
        "knowledge_graph_neo4j_client"
    )
    realtime_companion_ports: RealtimeCompanionExecutionPorts | None = None
    surface_memory_recall_service: Any | None = None
    surface_memory_recall_service_lock = RLock()

    def resolve_life_authority() -> tuple[Any, Any]:
        try:
            service, reader = life_authority_provider()
        except Exception as exc:
            raise AgentRuntimeCompositionError(
                "HOST_AGENT_RUNTIME_LIFE_AUTHORITY_UNAVAILABLE"
            ) from exc
        if service is None or reader is None:
            raise AgentRuntimeCompositionError(
                "HOST_AGENT_RUNTIME_LIFE_AUTHORITY_UNAVAILABLE"
            )
        return service, reader

    async def run_retrieval_with_life_authority(**kwargs: Any) -> Any:
        _, reader = resolve_life_authority()

        def build_surface_memory_recall_service() -> Any:
            nonlocal surface_memory_recall_service
            with surface_memory_recall_service_lock:
                if surface_memory_recall_service is None:
                    owned_episodic_store = episodic_store()
                    surface_memory_recall_service = _build_surface_memory_recall_service(
                        ledger=ledger(),
                        life_memory_store=life_memory_store(),
                        chroma_client=getattr(owned_episodic_store, "chroma", None),
                        knowledge_graph_neo4j_client=(
                            knowledge_graph_neo4j_client()
                        ),
                    )
                return surface_memory_recall_service

        return await _run_retrieval(
            life_snapshot_reader=reader,
            life_memory_store=life_memory_store(),
            conversation_history=conversation_history(),
            build_user_preference_pack=build_user_preference_pack_with_ledger,
            recall_service_factory=build_surface_memory_recall_service,
            active_knowledge_search=search_active_knowledge,
            **kwargs,
        )

    def record_user_turn_with_life_authority(**kwargs: Any) -> Any:
        service, reader = resolve_life_authority()
        return record_user_turn(
            ledger=ledger(),
            life_service=service,
            life_reader=reader,
            **kwargs,
        )

    def record_assistant_turn_with_life_authority(**kwargs: Any) -> Any:
        service, reader = resolve_life_authority()
        return record_assistant_turn(
            ledger=ledger(),
            life_service=service,
            life_reader=reader,
            session_topic_ledger_store=session_topic_ledger_store(),
            session_tactic_ledger_store=session_tactic_ledger_store(),
            **kwargs,
        )

    async def resolve_host_llm_runtime_with_catalog(**kwargs: Any) -> Any:
        catalog = model_route_catalog()
        if not callable(getattr(catalog, "resolve", None)):
            raise AgentRuntimeCompositionError(
                "HOST_AGENT_RUNTIME_MODEL_ROUTE_CATALOG_UNAVAILABLE"
            )
        return resolve_host_llm_runtime(
            catalog=catalog,
            **kwargs,
        )

    def resolve_surface_reply_llm_runtime_with_catalog(
        **kwargs: Any,
    ) -> dict[str, Any]:
        catalog = model_route_catalog()
        if not callable(getattr(catalog, "resolve", None)):
            raise AgentRuntimeCompositionError(
                "HOST_AGENT_RUNTIME_MODEL_ROUTE_CATALOG_UNAVAILABLE"
            )
        return resolve_surface_reply_llm_runtime(
            catalog=catalog,
            **kwargs,
        )

    def build_user_preference_pack_with_ledger(**kwargs: Any) -> dict[str, Any]:
        return build_user_preference_pack(ledger=ledger(), **kwargs)

    def task_status_projection() -> Any:
        return DurableTaskStatusProjection.from_ledger(ledger())

    def conversation_experience_read_pack(**kwargs: Any) -> dict[str, Any]:
        """Expose persisted Conversation Experience as an immutable read pack.

        Frozen Chat nodes may consume prior belief and lessons while executing,
        but they must not receive a mutable ConversationExperienceStore.  New
        experience mutation is deferred to the NE-06 proposal/activation path.
        """

        return build_conversation_experience_read_pack(ledger=ledger(), **kwargs)

    def resolve_expression_preferences(
        *,
        persona_user_id: str,
    ) -> dict[str, Any] | None:
        provider = host_services.evolution_activation_service
        if not callable(provider):
            return None
        service = provider()
        resolver = getattr(service, "resolve_expression_preferences", None)
        if not callable(resolver):
            return None
        payload = resolver(
            workspace_id=resolve_prefrontal_persona_workspace_id(),
            subject_id=resolve_prefrontal_persona_id(persona_user_id),
        )
        return dict(payload) if isinstance(payload, dict) else None

    def search_active_knowledge(
        *,
        query: str,
        person_id: str,
        top_k: int = 4,
        threshold: float = 0.05,
    ) -> list[dict[str, Any]]:
        """Read active Knowledge only for a Surface recall turn.

        This intentionally bypasses the legacy shadow wrapper.  Calling that
        wrapper here would duplicate Chroma work and append comparison rows to
        an otherwise read-only conversation path.
        """

        provider = host_services.evolution_activation_service
        if not callable(provider):
            return []
        workspace_id = resolve_prefrontal_persona_workspace_id()
        relationship_subject = str(person_id or "").strip()
        if (
            not relationship_subject
            or len(relationship_subject) > 128
            or any(
                ord(char) < 32 or ord(char) == 127
                for char in relationship_subject
            )
        ):
            return []
        resolved_top_k = max(1, min(int(top_k or 4), 8))
        resolved_threshold = max(0.0, min(float(threshold), 1.0))
        service = provider()
        search = getattr(service, "search_active_knowledge", None)
        if not callable(search):
            return []
        rows: list[dict[str, Any]] = []
        try:
            for namespace in (
                f"life-memory://person/{relationship_subject}",
                f"project-knowledge://workspace/{workspace_id}",
            ):
                for item in search(
                    workspace_id=workspace_id,
                    namespace=namespace,
                    query=str(query or ""),
                    top_k=resolved_top_k,
                    threshold=resolved_threshold,
                ):
                    if isinstance(item, dict):
                        rows.append(dict(item))
        except Exception:
            return []
        deduplicated: dict[str, dict[str, Any]] = {}
        for item in rows:
            key = str(item.get("doc_id") or "").strip()
            if not key:
                continue
            previous = deduplicated.get(key)
            if previous is None or float(item.get("score") or 0.0) > float(
                previous.get("score") or 0.0
            ):
                deduplicated[key] = item
        return sorted(
            deduplicated.values(),
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("doc_id") or ""),
            ),
        )[:resolved_top_k]

    def resolve_active_identity() -> dict[str, Any] | None:
        provider = host_services.evolution_activation_service
        if not callable(provider):
            return None
        service = provider()
        resolver = getattr(service, "active_identity", None)
        if not callable(resolver):
            return None
        try:
            payload = resolver(
                workspace_id=resolve_prefrontal_persona_workspace_id(),
            )
        except Exception:
            return None
        return dict(payload) if isinstance(payload, dict) else None

    async def stream_realtime_chat(**kwargs: Any):
        if realtime_companion_ports is None:
            raise AgentRuntimeCompositionError(
                "HOST_REALTIME_COMPANION_RUNTIME_UNAVAILABLE"
            )
        async for event in stream_realtime_chat_with_context(
            ports=realtime_companion_ports,
            **kwargs,
        ):
            yield event

    application_ports = AgentRuntimeApplicationPorts(
        record_user_turn=record_user_turn_with_life_authority,
        record_assistant_turn=record_assistant_turn_with_life_authority,
        run_retrieval=run_retrieval_with_life_authority,
        filter_retrieval_evidence_items_for_prompt=(
            filter_retrieval_evidence_items_for_prompt
        ),
        build_user_preference_pack=build_user_preference_pack_with_ledger,
        resolve_host_llm_runtime_async=resolve_host_llm_runtime_with_catalog,
        resolve_surface_reply_llm_runtime=(
            resolve_surface_reply_llm_runtime_with_catalog
        ),
        build_conversation_experience_read_pack=conversation_experience_read_pack,
        conversation_history=conversation_history,
        life_memory_service=life_memory_service,
        audit_ledger=ledger,
        prompt_registry=prompt_registry,
        model_attempt_service=model_attempt_service,
        context_read_ports=ConversationContextReadPorts(
            conversation_history=conversation_history,
            conversation_turn_store=conversation_turn_store,
            session_topic_ledger_store=session_topic_ledger_store,
            session_tactic_ledger_store=session_tactic_ledger_store,
        ),
        task_status_projection=task_status_projection,
        persist_post_process_audit=partial(
            _persist_post_process_audit,
            audit_ledger=ledger,
        ),
        stream_realtime_chat=stream_realtime_chat,
        resolve_expression_preferences=resolve_expression_preferences,
        search_active_knowledge=search_active_knowledge,
        resolve_active_identity=resolve_active_identity,
    )
    surface_runtime_ports = build_surface_runtime_ports(
        agent_runtime_ports=application_ports,
        life_authority_provider=life_authority_provider,
    )
    realtime_companion_ports = build_realtime_companion_execution_ports(
        surface_runtime_ports=surface_runtime_ports,
    )
    return application_ports


__all__ = [
    "AgentRuntimeApplicationScope",
    "AgentRuntimeCompositionError",
    "LifeAuthorityProvider",
    "build_agent_runtime_application_ports",
]
