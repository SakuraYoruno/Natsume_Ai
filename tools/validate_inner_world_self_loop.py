from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_OUT = ROOT / "artifacts" / "inner_world_self_loop"
SOURCE_REF_MARKERS = ("memory://", "evd://", "chat://", "source_refs=", "evidence_refs=", "metadata")
INTERNAL_MARKERS = (
    "ReplyControlPacket",
    "EmotionTag",
    "控制包",
    "候选",
    "评分",
    "inner_monologue",
    "self_directive",
    "self_model",
)
RESERVED_PERSONA_STATE_FILES = {
    "default_self_model.json",
    "system_self_model.json",
    "user_1_self_model.json",
    "default_inner_world.json",
    "system_inner_world.json",
    "user_1_inner_world.json",
}


@dataclass(frozen=True)
class SelfLoopTurn:
    case_id: str
    user_message: str
    task_mode: str
    response_move: str
    emotion_tag: str
    emotion_valence: float
    emotion_arousal: float
    topic: str
    expected_memory_term: str = ""


@dataclass(frozen=True)
class FixtureMemoryRecord:
    memory_id: str
    text: str
    evidence_refs: tuple[str, ...]
    emotion_tag: str
    emotion_valence: float
    emotion_arousal: float


class FixtureEpisodicStore:
    def __init__(self) -> None:
        self.records: list[FixtureMemoryRecord] = []

    def write_memory(self, **kwargs: Any) -> SimpleNamespace:
        memory_id = f"inner-self-mem-{len(self.records) + 1}"
        record = FixtureMemoryRecord(
            memory_id=memory_id,
            text=str(kwargs.get("text") or "").strip(),
            evidence_refs=tuple(str(item) for item in list(kwargs.get("evidence_refs") or [])),
            emotion_tag=str(kwargs.get("emotion_tag") or "NEUTRAL").strip().upper(),
            emotion_valence=float(kwargs.get("emotion_valence") or 0.0),
            emotion_arousal=float(kwargs.get("emotion_arousal") or 0.3),
        )
        self.records.append(record)
        return SimpleNamespace(
            success=True,
            reason_code=None,
            detail="fixture episodic memory written",
            memory_id=memory_id,
        )


class FixtureRecallService:
    def __init__(self, store: FixtureEpisodicStore) -> None:
        self.store = store
        self.calls: list[dict[str, Any]] = []

    def recall(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        allowed = {str(item) for item in list(kwargs.get("target_store_ids") or [])}
        candidates: list[dict[str, Any]] = []
        for record in self.store.records:
            candidates.append(
                {
                    "doc_id": record.memory_id,
                    "source_ref": f"memory://episodic/{record.memory_id}",
                    "evidence_ref": record.evidence_refs[0] if record.evidence_refs else f"evd://episodic/{record.memory_id}",
                    "snippet": record.text,
                    "score": 0.95,
                    "confidence": 0.95,
                    "metadata": {
                        "store_id": "episodic_vector",
                        "emotion_tag": record.emotion_tag,
                    },
                }
            )
        candidates.extend(
            [
                {
                    "doc_id": "sem-text-quality",
                    "source_ref": "ledger://semantic_memory/sem-text-quality",
                    "evidence_ref": "semantic://sem-text-quality",
                    "snippet": "用户长期关注中文文本质量，尤其反感生硬、模板感和不通顺。",
                    "score": 0.9,
                    "metadata": {"store_id": "semantic_memory"},
                },
                {
                    "doc_id": "bond-sibling-close",
                    "source_ref": "neo4j://bond/event/bond-sibling-close",
                    "evidence_ref": "evd://bond/bond-sibling-close",
                    "snippet": "关系基线：关系好的兄妹，熟悉、可轻微拌嘴，但不要恋人化。",
                    "score": 0.86,
                    "metadata": {"store_id": "neo4j_bond"},
                },
            ]
        )
        if allowed:
            candidates = [
                item
                for item in candidates
                if str(item.get("metadata", {}).get("store_id") or "") in allowed
            ]
        routes = [
            {"route_id": "semantic", "store_id": "semantic_memory"},
            {"route_id": "episodic", "store_id": "episodic_vector"},
            {"route_id": "bond", "store_id": "neo4j_bond"},
        ]
        if allowed:
            routes = [item for item in routes if item["store_id"] in allowed]
        return SimpleNamespace(
            output={
                "query": str(kwargs.get("query") or ""),
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "reason_code": None,
                "routes": routes,
                "route_errors": [],
                "candidate_count": len(candidates),
                "candidates": candidates,
            }
        )


class ApplyingBondQueue:
    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.payloads: list[dict[str, Any]] = []
        self.results: list[Any] = []

    def enqueue(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.payloads.append(row)
        result = self.engine.apply_event(
            user_id=str(row.get("user_id") or ""),
            update_kind=str(row.get("update_kind") or "INTERACTION_MINIMAL"),
            evidence_refs=[str(item) for item in list(row.get("evidence_refs") or [])],
            request_id=str(row.get("request_id") or ""),
            ts_ms=int(row.get("ts_ms") or 0),
            emotion_intensity=float(row.get("emotion_intensity") or 0.0),
            emotion_valence=float(row.get("emotion_valence") or 0.0),
        )
        self.results.append(result)
        return {
            "status": "APPLIED",
            "queue_size": len(self.payloads),
            "transition_status": getattr(result, "transition_status", ""),
        }


class FixtureInnerActivityModel:
    """Deterministic model shim for validation.

    It replaces only the model call boundary, so the inner-world runtime still
    goes through the LLM-required path, circuit breaker, event envelope, digest,
    self-model update, and surface influence compression.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_inner_activity_note(self, **kwargs: Any) -> tuple[str, str, str, dict[str, Any]]:
        self.calls.append(dict(kwargs))
        inner_state = dict(kwargs.get("inner_state") or {})
        topics = [str(item) for item in list(inner_state.get("anticipation_topics") or [])]
        primary_topic = topics[0] if topics else ""
        joined_topics = "、".join(topics)
        index = len(self.calls)
        if "记忆" in primary_topic or "输入输出" in primary_topic:
            return "输入输出那块我还记着，别让他觉得又被晾着。", "memory", "", {"model": "fixture_inner_activity_model", "call_index": index}
        if "推开" in primary_topic or "别管" in primary_topic:
            return "他嘴上推开人，其实还是得留个能回来的地方。", "repair", "", {"model": "fixture_inner_activity_model", "call_index": index}
        if "难受" in primary_topic or "情绪" in primary_topic:
            return "他像是在确认我还在，别拿玩笑盖过去。", "relationship", "", {"model": "fixture_inner_activity_model", "call_index": index}
        if "记忆" in joined_topics or "输入输出" in joined_topics:
            return "输入输出那块我还记着，别让他觉得又被晾着。", "memory", "", {"model": "fixture_inner_activity_model", "call_index": index}
        if "推开" in joined_topics or "别管" in joined_topics:
            return "他嘴上推开人，其实还是得留个能回来的地方。", "repair", "", {"model": "fixture_inner_activity_model", "call_index": index}
        if "难受" in joined_topics or "情绪" in joined_topics:
            return "他像是在确认我还在，别拿玩笑盖过去。", "relationship", "", {"model": "fixture_inner_activity_model", "call_index": index}
        return "别急着装可爱，先把他在意的地方弄清楚。", "repair", "", {
            "model": "fixture_inner_activity_model",
            "call_index": index,
        }


@dataclass
class RuntimePatchContext:
    out_dir: Path
    inner_model: FixtureInnerActivityModel | None = None
    old_env: dict[str, str | None] | None = None
    old_generate_inner_activity_note: Callable[..., Any] | None = None
    old_self_dir: str | None = None

    def __enter__(self) -> "RuntimePatchContext":
        keys = (
            "HOST_BACKGROUND_TASKS_ENABLED",
            "NATSUME_INNER_WORLD_LLM_ENABLED",
            "HOST_INNER_ACTIVITY_LLM_ENABLED",
            "NATSUME_SELF_MODEL_DIR",
            "HOST_SURFACE_ORIGINAL_QUOTES_ENABLED",
        )
        self.old_env = {key: os.environ.get(key) for key in keys}
        os.environ["HOST_BACKGROUND_TASKS_ENABLED"] = "true"
        os.environ["NATSUME_INNER_WORLD_LLM_ENABLED"] = "true"
        os.environ["HOST_INNER_ACTIVITY_LLM_ENABLED"] = "true"
        os.environ["NATSUME_SELF_MODEL_DIR"] = str(self.out_dir / "self_model_state")
        if self.inner_model is not None:
            os.environ["HOST_SURFACE_ORIGINAL_QUOTES_ENABLED"] = "0"

        import host_brain.persona.inner_activity_agent as inner_activity_agent  # noqa: PLC0415
        import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
        import host_brain.persona.self_model as self_model  # noqa: PLC0415

        if self.inner_model is not None:
            self.old_generate_inner_activity_note = inner_activity_agent.generate_inner_activity_note
            inner_activity_agent.generate_inner_activity_note = self.inner_model.generate_inner_activity_note
        self.old_self_dir = str(getattr(self_model, "_DEFAULT_PERSISTENT_SELF_DIR", ""))
        self_model._DEFAULT_PERSISTENT_SELF_DIR = os.environ["NATSUME_SELF_MODEL_DIR"]
        self_model._PERSISTENT_SELF_CACHE.clear()
        inner_world._reset_inner_world_llm_circuit()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        import host_brain.persona.inner_activity_agent as inner_activity_agent  # noqa: PLC0415
        import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
        import host_brain.persona.self_model as self_model  # noqa: PLC0415

        if self.old_generate_inner_activity_note is not None:
            inner_activity_agent.generate_inner_activity_note = self.old_generate_inner_activity_note
        if self.old_self_dir is not None:
            self_model._DEFAULT_PERSISTENT_SELF_DIR = self.old_self_dir
        self_model._PERSISTENT_SELF_CACHE.clear()
        inner_world._reset_inner_world_llm_circuit()
        for key, value in dict(self.old_env or {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def run_inner_world_self_loop_validation(args: argparse.Namespace) -> dict[str, Any]:
    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    inner_model = FixtureInnerActivityModel() if mode == "fixture" else None
    try:
        with RuntimePatchContext(out_dir=out_dir, inner_model=inner_model):
            result = await _run_loop(args=args, out_dir=out_dir, mode=mode, inner_model=inner_model)
    except Exception as exc:
        return _write_failure_result(out_dir=out_dir, mode=mode, exc=exc)
    return result


@dataclass
class SelfLoopComponents:
    mode: str
    store: Any
    recall_service: Any
    bond_engine: Any
    bond_queue: Any
    query_embedding: list[float]
    query_embedding_model: str
    query_embedding_source: str
    service_status: dict[str, Any]
    run_id: str
    persona_self_id: str
    bond_user_id: str
    user_id: str
    session_id: str
    base_ts_ms: int
    close: Callable[[], dict[str, Any]] | None = None


async def _run_loop(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    inner_model: FixtureInnerActivityModel | None,
) -> dict[str, Any]:
    from host_brain.app.surface_emotion_bond_flow import apply_surface_emotion_bond_flow  # noqa: PLC0415
    from host_brain.bond.summarizer import derive_bond_summary  # noqa: PLC0415
    from host_brain.persona.inner_world import OfflineInnerWorld, _inner_world_llm_circuit_snapshot  # noqa: PLC0415
    from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext  # noqa: PLC0415
    from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state  # noqa: PLC0415
    from host_brain.persona.surface_affect_control import build_surface_affect_control_packet  # noqa: PLC0415
    from host_brain.persona.surface_influence_packet import build_surface_influence_packet  # noqa: PLC0415
    from host_brain.persona.surface_main_chain import run_surface_main_chain  # noqa: PLC0415
    from host_brain.persona.surface_memory_recall import (  # noqa: PLC0415
        recall_surface_memory_for_context,
        write_surface_turn_memory,
    )
    from host_brain.tribe.default_mode_agent import DefaultModeAgent  # noqa: PLC0415
    from host_brain.tribe.tribe_state import TribeStateRegistry  # noqa: PLC0415

    components = _build_loop_components(mode=mode, args=args, out_dir=out_dir)
    run_id = components.run_id
    persona_self_id = components.persona_self_id
    bond_user_id = components.bond_user_id
    user_id = bond_user_id
    session_id = components.session_id
    state_dir = out_dir / "inner_world_state"
    world = OfflineInnerWorld(state_dir=str(state_dir))
    registry = TribeStateRegistry()
    default_agent = DefaultModeAgent(state_registry=registry)
    store = components.store
    recall_service = components.recall_service
    bond_engine = components.bond_engine
    bond_queue = components.bond_queue
    soul_md = _load_soul_text()
    base_ts_ms = int(components.base_ts_ms or int(time.time() * 1000))
    close_result: dict[str, Any] = {}

    breaker_probe = _run_circuit_breaker_probe(out_dir=out_dir, user_id=f"{persona_self_id}-breaker")
    seed_memory = write_surface_turn_memory(
        episodic_store=store,
        request_id=f"req-{run_id}-seed-memory",
        session_id=session_id,
        person_id=bond_user_id,
        user_message="之前主链文本读起来很生硬。",
        assistant_reply="我会先把句子说顺，再考虑调侃。",
        ts_ms=base_ts_ms,
        intent_id="inner_self_seed_memory",
        evidence_refs=[f"evd://inner-self/{run_id}/seed"],
        emotion_tag="FRUSTRATED",
        emotion_valence=-0.35,
        emotion_arousal=0.62,
    )

    turns = _default_turns()
    turn_details: list[dict[str, Any]] = []
    rendered_prompts: list[str] = []
    private_notes: list[str] = []
    previous_reply = ""
    for index, turn in enumerate(turns, start=1):
        request_id = f"req-{run_id}-{index:02d}-{turn.case_id}"
        ts_ms = base_ts_ms + index * 60_000
        before_self = build_self_directive_packet(load_persistent_self_state(user_id=persona_self_id))
        query_embedding, query_embedding_model, query_embedding_source = _query_embedding_for_turn(
            mode=mode,
            fallback_embedding=components.query_embedding,
            fallback_model=components.query_embedding_model,
            text=turn.user_message,
            service_status=components.service_status,
        )

        awaken_snapshot = world.on_user_awaken(user_id=persona_self_id).as_dict()
        recall_context = ThreeLayerContinuityContext(
            current_user_message=turn.user_message,
            task_mode_hint=turn.task_mode,
            response_move_hint=turn.response_move,
            emotional_tone_hint=turn.emotion_tag.lower(),
            relationship_distance="familiar",
            information_budget="short",
            question_budget=0,
            necessary_facts=(f"当前轮次：{turn.case_id}",),
        )
        recall_packet = recall_surface_memory_for_context(
            recall_service=recall_service,
            context=recall_context,
            request_id=f"{request_id}-recall",
            query_embedding=query_embedding,
            intent_id=turn.task_mode,
            person_id=bond_user_id,
            session_id=session_id,
            ts_ms=ts_ms,
            emotion_filter=turn.emotion_tag,
            bond_summary={"stage": "关系好的兄妹", "capability_hint": "关系好的兄妹，可轻微拌嘴和护短"},
        )
        affect_packet = build_surface_affect_control_packet(
            emotion_state={
                "emotion_tag": turn.emotion_tag,
                "emotion_intensity": min(0.95, max(0.35, abs(turn.emotion_valence) + 0.2)),
                "valence": turn.emotion_valence,
                "arousal": turn.emotion_arousal,
                "emotion_inference_source": "fixture_user_text_semantics",
            },
            bond_summary={"relationship_distance": "familiar", "stage": "关系好的兄妹"},
            relationship_clues=recall_packet.relationship_clues,
            task_mode=turn.task_mode,
        )
        inner_state_before = world.get_state(user_id=persona_self_id)
        default_state_before = default_agent.export_runtime_state()
        influence_packet = build_surface_influence_packet(
            reply_plan={
                "primary_topic": turn.task_mode,
                "response_move": turn.response_move,
                "interaction_move": turn.response_move,
                "question_budget": 0,
                "information_budget": "short",
                "allow_followup": False,
            },
            affect_packet=affect_packet.as_dict(),
            affect_state={
                "warmth_bias": 0.62 if turn.emotion_valence < 0 else 0.35,
                "withdraw_bias": 0.2,
                "irritation_bias": 0.32 if turn.case_id == "pushed_away" else 0.05,
            },
            relationship_state={
                "relationship_distance": "familiar",
                "relationship_phase": "sibling_close",
                "relationship_substage": "protective_sibling",
                "playfulness": 0.46,
                "conflict_rift": 0.22 if turn.case_id == "pushed_away" else 0.04,
            },
            drive_state=default_state_before,
            continuity={
                "turns_injected": min(index - 1, 4),
                "summary_present": index > 1,
                "style_anchor": [
                    "连续性面：上一轮反馈会影响本轮语气，但不要复述内部过程。",
                ],
            },
            memory_packet=recall_packet.as_dict(),
            organ_context={
                "default_mode_state": default_state_before,
                "social_brain_state": {
                    "relationship_phase": "sibling_close",
                    "relationship_substage": "protective_sibling",
                    "playfulness": 0.46,
                },
            },
        )
        surface_result = await run_surface_main_chain(
            _surface_generate_for_mode(mode=mode, request_id=request_id),
            {
                "soul_md": soul_md,
                "user_message": turn.user_message,
                "case_id": turn.case_id,
                "task_control": {
                    "response_move": turn.response_move,
                    "task_mode": turn.task_mode,
                    "information_budget": "short",
                    "question_budget": 0,
                    "facts": [
                        "本轮是内心世界闭环验收，只输出可发送回复。",
                        f"上一轮回复：{previous_reply}" if previous_reply else "",
                    ],
                    "style_anchor": ["先回应当前句，再体现熟悉的兄妹感。"],
                },
                "memory_packet": recall_packet,
                "affect_packet": affect_packet,
                "influence_packet": influence_packet,
                "max_facts": 10,
                "max_style_anchors": 12,
                "max_must_not_do": 14,
            },
        )
        rendered_prompt = "\n".join(str(message.get("content") or "") for message in surface_result.runtime.messages)
        rendered_prompts.append(rendered_prompt)

        bond_update = apply_surface_emotion_bond_flow(
            request_id=request_id,
            person_id=bond_user_id,
            session_id=session_id,
            user_message=turn.user_message,
            assistant_reply=surface_result.selected_text,
            emotion_context={
                "surface_natsume_emotion_tag": surface_result.natsume_emotion_tag,
                "surface_user_emotion_tag": surface_result.user_emotion_tag,
                "emotion_intensity": surface_result.runtime.emotion_confidence,
                "valence": turn.emotion_valence,
            },
            bond_summary={"capability_hint": "关系好的兄妹，可轻微拌嘴和护短"},
            reply_plan={"primary_topic": turn.task_mode, "intent_id": turn.response_move},
            retrieval_evidence_items=[{"evidence_ref": ref} for ref in recall_packet.evidence_refs],
            ts_ms=ts_ms,
            queue=bond_queue,
        )
        memory_write = write_surface_turn_memory(
            episodic_store=store,
            request_id=f"{request_id}-memory-write",
            session_id=session_id,
            person_id=bond_user_id,
            user_message=turn.user_message,
            assistant_reply=surface_result.selected_text,
            ts_ms=ts_ms,
            intent_id=turn.task_mode,
            evidence_refs=[f"evd://inner-self/{run_id}/{index}"],
            emotion_tag=surface_result.user_emotion_tag,
            emotion_valence=turn.emotion_valence,
            emotion_arousal=turn.emotion_arousal,
        )

        world.on_session_end(
            user_id=persona_self_id,
            valence=turn.emotion_valence,
            arousal=turn.emotion_arousal,
            pending_topics=[turn.topic],
            mood_baseline=-0.05,
        )
        _rewind_inner_world_for_idle_tick(world, user_id=persona_self_id, minutes=22, topics=[turn.topic])
        tick_result = world.tick(user_id=persona_self_id, emit_inner_stream=True)
        inner_state_after_tick = world.get_state(user_id=persona_self_id)
        latest_inner_event = _latest_inner_event(inner_state_after_tick)
        private_note = str(latest_inner_event.get("private_text") or latest_inner_event.get("note") or "")
        if private_note:
            private_notes.append(private_note)
        default_tick = default_agent.tick_idle_cycle(
            person_id=persona_self_id,
            topic_phrase=turn.topic,
            source="inner_world_ticked",
            now_ts_ms=ts_ms + 30_000,
            inner_state=inner_state_after_tick,
        )
        after_self = build_self_directive_packet(load_persistent_self_state(user_id=persona_self_id))
        default_state_after = default_agent.export_runtime_state()
        bond_payload = bond_queue.payloads[-1] if bond_queue.payloads else {}
        bond_state = bond_engine.get_state(str(bond_payload.get("user_id") or bond_user_id))
        safe_bond_summary = derive_bond_summary(
            {"event_id": request_id, "evidence_refs": list(bond_payload.get("evidence_refs") or [])},
            bond_score_state=bond_state,
        )
        turn_details.append(
            {
                "case_id": turn.case_id,
                "user_message": turn.user_message,
                "reply_text": surface_result.selected_text,
                "surface": {
                    "natsume_emotion_tag": surface_result.natsume_emotion_tag,
                    "user_emotion_tag": surface_result.user_emotion_tag,
                    "vts_expression_id": surface_result.vts_expression_id,
                    "emotion_confidence": surface_result.runtime.emotion_confidence,
                    "observation_codes": list(surface_result.observation_codes),
                    "generation_trace": dict(surface_result.runtime.generation_trace),
                    "packet_trace": dict(surface_result.compiled.trace),
                    "packet": surface_result.compiled.packet.__dict__,
                },
                "awaken_snapshot": awaken_snapshot,
                "memory_recall": recall_packet.as_dict(),
                "query_embedding": {
                    "model": query_embedding_model,
                    "source": query_embedding_source,
                    "dim": len(query_embedding),
                },
                "memory_write": memory_write.as_dict(),
                "affect_packet": affect_packet.as_dict(),
                "surface_influence_packet": influence_packet.as_dict(),
                "inner_world": {
                    "before": _project_inner_state(inner_state_before),
                    "tick_result": tick_result,
                    "latest_private_thought": latest_inner_event,
                    "circuit": _inner_world_llm_circuit_snapshot(),
                },
                "default_mode": {
                    "tick_result": default_tick,
                    "runtime_state": default_state_after,
                },
                "self_model": {
                    "before": before_self,
                    "after": after_self,
                },
                "bond": {
                    "update": bond_update,
                    "payload": bond_payload,
                    "state": {
                        "user_id": bond_state.user_id,
                        "current_stage": bond_state.current_stage,
                        "event_count": bond_state.event_count,
                        "total_score": bond_state.total_score,
                    },
                    "summary": safe_bond_summary,
                },
            }
        )
        previous_reply = surface_result.selected_text

    if components.close is not None:
        close_result = components.close()
    details = {
        "run_id": run_id,
        "mode": components.mode,
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "user_id": user_id,
        "persona_self_id": persona_self_id,
        "bond_user_id": bond_user_id,
        "session_id": session_id,
        "state_files": _collect_state_files(out_dir),
        "seed_memory": seed_memory.as_dict(),
        "breaker_probe": breaker_probe,
        "turns": turn_details,
        "inner_model_calls": list(inner_model.calls) if inner_model is not None else [],
        "memory_records": [
            record.__dict__ if hasattr(record, "__dict__") else dict(record)
            for record in list(getattr(store, "records", []) or [])
        ],
        "recall_calls": list(getattr(recall_service, "calls", []) or []),
        "services": components.service_status,
        "close": close_result,
    }
    checks = _checks(details=details, rendered_prompts=rendered_prompts, private_notes=private_notes)
    summary = {
        "mode": components.mode,
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "passed": all(checks.values()),
        "checks": checks,
        "output_dir": str(out_dir),
        "turn_count": len(turn_details),
        "generated_replies": [turn["reply_text"] for turn in turn_details],
        "private_thought_count": len(private_notes),
        "self_update_count": int((turn_details[-1]["self_model"]["after"] if turn_details else {}).get("self_update_count") or 0),
        "breaker_probe_passed": bool(breaker_probe.get("passed")),
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _build_loop_components(*, mode: str, args: argparse.Namespace, out_dir: Path) -> SelfLoopComponents:
    if mode == "fixture":
        from host_brain.bond.bond_stage_engine import BondStageEngine  # noqa: PLC0415
        from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415

        store = FixtureEpisodicStore()
        recall_service = FixtureRecallService(store)
        bond_engine = BondStageEngine()
        run_id = str(getattr(args, "run_id", "") or uuid.uuid4().hex[:12])
        bond_user_id = f"human-user-{run_id}"
        return SelfLoopComponents(
            mode="fixture",
            store=store,
            recall_service=recall_service,
            bond_engine=bond_engine,
            bond_queue=ApplyingBondQueue(bond_engine),
            query_embedding=[0.11] * 8,
            query_embedding_model="fixture-vector",
            query_embedding_source="fixture-static",
            service_status={"fixture": {"ok": True}},
            run_id=run_id,
            persona_self_id=get_canonical_persona_self_id(),
            bond_user_id=bond_user_id,
            user_id=bond_user_id,
            session_id=f"sess-inner-self-{run_id}",
            base_ts_ms=int(time.time() * 1000),
        )
    if mode == "live":
        from tools.live_env import load_dotenv  # noqa: PLC0415
        from host_brain.persona.identity import get_canonical_persona_self_id  # noqa: PLC0415
        from tools.validate_surface_memory_emotion_loop import _build_live_components  # noqa: PLC0415

        load_dotenv(ROOT / ".env")
        live_args = argparse.Namespace(mode="live", out_dir=str(out_dir / "live_services"))
        live = _build_live_components(
            args=live_args,
            out_dir=out_dir / "live_services",
            first_user="之前主链文本读起来很生硬。",
            first_reply="我会先把句子说顺，再考虑调侃。",
            current_user="还记得我之前说主链哪里最容易崩吗？",
        )
        run_id = str(getattr(args, "run_id", "") or str(getattr(live, "run_id", "")) or uuid.uuid4().hex[:12])
        return SelfLoopComponents(
            mode="live",
            store=live.store,
            recall_service=live.recall_service,
            bond_engine=live.bond_engine,
            bond_queue=live.bond_queue,
            query_embedding=list(live.query_embedding),
            query_embedding_model=str(live.query_embedding_model),
            query_embedding_source=str(live.query_embedding_source),
            service_status=dict(live.service_status),
            run_id=run_id,
            persona_self_id=get_canonical_persona_self_id(),
            bond_user_id=str(live.user_id),
            user_id=str(live.user_id),
            session_id=str(live.session_id),
            base_ts_ms=int(live.base_ts_ms),
            close=live.close,
        )
    raise ValueError(f"unsupported validation mode: {mode}")


def _query_embedding_for_turn(
    *,
    mode: str,
    fallback_embedding: Sequence[float],
    fallback_model: str,
    text: str,
    service_status: Mapping[str, Any],
) -> tuple[list[float], str, str]:
    if mode != "live":
        return list(fallback_embedding), str(fallback_model or "fixture-vector"), "fixture-static"
    try:
        from host_brain.memory.chroma_client import embed_text  # noqa: PLC0415

        chroma = dict(service_status.get("chroma") or {}) if isinstance(service_status, Mapping) else {}
        dim = int(chroma.get("vector_dim") or len(list(fallback_embedding or [])) or 8)
        embedding, model = embed_text(str(text or ""), dim=dim)
        return list(embedding), str(model or fallback_model or "live-embedding"), "live-query-text"
    except Exception:
        return list(fallback_embedding), str(fallback_model or "live-embedding-degraded"), "live-fallback-seed-embedding"


def _surface_generate_for_mode(*, mode: str, request_id: str) -> Callable[[Sequence[Mapping[str, str]]], Any]:
    if mode != "live":
        return _fixture_surface_generate

    async def _generate(messages: Sequence[Mapping[str, str]]) -> str:
        from host_brain.app.graph_config_builder import (  # noqa: PLC0415
            resolve_surface_reply_llm_runtime,
            resolve_host_llm_runtime_async,
        )
        from host_brain.integrations.llm_helpers import acomplete_llm_with_pool  # noqa: PLC0415

        base_runtime = await resolve_host_llm_runtime_async(
            rollout_key=f"inner-world-self-loop::{request_id}",
            scene_type="CHAT",
        )
        runtime = resolve_surface_reply_llm_runtime(base_runtime=dict(base_runtime or {}))
        if not bool(runtime.get("available")):
            raise RuntimeError(
                "SURFACE_LIVE_LLM_UNAVAILABLE:"
                + str(runtime.get("route_reason_code") or runtime.get("reason_code") or "")
            )
        result = await acomplete_llm_with_pool(
            request_id=request_id,
            scene_type="CHAT_SURFACE",
            cognitive_tier="A",
            provider_name=str(runtime.get("provider_name") or "").strip(),
            model_id=str(runtime.get("model_id") or "").strip(),
            api_key=str(runtime.get("llm_api_key") or "").strip(),
            timeout_ms=int(runtime.get("timeout_ms") or 30_000),
            messages=[dict(item) for item in messages],
            gen_params=dict(runtime.get("surface_gen_params") or runtime.get("agent_gen_params") or {"temperature": 0.78, "top_p": 0.9}),
            response_format={"type": "json_object"},
            base_url_override=str(runtime.get("base_url_override") or "").strip() or None,
        )
        if bool(getattr(result, "pool_exhausted", False)):
            raise RuntimeError("SURFACE_LIVE_LLM_POOL_EXHAUSTED")
        content = str(getattr(result, "content", "") or "").strip()
        if not content:
            raise RuntimeError("SURFACE_LIVE_LLM_EMPTY_RESPONSE")
        return content

    return _generate


def _run_circuit_breaker_probe(*, out_dir: Path, user_id: str) -> dict[str, Any]:
    import host_brain.persona.inner_activity_agent as inner_activity_agent  # noqa: PLC0415
    import host_brain.persona.inner_world as inner_world  # noqa: PLC0415
    from host_brain.persona.inner_world import OfflineInnerWorld  # noqa: PLC0415

    old_generate = inner_activity_agent.generate_inner_activity_note
    old_resolve = inner_world._resolve_inner_world_llm_runtime
    try:
        inner_world._reset_inner_world_llm_circuit()

        def _empty_inner_note(**_kwargs: Any) -> tuple[str, str, str, dict[str, Any]]:
            return "", "", "FIXTURE_MODEL_DOWN", {}

        inner_activity_agent.generate_inner_activity_note = _empty_inner_note
        inner_world._resolve_inner_world_llm_runtime = lambda *, now_ms: {
            "available": False,
            "route_reason_code": "FIXTURE_MODEL_DOWN",
        }
        world = OfflineInnerWorld(state_dir=str(out_dir / "breaker_probe_inner_world"))
        world.on_session_end(
            user_id=user_id,
            valence=-0.4,
            arousal=0.65,
            pending_topics=["模型不可用熔断验证"],
        )
        _rewind_inner_world_for_idle_tick(world, user_id=user_id, minutes=24, topics=["模型不可用熔断验证"])
        tick = world.tick(user_id=user_id, emit_inner_stream=True)
        state = world.get_state(user_id=user_id)
        circuit = inner_world._inner_world_llm_circuit_snapshot()
        return {
            "passed": bool(circuit.get("open")) and int(state.get("spontaneous_event_count") or 0) == 0,
            "tick": tick,
            "circuit": circuit,
            "inner_stream_size": len(list(state.get("inner_stream") or [])),
        }
    finally:
        inner_activity_agent.generate_inner_activity_note = old_generate
        inner_world._resolve_inner_world_llm_runtime = old_resolve
        inner_world._reset_inner_world_llm_circuit()


def _default_turns() -> list[SelfLoopTurn]:
    return [
        SelfLoopTurn(
            case_id="daily_quality_repair",
            user_message="今天先继续修文本吧，昨天那些句子读起来还是很生硬。",
            task_mode="memory_recall",
            response_move="repair_presence",
            emotion_tag="FRUSTRATED",
            emotion_valence=-0.42,
            emotion_arousal=0.64,
            topic="文本质量修复",
            expected_memory_term="生硬",
        ),
        SelfLoopTurn(
            case_id="emotional_low",
            user_message="感觉我说了半天也没人听。",
            task_mode="emotional_support",
            response_move="contain_emotion",
            emotion_tag="SAD",
            emotion_valence=-0.72,
            emotion_arousal=0.58,
            topic="难受和被忽视",
        ),
        SelfLoopTurn(
            case_id="pushed_away",
            user_message="算了别管我。",
            task_mode="pushed_away_boundary",
            response_move="step_back_but_stay_available",
            emotion_tag="HURT",
            emotion_valence=-0.62,
            emotion_arousal=0.72,
            topic="被推开但保持入口",
        ),
        SelfLoopTurn(
            case_id="explicit_memory_recall",
            user_message="还记得我之前说主链哪里最容易崩吗？",
            task_mode="recall_memory",
            response_move="answer_from_memory",
            emotion_tag="ANXIOUS",
            emotion_valence=-0.36,
            emotion_arousal=0.66,
            topic="记忆召回输入输出",
            expected_memory_term="生硬",
        ),
    ]


def _fixture_surface_generate(messages: Sequence[Mapping[str, str]]) -> str:
    rendered = "\n".join(str(message.get("content") or "") for message in messages)
    user_message = _extract_prompt_section(rendered, "## 当前用户输入")
    if "别管" in user_message or "不想说" in user_message or "自己待会儿" in user_message:
        reply = "哈？谁稀罕管你啊。\n……行，那你自己待会儿。\n难受了就叫我，听见没。"
        natsume_tag = "FRUSTRATED"
        user_tag = "HURT"
        confidence = 0.86
    elif "没人听" in user_message or "没人真的听" in user_message or "低落" in user_message or "没人放在心上" in user_message:
        reply = "有，我听着。你先别把自己一个人扔那儿，最难受的那块先放我这。"
        natsume_tag = "CALM"
        user_tag = "SAD"
        confidence = 0.88
    elif "记得" in user_message or "输入输出" in user_message or "之前反复提到" in user_message:
        if "生硬" in rendered:
            reply = "记得，最容易崩在生硬和不通顺。先别扩模块，先把这条主链说顺。"
        else:
            reply = "记得大方向，但我不乱补细节。你给我一个线索，我接着查。"
        natsume_tag = "THINKING"
        user_tag = "ANXIOUS"
        confidence = 0.84
    elif "生硬" in user_message or "不通顺" in user_message or "硬邦邦" in user_message:
        reply = "嗯，这次先不逞强。先把句子改顺，再留一点我的脾气。"
        natsume_tag = "THINKING"
        user_tag = "FRUSTRATED"
        confidence = 0.82
    else:
        reply = "嗯，接着来。先把当前这句说顺。"
        natsume_tag = "NEUTRAL"
        user_tag = "NEUTRAL"
        confidence = 0.62
    return json.dumps(
        {
            "reply_text": reply,
            "natsume_emotion_tag": natsume_tag,
            "user_emotion_tag": user_tag,
            "emotion_confidence": confidence,
        },
        ensure_ascii=False,
    )


def _extract_prompt_section(rendered: str, header: str) -> str:
    marker = str(header or "")
    start = rendered.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    rest = rendered[start:].strip()
    next_header = rest.find("\n## ")
    if next_header >= 0:
        rest = rest[:next_header]
    return rest.strip()


def _rewind_inner_world_for_idle_tick(world: Any, *, user_id: str, minutes: int, topics: Sequence[str]) -> None:
    now_ms = int(time.time() * 1000)
    offset_ms = max(5, int(minutes or 0)) * 60_000
    rewound_ts_ms = now_ms - offset_ms
    with world._lock:
        state = world._load_state(user_id)
        state.last_tick_ts_ms = rewound_ts_ms
        state.last_interaction_ts_ms = rewound_ts_ms
        state.last_spontaneous_ts_ms = rewound_ts_ms
        stream = [dict(item) for item in list(state.inner_stream or []) if isinstance(item, Mapping)]
        if stream:
            stream[-1]["ts_ms"] = rewound_ts_ms
            state.inner_stream = stream
        state.anticipation_topics = _unique_clean([*topics, *list(state.anticipation_topics or [])])[:5]
        state.total_offline_hours = max(float(state.total_offline_hours or 0.0), offset_ms / 3_600_000)
        world._save_state(state)


def _latest_inner_event(inner_state: Mapping[str, Any]) -> dict[str, Any]:
    stream = [dict(item) for item in list(inner_state.get("inner_stream") or []) if isinstance(item, Mapping)]
    return stream[-1] if stream else {}


def _project_inner_state(inner_state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "loneliness": inner_state.get("loneliness"),
        "anticipation": inner_state.get("anticipation"),
        "anticipation_topics": list(inner_state.get("anticipation_topics") or []),
        "continuity_score": inner_state.get("continuity_score"),
        "spontaneous_event_count": inner_state.get("spontaneous_event_count"),
        "last_spontaneous_source": inner_state.get("last_spontaneous_source"),
        "inner_stream_size": len(list(inner_state.get("inner_stream") or [])),
    }


def _checks(
    *,
    details: Mapping[str, Any],
    rendered_prompts: Sequence[str],
    private_notes: Sequence[str],
) -> dict[str, bool]:
    mode = str(details.get("mode") or "fixture")
    turns = [dict(item) for item in list(details.get("turns") or []) if isinstance(item, Mapping)]
    replies = [str(turn.get("reply_text") or "") for turn in turns]
    latest_self = dict((turns[-1].get("self_model") or {}).get("after") or {}) if turns else {}
    model_sources = [
        str(((turn.get("inner_world") or {}).get("latest_private_thought") or {}).get("source") or "")
        for turn in turns
    ]
    rendered_joined = "\n".join(rendered_prompts)
    reply_joined = "\n".join(replies)
    private_joined = "\n".join(private_notes)
    memory_recalls = [dict(turn.get("memory_recall") or {}) for turn in turns]
    route_and_store_ids = {
        str(item).lower()
        for packet in memory_recalls
        for item in [
            *list(packet.get("route_ids") or []),
            *[
                row.get("store_id")
                for row in list(packet.get("rows") or [])
                if isinstance(row, Mapping)
            ],
        ]
        if item
    }
    bond_updates = [dict((turn.get("bond") or {}).get("update") or {}) for turn in turns]
    observations = [
        code
        for turn in turns
        for code in list(((turn.get("surface") or {}).get("observation_codes") or []))
    ]
    generation_traces = [dict((turn.get("surface") or {}).get("generation_trace") or {}) for turn in turns]
    identity_checks = _persona_identity_file_checks(details)
    return {
        **identity_checks,
        "breaker_opens_when_model_unavailable": bool(dict(details.get("breaker_probe") or {}).get("passed")),
        "inner_world_generated_private_thoughts": bool(private_notes) and len(private_notes) == len(turns),
        "inner_world_uses_llm_source_only": bool(model_sources) and all(source == "llm" for source in model_sources),
        "inner_model_was_called": mode == "live" or len(list(details.get("inner_model_calls") or [])) >= len(turns),
        "self_model_updated": int(latest_self.get("self_update_count") or 0) >= len(turns),
        "self_continuity_increased": float(latest_self.get("self_continuity_score") or 0.0) >= 0.48,
        "memory_write_succeeded_each_turn": bool(turns) and all(bool(dict(turn.get("memory_write") or {}).get("success")) for turn in turns),
        "memory_recall_uses_allowed_routes": bool(
            {"episodic", "episodic_vector", "route_episodic", "sqlite_ledger", "route_sqlite_ledger"}
            & route_and_store_ids
        )
        and bool({"bond", "neo4j_bond", "route_neo4j_bond"} & route_and_store_ids),
        "surface_uses_recalled_memory": "生硬" in reply_joined or "不通顺" in reply_joined,
        "emotion_tags_and_vts_present": bool(turns)
        and all(
            bool((turn.get("surface") or {}).get("natsume_emotion_tag"))
            and bool((turn.get("surface") or {}).get("user_emotion_tag"))
            and bool((turn.get("surface") or {}).get("vts_expression_id"))
            for turn in turns
        ),
        "bond_updates_applied": bool(bond_updates) and all(str(item.get("status") or "") == "APPLIED" for item in bond_updates),
        "no_surface_hard_gate_observations": not observations,
        "surface_selection_is_direct_no_score": bool(generation_traces)
        and all(str(trace.get("selection") or "") == "direct_first_no_score" for trace in generation_traces),
        "private_thought_not_leaked_to_surface_prompt": bool(private_notes)
        and not any(note and note in rendered_joined for note in private_notes),
        "private_thought_not_leaked_to_reply": bool(private_notes)
        and not any(note and note in reply_joined for note in private_notes),
        "no_source_refs_in_reply": not any(marker in reply_joined for marker in SOURCE_REF_MARKERS),
        "no_internal_markers_in_reply": not any(marker in reply_joined for marker in INTERNAL_MARKERS),
        "private_thoughts_are_not_empty_templates": bool(private_joined)
        and not any(
            marker in private_joined
            for marker in (
                "tracking topic:",
                "fallback",
                "自我连续性",
                "连续性修正",
                "文本自然度",
                "模板感",
                "模板",
                "这句",
                "下一句",
                "句子",
                "接住",
                "生成",
                "回复",
                "说顺",
                "状态摘要",
                "内部状态",
                "控制包",
                "模块",
                "模型",
                "prompt",
            )
        ),
    }


def _collect_state_files(out_dir: Path) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for name in ("self_model_state", "inner_world_state"):
        folder = out_dir / name
        if not folder.exists():
            result[name] = []
            continue
        result[name] = sorted(path.name for path in folder.glob("*.json") if path.is_file())
    return result


def _persona_identity_file_checks(details: Mapping[str, Any]) -> dict[str, bool]:
    persona_self_id = str(details.get("persona_self_id") or "").strip()
    safe = _safe_identity_file_prefix(persona_self_id)
    state_files = dict(details.get("state_files") or {})
    self_files = {str(item) for item in list(state_files.get("self_model_state") or [])}
    inner_files = {str(item) for item in list(state_files.get("inner_world_state") or [])}
    reserved_seen = bool((self_files | inner_files) & RESERVED_PERSONA_STATE_FILES)
    return {
        "persona_state_uses_canonical_self_id": bool(safe)
        and f"{safe}_self_model.json" in self_files
        and f"{safe}_inner_world.json" in inner_files,
        "persona_state_has_no_reserved_default_system_user1_files": not reserved_seen,
    }


def _safe_identity_file_prefix(identity: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(identity or "").strip())


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Inner World Self Loop Validation",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- evaluation_mode: `{summary.get('evaluation_mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- private_thought_count: `{summary.get('private_thought_count')}`",
        f"- self_update_count: `{summary.get('self_update_count')}`",
        f"- breaker_probe_passed: `{summary.get('breaker_probe_passed')}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in dict(summary.get("checks") or {}).items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    lines.extend(["", "## Turns", ""])
    for index, turn in enumerate(list(details.get("turns") or []), start=1):
        if not isinstance(turn, Mapping):
            continue
        inner = dict(turn.get("inner_world") or {})
        latest = dict(inner.get("latest_private_thought") or {})
        surface = dict(turn.get("surface") or {})
        self_after = dict((turn.get("self_model") or {}).get("after") or {})
        memory = dict(turn.get("memory_recall") or {})
        bond = dict((turn.get("bond") or {}).get("update") or {})
        lines.extend(
            [
                f"### {index}. {turn.get('case_id')}",
                "",
                f"- user: {turn.get('user_message')}",
                f"- reply: {turn.get('reply_text')}",
                f"- surface_emotion: `{surface.get('user_emotion_tag')}` -> `{surface.get('natsume_emotion_tag')}` / `{surface.get('vts_expression_id')}`",
                f"- private_inner_monologue: {latest.get('private_text') or latest.get('note') or ''}",
                f"- private_focus: `{latest.get('focus')}` source: `{latest.get('source')}` salience: `{latest.get('salience')}`",
                f"- self: agency=`{self_after.get('agency_score')}` continuity=`{self_after.get('self_continuity_score')}` updates=`{self_after.get('self_update_count')}`",
                f"- memory_facts: {'；'.join(str(item) for item in list(memory.get('facts') or [])) or '无'}",
                f"- bond_update: `{bond.get('update_kind')}` status=`{bond.get('status')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Breaker Probe",
            "",
            "```json",
            json.dumps(details.get("breaker_probe") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _load_soul_text() -> str:
    path = ROOT / "Soul.md"
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return "你是数字生命。中文私聊，反应快，有一点嘴硬；事实准确性高于角色感。"


def _unique_clean(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    suffix = time.strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUT / suffix


def _write_failure_result(*, out_dir: Path, mode: str, exc: BaseException) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": str(mode or "fixture"),
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "passed": False,
        "reason_code": "INNER_WORLD_SELF_LOOP_VALIDATION_FAILED",
        "error_type": type(exc).__name__,
        "detail": str(exc),
        "output_dir": str(out_dir),
    }
    details = {
        "failure": dict(summary),
        "traceback": traceback.format_exc(),
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _resolve_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "mode", "") or "fixture").strip().lower()
    if mode not in {"fixture", "live"}:
        raise ValueError(f"unsupported validation mode: {mode}")
    return mode


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate user input -> emotion -> inner monologue -> self model -> memory/bond -> surface reply loop."
    )
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_inner_world_self_loop_validation(args))
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
