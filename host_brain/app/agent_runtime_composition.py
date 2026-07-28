"""App-local composition for canonical Agent Runtime application ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
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
from host_brain.app.durable_task_status import build_durable_task_status_projection
from host_brain.app.host_lifecycle_composition import HostLifecycleApplicationPorts
from host_brain.app.route_catalog_runtime import (
    resolve_host_llm_runtime_async_from_catalog,
    resolve_surface_reply_llm_runtime,
)
from host_brain.app.state_arbiter_queries import _resolve_state_arbiter_bond_fact
from host_brain.app.tts_route import resolve_tts_route
from host_brain.app.user_preference_store import build_user_preference_pack
from host_brain.app.vts_emotion_driver import _drive_stream_vts_emotion
from host_brain.observability.runtime_audit import _persist_post_process_audit


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
    life_memory_service = host_services.require_provider("life_memory_service")
    conversation_history = host_services.require_provider("conversation_history")
    conversation_turn_store = host_services.require_provider(
        "conversation_turn_store"
    )
    prompt_registry = host_services.require_provider("prompt_registry")
    model_attempt_service = host_services.require_provider("model_attempt_service")
    model_route_catalog = host_services.require_provider("model_route_catalog")
    bond_stage_engine = host_services.require_provider("bond_stage_engine")
    session_topic_ledger_store = host_services.require_provider(
        "session_topic_ledger_store"
    )
    session_tactic_ledger_store = host_services.require_provider(
        "session_tactic_ledger_store"
    )
    tts_context_router = host_services.require_provider("tts_context_router")
    meta_learning_loop = host_services.require_provider("meta_learning_loop")
    bond_neo4j_client = host_services.require_provider("bond_neo4j_client")

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
            return _build_surface_memory_recall_service(
                ledger=ledger(),
                life_memory_store=life_memory_store(),
                bond_neo4j_client=bond_neo4j_client(),
            )

        return await _run_retrieval(
            life_snapshot_reader=reader,
            life_memory_store=life_memory_store(),
            conversation_history=conversation_history(),
            build_user_preference_pack=build_user_preference_pack_with_ledger,
            recall_service_factory=build_surface_memory_recall_service,
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
        return await resolve_host_llm_runtime_async_from_catalog(
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

    async def resolve_state_arbiter_bond_fact_with_engine(**kwargs: Any) -> Any:
        return await _resolve_state_arbiter_bond_fact(
            bond_stage_engine=bond_stage_engine(),
            **kwargs,
        )

    def build_user_preference_pack_with_ledger(**kwargs: Any) -> dict[str, Any]:
        return build_user_preference_pack(ledger=ledger(), **kwargs)

    def retire_surface_emotion_bond_flow(**_kwargs: Any) -> dict[str, Any]:
        """Keep the old Surface hook observable without resurrecting Bond writes.

        Foreground relationship outcomes are already committed through the
        canonical Life relationship lifecycle before this post-process stage.
        A second score queue would be volatile and could contradict recovery.
        """

        return {
            "status": "RETIRED_CANONICAL_LIFE",
            "authority": "CANONICAL_LIFE_JOURNAL",
            "reason_code": "LEGACY_SURFACE_BOND_FLOW_RETIRED",
        }

    def task_status_projection() -> Any:
        return build_durable_task_status_projection(ledger=ledger())

    def conversation_experience_read_pack(**kwargs: Any) -> dict[str, Any]:
        """Expose persisted Conversation Experience as an immutable read pack.

        Frozen Chat nodes may consume prior belief and lessons while executing,
        but they must not receive a mutable ConversationExperienceStore.  New
        experience mutation is deferred to the NE-06 proposal/activation path.
        """

        return build_conversation_experience_read_pack(ledger=ledger(), **kwargs)

    async def drive_stream_vts_emotion_with_ledger(**kwargs: Any) -> Any:
        return await _drive_stream_vts_emotion(
            ledger=ledger(),
            **kwargs,
        )

    def resolve_tts_route_with_host_services(**kwargs: Any) -> Any:
        return resolve_tts_route(
            tts_context_router=tts_context_router(),
            meta_learning_loop=meta_learning_loop(),
            **kwargs,
        )

    return AgentRuntimeApplicationPorts(
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
        resolve_state_arbiter_bond_fact=resolve_state_arbiter_bond_fact_with_engine,
        apply_surface_emotion_bond_flow=retire_surface_emotion_bond_flow,
        build_conversation_experience_read_pack=conversation_experience_read_pack,
        drive_stream_vts_emotion=drive_stream_vts_emotion_with_ledger,
        resolve_tts_route=resolve_tts_route_with_host_services,
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
    )


__all__ = [
    "AgentRuntimeApplicationScope",
    "AgentRuntimeCompositionError",
    "LifeAuthorityProvider",
    "build_agent_runtime_application_ports",
]
