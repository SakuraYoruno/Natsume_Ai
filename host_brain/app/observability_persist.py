"""可观测性和记忆持久化模块。

从 fastapi_app.py 拆分，负责 chat 请求的可观测性数据和情节记忆写入。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import time
from typing import Any, Mapping

from host_brain.app.emotion_context import map_emotion_tag_to_vad
from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake, _truncate_for_prompt
from host_brain.audit.sqlite_ledger import SQLiteLedger


def build_payload_digest(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except Exception:
        serialized = str(value)
    if not serialized:
        return None
    return hashlib.sha256(serialized.encode("utf-8", errors="ignore")).hexdigest()


def _run_distillation_batch_sync(engine: Any) -> Any:
    coroutine = engine.run_batch()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result(timeout=30)


def persist_chat_observability_rows(
    *,
    ledger: SQLiteLedger,
    request_id: str,
    session_id: str,
    llm_summary: Mapping[str, Any],
    ts_ms: int,
    fallback_reason_code: str | None,
) -> None:
    nodes_raw = llm_summary.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        return

    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    now_value = int(time.time() * 1000)

    for idx, node in enumerate(nodes_raw):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("node_id") or f"node_{idx}")
        status = str(node.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        reason_code = str(node.get("reason_code") or "").strip() or None
        provider = str(node.get("provider") or "").strip() or None
        model_id = str(node.get("model_id") or "").strip() or None
        node_input = dict(node.get("input") or {}) if isinstance(node.get("input"), dict) else node.get("input")
        node_output = dict(node.get("output") or {}) if isinstance(node.get("output"), dict) else node.get("output")
        row_ts = int(now_value + idx)

        ledger.log_llm_node_call(
            {
                "request_id": request_id,
                "node_id": node_id,
                "provider": provider or "",
                "model_id": model_id or "",
                "status": status,
                "reason_code": reason_code,
                "latency_ms": node.get("latency_ms"),
                "input_digest": build_payload_digest(node_input),
                "output_digest": build_payload_digest(node_output),
                "ts_ms": row_ts,
            }
        )

        graph_nodes.append(
            {
                "node_id": node_id,
                "node_type": "llm_node",
                "display_name": node_id,
                "provider": provider,
                "model_id": model_id,
                "status": status,
                "latency_ms": node.get("latency_ms"),
                "input_data": node_input,
                "output_data": node_output,
                "ts_ms": row_ts,
                "error": (reason_code if status != "OK" else None),
            }
        )

    for idx in range(len(graph_nodes) - 1):
        graph_edges.append(
            {
                "source": str(graph_nodes[idx].get("node_id") or ""),
                "target": str(graph_nodes[idx + 1].get("node_id") or ""),
            }
        )

    trace_status = str(llm_summary.get("status") or "OK").strip().upper() or "OK"
    trace_reason = (
        str(llm_summary.get("reason_code") or "").strip()
        or str(fallback_reason_code or "").strip()
        or None
    )
    total_latency_ms = max(0, int(time.time() * 1000) - int(ts_ms))
    ledger.log_graph_run_trace(
        {
            "request_id": request_id,
            "session_id": session_id,
            "total_latency_ms": total_latency_ms,
            "status": trace_status,
            "reason_code": trace_reason,
            "node_count": len(graph_nodes),
            "nodes_json": graph_nodes,
            "edges_json": graph_edges,
            "ts_ms": int(time.time() * 1000),
        }
    )


def build_chat_episodic_text(*, user_message: str, assistant_reply: str, session_id: str) -> str:
    user_text = _truncate_for_prompt(
        repair_utf8_latin1_mojibake(str(user_message or "").strip()),
        limit=800,
    )
    assistant_text = _truncate_for_prompt(
        repair_utf8_latin1_mojibake(str(assistant_reply or "").strip()),
        limit=1200,
    )
    return (
        f"[session={session_id}]\n"
        f"User: {user_text}\n"
        f"Natsume: {assistant_text}"
    ).strip()


def persist_chat_episodic_memory(
    *,
    request_id: str,
    intent_id: str | None,
    person_id: str | None,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    emotion_context: Mapping[str, Any] | None,
    ts_ms: int,
    episodic_store: Any,
) -> dict[str, Any]:
    text = build_chat_episodic_text(
        user_message=user_message,
        assistant_reply=assistant_reply,
        session_id=session_id,
    )
    if not str(text).strip():
        return {
            "status": "SKIPPED",
            "reason_code": "EPISODIC_EMPTY_TEXT",
            "memory_id": None,
            "detail": "chat episodic text is empty",
        }

    raw_tag = (emotion_context or {}).get("emotion_tag")
    tag = str(raw_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    valence, arousal, _dominance = map_emotion_tag_to_vad(tag)

    # 方向 A：记录情感来源（前端传入 vs Phase 6 推理），用于审计
    _emotion_source = str((emotion_context or {}).get("emotion_source") or "FRONTEND").strip()
    _inferred_user_emotion = str((emotion_context or {}).get("inferred_user_emotion") or "").strip()
    _inferred_confidence = 0.0
    try:
        _inferred_confidence = float((emotion_context or {}).get("inferred_confidence") or 0.0)
    except (TypeError, ValueError):
        pass

    from host_brain.app.dependencies import (  # noqa: PLC0415
        get_consolidation_trigger,
        get_distillation_engine,
        get_episodic_memory_graph,
        get_memory_worth_evaluator,
        get_meta_learning_loop,
        get_predictive_state_engine,
    )

    _turn_count = max(1, int((emotion_context or {}).get("turn_count") or 1))
    _predictive_state = get_predictive_state_engine()
    _predictive_state.record_emotion(tag.lower(), ts_ms=ts_ms)
    _memory_worth = get_memory_worth_evaluator().evaluate(
        user_message,
        assistant_reply,
        emotion_tag=tag,
        turn_count=_turn_count,
    )
    get_meta_learning_loop().record(
        "memory_worth",
        success=(_memory_worth.decision != "skip"),
        skipped=(_memory_worth.decision == "skip"),
        latency_ms=0.0,
        custom={"worth_score": float(_memory_worth.worth_score)},
    )
    if _memory_worth.decision == "skip":
        return {
            "status": "SKIPPED",
            "reason_code": "MEMORY_WORTH_SKIP",
            "memory_id": None,
            "detail": _memory_worth.reason,
            "emotion_tag": tag,
            "memory_worth": _memory_worth.as_dict(),
        }
    if _memory_worth.decision == "write_light":
        return {
            "status": "SKIPPED",
            "reason_code": "MEMORY_WORTH_LIGHT_ONLY",
            "memory_id": None,
            "detail": _memory_worth.reason,
            "emotion_tag": tag,
            "memory_worth": _memory_worth.as_dict(),
        }

    # [选项二] 接入 MemoryGuard：在写入 EpisodicStore 前进行宪法校验
    # 校验条件：对话轮次 ≥ 2 且包含情感/事实/偏好标签
    # 降级策略：校验失败时跳过写入（不抛出异常），记录 reason_code
    try:
        from host_brain.app.dependencies import get_memory_guard  # noqa: PLC0415
        from host_brain.app.memory.memory_guard import MemoryGuardError  # noqa: PLC0415
        from host_brain.app.schemas.memory_types import EpisodicRecord, EvidenceLevel  # noqa: PLC0415
        _guard = get_memory_guard()
        # 从 emotion_context 中提取轮次信息（默认 2，满足最低写入条件）
        _turn_count = max(2, int((emotion_context or {}).get("turn_count") or 2))
        # 保证至少有一个 qualifying tag（含 NEUTRAL），避免 EPISODIC_NO_QUALIFYING_TAGS 阻断封测
        _emotion_tags = [tag] if tag else ["NEUTRAL"]
        _fact_tags = list((emotion_context or {}).get("fact_tags") or []) + list(_memory_worth.tags)
        _pref_tags = list((emotion_context or {}).get("preference_tags") or [])
        _record = EpisodicRecord(
            session_id=session_id,
            content=text,
            turn_count=_turn_count,
            emotion_tags=_emotion_tags,
            fact_tags=_fact_tags,
            preference_tags=_pref_tags,
            evidence_level=EvidenceLevel.CANDIDATE,
        )
        _guard.check_episodic_write(_record)
        # 聚合情绪标签（session 粒度）
        if _emotion_tags:
            _guard.aggregate_emotion_tags(session_id, _emotion_tags)
    except ImportError:
        pass  # MemoryGuard 模块不可用，降级跳过校验
    except Exception as _guard_exc:
        _guard_reason = str(getattr(_guard_exc, "reason", None) or type(_guard_exc).__name__)
        return {
            "status": "SKIPPED",
            "reason_code": f"MEMORY_GUARD_REJECTED:{_guard_reason}",
            "memory_id": None,
            "detail": str(_guard_exc),
        }

    evidence_refs = [
        f"chat://request/{request_id}",
        f"chat://person/{str(person_id or '').strip() or 'default'}",
        f"chat://session/{session_id}",
        f"chat://intent/{str(intent_id or request_id)}",
    ]
    result = episodic_store.write_memory(
        text=text,
        evidence_refs=evidence_refs,
        request_id=request_id,
        intent_id=str(intent_id or request_id),
        decay_profile_id="SLOW",
        visibility="internal",
        ts_ms=ts_ms,
        emotion_tag=tag,
        emotion_valence=valence,
        emotion_arousal=arousal,
    )
    memory_graph_summary: dict[str, Any] = {"status": "SKIPPED", "reason_code": "WRITE_FAILED"}
    consolidation_summary: dict[str, Any] = {"status": "SKIPPED", "reason_code": "WRITE_FAILED"}
    if result.success and result.memory_id:
        graph = get_episodic_memory_graph()
        memory_node = graph.add_memory(
            memory_id=str(result.memory_id),
            text=text,
            ts_ms=ts_ms,
        )
        memory_walk = graph.walk(str(result.memory_id), max_steps=3)
        memory_graph_summary = {
            "status": "OK",
            "node": memory_node.as_dict(),
            "walk": memory_walk.as_dict(),
        }
        get_meta_learning_loop().record(
            "episodic_memory_graph",
            success=True,
            latency_ms=0.0,
            custom={"chain_length": float(len(memory_walk.chain))},
        )

        consolidation_trigger = get_consolidation_trigger()
        consolidation_trigger.on_memory_written(
            tags=list(_memory_worth.tags),
            emotion_tag=tag,
            text_preview=user_message,
        )
        consolidation_decision = consolidation_trigger.check()
        consolidation_summary = {
            "status": "TRIGGERED" if consolidation_decision.should_trigger else "SKIPPED",
            **consolidation_decision.as_dict(),
        }
        if consolidation_decision.should_trigger:
            try:
                report = _run_distillation_batch_sync(get_distillation_engine())
                consolidation_summary["distillation"] = {
                    "status": getattr(report, "status", "OK"),
                    "processed": getattr(report, "processed_count", None),
                }
            except Exception as exc:
                consolidation_summary["status"] = "DEGRADED"
                consolidation_summary["reason_code"] = "DISTILLATION_TRIGGER_FAILED"
                consolidation_summary["detail"] = str(exc)
        get_meta_learning_loop().record(
            "consolidation_trigger",
            success=not consolidation_decision.should_trigger or consolidation_summary.get("status") == "TRIGGERED",
            skipped=not consolidation_decision.should_trigger,
            latency_ms=0.0,
            custom={"memory_count_24h": float(consolidation_decision.memory_count_24h)},
        )
    return {
        "status": ("OK" if result.success else "DEGRADED"),
        "reason_code": result.reason_code,
        "memory_id": result.memory_id,
        "detail": result.detail,
        "emotion_tag": tag,
        "memory_worth": _memory_worth.as_dict(),
        "memory_graph": memory_graph_summary,
        "consolidation": consolidation_summary,
        # 方向 A：情感记忆闭环审计字段
        "emotion_source": _emotion_source,
        "inferred_user_emotion": _inferred_user_emotion or None,
        "inferred_confidence": round(_inferred_confidence, 3) if _inferred_confidence > 0 else None,
    }
