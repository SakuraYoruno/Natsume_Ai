from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_inner_world_self_loop import (  # noqa: E402
    FixtureInnerActivityModel,
    RuntimePatchContext,
    _build_loop_components,
    _collect_state_files,
    _default_turns,
    _latest_inner_event,
    _load_soul_text,
    _project_inner_state,
    _persona_identity_file_checks,
    _query_embedding_for_turn,
    _rewind_inner_world_for_idle_tick,
    _run_circuit_breaker_probe,
    _surface_generate_for_mode,
)
from tools.validate_surface_organ_system import (  # noqa: E402
    RAW_LEAK_MARKER,
    build_full_surface_organ_probe_context,
)


DEFAULT_OUT = ROOT / "artifacts" / "full_digital_life_loop"
SOURCE_REF_MARKERS = ("memory://", "evd://", "chat://", "source_refs=", "evidence_refs=", "metadata")
MEMORY_REPLY_TOKENS = ("输入输出", "生硬", "不通顺", "硬邦邦", "一硬就断", "读起来")
INTERNAL_MARKERS = (
    "ReplyControlPacket",
    "EmotionTag",
    "控制包",
    "候选",
    "评分",
    "score_trace",
    "candidate_count",
    "inner_monologue",
    "self_directive",
    "self_model",
)
RAW_CONTEXT_MARKERS = (
    RAW_LEAK_MARKER,
    "raw_organ_outputs",
    "organ_outputs",
    "natural_language_advice",
    "module_advice",
    "agent_advice",
    "vital_signs",
    "tribe_context_source",
)


async def run_full_digital_life_loop_validation(args: argparse.Namespace) -> dict[str, Any]:
    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    inner_model = FixtureInnerActivityModel() if mode == "fixture" else None
    try:
        with RuntimePatchContext(out_dir=out_dir, inner_model=inner_model):
            return await _run_loop(args=args, out_dir=out_dir, mode=mode, inner_model=inner_model)
    except Exception as exc:
        return _write_failure_result(out_dir=out_dir, mode=mode, exc=exc)


async def _run_loop(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    mode: str,
    inner_model: FixtureInnerActivityModel | None,
) -> dict[str, Any]:
    from host_brain.app.surface_context_packet import build_surface_context_packet  # noqa: PLC0415
    from host_brain.app.surface_emotion_bond_flow import apply_surface_emotion_bond_flow  # noqa: PLC0415
    from host_brain.bond.summarizer import derive_bond_summary  # noqa: PLC0415
    from host_brain.persona.inner_world import OfflineInnerWorld, _inner_world_llm_circuit_snapshot  # noqa: PLC0415
    from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext  # noqa: PLC0415
    from host_brain.persona.self_model import build_self_directive_packet, load_persistent_self_state  # noqa: PLC0415
    from host_brain.persona.surface_affect_control import build_surface_affect_control_packet  # noqa: PLC0415
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
    base_ts_ms = int(components.base_ts_ms or int(time.time() * 1000))
    turns = _selected_turns(args)
    world = OfflineInnerWorld(
        state_dir=str(out_dir / "inner_world_state"),
        persistence_enabled=True,
    )
    default_agent = DefaultModeAgent(state_registry=TribeStateRegistry())
    soul_md = _load_soul_text()
    close_result: dict[str, Any] = {}

    breaker_probe = _run_circuit_breaker_probe(out_dir=out_dir, user_id=f"{persona_self_id}-breaker")
    seed_memory = write_surface_turn_memory(
        episodic_store=components.store,
        request_id=f"req-{run_id}-seed-memory",
        session_id=session_id,
        person_id=bond_user_id,
        user_message="之前主链文本读起来很生硬。",
        assistant_reply="我会先把句子说顺，再考虑调侃。",
        ts_ms=base_ts_ms,
        intent_id="full_digital_life_seed_memory",
        evidence_refs=[f"evd://full-life/{run_id}/seed"],
        emotion_tag="FRUSTRATED",
        emotion_valence=-0.35,
        emotion_arousal=0.62,
    )

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
            recall_service=components.recall_service,
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
                "emotion_inference_source": "surface_text_semantics",
            },
            bond_summary={"relationship_distance": "familiar", "stage": "关系好的兄妹"},
            relationship_clues=recall_packet.relationship_clues,
            task_mode=turn.task_mode,
        )
        inner_state_before = world.get_state(user_id=persona_self_id)
        default_state_before = default_agent.export_runtime_state()
        bond_state_before = components.bond_engine.get_state(bond_user_id)
        bond_summary_before = derive_bond_summary(
            {"event_id": request_id, "evidence_refs": list(recall_packet.evidence_refs)},
            bond_score_state=bond_state_before,
        )
        organ_context, organ_collection_meta = _collect_organ_context(
            mode=mode,
            turn=turn,
            index=index,
            request_id=request_id,
            user_id=bond_user_id,
            session_id=session_id,
            default_state=default_state_before,
            recall_packet=recall_packet.as_dict(),
            affect_packet=affect_packet.as_dict(),
            bond_summary=bond_summary_before,
        )
        surface_context = build_surface_context_packet(
            user_message=turn.user_message,
            render_target_lang="zh",
            emotion_context={
                "emotion_tag": turn.emotion_tag,
                "emotion_intensity": min(0.95, max(0.35, abs(turn.emotion_valence) + 0.2)),
                "valence": turn.emotion_valence,
                "arousal": turn.emotion_arousal,
            },
            emotion_snapshot={},
            retrieval_summary={"status": "OK", "surface_memory_recall_packet": recall_packet.as_dict()},
            retrieval_evidence_items=[{"evidence_ref": ref} for ref in recall_packet.evidence_refs],
            person_id=bond_user_id,
            session_id=session_id,
            request_id=request_id,
            thread_id=f"thread-{run_id}",
            bond_summary=bond_summary_before,
            intent_draft={"intent_id": turn.task_mode, "primary_topic": turn.task_mode},
            organ_context=organ_context,
            surface_task_control={
                "task_mode": turn.task_mode,
                "primary_topic": turn.task_mode,
                "response_move": turn.response_move,
                "interaction_move": turn.response_move,
                "information_budget": "short",
                "question_budget": 0,
                "allow_followup": False,
            },
            eval_history_turns=[],
        )
        surface_influence_packet = dict(surface_context.get("surface_influence_packet") or {})
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
                        "本轮是完整数字生命闭环验收，只输出可发送回复。",
                        f"上一轮回复：{previous_reply}" if previous_reply else "",
                    ],
                    "style_anchor": ["先回应当前句，再体现熟悉的兄妹感。"],
                },
                "memory_packet": recall_packet,
                "affect_packet": affect_packet,
                "surface_influence_packet": surface_influence_packet,
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
            queue=components.bond_queue,
        )
        memory_write = write_surface_turn_memory(
            episodic_store=components.store,
            request_id=f"{request_id}-memory-write",
            session_id=session_id,
            person_id=bond_user_id,
            user_message=turn.user_message,
            assistant_reply=surface_result.selected_text,
            ts_ms=ts_ms,
            intent_id=turn.task_mode,
            evidence_refs=[f"evd://full-life/{run_id}/{index}"],
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
            source="full_digital_life_loop",
            now_ts_ms=ts_ms + 30_000,
            inner_state=inner_state_after_tick,
        )
        after_self = build_self_directive_packet(load_persistent_self_state(user_id=persona_self_id))
        default_state_after = default_agent.export_runtime_state()
        bond_payload = components.bond_queue.payloads[-1] if components.bond_queue.payloads else {}
        bond_state_after = components.bond_engine.get_state(str(bond_payload.get("user_id") or bond_user_id))
        safe_bond_summary = derive_bond_summary(
            {"event_id": request_id, "evidence_refs": list(bond_payload.get("evidence_refs") or [])},
            bond_score_state=bond_state_after,
        )
        turn_details.append(
            {
                "case_id": turn.case_id,
                "user_message": turn.user_message,
                "task_mode": turn.task_mode,
                "response_move": turn.response_move,
                "expected_memory_term": str(getattr(turn, "expected_memory_term", "") or ""),
                "reply_text": surface_result.selected_text,
                "organ_collection": organ_collection_meta,
                "surface_context_packet": {
                    "reply_plan": dict(surface_context.get("reply_plan") or {}),
                    "surface_organ_context_meta": dict(surface_context.get("surface_organ_context_meta") or {}),
                    "surface_organ_influence": dict(surface_context.get("surface_organ_influence") or {}),
                    "surface_influence_packet": surface_influence_packet,
                },
                "reply_control_packet": _model_payload(surface_result.compiled.packet),
                "surface": {
                    "natsume_emotion_tag": surface_result.natsume_emotion_tag,
                    "user_emotion_tag": surface_result.user_emotion_tag,
                    "vts_expression_id": surface_result.vts_expression_id,
                    "emotion_confidence": surface_result.runtime.emotion_confidence,
                    "observation_codes": list(surface_result.observation_codes),
                    "generation_trace": dict(surface_result.runtime.generation_trace),
                    "packet_trace": dict(surface_result.compiled.trace),
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
                        "user_id": bond_state_after.user_id,
                        "current_stage": bond_state_after.current_stage,
                        "event_count": bond_state_after.event_count,
                        "total_score": bond_state_after.total_score,
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
        "turns": turn_details,
        "inner_model_calls": list(inner_model.calls) if inner_model is not None else [],
        "services": components.service_status,
        "breaker_probe": breaker_probe,
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
        "min_mapped_organ_count": _min_mapped_organ_count(turn_details),
        "breaker_probe_passed": bool(breaker_probe.get("passed")),
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _collect_organ_context(
    *,
    mode: str,
    turn: Any,
    index: int,
    request_id: str,
    user_id: str,
    session_id: str,
    default_state: Mapping[str, Any],
    recall_packet: Mapping[str, Any],
    affect_packet: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if mode == "live":
        raw_context: Any = {}
        error = ""
        try:
            from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

            raw_context = get_cortex_agent().collect_tribe_context(
                request_body={
                    "request_id": request_id,
                    "person_id": user_id,
                    "session_id": session_id,
                    "user_message": str(turn.user_message),
                    "task_mode": str(turn.task_mode),
                },
                session_id=session_id,
                user_id=user_id,
            )
        except Exception as exc:  # pragma: no cover - live diagnostic path
            error = repr(exc)
        context = dict(raw_context or {}) if isinstance(raw_context, Mapping) else {}
        meta = {
            "status": "OK" if context else "DEGRADED",
            "source": "cortex_collect_tribe_context",
            "key_count": len(context),
            "error": error,
        }
    else:
        context = build_full_surface_organ_probe_context()
        meta = {
            "status": "OK",
            "source": "fixture_full_surface_organ_probe",
            "key_count": len(context),
            "error": "",
        }
    enriched = _enrich_organ_context(
        context,
        turn=turn,
        index=index,
        default_state=default_state,
        recall_packet=recall_packet,
        affect_packet=affect_packet,
        bond_summary=bond_summary,
    )
    meta["key_count_after_enrichment"] = len(enriched)
    return enriched, meta


def _enrich_organ_context(
    context: Mapping[str, Any],
    *,
    turn: Any,
    index: int,
    default_state: Mapping[str, Any],
    recall_packet: Mapping[str, Any],
    affect_packet: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
) -> dict[str, Any]:
    payload = dict(context or {})
    intensity = min(0.95, max(0.35, abs(float(turn.emotion_valence)) + 0.2))
    vital = dict(payload.get("vital_signs") or {})
    vital.update(_vital_overlay_for_turn(turn, intensity=intensity))
    payload["vital_signs"] = vital
    payload["emotion_context"] = {
        "emotion_tag": str(turn.emotion_tag),
        "emotion_intensity": intensity,
        "valence": float(turn.emotion_valence),
        "arousal": float(turn.emotion_arousal),
    }
    payload["voice_emotion"] = {
        "emotion_tag": str(turn.emotion_tag),
        "emotion_intensity": intensity,
        "source": "text_semantics_fallback",
    }
    payload["bond_context"] = {
        "stage": "FRIEND",
        "stage_label": "关系好的兄妹",
        "relationship_substage": "protective_sibling" if turn.case_id != "pushed_away" else "minor_conflict_sibling",
        "bond_vector": {"intimacy": 0.58, "trust": 0.72, "playfulness": 0.46},
        "summary": dict(bond_summary or {}),
    }
    payload["bond_vector"] = {"intimacy": 0.58, "trust": 0.72, "playfulness": 0.46}
    payload["social_brain_state"] = {
        "bond_stage": "FRIEND",
        "stage_label": "关系好的兄妹",
        "relationship_substage": "protective_sibling" if turn.case_id != "pushed_away" else "minor_conflict_sibling",
        "relationship_playfulness": 0.50 if turn.case_id != "emotional_low" else 0.18,
        "relationship_conflict_rift": 0.26 if turn.case_id == "pushed_away" else 0.05,
    }
    payload["default_mode_state"] = dict(default_state or {})
    payload["memory_layers"] = {
        "recent_summary_present": index > 1,
        "episodic_available": bool(recall_packet.get("facts") or recall_packet.get("rows")),
        "route_ids": list(recall_packet.get("route_ids") or []),
    }
    payload["language_dual_system"] = {
        "response_mode": "chat_text",
        "vts_expression_id": _vts_expression_for_emotion(str(turn.emotion_tag)),
    }
    payload["language_style"] = {"response_mode": "chat_text"}
    payload["raw_organ_outputs"] = {"limbic": {"messages": [RAW_LEAK_MARKER]}}
    payload["surface_validation_meta"] = {
        "turn_case_id": str(turn.case_id),
        "affect_packet_present": bool(affect_packet),
    }
    return payload


def _vital_overlay_for_turn(turn: Any, *, intensity: float) -> dict[str, Any]:
    base = {
        "stress_index": 0.32 + intensity * 0.25,
        "cognitive_load_index": 0.36,
        "sleep_pressure": 0.24,
        "immune_risk_level": "low",
        "loneliness_level": 0.30,
        "mirror_resonance_strength": 0.46,
        "pain_level": 0.18,
        "silence_willingness": 0.58,
        "voice_emotion_tag": str(turn.emotion_tag),
        "voice_emotion_intensity": intensity,
    }
    if str(turn.case_id) == "emotional_low":
        base.update({"loneliness_level": 0.72, "mirror_resonance_strength": 0.66, "stress_index": 0.56})
    elif str(turn.case_id) == "pushed_away":
        base.update({"stress_index": 0.72, "pain_level": 0.42, "silence_willingness": 0.34, "immune_risk_level": "medium"})
    elif str(turn.case_id) == "explicit_memory_recall":
        base.update({"cognitive_load_index": 0.44, "mirror_resonance_strength": 0.52})
    return base


def _vts_expression_for_emotion(emotion_tag: str) -> str:
    tag = str(emotion_tag or "").strip().upper()
    mapping = {
        "FRUSTRATED": "HK_Angry",
        "ANGRY": "HK_Angry",
        "HURT": "HK_Sad",
        "SAD": "HK_Sad",
        "ANXIOUS": "HK_Thinking",
        "THINKING": "HK_Thinking",
    }
    return mapping.get(tag, "HK_Normal")


def _checks(
    *,
    details: Mapping[str, Any],
    rendered_prompts: Sequence[str],
    private_notes: Sequence[str],
) -> dict[str, bool]:
    mode = str(details.get("mode") or "fixture")
    turns = [dict(item) for item in list(details.get("turns") or []) if isinstance(item, Mapping)]
    replies = [str(turn.get("reply_text") or "") for turn in turns]
    memory_answer_turns = [
        turn for turn in turns
        if str(turn.get("expected_memory_term") or "").strip()
        and str(turn.get("response_move") or "") == "answer_from_memory"
    ]
    rendered_joined = "\n".join(rendered_prompts)
    reply_joined = "\n".join(replies)
    private_joined = "\n".join(private_notes)
    mapped_counts = [_mapped_count(turn) for turn in turns]
    min_expected_organs = 23
    organ_metas = [dict(turn.get("organ_collection") or {}) for turn in turns]
    surface_metas = [
        dict((turn.get("surface_context_packet") or {}).get("surface_organ_context_meta") or {})
        for turn in turns
    ]
    reply_packets = [dict(turn.get("reply_control_packet") or {}) for turn in turns]
    packet_traces = [dict((turn.get("surface") or {}).get("packet_trace") or {}) for turn in turns]
    generation_traces = [dict((turn.get("surface") or {}).get("generation_trace") or {}) for turn in turns]
    observations = [
        code
        for turn in turns
        for code in list(((turn.get("surface") or {}).get("observation_codes") or []))
    ]
    model_sources = [
        str(((turn.get("inner_world") or {}).get("latest_private_thought") or {}).get("source") or "")
        for turn in turns
    ]
    latest_self = dict((turns[-1].get("self_model") or {}).get("after") or {}) if turns else {}
    memory_recalls = [dict(turn.get("memory_recall") or {}) for turn in turns]
    memory_fact_joined = "\n".join(
        str(fact)
        for packet in memory_recalls
        for fact in list(packet.get("facts") or [])
    )
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
    identity_checks = _persona_identity_file_checks(details)
    return {
        **identity_checks,
        "breaker_opens_when_model_unavailable": bool(dict(details.get("breaker_probe") or {}).get("passed")),
        "organ_context_collected_each_turn": bool(turns)
        and all(str(meta.get("status") or "") == "OK" for meta in organ_metas)
        and all(int(meta.get("key_count_after_enrichment") or 0) > 0 for meta in organ_metas),
        "surface_context_packet_prepared_each_turn": bool(surface_metas)
        and all(str(meta.get("status") or "") == "OK" for meta in surface_metas),
        "mapped_surface_organs_present": bool(mapped_counts) and min(mapped_counts) >= min_expected_organs,
        "reply_control_has_compressed_organ_influence": bool(packet_traces)
        and all(bool(trace.get("influence_applied")) for trace in packet_traces)
        and all(any("器官状态" in item for item in list(packet.get("must_not_do") or [])) for packet in reply_packets),
        "surface_prompt_has_no_raw_organ_context": not any(marker in rendered_joined for marker in RAW_CONTEXT_MARKERS),
        "raw_organ_text_not_leaked_to_reply": not any(marker in reply_joined for marker in RAW_CONTEXT_MARKERS),
        "memory_write_succeeded_each_turn": bool(turns)
        and all(bool(dict(turn.get("memory_write") or {}).get("success")) for turn in turns),
        "memory_recall_uses_allowed_routes": bool(
            {"episodic", "episodic_vector", "route_episodic", "sqlite_ledger", "route_sqlite_ledger"} & route_and_store_ids
        )
        and bool({"bond", "neo4j_bond", "route_neo4j_bond"} & route_and_store_ids),
        "memory_fact_available_to_surface": "生硬" in memory_fact_joined or "不通顺" in memory_fact_joined,
        "explicit_memory_reply_uses_recalled_fact": all(
            _reply_uses_expected_memory_fact(turn)
            for turn in memory_answer_turns
        ),
        "emotion_tags_and_vts_present": bool(turns)
        and all(
            bool((turn.get("surface") or {}).get("natsume_emotion_tag"))
            and bool((turn.get("surface") or {}).get("user_emotion_tag"))
            and bool((turn.get("surface") or {}).get("vts_expression_id"))
            for turn in turns
        ),
        "bond_updates_applied": bool(bond_updates) and all(str(item.get("status") or "") == "APPLIED" for item in bond_updates),
        "inner_world_generated_private_thoughts": bool(private_notes) and len(private_notes) == len(turns),
        "inner_world_uses_llm_source_only": bool(model_sources) and all(source == "llm" for source in model_sources),
        "inner_model_was_called": mode == "live" or len(list(details.get("inner_model_calls") or [])) >= len(turns),
        "self_model_updated": int(latest_self.get("self_update_count") or 0) >= len(turns),
        "surface_selection_is_direct_no_score": bool(generation_traces)
        and all(str(trace.get("selection") or "") == "direct_first_no_score" for trace in generation_traces)
        and all(int(trace.get("candidate_count") or 0) <= 1 for trace in generation_traces),
        "no_surface_hard_gate_observations": not observations,
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


def _reply_uses_expected_memory_fact(turn: Mapping[str, Any]) -> bool:
    expected = str(turn.get("expected_memory_term") or "").strip()
    reply = str(turn.get("reply_text") or "")
    if expected and expected in reply:
        return True
    recall = dict(turn.get("memory_recall") or {})
    recall_facts = "\n".join(str(item) for item in list(recall.get("facts") or []))
    if not recall_facts:
        return False
    relevant_tokens = [token for token in MEMORY_REPLY_TOKENS if token in recall_facts]
    return bool(relevant_tokens) and any(token in reply for token in relevant_tokens)


def _mapped_count(turn: Mapping[str, Any]) -> int:
    packet = dict((turn.get("surface_context_packet") or {}).get("surface_influence_packet") or {})
    trace = dict(packet.get("organ_trace") or {})
    return int(trace.get("mapped_organ_count") or len(list(trace.get("mapped_organs") or [])) or 0)


def _min_mapped_organ_count(turns: Sequence[Mapping[str, Any]]) -> int:
    counts = [_mapped_count(turn) for turn in turns]
    return min(counts) if counts else 0


def _model_payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump())
    if hasattr(value, "dict"):
        return dict(value.dict())
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _selected_turns(args: argparse.Namespace) -> list[Any]:
    max_turns = int(getattr(args, "max_turns", 4) or 4)
    max_turns = max(1, max_turns)
    base_turns = _default_turns()
    turns: list[Any] = []
    for index in range(max_turns):
        base = base_turns[index % len(base_turns)]
        cycle = index // len(base_turns)
        if cycle <= 0:
            turns.append(base)
            continue
        turns.append(
            replace(
                base,
                case_id=f"{base.case_id}_{cycle + 1:02d}",
                user_message=_cycled_user_message(base.case_id, cycle),
                topic=f"{base.topic}#{cycle + 1}",
            )
        )
    return turns


def _cycled_user_message(case_id: str, cycle: int) -> str:
    variants = {
        "daily_quality_repair": (
            "今天先继续修文本吧，昨天那些句子读起来还是很生硬。",
            "这轮还是看文本质量，别扩模块，先把生硬和不通顺压下去。",
            "我想继续查主链文本，尤其是那些看着硬邦邦的回复。",
            "先跑当前文本链吧，重点还是别让回复读起来生硬。",
        ),
        "emotional_low": (
            "感觉我说了半天也没人听。",
            "我有点烦，像是一直在讲但没人真的听进去。",
            "算了，感觉这事说了也没人放在心上。",
            "我现在有点低落，像是在原地空转。",
        ),
        "pushed_away": (
            "算了别管我。",
            "行了别管我，我自己待会儿。",
            "别问了，先别管我。",
            "我不想说了，你别管。",
        ),
        "explicit_memory_recall": (
            "还记得我之前说主链哪里最容易崩吗？",
            "你还记得我最在意文本链哪里不通顺吗？",
            "前面我一直说输入输出哪里会崩，你记得吗？",
            "我之前反复提到的生硬问题，你还记得是哪块吗？",
        ),
    }
    rows = variants.get(str(case_id), ())
    if not rows:
        return str(case_id)
    return rows[cycle % len(rows)]


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    lines = [
        "# Full Digital Life Loop Validation",
        "",
        "This is a contract validation, not text quality scoring. Surface output is direct generation plus hard gates only.",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- turn_count: `{summary.get('turn_count')}`",
        f"- min_mapped_organ_count: `{summary.get('min_mapped_organ_count')}`",
        f"- private_thought_count: `{summary.get('private_thought_count')}`",
        f"- self_update_count: `{summary.get('self_update_count')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
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
        surface = dict(turn.get("surface") or {})
        packet = dict(turn.get("reply_control_packet") or {})
        memory = dict(turn.get("memory_recall") or {})
        inner = dict(turn.get("inner_world") or {})
        latest = dict(inner.get("latest_private_thought") or {})
        self_after = dict((turn.get("self_model") or {}).get("after") or {})
        bond = dict((turn.get("bond") or {}).get("update") or {})
        organ_meta = dict(turn.get("organ_collection") or {})
        lines.extend(
            [
                f"### {index}. {turn.get('case_id')}",
                "",
                f"- user: {turn.get('user_message')}",
                f"- reply: {turn.get('reply_text')}",
                f"- organ_source: `{organ_meta.get('source')}` mapped=`{_mapped_count(turn)}`",
                f"- surface_emotion: `{surface.get('user_emotion_tag')}` -> `{surface.get('natsume_emotion_tag')}` / `{surface.get('vts_expression_id')}`",
                f"- reply_control: task=`{packet.get('task_mode')}` tone=`{packet.get('emotional_tone')}` distance=`{packet.get('relationship_distance')}` questions=`{packet.get('question_budget')}`",
                f"- packet_facts: {'；'.join(str(item) for item in list(packet.get('facts') or [])) or '无'}",
                f"- packet_style_anchor: {'；'.join(str(item) for item in list(packet.get('style_anchor') or [])) or '无'}",
                f"- memory_facts: {'；'.join(str(item) for item in list(memory.get('facts') or [])) or '无'}",
                f"- private_inner_monologue: {latest.get('private_text') or latest.get('note') or ''}",
                f"- private_focus: `{latest.get('focus')}` source: `{latest.get('source')}` salience: `{latest.get('salience')}`",
                f"- self: agency=`{self_after.get('agency_score')}` continuity=`{self_after.get('self_continuity_score')}` updates=`{self_after.get('self_update_count')}`",
                f"- bond_update: `{bond.get('update_kind')}` status=`{bond.get('status')}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Generated Replies",
            "",
            "```text",
            "\n\n".join(str(item) for item in list(summary.get("generated_replies") or [])),
            "```",
            "",
            "## Breaker Probe",
            "",
            "```json",
            json.dumps(details.get("breaker_probe") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_failure_result(*, out_dir: Path, mode: str, exc: BaseException) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": str(mode or "fixture"),
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "passed": False,
        "reason_code": "FULL_DIGITAL_LIFE_LOOP_VALIDATION_FAILED",
        "error_type": type(exc).__name__,
        "detail": str(exc),
        "output_dir": str(out_dir),
    }
    details = {"failure": dict(summary), "traceback": traceback.format_exc()}
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def _resolve_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "mode", "") or "fixture").strip().lower()
    if mode not in {"fixture", "live"}:
        raise ValueError(f"unsupported validation mode: {mode}")
    return mode


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate 23 organs -> compressed surface packet -> reply -> memory/bond/emotion/inner-self writeback."
    )
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--max-turns", type=int, default=4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_full_digital_life_loop_validation(args))
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
