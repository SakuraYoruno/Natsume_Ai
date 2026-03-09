"""Chat 核心业务逻辑模块。
从 fastapi_app.py 拆分，负责 POST /v1/chat 的核心编排逻辑。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Mapping, Optional

from edge_client.rpc.auth import build_edge_rpc_signature
from fastapi import Request
from host_brain.app.dependencies import (
    get_bond_stage_engine,
    get_episodic_store,
    get_graph_runtime,
    get_intent_engine,
    get_ledger,
    get_retrieval_orchestrator,
)
from host_brain.app.emotion_context import (
    build_chat_governance_snapshot,
    resolve_chat_emotion_context,
)
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_GRAPH_NODE_ORDER,
    CHAT_INTENT_SOURCE_NODE_ID,
    CHAT_RENDER_NODE_ID,
    HOST_CHAT_ROUTE_EDGE_3B,
    HOST_CHAT_ROUTE_SKILLS,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_TYPE_AUTO,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    build_unified_route_decision,
    build_chat_graph_config,
    resolve_chat_retrieval_gate,
    resolve_host_llm_runtime,
)
from host_brain.app.observability_persist import (
    persist_chat_episodic_memory,
    persist_chat_observability_rows,
)
from host_brain.app.chat_context_builder import (
    build_chat_context_pack as _build_chat_context_pack,
    build_chat_generation_prompt_payload as _build_chat_generation_prompt_payload,
    build_node_style_bindings as _build_node_style_bindings,
    extract_chat_graph_gateway_payload as _extract_chat_graph_gateway_payload,
    extract_chat_graph_node_entry as _extract_chat_graph_node_entry,
    normalize_node_style_bindings as _normalize_node_style_bindings,
)
from host_brain.app.prompt_builder import (
    _truncate_for_prompt,
    extract_gateway_narrative,
    infer_render_target_lang,
    repair_utf8_latin1_mojibake,
)
from host_brain.app.schemas import ChatRequest, ChatResponse
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.core.errors import (
    EDGE_RPC_AUTH_INVALID,
    EDGE_RPC_FAILED,
    SERVICE_UNAVAILABLE_EDGE_RPC,
    VOICE_FALLBACK_UNAVAILABLE,
)
from host_brain.core.ids import new_request_id
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.integrations.llm_helpers import (
    PROVIDER_GEMINI,
    adjust_gen_params_for_emotion,
)
from host_brain.retrieval.orchestrator import (
    RetrievalOrchestratorRequest,
    build_skipped_retrieval_summary,
)

_logger = logging.getLogger("Natsume.chat_handler")
_EDGE_GAMING_STAGE = "S_EDGE_RPC_GAMING_CHAT"
_EDGE_GAMING_PROVIDER = "edge.fallback_llm"
_EDGE_GAMING_ROUTE_REASON = "HOST_GAMING_EDGE_CHAT"
_EDGE_GAMING_ROUTE_FAILED_REASON = "HOST_GAMING_EDGE_ROUTE_FAILED"
_EDGE_GAMING_EPISODIC_SKIPPED_REASON = "HOST_GAMING_EDGE_CHAT_MINIMAL_PATH"
_EDGE_GAMING_DEFAULT_REPLY = "收到，游戏中我先简短回应。"


def _log_context_pack_observability(
    *,
    request_id: str,
    session_id: str,
    context_pack_observability: Mapping[str, Any],
) -> None:
    if not isinstance(context_pack_observability, Mapping) or not context_pack_observability:
        return

    selected_tier = str(context_pack_observability.get("selected_tier") or "").strip()
    selected_reason = str(context_pack_observability.get("selected_reason") or "").strip()
    retrieval_metrics_raw = context_pack_observability.get("retrieval_metrics")
    retrieval_metrics = dict(retrieval_metrics_raw) if isinstance(retrieval_metrics_raw, Mapping) else {}
    source_stats_raw = context_pack_observability.get("source_stats")
    source_stats = dict(source_stats_raw) if isinstance(source_stats_raw, Mapping) else {}
    slot_counts_raw = context_pack_observability.get("slot_item_counts")
    slot_counts = dict(slot_counts_raw) if isinstance(slot_counts_raw, Mapping) else {}

    _logger.info(
        "chat context pack observability",
        extra={
            "request_id": request_id,
            "session_id": session_id,
            "selected_tier": selected_tier or None,
            "selected_reason": selected_reason or None,
            "retrieval_status": retrieval_metrics.get("status"),
            "retrieval_path": retrieval_metrics.get("path"),
            "retrieval_route_count": retrieval_metrics.get("route_count"),
            "retrieval_candidate_count": retrieval_metrics.get("candidate_count"),
            "retrieval_rerank_topk": retrieval_metrics.get("rerank_topk"),
            "retrieval_cache_hit": retrieval_metrics.get("cache_hit"),
            "retrieval_cache_age_ms": retrieval_metrics.get("cache_age_ms"),
            "retrieval_used_items": source_stats.get("retrieval_used_items"),
            "retrieval_trimmed_items": source_stats.get("retrieval_trimmed_items"),
            "context_slot_counts": slot_counts,
            "context_size_chars": context_pack_observability.get("context_size_chars"),
        },
    )

    if selected_tier and selected_tier != "six_route":
        _logger.warning(
            "chat context degrade tier selected",
            extra={
                "request_id": request_id,
                "session_id": session_id,
                "selected_tier": selected_tier,
                "selected_reason": selected_reason or None,
                "degrade_order": list(context_pack_observability.get("degrade_order") or []),
            },
        )


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _resolve_edge_rpc_base_url() -> str:
    override = str(os.environ.get("HOST_EDGE_RPC_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    host = str(os.environ.get("EDGE_RPC_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port_raw = str(os.environ.get("EDGE_RPC_PORT") or "17800").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 17800
    port = max(1, min(port, 65535))
    return f"http://{host}:{port}"


def _resolve_edge_rpc_timeout_ms() -> int:
    raw = str(os.environ.get("HOST_GAMING_EDGE_RPC_TIMEOUT_MS") or "1200").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 1200
    return max(200, min(value, 10_000))


def _post_edge_fallback_rpc(
    *,
    request_id: str,
    endpoint_path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    base_url = _resolve_edge_rpc_base_url()
    timeout_ms = _resolve_edge_rpc_timeout_ms()
    allow_insecure_auth = _parse_bool(
        os.environ.get("HOST_EDGE_RPC_ALLOW_INSECURE_AUTH"),
        default=_parse_bool(os.environ.get("EDGE_ALLOW_INSECURE_AUTH"), default=True),
    )
    shared_key = str(
        os.environ.get("HOST_EDGE_RPC_SHARED_KEY")
        or os.environ.get("EDGE_HMAC_SHARED_KEY")
        or ""
    ).strip()
    ts_ms = int(time.time() * 1000)
    nonce = f"nonce::{request_id}::{uuid.uuid4().hex[:8]}"
    envelope_payload = dict(payload or {})

    if allow_insecure_auth:
        signature = "insecure"
    else:
        if not shared_key:
            return {
                "success": False,
                "status": "REJECTED",
                "reason_code": EDGE_RPC_AUTH_INVALID,
                "detail": "edge rpc shared key missing while insecure auth disabled",
                "event": {"stage": _EDGE_GAMING_STAGE},
                "ts_ms": ts_ms,
            }
        signature = build_edge_rpc_signature(
            request_id=request_id,
            ts_ms=ts_ms,
            nonce=nonce,
            payload=envelope_payload,
            shared_key=shared_key,
        )

    envelope = {
        "request_id": request_id,
        "ts_ms": ts_ms,
        "nonce": nonce,
        "signature": signature,
        "payload": envelope_payload,
    }
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    endpoint = f"{base_url}{endpoint_path}"
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "NatsumeHostEdgeFallback/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_ms) / 1000.0) as resp:
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.URLError as exc:
        return {
            "success": False,
            "status": "REJECTED",
            "reason_code": SERVICE_UNAVAILABLE_EDGE_RPC,
            "detail": f"edge rpc unavailable: {exc}",
            "event": {"stage": _EDGE_GAMING_STAGE},
            "ts_ms": ts_ms,
        }
    except Exception as exc:  # pragma: no cover - defensive network guard
        return {
            "success": False,
            "status": "REJECTED",
            "reason_code": EDGE_RPC_FAILED,
            "detail": f"edge rpc call failed: {exc}",
            "event": {"stage": _EDGE_GAMING_STAGE},
            "ts_ms": ts_ms,
        }

    if not isinstance(parsed, dict):
        parsed = {}
    event = dict(parsed.get("event") or {})
    event.setdefault("stage", _EDGE_GAMING_STAGE)
    return {
        "success": bool(parsed.get("success")),
        "status": str(parsed.get("status") or "REJECTED"),
        "reason_code": (str(parsed.get("reason_code") or "").strip() or None),
        "detail": str(parsed.get("detail") or "edge rpc response"),
        "event": event,
        "ts_ms": int(parsed.get("ts_ms") or ts_ms),
    }


def _build_gaming_edge_narrative(*, draft: Mapping[str, Any]) -> str:
    summary = str(draft.get("summary") or "").strip()
    if not summary:
        summary = str(draft.get("compressed_context") or "").strip()
    if not summary:
        note = str(draft.get("note") or "").strip()
        if note:
            summary = note
    if not summary:
        keywords = [str(x).strip() for x in list(draft.get("keywords") or []) if str(x).strip()]
        if keywords:
            summary = "，".join(keywords[:3])
    if not summary:
        return _EDGE_GAMING_DEFAULT_REPLY
    compact = " ".join(summary.split())
    return _truncate_for_prompt(compact, limit=80) or _EDGE_GAMING_DEFAULT_REPLY


def _run_gaming_edge_chat(
    *,
    request_id: str,
    session_id: str,
    message: str,
) -> dict[str, Any]:
    started_ms = int(time.time() * 1000)
    presence = _post_edge_fallback_rpc(
        request_id=request_id,
        endpoint_path="/v1/edge/fallback/presence",
        payload={"request_hint": "gaming_chat", "seq": 1},
    )
    presence_event = dict(presence.get("event") or {})
    if not bool(presence.get("success")):
        return {
            "success": False,
            "reason_code": str(presence.get("reason_code") or SERVICE_UNAVAILABLE_EDGE_RPC),
            "detail": str(presence.get("detail") or "edge presence call failed"),
            "presence": presence,
        }
    if not bool(presence_event.get("available", False)):
        return {
            "success": False,
            "reason_code": str(
                presence.get("reason_code")
                or VOICE_FALLBACK_UNAVAILABLE
            ),
            "detail": str(presence.get("detail") or "edge fallback llm unavailable"),
            "presence": presence,
        }

    compile_result = _post_edge_fallback_rpc(
        request_id=request_id,
        endpoint_path="/v1/edge/fallback/asr_compile",
        payload={
            "session_id": session_id,
            "text": str(message or ""),
            "mode": "gaming_chat",
            "seq": 2,
        },
    )
    if not bool(compile_result.get("success")):
        return {
            "success": False,
            "reason_code": str(compile_result.get("reason_code") or EDGE_RPC_FAILED),
            "detail": str(compile_result.get("detail") or "edge asr_compile failed"),
            "presence": presence,
            "compile": compile_result,
        }

    compile_event = dict(compile_result.get("event") or {})
    draft = dict(compile_event.get("draft") or {})
    narrative = _build_gaming_edge_narrative(draft=draft)
    return {
        "success": True,
        "reason_code": None,
        "detail": "gaming chat routed to edge fallback llm",
        "narrative": narrative,
        "model_id": str(compile_event.get("model_id") or "edge-fallback-3b"),
        "draft_only": bool(compile_event.get("draft_only", True)),
        "latency_ms": max(0, int(time.time() * 1000) - started_ms),
        "presence": presence,
        "compile": compile_result,
    }



async def handle_chat(request_body: ChatRequest, request: Request, metrics: Any) -> ChatResponse:
    """POST /v1/chat 核心处理函数。"""
    metrics.inc_chat()
    request_id = getattr(request.state, "request_id", None) or new_request_id()
    ts_ms = int(time.time() * 1000)
    session_id = request_body.session_id or f"session::{request_id}"

    try:
        intent_draft = request_body.intent_draft
        if intent_draft is None:
            intent_draft = {
                "schema_version": "Natsume.intent.v1",
                "id": f"intent::{request_id}",
                "ts_ms": ts_ms,
                "session_id": session_id,
                "seq": 1,
                "type": "query",
                "action": "narrative_only",
                "target": "host",
                "args": {"text": request_body.message},
                "nonce": f"nonce::{request_id}",
            }

        emotion_context = resolve_chat_emotion_context(request_body=request_body)
        governance_snapshot = build_chat_governance_snapshot(
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )

        explicit_scene_type = str(request_body.scene_type or "").strip().upper()
        scene_type_for_retrieval = explicit_scene_type or SCENE_CHAT
        sys_mode = str(os.environ.get("HOST_SYS_MODE") or "normal").strip().lower() or "normal"
        gaming_edge_chat_enabled = _parse_bool(
            os.environ.get("HOST_GAMING_EDGE_CHAT_ENABLED"),
            default=False,
        )
        host_edge_rpc_enabled = _parse_bool(
            os.environ.get("HOST_ENABLE_EDGE_RPC"),
            default=True,
        )
        use_gaming_edge_chat = bool(
            sys_mode == "gaming"
            and gaming_edge_chat_enabled
            and host_edge_rpc_enabled
            and scene_type_for_retrieval == SCENE_CHAT
        )
        retrieval_enabled, retrieval_disabled_reason = resolve_chat_retrieval_gate(
            scene_type=scene_type_for_retrieval
        )
        if use_gaming_edge_chat:
            retrieval_enabled = False
            retrieval_disabled_reason = _EDGE_GAMING_ROUTE_REASON
        graph_scene_type = explicit_scene_type or SCENE_TYPE_AUTO
        graph_scene_type_source = "explicit" if explicit_scene_type else "graph_entry_router"

        engine = get_intent_engine()

        _gov_task = asyncio.create_task(
            asyncio.to_thread(
                engine.evaluate_intent,
                raw_intent=intent_draft,
                request_id=request_id,
                thread_id=request_body.thread_id,
                governance_snapshot=governance_snapshot,
            )
        )

        _retrieval_task = None
        if retrieval_enabled:
            try:
                _retr_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "5").strip())
            except ValueError:
                _retr_top_k = 5
            _retr_top_k = max(1, min(_retr_top_k, 20))
            _retr_emotion = str(emotion_context.get("emotion_tag") or "") or None
            _retr_orchestrator = get_retrieval_orchestrator()
            _retrieval_task = asyncio.create_task(
                _retr_orchestrator.arun(
                    request=RetrievalOrchestratorRequest(
                        trace_id=request_id,
                        query=request_body.message,
                        session_state={
                            "session_id": session_id,
                            "thread_id": request_body.thread_id,
                            "scene_type": scene_type_for_retrieval,
                        },
                        request_id=request_id,
                        session_id=session_id,
                        top_k=_retr_top_k,
                        ts_ms=ts_ms,
                        emotion_context=_retr_emotion,
                    )
                )
            )

        governor_result = await _gov_task

        decision = governor_result.get("decision")
        reason_code = governor_result.get("reason_code")
        intent_id = governor_result.get("intent_id")
        approval_token_ref = None
        tokens = governor_result.get("tokens")
        if isinstance(tokens, dict):
            approval_token_ref = tokens.get("approval_token_ref")
        _committed_token_detail = governor_result.get("committed_route_token")
        approval_tier: Optional[str] = None
        if isinstance(_committed_token_detail, dict):
            approval_tier = _committed_token_detail.get("approval_tier")

        retrieval_summary: Dict[str, Any] = {}
        llm_summary: Dict[str, Any] = {}
        episodic_summary: Dict[str, Any] = {
            "status": "SKIPPED",
            "reason_code": "NOT_APPROVED",
            "memory_id": None,
            "detail": "governor decision is not APPROVE",
        }

        if decision == DECISION_APPROVE:
            if use_gaming_edge_chat:
                _logger.info(
                    "chat route switch: gaming mode -> edge fallback llm",
                    extra={"request_id": request_id, "session_id": session_id, "sys_mode": sys_mode},
                )
                edge_chat_result = await asyncio.to_thread(
                    _run_gaming_edge_chat,
                    request_id=request_id,
                    session_id=session_id,
                    message=request_body.message,
                )
                if bool(edge_chat_result.get("success")):
                    narrative = str(edge_chat_result.get("narrative") or "").strip() or _EDGE_GAMING_DEFAULT_REPLY
                    edge_route_decision = build_unified_route_decision(
                        scene_type=SCENE_CHAT,
                        scene_type_source="gaming_mode_edge_route",
                        route_target=HOST_CHAT_ROUTE_EDGE_3B,
                        route_reason_code=_EDGE_GAMING_ROUTE_REASON,
                        next_node="edge_fallback_asr_compile",
                        router_node="gaming_edge_route",
                        provider_name=_EDGE_GAMING_PROVIDER,
                        model_id=str(edge_chat_result.get("model_id") or "edge-fallback-3b"),
                        sys_mode=sys_mode,
                    )
                    retrieval_summary = {
                        "status": "SKIPPED",
                        "reason_code": _EDGE_GAMING_ROUTE_REASON,
                        "route_count": 0,
                        "routes": [],
                        "route_rows": [],
                        "route_errors": [],
                        "candidate_count": 0,
                        "rerank_topk": 0,
                        "evidence_refs": [],
                        "reason_chain": [],
                        "degraded": False,
                    }
                    llm_summary = {
                        "status": "OK",
                        "reason_code": None,
                        "stage": _EDGE_GAMING_STAGE,
                        "provider": _EDGE_GAMING_PROVIDER,
                        "model_id": str(edge_chat_result.get("model_id") or "edge-fallback-3b"),
                        "model_profile_id": "edge_gaming_3b_minimal",
                        "scene_type": SCENE_CHAT,
                        "scene_type_source": "gaming_mode_edge_route",
                        "render_target_lang": infer_render_target_lang(user_message=request_body.message),
                        "latency_ms": edge_chat_result.get("latency_ms"),
                        "draft_only": bool(edge_chat_result.get("draft_only", True)),
                        "route_target": HOST_CHAT_ROUTE_EDGE_3B,
                        "route_reason_code": _EDGE_GAMING_ROUTE_REASON,
                        "route_decision": edge_route_decision,
                        "node_count": 1,
                        "nodes": [
                            {
                                "node_id": "edge_fallback_asr_compile",
                                "status": "OK",
                                "reason_code": None,
                                "provider": _EDGE_GAMING_PROVIDER,
                                "model_id": str(edge_chat_result.get("model_id") or "edge-fallback-3b"),
                                "input": {
                                    "user_message": request_body.message,
                                    "sys_mode": sys_mode,
                                    "route_target": "edge_3b",
                                    "minimal_reply": True,
                                },
                                "output": {
                                    "narrative": narrative,
                                    "draft_only": bool(edge_chat_result.get("draft_only", True)),
                                },
                                "narrative_preview": _truncate_for_prompt(narrative, limit=120),
                            }
                        ],
                        "model_control": [
                            {
                                "route": "gaming_edge_3b",
                                "sys_mode": sys_mode,
                                "minimal_reply": True,
                            }
                        ],
                        "context_pack_validation": {},
                        "context_pack_observability": {},
                    }
                    quality = {"stage": "S_COMPLETE", "reason_code": None}
                    episodic_summary = {
                        "status": "SKIPPED",
                        "reason_code": _EDGE_GAMING_EPISODIC_SKIPPED_REASON,
                        "memory_id": None,
                        "detail": "gaming edge route keeps minimal path and skips episodic persistence",
                    }
                    try:
                        persist_chat_observability_rows(
                            ledger=get_ledger(),
                            request_id=request_id,
                            session_id=session_id,
                            llm_summary=llm_summary,
                            ts_ms=ts_ms,
                            fallback_reason_code=None,
                        )
                    except Exception as exc:
                        _logger.warning(
                            "chat observability persistence failed (gaming edge route)",
                            extra={"request_id": request_id, "error": str(exc)},
                        )
                    return ChatResponse(
                        request_id=request_id,
                        narrative=narrative,
                        intent_draft=intent_draft,
                        decision=decision,
                        reason_code=reason_code,
                        approval_token_ref=approval_token_ref,
                        approval_tier=approval_tier,
                        quality=quality,
                        audit_refs={
                            "intent_id": intent_id,
                            "governor_stage": governor_result.get("stage"),
                            "thread_id": request_body.thread_id,
                            "retrieval": retrieval_summary,
                            "llm": llm_summary,
                            "context_pack_validation": llm_summary.get("context_pack_validation"),
                            "context_pack_observability": llm_summary.get("context_pack_observability"),
                            "scene_type": llm_summary.get("scene_type"),
                            "scene_type_source": llm_summary.get("scene_type_source"),
                            "route_decision": edge_route_decision,
                            "emotion_context": dict(emotion_context),
                            "episodic_memory": dict(episodic_summary),
                            "edge_route": {
                                "route": "gaming_edge_3b",
                                "presence": dict(edge_chat_result.get("presence") or {}),
                                "compile": dict(edge_chat_result.get("compile") or {}),
                            },
                        },
                        timestamp=ts_ms,
                    )

                edge_failure_reason = str(
                    edge_chat_result.get("reason_code") or SERVICE_UNAVAILABLE_EDGE_RPC
                )
                edge_failure_detail = str(edge_chat_result.get("detail") or "edge route failed")
                _logger.warning(
                    "gaming edge route failed; fallback to host graph",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "reason_code": edge_failure_reason,
                        "detail": edge_failure_detail,
                    },
                )
                retrieval_disabled_reason = _EDGE_GAMING_ROUTE_FAILED_REASON

            retrieval_evidence_items: list[dict[str, Any]] = []
            if retrieval_enabled and _retrieval_task is not None:
                retrieval_result = await _retrieval_task
                retrieval_summary = dict(retrieval_result.retrieval_summary)
                retrieval_evidence_items = list(retrieval_result.retrieval_evidence_items)
            else:
                retrieval_summary = build_skipped_retrieval_summary(
                    str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")
                )
                retrieval_summary["degraded"] = (
                    str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")
                    not in {"HOST_RETRIEVAL_DISABLED", "HOST_CHAT_RETRIEVAL_DISABLED"}
                )

            render_target_lang = infer_render_target_lang(user_message=request_body.message)
            node_style_bindings = _build_node_style_bindings(render_target_lang=render_target_lang)
            llm_prompt_payload = _build_chat_generation_prompt_payload(
                request_id=request_id,
                session_id=session_id,
                thread_id=request_body.thread_id,
                user_message=request_body.message,
                render_target_lang=render_target_lang,
                retrieval_summary=retrieval_summary,
                retrieval_evidence_items=retrieval_evidence_items,
                style_bindings=node_style_bindings,
                emotion_context=emotion_context if emotion_context else None,
            )
            if emotion_context:
                llm_prompt_payload = adjust_gen_params_for_emotion(llm_prompt_payload, emotion_context)
            context_pack_validation = dict(llm_prompt_payload.get("context_pack_validation") or {})
            context_pack_observability = dict(llm_prompt_payload.get("context_pack_observability") or {})
            _log_context_pack_observability(
                request_id=request_id,
                session_id=session_id,
                context_pack_observability=context_pack_observability,
            )

            llm_runtime = resolve_host_llm_runtime()
            provider_name = str(llm_runtime["provider_name"])
            model_id = str(llm_runtime["model_id"])
            timeout_ms = int(llm_runtime["timeout_ms"])
            llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
            available = bool(llm_runtime["available"])
            provider_fn = llm_runtime["provider_fn"]
            cloud_provider_name = (
                str(llm_runtime.get("cloud_provider_name") or PROVIDER_GEMINI).strip().lower() or PROVIDER_GEMINI
            )
            cloud_model_id = str(llm_runtime.get("cloud_model_id") or "").strip()
            cloud_timeout_ms = int(llm_runtime.get("cloud_timeout_ms") or timeout_ms)
            cloud_llm_api_key = str(llm_runtime.get("cloud_llm_api_key") or "").strip() or None
            cloud_available = bool(llm_runtime.get("cloud_available", available))
            chat_base_url_override = str(llm_runtime.get("chat_base_url_override") or "").strip() or None
            cloud_base_url_override = str(llm_runtime.get("cloud_base_url_override") or "").strip() or None
            graph_config = build_chat_graph_config(
                provider_name=provider_name,
                model_id=model_id,
                timeout_ms=timeout_ms,
                available=available,
                api_key=llm_api_key,
                provider_fn=provider_fn,
                scene_type=graph_scene_type,
                scene_type_source=graph_scene_type_source,
                render_target_lang=render_target_lang,
                cloud_provider_name=cloud_provider_name,
                cloud_model_id=cloud_model_id or None,
                cloud_timeout_ms=cloud_timeout_ms,
                cloud_api_key=cloud_llm_api_key,
                cloud_available=cloud_available,
                chat_base_url_override=chat_base_url_override,
                cloud_base_url_override=cloud_base_url_override,
            )
            dependency_statuses: dict[str, dict[str, Any]] = {}
            for node_id in CHAT_GRAPH_NODE_ORDER:
                is_chat_node = node_id == CHAT_DIRECT_NODE_ID
                node_available = available if is_chat_node else cloud_available
                dependency_statuses[node_id] = {
                    "available": node_available,
                    "detail": (
                        "chat node runtime ready"
                        if is_chat_node and node_available
                        else (
                            "cloud node runtime ready"
                            if (not is_chat_node and node_available)
                            else "model node runtime unavailable"
                        )
                    ),
                }
            dependency_statuses[provider_name] = {
                "available": available,
                "detail": ("provider auth ready" if llm_api_key else "provider key missing"),
                "api_key": (llm_api_key or None),
            }
            if cloud_provider_name not in dependency_statuses:
                dependency_statuses[cloud_provider_name] = {
                    "available": cloud_available,
                    "detail": ("provider auth ready" if cloud_llm_api_key else "provider key missing"),
                    "api_key": cloud_llm_api_key,
                }
            if PROVIDER_GEMINI not in dependency_statuses:
                gemini_key = str(os.environ.get("GEMINI_API_KEY") or "").strip() or None
                dependency_statuses[PROVIDER_GEMINI] = {
                    "available": bool(gemini_key),
                    "detail": ("provider auth ready" if gemini_key else "provider key missing"),
                    "api_key": gemini_key,
                }

            graph_start_ms = int(time.time() * 1000)
            graph_result = get_graph_runtime().run_graph(
                request_id=request_id,
                graph_config=graph_config,
                payload={
                    **llm_prompt_payload,
                    "user_message": request_body.message,
                    "scene_type": (explicit_scene_type or None),
                    "committed_route_token": governor_result.get("committed_route_token_ref"),
                    "committed_route_token_detail": governor_result.get("committed_route_token"),
                },
                dependency_statuses=dependency_statuses,
                ts_ms=graph_start_ms,
            )

            graph_output = dict(graph_result.output or {})
            payload_root = dict(graph_output.get("payload") or {})
            graph_meta = dict(graph_config.get("graph_meta") or {})
            runtime_scene_type = (
                str(payload_root.get("scene_type") or graph_meta.get("scene_type") or scene_type_for_retrieval)
                .strip()
                .upper()
                or SCENE_CHAT
            )
            runtime_scene_type_source = (
                str(payload_root.get("scene_type_source") or graph_meta.get("scene_type_source") or graph_scene_type_source)
                .strip()
                or graph_scene_type_source
            )
            default_route_next_node = CHAT_DIRECT_NODE_ID
            if runtime_scene_type == SCENE_WORK_TRANSLATE:
                default_route_next_node = "workbench_translate"
            elif runtime_scene_type == SCENE_WORK_CODE:
                default_route_next_node = "workbench_code_assist"
            elif runtime_scene_type == SCENE_EXEC_INTENT:
                default_route_next_node = "planner_constraints"
            route_decision_payload_raw = payload_root.get("route_decision")
            route_decision_payload = (
                dict(route_decision_payload_raw)
                if isinstance(route_decision_payload_raw, Mapping)
                else {}
            )
            runtime_route_target = str(route_decision_payload.get("route_target") or "").strip().lower()
            if not runtime_route_target:
                if runtime_scene_type == SCENE_EXEC_INTENT:
                    runtime_route_target = HOST_CHAT_ROUTE_SKILLS
                elif runtime_scene_type in {SCENE_WORK_TRANSLATE, SCENE_WORK_CODE}:
                    runtime_route_target = "cloud"
                else:
                    runtime_route_target = str(llm_runtime.get("route_target") or "cloud").strip().lower() or "cloud"
            is_chat_direct_path = runtime_scene_type == SCENE_CHAT
            chat_intent_node_id = CHAT_DIRECT_NODE_ID if is_chat_direct_path else CHAT_INTENT_SOURCE_NODE_ID
            chat_response_node_id = CHAT_DIRECT_NODE_ID if is_chat_direct_path else CHAT_RENDER_NODE_ID
            _core_node, core_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=chat_intent_node_id)
            secondary_node, secondary_payload = _extract_chat_graph_gateway_payload(graph_output, node_id="core_reasoner_secondary")
            render_node, render_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=chat_response_node_id)
            gateway_status = str(render_node.get("gateway_status") or "").strip().upper()
            gateway_reason = str(render_node.get("reason_code") or "").strip() or None
            route_decision = build_unified_route_decision(
                scene_type=str(route_decision_payload.get("scene_type") or runtime_scene_type),
                scene_type_source=str(route_decision_payload.get("scene_type_source") or runtime_scene_type_source),
                route_target=runtime_route_target,
                route_reason_code=str(
                    route_decision_payload.get("route_reason_code")
                    or llm_runtime.get("route_reason_code")
                    or ""
                ),
                next_node=str(route_decision_payload.get("next_node") or default_route_next_node),
                router_node=str(route_decision_payload.get("router_node") or ""),
                provider_name=str(render_node.get("provider") or provider_name),
                model_id=str(render_node.get("model_id") or model_id),
                sys_mode=str(llm_runtime.get("sys_mode") or ""),
            )

            core_gateway_status = str(_core_node.get("gateway_status") or "").strip().upper()
            secondary_gateway_status = str(secondary_node.get("gateway_status") or "").strip().upper()
            render_narrative = extract_gateway_narrative(render_payload)
            core_narrative = extract_gateway_narrative(core_payload)
            secondary_narrative = extract_gateway_narrative(secondary_payload)

            primary_degraded = core_gateway_status not in {"OK", ""}
            effective_core_narrative = core_narrative
            if primary_degraded and secondary_narrative and secondary_gateway_status == "OK":
                effective_core_narrative = secondary_narrative

            narrative = render_narrative
            if not narrative and secondary_narrative and secondary_gateway_status == "OK" and primary_degraded:
                narrative = secondary_narrative
            if not narrative:
                narrative = effective_core_narrative or core_narrative or secondary_narrative
            narrative = repair_utf8_latin1_mojibake(narrative)
            if not narrative:
                narrative = "系统有点忙，请稍后再试。"

            core_intent_draft = core_payload.get("intent_draft")
            if primary_degraded and secondary_gateway_status == "OK":
                secondary_intent = secondary_payload.get("intent_draft")
                if isinstance(secondary_intent, dict) and secondary_intent:
                    core_intent_draft = secondary_intent
            if isinstance(core_intent_draft, dict) and core_intent_draft:
                intent_draft = core_intent_draft

            style_bindings_payload_raw = llm_prompt_payload.get("style_bindings")
            style_bindings_payload = (
                dict(style_bindings_payload_raw)
                if isinstance(style_bindings_payload_raw, Mapping)
                else {}
            )
            graph_trace_rows = list(graph_output.get("graph_trace") or [])
            active_node_ids: list[str] = []
            for row in graph_trace_rows:
                if not isinstance(row, Mapping):
                    continue
                node_id = str(row.get("node_id") or "").strip()
                if node_id and node_id not in active_node_ids:
                    active_node_ids.append(node_id)
            if not active_node_ids:
                active_node_ids = [
                    str(node_id).strip()
                    for node_id in list(graph_meta.get("active_nodes") or [])
                    if str(node_id).strip()
                ]
            observed_nodes = (
                list(CHAT_GRAPH_NODE_ORDER)
                + ["scene_router_entry", "scene_router_post_core"]
            )
            for node_id in observed_nodes:
                if node_id in active_node_ids:
                    continue
                if isinstance(payload_root.get(node_id), Mapping):
                    active_node_ids.append(node_id)
            if not active_node_ids:
                active_node_ids = [chat_intent_node_id]
            for node_id in CHAT_GRAPH_NODE_ORDER:
                if node_id in active_node_ids:
                    continue
                if isinstance(payload_root.get(node_id), Mapping):
                    active_node_ids.append(node_id)

            node_summaries: list[dict[str, Any]] = []
            for node_index, node_id in enumerate(active_node_ids):
                node_entry, node_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=node_id)
                node_status = str(node_entry.get("gateway_status") or "").strip().upper() or "UNKNOWN"
                node_provider = node_entry.get("provider")
                node_model_id = node_entry.get("model_id")
                if (
                    node_status == "UNKNOWN"
                    and not node_provider
                    and not node_model_id
                    and not node_payload
                ):
                    # scene router handler 等非 LLM 节点不写入 llm_node_call_log，避免污染可观测数据。
                    continue
                node_input: dict[str, Any] = {
                    "user_message": (request_body.message if node_index == 0 else None),
                    "retrieval_status": retrieval_summary.get("status"),
                    "retrieval_route_count": retrieval_summary.get("route_count"),
                    "upstream_node_ids": active_node_ids[:node_index],
                }
                node_style_raw = style_bindings_payload.get(node_id)
                node_style = dict(node_style_raw) if isinstance(node_style_raw, Mapping) else {}
                if node_style:
                    node_input["style_canon_id"] = node_style.get("canon_id")
                    node_input["style_citation"] = node_style.get("citation")
                if node_id == CHAT_DIRECT_NODE_ID:
                    node_input["response_mode"] = "direct_final_reply"
                if node_id == CHAT_RENDER_NODE_ID and not is_chat_direct_path:
                    node_input["core_narrative_zh"] = effective_core_narrative
                    node_input["core_intent_draft"] = core_intent_draft
                node_summaries.append(
                    {
                        "node_id": node_id,
                        "status": node_status,
                        "reason_code": (str(node_entry.get("reason_code") or "").strip() or None),
                        "provider": node_provider,
                        "model_id": node_model_id,
                        "input": node_input,
                        "output": node_payload,
                        "narrative_preview": _truncate_for_prompt(
                            repair_utf8_latin1_mojibake(extract_gateway_narrative(node_payload)),
                            limit=120,
                        ),
                    }
                )

            llm_summary = {
                "status": (gateway_status or str(graph_result.status)),
                "reason_code": (gateway_reason or graph_result.reason_code),
                "stage": str(graph_result.stage),
                "provider": (render_node.get("provider") or provider_name),
                "model_id": (render_node.get("model_id") or model_id),
                "model_profile_id": graph_output.get("model_profile_id"),
                "scene_type": runtime_scene_type,
                "scene_type_source": runtime_scene_type_source,
                "render_target_lang": str(llm_prompt_payload.get("render_target_lang") or ""),
                "latency_ms": None,
                "draft_only": bool(dict(render_payload.get("meta") or {}).get("draft_only", True)),
                "route_target": str(route_decision.get("route_target") or llm_runtime.get("route_target") or "cloud"),
                "route_reason_code": str(
                    route_decision.get("route_reason_code") or llm_runtime.get("route_reason_code") or ""
                ),
                "route_decision": route_decision,
                "node_count": len(node_summaries),
                "nodes": node_summaries,
                "model_control": list(graph_output.get("model_control") or []),
                "context_pack_validation": context_pack_validation,
                "context_pack_observability": context_pack_observability,
            }

            retrieval_reason_code = str(retrieval_summary.get("reason_code") or "")
            if retrieval_summary.get("degraded") and retrieval_reason_code not in {"", "HOST_RETRIEVAL_DISABLED", "HOST_CHAT_RETRIEVAL_DISABLED"}:
                reason_code = reason_code or retrieval_reason_code
            graph_ok = str(graph_result.status).upper() == "OK"
            renderer_ok = gateway_status == "OK"
            if not graph_ok or not renderer_ok:
                reason_code = reason_code or gateway_reason or str(graph_result.reason_code or "") or "LLM_UNAVAILABLE"
                quality = {"stage": str(graph_result.stage), "reason_code": reason_code}
            else:
                quality = {"stage": "S_COMPLETE", "reason_code": None}

            try:
                persist_chat_observability_rows(
                    ledger=get_ledger(),
                    request_id=request_id,
                    session_id=session_id,
                    llm_summary=llm_summary,
                    ts_ms=ts_ms,
                    fallback_reason_code=(reason_code or gateway_reason or graph_result.reason_code),
                )
            except Exception as exc:
                _logger.warning("chat observability persistence failed", extra={"request_id": request_id, "error": str(exc)})

            try:
                _etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
                _intensity = float(emotion_context.get("emotion_intensity") or 0.5)
                _update_kind = BondDynamicsWriter.classify_interaction(
                    text=request_body.message,
                    emotion_intensity=_intensity,
                )
                _bond_writer = BondDynamicsWriter(
                    ledger=get_ledger(),
                    stage_engine=get_bond_stage_engine(),
                )
                _bond_writer.apply_update(
                    request_id=request_id,
                    intent_id=str(intent_id or request_id),
                    session_id=session_id,
                    seq=1,
                    user_id=session_id,
                    topic_id=str(intent_draft.get("args", {}).get("text", request_body.message))[:64],
                    update_kind=_update_kind,
                    evidence_refs=[str(intent_id or request_id)],
                    emotion_tag=_etag,
                    emotion_intensity=_intensity,
                    ts_ms=ts_ms,
                )
            except Exception as exc:
                _logger.debug("bond emotion record skipped", extra={"request_id": request_id, "error": str(exc)})

            try:
                episodic_summary = persist_chat_episodic_memory(
                    request_id=request_id,
                    intent_id=str(intent_id or request_id),
                    session_id=session_id,
                    user_message=request_body.message,
                    assistant_reply=narrative,
                    emotion_context=emotion_context,
                    ts_ms=ts_ms,
                    episodic_store=get_episodic_store(),
                )
            except Exception as exc:
                episodic_summary = {
                    "status": "DEGRADED",
                    "reason_code": "EPISODIC_WRITE_FAILED",
                    "memory_id": None,
                    "detail": str(exc),
                }
                _logger.debug("episodic memory write skipped", extra={"request_id": request_id, "error": str(exc)})
        else:
            narrative = f"[REJECTED] reason: {reason_code or 'UNKNOWN'}"
            quality = {"stage": governor_result.get("stage", "S_GOV_REJECT"), "reason_code": reason_code}
            metrics.inc_rejection(reason_code or "UNKNOWN")

        return ChatResponse(
            request_id=request_id,
            narrative=narrative,
            intent_draft=intent_draft,
            decision=decision,
            reason_code=reason_code,
            approval_token_ref=approval_token_ref,
            approval_tier=approval_tier,
            quality=quality,
            audit_refs={
                "intent_id": intent_id,
                "governor_stage": governor_result.get("stage"),
                "thread_id": request_body.thread_id,
                "retrieval": retrieval_summary,
                "llm": llm_summary,
                "context_pack_validation": llm_summary.get("context_pack_validation"),
                "context_pack_observability": llm_summary.get("context_pack_observability"),
                "scene_type": llm_summary.get("scene_type"),
                "scene_type_source": llm_summary.get("scene_type_source"),
                "route_decision": llm_summary.get("route_decision"),
                "emotion_context": dict(emotion_context),
                "episodic_memory": dict(episodic_summary),
            },
            timestamp=ts_ms,
        )

    except Exception as exc:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        metrics.inc_error()
        _logger.error(
            "Chat request failed",
            extra={"request_id": request_id, "error": str(exc), "traceback": _tb_str},
        )
        # 写入 error_log 方便诊断
        try:
            get_ledger().log_error(
                request_id=request_id,
                stage="S_INTERNAL_ERROR",
                reason_code="INTERNAL_SERVER_ERROR",
                detail=f"{type(exc).__name__}: {exc}\n{_tb_str[:800]}",
            )
        except Exception:
            pass
        return ChatResponse(
            request_id=request_id,
            narrative="系统有点忙，请稍后再试。",
            intent_draft=None,
            decision="REJECT",
            reason_code="INTERNAL_SERVER_ERROR",
            approval_token_ref=None,
            approval_tier=None,
            quality={"stage": "S_INTERNAL_ERROR", "reason_code": "INTERNAL_SERVER_ERROR"},
            audit_refs={"request_id": request_id},
            timestamp=ts_ms,
        )
