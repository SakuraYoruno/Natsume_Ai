"""Memory distillation engine (M10-C/D).

Distills top-k episodic memories into one semantic memory record.
Supports optional LLM summarization with fallback to deterministic merge.
"""

from __future__ import annotations

import hashlib
import json
import threading
import os
import uuid
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import LLM_POOL_EXHAUSTED
from host_brain.core.ids import now_ms, new_request_id
from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync
from host_brain.memory.episodic import EpisodicStore


@runtime_checkable
class LlmSummaryClient(Protocol):
    """Protocol for external LLM summary adapters."""

    def summarize(
        self,
        texts: list[str],
        *,
        query_text: str,
        max_tokens: int = 200,
    ) -> str:
        ...


def _load_distillation_llm_templates() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("memory_distillation.llm_summary.prompt")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _pool_credentials_available() -> bool:
    for key in (
        "HOST_GEMINI_API_KEY",
        "GEMINI_API_KEY",
        "HOST_MINIMAX_API_KEY",
        "MINIMAX_API_KEY",
        "OPENAI_API_KEY",
        "HOST_OPENAI_API_KEY",
    ):
        if str(os.environ.get(key) or "").strip():
            return True
    return False


def _truncate_text(text: Any, *, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max(1, int(limit)):
        return normalized
    return normalized[: max(1, int(limit)) - 1].rstrip() + "…"


def _resolve_template_text(templates: Mapping[str, Any], key: str, *, lang: str) -> str:
    raw = templates.get(key)
    if isinstance(raw, Mapping):
        localized = raw.get(lang) or raw.get("zh") or raw.get("ja")
        return str(localized or "").strip()
    return str(raw or "").strip()


class PoolSummaryClient:
    """Shared-pool LLM summary client for semantic distillation."""

    def __init__(
        self,
        *,
        scene_type: str = "MEMORY_DISTILLATION",
        cognitive_tier: str = "D",
        timeout_ms: int = 2600,
        render_target_lang: str = "zh",
    ) -> None:
        self._scene_type = str(scene_type or "MEMORY_DISTILLATION").strip().upper() or "MEMORY_DISTILLATION"
        self._cognitive_tier = str(cognitive_tier or "D").strip().upper() or "D"
        self._timeout_ms = max(800, int(timeout_ms))
        self._render_target_lang = "ja" if str(render_target_lang or "").strip().lower() == "ja" else "zh"

    def summarize(
        self,
        texts: list[str],
        *,
        query_text: str,
        max_tokens: int = 200,
    ) -> str:
        if not _pool_credentials_available():
            raise RuntimeError("llm pool credentials unavailable")

        snippets = [
            f"{index + 1}. {_truncate_text(text, limit=220)}"
            for index, text in enumerate(list(texts or [])[:6])
            if str(text or "").strip()
        ]
        if not snippets:
            raise ValueError("no texts to summarize")

        templates = _load_distillation_llm_templates()
        system_prompt = _resolve_template_text(
            templates,
            "system_prompt",
            lang=self._render_target_lang,
        ) or "你负责做长期记忆蒸馏，请输出简洁、准确、可复用的语义摘要。"
        user_template = _resolve_template_text(
            templates,
            "user_prompt",
            lang=self._render_target_lang,
        ) or (
            "查询主题：{query_text}\n"
            "记忆片段：\n{memory_lines}\n\n"
            "请输出 JSON：{{\"summary\":\"...\"}}"
        )
        user_prompt = user_template.format(
            query_text=_truncate_text(query_text, limit=120),
            memory_lines="\n".join(snippets),
            max_tokens=max(64, min(int(max_tokens or 200), 400)),
        )
        result = complete_llm_with_pool_sync(
            request_id=new_request_id(),
            scene_type=self._scene_type,
            cognitive_tier=self._cognitive_tier,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout_ms=self._timeout_ms,
            gen_params={"temperature": 0.35},
            response_format="json_object",
        )
        if result.pool_exhausted:
            raise RuntimeError(LLM_POOL_EXHAUSTED)
        parsed = json.loads(result.content)
        summary = _truncate_text(parsed.get("summary"), limit=320)
        if not summary:
            raise ValueError("empty distillation summary")
        return summary


def build_default_llm_summary_client(
    *,
    render_target_lang: str = "zh",
    timeout_ms: int = 2600,
) -> LlmSummaryClient:
    return PoolSummaryClient(
        scene_type="MEMORY_DISTILLATION",
        cognitive_tier="D",
        timeout_ms=timeout_ms,
        render_target_lang=render_target_lang,
    )


@dataclass(frozen=True)
class SemanticMemoryRecord:
    """One semantic memory row generated by distillation."""

    semantic_id: str
    summary: str
    source_memory_ids: list[str]
    source_hash: str
    distill_strategy: str
    confidence: float
    tags: list[str]
    ts_ms: int
    operator_node_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_id": self.semantic_id,
            "summary": self.summary,
            "source_memory_ids": list(self.source_memory_ids),
            "source_hash": self.source_hash,
            "distill_strategy": self.distill_strategy,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "ts_ms": self.ts_ms,
            "operator_node_id": self.operator_node_id,
        }


@dataclass(frozen=True)
class DistillResult:
    """Distillation outcome."""

    success: bool
    semantic_id: str | None
    source_count: int
    detail: str
    ts_ms: int
    skipped: bool = False


class MemoryDistillationEngine:
    """Distills semantic memory from episodic recall results."""

    def __init__(
        self,
        *,
        episodic_store: EpisodicStore,
        ledger: SQLiteLedger,
    ) -> None:
        self.episodic = episodic_store
        self.ledger = ledger
        # F-41: protect idempotency check + write critical section.
        self._distill_lock = threading.Lock()

    def distill_top_k(
        self,
        *,
        query_text: str,
        top_k: int = 5,
        distill_strategy: str = "merge_top_k",
        tags: list[str] | None = None,
        operator_node_id: str = "distillation_engine",
        ts_ms: int | None = None,
        llm_client: LlmSummaryClient | None = None,
    ) -> DistillResult:
        distill_ts = int(ts_ms if ts_ms is not None else now_ms())
        top_k = min(max(1, top_k), 20)

        recall_k = min(max(top_k * 3, top_k), 20)
        recalled = [
            row
            for row in self.episodic.recall(query_text=query_text, top_k=recall_k)
            if not _is_semantic_memory_projection(row)
        ][:top_k]
        if not recalled:
            return DistillResult(
                success=False,
                semantic_id=None,
                source_count=0,
                detail="no episodic memories found for query",
                ts_ms=distill_ts,
            )

        source_ids = [r["memory_id"] for r in recalled]
        source_hash = _compute_source_hash(source_ids)

        existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
        if existing is not None:
            return DistillResult(
                success=True,
                semantic_id=existing["semantic_id"],
                source_count=len(source_ids),
                detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                ts_ms=distill_ts,
                skipped=True,
            )

        texts = [r["text"] for r in recalled]
        if llm_client is not None:
            try:
                summary = llm_client.summarize(
                    texts,
                    query_text=query_text,
                    max_tokens=200,
                )
                distill_strategy = "llm_summary"
            except Exception:
                summary = _merge_texts(texts, query_text=query_text)
        else:
            summary = _merge_texts(texts, query_text=query_text)

        semantic_id = f"sem_{uuid.uuid4().hex}"
        record = SemanticMemoryRecord(
            semantic_id=semantic_id,
            summary=summary,
            source_memory_ids=source_ids,
            source_hash=source_hash,
            distill_strategy=distill_strategy,
            confidence=_compute_confidence(recalled),
            tags=list(tags or []),
            ts_ms=distill_ts,
            operator_node_id=operator_node_id,
        )

        with self._distill_lock:
            existing = self.ledger.get_semantic_memory_by_source_hash(source_hash)
            if existing is not None:
                return DistillResult(
                    success=True,
                    semantic_id=existing["semantic_id"],
                    source_count=len(source_ids),
                    detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                    ts_ms=distill_ts,
                    skipped=True,
                )

            self.ledger.log_semantic_memory(record.to_dict())
            persisted = self.ledger.get_semantic_memory_by_source_hash(source_hash)
            if persisted is None:
                return DistillResult(
                    success=False,
                    semantic_id=None,
                    source_count=len(source_ids),
                    detail="distillation write failed: semantic_memory missing after insert",
                    ts_ms=distill_ts,
                )
            if persisted["semantic_id"] != semantic_id:
                return DistillResult(
                    success=True,
                    semantic_id=persisted["semantic_id"],
                    source_count=len(source_ids),
                    detail=f"skipped: semantic memory already exists for source_hash={source_hash[:12]}",
                    ts_ms=distill_ts,
                    skipped=True,
                )

        try:
            self.ledger.log_system_event(
                {
                    "request_id": semantic_id,
                    "event_type": "MEMORY_DISTILLED",
                    "source": operator_node_id,
                    "detail": (
                        f"semantic_id={semantic_id} "
                        f"source_count={len(source_ids)} "
                        f"strategy={distill_strategy} "
                        f"confidence={record.confidence:.3f}"
                    ),
                    "ts_ms": distill_ts,
                }
            )
        except Exception:
            pass

        return DistillResult(
            success=True,
            semantic_id=semantic_id,
            source_count=len(source_ids),
            detail=f"distilled {len(source_ids)} episodic memories into semantic_id={semantic_id}",
            ts_ms=distill_ts,
        )

    def list_semantic_memories(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        return self.ledger.list_semantic_memories(limit=limit, offset=offset)


def _compute_source_hash(source_ids: list[str]) -> str:
    sorted_ids = sorted(source_ids)
    payload = json.dumps(sorted_ids, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_semantic_memory_projection(row: Mapping[str, Any]) -> bool:
    memory_id = str(row.get("memory_id") or "").strip()
    metadata = row.get("metadata")
    meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    source_table = str(meta.get("source_table") or "").strip()
    semantic_id = str(meta.get("semantic_id") or "").strip()
    return (
        memory_id.startswith("semantic::")
        or source_table == "semantic_memory"
        or bool(semantic_id)
    )


def _merge_texts(texts: list[str], *, query_text: str) -> str:
    header = f"[语义摘要 | 查询: {query_text[:50]}]"
    body = "\n---\n".join(f"- {t.strip()}" for t in texts if t.strip())
    return f"{header}\n{body}"


def _compute_confidence(recalled: list[dict[str, Any]]) -> float:
    if not recalled:
        return 0.0
    scores = [float(r.get("score", 0.0)) for r in recalled]
    avg = sum(scores) / len(scores)
    return round(min(max(avg, 0.0), 1.0), 4)
