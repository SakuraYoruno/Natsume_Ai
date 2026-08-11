"""Pure support code for the governed Skill node executor.

The helpers in this module carry no routing, admission, token, planner, or
delivery authority.  They only normalize worker results and compose explicit
memory ports already injected by the app-local runtime owner.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import threading
from typing import Any


SKILL_EXEC_FAIL_STATUS = frozenset(
    {"failed", "rejected", "degraded", "error"}
)
SKILL_EXEC_FAIL_CODES = frozenset(
    {
        "SCHED_NO_CONTENT",
        "SKILL_EXECUTION_FAILED",
        "SKILL_EXECUTION_ERROR",
        "MEMORY_WRITER_UNAVAILABLE",
        "MEMORY_STORE_UNAVAILABLE",
    }
)
SKILL_EXEC_NO_EFFECT_MARKERS = (
    "no memory writer available",
    "no memory store available",
    "no memory backend available",
    "skipping diary",
    "no conversations on",
)


@dataclass(frozen=True, slots=True)
class GovernedSkillExecutionOutcome:
    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict[str, Any]


def normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in (
            "result_text",
            "result",
            "detail",
            "message",
            "summary",
            "output",
            "text",
        ):
            text = flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)


def effective_skill_success(
    *,
    raw_status: str,
    raw_code: str,
    raw_result: Any,
    result_text: str,
) -> bool:
    status_value = normalize_text(raw_status, lower=True)
    if status_value in SKILL_EXEC_FAIL_STATUS:
        return False
    code_value = normalize_text(raw_code).upper()
    if code_value in SKILL_EXEC_FAIL_CODES:
        return False
    digest = f"{str(raw_result or '')} {str(result_text or '')}".lower()
    return not any(marker in digest for marker in SKILL_EXEC_NO_EFFECT_MARKERS)


def build_memory_exec_context(
    *,
    ports: Any,
    request_id: str,
    session_id: str,
    intent_id: str,
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    episodic_store = ports.get_episodic_store()
    semantic_recaller = ports.get_semantic_recaller()
    if episodic_store is None and semantic_recaller is None:
        return {}

    default_emotion_tag = (
        normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper()
        or "NEUTRAL"
    )

    def episodic_writer(
        *,
        content: str,
        tags: list[str] | None = None,
        importance: float | None = None,
        source: str | None = None,
        emotion_tag: str | None = None,
        session_id: str | None = None,
    ) -> str:
        del importance
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
            emotion_tag=(
                str(emotion_tag or default_emotion_tag).strip().upper()
                or default_emotion_tag
            ),
        )
        if not bool(result.success):
            raise RuntimeError(
                str(result.reason_code or result.detail or "memory write failed")
            )
        return str(result.memory_id or "")

    def semantic_recaller_wrapper(
        *,
        query: str,
        top_k: int = 5,
        threshold: float = 0.6,
        tags: list[str] | None = None,
        session_id: str | None = None,
        search_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        del tags
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
                threshold=(
                    float(threshold) if threshold is not None else None
                ),
                filter_metadata=filter_metadata or None,
            )
        )
        hits = list(getattr(recall_result, "hits", []) or [])
        return [
            {
                "doc_id": str(getattr(hit, "doc_id", "") or ""),
                "content": str(getattr(hit, "content", "") or ""),
                "score": float(getattr(hit, "score", 0.0) or 0.0),
                "metadata": dict(getattr(hit, "metadata", {}) or {}),
                "source": str(getattr(hit, "source", "") or ""),
            }
            for hit in hits
        ]

    return {
        "episodic_store": episodic_store,
        "episodic_writer": episodic_writer,
        "semantic_recaller": semantic_recaller_wrapper,
    }


def coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _run_awaitable_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            result_box["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error_box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(
        target=worker,
        name="governed-memory-skill-awaitable",
        daemon=True,
    )
    thread.start()
    done.wait()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


__all__ = [
    "GovernedSkillExecutionOutcome",
    "build_memory_exec_context",
    "coerce_mapping",
    "effective_skill_success",
    "flatten_result_text",
    "normalize_text",
]
