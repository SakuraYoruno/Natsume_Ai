from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import (
    get_chat_runtime_prompt_context,
    get_chat_runtime_render_target_lang,
    resolve_prompt_context_style_row,
)
from host_brain.conversation import conversation_max_turns

from .affect_state import AffectStateBlock, resolve_affect_state
from .dialogue_state import DialogueStateBlock, analyze_dialogue_state
from .drive_state import DriveStateBlock, resolve_drive_state
from .identity_source import (
    build_character_profile_disambiguation,
    build_style_usage_disambiguation,
    load_character_profile,
)
from .relationship_state import RelationshipStateBlock, resolve_relationship_state
from .topic_state import TopicStateBlock


@dataclass(frozen=True)
class IdentityBlock:
    render_target_lang: str = "zh"
    profile_disambiguation: str = ""
    character_profile: str = ""
    character_dynamic_patch: str = ""
    style_disambiguation: str = ""
    style_instruction: str = ""
    style_citation: str = ""


@dataclass(frozen=True)
class ContinuityBlock:
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    turns_injected: int = 0
    fetched_turns: int = 0
    older_turn_count: int = 0
    oldest_ts_ms: int | None = None
    newest_ts_ms: int | None = None
    skipped_corrupted_count: int = 0
    skipped_empty_count: int = 0
    deduped_same_role_count: int = 0


@dataclass(frozen=True)
class DialogueBlock:
    state: DialogueStateBlock = field(default_factory=DialogueStateBlock)


@dataclass(frozen=True)
class TopicBlock:
    state: TopicStateBlock = field(default_factory=TopicStateBlock)


@dataclass(frozen=True)
class AffectBlock:
    state: AffectStateBlock = field(default_factory=AffectStateBlock)


@dataclass(frozen=True)
class RelationshipBlock:
    state: RelationshipStateBlock = field(default_factory=RelationshipStateBlock)
    bond_summary: dict[str, Any] = field(default_factory=dict)
    motive_state: dict[str, Any] = field(default_factory=dict)
    social_brain_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriveBlock:
    state: DriveStateBlock = field(default_factory=DriveStateBlock)


@dataclass(frozen=True)
class MemoryBlock:
    retrieval_summary: dict[str, Any] = field(default_factory=dict)
    retrieval_evidence_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class IntentBlock:
    user_message: str = ""
    intent_draft: dict[str, Any] = field(default_factory=dict)
    sub_agent_result: dict[str, Any] = field(default_factory=dict)
    prefrontal_plan: dict[str, Any] = field(default_factory=dict)
    turn_context_sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderPolicyBlock:
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetTrace:
    total_budget: int = 0
    used_budget: int = 0
    trimmed_sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptContextPlaneV2:
    identity: IdentityBlock = field(default_factory=IdentityBlock)
    continuity: ContinuityBlock = field(default_factory=ContinuityBlock)
    dialogue: DialogueBlock = field(default_factory=DialogueBlock)
    topic: TopicBlock = field(default_factory=TopicBlock)
    affect: AffectBlock = field(default_factory=AffectBlock)
    relationship: RelationshipBlock = field(default_factory=RelationshipBlock)
    drive: DriveBlock = field(default_factory=DriveBlock)
    memory: MemoryBlock = field(default_factory=MemoryBlock)
    intent: IntentBlock = field(default_factory=IntentBlock)
    render_policy: RenderPolicyBlock = field(default_factory=RenderPolicyBlock)
    budget_trace: BudgetTrace = field(default_factory=BudgetTrace)


class ContextAssembler:
    """Assemble the normal-chat prompt context from one canonical source of truth."""

    def assemble(
        self,
        *,
        node_id: str,
        runtime_payload: Mapping[str, Any],
        turn_context_sections: list[str] | tuple[str, ...] | None = None,
    ) -> PromptContextPlaneV2:
        from host_brain.app.dependencies import (  # noqa: PLC0415
            get_conversation_history,
            get_conversation_turn_store,
            get_session_topic_state_store,
        )
        from host_brain.app.prompt_builder_utils import _build_character_dynamic_patch  # noqa: PLC0415
        from host_brain.persona.render_policy import build_render_policy  # noqa: PLC0415

        prompt_context = get_chat_runtime_prompt_context(runtime_payload)
        render_target_lang = get_chat_runtime_render_target_lang(runtime_payload)
        node_style = resolve_prompt_context_style_row(prompt_context, node_id=node_id)
        request_payload = dict(runtime_payload.get("request") or {})
        request_id = str(request_payload.get("request_id") or "").strip()
        session_id = str(request_payload.get("session_id") or "").strip()
        person_id = str(request_payload.get("person_id") or "").strip()

        merged_emotion_context = dict(runtime_payload.get("emotion_context") or {})
        emotion_context = dict(dict(runtime_payload.get("emotion") or {}).get("state") or {})
        effective_emotion_context = merged_emotion_context or emotion_context
        emotion_tag = str(effective_emotion_context.get("emotion_tag") or "").strip().upper()
        is_holiday = bool(runtime_payload.get("is_holiday") or prompt_context.get("is_holiday"))
        holiday_name = str(runtime_payload.get("holiday_name") or prompt_context.get("holiday_name") or "").strip()

        identity = IdentityBlock(
            render_target_lang=render_target_lang,
            profile_disambiguation=build_character_profile_disambiguation(render_target_lang),
            character_profile=load_character_profile(render_target_lang),
            character_dynamic_patch=_build_character_dynamic_patch(
                emotion_tag=emotion_tag,
                is_holiday=is_holiday,
                holiday_name=holiday_name,
                render_target_lang=render_target_lang,
            ),
            style_disambiguation=build_style_usage_disambiguation(render_target_lang),
            style_instruction=str(node_style.get("style_instruction") or node_style.get("instruction") or "").strip(),
            style_citation=str(node_style.get("citation") or "").strip(),
        )

        continuity = ContinuityBlock()
        current_turn_index: int | None = None
        if request_id and session_id:
            history_window = get_conversation_history().fetch_window_for_messages(
                session_id=session_id,
                max_turns=conversation_max_turns(),
                exclude_request_id=request_id,
                render_target_lang="ja" if render_target_lang == "ja" else "zh",
            )
            continuity = ContinuityBlock(
                recent_turns=[
                    {
                        "turn_id": turn.turn_id,
                        "request_id": turn.request_id,
                        "role": turn.role,
                        "content": turn.content,
                        "turn_index": turn.turn_index,
                        "lang": turn.lang,
                    }
                    for turn in history_window.recent_turns
                ],
                summary=str(history_window.older_summary or "").strip(),
                turns_injected=int(history_window.injected_count or 0),
                fetched_turns=int(history_window.fetched_count or 0),
                older_turn_count=int(history_window.older_turn_count or 0),
                oldest_ts_ms=history_window.oldest_ts_ms,
                newest_ts_ms=history_window.newest_ts_ms,
                skipped_corrupted_count=int(history_window.skipped_corrupted_count or 0),
                skipped_empty_count=int(history_window.skipped_empty_count or 0),
                deduped_same_role_count=int(history_window.deduped_same_role_count or 0),
            )
            try:
                current_turn_index = int(get_conversation_turn_store().next_turn_index(session_id=session_id) - 1)
            except Exception:
                current_turn_index = None

        intent = IntentBlock(
            user_message=str(request_payload.get("user_message") or ""),
            intent_draft=dict(runtime_payload.get("intent_draft") or {}),
            prefrontal_plan=dict(runtime_payload.get("planning_outline") or {}),
            turn_context_sections=tuple(
                str(section or "").strip()
                for section in list(turn_context_sections or [])
                if str(section or "").strip()
            ),
        )

        dialogue_state = analyze_dialogue_state(
            recent_turns=continuity.recent_turns[-6:],
            user_message=intent.user_message,
        )
        dialogue = DialogueBlock(state=dialogue_state)

        topic_state = get_session_topic_state_store().fetch_block(
            session_id=session_id,
            current_turn_index=current_turn_index,
        ) if session_id else TopicStateBlock()
        topic = TopicBlock(state=topic_state)

        affect_state = resolve_affect_state(
            emotion_context=effective_emotion_context,
            body_sense=dict(runtime_payload.get("body_sense") or {}),
            offline_state=dict(runtime_payload.get("offline_state") or {}),
            silence_state=dict(runtime_payload.get("silence_state") or {}),
        )
        affect = AffectBlock(state=affect_state)

        bond_summary = dict(prompt_context.get("bond_summary") or runtime_payload.get("bond_summary") or {})
        motive_state = dict(runtime_payload.get("motive_state") or {})
        social_brain_state = dict(runtime_payload.get("social_brain_state") or {})
        relationship_state = resolve_relationship_state(
            bond_summary=bond_summary,
            social_brain_state=social_brain_state,
        )
        relationship = RelationshipBlock(
            state=relationship_state,
            bond_summary=bond_summary,
            motive_state=motive_state,
            social_brain_state=social_brain_state,
        )

        drive_state = resolve_drive_state(
            motive_state=motive_state,
            affect_state=affect_state,
            relationship_state=relationship_state,
            dialogue_state=dialogue_state,
        )
        drive = DriveBlock(state=drive_state)

        memory = MemoryBlock(
            retrieval_summary=dict(runtime_payload.get("retrieval") or {}),
            retrieval_evidence_items=list(dict(runtime_payload.get("retrieval") or {}).get("evidence_items") or []),
        )

        render_policy = RenderPolicyBlock(
            state=build_render_policy(
                affect_state=affect_state,
                relationship_state=relationship_state,
                drive_state=drive_state,
                dialogue_state=dialogue_state,
                topic_state=topic_state,
                render_target_lang=render_target_lang,
            ).as_dict()
        )

        return PromptContextPlaneV2(
            identity=identity,
            continuity=continuity,
            dialogue=dialogue,
            topic=topic,
            affect=affect,
            relationship=relationship,
            drive=drive,
            memory=memory,
            intent=intent,
            render_policy=render_policy,
        )


def assemble_prompt_context_plane(
    *,
    node_id: str,
    runtime_payload: Mapping[str, Any],
    turn_context_sections: list[str] | tuple[str, ...] | None = None,
) -> PromptContextPlaneV2:
    return ContextAssembler().assemble(
        node_id=node_id,
        runtime_payload=runtime_payload,
        turn_context_sections=turn_context_sections,
    )


__all__ = [
    "AffectBlock",
    "BudgetTrace",
    "ContextAssembler",
    "ContinuityBlock",
    "DialogueBlock",
    "DriveBlock",
    "IdentityBlock",
    "IntentBlock",
    "MemoryBlock",
    "PromptContextPlaneV2",
    "RelationshipBlock",
    "RenderPolicyBlock",
    "TopicBlock",
    "assemble_prompt_context_plane",
]
