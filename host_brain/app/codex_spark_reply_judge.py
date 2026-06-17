from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_DEFAULT_MODEL_ID = "gpt-5.3-codex-spark"
_DEFAULT_TIMEOUT_MS = 12_000
_MAX_TEXT_CHARS = 420


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _parse_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


def _enabled() -> bool:
    return _parse_bool(os.environ.get("HOST_CODEX_SPARK_REPLY_JUDGE_ENABLED"), default=False)


def _rewrite_enabled() -> bool:
    return _parse_bool(os.environ.get("HOST_CODEX_SPARK_REPLY_JUDGE_REWRITE_ENABLED"), default=True)


def _judge_mode() -> str:
    return str(os.environ.get("HOST_CODEX_SPARK_REPLY_JUDGE_MODE") or "suspicious").strip().lower()


def _model_id() -> str:
    return str(os.environ.get("HOST_CODEX_SPARK_REPLY_JUDGE_MODEL_ID") or _DEFAULT_MODEL_ID).strip() or _DEFAULT_MODEL_ID


def _timeout_ms() -> int:
    return _parse_int(
        os.environ.get("HOST_CODEX_SPARK_REPLY_JUDGE_TIMEOUT_MS"),
        default=_DEFAULT_TIMEOUT_MS,
        minimum=1_000,
        maximum=60_000,
    )


def _compact_text(value: Any, *, limit: int = _MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return _compact_text(value, limit=160)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value if not isinstance(value, str) else _compact_text(value, limit=500)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:20]:
            out[str(key)] = _jsonable(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth=depth + 1) for item in list(value)[:12]]
    return _compact_text(value, limit=200)


def _audit_db_path() -> Path:
    configured = str(os.environ.get("HOST_AUDIT_DB_PATH") or os.environ.get("AUDIT_DB_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "data" / "audit.sqlite3"


def _current_request_boundary_ms(*, person_id: str, session_id: str, request_id: str) -> int:
    rid = str(request_id or "").strip()
    if not rid:
        return 0
    db_path = _audit_db_path()
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT created_at_ms
                FROM memory_event
                WHERE active = 1
                  AND visibility = 'visible'
                  AND role = 'user'
                  AND person_id = ?
                  AND (? = '' OR session_id = ?)
                  AND json_extract(refs_json, '$.request_id') = ?
                ORDER BY created_at_ms ASC
                LIMIT 1
                """,
                (person_id, session_id, session_id, rid),
            ).fetchone()
            return int(row["created_at_ms"] or 0) if row is not None else 0
    except Exception:
        return 0


def _recent_visible_events(
    *,
    person_id: str,
    session_id: str,
    request_id: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    db_path = _audit_db_path()
    if not db_path.exists():
        return []
    boundary_ms = _current_request_boundary_ms(
        person_id=person_id,
        session_id=session_id,
        request_id=request_id,
    )
    rows: list[dict[str, Any]] = []
    try:
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            boundary_clause = "AND created_at_ms <= ?" if boundary_ms else ""
            query = f"""
                SELECT event_id, session_id, source_kind, event_kind, role, content, content_summary, created_at_ms
                FROM memory_event
                WHERE active = 1
                  AND visibility = 'visible'
                  AND person_id = ?
                  AND (? = '' OR session_id = ?)
                  {boundary_clause}
                ORDER BY created_at_ms DESC
                LIMIT ?
            """
            if boundary_ms:
                params = (person_id, session_id, session_id, int(boundary_ms), max(1, int(limit)))
            else:
                params = (person_id, session_id, session_id, max(1, int(limit)))
            for row in conn.execute(query, params):
                rows.append(
                    {
                        "event_id": str(row["event_id"] or ""),
                        "session_id": str(row["session_id"] or ""),
                        "source_kind": str(row["source_kind"] or ""),
                        "event_kind": str(row["event_kind"] or ""),
                        "role": str(row["role"] or ""),
                        "content": _compact_text(row["content"] or row["content_summary"], limit=260),
                        "created_at_ms": int(row["created_at_ms"] or 0),
                    }
                )
    except Exception:
        return []
    rows.reverse()
    return rows


def _environment_snapshot() -> dict[str, Any]:
    try:
        from host_brain.runtime.environment_state import build_environment_snapshot  # noqa: PLC0415

        snapshot = build_environment_snapshot()
        if isinstance(snapshot, Mapping):
            return _jsonable(snapshot)
    except Exception:
        pass
    return {
        "now_ms": int(time.time() * 1000),
        "local_timezone": os.environ.get("TZ") or "Asia/Shanghai",
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
    return {}


def _looks_suspicious_candidate(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    suspicious_tokens = (
        "semantic=",
        "act=",
        "semantic_intent",
        "pragmatic_frame",
        "trace",
        "reason_code",
        "reply_envelope",
        "narrative",
        "as an ai",
        "ai助手",
        "作为助手",
        "作为ai",
    )
    if any(token in lowered for token in suspicious_tokens):
        return True
    if raw.startswith("{") and raw.endswith("}"):
        return True
    if raw.startswith("```") or "```json" in lowered:
        return True
    if re.search(r"\b[a-z_]{3,}=[a-z0-9_:-]{3,}", lowered):
        return True
    return False


def _build_payload(*, agent_input: Any, candidate_text: str, trace_stage_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    person_id = str(getattr(agent_input, "person_id", "") or "").strip() or "default"
    session_id = str(getattr(agent_input, "session_id", "") or "").strip()
    trace = dict(trace_stage_meta or {})
    selected_trace = {
        key: _jsonable(trace.get(key))
        for key in (
            "life_director",
            "surface_memory_context",
            "memory_context",
            "memory_context_orchestrator",
            "surface_memory_recall_packet",
            "surface_user_preference_pack",
            "codex_backend_status",
        )
        if key in trace
    }
    return {
        "request_id": str(getattr(agent_input, "request_id", "") or "").strip(),
        "person_id": person_id,
        "session_id": session_id,
        "reply_channel": str(getattr(agent_input, "reply_channel", "") or getattr(agent_input, "platform", "") or "").strip(),
        "reply_target": str(getattr(agent_input, "reply_target", "") or getattr(agent_input, "conversation_id", "") or "").strip(),
        "current_user_message": _compact_text(getattr(agent_input, "user_message", ""), limit=700),
        "candidate_reply": _compact_text(candidate_text, limit=900),
        "recent_visible_events": _recent_visible_events(
            person_id=person_id,
            session_id=session_id,
            request_id=str(getattr(agent_input, "request_id", "") or "").strip(),
        ),
        "environment": _environment_snapshot(),
        "trace_context": selected_trace,
    }


def _messages(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是 Natsume 前台聊天回复的快速质量判断与改写模型。"
                "你的职责不是套模板，也不是关键词拦截，而是根据当前用户消息、最近可见对话、环境时间和候选回复，"
                "判断候选回复能不能直接发给用户。"
                "必须重点检查：是否承接当前用户意图；是否和最近可见对话一致；是否把内部意图、semantic、act、JSON、trace、规则名等泄露给用户；"
                "是否跳到旧话题；是否使用错误时间问候；是否编造没有证据的事实；是否像企业客服或 AI 助手。"
                "如果候选回复可用，final_reply 原样或轻微润色。"
                "如果不可用，final_reply 必须重写成一条可以直接发送的自然中文回复。"
                "重写时只能使用输入里的证据；证据不足就自然承认没接准或反问一句，但不要输出内部分析。"
                "如果用户问的是 Natsume 自己的感受、喜好或刚才那句话是什么意思，就直接回答这个问题，"
                "不要顺手变成任务承诺、推荐服务或“我可以继续帮你”的助手式结尾。"
                "语气要短、像熟人聊天，可以有一点别扭和嘴硬，但不能为了人设牺牲上下文承接。"
                "不要输出代码块，不要解释判断过程。"
                "输出 JSON："
                "{\"ok\":true|false,\"action\":\"keep|rewrite\",\"issues\":[\"...\"],\"confidence\":0.0,\"final_reply\":\"...\"}"
            ),
        },
        {"role": "user", "content": json.dumps(dict(payload), ensure_ascii=False)},
    ]


def _judge_sync(payload: Mapping[str, Any], *, request_id: str) -> dict[str, Any]:
    from host_brain.integrations.codex_cli_provider import invoke_codex_cli_text  # noqa: PLC0415

    raw = invoke_codex_cli_text(
        model_id=_model_id(),
        messages=_messages(payload),
        timeout_ms=_timeout_ms(),
        response_format={"type": "json_object"},
        feature_key=f"surface_spark_reply_judge:{request_id or int(time.time() * 1000)}",
    )
    parsed = _extract_json_object(raw)
    if not parsed:
        return {"ok": True, "action": "keep", "issues": ["INVALID_JUDGE_JSON"], "final_reply": ""}
    return parsed


async def judge_surface_reply_with_codex_spark(
    *,
    agent_input: Any,
    candidate_text: str,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    candidate = str(candidate_text or "").strip()
    if not candidate or not _enabled():
        return candidate, {}
    mode = _judge_mode()
    if mode not in {"always", "all"} and not _looks_suspicious_candidate(candidate):
        return candidate, {
            "status": "SKIPPED",
            "model_id": _model_id(),
            "reason_code": "SPARK_REPLY_JUDGE_NOT_SUSPICIOUS",
        }

    started_ms = int(time.time() * 1000)
    payload = _build_payload(agent_input=agent_input, candidate_text=candidate, trace_stage_meta=trace_stage_meta)
    request_id = str(payload.get("request_id") or "").strip()
    try:
        judged = await asyncio.to_thread(_judge_sync, payload, request_id=request_id)
    except Exception as exc:
        return candidate, {
            "status": "DEGRADED",
            "model_id": _model_id(),
            "latency_ms": max(0, int(time.time() * 1000) - started_ms),
            "error": type(exc).__name__,
        }

    final_reply = _compact_text(judged.get("final_reply"), limit=900)
    action = str(judged.get("action") or "").strip().lower()
    ok = bool(judged.get("ok", action != "rewrite"))
    should_rewrite = _rewrite_enabled() and action == "rewrite" and final_reply
    if should_rewrite:
        candidate = final_reply

    meta = {
        "status": "OK",
        "model_id": _model_id(),
        "latency_ms": max(0, int(time.time() * 1000) - started_ms),
        "ok": ok,
        "action": "rewrite" if should_rewrite else "keep",
        "issues": [str(item or "").strip() for item in list(judged.get("issues") or [])[:6] if str(item or "").strip()],
        "confidence": judged.get("confidence"),
    }
    return candidate, meta
