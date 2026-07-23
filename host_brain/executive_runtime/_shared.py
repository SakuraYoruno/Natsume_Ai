"""Skill-first executive runtime core.

This module owns the canonical execution path behind:
chat_handler -> cortex_orchestrator -> executive_router -> executive_runtime.

The runtime only routes to skill families / executors. Legacy dispatch/top-level
entrypoints are intentionally not reintroduced here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Mapping

from host_brain.agent_runtime.skill_ids import AGENT_RUN_MODE_BY_SKILL_ID, is_agent_runtime_skill
from host_brain.skills.executor_registry import (
    bind_validated_skill_authority,
    dispatch_executor_payload,
)


_logger = logging.getLogger("Natsume.executive_runtime_core")

_RECENT_TURN_PATTERNS = (
    "刚刚",
    "上句",
    "上一句",
    "上一条",
    "刚才说",
    "我刚才说",
    "你刚才说",
    "我们刚才",
)

_TASK_PROGRESS_PATTERNS = (
    "进度",
    "做到哪",
    "做完了吗",
    "还在跑",
    "执行到哪",
    "任务状态",
    "现在怎么样",
    "还没好",
)

_SKILL_EXEC_FAIL_STATUS = {"failed", "rejected", "degraded", "error"}
_SKILL_EXEC_FAIL_CODES = {
    "SCHED_NO_CONTENT",
    "SKILL_EXECUTION_FAILED",
    "SKILL_EXECUTION_ERROR",
    "MEMORY_WRITER_UNAVAILABLE",
    "MEMORY_STORE_UNAVAILABLE",
}
_SKILL_EXEC_NO_EFFECT_MARKERS = (
    "no memory writer available",
    "no memory store available",
    "no memory backend available",
    "skipping diary",
    "no conversations on",
)


@dataclass(frozen=True)
class _ExecutionOutcome:
    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict[str, Any]


def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _run_awaitable_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result_box["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error_box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, name="memory-skill-awaitable", daemon=True)
    thread.start()
    done.wait()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _build_memory_exec_context(
    *,
    ports: Any,
    request_id: str,
    session_id: str,
    intent_id: str,
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    try:
        episodic_store = ports.get_episodic_store()
    except Exception as exc:  # pragma: no cover - depends on runtime services
        episodic_store = None
        _logger.debug("memory skill episodic_store unavailable: %s", exc)

    try:
        semantic_recaller = ports.get_semantic_recaller()
    except Exception as exc:  # pragma: no cover - depends on runtime services
        semantic_recaller = None
        _logger.debug("memory skill semantic_recaller unavailable: %s", exc)

    if episodic_store is None and semantic_recaller is None:
        return {}

    default_emotion_tag = _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL"

    def _episodic_writer(
        *,
        content: str,
        tags: list[str] | None = None,
        importance: float | None = None,
        source: str | None = None,
        emotion_tag: str | None = None,
        session_id: str | None = None,
    ) -> str:
        _ = importance
        if episodic_store is None:
            raise RuntimeError("episodic store unavailable")
        evidence_refs = [f"request:{request_id}"]
        session_ref = str(session_id or "").strip()
        if session_ref:
            evidence_refs.append(f"session:{session_ref}")
        source_ref = str(source or "").strip()
        if source_ref:
            evidence_refs.append(f"source:{source_ref}")
        for tag in list(tags or []):
            tag_text = str(tag or "").strip()
            if tag_text:
                evidence_refs.append(f"tag:{tag_text}")
        result = episodic_store.write_memory(
            text=str(content or "").strip(),
            evidence_refs=evidence_refs,
            request_id=request_id,
            intent_id=intent_id,
            emotion_tag=str(emotion_tag or default_emotion_tag).strip().upper() or default_emotion_tag,
        )
        if not bool(result.success):
            raise RuntimeError(str(result.reason_code or result.detail or "memory write failed"))
        return str(result.memory_id or "")

    def _semantic_recaller_wrapper(
        *,
        query: str,
        top_k: int = 5,
        threshold: float = 0.6,
        tags: list[str] | None = None,
        session_id: str | None = None,
        search_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = tags
        if semantic_recaller is None:
            return []
        filter_metadata: dict[str, Any] = {}
        session_ref = str(session_id or "").strip()
        if session_ref:
            filter_metadata["session_id"] = session_ref
        if str(search_scope or "").strip().lower() == "long_term":
            filter_metadata["memory_layer"] = "long_term"
        recall_result = _run_awaitable_sync(
            semantic_recaller.recall(
                str(query or ""),
                top_k=int(top_k or 5),
                threshold=float(threshold) if threshold is not None else None,
                filter_metadata=filter_metadata or None,
            )
        )
        hits = list(getattr(recall_result, "hits", []) or [])
        normalized_hits: list[dict[str, Any]] = []
        for hit in hits:
            normalized_hits.append(
                {
                    "doc_id": str(getattr(hit, "doc_id", "") or ""),
                    "content": str(getattr(hit, "content", "") or ""),
                    "score": float(getattr(hit, "score", 0.0) or 0.0),
                    "metadata": dict(getattr(hit, "metadata", {}) or {}),
                    "source": str(getattr(hit, "source", "") or ""),
                }
            )
        return normalized_hits

    return {
        "episodic_store": episodic_store,
        "episodic_writer": _episodic_writer,
        "semantic_recaller": _semantic_recaller_wrapper,
    }


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


__all__ = [name for name in globals() if not name.startswith("__")]
