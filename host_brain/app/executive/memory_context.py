"""Memory execution context builder for the executive runtime.

Builds the episodic-writer / semantic-recaller wrappers that are
injected into skill execution context for memory-related skills.
"""

from __future__ import annotations

import logging
from typing import Any

from host_brain.app.executive.text_utils import _normalize_text, _run_awaitable_sync

_logger = logging.getLogger("Natsume.executive_runtime_core")


def _build_memory_exec_context(
    *,
    request_id: str,
    session_id: str,
    intent_id: str,
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    from host_brain.app.dependencies import get_episodic_store, get_semantic_recaller  # noqa: PLC0415

    try:
        episodic_store = get_episodic_store()
    except Exception as exc:  # pragma: no cover - depends on runtime services
        episodic_store = None
        _logger.debug("memory skill episodic_store unavailable: %s", exc)

    try:
        semantic_recaller = get_semantic_recaller()
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
