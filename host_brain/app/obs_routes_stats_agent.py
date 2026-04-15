"""obs_routes_stats_agent.py — 可观测性统计端点（从 obs_routes_stats.py 拆分）。

子模块：Agent Loop统计、Memory子系统统计
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from host_brain.retrieval.model_suite import resolve_retrieval_model_suite

from host_brain.audit.sqlite_ledger import SQLiteLedger

logger = logging.getLogger("Natsume.obs")


from .obs_routes_core import (
    _build_trace_summary,
    router,
    _extract_input_dispatch_latency_ms,
    _fetch_request_rows,
    _get_ledger,
    _is_input_dispatch_event,
    _normalize_input_dispatch_source,
    _now_ms,
    _safe_json,
)


# ---------------------------------------------------------------------------
# P3: GET /obs/agent_loop/stats  — Agent Loop 详细统计
# ---------------------------------------------------------------------------
class AgentLoopRoundItem(BaseModel):
    request_id: str
    session_id: Optional[str]
    goal: str
    total_rounds: int
    goal_achieved: bool
    total_actions: int
    success_actions: int
    failed_actions: int
    total_latency_ms: Optional[int]
    ts_ms: int

class AgentLoopStatsResponse(BaseModel):
    window_minutes: int
    total_loops: int
    achieved: int
    failed: int
    achievement_rate: float
    avg_rounds: Optional[float]
    recent_loops: List[AgentLoopRoundItem]
    timestamp: int

@router.get("/agent_loop/stats", response_model=AgentLoopStatsResponse)
def get_agent_loop_stats(
    window_minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(10, ge=1, le=50),
):
    """P3: Agent Loop 详细统计 — 目标达成率、平均轮次、最近执行列表。"""
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    try:
        rows = ledger.fetch_by_time_range("agent_loop_log", start_ms=since_ms, end_ms=now)
    except Exception:
        rows = []

    # 按 request_id 聚合（取 LOOP_END 或最后一行）
    from collections import defaultdict
    loop_map: Dict[str, List] = defaultdict(list)
    for r in rows:
        loop_map[r.get("request_id", "")].append(r)

    loops = []
    for req_id, loop_rows in loop_map.items():
        # 找 LOOP_END 行
        end_rows = [r for r in loop_rows if str(r.get("phase", "")).upper() in ("LOOP_END", "LOOP_SUCCESS", "LOOP_FAILED")]
        summary_row = end_rows[-1] if end_rows else loop_rows[-1]

        total_rounds = max((r.get("round") or 0 for r in loop_rows), default=0)
        goal_achieved = bool(summary_row.get("goal_achieved"))
        total_actions = sum(r.get("action_count") or 0 for r in loop_rows)
        success_actions = sum(r.get("success_count") or 0 for r in loop_rows)
        failed_actions = sum(r.get("failed_count") or 0 for r in loop_rows)
        total_latency = sum(r.get("latency_ms") or 0 for r in loop_rows if r.get("latency_ms"))

        loops.append({
            "request_id": req_id,
            "session_id": summary_row.get("session_id"),
            "goal": (summary_row.get("goal") or "")[:80],
            "total_rounds": total_rounds,
            "goal_achieved": goal_achieved,
            "total_actions": total_actions,
            "success_actions": success_actions,
            "failed_actions": failed_actions,
            "total_latency_ms": total_latency or None,
            "ts_ms": summary_row.get("ts_ms") or 0,
        })

    total_loops = len(loops)
    achieved = sum(1 for l in loops if l["goal_achieved"])
    failed_loops = total_loops - achieved
    achievement_rate = round(achieved / total_loops * 100, 1) if total_loops > 0 else 0.0
    avg_rounds = round(sum(l["total_rounds"] for l in loops) / total_loops, 1) if total_loops > 0 else None

    # 最近 N 条（按 ts_ms 倒序）
    recent = sorted(loops, key=lambda l: l["ts_ms"], reverse=True)[:limit]

    return AgentLoopStatsResponse(
        window_minutes=window_minutes,
        total_loops=total_loops,
        achieved=achieved,
        failed=failed_loops,
        achievement_rate=achievement_rate,
        avg_rounds=avg_rounds,
        recent_loops=[AgentLoopRoundItem(**l) for l in recent],
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #26: GET /obs/memory/stats  — Memory 子系统详细统计
# ---------------------------------------------------------------------------
class MemorySubsystemItem(BaseModel):
    subsystem: str
    display_name: str
    total: int
    success: int
    failed: int
    success_rate: Optional[float]
    p95_ms: Optional[int]
    last_ts: Optional[int]
    extra: Optional[Dict[str, Any]] = None

class MemoryStatsResponse(BaseModel):
    window_minutes: int
    subsystems: List[MemorySubsystemItem]
    # 聚合指标
    total_writes: int
    guard_reject_rate: Optional[float]
    compress_rate: Optional[float]
    distill_count: int
    timestamp: int

@router.get("/memory/stats", response_model=MemoryStatsResponse)
def get_memory_stats(
    window_minutes: int = Query(60, ge=1, le=10080, description="统计窗口（分钟）"),
):
    """Ref #26: Memory 子系统详细统计。

    覆盖四个 Memory 子系统：
    - EpisodicStore：写入量、成功率
    - MemoryGuard：拒绝率（NARRATIVE_ONLY 过滤 + 宪法校验拒绝）
    - ContextCompressor：压缩率（压缩前/后 token 数比值）
    - LongTermDistiller：蒸馏次数、蒸馏成功率
    """
    ledger = _get_ledger()
    now = _now_ms()
    since_ms = now - window_minutes * 60 * 1000

    # ── EpisodicStore 写入统计 ────────────────────────────────────────────
    try:
        episodic_rows = ledger.fetch_by_time_range("episodic_store_log", start_ms=since_ms, end_ms=now)
    except Exception:
        episodic_rows = []

    ep_total = len(episodic_rows)
    ep_success = sum(1 for r in episodic_rows if str(r.get("status", "")).upper() in ("OK", "WRITTEN", "SUCCESS"))
    ep_failed = ep_total - ep_success
    ep_rate = round(ep_success / ep_total * 100, 1) if ep_total > 0 else None
    ep_last = max((r.get("ts_ms") or 0 for r in episodic_rows), default=None)

    # ── MemoryGuard 拒绝统计 ──────────────────────────────────────────────
    try:
        guard_rows = ledger.fetch_by_time_range("memory_guard_log", start_ms=since_ms, end_ms=now)
    except Exception:
        guard_rows = []

    # 从 error_log 中补充 MemoryGuard 相关记录（MEMORY_GUARD_FILTERED 标签）
    try:
        error_rows = ledger.fetch_by_time_range("error_log", start_ms=since_ms, end_ms=now)
        guard_error_rows = [
            r for r in error_rows
            if "MEMORY_GUARD" in str(r.get("stage", "")).upper()
            or "MEMORY_GUARD" in str(r.get("reason_code", "")).upper()
        ]
    except Exception:
        guard_error_rows = []

    all_guard_rows = guard_rows + guard_error_rows
    guard_total = len(all_guard_rows)
    guard_rejected = sum(
        1 for r in all_guard_rows
        if str(r.get("status", "")).upper() in ("REJECTED", "BLOCKED", "FAILED")
        or str(r.get("reason_code", "")).upper().startswith("EPISODIC_")
        or str(r.get("reason_code", "")).upper().startswith("CANON_")
    )
    guard_reject_rate = round(guard_rejected / guard_total * 100, 1) if guard_total > 0 else None
    guard_last = max((r.get("ts_ms") or 0 for r in all_guard_rows), default=None)

    # ── ContextCompressor 压缩统计 ────────────────────────────────────────
    try:
        compress_rows = ledger.fetch_by_time_range("context_compress_log", start_ms=since_ms, end_ms=now)
    except Exception:
        compress_rows = []

    comp_total = len(compress_rows)
    comp_success = sum(1 for r in compress_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    comp_failed = comp_total - comp_success
    comp_rate = None
    if compress_rows:
        # 计算平均压缩率：(1 - output_tokens / input_tokens) * 100
        ratios = []
        for r in compress_rows:
            inp = r.get("input_tokens") or r.get("input_chars")
            out = r.get("output_tokens") or r.get("output_chars")
            if inp and out and inp > 0:
                ratios.append(1.0 - out / inp)
        if ratios:
            comp_rate = round(sum(ratios) / len(ratios) * 100, 1)
    comp_last = max((r.get("ts_ms") or 0 for r in compress_rows), default=None)

    # ── LongTermDistiller 蒸馏统计 ────────────────────────────────────────
    try:
        distill_rows = ledger.fetch_by_time_range("distill_log", start_ms=since_ms, end_ms=now)
    except Exception:
        distill_rows = []

    distill_total = len(distill_rows)
    distill_success = sum(1 for r in distill_rows if str(r.get("status", "")).upper() in ("OK", "DONE", "SUCCESS"))
    distill_failed = distill_total - distill_success
    distill_rate = round(distill_success / distill_total * 100, 1) if distill_total > 0 else None
    distill_last = max((r.get("ts_ms") or 0 for r in distill_rows), default=None)

    subsystems = [
        MemorySubsystemItem(
            subsystem="episodic_store",
            display_name="EpisodicStore 写入",
            total=ep_total,
            success=ep_success,
            failed=ep_failed,
            success_rate=ep_rate,
            p95_ms=None,
            last_ts=ep_last,
            extra={"write_count": ep_success},
        ),
        MemorySubsystemItem(
            subsystem="memory_guard",
            display_name="MemoryGuard 宪法守卫",
            total=guard_total,
            success=guard_total - guard_rejected,
            failed=guard_rejected,
            success_rate=(100.0 - guard_reject_rate) if guard_reject_rate is not None else None,
            p95_ms=None,
            last_ts=guard_last,
            extra={"reject_rate": guard_reject_rate, "rejected_count": guard_rejected},
        ),
        MemorySubsystemItem(
            subsystem="context_compressor",
            display_name="ContextCompressor 压缩",
            total=comp_total,
            success=comp_success,
            failed=comp_failed,
            success_rate=round(comp_success / comp_total * 100, 1) if comp_total > 0 else None,
            p95_ms=None,
            last_ts=comp_last,
            extra={"avg_compress_rate_pct": comp_rate},
        ),
        MemorySubsystemItem(
            subsystem="long_term_distiller",
            display_name="LongTermDistiller 蒸馏",
            total=distill_total,
            success=distill_success,
            failed=distill_failed,
            success_rate=distill_rate,
            p95_ms=None,
            last_ts=distill_last,
            extra={"distill_count": distill_success},
        ),
    ]

    return MemoryStatsResponse(
        window_minutes=window_minutes,
        subsystems=subsystems,
        total_writes=ep_success,
        guard_reject_rate=guard_reject_rate,
        compress_rate=comp_rate,
        distill_count=distill_success,
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Ref #126: GET /obs/memory/resident-context — 常驻设定 / 人格风格注入快照
# ---------------------------------------------------------------------------
def _truncate_memory_preview(value: Any, limit: int = 96) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _resolve_style_source_path(style_root: str, canon_id: str | None) -> str:
    normalized = str(canon_id or "").strip()
    if not normalized:
        return os.path.join(style_root, "style_profile_v5.json")
    if normalized.endswith("_v5") or normalized in {"natsume_ai_cn", "natsume_ai_ja"}:
        return os.path.join(style_root, "style_profile_v5.json")
    return os.path.join(style_root, f"{normalized}.json")


class ResidentCanonFileItem(BaseModel):
    kind: str
    doc_id: str
    title: Optional[str] = None
    source_path: Optional[str] = None
    version: Optional[int] = None
    status: Optional[str] = None
    updated_by: Optional[str] = None
    ts_ms: Optional[int] = None
    preview: Optional[str] = None


class ResidentStyleBindingItem(BaseModel):
    node_id: str
    canon_id: Optional[str] = None
    version: Optional[int] = None
    citation: Optional[str] = None
    source_path: Optional[str] = None
    tone_only: bool = True
    unsafe_as_fact: bool = True
    preview: Optional[str] = None


class ResidentMemoryPersonaEvidenceItem(BaseModel):
    kind: Optional[str] = None
    source_ref: Optional[str] = None
    evidence_ref: Optional[str] = None
    emotion_color_tag: Optional[str] = None
    suppression_flag: bool = False
    distortion_level: Optional[float] = None
    content: Optional[str] = None


class ResidentMemoryContextResponse(BaseModel):
    world_canon_documents: List[ResidentCanonFileItem]
    persona_resident_documents: List[ResidentCanonFileItem]
    style_canon_documents: List[ResidentCanonFileItem]
    active_style_bindings: List[ResidentStyleBindingItem]
    latest_style_pack_rows: List[ResidentStyleBindingItem]
    latest_memory_persona_evidence: List[ResidentMemoryPersonaEvidenceItem]
    latest_memory_recall_summary: Optional[str] = None
    latest_request_id: Optional[str] = None
    latest_session_id: Optional[str] = None
    latest_render_target_lang: Optional[str] = None
    latest_selected_tier: Optional[str] = None
    prompt_context_available: bool = False
    timestamp: int


@router.get("/memory/resident-context", response_model=ResidentMemoryContextResponse)
def get_memory_resident_context():
    """Ref #126: 常驻设定文件、风格绑定与最新人格证据快照。"""
    now = _now_ms()
    ledger = _get_ledger()
    world_root = str(os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world").strip()
    style_root = str(os.environ.get("Natsume_STYLE_CANON_ROOT") or "host_brain/canon/style").strip()
    character_profile_path = str(
        os.environ.get("NATSUME_CHARACTER_PROFILE_PATH")
        or os.path.join("host_brain", "canon", "character", "character_profile.json")
    ).strip()

    world_docs: List[ResidentCanonFileItem] = []
    try:
        from host_brain.memory.canon_world_store import WorldCanonStore  # noqa: PLC0415

        world_store = WorldCanonStore(world_root=world_root, ledger=ledger)
        for doc in world_store.list_documents():
            world_docs.append(
                ResidentCanonFileItem(
                    kind="world",
                    doc_id=str(getattr(doc, "doc_id", "") or ""),
                    title=str(getattr(doc, "title", "") or "").strip() or None,
                    source_path=str(getattr(doc, "source_path", "") or "").strip() or None,
                    version=int(getattr(doc, "version", 0) or 0) or None,
                    status=str(getattr(doc, "status", "") or "").strip() or None,
                    updated_by=str(getattr(doc, "updated_by", "") or "").strip() or None,
                    ts_ms=int(getattr(doc, "ts_ms", 0) or 0) or None,
                    preview=_truncate_memory_preview(getattr(doc, "body", ""), limit=120) or None,
                )
            )
    except Exception as exc:
        logger.debug("resident world canon context degraded: %s", exc)

    persona_docs: List[ResidentCanonFileItem] = []
    try:
        with open(character_profile_path, "r", encoding="utf-8-sig") as fp:
            character_payload = json.load(fp)
        persona_docs.append(
            ResidentCanonFileItem(
                kind="persona",
                doc_id=str(character_payload.get("doc_id") or "character-profile").strip(),
                title=str(character_payload.get("title") or "角色人设常驻文件").strip() or None,
                source_path=character_profile_path,
                version=(int(character_payload.get("version")) if character_payload.get("version") not in (None, "") else None),
                status=str(character_payload.get("status") or "ACTIVE").strip() or None,
                updated_by="character_profile",
                ts_ms=(int(os.path.getmtime(character_profile_path) * 1000) if os.path.exists(character_profile_path) else None),
                preview=_truncate_memory_preview(character_payload.get("instruction_cn"), limit=120) or None,
            )
        )
    except Exception as exc:
        logger.debug("resident persona context degraded: %s", exc)

    style_docs: List[ResidentCanonFileItem] = []
    try:
        from host_brain.memory.canon_style_store import StyleCanonStore  # noqa: PLC0415

        style_store = StyleCanonStore(style_root=style_root, ledger=ledger)
        for entry in style_store.list_entries():
            style_docs.append(
                ResidentCanonFileItem(
                    kind="style",
                    doc_id=str(getattr(entry, "canon_id", "") or ""),
                    title=str(getattr(entry, "canon_id", "") or "").strip() or None,
                    source_path=_resolve_style_source_path(style_root, getattr(entry, "canon_id", None)),
                    version=int(getattr(entry, "version", 0) or 0) or None,
                    status="ACTIVE",
                    updated_by=str(getattr(entry, "updated_by", "") or "").strip() or None,
                    ts_ms=int(getattr(entry, "ts_ms", 0) or 0) or None,
                    preview=_truncate_memory_preview(getattr(entry, "content", ""), limit=120) or None,
                )
            )
    except Exception as exc:
        logger.debug("resident style canon context degraded: %s", exc)

    latest_snapshot: dict[str, Any] = {}
    active_style_bindings_raw: Mapping[str, Any] | None = None
    latest_render_target_lang = "zh"
    try:
        from host_brain.app.chat_context_builder import (  # noqa: PLC0415
            build_node_style_bindings,
            get_latest_prompt_context_runtime_snapshot,
        )

        latest_snapshot = dict(get_latest_prompt_context_runtime_snapshot() or {})
        latest_render_target_lang = str(latest_snapshot.get("render_target_lang") or "zh").strip().lower() or "zh"
        active_style_bindings_raw = latest_snapshot.get("style_bindings")
        if not isinstance(active_style_bindings_raw, Mapping) or not active_style_bindings_raw:
            active_style_bindings_raw = build_node_style_bindings(render_target_lang=latest_render_target_lang)
    except Exception as exc:
        logger.debug("resident prompt-context snapshot unavailable: %s", exc)
        latest_snapshot = {}
        active_style_bindings_raw = {}

    active_style_bindings: List[ResidentStyleBindingItem] = []
    for node_id, row in dict(active_style_bindings_raw or {}).items():
        if not isinstance(row, Mapping):
            continue
        canon_id = str(row.get("canon_id") or "").strip() or None
        active_style_bindings.append(
            ResidentStyleBindingItem(
                node_id=str(node_id),
                canon_id=canon_id,
                version=(int(row.get("version")) if row.get("version") not in (None, "") else None),
                citation=str(row.get("citation") or "").strip() or None,
                source_path=_resolve_style_source_path(style_root, canon_id),
                tone_only=bool(row.get("tone_only", True)),
                unsafe_as_fact=bool(row.get("unsafe_as_fact", True)),
                preview=_truncate_memory_preview(
                    row.get("style_instruction") or row.get("instruction"),
                    limit=88,
                ) or None,
            )
        )

    style_pack_raw = latest_snapshot.get("style_pack")
    style_pack = dict(style_pack_raw) if isinstance(style_pack_raw, Mapping) else {}
    style_rows = list(style_pack.get("style_canon") or []) if isinstance(style_pack.get("style_canon"), list) else []
    latest_style_pack_rows: List[ResidentStyleBindingItem] = []
    for row in style_rows:
        if not isinstance(row, Mapping):
            continue
        canon_id = str(row.get("canon_id") or "").strip() or None
        latest_style_pack_rows.append(
            ResidentStyleBindingItem(
                node_id=str(row.get("node_id") or row.get("slot") or "style_pack"),
                canon_id=canon_id,
                version=(int(row.get("version")) if row.get("version") not in (None, "") else None),
                citation=str(row.get("citation") or "").strip() or None,
                source_path=_resolve_style_source_path(style_root, canon_id),
                tone_only=bool(row.get("tone_only", True)),
                unsafe_as_fact=bool(row.get("unsafe_as_fact", True)),
                preview=_truncate_memory_preview(
                    row.get("style_instruction") or row.get("instruction"),
                    limit=88,
                ) or None,
            )
        )

    memory_persona_evidence_raw = latest_snapshot.get("memory_persona_evidence")
    memory_persona_rows = (
        list(memory_persona_evidence_raw)
        if isinstance(memory_persona_evidence_raw, list)
        else []
    )
    latest_memory_persona_evidence: List[ResidentMemoryPersonaEvidenceItem] = []
    for row in memory_persona_rows:
        if not isinstance(row, Mapping):
            continue
        latest_memory_persona_evidence.append(
            ResidentMemoryPersonaEvidenceItem(
                kind=str(row.get("kind") or "").strip() or None,
                source_ref=str(row.get("source_ref") or "").strip() or None,
                evidence_ref=str(row.get("evidence_ref") or "").strip() or None,
                emotion_color_tag=str(row.get("emotion_color_tag") or "").strip() or None,
                suppression_flag=bool(row.get("suppression_flag", False)),
                distortion_level=(
                    float(row.get("distortion_level"))
                    if row.get("distortion_level") not in (None, "")
                    else None
                ),
                content=_truncate_memory_preview(row.get("content"), limit=120) or None,
            )
        )

    prompt_context_observability = (
        dict(latest_snapshot.get("prompt_context_observability") or {})
        if isinstance(latest_snapshot.get("prompt_context_observability"), Mapping)
        else {}
    )

    return ResidentMemoryContextResponse(
        world_canon_documents=world_docs,
        persona_resident_documents=persona_docs,
        style_canon_documents=style_docs,
        active_style_bindings=active_style_bindings,
        latest_style_pack_rows=latest_style_pack_rows,
        latest_memory_persona_evidence=latest_memory_persona_evidence,
        latest_memory_recall_summary=str(latest_snapshot.get("memory_recall_summary") or "").strip() or None,
        latest_request_id=str(latest_snapshot.get("request_id") or "").strip() or None,
        latest_session_id=str(latest_snapshot.get("session_id") or "").strip() or None,
        latest_render_target_lang=latest_render_target_lang or None,
        latest_selected_tier=str(prompt_context_observability.get("selected_tier") or "").strip() or None,
        prompt_context_available=bool(latest_snapshot),
        timestamp=now,
    )


