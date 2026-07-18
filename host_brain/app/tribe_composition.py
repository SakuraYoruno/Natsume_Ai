"""Application adapter that supplies explicit ports to Tribe composition."""

from __future__ import annotations

from host_brain.tribe.application_ports import TribeApplicationPorts


def configure_tribe_application_ports() -> TribeApplicationPorts:
    from host_brain.app.context.conversation_belief_tracker import (
        build_conversation_understanding_pack,
    )
    from host_brain.app.context.conversation_experience_store import (
        get_conversation_experience_store,
    )
    from host_brain.app.emotion_context import (
        build_chat_emotion_snapshot,
        resolve_chat_emotion_context,
    )
    from host_brain.app.intent_seq import allocate_intent_seq
    from host_brain.app.user_preference_store import build_user_preference_pack
    from host_brain.composition import process_services
    from host_brain.composition.tribe_runtime import (
        compose_process_tribe_runtime,
        configure_tribe_application_ports as bind_ports,
    )
    from host_brain.tribe.registry import get_host_tribe_registry
    from host_brain.tribe.tribe_state import get_global_registry

    compose_process_tribe_runtime(
        state_registry=get_global_registry(),
        tribe_registry=get_host_tribe_registry(),
    )

    def resolve_process_mode_transition_service():
        monitor = process_services.get_activity_monitor()
        return getattr(monitor, "_mode_service", None) if monitor is not None else None

    return bind_ports(
        TribeApplicationPorts(
            activity_monitor=process_services.get_activity_monitor,
            mode_transition_service=resolve_process_mode_transition_service,
            bond_update_queue=process_services.get_bond_update_queue,
            ledger=process_services.get_ledger,
            life_snapshot_reader=process_services.get_life_snapshot_reader,
            silence_engine=process_services.get_silence_engine,
            state_write_arbiter=process_services.get_state_write_arbiter,
            emotion_timeline=process_services.get_emotion_timeline,
            hard_state=process_services.get_hard_state,
            resource_manager=process_services.get_resource_manager,
            bond_stage_engine=process_services.get_bond_stage_engine,
            shared_memory_index=process_services.get_shared_memory_index,
            milestone_tracker=process_services.get_milestone_tracker,
            model_attempt_service=process_services.get_model_attempt_service,
            prompt_registry=process_services.get_prompt_registry,
            resolve_chat_emotion_context=resolve_chat_emotion_context,
            build_chat_emotion_snapshot=build_chat_emotion_snapshot,
            build_user_preference_pack=build_user_preference_pack,
            conversation_experience_store=get_conversation_experience_store,
            build_conversation_understanding_pack=build_conversation_understanding_pack,
            allocate_intent_seq=allocate_intent_seq,
        )
    )


__all__ = ["configure_tribe_application_ports"]
