"""Chat 核心业务逻辑模块。
从 fastapi_app.py 拆分，负责 POST /v1/chat 的核心编排逻辑。"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Mapping, Optional

from fastapi import Request
from host_brain.app.dependencies import (
    get_bond_stage_engine,
    get_episodic_store,
    get_graph_runtime,
    get_intent_engine,
    get_ledger,
    get_retrieval_pipeline,
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
    SCENE_CHAT,
    build_chat_graph_config,
    resolve_chat_retrieval_gate,
    resolve_chat_scene_type,
    resolve_host_llm_runtime,
    resolve_scene_node_sequence,
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
from host_brain.core.ids import new_request_id
from host_brain.governor.schemas import DECISION_APPROVE
from host_brain.integrations.llm_helpers import (
    PROVIDER_GEMINI,
    adjust_gen_params_for_emotion,
)

_logger = logging.getLogger("Natsume.chat_handler")



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

        scene_type, scene_type_source = resolve_chat_scene_type(request_body=request_body)
        retrieval_enabled, retrieval_disabled_reason = resolve_chat_retrieval_gate(scene_type=scene_type)

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
            _retr_pipeline = get_retrieval_pipeline()
            _retr_emotion = str(emotion_context.get("emotion_tag") or "") or None
            _retrieval_task = asyncio.create_task(
                asyncio.to_thread(
                    _retr_pipeline.run,
                    request_id=request_id,
                    query=request_body.message,
                    intent_id=None,
                    session_id=session_id,
                    top_k=_retr_top_k,
                    ts_ms=ts_ms,
                    emotion_context=_retr_emotion,
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
            retrieval_evidence_items: list[dict[str, Any]] = []
            if retrieval_enabled and _retrieval_task is not None:
                retrieval_result = await _retrieval_task
                recall_output = dict(retrieval_result.output.get("recall") or {})
                rerank_output = dict(retrieval_result.output.get("rerank") or {})
                evidence_pack = dict(retrieval_result.output.get("evidence_pack") or {})
                rerank_topk_items = list(rerank_output.get("topk") or [])
                for item in rerank_topk_items[:5]:
                    if not isinstance(item, dict):
                        continue
                    snippet = _truncate_for_prompt(item.get("snippet"), limit=240)
                    if not snippet:
                        continue
                    retrieval_evidence_items.append(
                        {
                            "evidence_ref": str(item.get("evidence_ref") or "").strip() or str(item.get("doc_id") or ""),
                            "source_ref": str(item.get("source_ref") or "").strip(),
                            "snippet": snippet,
                            "score": item.get("score"),
                            "confidence": item.get("confidence"),
                        }
                    )
                retrieval_summary = {
                    "status": retrieval_result.status,
                    "reason_code": retrieval_result.reason_code,
                    "route_count": len(recall_output.get("routes") or []),
                    "routes": list(recall_output.get("routes") or []),
                    "route_rows": list(recall_output.get("route_rows") or []),
                    "route_errors": list(recall_output.get("route_errors") or []),
                    "candidate_count": int(recall_output.get("candidate_count") or 0),
                    "rerank_topk": len(rerank_topk_items),
                    "evidence_refs": list(evidence_pack.get("evidence_refs") or []),
                    "reason_chain": list(retrieval_result.output.get("reason_chain") or []),
                    "degraded": bool(retrieval_result.output.get("degraded", False)),
                }
            else:
                retrieval_summary = {
                    "status": "SKIPPED",
                    "reason_code": str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED"),
                    "route_count": 0, "routes": [], "route_rows": [], "route_errors": [],
                    "candidate_count": 0, "rerank_topk": 0, "evidence_refs": [], "reason_chain": [],
                    "degraded": str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")
                    not in {"HOST_RETRIEVAL_DISABLED", "HOST_CHAT_RETRIEVAL_DISABLED"},
                }

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

            llm_runtime = resolve_host_llm_runtime()
            provider_name = str(llm_runtime["provider_name"])
            model_id = str(llm_runtime["model_id"])
            timeout_ms = int(llm_runtime["timeout_ms"])
            llm_api_key = str(llm_runtime.get("llm_api_key") or "").strip()
            available = bool(llm_runtime["available"])
            provider_fn = llm_runtime["provider_fn"]
            graph_config = build_chat_graph_config(
                provider_name=provider_name,
                model_id=model_id,
                timeout_ms=timeout_ms,
                available=available,
                api_key=llm_api_key,
                provider_fn=provider_fn,
                scene_type=scene_type,
                scene_type_source=scene_type_source,
                render_target_lang=render_target_lang,
            )
            dependency_statuses: dict[str, dict[str, Any]] = {
                node_id: {
                    "available": available,
                    "detail": ("model node runtime ready" if available else "model node runtime unavailable"),
                }
                for node_id in CHAT_GRAPH_NODE_ORDER
            }
            dependency_statuses[PROVIDER_GEMINI] = {
                "available": available,
                "detail": ("provider auth ready" if llm_api_key else "provider key missing"),
                "api_key": (llm_api_key or None),
            }
            if provider_name != PROVIDER_GEMINI:
                dependency_statuses[provider_name] = {
                    "available": available,
                    "detail": ("provider auth ready" if llm_api_key else "provider key missing"),
                    "api_key": (llm_api_key or None),
                }

            graph_start_ms = int(time.time() * 1000)
            graph_result = get_graph_runtime().run_graph(
                request_id=request_id,
                graph_config=graph_config,
                payload={
                    **llm_prompt_payload,
                    "committed_route_token": governor_result.get("committed_route_token_ref"),
                    "committed_route_token_detail": governor_result.get("committed_route_token"),
                },
                dependency_statuses=dependency_statuses,
                ts_ms=graph_start_ms,
            )

            graph_output = dict(graph_result.output or {})
            is_chat_direct_path = str(scene_type or "").strip().upper() == SCENE_CHAT
            chat_intent_node_id = CHAT_DIRECT_NODE_ID if is_chat_direct_path else CHAT_INTENT_SOURCE_NODE_ID
            chat_response_node_id = CHAT_DIRECT_NODE_ID if is_chat_direct_path else CHAT_RENDER_NODE_ID
            _core_node, core_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=chat_intent_node_id)
            secondary_node, secondary_payload = _extract_chat_graph_gateway_payload(graph_output, node_id="core_reasoner_secondary")
            render_node, render_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=chat_response_node_id)
            gateway_status = str(render_node.get("gateway_status") or "").strip().upper()
            gateway_reason = str(render_node.get("reason_code") or "").strip() or None

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
            graph_meta = dict(graph_config.get("graph_meta") or {})
            active_node_ids = [
                str(node_id).strip()
                for node_id in list(graph_meta.get("active_nodes") or [])
                if str(node_id).strip() in CHAT_GRAPH_NODE_ORDER
            ]
            payload_root = dict(graph_output.get("payload") or {})
            for node_id in CHAT_GRAPH_NODE_ORDER:
                if node_id in active_node_ids:
                    continue
                if isinstance(payload_root.get(node_id), Mapping):
                    active_node_ids.append(node_id)
            if not active_node_ids:
                active_node_ids = list(resolve_scene_node_sequence(scene_type))

            node_summaries: list[dict[str, Any]] = []
            for node_index, node_id in enumerate(active_node_ids):
                node_entry, node_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=node_id)
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
                        "status": str(node_entry.get("gateway_status") or "").strip().upper() or "UNKNOWN",
                        "reason_code": (str(node_entry.get("reason_code") or "").strip() or None),
                        "provider": node_entry.get("provider"),
                        "model_id": node_entry.get("model_id"),
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
                "render_target_lang": str(llm_prompt_payload.get("render_target_lang") or ""),
                "latency_ms": None,
                "draft_only": bool(dict(render_payload.get("meta") or {}).get("draft_only", True)),
                "node_count": len(node_summaries),
                "nodes": node_summaries,
                "model_control": list(graph_output.get("model_control") or []),
                "context_pack_validation": dict(llm_prompt_payload.get("context_pack_validation") or {}),
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
                "emotion_context": dict(emotion_context),
                "episodic_memory": dict(episodic_summary),
            },
            timestamp=ts_ms,
        )

    except Exception as exc:
        metrics.inc_error()
        _logger.error("Chat request failed", extra={"request_id": request_id, "error": str(exc)})
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
