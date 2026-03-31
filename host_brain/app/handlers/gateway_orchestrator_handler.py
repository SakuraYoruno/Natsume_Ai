"""Gateway Orchestrator Handler — SCENE_GATEWAY 专用 SSE 生成器。

职责：
- 接管所有 Gateway（微信/QQ/飞书/企微）输入的回复生成
- 内部控制 style canon 选择：纯文字→cn，需要语音→文字仍 cn + TTS 走 ja
- 意图识别复用 pre_intent_classifier
- 非 CHAT 场景委托给对应复杂场景处理器
- TTS 行为：suppress_local_play=True, suppress_vts=True（不驱动本地扬声器/VTS）
- 遵守 SSE 事件协议：SCENE_ROUTED → TOKEN → SENTENCE → COMMIT

完整链路（Phase XIV v4）：
  RecallFusion 预召回 → StateArbiter 查询（bond 为主，world_canon/style_canon/episodic 辅助）
  → RecallPackBuilder slot 分配 → build_chat_runtime_input → _build_stream_chat_prompt
  → adjust_gen_params_for_emotion → astream_llm_chunks → SentenceSplitter
  → 响应内容分类 → 情节记忆写入 → Bond 动态写入 → COMMIT

安全保证：
- 上游已经过 Airlock → Governor → RecallFusion 等前置安全节点
- 下游经过 响应内容分类 → 情节记忆写入 → Bond 更新 → 审计

与 SCENE_CHAT 的差异点：
- render_target_lang 强制为 "zh"（中文回复）
- TTS 仅在 need_voice=True 时触发，且 suppress_local_play=True, suppress_vts=True
- 时效性要求较低（不追求首字 token 极速）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Dict, Mapping

from host_brain.bond.user_identity import resolve_bond_user_id

logger = logging.getLogger("Natsume.gateway_orchestrator")

# ---------------------------------------------------------------------------
# 环境变量解析辅助
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _parse_bool(val: Any, *, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    raw = str(val).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


_BRIEF_REPLY_TOKENS: tuple[str, ...] = (
    "简短",
    "简洁",
    "短一点",
    "一句",
    "一两句",
    "简要",
    "brief",
    "short",
    "one sentence",
)


def _user_requested_brief_reply(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in _BRIEF_REPLY_TOKENS)


def _augment_gateway_prompt_for_reply_depth(*, prompt_text: str, user_message: str) -> str:
    if not _parse_bool_env("HOST_GATEWAY_DETAIL_REPLY_ENABLED", default=True):
        return prompt_text
    if _user_requested_brief_reply(user_message):
        depth_policy = (
            "GATEWAY_REPLY_DEPTH_POLICY:\n"
            "- The user requested a brief answer.\n"
            "- Keep the visible reply concise (1-2 short sentences), but still answer directly.\n"
        )
    else:
        depth_policy = (
            "GATEWAY_REPLY_DEPTH_POLICY:\n"
            "- Answer directly first, then provide concise supporting detail.\n"
            "- Default to 2-4 short sentences for normal chat; avoid one-line shallow replies.\n"
            "- For ordinary QQ chat, do not stop at a bare acknowledgement; include at least one supporting clause.\n"
            "- Unless the user requested brevity, avoid replies shorter than about 12 Chinese characters.\n"
            "- If evidence is insufficient, state uncertainty and give one clear next step.\n"
            "- Avoid generic filler and repetitive greetings.\n"
        )
    return f"{prompt_text}\n\n{depth_policy}\n"


# ---------------------------------------------------------------------------
# 回复内容简单情感推断（与 chat_stream_handler 保持一致）
# ---------------------------------------------------------------------------

_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [
    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起"),
    ("ANGRY", "生气|气死|烦"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你"),
    ("SURPRISED", "哇|真的吗|竟然"),
]


def _infer_emotion_tag_for_tts(narrative: str, current_tag: str) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案简单推断 emotion_tag 供 TTS 使用。"""
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    for tag, pattern in _EMOTION_INFER_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


def _normalize_gateway_commit_narrative(*, raw_text: str, rendered_text: str = "") -> str:
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        extract_gateway_narrative,
        extract_json_object_from_text,
        repair_utf8_latin1_mojibake,
    )

    raw_narrative = repair_utf8_latin1_mojibake(str(raw_text or "").strip())
    rendered_narrative = repair_utf8_latin1_mojibake(str(rendered_text or "").strip())
    if raw_narrative:
        try:
            payload = extract_json_object_from_text(raw_narrative)
        except Exception:
            payload = None
        extracted = extract_gateway_narrative(payload)
        if extracted:
            return extracted
    return rendered_narrative or raw_narrative


# ---------------------------------------------------------------------------
# SSE 事件构建辅助
# ---------------------------------------------------------------------------

def _sse_event(data: dict[str, Any]) -> str:
    """构建标准 SSE 事件字符串。"""
    return f"data:{json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_scene_routed(request_id: str, scene: str, source: str) -> str:
    return _sse_event({
        "phase": "SCENE_ROUTED",
        "request_id": request_id,
        "scene_type": scene,
        "scene_type_source": source,
        "ts_ms": int(time.time() * 1000),
    })


def _sse_token(request_id: str, token: str, index: int) -> str:
    return _sse_event({
        "phase": "TOKEN",
        "request_id": request_id,
        "token": token,
        "index": index,
        "ts_ms": int(time.time() * 1000),
    })


def _sse_sentence(request_id: str, sentence: str, index: int) -> str:
    return _sse_event({
        "phase": "SENTENCE",
        "request_id": request_id,
        "sentence": sentence,
        "sentence_index": index,
        "ts_ms": int(time.time() * 1000),
    })


def _sse_commit(
    request_id: str,
    decision: str,
    narrative: str,
    *,
    reason_code: str = "",
    detail: str = "",
    tts_ref: str | None = None,
    audit_refs: dict[str, Any] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "phase": "COMMIT",
        "done": True,
        "request_id": request_id,
        "decision": decision,
        "narrative": narrative,
        "reason_code": reason_code,
        "detail": detail,
        "ts_ms": int(time.time() * 1000),
    }
    if tts_ref:
        payload["tts_ref"] = tts_ref
    if audit_refs:
        payload["audit_refs"] = audit_refs
    return _sse_event(payload)


def _build_gateway_llm_summary(
    *,
    node_id: str,
    provider_name: str,
    model_id: str,
    llm_runtime: Mapping[str, Any],
    resolved_message: str,
    narrative: str,
    retrieval_summary: Mapping[str, Any] | None,
    prompt_context_validation: Mapping[str, Any] | None,
    prompt_context_observability: Mapping[str, Any] | None,
    emotion_snapshot: Mapping[str, Any] | None,
    response_intelligence_meta: Mapping[str, Any] | None,
    llm_total_latency_ms: int,
    first_token_latency_ms: int | None,
    llm_failed: bool = False,
    failure_reason_code: str | None = None,
) -> dict[str, Any]:
    final_narrative = str(narrative or "")
    llm_status = "DEGRADED" if llm_failed else ("OK" if final_narrative.strip() else "DEGRADED")
    llm_reason_code = (
        str(failure_reason_code or "").strip()
        or (None if final_narrative.strip() else "LLM_EMPTY_OUTPUT")
    )
    route_target = str(llm_runtime.get("route_target") or "cloud")
    route_reason_code = str(llm_runtime.get("route_reason_code") or "")
    retrieval_status = None
    if isinstance(retrieval_summary, Mapping):
        retrieval_status = retrieval_summary.get("status")
    validation_payload = dict(prompt_context_validation or {})
    observability_payload = dict(prompt_context_observability or {})
    emotion_snapshot_payload = dict(emotion_snapshot or {})
    response_intelligence_payload = dict(response_intelligence_meta or {})
    return {
        "status": llm_status,
        "reason_code": llm_reason_code,
        "provider": provider_name,
        "model_id": model_id,
        "scene_type": "GATEWAY",
        "route_target": route_target,
        "route_reason_code": route_reason_code,
        "emotion_snapshot": emotion_snapshot_payload,
        "prompt_context_validation": validation_payload,
        "prompt_context_observability": observability_payload,
        "latency_ms": llm_total_latency_ms,
        "first_token_latency_ms": first_token_latency_ms,
        "node_count": 1,
        "nodes": [
            {
                "node_id": node_id,
                "status": llm_status,
                "reason_code": llm_reason_code,
                "provider": provider_name,
                "model_id": model_id,
                "latency_ms": llm_total_latency_ms,
                "input": {
                    "user_message": resolved_message,
                    "render_target_lang": "zh",
                    "retrieval_status": retrieval_status,
                    "prompt_context_validation": validation_payload,
                },
                "output": {
                    "narrative": final_narrative,
                    "emotion_snapshot": emotion_snapshot_payload,
                    "response_intelligence": response_intelligence_payload,
                },
            }
        ],
    }


# ---------------------------------------------------------------------------
# StateArbiter 查询（world_canon / style_canon / episodic / bond）
# Note: Gateway 场景仍需四路查询以构建完整上下文
# ---------------------------------------------------------------------------

async def _resolve_state_arbiter_all(
    *,
    session_id: str,
    request_id: str,
) -> tuple[
    dict[str, Any],  # retrieval_summary 片段
    list[dict[str, Any]],  # 注入的 evidence_items
    dict[str, Any],  # bond_summary（用于 runtime_input）
]:
    """并行查询 StateArbiter 四路事实，返回汇总的 retrieval_summary 和 evidence_items。"""
    from host_brain.app.state_arbiter_queries import (  # noqa: PLC0415
        _resolve_state_arbiter_world_fact,
        _resolve_state_arbiter_style_fact,
        _resolve_state_arbiter_episodic_fact,
        _resolve_state_arbiter_bond_fact,
    )

    (
        (world_summary, world_item),
        (style_summary, style_item),
        (episodic_summary, episodic_item),
        (bond_summary, bond_item),
    ) = await asyncio.gather(
        _resolve_state_arbiter_world_fact(session_id=session_id, request_id=request_id),
        _resolve_state_arbiter_style_fact(session_id=session_id, request_id=request_id),
        _resolve_state_arbiter_episodic_fact(session_id=session_id, request_id=request_id),
        _resolve_state_arbiter_bond_fact(session_id=session_id, request_id=request_id),
    )

    summary_fragment: dict[str, Any] = {
        "state_arbiter": dict(world_summary),
        "style_canon": dict(style_summary),
        "episodic_latest": dict(episodic_summary),
        "bond_graph": dict(bond_summary),
    }

    injected_items: list[dict[str, Any]] = []
    # 注入优先级：world_canon(1.0) > style_canon(0.85) > episodic_latest(0.75) > bond_graph(0.65)
    if world_item is not None:
        injected_items.append(world_item)
        summary_fragment["state_arbiter"]["injected"] = True
    if style_item is not None:
        injected_items.append(style_item)
        summary_fragment["style_canon"]["injected"] = True
    if episodic_item is not None:
        injected_items.append(episodic_item)
        summary_fragment["episodic_latest"]["injected"] = True
    if bond_item is not None:
        injected_items.append(bond_item)
        summary_fragment["bond_graph"]["injected"] = True

    return summary_fragment, injected_items, dict(bond_summary)


# ---------------------------------------------------------------------------
# RecallPack 构建（复用 chat_stream_handler 的构建逻辑）
# ---------------------------------------------------------------------------

def _build_recall_pack(
    retrieval_evidence_items: list[dict[str, Any]],
    request_id: str,
    retrieval_summary: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    """构建 RecallPack，返回 (rewritten_items, recall_slot_text, summary)。"""
    from host_brain.app.chat_retrieval_helpers import (  # noqa: PLC0415
        _build_recall_pack_inputs,
        _recall_pack_to_retrieval_evidence_items,
    )

    recall_slot_text: dict[str, str] = {}
    summary: dict[str, Any] = {}
    rewritten_items = list(retrieval_evidence_items)

    try:
        from host_brain.app.dependencies import get_recall_pack_builder  # noqa: PLC0415
        cpb = get_recall_pack_builder()
        evidence_items = list(retrieval_evidence_items or [])
        recall_results = _build_recall_pack_inputs(evidence_items)
        recall_pack_active = _parse_bool_env("HOST_RECALL_PACK_ACTIVE", default=True)
        slot_budget_overrides = dict(retrieval_summary or {}).get("slot_budget_overrides") or {}

        if recall_results:
            cp = cpb.build(
                recall_results=recall_results,
                task_type="chat",
                slot_budget_overrides=dict(slot_budget_overrides),
            )
            rw_items = _recall_pack_to_retrieval_evidence_items(cp)
            applied_to_prompt = False
            if recall_pack_active and rw_items:
                rewritten_items = rw_items
                applied_to_prompt = True
            # 提取 slot text
            if recall_pack_active:
                from host_brain.app.schemas.recall_pack import RECALL_SLOT_PRIORITY  # noqa: PLC0415
                for slot_name in RECALL_SLOT_PRIORITY:
                    slot = cp.get_slot(slot_name)
                    if slot and str(getattr(slot, "content", "") or "").strip():
                        recall_slot_text[slot_name] = str(slot.content).strip()
            summary = {
                "status": "OK",
                "mode": ("ACTIVE" if applied_to_prompt else "SHADOW"),
                "active_enabled": recall_pack_active,
                "applied_to_prompt": applied_to_prompt,
                "rewritten_item_count": len(rw_items),
                "input_item_count": len(evidence_items),
                "total_tokens": cp.total_token_count,
                "recall_slot_text_injected": list(recall_slot_text.keys()),
                "slot_budget_overrides": dict(slot_budget_overrides),
            }
        else:
            summary = {
                "status": "SKIPPED",
                "mode": "SHADOW",
                "active_enabled": recall_pack_active,
                "reason_code": "NO_RECALL_RESULTS",
                "input_item_count": len(evidence_items),
            }
    except Exception as exc:
        summary = {"status": "DEGRADED", "reason": str(exc)}
        logger.debug(
            "[%s] Gateway RecallPackBuilder 调用失败（降级）: %s",
            request_id, exc,
        )

    return rewritten_items, recall_slot_text, summary


# ---------------------------------------------------------------------------
# 后置任务：响应分类 / 情节记忆写入 / Bond 动态写入
# ---------------------------------------------------------------------------

async def _run_post_generation_tasks(
    *,
    request_id: str,
    session_id: str,
    intent_id: Any,
    narrative: str,
    user_message: str,
    emotion_context: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    """并行执行后置任务：Airlock 检疫 + 情节记忆写入 + Bond 动态写入。

    返回汇总的 audit_refs 字典。
    """

    async def _run_airlock() -> tuple[dict[str, Any], int]:
        t0 = int(time.time() * 1000)
        refs: dict[str, Any] = {}
        try:
            from host_brain.airlock.quarantine import classify_ingress  # noqa: PLC0415
            classification = await asyncio.to_thread(classify_ingress, narrative)
            if classification:
                refs = {
                    "ingress_classification": classification,
                    "quarantine_digest": None,
                    "airlock_mode": "OBSERVE_ONLY",
                }
        except Exception as exc:
            logger.warning(
                "[%s] Gateway 响应分类失败（降级）: %s",
                request_id, exc,
            )
        return refs, max(0, int(time.time() * 1000) - t0)

    async def _run_episodic() -> tuple[dict[str, Any], int]:
        t0 = int(time.time() * 1000)
        summary: dict[str, Any] = {}
        try:
            from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415
            from host_brain.app.observability_persist import persist_chat_episodic_memory  # noqa: PLC0415
            summary = await asyncio.to_thread(
                persist_chat_episodic_memory,
                request_id=request_id,
                intent_id=str(intent_id or request_id),
                session_id=session_id,
                user_message=user_message,
                assistant_reply=narrative,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
                episodic_store=get_episodic_store(),
            )
        except Exception as exc:
            summary = {
                "status": "DEGRADED",
                "reason_code": "EPISODIC_WRITE_FAILED",
                "memory_id": None,
                "detail": str(exc),
            }
            logger.debug(
                "[%s] Gateway 情节记忆写入失败（降级）: %s",
                request_id, exc,
            )
        return summary, max(0, int(time.time() * 1000) - t0)

    async def _run_bond() -> tuple[dict[str, Any], int]:
        t0 = int(time.time() * 1000)
        summary: dict[str, Any] = {"status": "SKIPPED", "reason_code": "NOT_ATTEMPTED"}
        try:
            from host_brain.app.dependencies import get_bond_stage_engine, get_bond_update_queue  # noqa: PLC0415
            from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: PLC0415

            etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
            intensity = float(emotion_context.get("emotion_intensity") or 0.5)
            update_kind = BondDynamicsWriter.classify_interaction(
                text=user_message,
                emotion_intensity=intensity,
            )
            bond_payload = {
                "request_id": request_id,
                "intent_id": str(intent_id or request_id),
                "session_id": session_id,
                "seq": 1,
                "user_id": resolve_bond_user_id(session_id=session_id),
                "topic_id": user_message[:64],
                "capability_hint": "bond_memory",
                "update_kind": update_kind,
                "evidence_refs": [str(intent_id or request_id)],
                "emotion_tag": etag,
                "emotion_intensity": intensity,
                "ts_ms": ts_ms,
            }
            bond_queue = get_bond_update_queue()
            queue_result = bond_queue.enqueue(bond_payload)
            queue_status = str(queue_result.get("status") or "UNKNOWN")
            if queue_status != "QUEUED":
                bond_writer = BondDynamicsWriter(
                    ledger=get_ledger(),
                    stage_engine=get_bond_stage_engine(),
                )
                await asyncio.to_thread(bond_writer.apply_update, **bond_payload)
                queue_status = "DIRECT_FALLBACK_APPLIED"
            summary = {"status": queue_status, "update_kind": update_kind}
        except Exception as exc:
            summary = {"status": "DEGRADED", "reason_code": str(exc)}
            logger.debug(
                "[%s] Gateway Bond 更新失败（降级）: %s",
                request_id, exc,
            )
        return summary, max(0, int(time.time() * 1000) - t0)

    # 并行执行所有后置任务
    (
        (airlock_refs, airlock_ms),
        (episodic_summary, episodic_ms),
        (bond_summary, bond_ms),
    ) = await asyncio.gather(
        _run_airlock(),
        _run_episodic(),
        _run_bond(),
    )

    return {
        "airlock": airlock_refs,
        "episodic_memory": episodic_summary,
        "bond": bond_summary,
        "latency_ms": {
            "airlock": airlock_ms,
            "episodic": episodic_ms,
            "bond": bond_ms,
        },
    }


# ---------------------------------------------------------------------------
# TTS 异步生成（可选，不阻塞主流程）
# ---------------------------------------------------------------------------

async def _generate_tts_async(
    *,
    narrative: str,
    request_id: str,
    session_id: str,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """异步生成 TTS 音频（suppress_local_play=True, suppress_vts=True）。

    返回 (audio_ref, tts_summary)。
    失败时返回 (None, degraded_summary)。
    """
    tts_enabled = _parse_bool_env("HOST_GATEWAY_TTS_ENABLED", default=False)
    if not tts_enabled:
        return None, {"status": "SKIPPED", "reason_code": "HOST_GATEWAY_TTS_DISABLED"}

    if not narrative or not narrative.strip():
        return None, {"status": "SKIPPED", "reason_code": "NARRATIVE_EMPTY"}

    try:
        from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
        from host_brain.app.intelligent_runtime import resolve_tts_route  # noqa: PLC0415

        base_etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        tts_route = resolve_tts_route(
            text=narrative,
            emotion_context={**dict(emotion_context), "emotion_tag": base_etag},
            bond_summary=bond_summary or {},
        )
        etag = str(tts_route.get("emotion_tag") or _infer_emotion_tag_for_tts(narrative, base_etag)).strip()
        intensity_raw = emotion_context.get("emotion_intensity")
        intensity = None
        if intensity_raw is not None:
            try:
                intensity = max(0.0, min(1.0, float(intensity_raw)))
            except (TypeError, ValueError):
                pass

        result = await post_speak(
            text=narrative,
            request_id=request_id,
            session_id=session_id,
            speed=float(tts_route.get("speed") or 1.0),
            pitch=float(tts_route.get("pitch") or 0.0),
            emotion_tag=etag,
            emotion_intensity=intensity,
            style_note=str(tts_route.get("style_note") or ""),
            lang_hint=str(tts_route.get("lang_hint") or "zh"),
        )
        if result and result.success:
            audio_ref = result.event.get("audio_ref")
            logger.info(
                "[%s] Gateway TTS 生成成功 audio_ref=%s",
                request_id, audio_ref,
            )
            result_payload = result.as_dict()
            result_payload["route"] = dict(tts_route.get("route") or {})
            return (str(audio_ref) if audio_ref else "tts_generated"), result_payload
        logger.warning(
            "[%s] Gateway TTS 生成失败（降级）: %s",
            request_id, result.detail if result else "no_result",
        )
        return None, {"status": "DEGRADED", "reason_code": "TTS_CALL_FAILED"}
    except Exception as exc:
        logger.warning(
            "[%s] Gateway TTS 异常（降级）: %s",
            request_id, exc,
        )
        return None, {"status": "DEGRADED", "reason_code": "TTS_EXCEPTION", "detail": str(exc)}


# ---------------------------------------------------------------------------
# 主入口：Gateway Orchestrator SSE 生成器
# ---------------------------------------------------------------------------

async def gateway_orchestrator_sse_generator(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    scene_type: str,
    scene_type_source: str,
    governor_result: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    intent_id: Any,
    reason_code: Any,
    metrics: Any,
    _pre_recall_evidence: list[dict] | None = None,
    **kwargs: Any,
):
    """SCENE_GATEWAY 专用 SSE 生成器。

    由 SceneRouter.route_sse() 在 scene_type == "GATEWAY" 时调用。
    上游已通过 Airlock/Governor/Recall 等前置安全节点。

    完整链路（与 SCENE_CHAT 对齐）：
    1. 意图识别（复用 pre_intent_classifier）
    2. 场景决策：CHAT → 完整中文回复生成；其他 → 委托复杂场景处理器
    3. 预召回证据 + StateArbiter 四路查询 + RecallPackBuilder
    4. build_chat_runtime_input → _build_stream_chat_prompt（完整 prompt 构建）
    5. adjust_gen_params_for_emotion（情感→LLM 参数）
    6. 流式输出 TOKEN/SENTENCE 事件
    7. 后置任务：Airlock 检疫 + 情节记忆 + Bond 动态
    8. 可选 TTS 异步生成
    9. 发出 COMMIT 事件
    """
    from host_brain.app.graph_config_builder import (  # noqa: PLC0415
        pre_intent_classifier,
        CHAT_DIRECT_NODE_ID,
        resolve_host_llm_runtime_async,
    )
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        SCENE_CHAT,
        SCENE_GATEWAY,
        _truncate_for_prompt,
        infer_render_target_lang,
        repair_utf8_latin1_mojibake,
    )
    from host_brain.app.emotion_context import (  # noqa: PLC0415
        build_chat_emotion_snapshot,
        normalize_chat_emotion_context,
        resolve_chat_emotion_context,
    )
    from host_brain.app.chat_runtime_input import (  # noqa: PLC0415
        build_chat_runtime_input,
    )
    from host_brain.app.chat_stream_handler import (  # noqa: PLC0415  # TODO: Phase XIV cleanup — migrate to new module
        _build_stream_chat_prompt,
    )
    from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
        adjust_gen_params_for_emotion,
        astream_llm_chunks,
    )
    from host_brain.app.sentence_splitter import SentenceSplitter  # noqa: PLC0415
    from host_brain.app.chat_stream_handler import (  # noqa: PLC0415  # TODO: Phase XIV cleanup — migrate
        _extract_stream_render_text,
    )

    message = str(getattr(request_body, "message", "") or "").strip()
    thread_id = str(getattr(request_body, "thread_id", "") or "").strip() or None
    start_ts = time.monotonic()

    logger.info(
        "[%s] GatewayOrchestrator 启动 session=%s message=%.60s scene_source=%s",
        request_id, session_id, message, scene_type_source,
    )

    # ── 0. 发出 SCENE_ROUTED 事件 ──
    yield _sse_scene_routed(request_id, "GATEWAY", scene_type_source)

    # ── 1. 意图识别（复用 pre_intent_classifier）──
    sub_scene = pre_intent_classifier(message)
    logger.info(
        "[%s] GatewayOrchestrator 意图识别结果 sub_scene=%s",
        request_id, sub_scene,
    )

    # ── 2. 非 CHAT 场景：委托给复杂场景处理器 ──
    if sub_scene != SCENE_CHAT:
        logger.info(
            "[%s] GatewayOrchestrator 委托复杂场景: %s",
            request_id, sub_scene,
        )
        from host_brain.app.chat_handler_complex import _complex_scene_sse_generator  # noqa: PLC0415

        async for event in _complex_scene_sse_generator(
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            scene_type=sub_scene,
            scene_type_source="gateway_orchestrator",
            governor_result=governor_result,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            intent_id=intent_id,
            reason_code=reason_code,
            metrics=metrics,
        ):
            yield event
        return

    # ══════════════════════════════════════════════════════════════════════
    # ── 3. CHAT 场景：完整中文回复生成（对齐 SCENE_CHAT 全链路）──
    # ══════════════════════════════════════════════════════════════════════

    resolved_message = repair_utf8_latin1_mojibake(message)
    if not resolved_message:
        resolved_message = message

    # ── 3a. 情感上下文完整解析 ──
    resolved_emotion_context = normalize_chat_emotion_context(emotion_context)
    emotion_snapshot_resolved = build_chat_emotion_snapshot(
        emotion_context=resolved_emotion_context,
        source="gateway_pre_llm",
        ts_ms=int(time.time() * 1000),
    )

    # ── 3b. 预召回证据 + StateArbiter 四路查询（并行）──
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {}

    if _pre_recall_evidence is not None:
        retrieval_evidence_items = list(_pre_recall_evidence)
        retrieval_summary = {
            "status": "PRE_RECALLED",
            "source": "RecallFusionNode",
            "evidence_count": len(retrieval_evidence_items),
        }
        logger.debug(
            "[%s] Gateway 使用预召回结果 evidence_count=%d",
            request_id, len(retrieval_evidence_items),
        )
    else:
        retrieval_summary = {
            "status": "SKIPPED",
            "reason_code": "NO_PRE_RECALL",
        }

    # StateArbiter 四路查询（world_canon/style_canon/episodic/bond）
    try:
        arbiter_summary, arbiter_items, bond_state_summary = await _resolve_state_arbiter_all(
            session_id=session_id,
            request_id=request_id,
        )
        retrieval_summary.update(arbiter_summary)
        # 注入 StateArbiter 证据（优先级排序已在内部完成）
        if arbiter_items:
            retrieval_evidence_items = arbiter_items + list(retrieval_evidence_items)
    except Exception as exc:
        logger.warning(
            "[%s] Gateway StateArbiter 查询失败（降级）: %s",
            request_id, exc,
        )
        bond_state_summary = {}

    # ── 3c. RecallPackBuilder 构建（slot 分配 + token 预算）──
    retrieval_evidence_items, recall_slot_text, recall_pack_summary = _build_recall_pack(
        retrieval_evidence_items=retrieval_evidence_items,
        request_id=request_id,
        retrieval_summary=retrieval_summary,
    )

    # ── 3d. Bond 上下文提取 ──
    bond_summary_for_ctx: dict[str, Any] = {}
    bond_stage = str(bond_state_summary.get("bond_stage") or "").strip()
    if bond_stage:
        bond_summary_for_ctx = {
            "stage": bond_stage,
            "trend": str(bond_state_summary.get("trend") or ""),
            "capability_hint": str(bond_state_summary.get("capability_hint") or ""),
        }

    # ── 3e. 构建完整 runtime_input + prompt（复用 SCENE_CHAT 的完整管线）──
    runtime_input = build_chat_runtime_input(
        request_id=request_id,
        session_id=session_id,
        thread_id=thread_id,
        user_message=resolved_message,
        render_target_lang="zh",  # Gateway 强制中文
        retrieval_summary=retrieval_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        emotion_state=resolved_emotion_context,
        emotion_snapshot=emotion_snapshot_resolved,
        recall_slot_text=recall_slot_text if recall_slot_text else None,
        bond_summary=bond_summary_for_ctx or None,
        scene_type="GATEWAY",
        scene_type_source="gateway_orchestrator",
    )

    prompt_build_result = await _build_stream_chat_prompt(
        runtime_input=runtime_input,
    )
    prompt_context_validation: dict[str, Any] = {}
    prompt_context_observability: dict[str, Any] = {}
    if len(prompt_build_result) >= 5:
        prompt_text, system_prompt, gen_params, _, prompt_meta = prompt_build_result
        if isinstance(prompt_meta, Mapping):
            prompt_context_validation = dict(prompt_meta.get("prompt_context_validation") or {})
            prompt_context_observability = dict(prompt_meta.get("prompt_context_observability") or {})
    else:
        prompt_text, system_prompt, gen_params, _ = prompt_build_result  # type: ignore[misc]

    prompt_text = _augment_gateway_prompt_for_reply_depth(
        prompt_text=prompt_text,
        user_message=resolved_message,
    )

    # ── 3e-2. 情感参数注入（adjust_gen_params_for_emotion）──
    # 根据情感状态动态调整 temperature/top_p 等 LLM 生成参数
    try:
        _adjusted = adjust_gen_params_for_emotion(
            {"gen_params": gen_params},
            resolved_emotion_context,
        )
        gen_params = _adjusted.get("gen_params") or gen_params
    except Exception as _adj_exc:
        logger.debug(
            "[%s] Gateway adjust_gen_params_for_emotion 失败（降级）: %s",
            request_id, _adj_exc,
        )

    # ── 3f. LLM 运行时解析 ──
    llm_runtime = await resolve_host_llm_runtime_async(scene_type="CHAT")
    provider_name = str(llm_runtime.get("provider_name") or "gemini").strip().lower()
    model_id = str(llm_runtime.get("model_id") or "").strip()
    llm_api_key = str(llm_runtime.get("llm_api_key") or llm_runtime.get("api_key") or "").strip()
    timeout_ms = int(llm_runtime.get("timeout_ms") or 30000)  # Gateway 时效要求低，可给更多时间
    base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None
    available = bool(llm_runtime.get("available", True))

    if not available:
        yield _sse_commit(
            request_id=request_id,
            decision="REJECT",
            narrative="服务暂时不可用，请稍后再试。",
            reason_code="LLM_UNAVAILABLE",
        )
        return

    if not llm_api_key:
        from host_brain.integrations.llm_helpers import _load_api_key_from_env  # noqa: PLC0415
        llm_api_key = str(_load_api_key_from_env(provider_name) or "").strip()

    # ── 3g. 流式 LLM 生成 ──
    splitter = SentenceSplitter()
    full_text = ""
    rendered_text = ""
    sentence_index = 0
    token_index = 0
    llm_stream_started_ms = int(time.time() * 1000)
    first_token_latency_ms: int | None = None
    llm_failed = False
    llm_failure_reason_code: str | None = None

    try:
        async for chunk in astream_llm_chunks(
            provider_name=provider_name,
            model_id=model_id,
            api_key=llm_api_key,
            timeout_ms=timeout_ms,
            prompt_text=prompt_text,
            system_prompt=system_prompt,
            gen_params=gen_params,
            base_url_override=base_url_override,
        ):
            if not chunk:
                continue
            if first_token_latency_ms is None:
                first_token_latency_ms = max(0, int(time.time() * 1000) - llm_stream_started_ms)
            full_text += chunk

            # TOKEN 事件
            yield _sse_token(request_id, chunk, token_index)
            token_index += 1

            # 尝试提取可渲染文本（跳过 think tag 等）
            try:
                visible_text = _extract_stream_render_text(full_text)
            except Exception:
                visible_text = full_text
            if not visible_text:
                continue
            rendered_text = visible_text

            # 句子边界检测
            for sentence in splitter.feed(chunk):
                yield _sse_sentence(request_id, sentence, sentence_index)
                sentence_index += 1

        # 刷出残余句子
        remainder = splitter.flush()
        if remainder:
            yield _sse_sentence(request_id, remainder, sentence_index)
            sentence_index += 1

    except Exception as exc:
        llm_failed = True
        llm_failure_reason_code = "LLM_STREAM_FAILED"
        logger.error(
            "[%s] GatewayOrchestrator LLM 生成异常: %s",
            request_id, exc,
        )
        if not full_text.strip():
            full_text = "抱歉，处理出了点问题，请稍后再试。"
            rendered_text = full_text
            yield _sse_sentence(request_id, full_text, 0)

    narrative = _normalize_gateway_commit_narrative(
        raw_text=full_text,
        rendered_text=rendered_text,
    )
    try:
        from host_brain.app.intelligent_runtime import apply_response_intelligence  # noqa: PLC0415

        narrative, response_intelligence_meta = apply_response_intelligence(
            request_id=request_id,
            user_message=resolved_message,
            narrative=narrative,
            emotion_context=resolved_emotion_context,
            bond_summary=bond_summary_for_ctx or None,
            render_lang="zh",
            allow_rewrite=True,
        )
    except Exception as exc:
        response_intelligence_meta = {
            "reflection": {"status": "DEGRADED", "reason_code": "POSTPROCESS_FAILED", "detail": str(exc)},
            "multimodal_output": {"status": "SKIPPED", "reason_code": "POSTPROCESS_FAILED"},
        }

    llm_total_latency_ms = max(0, int(time.time() * 1000) - llm_stream_started_ms)
    llm_summary = _build_gateway_llm_summary(
        node_id=CHAT_DIRECT_NODE_ID,
        provider_name=provider_name,
        model_id=model_id,
        llm_runtime=llm_runtime,
        resolved_message=resolved_message,
        narrative=narrative,
        retrieval_summary=retrieval_summary,
        prompt_context_validation=prompt_context_validation,
        prompt_context_observability=prompt_context_observability,
        emotion_snapshot=emotion_snapshot_resolved,
        response_intelligence_meta=response_intelligence_meta,
        llm_total_latency_ms=llm_total_latency_ms,
        first_token_latency_ms=first_token_latency_ms,
        llm_failed=llm_failed,
        failure_reason_code=llm_failure_reason_code,
    )

    # ── 4. 后置任务：Airlock 检疫 + 情节记忆 + Bond 动态（并行）──
    post_task_refs = await _run_post_generation_tasks(
        request_id=request_id,
        session_id=session_id,
        intent_id=intent_id,
        narrative=narrative,
        user_message=resolved_message,
        emotion_context=resolved_emotion_context,
        ts_ms=ts_ms,
    )

    # ── 5. 可选 TTS 异步生成 ──
    tts_ref: str | None = None
    tts_summary: dict[str, Any] = {"status": "SKIPPED", "reason_code": "NOT_REQUESTED"}
    need_voice = _parse_bool(
        getattr(request_body, "need_voice", None),
        default=False,
    )
    if need_voice and narrative:
        tts_ref, tts_summary = await _generate_tts_async(
            narrative=narrative,
            request_id=request_id,
            session_id=session_id,
            emotion_context=resolved_emotion_context,
            bond_summary=bond_summary_for_ctx or None,
        )

    # ── 6. 可观测性持久化（降级不阻断）──
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.app.observability_persist import persist_chat_observability_rows  # noqa: PLC0415
        persist_chat_observability_rows(
            ledger=get_ledger(),
            request_id=request_id,
            session_id=session_id,
            llm_summary=llm_summary,
            ts_ms=ts_ms,
            fallback_reason_code=str(llm_summary.get("route_reason_code") or "") or None,
        )
    except Exception as exc:
        logger.debug(
            "[%s] Gateway 可观测性持久化失败（降级）: %s",
            request_id, exc,
        )

    # ── 7. 发出 COMMIT 事件 ──
    elapsed_ms = int((time.monotonic() - start_ts) * 1000)
    logger.info(
        "[%s] GatewayOrchestrator 完成 narrative_len=%d tts_ref=%s elapsed_ms=%d",
        request_id, len(narrative), tts_ref, elapsed_ms,
    )

    yield _sse_commit(
        request_id=request_id,
        decision="APPROVE",
        narrative=narrative,
        detail=f"gateway_orchestrator elapsed_ms={elapsed_ms}",
        tts_ref=tts_ref,
        audit_refs={
            "intent_id": intent_id,
            "retrieval": retrieval_summary,
            "llm": llm_summary,
            "recall_pack_builder": recall_pack_summary,
            "prompt_context_validation": prompt_context_validation,
            "prompt_context_observability": prompt_context_observability,
            "emotion_snapshot": emotion_snapshot_resolved,
            "tts": tts_summary,
            "post_tasks": post_task_refs,
            "response_intelligence": response_intelligence_meta,
            "trace_metrics": {
                "elapsed_ms": elapsed_ms,
                "llm_total_latency_ms": llm_total_latency_ms,
                "first_token_latency_ms": first_token_latency_ms,
                "render_target_lang": "zh",
            },
        },
    )
