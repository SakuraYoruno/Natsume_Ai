"""Production composition for the canonical agent runtime.

Runtime behavior lives outside ``host_brain.app``.  This module only binds the
application ports used by Cortex and post-processing entry points.
"""

from __future__ import annotations

from functools import partial

from host_brain.agent_runtime.application_ports import (
    AgentRuntimeApplicationPorts,
)
from host_brain.app.chat_retrieval_helpers import (
    _run_retrieval,
    filter_retrieval_evidence_items_for_prompt,
)
from host_brain.app.context.conversation_experience_store import (
    get_conversation_experience_store,
)
from host_brain.app.conversation_writer import record_assistant_turn, record_user_turn
from host_brain.app.intelligent_runtime import resolve_tts_route
from host_brain.app.route_catalog_runtime import resolve_host_llm_runtime_async
from host_brain.app.state_arbiter_queries import _resolve_state_arbiter_bond_fact
from host_brain.app.surface_emotion_bond_flow import apply_surface_emotion_bond_flow
from host_brain.app.user_preference_store import build_user_preference_pack
from host_brain.app.vts_emotion_driver import _drive_stream_vts_emotion
from host_brain.observability.runtime_audit import _persist_post_process_audit
from host_brain.app.executive_runtime import executive_runtime_sse_generator
from host_brain.composition.process_services import (
    get_conversation_history,
    get_ledger,
    get_life_memory_service,
    get_life_memory_store,
    get_model_attempt_service,
    get_prompt_registry,
)
from host_brain.composition.life import PROCESS_LIFE_PROVIDER


async def _run_retrieval_with_life_ports(**kwargs):  # noqa: ANN003
    life = PROCESS_LIFE_PROVIDER.require()
    return await _run_retrieval(
        life_snapshot_reader=life.snapshot_reader,
        life_memory_store=get_life_memory_store(),
        **kwargs,
    )


def _record_user_turn_with_life_ports(**kwargs):  # noqa: ANN003
    life = PROCESS_LIFE_PROVIDER.require()
    return record_user_turn(
        ledger=get_ledger(),
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        **kwargs,
    )


def _record_assistant_turn_with_life_ports(**kwargs):  # noqa: ANN003
    life = PROCESS_LIFE_PROVIDER.require()
    return record_assistant_turn(
        ledger=get_ledger(),
        life_service=life.command_service,
        life_reader=life.snapshot_reader,
        **kwargs,
    )


AGENT_RUNTIME_PORTS = AgentRuntimeApplicationPorts(
        record_user_turn=_record_user_turn_with_life_ports,
        record_assistant_turn=_record_assistant_turn_with_life_ports,
        run_retrieval=_run_retrieval_with_life_ports,
        filter_retrieval_evidence_items_for_prompt=filter_retrieval_evidence_items_for_prompt,
        build_user_preference_pack=build_user_preference_pack,
        resolve_host_llm_runtime_async=resolve_host_llm_runtime_async,
        resolve_state_arbiter_bond_fact=_resolve_state_arbiter_bond_fact,
        apply_surface_emotion_bond_flow=apply_surface_emotion_bond_flow,
        get_conversation_experience_store=get_conversation_experience_store,
        drive_stream_vts_emotion=_drive_stream_vts_emotion,
        resolve_tts_route=resolve_tts_route,
        conversation_history=get_conversation_history,
        life_memory_service=get_life_memory_service,
        audit_ledger=get_ledger,
        prompt_registry=get_prompt_registry,
        model_attempt_service=get_model_attempt_service,
        persist_post_process_audit=partial(
            _persist_post_process_audit,
            audit_ledger=get_ledger,
        ),
        executive_router_sse_generator=executive_runtime_sse_generator,
)

__all__ = ["AGENT_RUNTIME_PORTS"]
