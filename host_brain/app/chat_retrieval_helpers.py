"""chat_retrieval_helpers.py — 检索辅助函数（从 chat_stream_handler.py 迁移）。

提供：
  - _run_retrieval: 并行执行检索
  - _build_recall_pack_inputs: RecallPack 输入构建
  - _recall_pack_to_retrieval_evidence_items: RecallPack → evidence items 转换
  - _build_skipped_retrieval_summary / _build_degraded_retrieval_summary: 降级摘要
  - _normalize_recall_pack_slot: slot 归一化
"""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

from host_brain.app.dependencies import get_retrieval_orchestrator
from host_brain.app.prompt_builder import _truncate_for_prompt
from host_brain.retrieval.orchestrator import (
    RetrievalOrchestratorRequest,
    build_degraded_retrieval_summary as _orchestrator_degraded_summary,
    build_skipped_retrieval_summary as _orchestrator_skipped_summary,
)

_RECALL_PACK_SLOT_ALIASES: dict[str, str] = {
    "world": "world_canon",
    "world_canon": "world_canon",
    "episodic": "episodic_vector",
    "episodic_store": "episodic_vector",
    "episodic_vector": "episodic_vector",
    "bond": "bond_graph",
    "bond_graph": "bond_graph",
    "artifact": "artifact_vault",
    "artifact_vault": "artifact_vault",
    "sqlite": "sqlite_ledger",
    "sqlite_ledger": "sqlite_ledger",
    "ledger": "sqlite_ledger",
}

_RECALL_PACK_SLOT_TO_STORE_ID: dict[str, str] = {
    "world_canon": "world_canon",
    "episodic_vector": "episodic_vector",
    "bond_graph": "bond_graph",
    "artifact_vault": "artifact_vault",
    "sqlite_ledger": "sqlite_ledger",
}


async def _run_retrieval(
    *,
    request_id: str,
    message: str,
    person_id: str,
    session_id: str,
    ts_ms: int,
    emotion_tag: Optional[str],
    render_target_lang: str,
) -> tuple[Dict[str, Any], list[dict[str, Any]]]:
    """并行执行检索，返回 (retrieval_summary, retrieval_evidence_items)。"""
    try:
        _retr_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "8").strip())
    except ValueError:
        _retr_top_k = 8
    orchestrator = get_retrieval_orchestrator()
    orchestrator_result = await orchestrator.arun(
        request=RetrievalOrchestratorRequest(
            trace_id=request_id,
            query=message,
            session_state={
                "person_id": person_id,
                "session_id": session_id,
                "emotion_tag": emotion_tag,
            },
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            top_k=_retr_top_k,
            ts_ms=ts_ms,
            emotion_context=emotion_tag,
            render_target_lang=render_target_lang,
        )
    )
    return (
        dict(orchestrator_result.retrieval_summary),
        list(orchestrator_result.retrieval_evidence_items),
    )


def _build_skipped_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return _orchestrator_skipped_summary(reason_code)


def _build_degraded_retrieval_summary(reason_code: str) -> Dict[str, Any]:
    return _orchestrator_degraded_summary(reason_code)


def _normalize_recall_pack_slot(
    *,
    slot_hint: str,
    source_ref: str,
) -> str | None:
    normalized_hint = str(slot_hint or "").strip().lower()
    if normalized_hint:
        direct = _RECALL_PACK_SLOT_ALIASES.get(normalized_hint)
        if direct:
            return direct
    normalized_source_ref = str(source_ref or "").strip().lower()
    if not normalized_source_ref:
        return None
    for alias, slot in _RECALL_PACK_SLOT_ALIASES.items():
        token = f"{alias}://"
        if normalized_source_ref.startswith(token):
            return slot
    return None


def _build_recall_pack_inputs(
    retrieval_evidence_items: list[dict[str, Any]],
) -> dict[str, str]:
    recall_results: dict[str, str] = {}
    for item in list(retrieval_evidence_items or []):
        if not isinstance(item, Mapping):
            continue
        metadata_raw = item.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        slot = _normalize_recall_pack_slot(
            slot_hint=str(
                item.get("slot_name")
                or item.get("source")
                or item.get("store_id")
                or metadata.get("store_id")
                or ""
            ),
            source_ref=str(item.get("source_ref") or metadata.get("source_ref") or ""),
        )
        if not slot:
            continue
        text = str(
            item.get("text") or item.get("content") or item.get("snippet") or ""
        ).strip()
        if not text:
            continue
        existing = recall_results.get(slot)
        if existing:
            merged = f"{existing}\n{text}"
            recall_results[slot] = _truncate_for_prompt(merged, limit=1600)
        else:
            recall_results[slot] = text
    return recall_results


def _recall_pack_to_retrieval_evidence_items(recall_pack: Any) -> list[dict[str, Any]]:
    from host_brain.app.schemas.recall_pack import RECALL_SLOT_PRIORITY  # noqa: PLC0415

    rewritten: list[dict[str, Any]] = []
    for slot_name in RECALL_SLOT_PRIORITY:
        slot = recall_pack.get_slot(slot_name)
        if slot is None:
            continue
        content = str(getattr(slot, "content", "") or "").strip()
        if not content:
            continue
        store_id = _RECALL_PACK_SLOT_TO_STORE_ID.get(slot_name, slot_name)
        rewritten.append(
            {
                "slot_name": slot_name,
                "source": slot_name,
                "store_id": store_id,
                "evidence_ref": f"recall_pack://{slot_name}",
                "source_ref": f"{slot_name}://recall_pack_builder",
                "snippet": _truncate_for_prompt(content, limit=240),
                "content": content,
                "text": content,
                "metadata": {
                    "store_id": store_id,
                    "source": "recall_pack_builder",
                    "truncated": bool(getattr(slot, "truncated", False)),
                    "conflict_suppressed": bool(getattr(slot, "conflict_suppressed", False)),
                },
            }
        )
    return rewritten
