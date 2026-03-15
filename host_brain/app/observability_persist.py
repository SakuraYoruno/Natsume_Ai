"""可观测性和记忆持久化模块。

从 fastapi_app.py 拆分，负责 chat 请求的可观测性数据和情节记忆写入。
"""

from __future__ import annotations

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

    tag = str((emotion_context or {}).get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
    valence, arousal = map_emotion_tag_to_vad(tag)

    # [选项二] 接入 MemoryGuard：在写入 EpisodicStore 前进行宪法校验
    # 校验条件：对话轮次 ≥ 2 且包含情感/事实/偏好标签
    # 降级策略：校验失败时跳过写入（不抛出异常），记录 reason_code
    try:
        from host_brain.app.dependencies import get_memory_guard  # noqa: PLC0415
        from host_brain.app.memory.memory_guard import MemoryGuardError  # noqa: PLC0415
        from host_brain.app.schemas.memory_types import EpisodicRecord, EvidenceLevel  # noqa: PLC0415
        _guard = get_memory_guard()
        # 从 emotion_context 中提取轮次信息（默认 2，满足最低写入条件）
        _turn_count = int((emotion_context or {}).get("turn_count") or 2)
        # Include NEUTRAL as a valid emotion tag so chat turns always have at
        # least one qualifying tag and pass the MemoryGuard episodic write check.
        _emotion_tags = [tag] if tag else []
        _fact_tags = list((emotion_context or {}).get("fact_tags") or [])
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
    return {
        "status": ("OK" if result.success else "DEGRADED"),
        "reason_code": result.reason_code,
        "memory_id": result.memory_id,
        "detail": result.detail,
        "emotion_tag": tag,
    }
