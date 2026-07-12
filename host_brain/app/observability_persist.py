"""可观测性和记忆持久化模块。

负责 chat 请求可观测性，以及尚未迁移的后台/动作记忆兼容写入。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Mapping, Sequence

from host_brain.app.prompt_builder import repair_utf8_latin1_mojibake, _truncate_for_prompt
from host_brain.audit.sqlite_ledger import SQLiteLedger


_DEFAULT_EPISODIC_STORE = object()


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
    _ = assistant_reply
    return f"[session={session_id}]\nUser: {user_text}".strip()


_SCHEDULER_MEMORY_STATUS_DEFAULTS = {
    "ERROR",
    "FAILED",
    "DEGRADED",
    "SUSPENDED",
    "DEFERRED",
    "HEARTBEAT_ALERT",
    "EDGE_PROBE_ALERT",
}
_SCHEDULER_MEMORY_OK_TASK_DEFAULTS = {
    "daily_recommendation",
    "diary_generate",
    "diary_writer_daily",
    "dream_generator_sleep",
    "lightnovel_recommender_daily",
    "long_term_memory_distillation",
    "distillation_engine_periodic",
    "memory_distillation",
    "emergent_growth_weekly",
    "evolution_cycle_daily",
}


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_csv_set(name: str, *, default: set[str]) -> set[str]:
    raw = os.environ.get(name)
    if raw is None:
        return {str(item).strip().lower() for item in default if str(item).strip()}
    values = {
        str(item).strip().lower()
        for item in str(raw).split(",")
        if str(item).strip()
    }
    return values


def _compact_memory_text(value: Any, *, limit: int = 360) -> str:
    return _truncate_for_prompt(
        repair_utf8_latin1_mojibake(str(value or "").strip()),
        limit=max(1, int(limit)),
    ).strip()


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def should_persist_background_event_memory(
    *,
    event_source: str,
    task_id: str | None = None,
    status: str | None = None,
    detail: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    source = str(event_source or "").strip().lower()
    status_token = str(status or "").strip().upper()
    if source == "codex_production":
        if not _env_bool("HOST_CODEX_PRODUCTION_MEMORY_ENABLED", default=True):
            return False
        return bool(str(detail or "").strip()) and status_token in {
            "COMPLETED",
            "FAILED",
            "DEGRADED",
            "BLOCKED",
            "EXECUTED",
        }

    if not _env_bool("HOST_NON_CHAT_MEMORY_ENABLED", default=False):
        return False

    if source in {"inner_world", "inner_stream", "inner_activity"}:
        if not _env_bool("HOST_INNER_STREAM_MEMORY_ENABLED", default=True):
            return False
        note = str(detail or (metadata or {}).get("note") or "").strip()
        return bool(note)

    if source == "scheduler":
        task_key = str(task_id or "").strip().lower()
        status_allowlist = {
            item.upper()
            for item in _env_csv_set(
                "HOST_SCHEDULER_MEMORY_STATUS_ALLOWLIST",
                default={item.lower() for item in _SCHEDULER_MEMORY_STATUS_DEFAULTS},
            )
        }
        if status_token in status_allowlist:
            return True
        ok_task_allowlist = _env_csv_set(
            "HOST_SCHEDULER_MEMORY_OK_TASK_ALLOWLIST",
            default=_SCHEDULER_MEMORY_OK_TASK_DEFAULTS,
        )
        return status_token == "OK" and task_key in ok_task_allowlist

    return False


def build_background_event_memory_text(
    *,
    event_source: str,
    task_id: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    detail: str | None = None,
    person_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    source = str(event_source or "").strip().lower()
    meta = dict(metadata or {})
    status_text = str(status or "UNKNOWN").strip().upper() or "UNKNOWN"
    reason_text = _compact_memory_text(reason_code, limit=120)
    detail_text = _compact_memory_text(detail or meta.get("note") or meta.get("summary"), limit=360)

    if source in {"inner_world", "inner_stream", "inner_activity"}:
        note = detail_text
        if not note:
            return ""
        focus = _compact_memory_text(meta.get("focus"), limit=80)
        source_label = _compact_memory_text(meta.get("source") or source, limit=80)
        person_text = _compact_memory_text(person_id or meta.get("user_id") or "persona", limit=80)
        parts = [f"内心流记忆：{person_text}"]
        if focus:
            parts.append(f"焦点={focus}")
        parts.append(f"内容「{note}」")
        if source_label:
            parts.append(f"来源={source_label}")
        return "；".join(parts) + "。"

    if source == "scheduler":
        task_text = _compact_memory_text(task_id or "unknown_task", limit=120)
        parts = [f"后台任务记忆：{task_text} 状态={status_text}"]
        if reason_text:
            parts.append(f"原因={reason_text}")
        if detail_text:
            parts.append(f"细节={detail_text}")
        return "；".join(parts) + "。"

    return detail_text


def persist_background_event_memory(
    *,
    event_source: str,
    event_id: str | None = None,
    request_id: str | None = None,
    intent_id: str | None = None,
    person_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    detail: str | None = None,
    ts_ms: int | None = None,
    episodic_store: Any = _DEFAULT_EPISODIC_STORE,
    life_memory_store: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata or {})
    text = build_background_event_memory_text(
        event_source=event_source,
        task_id=task_id,
        status=status,
        reason_code=reason_code,
        detail=detail,
        person_id=person_id,
        metadata=meta,
    )
    source = str(event_source or "background").strip().lower() or "background"
    event_key = str(event_id or request_id or meta.get("event_id") or "").strip()
    ts_value = int(ts_ms if ts_ms is not None else time.time() * 1000)
    refs: list[str] = [f"source:{source}"]
    if event_key:
        refs.append(f"{source}://event/{event_key}")
    elif task_id:
        refs.append(f"{source}://event/{task_id}/{ts_value}")
    if task_id:
        refs.append(f"scheduler://task/{str(task_id).strip()}")
    if person_id:
        refs.append(f"chat://person/{str(person_id).strip()}")
    if session_id:
        refs.append(f"chat://session/{str(session_id).strip()}")

    life_result: dict[str, Any] = {"status": "SKIPPED", "reason_code": "LIFE_MEMORY_NOT_ATTEMPTED"}
    life_store = life_memory_store
    if life_store is None:
        try:
            from host_brain.app.dependencies import get_life_memory_store  # noqa: PLC0415

            life_store = get_life_memory_store()
        except Exception as exc:
            life_result = {
                "status": "SKIPPED",
                "reason_code": "LIFE_MEMORY_STORE_UNAVAILABLE",
                "episode_id": None,
                "detail": str(exc)[:160],
            }
    if life_store is not None:
        try:
            life_result = life_store.write_background_episode(
                event_source=source,
                event_id=event_key or None,
                request_id=request_id,
                intent_id=intent_id,
                person_id=person_id,
                session_id=session_id,
                task_id=task_id,
                status=status,
                reason_code=reason_code,
                detail=detail or text or reason_code or status,
                ts_ms=ts_value,
                metadata={
                    **meta,
                    "legacy_memory_text": text,
                    "evidence_refs": refs,
                },
            )
        except Exception as exc:
            life_result = {
                "status": "FAILED",
                "reason_code": "LIFE_BACKGROUND_MEMORY_WRITE_FAILED",
                "episode_id": None,
                "detail": str(exc)[:160],
            }

    active_memory_allowed = should_persist_background_event_memory(
        event_source=event_source,
        task_id=task_id,
        status=status,
        detail=detail,
        metadata=meta,
    )
    if not active_memory_allowed:
        return {
            "status": "OK" if life_result.get("status") == "OK" else "SKIPPED",
            "reason_code": "NON_CHAT_MEMORY_NOT_SALIENT",
            "memory_id": None,
            "life_memory": life_result,
        }

    if not text:
        return {
            "status": "OK" if life_result.get("status") == "OK" else "SKIPPED",
            "reason_code": "NON_CHAT_MEMORY_EMPTY_TEXT",
            "memory_id": None,
            "life_memory": life_result,
        }

    store = episodic_store
    if store is _DEFAULT_EPISODIC_STORE:
        try:
            from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

            store = get_episodic_store()
        except Exception as exc:
            return {
                "status": "OK" if life_result.get("status") == "OK" else "SKIPPED",
                "reason_code": "EPISODIC_STORE_UNAVAILABLE",
                "memory_id": None,
                "life_memory": life_result,
                "detail": str(exc)[:160],
            }
    if store is None:
        return {
            "status": "OK" if life_result.get("status") == "OK" else "SKIPPED",
            "reason_code": "EPISODIC_STORE_DISABLED",
            "memory_id": None,
            "life_memory": life_result,
        }

    try:
        result = store.write_memory(
            text=text,
            evidence_refs=refs,
            request_id=str(request_id or event_key or f"{source}:{ts_value}"),
            intent_id=str(intent_id or task_id or source),
            decay_profile_id="SLOW",
            visibility="internal",
            ts_ms=ts_value,
            emotion_tag=str(meta.get("emotion_tag") or "NEUTRAL"),
            emotion_valence=_safe_float(meta.get("emotion_valence"), default=0.0),
            emotion_arousal=_safe_float(meta.get("emotion_arousal"), default=0.3),
        )
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason_code": "NON_CHAT_MEMORY_WRITE_FAILED",
            "memory_id": None,
            "life_memory": life_result,
            "detail": str(exc)[:160],
        }
    return {
        "status": "OK" if getattr(result, "success", False) else "SKIPPED",
        "reason_code": getattr(result, "reason_code", None),
        "memory_id": getattr(result, "memory_id", None),
        "detail": getattr(result, "detail", ""),
        "life_memory": life_result,
    }


def build_life_action_event_memory_text(
    *,
    actor_id: str | None = None,
    action_kind: str | None = None,
    action_summary: str | None = None,
    status: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    meta = dict(metadata or {})
    actor = _compact_memory_text(actor_id or meta.get("actor_id") or "natsume", limit=80)
    kind = _compact_memory_text(action_kind or meta.get("action_kind") or "action", limit=80)
    status_text = _compact_memory_text(status or meta.get("status") or "DONE", limit=40).upper()
    summary = _compact_memory_text(action_summary or meta.get("summary") or meta.get("detail"), limit=640)
    if not summary:
        return ""
    return f"行动记忆：{actor} 执行了 {kind}，状态={status_text}；{summary}"


_GATEWAY_SESSION_PREFIXES = (
    "qq_",
    "wechat_",
    "wx_",
    "wecom_",
    "feishu_",
)


def _resolve_life_action_person_id(
    *,
    person_id: str | None,
    session_id: str | None,
    metadata: Mapping[str, Any] | None,
) -> str | None:
    explicit = str(person_id or "").strip()
    if explicit:
        return explicit

    meta = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
    raw_extra = meta.get("raw_extra")
    extra = dict(raw_extra) if isinstance(raw_extra, Mapping) else {}
    extra_person = str(extra.get("person_id") or "").strip()
    if extra_person:
        return extra_person

    platform = str(meta.get("platform") or extra.get("platform") or "").strip().lower()
    conversation_id = str(meta.get("conversation_id") or extra.get("conversation_id") or "").strip()
    normalized_session = str(session_id or meta.get("session_id") or extra.get("session_id") or "").strip()
    from_id = str(
        meta.get("from_id")
        or meta.get("sender_id")
        or meta.get("user_id")
        or extra.get("from_id")
        or extra.get("sender_id")
        or extra.get("user_id")
        or ""
    ).strip()
    if not conversation_id and normalized_session.startswith(_GATEWAY_SESSION_PREFIXES):
        conversation_id = normalized_session
    if not platform:
        if conversation_id.startswith("qq_") or normalized_session.startswith("qq_"):
            platform = "qq"
        elif conversation_id.startswith(("wechat_", "wx_")) or normalized_session.startswith(("wechat_", "wx_")):
            platform = "wechat"
        elif conversation_id.startswith("wecom_") or normalized_session.startswith("wecom_"):
            platform = "wecom"
        elif conversation_id.startswith("feishu_") or normalized_session.startswith("feishu_"):
            platform = "feishu"

    has_gateway_context = bool(platform or conversation_id.startswith(_GATEWAY_SESSION_PREFIXES))
    if not has_gateway_context:
        return None

    try:
        from host_brain.bond.user_identity import resolve_person_id  # noqa: PLC0415

        return resolve_person_id(
            None,
            session_id=normalized_session or None,
            platform=platform or None,
            from_id=from_id or None,
            conversation_id=conversation_id or None,
        )
    except Exception:
        return None


def persist_life_action_event_memory(
    *,
    event_source: str,
    action_kind: str,
    action_id: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
    intent_id: str | None = None,
    person_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    action_summary: str | None = None,
    ts_ms: int | None = None,
    evidence_refs: Sequence[str] | None = None,
    episodic_store: Any = _DEFAULT_EPISODIC_STORE,
    life_memory_store: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not _env_bool("HOST_LIFE_ACTION_EVENT_MEMORY_ENABLED", default=True):
        return {
            "status": "SKIPPED",
            "reason_code": "LIFE_ACTION_EVENT_MEMORY_DISABLED",
            "memory_id": None,
        }

    meta = dict(metadata or {})
    resolved_person_id = _resolve_life_action_person_id(
        person_id=person_id,
        session_id=session_id,
        metadata=meta,
    )
    if resolved_person_id and not str(person_id or "").strip():
        meta.setdefault("resolved_person_id_source", "gateway_context")
    person_id = resolved_person_id
    source = str(event_source or "action").strip().lower() or "action"
    kind = str(action_kind or meta.get("action_kind") or "action").strip() or "action"
    event_key = str(action_id or request_id or meta.get("event_id") or f"{source}:{kind}:{int(time.time() * 1000)}").strip()
    ts_value = int(ts_ms if ts_ms is not None else time.time() * 1000)
    refs: list[str] = [f"source:{source}", f"{source}://event/{event_key}", f"action://event/{event_key}"]
    for ref in list(evidence_refs or []):
        cleaned = str(ref or "").strip()
        if cleaned and cleaned not in refs:
            refs.append(cleaned)
    if person_id:
        refs.append(f"chat://person/{str(person_id).strip()}")
    if session_id:
        refs.append(f"chat://session/{str(session_id).strip()}")

    text = build_life_action_event_memory_text(
        actor_id=actor_id,
        action_kind=kind,
        action_summary=action_summary,
        status=status,
        metadata=meta,
    )
    if not text:
        return {
            "status": "SKIPPED",
            "reason_code": "LIFE_ACTION_EVENT_MEMORY_EMPTY_TEXT",
            "memory_id": None,
        }

    life_result: dict[str, Any] = {"status": "SKIPPED", "reason_code": "LIFE_MEMORY_NOT_ATTEMPTED"}
    life_store = life_memory_store
    if life_store is None:
        try:
            from host_brain.app.dependencies import get_life_memory_store  # noqa: PLC0415

            life_store = get_life_memory_store()
        except Exception as exc:
            life_result = {
                "status": "SKIPPED",
                "reason_code": "LIFE_MEMORY_STORE_UNAVAILABLE",
                "episode_id": None,
                "detail": str(exc)[:160],
            }
    if life_store is not None:
        try:
            life_result = life_store.write_background_episode(
                event_source=source,
                event_id=event_key,
                request_id=request_id,
                intent_id=intent_id,
                person_id=person_id,
                session_id=session_id,
                task_id=kind,
                status=status or "DONE",
                reason_code=str(meta.get("reason_code") or "").strip() or None,
                detail=text,
                ts_ms=ts_value,
                metadata={
                    **meta,
                    "event_kind": "life_action_event",
                    "action_kind": kind,
                    "actor_id": actor_id or "natsume",
                    "action_summary": action_summary,
                    "legacy_memory_text": text,
                    "evidence_refs": refs,
                },
            )
        except Exception as exc:
            life_result = {
                "status": "FAILED",
                "reason_code": "LIFE_ACTION_MEMORY_WRITE_FAILED",
                "episode_id": None,
                "detail": str(exc)[:160],
            }

    store = episodic_store
    if store is _DEFAULT_EPISODIC_STORE:
        try:
            from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

            store = get_episodic_store()
        except Exception as exc:
            return {
                "status": "OK" if life_result.get("status") == "OK" else "SKIPPED",
                "reason_code": "EPISODIC_STORE_UNAVAILABLE",
                "memory_id": None,
                "life_memory": life_result,
                "detail": str(exc)[:160],
            }
    if store is None:
        return {
            "status": "OK" if life_result.get("status") == "OK" else "SKIPPED",
            "reason_code": "EPISODIC_STORE_DISABLED",
            "memory_id": None,
            "life_memory": life_result,
        }
    try:
        result = store.write_memory(
            text=text,
            evidence_refs=refs,
            request_id=str(request_id or event_key),
            intent_id=str(intent_id or kind),
            decay_profile_id="SLOW",
            visibility="internal",
            ts_ms=ts_value,
            emotion_tag=str(meta.get("emotion_tag") or "NEUTRAL"),
            emotion_valence=_safe_float(meta.get("emotion_valence"), default=0.0),
            emotion_arousal=_safe_float(meta.get("emotion_arousal"), default=0.3),
        )
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason_code": "LIFE_ACTION_EVENT_MEMORY_WRITE_FAILED",
            "memory_id": None,
            "life_memory": life_result,
            "detail": str(exc)[:160],
        }
    return {
        "status": "OK" if getattr(result, "success", False) else "SKIPPED",
        "reason_code": getattr(result, "reason_code", None),
        "memory_id": getattr(result, "memory_id", None),
        "detail": getattr(result, "detail", ""),
        "life_memory": life_result,
    }


def persist_owner_directive_memory(
    *,
    request_id: str,
    directive_text: str,
    person_id: str | None = "owner",
    session_id: str | None = "codex_operator",
    directive_kind: str | None = None,
    applied_result: str | None = None,
    ts_ms: int | None = None,
    life_memory_store: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a real owner/operator directive into LifeMemory."""

    safe_directive = _scrub_owner_directive_text(directive_text)
    safe_result = _scrub_owner_directive_text(applied_result)
    if not safe_directive:
        return {
            "status": "SKIPPED",
            "reason_code": "OWNER_DIRECTIVE_EMPTY_TEXT",
            "episode_id": None,
        }
    store = life_memory_store
    if store is None:
        try:
            from host_brain.app.dependencies import get_life_memory_store  # noqa: PLC0415

            store = get_life_memory_store()
        except Exception as exc:
            return {
                "status": "SKIPPED",
                "reason_code": "LIFE_MEMORY_STORE_UNAVAILABLE",
                "episode_id": None,
                "detail": str(exc)[:160],
            }
    try:
        return store.write_owner_directive_episode(
            request_id=request_id,
            directive_text=safe_directive,
            person_id=person_id,
            session_id=session_id,
            directive_kind=directive_kind,
            applied_result=safe_result,
            ts_ms=ts_ms,
            metadata={
                **dict(metadata or {}),
                "secret_redaction_applied": safe_directive != str(directive_text or "").strip()
                or safe_result != str(applied_result or "").strip(),
            },
        )
    except Exception as exc:
        return {
            "status": "FAILED",
            "reason_code": "OWNER_DIRECTIVE_MEMORY_WRITE_FAILED",
            "episode_id": None,
            "detail": str(exc)[:160],
        }


def _scrub_owner_directive_text(value: Any) -> str:
    text = _compact_memory_text(value, limit=2000)
    if not text:
        return ""
    patterns = [
        r"(?i)\b(api[_-]?key|secret|token|cookie|authorization|password)\s*[:=]\s*([^\s,;，；]+)",
        r"(密码|密钥|令牌|验证码|cookie)\s*[:=：]\s*([^\s,;，；]+)",
    ]
    scrubbed = text
    for pattern in patterns:
        scrubbed = re.sub(pattern, lambda match: f"{match.group(1)}=[REDACTED]", scrubbed)
    scrubbed = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "sk-[REDACTED]", scrubbed)
    return scrubbed.strip()
