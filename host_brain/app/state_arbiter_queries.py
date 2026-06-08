"""state_arbiter_queries.py — StateArbiter 四路查询（从 chat_stream_handler.py 迁移）。

提供：
  - _resolve_state_arbiter_world_fact: 世界观查询
  - _resolve_state_arbiter_bond_fact: Bond 关系查询
  - _resolve_state_arbiter_episodic_fact: 情节记忆查询
  - _normalize_state_arbiter_value / _build_state_arbiter_world_evidence_item: 辅助函数
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Mapping

from host_brain.app.prompt_builder import _truncate_for_prompt

_logger = logging.getLogger("Natsume.state_arbiter_queries")


def _normalize_state_arbiter_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("content", "body", "text", "summary", "value"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        try:
            return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value).strip()
    if isinstance(value, list):
        chunks: list[str] = []
        for row in value[:3]:
            if isinstance(row, Mapping):
                piece = (
                    str(row.get("content") or "").strip()
                    or str(row.get("body") or "").strip()
                    or str(row.get("text") or "").strip()
                    or str(row.get("title") or "").strip()
                )
            else:
                piece = str(row).strip()
            if piece:
                chunks.append(piece)
        return "\n".join(chunks).strip()
    return str(value).strip()


def _build_state_arbiter_world_evidence_item(
    *,
    fact_text: str,
    source_name: str,
    fact_key: str,
) -> dict[str, Any]:
    normalized_source = str(source_name or "unknown").strip() or "unknown"
    normalized_key = str(fact_key or "world_canon_facts").strip() or "world_canon_facts"
    snippet = _truncate_for_prompt(fact_text, limit=240)
    return {
        "slot_name": "world_canon",
        "source": "state_arbiter",
        "store_id": "world_canon",
        "evidence_ref": f"state_arbiter://{normalized_source}/{normalized_key}",
        "source_ref": f"state_arbiter://{normalized_source}",
        "snippet": snippet,
        "content": fact_text,
        "text": fact_text,
        "metadata": {
            "store_id": "world_canon",
            "source": "state_arbiter",
            "arbiter_source": normalized_source,
            "fact_key": normalized_key,
        },
    }


async def _resolve_state_arbiter_world_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "world_canon_facts",
    }
    try:
        from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415

        arbiter = get_state_arbiter()
        result = await arbiter.resolve("world_canon_facts", session_id=session_id)
        if result is None:
            summary["reason_code"] = "NO_MATCH"
            return summary, None

        fact_text = _normalize_state_arbiter_value(result.value)
        summary.update(
            {
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
                "is_fallback": bool(getattr(result, "is_fallback", False)),
                "fallback_chain": [
                    str(getattr(row, "value", row) or "")
                    for row in list(getattr(result, "fallback_chain", []) or [])
                    if str(getattr(row, "value", row) or "").strip()
                ],
                "conflict_detected": bool(getattr(result, "conflict_detected", False)),
                "conflict_detail": str(getattr(result, "conflict_detail", "") or "").strip() or None,
            }
        )
        if not fact_text:
            summary["reason_code"] = "EMPTY_VALUE"
            return summary, None

        evidence_item = _build_state_arbiter_world_evidence_item(
            fact_text=fact_text,
            source_name=str(getattr(result.source, "value", result.source) or "unknown"),
            fact_key=str(getattr(result, "fact_key", "world_canon_facts") or "world_canon_facts"),
        )
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item
    except Exception as exc:
        _logger.debug(
            "StateArbiter 调用失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "STATE_ARBITER_FAILED"
        summary["detail"] = str(exc)
        return summary, None


async def _resolve_state_arbiter_bond_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """查询 BondGraph 关系强度，注入 prompt 作为关系上下文。"""
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "bond_graph_state",
    }
    try:
        from host_brain.tribe.social_brain_agent import get_social_brain_agent  # noqa: PLC0415

        _bond_engine = get_social_brain_agent().resolve_stage_engine()
        if _bond_engine is None:
            summary["reason_code"] = "BOND_ENGINE_NOT_AVAILABLE"
            return summary, None

        _bond_state = await asyncio.to_thread(
            _bond_engine.get_bond_state,
            session_id=session_id,
        )
        if _bond_state is None:
            summary["reason_code"] = "NO_BOND_STATE"
            return summary, None

        bond_stage = str(getattr(_bond_state, "stage", "") or "").strip()
        bond_score = float(getattr(_bond_state, "score", 0.0) or 0.0)
        bond_label = str(getattr(_bond_state, "label", "") or "").strip()
        interaction_count = int(getattr(_bond_state, "interaction_count", 0) or 0)

        if not bond_stage:
            summary["reason_code"] = "EMPTY_BOND_STAGE"
            return summary, None

        fact_parts: list[str] = [f"当前关系阶段：{bond_stage}"]
        if bond_label:
            fact_parts.append(f"关系标签：{bond_label}")
        if bond_score > 0:
            fact_parts.append(f"关系强度：{bond_score:.2f}")
        if interaction_count > 0:
            fact_parts.append(f"累计互动次数：{interaction_count}")
        fact_text = "，".join(fact_parts)

        evidence_item = {
            "slot_name": "bond_graph",
            "doc_id": f"bond_graph::{session_id}",
            "snippet": fact_text[:300],
            "score": 0.65,
            "source": "state_arbiter_bond",
            "metadata": {
                "fact_key": "bond_graph_state",
                "bond_stage": bond_stage,
                "bond_score": bond_score,
                "interaction_count": interaction_count,
                "source": "social_brain_agent",
            },
        }
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["bond_stage"] = bond_stage
        summary["bond_score"] = bond_score
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item

    except Exception as exc:
        _logger.debug(
            "BondGraph 状态查询失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "BOND_GRAPH_FAILED"
        summary["detail"] = str(exc)
        return summary, None


async def _resolve_state_arbiter_episodic_fact(
    *,
    session_id: str,
    request_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """查询最新情节记忆，注入 prompt 作为近期记忆上下文。"""
    summary: dict[str, Any] = {
        "status": "SKIPPED",
        "reason_code": "NOT_AVAILABLE",
        "fact_key": "episodic_latest",
    }
    try:
        from host_brain.app.dependencies import get_state_arbiter  # noqa: PLC0415

        arbiter = get_state_arbiter()
        result = await arbiter.resolve("episodic_latest", session_id=session_id)
        if result is None:
            summary["reason_code"] = "NO_MATCH"
            return summary, None

        fact_text = _normalize_state_arbiter_value(result.value)
        summary.update(
            {
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
                "is_fallback": bool(getattr(result, "is_fallback", False)),
                "conflict_detected": bool(getattr(result, "conflict_detected", False)),
            }
        )
        if not fact_text:
            summary["reason_code"] = "EMPTY_VALUE"
            return summary, None

        evidence_item = {
            "slot_name": "episodic_latest",
            "doc_id": f"episodic_latest::{session_id}",
            "snippet": fact_text[:400],
            "score": 0.75,
            "source": "state_arbiter_episodic",
            "metadata": {
                "fact_key": "episodic_latest",
                "source": str(getattr(result.source, "value", result.source) or "unknown"),
            },
        }
        summary["status"] = "APPLIED"
        summary["reason_code"] = None
        summary["value_chars"] = len(fact_text)
        return summary, evidence_item
    except Exception as exc:
        _logger.debug(
            "StateArbiter episodic_latest 调用失败（降级）: %s",
            exc,
            extra={"request_id": request_id},
        )
        summary["status"] = "DEGRADED"
        summary["reason_code"] = "STATE_ARBITER_EPISODIC_FAILED"
        summary["detail"] = str(exc)
        return summary, None
