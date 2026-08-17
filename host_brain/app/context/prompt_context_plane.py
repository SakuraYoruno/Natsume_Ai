"""把对话、检索、感知与关系投影组装为只读提示上下文平面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.chat_runtime_input import (
    get_chat_runtime_prompt_context,
    get_chat_runtime_render_target_lang,
)
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.conversation import conversation_max_turns

from host_brain.persona.context.affect_state import AffectStateBlock, resolve_affect_state
from host_brain.persona.context.conversation_interpretation import ConversationInterpretation, interpret_conversation
from host_brain.persona.context.drive_state import DriveStateBlock, resolve_drive_state
from .interaction_state import InteractionState, build_interaction_state
from host_brain.persona.context.interaction_thread import InteractionThreadState, resolve_interaction_thread
from host_brain.persona.context.perception_packet import PerceptionPacket, build_perception_packet
from host_brain.persona.context.relationship_state import (
    RelationshipStateBlock,
    canonical_relationship_context_claimed,
    extract_canonical_relationship_projection,
    resolve_relationship_state,
)
from host_brain.persona.context.tactic_ledger import TacticLedgerBlock
from host_brain.persona.context.topic_ledger import TopicLedgerBlock


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
class TopicLedgerBlockWrapper:
    state: TopicLedgerBlock = field(default_factory=TopicLedgerBlock)


@dataclass(frozen=True)
class TacticLedgerBlockWrapper:
    state: TacticLedgerBlock = field(default_factory=TacticLedgerBlock)


@dataclass(frozen=True)
class ConversationInterpretationBlock:
    state: ConversationInterpretation = field(default_factory=ConversationInterpretation)


@dataclass(frozen=True)
class InteractionThreadBlock:
    state: InteractionThreadState = field(default_factory=InteractionThreadState)


@dataclass(frozen=True)
class InteractionStateBlock:
    state: InteractionState = field(default_factory=InteractionState)


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
class MultimodalBlock:
    state: PerceptionPacket = field(default_factory=PerceptionPacket)


@dataclass(frozen=True)
class IntentBlock:
    user_message: str = ""
    intent_draft: dict[str, Any] = field(default_factory=dict)
    sub_agent_result: dict[str, Any] = field(default_factory=dict)
    prefrontal_plan: dict[str, Any] = field(default_factory=dict)
    turn_context_sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplyPlanBlock:
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetTrace:
    total_budget: int = 0
    used_budget: int = 0
    trimmed_sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptContextPlaneV5:
    continuity: ContinuityBlock = field(default_factory=ContinuityBlock)
    topic_ledger: TopicLedgerBlockWrapper = field(default_factory=TopicLedgerBlockWrapper)
    tactic_ledger: TacticLedgerBlockWrapper = field(default_factory=TacticLedgerBlockWrapper)
    conversation_interpretation: ConversationInterpretationBlock = field(default_factory=ConversationInterpretationBlock)
    interaction_thread: InteractionThreadBlock = field(default_factory=InteractionThreadBlock)
    interaction_state: InteractionStateBlock = field(default_factory=InteractionStateBlock)
    affect: AffectBlock = field(default_factory=AffectBlock)
    relationship: RelationshipBlock = field(default_factory=RelationshipBlock)
    drive: DriveBlock = field(default_factory=DriveBlock)
    memory: MemoryBlock = field(default_factory=MemoryBlock)
    multimodal: MultimodalBlock = field(default_factory=MultimodalBlock)
    intent: IntentBlock = field(default_factory=IntentBlock)
    reply_plan: ReplyPlanBlock = field(default_factory=ReplyPlanBlock)
    budget_trace: BudgetTrace = field(default_factory=BudgetTrace)


class ContextAssembler:
    """从运行时载荷与只读端口装配普通聊天的规范提示上下文。

    装配器只读取历史、账本投影和本轮载荷：它不写入记忆、不提交关系状态，也不授予
    路由/工具权限。历史窗口、旧摘要和各软状态均作为提示线索，不能替代原始证据。
    """

    def __init__(
        self,
        *,
        read_ports: ConversationContextReadPorts | None = None,
    ) -> None:
        if read_ports is not None and not isinstance(
            read_ports,
            ConversationContextReadPorts,
        ):
            raise TypeError("read_ports must be ConversationContextReadPorts or None")
        self._read_ports = read_ports

    def assemble(
        self,
        *,
        node_id: str,
        runtime_payload: Mapping[str, Any],
        turn_context_sections: list[str] | tuple[str, ...] | None = None,
        history_max_turns: int | None = None,
        allow_older_summary: bool = True,
    ) -> PromptContextPlaneV5:
        """生成一次请求的 v5 分区快照。

        AB 评测历史优先于持久会话历史以保持实验可复现；生产历史读取则排除当前请求，
        防止尚未完成的用户输入重复注入。关系投影会按 person/workspace/session 对齐，
        不匹配的上下文不能跨主体进入提示词。
        """
        from host_brain.persona.reply_plan import build_reply_plan  # noqa: PLC0415

        read_ports = self._read_ports
        conversation_history = (
            read_ports.conversation_history() if read_ports is not None else None
        )
        conversation_turn_store = (
            read_ports.conversation_turn_store() if read_ports is not None else None
        )
        session_topic_ledger_store = (
            read_ports.session_topic_ledger_store()
            if read_ports is not None
            else None
        )
        session_tactic_ledger_store = (
            read_ports.session_tactic_ledger_store()
            if read_ports is not None
            else None
        )

        prompt_context = get_chat_runtime_prompt_context(runtime_payload)
        render_target_lang = get_chat_runtime_render_target_lang(runtime_payload)
        request_payload = dict(runtime_payload.get("request") or {})
        multimodal_payload = dict(runtime_payload.get("multimodal_input") or {})
        multimodal_observations = dict(multimodal_payload.get("observations") or request_payload.get("multimodal_observations") or {})
        request_id = str(request_payload.get("request_id") or "").strip()
        session_id = str(request_payload.get("session_id") or "").strip()
        person_id = str(request_payload.get("person_id") or "").strip()
        workspace_id = str(
            request_payload.get("workspace_id")
            or runtime_payload.get("workspace_id")
            or ""
        ).strip()
        ab_eval_payload = dict(runtime_payload.get("ab_eval") or {})
        eval_history_turns = [
            dict(item or {})
            for item in list(ab_eval_payload.get("history_turns") or [])
            if isinstance(item, Mapping)
        ]

        merged_emotion_context = dict(runtime_payload.get("emotion_context") or {})
        emotion_context = dict(dict(runtime_payload.get("emotion") or {}).get("state") or {})
        effective_emotion_context = merged_emotion_context or emotion_context

        continuity = ContinuityBlock()
        current_turn_index: int | None = None
        if eval_history_turns:
            normalized_recent_turns: list[dict[str, Any]] = []
            for index, raw_turn in enumerate(eval_history_turns):
                role = str(raw_turn.get("role") or "").strip().lower()
                role = "assistant" if role == "assistant" else "user"
                content = str(raw_turn.get("content") or raw_turn.get("text") or "").strip()
                if not content:
                    continue
                normalized_recent_turns.append(
                    {
                        "turn_id": str(raw_turn.get("turn_id") or f"ab_eval_{index}"),
                        "request_id": str(raw_turn.get("request_id") or ""),
                        "role": role,
                        "content": content,
                        "turn_index": int(raw_turn.get("turn_index") or index),
                        "lang": str(raw_turn.get("lang") or ("ja" if render_target_lang == "ja" else "zh")),
                        "metadata": {
                            **dict(raw_turn.get("metadata") or {}),
                            "source": "ab_eval_case",
                        },
                    }
                )
            continuity = ContinuityBlock(
                recent_turns=normalized_recent_turns,
                turns_injected=len(normalized_recent_turns),
                fetched_turns=len(normalized_recent_turns),
            )
            current_turn_index = len(normalized_recent_turns)
        elif (
            request_id
            and session_id
            and conversation_history is not None
            and conversation_turn_store is not None
        ):
            resolved_history_max_turns = max(
                1,
                int(history_max_turns if history_max_turns is not None else conversation_max_turns()),
            )
            current_request_turn = None
            try:
                current_request_turn = conversation_turn_store.find_turn(
                    session_id=session_id,
                    request_id=request_id,
                    role="user",
                )
            except Exception:
                current_request_turn = None
            history_window = conversation_history.fetch_window_for_messages(
                session_id=session_id,
                max_turns=resolved_history_max_turns,
                exclude_request_id=request_id,
                before_turn_index=(
                    int(current_request_turn.get("turn_index") or 0)
                    if current_request_turn is not None
                    else None
                ),
                render_target_lang="ja" if render_target_lang == "ja" else "zh",
                summary_threshold=(0 if not allow_older_summary else None),
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
                        "metadata": dict(turn.metadata or {}),
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
                if current_request_turn is not None:
                    current_turn_index = int(current_request_turn.get("turn_index") or 0)
                else:
                    current_turn_index = int(
                        conversation_turn_store.next_turn_index(
                            session_id=session_id
                        )
                        - 1
                    )
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

        topic_ledger_state = (
            session_topic_ledger_store.fetch_block(
                session_id=session_id,
                current_turn_index=current_turn_index,
            )
            if session_id and session_topic_ledger_store is not None
            else TopicLedgerBlock()
        )
        topic_ledger = TopicLedgerBlockWrapper(state=topic_ledger_state)
        tactic_ledger_state = (
            session_tactic_ledger_store.fetch_block(session_id=session_id)
            if session_id and session_tactic_ledger_store is not None
            else TacticLedgerBlock()
        )
        tactic_ledger = TacticLedgerBlockWrapper(state=tactic_ledger_state)

        factual_perception = build_perception_packet(
            multimodal_observations=multimodal_observations,
            multimodal_payload=multimodal_payload,
            attachment_summary=str(request_payload.get("attachment_summary") or "").strip(),
            user_text=intent.user_message,
        )
        multimodal = MultimodalBlock(
            state=factual_perception,
        )

        preliminary_interpretation_state = interpret_conversation(
            recent_turns=continuity.recent_turns[-6:],
            user_message=intent.user_message,
            topic_ledger=topic_ledger_state,
            multimodal_observations=multimodal.state.as_dict(),
        )
        interaction_thread_state = resolve_interaction_thread(
            recent_turns=continuity.recent_turns[-8:],
            current_literal_topic=preliminary_interpretation_state.literal_topic,
            current_user_message=intent.user_message,
            multimodal_observations=multimodal.state.as_dict(),
        )
        interaction_thread = InteractionThreadBlock(state=interaction_thread_state)

        interpretation_state = interpret_conversation(
            recent_turns=continuity.recent_turns[-6:],
            user_message=intent.user_message,
            topic_ledger=topic_ledger_state,
            multimodal_observations=multimodal.state.as_dict(),
            interaction_thread=interaction_thread_state,
        )
        conversation_interpretation = ConversationInterpretationBlock(state=interpretation_state)

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
        canonical_relationship = extract_canonical_relationship_projection(
            effective_emotion_context,
            expected_relationship_subject_id=person_id,
            expected_workspace_id=workspace_id,
            expected_session_id=session_id,
        )
        relationship_state = resolve_relationship_state(
            bond_summary=bond_summary,
            social_brain_state=social_brain_state,
            canonical_relationship=canonical_relationship,
            canonical_context_claimed=canonical_relationship_context_claimed(
                effective_emotion_context
            ),
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
            dialogue_state={
                "current_user_act": interpretation_state.literal_topic,
                "repeat_question_count": interpretation_state.repeat_question_count,
                "user_stance": interpretation_state.user_stance,
            },
        )
        drive = DriveBlock(state=drive_state)

        memory = MemoryBlock(
            retrieval_summary=dict(runtime_payload.get("retrieval") or {}),
            retrieval_evidence_items=list(dict(runtime_payload.get("retrieval") or {}).get("evidence_items") or []),
        )

        interaction_state = InteractionStateBlock(
            state=build_interaction_state(
                interpretation=interpretation_state.as_dict(),
                interaction_thread=interaction_thread_state.as_dict(),
                affect_state=affect_state.as_dict(),
                relationship_state=relationship_state.as_dict(),
                drive_state=drive_state.as_dict(),
                topic_ledger=topic_ledger_state.as_dict(),
                tactic_ledger=tactic_ledger_state.as_dict(),
                perception_summary=factual_perception.summary_for_chat,
            )
        )

        reply_plan = ReplyPlanBlock(
            state=build_reply_plan(
                affect_state=affect_state,
                relationship_state=relationship_state,
                drive_state=drive_state,
                interpretation=interpretation_state,
                interaction_thread=interaction_thread_state,
                topic_ledger=topic_ledger_state,
                tactic_ledger=tactic_ledger_state,
                multimodal=multimodal.state.as_dict(),
                render_target_lang=render_target_lang,
            ).as_dict()
        )

        return PromptContextPlaneV5(
            continuity=continuity,
            topic_ledger=topic_ledger,
            tactic_ledger=tactic_ledger,
            conversation_interpretation=conversation_interpretation,
            interaction_thread=interaction_thread,
            interaction_state=interaction_state,
            affect=affect,
            relationship=relationship,
            drive=drive,
            memory=memory,
            multimodal=multimodal,
            intent=intent,
            reply_plan=reply_plan,
        )


def assemble_prompt_context_plane(
    *,
    node_id: str,
    runtime_payload: Mapping[str, Any],
    turn_context_sections: list[str] | tuple[str, ...] | None = None,
    history_max_turns: int | None = None,
    allow_older_summary: bool = True,
    context_read_ports: ConversationContextReadPorts | None = None,
) -> PromptContextPlaneV4:
    return ContextAssembler(read_ports=context_read_ports).assemble(
        node_id=node_id,
        runtime_payload=runtime_payload,
        turn_context_sections=turn_context_sections,
        history_max_turns=history_max_turns,
        allow_older_summary=allow_older_summary,
    )


PromptContextPlaneV4 = PromptContextPlaneV5
MultimodalStateBlock = PerceptionPacket


def assemble_prompt_context_plane_v5(
    *,
    node_id: str,
    runtime_payload: Mapping[str, Any],
    turn_context_sections: list[str] | tuple[str, ...] | None = None,
    history_max_turns: int | None = None,
    allow_older_summary: bool = True,
    context_read_ports: ConversationContextReadPorts | None = None,
) -> PromptContextPlaneV5:
    return ContextAssembler(read_ports=context_read_ports).assemble(
        node_id=node_id,
        runtime_payload=runtime_payload,
        turn_context_sections=turn_context_sections,
        history_max_turns=history_max_turns,
        allow_older_summary=allow_older_summary,
    )


__all__ = [
    "AffectBlock",
    "BudgetTrace",
    "ContextAssembler",
    "ContinuityBlock",
    "ConversationInterpretationBlock",
    "DriveBlock",
    "IntentBlock",
    "InteractionStateBlock",
    "InteractionThreadBlock",
    "MemoryBlock",
    "MultimodalBlock",
    "MultimodalStateBlock",
    "PromptContextPlaneV4",
    "PromptContextPlaneV5",
    "RelationshipBlock",
    "ReplyPlanBlock",
    "TacticLedgerBlockWrapper",
    "TopicLedgerBlockWrapper",
    "assemble_prompt_context_plane",
    "assemble_prompt_context_plane_v5",
]
