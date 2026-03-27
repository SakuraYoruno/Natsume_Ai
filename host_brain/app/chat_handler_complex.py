"""复杂场景 SSE 生成器模块。

从 chat_handler.py 拆分，包含 WORK_CODE / WORK_TRANSLATE / EXEC_INTENT 等
复杂场景的 LangGraph 多节点编排逻辑：
  - _complex_scene_sse_generator：复杂场景 SSE 生成器
    节点级进度通知：NODE_START → NODE_DONE × N → COMMIT(narrative)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, Mapping, Optional

from host_brain.app.chat_context_builder import (
    build_chat_prompt_runtime_payload as _build_chat_prompt_runtime_payload,
    extract_chat_graph_gateway_payload as _extract_chat_graph_gateway_payload,
)
from host_brain.app.chat_runtime_input import (
    build_chat_runtime_input,
    get_chat_runtime_prompt_context_observability,
    get_chat_runtime_prompt_context_style_row,
    get_chat_runtime_prompt_context_validation,
    get_chat_runtime_graph,
    get_chat_runtime_render_target_lang,
)
from host_brain.app.dependencies import (
    get_bond_stage_engine,
    get_bond_update_queue,
    get_episodic_store,
    get_graph_runtime,
    get_ledger,
    get_retrieval_orchestrator,
)
from host_brain.app.emotion_context import build_chat_emotion_snapshot
from host_brain.app.execution_profile import (
    PROFILE_CHAT_FAST,
    PROFILE_INTENT_EXEC,
    PROFILE_WORKFLOW_SUPERVISED,
    resolve_execution_profile,
)
from host_brain.app.entry_routing import is_narrative_only_fast_pass
from host_brain.app.graph_config_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_GRAPH_NODE_ORDER,
    CHAT_INTENT_SOURCE_NODE_ID,
    CHAT_RENDER_NODE_ID,
    HOST_CHAT_ROUTE_SKILLS,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    build_chat_graph,
    build_intent_graph,
    build_supervised_graph,
    build_unified_route_decision,
    resolve_chat_retrieval_gate,
    resolve_host_llm_runtime_async as resolve_host_llm_runtime,  # 修复审计问题 #1：使用异步版本
)
from host_brain.app.observability_persist import (
    persist_chat_episodic_memory,
    persist_chat_observability_rows,
)
from host_brain.app.prompt_builder import (
    _truncate_for_prompt,
    extract_gateway_narrative,
    infer_render_target_lang,
    repair_utf8_latin1_mojibake,
)
from host_brain.app.schemas import ChatRequest
from host_brain.bond.bond_dynamics import BondDynamicsWriter
from host_brain.integrations.llm_helpers import (
    PROVIDER_GEMINI,
    adjust_gen_params_for_emotion,
)
from host_brain.retrieval.orchestrator import (
    RetrievalOrchestratorRequest,
    build_degraded_retrieval_summary,
    build_skipped_retrieval_summary,
)

_logger = logging.getLogger("Natsume.chat_handler")


def _coerce_request_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _log_prompt_context_observability(
    *,
    request_id: str,
    session_id: str,
    prompt_context_observability: Mapping[str, Any],
) -> None:
    if not isinstance(prompt_context_observability, Mapping) or not prompt_context_observability:
        return

    selected_tier = str(prompt_context_observability.get("selected_tier") or "").strip()
    selected_reason = str(prompt_context_observability.get("selected_reason") or "").strip()
    retrieval_metrics_raw = prompt_context_observability.get("retrieval_metrics")
    retrieval_metrics = dict(retrieval_metrics_raw) if isinstance(retrieval_metrics_raw, Mapping) else {}
    source_stats_raw = prompt_context_observability.get("source_stats")
    source_stats = dict(source_stats_raw) if isinstance(source_stats_raw, Mapping) else {}
    slot_counts_raw = prompt_context_observability.get("slot_item_counts")
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
            "context_size_chars": prompt_context_observability.get("context_size_chars"),
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
                "degrade_order": list(prompt_context_observability.get("degrade_order") or []),
            },
        )


async def _complex_scene_sse_generator(
    *,
    request_body: ChatRequest,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Dict[str, Any],
    scene_type: str,
    scene_type_source: str,
    governor_result: Dict[str, Any],
    emotion_context: Dict[str, Any],
    emotion_snapshot: Dict[str, Any],
    intent_id: Any,
    reason_code: Any,
    metrics: Any,
) -> AsyncGenerator[str, None]:
    """复杂场景 SSE 生成器（WORK_CODE / WORK_TRANSLATE / EXEC_INTENT）。

    节点级进度通知：
      NODE_START(node_id) → NODE_DONE(node_id, status) × N → COMMIT(narrative)
    """
    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    retrieval_latency_ms: int | None = None
    background_tasks_latency_ms: dict[str, int] = {"total_ms": 0}
    trace_metrics: dict[str, Any] = {
        "retrieval_latency_ms": None,
        "background_tasks_latency_ms": dict(background_tasks_latency_ms),
    }

    try:
        retrieval_enabled, retrieval_disabled_reason = resolve_chat_retrieval_gate(
            scene_type=scene_type
        )

        graph_scene_type = scene_type
        graph_scene_type_source = scene_type_source
        render_target_lang = infer_render_target_lang(user_message=request_body.message)

        _retrieval_task = None
        _retrieval_started_ms: int | None = None
        retrieval_init_failed_reason: str | None = None
        if retrieval_enabled:
            try:
                _retr_top_k = int(str(os.environ.get("HOST_RETR_TOP_K") or "8").strip())
            except ValueError:
                _retr_top_k = 8
            _retr_top_k = max(1, min(_retr_top_k, 20))
            _retr_emotion = str(emotion_context.get("emotion_tag") or "") or None
            _retrieval_started_ms = int(time.time() * 1000)
            try:
                _retr_orchestrator = get_retrieval_orchestrator()
            except Exception as exc:
                retrieval_enabled = False
                retrieval_init_failed_reason = "RETRIEVAL_INIT_FAILED"
                retrieval_disabled_reason = retrieval_init_failed_reason
                retrieval_latency_ms = max(0, int(time.time() * 1000) - _retrieval_started_ms)
                _logger.warning(
                    "retrieval orchestrator init failed; continue without retrieval",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "reason_code": retrieval_init_failed_reason,
                        "error": str(exc),
                    },
                )
            else:
                _retrieval_task = asyncio.create_task(
                    _retr_orchestrator.arun(
                        request=RetrievalOrchestratorRequest(
                            trace_id=request_id,
                            query=request_body.message,
                            session_state={
                                "session_id": session_id,
                                "thread_id": request_body.thread_id,
                                "scene_type": scene_type,
                            },
                            request_id=request_id,
                            session_id=session_id,
                            top_k=_retr_top_k,
                            ts_ms=ts_ms,
                            emotion_context=_retr_emotion,
                            render_target_lang=render_target_lang,
                        )
                    )
                )

        decision = governor_result.get("decision")
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

        retrieval_evidence_items: list[dict[str, Any]] = []
        if retrieval_enabled and _retrieval_task is not None:
            try:
                retrieval_result = await _retrieval_task
                retrieval_summary = dict(retrieval_result.retrieval_summary)
                retrieval_evidence_items = list(retrieval_result.retrieval_evidence_items)
            except Exception as exc:
                retrieval_summary = build_degraded_retrieval_summary("RETRIEVAL_FAILED")
                retrieval_evidence_items = []
                _logger.warning(
                    "retrieval task failed; degrade chat retrieval path",
                    extra={
                        "request_id": request_id,
                        "session_id": session_id,
                        "reason_code": "RETRIEVAL_FAILED",
                        "error": str(exc),
                    },
                )
            finally:
                if _retrieval_started_ms is not None:
                    retrieval_latency_ms = max(0, int(time.time() * 1000) - _retrieval_started_ms)
        elif retrieval_init_failed_reason:
            retrieval_summary = build_degraded_retrieval_summary(retrieval_init_failed_reason)
        else:
            retrieval_summary = build_skipped_retrieval_summary(
                str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")
            )
            retrieval_summary["degraded"] = (
                str(retrieval_disabled_reason or "HOST_RETRIEVAL_DISABLED")
                not in {"HOST_RETRIEVAL_DISABLED", "HOST_CHAT_RETRIEVAL_DISABLED"}
            )
        if retrieval_latency_ms is not None:
            retrieval_summary["latency_ms"] = int(retrieval_latency_ms)

        emotion_snapshot = build_chat_emotion_snapshot(
            emotion_context=emotion_context,
            source="chat_pre_llm",
            ts_ms=int(time.time() * 1000),
        )
        # EXEC_INTENT 场景需要把可用 skill 清单注入运行时输入，供路由与执行规划使用。
        _skill_manifest_for_graph: list[dict] = []
        if scene_type == SCENE_EXEC_INTENT:
            try:
                from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
                _skill_manifest_for_graph = get_skill_registry().build_skill_manifest(max_risk_level=3)
            except Exception as _sm_exc:
                _logger.warning("[%s] skill_manifest 构建失败（降级为空列表）: %s", request_id, _sm_exc)

        runtime_input = build_chat_runtime_input(
            request_id=request_id,
            session_id=session_id,
            thread_id=request_body.thread_id,
            user_message=request_body.message,
            render_target_lang=render_target_lang,
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            emotion_state=emotion_context if emotion_context else None,
            emotion_snapshot=emotion_snapshot,
            scene_type=scene_type,
            scene_type_source=scene_type_source,
            committed_route_token=governor_result.get("committed_route_token_ref"),
            committed_route_token_detail=governor_result.get("committed_route_token"),
            skill_manifest=_skill_manifest_for_graph,
        )
        llm_prompt_payload = _build_chat_prompt_runtime_payload(
            runtime_input=runtime_input,
        )
        if emotion_context:
            llm_prompt_payload = adjust_gen_params_for_emotion(llm_prompt_payload, emotion_context)
        prompt_context_validation = get_chat_runtime_prompt_context_validation(llm_prompt_payload)
        prompt_context_observability = get_chat_runtime_prompt_context_observability(llm_prompt_payload)
        _log_prompt_context_observability(
            request_id=request_id,
            session_id=session_id,
            prompt_context_observability=prompt_context_observability,
        )

        llm_runtime = await resolve_host_llm_runtime(
            rollout_key=session_id,
            scene_type=scene_type,
        )
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
        # §14.2：narrative_only = fast-pass 命中；classifier_says_exec = 分类器/规则明确判定为执行型
        narrative_only = (
            graph_scene_type == SCENE_CHAT
            and is_narrative_only_fast_pass(request_body.message)
        )
        classifier_says_exec = graph_scene_type == SCENE_EXEC_INTENT
        execution_profile = resolve_execution_profile(
            graph_scene_type,
            narrative_only=narrative_only,
            classifier_says_exec=classifier_says_exec,
        )
        if execution_profile == PROFILE_INTENT_EXEC:
            build_graph_fn = build_intent_graph
        elif execution_profile == PROFILE_WORKFLOW_SUPERVISED:
            build_graph_fn = build_supervised_graph
        else:
            build_graph_fn = build_chat_graph
        graph_config = build_graph_fn(
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
        graph_node_ids = [b["node_id"] for b in graph_config.get("node_bindings", []) if b.get("node_id")]
        for node_id in graph_node_ids:
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

        # ── 节点级进度通知：通知客户端 LangGraph 开始执行 ─────────────────
        from host_brain.app.graph_config_builder import resolve_scene_node_sequence  # noqa: PLC0415
        graph_meta = graph_config.get("graph_meta") or {}
        active_scene_nodes = graph_meta.get("active_nodes") or resolve_scene_node_sequence(scene_type)
        yield _sse({
            "phase": "GRAPH_START",
            "scene_type": scene_type,
            "node_sequence": active_scene_nodes,
            "node_count": len(active_scene_nodes),
            "request_id": request_id,
        })

        graph_start_ms = int(time.time() * 1000)
        graph_payload = {**dict(llm_prompt_payload), "execution_profile": execution_profile}
        graph_result = await asyncio.to_thread(
            get_graph_runtime().run_graph,
            request_id=request_id,
            graph_config=graph_config,
            payload=graph_payload,
            dependency_statuses=dependency_statuses,
            ts_ms=graph_start_ms,
        )
        graph_latency_ms = max(0, int(time.time() * 1000) - graph_start_ms)

        # ── 节点级进度通知：通知客户端 LangGraph 执行完成 ─────────────────
        yield _sse({
            "phase": "GRAPH_DONE",
            "scene_type": scene_type,
            "status": str(graph_result.status),
            "latency_ms": graph_latency_ms,
            "request_id": request_id,
        })

        graph_output = dict(graph_result.output or {})
        payload_root = dict(graph_output.get("payload") or {})
        graph_payload = get_chat_runtime_graph(payload_root)
        graph_meta = dict(graph_config.get("graph_meta") or {})
        runtime_scene_type = (
            str(graph_payload.get("scene_type") or graph_meta.get("scene_type") or scene_type)
            .strip().upper() or SCENE_CHAT
        )
        runtime_scene_type_source = (
            str(graph_payload.get("scene_type_source") or graph_meta.get("scene_type_source") or graph_scene_type_source)
            .strip() or graph_scene_type_source
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
            narrative = "啊啦，这边好像稍微有点忙不过来了呢。不要太着急哦，先稍微休息一下，喝口茶，等会儿再来试吧？"

        core_intent_draft = core_payload.get("intent_draft")
        if primary_degraded and secondary_gateway_status == "OK":
            secondary_intent = secondary_payload.get("intent_draft")
            if isinstance(secondary_intent, dict) and secondary_intent:
                core_intent_draft = secondary_intent
        if isinstance(core_intent_draft, dict) and core_intent_draft:
            intent_draft = core_intent_draft

        graph_trace_rows = list(graph_output.get("graph_trace") or [])
        active_node_ids: list[str] = []
        for row in graph_trace_rows:
            if not isinstance(row, Mapping):
                continue
            nid = str(row.get("node_id") or "").strip()
            if nid and nid not in active_node_ids:
                active_node_ids.append(nid)
        if not active_node_ids:
            active_node_ids = [
                str(nid).strip()
                for nid in list(graph_meta.get("active_nodes") or [])
                if str(nid).strip()
            ]
        observed_nodes = list(CHAT_GRAPH_NODE_ORDER) + ["scene_router_entry", "scene_router_post_core"]
        for nid in observed_nodes:
            if nid in active_node_ids:
                continue
            if isinstance(payload_root.get(nid), Mapping):
                active_node_ids.append(nid)
        if not active_node_ids:
            active_node_ids = [chat_intent_node_id]
        for nid in CHAT_GRAPH_NODE_ORDER:
            if nid in active_node_ids:
                continue
            if isinstance(payload_root.get(nid), Mapping):
                active_node_ids.append(nid)

        node_summaries: list[dict[str, Any]] = []
        for node_index, nid in enumerate(active_node_ids):
            node_entry, node_payload = _extract_chat_graph_gateway_payload(graph_output, node_id=nid)
            node_status = str(node_entry.get("gateway_status") or "").strip().upper() or "UNKNOWN"
            node_provider = node_entry.get("provider")
            node_model_id = node_entry.get("model_id")
            if (
                node_status == "UNKNOWN"
                and not node_provider
                and not node_model_id
                and not node_payload
            ):
                continue
            node_input: dict[str, Any] = {
                "user_message": (request_body.message if node_index == 0 else None),
                "retrieval_status": retrieval_summary.get("status"),
                "retrieval_route_count": retrieval_summary.get("route_count"),
                "upstream_node_ids": active_node_ids[:node_index],
            }
            node_style = get_chat_runtime_prompt_context_style_row(
                llm_prompt_payload,
                node_id=nid,
            )
            if node_style:
                node_input["style_canon_id"] = node_style.get("canon_id")
                node_input["style_citation"] = node_style.get("citation")
            if nid == CHAT_DIRECT_NODE_ID:
                node_input["response_mode"] = "direct_final_reply"
            if nid == CHAT_RENDER_NODE_ID and not is_chat_direct_path:
                node_input["core_narrative_zh"] = effective_core_narrative
                node_input["core_intent_draft"] = core_intent_draft
            node_summaries.append({
                "node_id": nid,
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
            })

        llm_summary = {
            "status": (gateway_status or str(graph_result.status)),
            "reason_code": (gateway_reason or graph_result.reason_code),
            "stage": str(graph_result.stage),
            "provider": (render_node.get("provider") or provider_name),
            "model_id": (render_node.get("model_id") or model_id),
            "model_profile_id": graph_output.get("model_profile_id"),
            "scene_type": runtime_scene_type,
            "scene_type_source": runtime_scene_type_source,
            "execution_profile": execution_profile,
            "graph_entry_node": str(graph_config.get("entry_node") or "").strip() or None,
            "render_target_lang": get_chat_runtime_render_target_lang(llm_prompt_payload),
            "latency_ms": graph_latency_ms,
            "draft_only": bool(dict(render_payload.get("meta") or {}).get("draft_only", True)),
            "route_target": str(route_decision.get("route_target") or llm_runtime.get("route_target") or "cloud"),
            "route_reason_code": str(
                route_decision.get("route_reason_code") or llm_runtime.get("route_reason_code") or ""
            ),
            "route_decision": route_decision,
            "emotion_snapshot": dict(emotion_snapshot),
            "node_count": len(node_summaries),
            "nodes": node_summaries,
            "model_control": list(graph_output.get("model_control") or []),
            "prompt_context_validation": prompt_context_validation,
            "prompt_context_observability": prompt_context_observability,
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

        _obs_persist_started_ms = int(time.time() * 1000)
        obs_persist_latency_ms = 0
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
        finally:
            obs_persist_latency_ms = max(0, int(time.time() * 1000) - _obs_persist_started_ms)

        _bond_started_ms = int(time.time() * 1000)
        bond_latency_ms = 0
        bond_queue_status = "SKIPPED"
        bond_queue_reason: str | None = None
        try:
            _etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            _intensity = float(emotion_context.get("emotion_intensity") or 0.5)
            _update_kind = BondDynamicsWriter.classify_interaction(
                text=request_body.message,
                emotion_intensity=_intensity,
            )
            _bond_payload = {
                "request_id": request_id,
                "intent_id": str(intent_id or request_id),
                "session_id": session_id,
                "seq": 1,
                "user_id": session_id,
                "topic_id": str(intent_draft.get("args", {}).get("text", request_body.message))[:64],
                "capability_hint": "bond_memory",
                "update_kind": _update_kind,
                "evidence_refs": [str(intent_id or request_id)],
                "emotion_tag": _etag,
                "emotion_intensity": _intensity,
                "ts_ms": ts_ms,
            }
            _bond_queue = get_bond_update_queue()
            _queue_result = _bond_queue.enqueue(_bond_payload)
            bond_queue_status = str(_queue_result.get("status") or "UNKNOWN")
            bond_queue_reason = str(_queue_result.get("reason_code") or "").strip() or None
            if bond_queue_status != "QUEUED":
                _bond_writer = BondDynamicsWriter(
                    ledger=get_ledger(),
                    stage_engine=get_bond_stage_engine(),
                )
                _bond_writer.apply_update(**_bond_payload)
                bond_queue_status = "DIRECT_FALLBACK_APPLIED"
        except Exception as exc:
            _logger.debug("bond emotion record skipped", extra={"request_id": request_id, "error": str(exc)})
            if not bond_queue_reason:
                bond_queue_reason = str(exc)
        finally:
            bond_latency_ms = max(0, int(time.time() * 1000) - _bond_started_ms)

        _episodic_started_ms = int(time.time() * 1000)
        episodic_latency_ms = 0
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
        finally:
            episodic_latency_ms = max(0, int(time.time() * 1000) - _episodic_started_ms)

        # ── 修复审计问题 #7：复杂场景补充 VTS 情感驱动 ─────────────────────
        from host_brain.app.chat_stream_handler import _drive_stream_vts_emotion  # noqa: PLC0415
        _vts_started_ms = int(time.time() * 1000)
        vts_summary: dict[str, Any] = {"status": "SKIPPED", "reason_code": "NOT_ATTEMPTED"}
        suppress_edge_vts = _coerce_request_bool(
            getattr(request_body, "suppress_edge_vts", None),
            default=False,
        )
        if suppress_edge_vts:
            vts_summary = {"status": "SKIPPED", "reason_code": "SUPPRESSED_BY_INPUT_POLICY"}
        else:
            try:
                vts_summary = await _drive_stream_vts_emotion(
                    request_id=request_id,
                    emotion_context=emotion_context,
                    ts_ms=ts_ms,
                )
            except Exception as exc:
                vts_summary = {"status": "DEGRADED", "reason_code": str(exc)}
                _logger.debug("complex scene VTS drive skipped", extra={"request_id": request_id, "error": str(exc)})
        vts_latency_ms = max(0, int(time.time() * 1000) - _vts_started_ms)

        background_tasks_latency_ms = {
            "observability_persist_ms": obs_persist_latency_ms,
            "bond_update_ms": bond_latency_ms,
            "episodic_memory_ms": episodic_latency_ms,
            "vts_emotion_ms": vts_latency_ms,
            "total_ms": obs_persist_latency_ms + bond_latency_ms + episodic_latency_ms + vts_latency_ms,
        }
        trace_metrics = {
            "retrieval_latency_ms": retrieval_latency_ms,
            "background_tasks_latency_ms": dict(background_tasks_latency_ms),
            "bond_queue": {
                "status": bond_queue_status,
                "reason_code": bond_queue_reason,
            },
        }

        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": narrative,
            "request_id": request_id,
            "audit_refs": {
                "intent_id": intent_id,
                "governor_stage": governor_result.get("stage"),
                "thread_id": request_body.thread_id,
                "retrieval": retrieval_summary,
                "llm": llm_summary,
                "prompt_context_validation": llm_summary.get("prompt_context_validation"),
                "prompt_context_observability": llm_summary.get("prompt_context_observability"),
                "scene_type": llm_summary.get("scene_type"),
                "scene_type_source": llm_summary.get("scene_type_source"),
                "route_decision": llm_summary.get("route_decision"),
                "emotion_context": dict(emotion_context),
                "emotion_snapshot": dict(emotion_snapshot),
                "vts": vts_summary,
                "episodic_memory": dict(episodic_summary),
                "trace_metrics": trace_metrics,
            },
        })

    except Exception as exc:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        if metrics is not None:
            try:
                metrics.inc_error()
            except Exception:
                pass
        _logger.error(
            "complex scene SSE generator failed",
            extra={"request_id": request_id, "error": str(exc), "traceback": _tb_str},
        )
        try:
            get_ledger().log_error(
                request_id=request_id,
                stage="S_INTERNAL_ERROR",
                reason_code="INTERNAL_SERVER_ERROR",
                detail=f"{type(exc).__name__}: {exc}\n{_tb_str[:800]}",
            )
        except Exception:
            pass
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "INTERNAL_SERVER_ERROR",
            "narrative": "啊啦，这边好像稍微有点忙不过来了呢。不要太着急哦，先稍微休息一下，喝口茶，等会儿再来试吧？",
            "request_id": request_id,
        })
