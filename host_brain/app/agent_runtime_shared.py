"""agent_runtime_shared.py — app 外壳之间的中性共享运行时 helper。

这一层先承接旧 `top_agent.py` 中已经被多个入口直接复用的兼容能力，
把 `dispatch_agent.py` / `chat_agent.py` 对 `top_agent.py` 的硬耦合先切断，
后续再继续把真实实现逐步下沉到 tribe / observability / airlock 等稳定归属。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Callable

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.sub_agent_protocol import ALL_AGENT_IDS
from host_brain.bond.user_identity import resolve_person_id


_logger = logging.getLogger("Natsume.agent_runtime_shared")


class _SafeTemplateDict(dict[str, Any]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"



def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {
            str(key): "" if value is None else str(value)
            for key, value in kwargs.items()
        }
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text



def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}



def _load_runtime_texts() -> dict[str, Any]:
    return _load_template_mapping("top_agent.runtime_texts")



def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, Any]:
    raw = mapping.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}



def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)


_RECENT_TURN_USER_QUERY_RE = re.compile(r"(我刚才|我上一句|我上句|我前一句|我前面).*?(说了什么|发了什么|是什么)")
_RECENT_TURN_ASSISTANT_QUERY_RE = re.compile(r"(你刚才|你上一句|你上句|你前一句|你前面).*?(说了什么|回了什么|回复了什么|是什么)")
_RECENT_TURN_GENERIC_QUERY_RE = re.compile(r"(上一句|上句|前一句|刚才说的什么|刚刚说的什么).*?(是什么|是什么来着|说了什么)?")
_DELEGATE_RE = re.compile(r"\[DELEGATE:(\w+?)(?::(.+?))?\]", re.DOTALL)
_DELEGATE_INTERNAL_MARKER_RE = re.compile(r"draft_only|intent_draft|ReAct|LLM gateway|agent_id=", re.IGNORECASE)
_CONTINUATION_QUERY_RE = re.compile(r"^(然后|然后呢|继续|接着|接着呢|后面呢|再然后呢|展开说说|具体一点|继续说|接着来|那后面呢)[!！。…~]*$")
_GREETING_FASTPATH_RE = re.compile(r"^(你好|你好啊|你好呀|您好|嗨|哈喽|早上好|中午好|晚上好)[!！。…~]*$")
_PRESENCE_FASTPATH_RE = re.compile(r"^(在吗|在不在|还在吗|忙吗|在嘛)[?？!！。…~]*$")
_ACK_FASTPATH_RE = re.compile(r"^(嗯|嗯嗯|哦|噢|好的|好呀|好啊|可以|可以啊|可以呀|收到|对|对啊|是的|行)[!！。…~]*$")
_TASK_PROGRESS_QUERY_RE = re.compile(r"(进度|做到哪|到哪了|还在.*(执行|跑|做)|上个任务.*(怎么样|进度)|刚才.*任务.*(怎么样|进度)|修好了吗|处理到哪)")
_TASK_FAILURE_QUERY_RE = re.compile(r"(哪一步.*失败|哪个步骤.*失败|为什么失败|失败原因|哪一步没成功|哪个步骤没成功)")
_TASK_STEP_INDEX_RE = re.compile(r"第\s*(\d+)\s*步")
_TASK_CONTEXT_KEYWORDS = (
    "思路", "方案", "步骤", "实现", "代码", "函数", "模块", "接口", "逻辑", "架构", "设计",
    "分析", "整理", "计划", "从哪里开始", "下一步", "接下来", "具体", "展开", "继续写",
    "贪吃蛇", "codeagent", "code_agent", "agent", "work", "tool", "翻译", "实现思路",
)

_EMOTION_INFER_KEYWORDS: list[tuple[str, str]] = [

    ("HAPPY", "哈哈|开心|高兴|嘿嘿|笑|太好了|耶|うれしい|楽しい|よかった"),
    ("SAD", "难过|伤心|哭|呜|抱歉|对不起|悲しい|つらい|ごめん"),
    ("ANGRY", "生气|气死|烦|腹立|むかつく"),
    ("LOVING", "谢谢|感谢|感动|喜欢|爱你|好き|大好き|愛してる|会いたかった"),
    ("SURPRISED", "哇|真的吗|竟然|びっくり|えっ|本当"),
]



def _normalize_retrieval_summary_for_audit(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(summary or {})
    route_rows = list(raw.get("route_rows") or []) if isinstance(raw.get("route_rows"), list) else []
    route_count = raw.get("route_count")
    if route_count in (None, ""):
        route_count = len(route_rows)
    candidate_count = int(raw.get("candidate_count") or 0)
    rerank_topk = int(raw.get("rerank_topk") or 0)
    reason_code = str(raw.get("reason_code") or "").strip() or None
    error_text = str(raw.get("error") or "").strip() or None
    explicit_status = str(raw.get("status") or "").strip().upper()

    if explicit_status in {"OK", "SKIPPED", "DEGRADED"}:
        status = explicit_status
    elif reason_code or error_text or bool(raw.get("degraded", False)):
        status = "DEGRADED"
    elif candidate_count > 0 or int(route_count) > 0 or rerank_topk > 0 or route_rows:
        status = "OK"
    else:
        status = "SKIPPED"

    degraded = status == "DEGRADED" or bool(raw.get("degraded", False))

    raw["status"] = status
    raw["route_count"] = int(route_count)
    raw["candidate_count"] = candidate_count
    raw["rerank_topk"] = rerank_topk
    raw["degraded"] = degraded
    raw["reason_code"] = reason_code
    raw["error"] = error_text
    return raw



def _coerce_trace_mapping(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)



def _coerce_trace_score(raw: Any) -> float | None:
    try:
        return round(float(raw), 4)
    except (TypeError, ValueError):
        return None



def _coerce_trace_bool(raw: Any) -> bool | None:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None



def _summarize_motive_state_for_trace(motive_state: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(motive_state)
    if not raw:
        return {}

    summary: dict[str, Any] = {}
    dominant_motive = str(raw.get("dominant_motive") or "").strip()
    if dominant_motive:
        summary["dominant_motive"] = dominant_motive

    drives: dict[str, float] = {}
    for key in (
        "reach_out_drive",
        "care_drive",
        "withdraw_drive",
        "continuity_drive",
        "self_protect_drive",
    ):
        score = _coerce_trace_score(raw.get(key))
        if score is not None:
            drives[key] = score
    if drives:
        summary["drives"] = drives

    reason_tags = [
        str(tag).strip()
        for tag in list(raw.get("motive_reason_tags") or [])
        if str(tag).strip()
    ]
    if reason_tags:
        summary["reason_tags"] = reason_tags[:8]
    return summary



def _summarize_response_policy_for_trace(response_policy: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(response_policy)
    if not raw:
        return {}

    summary: dict[str, Any] = {}

    for key in ("max_chars", "max_sentences", "min_sentences"):
        try:
            value = int(raw.get(key))
        except (TypeError, ValueError):
            continue
        summary[key] = value

    for key in (
        "allow_followup",
        "allow_topic_shift",
        "allow_proactive_after_reply",
        "should_be_brief",
        "should_be_withdrawn",
    ):
        value = _coerce_trace_bool(raw.get(key))
        if value is not None:
            summary[key] = value

    for key in ("warmth_floor", "warmth_ceiling", "intimacy_ceiling", "humor_ceiling"):
        value = _coerce_trace_score(raw.get(key))
        if value is not None:
            summary[key] = value

    for key in ("confession_guard", "withdrawal_guard"):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value

    reason_tags = [
        str(tag).strip()
        for tag in list(raw.get("policy_reason_tags") or [])
        if str(tag).strip()
    ]
    if reason_tags:
        summary["reason_tags"] = reason_tags[:8]
    return summary



def _summarize_emotion_context_for_trace(emotion_context: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(emotion_context)
    if not raw:
        return {}

    summary: dict[str, Any] = {}
    for key in ("emotion_tag", "mood_state", "emotion_range_hint"):
        value = str(raw.get(key) or "").strip()
        if value:
            summary[key] = value

    for key in ("emotion_intensity", "valence", "arousal"):
        score = _coerce_trace_score(raw.get(key))
        if score is not None:
            summary[key] = score

    vad_vector = raw.get("vad_vector")
    if isinstance(vad_vector, Mapping):
        vad_summary: dict[str, float] = {}
        for key in ("valence", "arousal", "dominance"):
            score = _coerce_trace_score(vad_vector.get(key))
            if score is not None:
                vad_summary[key] = score
        if vad_summary:
            summary["vad_vector"] = vad_summary

    triggered = [
        str(item).strip()
        for item in list(raw.get("triggered_emotions") or [])
        if str(item).strip()
    ]
    if triggered:
        summary["triggered_emotions"] = triggered[:8]
    return summary



def _summarize_persona_resident_memory_for_trace(
    *,
    bond_summary: Mapping[str, Any] | None,
    retrieval_summary: Mapping[str, Any] | None,
    retrieval_evidence_items: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {}

    bond_raw = _coerce_trace_mapping(bond_summary)
    if bond_raw:
        bond_info: dict[str, Any] = {}
        for key in ("stage", "trend", "user_id"):
            value = str(bond_raw.get(key) or "").strip()
            if value:
                bond_info[key] = value
        score = _coerce_trace_score(bond_raw.get("score"))
        if score is not None:
            bond_info["score"] = score
        if bond_info:
            summary["bond"] = bond_info

    retrieval_raw = _coerce_trace_mapping(retrieval_summary)
    if retrieval_raw:
        retrieval_info: dict[str, Any] = {}
        for key in ("status", "reason_code", "pack_id"):
            value = str(retrieval_raw.get(key) or "").strip()
            if value:
                retrieval_info[key] = value
        for key in ("candidate_count", "route_count", "rerank_topk"):
            try:
                retrieval_info[key] = int(retrieval_raw.get(key) or 0)
            except (TypeError, ValueError):
                continue
        target_stores = [
            str(item).strip()
            for item in list(retrieval_raw.get("target_stores") or [])
            if str(item).strip()
        ]
        if target_stores:
            retrieval_info["target_stores"] = target_stores
        if retrieval_info:
            summary["retrieval"] = retrieval_info

    evidence_items = [item for item in list(retrieval_evidence_items or []) if isinstance(item, Mapping)]
    if evidence_items:
        summary["evidence_count"] = len(evidence_items)
        store_ids = sorted(
            {
                str(item.get("store_id") or item.get("source_type") or item.get("kind") or "").strip()
                for item in evidence_items
                if str(item.get("store_id") or item.get("source_type") or item.get("kind") or "").strip()
            }
        )
        if store_ids:
            summary["evidence_store_ids"] = store_ids
    return summary



def _normalize_trace_stage_meta(trace_stage_meta: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trace_stage_meta, Mapping):
        return {}

    normalized: dict[str, Any] = {}
    for key, value in dict(trace_stage_meta).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        normalized[str(key)] = value
    return normalized



def _resolve_envelope_confidence(status: str) -> float:
    normalized = str(status or "").strip().upper()
    if normalized == "OK":
        return 1.0
    if normalized == "SKIPPED":
        return 0.6
    if normalized == "DEGRADED":
        return 0.35
    if normalized in {"FAILED", "ERROR"}:
        return 0.0
    return 0.5



def _build_trace_output_digest(output_data: Any) -> dict[str, Any]:
    if isinstance(output_data, Mapping):
        digest: dict[str, Any] = {"field_count": len(output_data)}
        scalar_count = 0
        for key, value in list(output_data.items())[:8]:
            key_text = str(key).strip()
            if not key_text:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                digest[key_text] = value
                scalar_count += 1
                continue
            if isinstance(value, list):
                digest[f"{key_text}_count"] = len(value)
                continue
            if isinstance(value, Mapping):
                digest[f"{key_text}_keys"] = list(value.keys())[:5]
        if scalar_count == 0 and len(digest) == 1:
            digest["kind"] = "mapping"
        return digest

    if isinstance(output_data, list):
        return {"kind": "list", "count": len(output_data)}

    if isinstance(output_data, str):
        text = output_data.strip()
        return {"kind": "text", "length": len(text), "preview": text[:120]}

    if output_data is None:
        return {}

    return {"kind": type(output_data).__name__}



def _detect_trace_fallback_used(
    *,
    reason_code: str | None,
    degrade_code: str | None,
    output_data: Any,
) -> bool:
    for token in (str(reason_code or ""), str(degrade_code or "")):
        if "FALLBACK" in token.upper():
            return True
    if isinstance(output_data, Mapping):
        for key, value in output_data.items():
            key_text = str(key).strip().lower()
            if "fallback" in key_text and bool(value):
                return True
    return False



def _build_trace_agent_envelope(
    *,
    request_id: str,
    node_id: str,
    parent_node_id: str | None,
    node_type: str,
    status: str,
    reason_code: str | None,
    output_data: Any,
    fallback_used: bool,
    degrade_code: str | None,
    ts_ms: int,
) -> dict[str, Any]:
    status_text = str(status or "OK").strip().upper() or "OK"
    reason_code_text = str(reason_code or "").strip() or None
    degrade_code_text = str(degrade_code or "").strip() or None
    reason_text = reason_code_text or ""
    output_digest = _build_trace_output_digest(output_data)

    fallback_marked = bool(fallback_used) or _detect_trace_fallback_used(
        reason_code=reason_code_text,
        degrade_code=degrade_code_text,
        output_data=output_data,
    )
    if degrade_code_text is None and status_text in {"DEGRADED", "FAILED", "ERROR"}:
        degrade_code_text = reason_code_text

    try:
        from host_brain.app.schemas.agent_envelope import AgentEnvelope  # noqa: PLC0415

        envelope_model = AgentEnvelope(
            agent_id=node_id,
            agent_family=str(node_type or "system").strip().lower() or "system",
            status=status_text,
            confidence=_resolve_envelope_confidence(status_text),
            reason=reason_text,
            reason_code=reason_code_text,
            request_id=str(request_id or "").strip(),
            node_id=node_id,
            parent_node_id=(str(parent_node_id).strip() if parent_node_id else None),
            output_digest=output_digest,
            fallback_used=fallback_marked,
            degrade_code=degrade_code_text,
            ts_ms=int(ts_ms or 0),
        )
        envelope = envelope_model.as_trace_dict()
        envelope["request_id"] = envelope_model.request_id
        envelope["reason"] = envelope_model.reason
        return envelope
    except Exception:
        return {
            "agent_id": node_id,
            "agent_family": str(node_type or "system").strip().lower() or "system",
            "node_id": node_id,
            "parent_node_id": (str(parent_node_id).strip() if parent_node_id else None),
            "request_id": str(request_id or "").strip(),
            "status": status_text,
            "confidence": _resolve_envelope_confidence(status_text),
            "reason": reason_text,
            "reason_code": reason_code_text,
            "fallback_used": fallback_marked,
            "degrade_code": degrade_code_text,
            "output_digest": output_digest,
            "schema_version": "1.0",
            "ts_ms": int(ts_ms or 0),
        }



def _build_trace_stage_node(
    *,
    node_id: str,
    node_type: str,
    display_name: str,
    ts_ms: int,
    status: str = "OK",
    side: str = "host",
    request_id: str = "",
    parent_node_id: str | None = None,
    reason_code: str | None = None,
    fallback_used: bool = False,
    degrade_code: str | None = None,
    latency_ms: int | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    input_data: Any = None,
    output_data: Any = None,
    error: str | None = None,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "node_id": node_id,
        "node_type": node_type,
        "display_name": display_name,
        "side": side,
        "status": status,
        "ts_ms": ts_ms,
    }
    if reason_code:
        node["reason_code"] = reason_code
    if latency_ms is not None:
        node["latency_ms"] = latency_ms
    if provider:
        node["provider"] = provider
    if model_id:
        node["model_id"] = model_id
    if input_data not in (None, {}, []):
        node["input_data"] = input_data
    if output_data not in (None, {}, []):
        node["output_data"] = output_data
    if error:
        node["error"] = error
    node["agent_envelope"] = _build_trace_agent_envelope(
        request_id=request_id,
        node_id=node_id,
        parent_node_id=parent_node_id,
        node_type=node_type,
        status=status,
        reason_code=reason_code,
        output_data=output_data,
        fallback_used=fallback_used,
        degrade_code=degrade_code,
        ts_ms=ts_ms,
    )
    return node



async def _persist_post_process_audit(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    narrative: str,
    emotion_context: Mapping[str, Any] | None = None,
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    response_policy: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> None:
    from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    ledger = get_ledger()
    _now = int(time.time() * 1000)

    if gov_decision:
        ledger.log_governor(
            {
                "request_id": request_id,
                "intent_id": request_id,
                "session_id": session_id,
                "decision": gov_decision.get("decision", "APPROVE"),
                "reason_code": gov_decision.get("reason_code"),
                "rule_trace": json.dumps(gov_decision.get("rule_trace", []), ensure_ascii=False, default=str),
                "latency_ms": gov_decision.get("latency_ms", 0),
                "ts_ms": ts_ms,
            }
        )

    try:
        runtime_task_registry = get_runtime_task_registry()
        runtime_task = runtime_task_registry.get_by_request_id(request_id)
        if runtime_task is not None and str(runtime_task.status).upper() in {"RUNNING", "RECOVERING"}:
            runtime_task_status_templates = _mapping_section(_load_runtime_texts(), "runtime_task_status")
            has_failure = bool(runtime_task.error or runtime_task.result_code)
            if has_failure:
                runtime_task_registry.mark_failed(
                    runtime_task.task_id,
                    detail=_mapping_text(
                        runtime_task_status_templates,
                        "failed_detail",
                        default="整条回复已经完成，但执行阶段有失败。",
                    ),
                    error=str(runtime_task.error or "").strip(),
                    result_code=str(runtime_task.result_code or "").strip() or None,
                    result_text=narrative,
                )
            else:
                runtime_task_registry.mark_completed(
                    runtime_task.task_id,
                    detail=_mapping_text(
                        runtime_task_status_templates,
                        "completed_detail",
                        default="整条回复已经完成。",
                    ),
                    result_text=narrative,
                )
    except Exception as exc:
        _logger.debug("[%s] runtime task finalize failed: %s", request_id, exc)

    ledger.log_llm_node_call(
        {
            "request_id": request_id,
            "node_id": "top_agent_llm_stream",
            "provider": provider_name,
            "model_id": model_id,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None if narrative.strip() else "LLM_EMPTY_OUTPUT",
            "latency_ms": llm_latency_ms,
            "input_digest": (llm_input_digest or "")[:500] or None,
            "output_digest": (narrative or "")[:400] or None,
            "ts_ms": _now,
        }
    )

    _normalized_retrieval_summary = _normalize_retrieval_summary_for_audit(retrieval_summary)
    _evidence_items = list(retrieval_evidence_items or [])
    _rs = _normalized_retrieval_summary
    if _evidence_items or _rs.get("status") == "OK":
        _query_plan = _rs.get("query_plan") or {}
        _topk_snippets = [
            {
                "evidence_ref": str(item.get("evidence_ref") or ""),
                "store_id": str(item.get("store_id") or ""),
                "snippet": str(item.get("snippet") or "")[:200],
                "score": item.get("score"),
            }
            for item in _evidence_items[:8]
        ]
        ledger.log_retrieval_event(
            {
                "request_id": request_id,
                "intent_id": request_id,
                "op_kind": "PIPELINE",
                "status": _rs.get("status", "OK"),
                "reason_code": _rs.get("reason_code"),
                "stage": "top_agent._async_post_process",
                "payload": {
                    "candidate_count": _rs.get("candidate_count", 0),
                    "route_count": _rs.get("route_count", 0),
                    "rerank_topk": _rs.get("rerank_topk", 0),
                    "degraded": bool(_rs.get("degraded", False)),
                    "query_plan": _query_plan,
                    "topk": _topk_snippets,
                    "agentic_meta": _rs.get("agentic_meta"),
                    "embedding_model_id": _rs.get("embedding_model_id"),
                    "embedding_provider": _rs.get("embedding_provider"),
                    "effective_embedding_provider": _rs.get("effective_embedding_provider"),
                    "fallback_embedding_used": bool(_rs.get("fallback_embedding_used", False)),
                    "fallback_embedding_reason_code": _rs.get("fallback_embedding_reason_code"),
                    "rerank_model_id": _rs.get("rerank_model_id"),
                    "rerank_provider": _rs.get("rerank_provider"),
                    "effective_rerank_provider": _rs.get("effective_rerank_provider"),
                    "fallback_reranker_used": bool(_rs.get("fallback_reranker_used", False)),
                    "fallback_reranker_reason_code": _rs.get("fallback_reranker_reason_code"),
                },
                "detail": (
                    f"candidate={_rs.get('candidate_count', 0)} "
                    f"topk={len(_topk_snippets)} "
                    f"intent={_query_plan.get('intent', 'unknown')}"
                ),
                "ts_ms": _now,
            }
        )

    _trace_stage_meta = _normalize_trace_stage_meta(trace_stage_meta)
    _retrieval_status = _normalized_retrieval_summary.get("status", "SKIPPED")
    _retrieval_count = _normalized_retrieval_summary.get("candidate_count", 0)
    _retrieval_reason = str(_normalized_retrieval_summary.get("reason_code") or "").strip() or None
    _retrieval_output = {
        "candidate_count": _retrieval_count,
        "status": _retrieval_status,
        "reason_code": _retrieval_reason,
        "error": _normalized_retrieval_summary.get("error"),
        "route_count": _normalized_retrieval_summary.get("route_count"),
        "rerank_topk": _normalized_retrieval_summary.get("rerank_topk"),
        "degraded": _normalized_retrieval_summary.get("degraded"),
        "target_stores": _normalized_retrieval_summary.get("target_stores"),
        "query_plan": _normalized_retrieval_summary.get("query_plan"),
        "agentic_meta": _normalized_retrieval_summary.get("agentic_meta"),
        "embedding_model_id": _normalized_retrieval_summary.get("embedding_model_id"),
        "embedding_provider": _normalized_retrieval_summary.get("embedding_provider"),
        "effective_embedding_provider": _normalized_retrieval_summary.get("effective_embedding_provider"),
        "fallback_embedding_used": bool(_normalized_retrieval_summary.get("fallback_embedding_used", False)),
        "fallback_embedding_reason_code": _normalized_retrieval_summary.get("fallback_embedding_reason_code"),
        "rerank_model_id": _normalized_retrieval_summary.get("rerank_model_id"),
        "rerank_provider": _normalized_retrieval_summary.get("rerank_provider"),
        "effective_rerank_provider": _normalized_retrieval_summary.get("effective_rerank_provider"),
        "fallback_reranker_used": bool(_normalized_retrieval_summary.get("fallback_reranker_used", False)),
        "fallback_reranker_reason_code": _normalized_retrieval_summary.get("fallback_reranker_reason_code"),
    }
    _query_plan_output = _coerce_trace_mapping(_normalized_retrieval_summary.get("query_plan"))
    _agentic_rag_output = _coerce_trace_mapping(_normalized_retrieval_summary.get("agentic_meta"))
    _emotion_trace = _summarize_emotion_context_for_trace(emotion_context)
    _persona_memory_trace = _summarize_persona_resident_memory_for_trace(
        bond_summary=bond_summary,
        retrieval_summary=_normalized_retrieval_summary,
        retrieval_evidence_items=_evidence_items,
    )
    _motive_trace = _summarize_motive_state_for_trace(motive_state)
    _response_policy_trace = _summarize_response_policy_for_trace(response_policy)
    _prompt_builder_output: dict[str, Any] = {}
    if _motive_trace:
        _prompt_builder_output["motive_state"] = _motive_trace
    if _response_policy_trace:
        _prompt_builder_output["response_policy"] = _response_policy_trace
    _prompt_builder_status = "OK" if (llm_input_digest or _prompt_builder_output) else "SKIPPED"
    _prompt_builder_node = _build_trace_stage_node(
        node_id="prompt_builder",
        node_type="system",
        display_name="Prompt Builder",
        status=_prompt_builder_status,
        ts_ms=ts_ms,
        output_data=_prompt_builder_output,
    )

    _dispatch_used = bool(_trace_stage_meta.get("dispatch_agent_used"))
    _chat_used = bool(_trace_stage_meta.get("chat_agent_used"))
    _path = str(
        _trace_stage_meta.get("dispatch_route")
        or ("delegate" if delegated_agents else "chat_direct")
    ).strip() or ("delegate" if delegated_agents else "chat_direct")
    _dispatch_reason = str(_trace_stage_meta.get("dispatch_reason") or "").strip() or None
    _dispatch_target = str(_trace_stage_meta.get("dispatch_target") or "").strip() or None
    _chat_mode = str(
        _trace_stage_meta.get("chat_mode")
        or ("sub_agent" if delegated_agents else "direct")
    ).strip() or ("sub_agent" if delegated_agents else "direct")
    _chat_sub_agent_id = str(_trace_stage_meta.get("chat_sub_agent_id") or "").strip() or None
    _dispatch_output = {
        "route": _path,
        "reason": _dispatch_reason,
        "target": _dispatch_target,
    }
    _chat_output = {
        "mode": _chat_mode,
        "sub_agent_id": _chat_sub_agent_id,
        "delegated_agent_count": len(list(delegated_agents or [])),
    }
    _top_agent_output = {
        "path": _path,
        "retrieval_status": _retrieval_status,
        "retrieval_candidate_count": _retrieval_count,
        "delegated_agent_count": len(list(delegated_agents or [])),
        "trace_stage_meta": _trace_stage_meta,
    }

    _trace_nodes_v2: list[dict[str, Any]] = [
        _build_trace_stage_node(
            node_id="airlock_ingress",
            node_type="governor",
            display_name="Airlock Ingress",
            ts_ms=ts_ms,
            status="OK",
        ),
        _build_trace_stage_node(
            node_id="governor",
            node_type="governor",
            display_name="Governor",
            ts_ms=ts_ms,
            status=gov_decision.get("decision", "APPROVE") if gov_decision else "OK",
            latency_ms=gov_decision.get("latency_ms", 0) if gov_decision else 0,
            reason_code=gov_decision.get("reason_code") if gov_decision else None,
            output_data={"rule_trace": gov_decision.get("rule_trace")} if gov_decision else None,
        ),
        _build_trace_stage_node(
            node_id="query_planner",
            node_type="retrieval",
            display_name="Query Planner",
            ts_ms=ts_ms,
            status=_retrieval_status if (_query_plan_output or _retrieval_status != "SKIPPED") else "SKIPPED",
            reason_code=_retrieval_reason,
            output_data=_query_plan_output,
        ),
        _build_trace_stage_node(
            node_id="agentic_rag",
            node_type="retrieval",
            display_name="Agentic RAG",
            ts_ms=ts_ms,
            status=_retrieval_status if (_agentic_rag_output or _retrieval_status != "SKIPPED") else "SKIPPED",
            reason_code=_retrieval_reason,
            output_data=_agentic_rag_output,
        ),
        _build_trace_stage_node(
            node_id="recall_fusion",
            node_type="recall",
            display_name="Recall Fusion",
            ts_ms=ts_ms,
            status=_retrieval_status,
            reason_code=_retrieval_reason,
            output_data=_retrieval_output,
        ),
        _build_trace_stage_node(
            node_id="emotion_context",
            node_type="system",
            display_name="Emotion Context",
            ts_ms=ts_ms,
            status="OK" if _emotion_trace else "SKIPPED",
            output_data=_emotion_trace,
        ),
        _build_trace_stage_node(
            node_id="persona_resident_memory",
            node_type="memory",
            display_name="Persona Resident Memory",
            ts_ms=ts_ms,
            status="OK" if _persona_memory_trace else "SKIPPED",
            output_data=_persona_memory_trace,
        ),
        _build_trace_stage_node(
            node_id="motive_state",
            node_type="system",
            display_name="Motive State",
            ts_ms=ts_ms,
            status="OK" if _motive_trace else "SKIPPED",
            output_data=_motive_trace,
        ),
        _build_trace_stage_node(
            node_id="response_policy",
            node_type="system",
            display_name="Response Policy",
            ts_ms=ts_ms,
            status="OK" if _response_policy_trace else "SKIPPED",
            output_data=_response_policy_trace,
        ),
        _build_trace_stage_node(
            node_id="top_agent",
            node_type="system",
            display_name="Top Agent",
            ts_ms=ts_ms,
            status="OK" if narrative.strip() else "DEGRADED",
            output_data=_top_agent_output,
        ),
    ]
    if _dispatch_used:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="dispatch_agent",
                node_type="agent",
                display_name="Dispatch Agent",
                ts_ms=ts_ms,
                status="OK",
                reason_code=_dispatch_reason,
                output_data=_dispatch_output,
            )
        )
    for da in (delegated_agents or []):
        _agent_id = str(da.get("agent_id") or "?")
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id=f"sub_agent:{_agent_id}",
                node_type="agent",
                display_name=f"Sub Agent: {_agent_id}",
                ts_ms=ts_ms,
                status="OK" if da.get("success") else "FAILED",
                latency_ms=da.get("latency_ms", 0),
                error=da.get("error"),
                output_data={
                    "agent_id": _agent_id,
                    "audit_refs": da.get("audit_refs"),
                },
            )
        )
    if _chat_used:
        _trace_nodes_v2.append(
            _build_trace_stage_node(
                node_id="chat_agent",
                node_type="agent",
                display_name="Chat Agent",
                ts_ms=ts_ms,
                status="OK" if narrative.strip() else "DEGRADED",
                output_data=_chat_output,
            )
        )
    _trace_nodes_v2.append(_prompt_builder_node)
    _trace_nodes_v2.append(
        _build_trace_stage_node(
            node_id="llm_stream",
            node_type="llm",
            display_name="LLM Stream",
            ts_ms=ts_ms,
            status="OK" if narrative.strip() else "DEGRADED",
            latency_ms=llm_latency_ms,
            provider=provider_name,
            model_id=model_id,
            input_data={
                "first_token_ms": first_token_ms,
                "buffer_stream": buffer_stream,
                "input_digest": (llm_input_digest or "")[:300] or None,
            },
            output_data={
                "final_response": (narrative or "")[:800] or None,
            },
        )
    )
    _trace_nodes_v2.append(
        _build_trace_stage_node(
            node_id="airlock_egress",
            node_type="governor",
            display_name="Airlock Egress",
            ts_ms=_now,
            status="OK",
        )
    )

    trace_nodes = list(_trace_nodes_v2)
    _pii_detail: dict[str, Any] = {}
    if pii_ingress_stats:
        _pii_detail["ingress"] = pii_ingress_stats
    if pii_egress_stats:
        _pii_detail["egress"] = pii_egress_stats
    if _pii_detail:
        trace_nodes.append(
            {
                "node_id": "pii_filter",
                "node_type": "governor",
                "display_name": "PII 过滤",
                "side": "host",
                "status": "OK",
                "output_data": _pii_detail,
                "ts_ms": _now,
            }
        )

    _prev_node_id: str | None = None
    for _node in trace_nodes:
        _node_id = str(_node.get("node_id") or "").strip()
        _node_type = str(_node.get("node_type") or "system").strip().lower() or "system"
        _node_status = str(_node.get("status") or "OK").strip().upper() or "OK"
        _node_reason = str(_node.get("reason_code") or "").strip() or None
        _node_output = _node.get("output_data")
        _node_ts = int(_node.get("ts_ms") or ts_ms)
        _node["agent_envelope"] = _build_trace_agent_envelope(
            request_id=request_id,
            node_id=_node_id,
            parent_node_id=_prev_node_id,
            node_type=_node_type,
            status=_node_status,
            reason_code=_node_reason,
            output_data=_node_output,
            fallback_used=False,
            degrade_code=None,
            ts_ms=_node_ts,
        )
        if _node_id:
            _prev_node_id = _node_id

    trace_edges = [
        {"source": trace_nodes[i]["node_id"], "target": trace_nodes[i + 1]["node_id"]}
        for i in range(len(trace_nodes) - 1)
    ]

    ledger.log_graph_run_trace(
        {
            "request_id": request_id,
            "session_id": session_id,
            "total_latency_ms": total_latency_ms,
            "status": "OK" if narrative.strip() else "DEGRADED",
            "reason_code": None,
            "node_count": len(trace_nodes),
            "nodes_json": trace_nodes,
            "edges_json": trace_edges,
            "ts_ms": _now,
        }
    )



def _airlock_sanitize_output(text: str) -> str:


    """出口净化：null byte + 长度截断。"""
    text = text.replace("\x00", "").strip()
    max_chars = int(os.environ.get("HOST_QA_MAX_RESPONSE_CHARS") or "4000")
    if len(text) > max_chars:
        truncated = text[:max_chars]
        for ch in ("。", "！", "？", ".", "!", "?"):
            last = truncated.rfind(ch)
            if last > max_chars * 0.7:
                truncated = truncated[: last + 1]
                break
        text = truncated
    return text



def _build_reply_preview(text: str, *, limit: int = 120) -> str:
    normalized = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    if len(normalized) <= int(limit):
        return normalized
    return normalized[: max(1, int(limit) - 3)].rstrip() + "..."


class _SubAgentGatewayAdapter:
    def __init__(
        self,
        *,
        provider_name: str,
        model_id: str,
        api_key: str,
        timeout_ms: int,
        base_url_override: str | None,
    ) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: PLC0415
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        from host_brain.app.prompt_builder import extract_langchain_content_text  # noqa: PLC0415

        self._human_message = HumanMessage
        self._system_message = SystemMessage
        self._ai_message = AIMessage
        self._extract_text = extract_langchain_content_text
        self._llm = ChatOpenAI(
            model=str(model_id or "").strip() or "default",
            api_key=api_key,
            base_url=(str(base_url_override or "").strip() or None),
            timeout=max(1.0, float(timeout_ms) / 1000.0),
            max_retries=1,
            temperature=0.3,
        )
        self.provider_name = str(provider_name or "").strip().lower()

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        lc_messages: list[Any] = []
        for row in list(messages or []):
            if not isinstance(row, Mapping):
                continue
            role = str(row.get("role") or "user").strip().lower()
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            if role == "system":
                lc_messages.append(self._system_message(content=content))
            elif role == "assistant":
                lc_messages.append(self._ai_message(content=content))
            else:
                lc_messages.append(self._human_message(content=content))
        response = self._llm.invoke(lc_messages)
        return {"content": self._extract_text(getattr(response, "content", ""))}



def _build_subagent_gateway_from_runtime(
    llm_runtime: Mapping[str, Any],
    adapter_class: type[Any] | None = None,
) -> Any | None:
    try:
        if not bool(llm_runtime.get("available")):
            return None
        provider_name = str(llm_runtime.get("provider_name") or "").strip()
        model_id = str(llm_runtime.get("model_id") or "").strip()
        api_key = str(llm_runtime.get("llm_api_key") or "").strip()
        timeout_ms = int(llm_runtime.get("timeout_ms") or 30_000)
        base_url_override = str(llm_runtime.get("base_url_override") or "").strip() or None
        if not provider_name or not model_id:
            return None
        _adapter_class = adapter_class or _SubAgentGatewayAdapter
        return _adapter_class(
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            timeout_ms=timeout_ms,
            base_url_override=base_url_override,
        )
    except Exception as exc:
        _logger.debug("sub-agent gateway adapter unavailable: %s", exc)
        return None



def _detect_recent_turn_query_role(
    user_message: str,
    user_query_re: Any | None = None,
    assistant_query_re: Any | None = None,
    generic_query_re: Any | None = None,
) -> str | None:
    normalized = str(user_message or "").strip()
    if not normalized:
        return None
    _user_query_re = user_query_re or _RECENT_TURN_USER_QUERY_RE
    _assistant_query_re = assistant_query_re or _RECENT_TURN_ASSISTANT_QUERY_RE
    _generic_query_re = generic_query_re or _RECENT_TURN_GENERIC_QUERY_RE
    if _user_query_re.search(normalized):
        return "user"
    if _assistant_query_re.search(normalized):
        return "assistant"
    if _generic_query_re.search(normalized):
        return "both"
    return None



def _load_continuity_turns(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    render_target_lang: str,
    fetch_limit: int = 24,
) -> list[dict[str, str]]:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.app.top_agent_prompt import _fetch_continuity_rows, _voice_rows_to_turns  # noqa: PLC0415

        rows = _fetch_continuity_rows(
            ledger=get_ledger(),
            session_id=session_id,
            person_id=person_id,
            request_id=request_id,
            fetch_limit=fetch_limit,
        )
        return _voice_rows_to_turns(rows=rows, render_target_lang=render_target_lang)
    except Exception as exc:
        _logger.debug("[%s] continuity lookup failed: %s", request_id, exc)
        return []



def _maybe_build_recent_turn_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    detect_recent_turn_query_role: Callable[[str], str | None] | None = None,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
) -> str | None:
    _detect_query_role = detect_recent_turn_query_role or _detect_recent_turn_query_role
    _load_turns = load_continuity_turns or _load_continuity_turns
    query_role = _detect_query_role(user_message)
    if query_role is None:
        return None

    turns = _load_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=24,
    )

    if not turns:
        return None

    def _finalize_recent_turn_text(candidate: str, *, fallback: str) -> str:
        normalized = str(candidate or "").strip()
        if not normalized:
            return fallback
        if any(
            token in normalized
            for token in ("已确认事实", "请直接告诉用户", "请直接回答", "不要再说看不到", "不要再说没看到")
        ):
            return fallback
        return normalized

    latest_user = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "user"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )
    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )

    templates = _mapping_section(_load_runtime_texts(), "recent_turn_answer")
    if query_role == "user" and latest_user:
        fallback = f"你上一句是「{latest_user}」。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "user_template",
                latest_user=latest_user,
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "assistant" and latest_assistant:
        fallback = f"我上一句说的是「{latest_assistant}」。"
        return _finalize_recent_turn_text(
            _mapping_text(
                templates,
                "assistant_template",
                latest_assistant=latest_assistant,
                default=fallback,
            ),
            fallback=fallback,
        )
    if query_role == "both":
        parts: list[str] = []
        if latest_user:
            parts.append(
                _mapping_text(
                    templates,
                    "both_user_item_template",
                    latest_user=latest_user,
                    default="你上一句是「{latest_user}」",
                )
            )
        if latest_assistant:
            parts.append(
                _mapping_text(
                    templates,
                    "both_assistant_item_template",
                    latest_assistant=latest_assistant,
                    default="我上一句说的是「{latest_assistant}」",
                )
            )
        if parts:
            fallback = "；".join(parts) + "。"
            return _finalize_recent_turn_text(
                _mapping_text(
                    templates,
                    "both_wrapper_template",
                    facts="；".join(parts),
                    default=fallback,
                ),
                fallback=fallback,
            )
    return None



def _resolve_persona_session_emotion(emotion_context: Mapping[str, Any]) -> tuple[float, float]:
    """为人格 session_end 提取可用的 valence / arousal。"""
    payload = dict(emotion_context or {})
    vad_payload = payload.get("vad") if isinstance(payload.get("vad"), Mapping) else {}

    raw_valence = payload.get("valence")
    if raw_valence is None:
        raw_valence = vad_payload.get("valence")

    raw_arousal = payload.get("arousal")
    if raw_arousal is None:
        raw_arousal = vad_payload.get("arousal")

    def _coerce(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)

    if raw_valence is not None or raw_arousal is not None:
        valence = max(-1.0, min(1.0, _coerce(raw_valence, 0.0)))
        arousal = max(0.0, min(1.0, _coerce(raw_arousal, 0.3)))
        return valence, arousal

    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
    intensity = max(0.0, min(1.0, _coerce(payload.get("emotion_intensity"), 0.5)))
    fallback_vad = {
        "HAPPY": (0.7, 0.65),
        "LOVING": (0.8, 0.45),
        "EXCITED": (0.75, 0.85),
        "SURPRISED": (0.2, 0.8),
        "SAD": (-0.6, 0.25),
        "ANGRY": (-0.7, 0.8),
        "NEUTRAL": (0.0, 0.3),
    }
    base_valence, base_arousal = fallback_vad.get(emotion_tag, (0.0, 0.3))
    valence = max(-1.0, min(1.0, float(base_valence) * max(0.4, intensity)))
    arousal = max(0.0, min(1.0, max(float(base_arousal), intensity)))
    return valence, arousal


async def _run_persona_session_end_hooks(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
) -> None:
    """统一触发人格 session_end/conversation_end 收口。"""
    try:
        from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        persona_user_id = get_canonical_bond_user_id()
        valence, arousal = _resolve_persona_session_emotion(emotion_context)
        pending_topics = []
        topic_preview = _build_reply_preview(user_message, limit=64)
        if topic_preview:
            pending_topics = [topic_preview]
        await asyncio.to_thread(
            get_limbic_agent().on_session_end,
            person_id=persona_user_id,
            valence=valence,
            arousal=arousal,
            pending_topics=pending_topics,
        )
    except Exception as exc:
        _logger.debug("[%s] persona inner_world session_end failed: %s", request_id, exc)

    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        await asyncio.to_thread(
            get_limbic_agent().on_conversation_ended,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
        )
    except Exception as exc:
        _logger.debug("[%s] persona body_sense conversation_end failed: %s", request_id, exc)



def _infer_emotion_tag_for_tts(
    narrative: str,
    current_tag: str,
    emotion_infer_keywords: list[tuple[str, str]] | None = None,
) -> str:
    """当 current_tag 为 NEUTRAL 时，根据回复文案推断 emotion_tag。"""
    if not narrative or str(current_tag or "").strip().upper() != "NEUTRAL":
        return str(current_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    text = (narrative or "")[:500]
    _emotion_infer_keywords = emotion_infer_keywords or _EMOTION_INFER_KEYWORDS
    for tag, pattern in _emotion_infer_keywords:
        if re.search(pattern, text, re.IGNORECASE):
            return tag
    return "NEUTRAL"


def _parse_bool_env(key: str, default: bool = False) -> bool:
    raw = str(os.environ.get(key, "")).strip().lower()
    return raw in ("1", "true", "yes", "on") if raw else default


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _airlock_sanitize_input(message: str) -> tuple[str, bool]:
    sanitized = message.replace("\x00", "").strip()
    return sanitized, bool(sanitized)


def _looks_like_active_task_context(turns: list[dict[str, str]]) -> bool:
    if not turns:
        return False
    recent_turns = list(turns[-8:])
    joined = "\n".join(str(turn.get("content") or "") for turn in recent_turns).lower()
    if any(keyword.lower() in joined for keyword in _TASK_CONTEXT_KEYWORDS):
        return True
    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(recent_turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
        ),
        "",
    )
    if len(latest_assistant) >= 40 and any(
        phrase in latest_assistant
        for phrase in ("从哪里开始", "具体", "下一步", "准备好", "继续", "展开", "思路", "方案")
    ):
        return True
    return False


def _pick_non_repeating_variant(*, variants: list[str], last_reply: str, request_id: str) -> str:
    if not variants:
        templates = _mapping_section(_load_runtime_texts(), "variant_selection")
        return _mapping_text(templates, "empty_variants_fallback")
    normalized_last = str(last_reply or "").strip()
    start = sum(ord(ch) for ch in str(request_id or "")) % len(variants)
    ordered = variants[start:] + variants[:start]
    for variant in ordered:
        if str(variant).strip() != normalized_last:
            return variant
    return ordered[0]


def _maybe_build_phatic_fastpath_reply(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
    load_continuity_turns: Callable[..., list[dict[str, str]]] | None = None,
    looks_like_active_task_context: Callable[[list[dict[str, str]]], bool] | None = None,
    continuation_query_re: Any | None = None,
    greeting_fastpath_re: Any | None = None,
    presence_fastpath_re: Any | None = None,
    ack_fastpath_re: Any | None = None,
) -> str | None:

    normalized = str(user_message or "").strip()
    if not normalized:
        return None

    _load_turns = load_continuity_turns or _load_continuity_turns
    _looks_active = looks_like_active_task_context or _looks_like_active_task_context
    _continuation_query_re = continuation_query_re or _CONTINUATION_QUERY_RE
    _greeting_fastpath_re = greeting_fastpath_re or _GREETING_FASTPATH_RE
    _presence_fastpath_re = presence_fastpath_re or _PRESENCE_FASTPATH_RE
    _ack_fastpath_re = ack_fastpath_re or _ACK_FASTPATH_RE
    turns = _load_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=24,
    )
    active_task = _looks_active(turns)

    if _continuation_query_re.match(normalized):
        if active_task:
            return None
        category = "followup"
    elif _greeting_fastpath_re.match(normalized):
        category = "greeting"
    elif _presence_fastpath_re.match(normalized):
        category = "presence"
    elif _ack_fastpath_re.match(normalized):
        if active_task:
            return None
        category = "ack"
    else:
        return None


    last_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
        ),
        "",
    )

    templates = _mapping_section(_load_runtime_texts(), "phatic_fastpath")
    category_notes = _mapping_section(templates, "category_notes")
    repeated_warning = ""
    if last_assistant:
        repeated_warning = _mapping_text(
            templates,
            "repeated_warning_template",
            last_assistant=last_assistant,
        )
    return _mapping_text(category_notes, category) + repeated_warning


def _extract_delegate_visible_text(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    try:
        from host_brain.app.intelligent_runtime import _normalize_narrative_payload  # noqa: PLC0415

        normalized = _normalize_narrative_payload(normalized)
    except Exception:
        pass
    return str(normalized or "").strip()


def _render_delegated_result_naturally(
    *,
    agent_id: str,
    result_text: str,
    reply_template: str | None,
    extract_delegate_visible_text: Callable[[str], str] | None = None,
) -> str:
    _extract_visible = extract_delegate_visible_text or _extract_delegate_visible_text
    visible = _extract_visible(result_text)
    templates = _mapping_section(_load_runtime_texts(), "delegated_result")
    empty_visible_templates = _mapping_section(templates, "empty_visible_result")
    if not visible or _DELEGATE_INTERNAL_MARKER_RE.search(visible):
        if agent_id == "code_agent":
            return _mapping_text(empty_visible_templates, "code_agent")
        return _mapping_text(empty_visible_templates, "default")

    if reply_template and "{agent_result}" in reply_template:
        rendered = reply_template.replace("{agent_result}", visible).strip()
        if rendered:
            return rendered
    if agent_id == "code_agent":
        return _mapping_text(
            templates,
            "code_agent_prefix_template",
            agent_result=visible,
        ).strip()
    return visible


def _resolve_retrieval_timeout_seconds() -> float:
    raw = str(os.environ.get("HOST_TOP_AGENT_RETRIEVAL_TIMEOUT_MS") or "").strip()
    try:
        timeout_ms = int(raw) if raw else 20_000
    except ValueError:
        timeout_ms = 20_000
    timeout_ms = max(500, min(timeout_ms, 60_000))
    return timeout_ms / 1000.0


def _maybe_build_task_progress_fastpath(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    task_progress_query_re: Any | None = None,
    task_failure_query_re: Any | None = None,
    task_step_index_re: Any | None = None,
) -> str | None:
    normalized = str(user_message or "").strip()
    _task_progress_query_re = task_progress_query_re or _TASK_PROGRESS_QUERY_RE
    _task_failure_query_re = task_failure_query_re or _TASK_FAILURE_QUERY_RE
    _task_step_index_re = task_step_index_re or _TASK_STEP_INDEX_RE
    if not normalized or not (_task_progress_query_re.search(normalized) or _task_failure_query_re.search(normalized)):
        return None

    try:
        from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

        summary = get_runtime_task_registry().summarize_for_chat(
            person_id=person_id,
            session_id=session_id,
            exclude_request_id=request_id,
        )
    except Exception:
        summary = None
    templates = _mapping_section(_load_runtime_texts(), "task_progress")
    default_labels = _mapping_section(templates, "defaults")
    if not summary:
        return _mapping_text(templates, "no_summary")

    child_tasks = [dict(item or {}) for item in list(summary.get("child_tasks") or []) if isinstance(item, dict)]
    step_match = _task_step_index_re.search(normalized)
    ask_failure = bool(_task_failure_query_re.search(normalized))


    selected_child = None
    if step_match:
        wanted_step = int(step_match.group(1))
        for child in child_tasks:
            meta = dict(child.get("meta") or {}) if isinstance(child.get("meta"), dict) else {}
            if int(meta.get("step_index") or 0) == wanted_step:
                selected_child = child
                break
    elif ask_failure:
        for child in child_tasks:
            if str(child.get("status") or "").strip().upper() in {"FAILED", "RECOVERING"}:
                selected_child = child
                break

    if selected_child is not None:
        selected_child_templates = _mapping_section(templates, "selected_child")
        meta = dict(selected_child.get("meta") or {}) if isinstance(selected_child.get("meta"), dict) else {}
        step_index = int(meta.get("step_index") or 0)
        default_step_label = _mapping_text(default_labels, "step_label")
        target_id = str(
            selected_child.get("target_id")
            or meta.get("target_id")
            or default_step_label
        ).strip() or default_step_label
        step_desc = str(meta.get("description") or selected_child.get("detail") or "").strip()
        child_status = str(selected_child.get("status") or "UNKNOWN").strip().upper()
        child_error = str(selected_child.get("error") or "").strip()
        recovery_text = str(selected_child.get("recovery_result_text") or "").strip()
        if child_status in {"FAILED", "RECOVERING"}:
            parts = [
                _mapping_text(
                    selected_child_templates,
                    "failed_intro_template",
                    step_index=step_index,
                    target_id=target_id,
                ),
                _mapping_text(selected_child_templates, "failed_status"),
            ]
            if step_desc:
                parts.append(
                    _mapping_text(
                        selected_child_templates,
                        "failed_detail_template",
                        step_desc=step_desc,
                    )
                )
            if child_error:
                parts.append(
                    _mapping_text(
                        selected_child_templates,
                        "failed_error_template",
                        error_text=child_error,
                    )
                )
            if recovery_text:
                parts.append(
                    _mapping_text(
                        selected_child_templates,
                        "failed_recovery_template",
                        recovery_text=recovery_text,
                    )
                )
            return "".join(parts)
        parts = [
            _mapping_text(
                selected_child_templates,
                "completed_intro_template",
                step_index=step_index,
                target_id=target_id,
            ),
            _mapping_text(selected_child_templates, "completed_status"),
        ]
        if step_desc:
            parts.append(
                _mapping_text(
                    selected_child_templates,
                    "completed_detail_template",
                    step_desc=step_desc,
                )
            )
        return "".join(parts)

    status = str(summary.get("status") or "UNKNOWN").strip().upper()
    if ask_failure and status in {"FAILED", "RECOVERING"}:
        failed_summary_templates = _mapping_section(templates, "failed_summary")
        default_task_label = _mapping_text(default_labels, "task_label")
        target_id = str(summary.get("target_id") or default_task_label).strip() or default_task_label
        error_text = str(summary.get("error") or "").strip()
        recovery_text = str(summary.get("recovery_result_text") or "").strip()
        parts = [
            _mapping_text(
                failed_summary_templates,
                "intro_template",
                target_id=target_id,
            )
        ]
        if error_text:
            parts.append(
                _mapping_text(
                    failed_summary_templates,
                    "error_template",
                    error_text=error_text,
                )
            )
        if recovery_text:
            parts.append(
                _mapping_text(
                    failed_summary_templates,
                    "recovery_template",
                    recovery_text=recovery_text,
                )
            )
        return "".join(parts)
    default_task_label = _mapping_text(default_labels, "task_label")
    target_id = str(summary.get("target_id") or default_task_label).strip() or default_task_label
    progress_message = str(summary.get("progress_message") or summary.get("detail") or "").strip()
    recovery_result_text = str(summary.get("recovery_result_text") or "").strip()
    if status in {"RUNNING", "RECOVERING"}:
        running_summary_templates = _mapping_section(templates, "running_summary")
        parts = [
            _mapping_text(
                running_summary_templates,
                "intro_template",
                target_id=target_id,
            )
        ]
        if progress_message:
            parts.append(
                _mapping_text(
                    running_summary_templates,
                    "progress_template",
                    progress_message=progress_message,
                )
            )
        if recovery_result_text:
            parts.append(
                _mapping_text(
                    running_summary_templates,
                    "recovery_template",
                    recovery_text=recovery_result_text,
                )
            )
        return "".join(parts)
    finished_summary_templates = _mapping_section(templates, "finished_summary")
    parts = [
        _mapping_text(
            finished_summary_templates,
            "intro_template",
            target_id=target_id,
            status_text=status.lower(),
        )
    ]
    if progress_message:
        parts.append(
            _mapping_text(
                finished_summary_templates,
                "progress_template",
                progress_message=progress_message,
            )
        )
    if recovery_result_text:
        parts.append(
            _mapping_text(
                finished_summary_templates,
                "recovery_template",
                recovery_text=recovery_result_text,
            )
        )
    return "".join(parts)


def _maybe_build_previous_meaning_answer(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    render_target_lang: str,
) -> str | None:
    normalized = str(user_message or "").strip()
    if not normalized or not _CLARIFICATION_DIRECT_QUERY_RE.match(normalized):
        return None

    turns = _load_continuity_turns(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        render_target_lang=render_target_lang,
        fetch_limit=24,
    )
    if not turns:
        return None

    latest_assistant = next(
        (
            str(turn.get("content") or "").strip()
            for turn in reversed(turns)
            if str(turn.get("role") or "").strip().lower() == "assistant"
            and str(turn.get("content") or "").strip()
        ),
        "",
    )
    if not latest_assistant:
        return None

    templates = _mapping_section(_load_runtime_texts(), "clarification_direct_answer")
    return _mapping_text(
        templates,
        "assistant_meaning_template",
        latest_assistant=latest_assistant,
        default="我刚才的意思是：{latest_assistant}",
    )


def _should_buffer_stream_output(
    *,
    emotion_context: Mapping[str, Any] | None,
    bond_summary: Mapping[str, Any] | None,
) -> bool:
    if not _parse_bool_env("HOST_STREAM_REFLECTION_BUFFER_ENABLED", default=True):
        return False
    try:
        from host_brain.app.dependencies import get_reflection_chain  # noqa: PLC0415

        reflection = get_reflection_chain()
        return bool(
            reflection.should_reflect(
                emotion_tag=str((emotion_context or {}).get("emotion_tag") or ""),
                intent=str((bond_summary or {}).get("intent") or ""),
                bond_stage=str((bond_summary or {}).get("stage") or "STRANGER"),
                turn_count=int((emotion_context or {}).get("turn_count") or 1),
            )
        )
    except Exception:
        return False


def _extract_delegate_from_llm_output(full_text: str) -> tuple[str | None, str | None, str | None]:
    m = _DELEGATE_RE.search(full_text)
    if m:
        agent_id = m.group(1).strip()
        task = (m.group(2) or "").strip()
        before = full_text[: m.start()].strip()
        after = full_text[m.end():].strip()
        template = f"{before}\n{{agent_result}}\n{after}".strip()
        if agent_id in ALL_AGENT_IDS:
            return agent_id, task, template

    try:
        json_text = full_text.strip()
        if json_text.startswith("```"):
            lines = json_text.split("\n")
            json_lines: list[str] = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.strip() == "```" and in_block:
                    break
                if in_block:
                    json_lines.append(line)
            json_text = "\n".join(json_lines)

        parsed = json.loads(json_text)
        if isinstance(parsed, dict):
            delegate = parsed.get("delegate")
            if isinstance(delegate, dict):
                agent_id = str(delegate.get("agent") or "").strip()
                task = str(delegate.get("task") or "").strip()
                template = str(parsed.get("reply_template") or "").strip()
                if agent_id in ALL_AGENT_IDS and task:
                    return agent_id, task, template or None
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return None, None, None


def _extract_visible_text(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                narrative = parsed.get("narrative")
                if isinstance(narrative, dict):
                    return str(narrative.get("text_zh") or "").strip()
        except (json.JSONDecodeError, ValueError):
            pass
    return text


def _build_retrieval_bond_summary(summary_raw: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _coerce_trace_mapping(summary_raw)
    bond_stage = str(raw.get("bond_stage") or raw.get("stage") or "").strip()
    if not bond_stage:
        return {}

    bond_summary: dict[str, Any] = {
        "stage": bond_stage,
        "trend": str(raw.get("trend") or "").strip(),
        "capability_hint": str(raw.get("capability_hint") or "").strip(),
    }
    bond_score = _coerce_trace_score(raw.get("bond_score"))
    if bond_score is not None:
        bond_summary["score"] = bond_score
        bond_summary["bond_score"] = bond_score
    try:
        interaction_count = int(raw.get("interaction_count") or 0)
    except (TypeError, ValueError):
        interaction_count = 0
    if interaction_count > 0:
        bond_summary["interaction_count"] = interaction_count
    return bond_summary


async def _prefetch_retrieval_persona_inputs(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    emotion_context: Mapping[str, Any],
    mood_state: str | None,
    governor_decision: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    bond_summary: dict[str, Any] = {}
    bond_evidence: dict[str, Any] | None = None
    motive_state: dict[str, Any] = {}

    try:
        from host_brain.app.state_arbiter_queries import _resolve_state_arbiter_bond_fact  # noqa: PLC0415

        bond_summary_raw, bond_evidence = await _resolve_state_arbiter_bond_fact(
            session_id=session_id,
            request_id=request_id,
        )
        bond_summary = _build_retrieval_bond_summary(bond_summary_raw)
    except Exception as exc:
        _logger.debug("[%s] retrieval persona bond prefetch failed: %s", request_id, exc)

    try:
        from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415

        retrieval_emotion_context = dict(emotion_context or {})
        if mood_state and not str(retrieval_emotion_context.get("mood_state") or "").strip():
            retrieval_emotion_context["mood_state"] = mood_state
        runtime_flags = {"source": "top_agent_retrieval"}
        resolved_sys_mode = str(
            (governor_decision or {}).get("sys_mode")
            or (governor_decision or {}).get("mood_state")
            or ""
        ).strip().lower()
        if resolved_sys_mode:
            runtime_flags["sys_mode"] = resolved_sys_mode
        motive_state_raw = await asyncio.to_thread(
            get_limbic_agent().compute_runtime_motive_state,
            persona_user_id=person_id,
            current_topic=user_message,
            bond_summary=bond_summary,
            emotion_context=retrieval_emotion_context,
            pending_topics=[user_message] if user_message else [],
            runtime_flags=runtime_flags,
            now_ms=None,
        )
        motive_state = dict(motive_state_raw) if isinstance(motive_state_raw, Mapping) else {}
    except Exception as exc:
        _logger.debug("[%s] retrieval persona motive prefetch failed: %s", request_id, exc)

    return bond_summary, bond_evidence, motive_state


async def _top_agent_sse_generator_impl(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    governor_result: dict[str, Any],
    intent_id: str | None,
    metrics: Any,
    sse_fn: Callable[[dict[str, Any]], str] | None = None,
    airlock_sanitize_input_fn: Callable[[str], tuple[str, bool]] | None = None,
    airlock_sanitize_output_fn: Callable[[str], str] | None = None,
    maybe_build_task_progress_fastpath_fn: Callable[..., str | None] | None = None,
    maybe_build_recent_turn_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_previous_meaning_answer_fn: Callable[..., str | None] | None = None,
    maybe_build_phatic_fastpath_reply_fn: Callable[..., str | None] | None = None,
    async_post_process_fn: Callable[..., Any] | None = None,
    resolve_retrieval_timeout_seconds_fn: Callable[[], float] | None = None,
    prefetch_retrieval_persona_inputs_fn: Callable[..., Any] | None = None,
) -> AsyncGenerator[str, None]:
    _emit_sse = sse_fn or _sse
    _sanitize_input = airlock_sanitize_input_fn or _airlock_sanitize_input
    _sanitize_output = airlock_sanitize_output_fn or _airlock_sanitize_output
    _task_progress_fastpath = maybe_build_task_progress_fastpath_fn or _maybe_build_task_progress_fastpath
    _recent_turn_answer = maybe_build_recent_turn_answer_fn or _maybe_build_recent_turn_answer
    _previous_meaning_answer = maybe_build_previous_meaning_answer_fn or _maybe_build_previous_meaning_answer
    _phatic_fastpath = maybe_build_phatic_fastpath_reply_fn or _maybe_build_phatic_fastpath_reply
    _post_process = async_post_process_fn or _async_post_process
    _resolve_timeout = resolve_retrieval_timeout_seconds_fn or _resolve_retrieval_timeout_seconds
    _prefetch_persona_inputs = prefetch_retrieval_persona_inputs_fn or _prefetch_retrieval_persona_inputs

    user_message = str(request_body.message or "").strip()
    thread_id = getattr(request_body, "thread_id", None)
    person_id = resolve_person_id(
        getattr(request_body, "person_id", None),
        session_id=session_id,
        platform=getattr(request_body, "reply_channel", None),
        conversation_id=getattr(request_body, "reply_target", None),
    )

    sanitized_message, airlock_passed = _sanitize_input(user_message)
    if not airlock_passed:
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "AIRLOCK_EMPTY_MESSAGE",
                "request_id": request_id,
            }
        )
        return

    _pii_ingress_stats: dict[str, Any] = {}
    try:
        from host_brain.airlock.pii_filter import _check_channel, pii_scrub_text  # noqa: PLC0415

        if _check_channel("INGRESS"):
            _pii_result = pii_scrub_text(sanitized_message, context="ingress")
            sanitized_message = _pii_result.scrubbed_text
            if _pii_result.has_pii:
                _pii_ingress_stats = {
                    "detections": _pii_result.total_detections,
                    "rules": sorted({d["rule_id"] for d in _pii_result.detections}),
                }
    except Exception:
        pass

    yield _emit_sse({"phase": "PRESENCE", "event": "THINKING", "request_id": request_id})

    gov = chat_lite_governor(session_id=session_id, request_id=request_id)
    if gov["decision"] != "APPROVE":
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": gov.get("reason_code", "REJECTED"),
                "request_id": request_id,
            }
        )
        return

    recall_slot_text: dict[str, str] = {}
    bond_summary: dict[str, Any] = {}

    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        get_cortex_agent().emit_conversation_started(
            user_id=str(person_id or ""),
            session_id=str(session_id or ""),
            request_id=str(request_id or ""),
        )
    except Exception:
        pass

    task_progress_answer = _task_progress_fastpath(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
    )
    if task_progress_answer:
        narrative = _sanitize_output(str(task_progress_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "top_agent:task_progress_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang="zh",
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "TASK_PROGRESS_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "top_agent_used": True,
                    "dispatch_agent_used": False,
                    "chat_agent_used": False,
                    "top_agent_mode": "task_progress_direct",
                },
            )
        )
        return

    recent_turn_answer = _recent_turn_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang="zh",
    )
    if recent_turn_answer:
        narrative = _sanitize_output(str(recent_turn_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "top_agent:recent_turn_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang="zh",
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "RECENT_TURN_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "top_agent_used": True,
                    "dispatch_agent_used": False,
                    "chat_agent_used": False,
                    "top_agent_mode": "recent_turn_direct",
                },
            )
        )
        return

    clarification_direct_answer = _previous_meaning_answer(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang="zh",
    )
    if clarification_direct_answer:
        narrative = _sanitize_output(str(clarification_direct_answer).strip())
        total_latency_ms = int(time.time() * 1000) - ts_ms
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "narrative": narrative,
                "request_id": request_id,
                "audit_refs": {
                    "request_id": request_id,
                    "path": "top_agent:clarification_direct",
                    "latency_ms": total_latency_ms,
                    "emotion_tag": str(emotion_context.get("emotion_tag") or "NEUTRAL"),
                },
            }
        )
        asyncio.create_task(
            _post_process(
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=sanitized_message,
                narrative=narrative,
                emotion_context=emotion_context,
                bond_summary={},
                ts_ms=ts_ms,
                render_target_lang="zh",
                total_latency_ms=total_latency_ms,
                llm_latency_ms=0,
                first_token_ms=None,
                provider_name="",
                model_id="",
                buffer_stream=False,
                delegated_agents=[],
                retrieval_summary={"status": "SKIPPED", "reason_code": "CLARIFICATION_DIRECT"},
                gov_decision=governor_result,
                pii_ingress_stats=_pii_ingress_stats,
                pii_egress_stats={},
                trace_stage_meta={
                    "top_agent_used": True,
                    "dispatch_agent_used": False,
                    "chat_agent_used": False,
                    "top_agent_mode": "clarification_direct",
                },
            )
        )
        return

    phatic_fastpath_reply = _phatic_fastpath(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        render_target_lang="zh",
    )
    if phatic_fastpath_reply:
        from host_brain.app.chat_agent import (  # noqa: PLC0415
            ChatAgentInput,
            SubAgentContext,
            chat_agent_sse_generator,
        )

        phatic_ctx = SubAgentContext(
            agent_id="phatic_fastpath",
            task="根据当前寒暄语境生成简短自然回复",
            result_text=str(phatic_fastpath_reply).strip(),
            success=True,
            latency_ms=0,
        )
        chat_input = ChatAgentInput(
            request_id=request_id,
            session_id=session_id,
            user_message=sanitized_message,
            person_id=person_id,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            recall_slot_text=recall_slot_text,
            retrieval_evidence_items=[],
            retrieval_summary={"status": "SKIPPED", "reason_code": "PHATIC_FASTPATH"},
            sub_agent_context=phatic_ctx,
            render_target_lang="zh",
            reply_channel=getattr(request_body, "reply_channel", None),
            suppress_edge_tts=getattr(request_body, "suppress_edge_tts", None),
            suppress_edge_vts=getattr(request_body, "suppress_edge_vts", None),
            reply_depth_policy=(
                "gateway" if str(getattr(request_body, "reply_channel", "") or "").upper() == "GATEWAY"
                else "normal"
            ),
            thread_id=thread_id,
            ts_ms=ts_ms,
            intent_draft={**intent_draft, "fastpath_type": "phatic"},
            llm_runtime=None,
            trace_stage_meta={
                "top_agent_used": True,
                "dispatch_agent_used": False,
                "chat_agent_used": True,
                "chat_mode": "sub_agent",
                "chat_sub_agent_id": "phatic_fastpath",
                "top_agent_mode": "phatic_fastpath",
            },
        )
        async for event in chat_agent_sse_generator(chat_input):
            yield event
        return

    try:
        from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

        runtime_task_status_templates = _mapping_section(_load_runtime_texts(), "runtime_task_status")
        runtime_task_registry = get_runtime_task_registry()
        if runtime_task_registry.get_by_request_id(request_id) is None:
            runtime_task_registry.create_task(
                task_kind="chat_request",
                target_id="top_agent",
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                user_message=sanitized_message,
                detail=_mapping_text(
                    runtime_task_status_templates,
                    "pre_dispatch_detail",
                    default="正在整理上下文和检索记忆。",
                ),
                progress_message=_mapping_text(
                    runtime_task_status_templates,
                    "pre_dispatch_progress",
                    default="我正在整理上下文和检索记忆。",
                ),
                meta={"stage": "top_agent_pre_dispatch"},
            )
    except Exception as task_exc:
        _logger.debug("[%s] top_agent runtime task init failed: %s", request_id, task_exc)

    retrieval_task: asyncio.Task | None = None
    retrieval_evidence_items: list[dict[str, Any]] = []
    retrieval_summary: dict[str, Any] = {"status": "SKIPPED"}
    bond_evidence: dict[str, Any] | None = None
    retrieval_motive_state: dict[str, Any] = {}
    _injected_lang = str(getattr(request_body, "render_target_lang", None) or "").strip().lower()
    render_target_lang = _injected_lang if _injected_lang in ("zh", "ja") else "ja"
    _hard_state_summary = gov.get("hard_state_summary") if isinstance(gov.get("hard_state_summary"), Mapping) else {}
    retrieval_mood_state = (
        str(
            emotion_snapshot.get("mood_state")
            or emotion_context.get("mood_state")
            or gov.get("mood_state")
            or _hard_state_summary.get("mood_state")
            or ""
        ).strip().lower()
        or None
    )

    bond_summary, bond_evidence, retrieval_motive_state = await _prefetch_persona_inputs(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        emotion_context=emotion_context,
        mood_state=retrieval_mood_state,
        governor_decision=gov,
    )

    try:
        from host_brain.app.chat_retrieval_helpers import _run_retrieval  # noqa: PLC0415
        from host_brain.app.prompt_builder import infer_render_target_lang  # noqa: PLC0415

        if not _injected_lang:
            render_target_lang = infer_render_target_lang(user_message=sanitized_message)
        retrieval_task = asyncio.create_task(
            _run_retrieval(
                request_id=request_id,
                message=sanitized_message,
                person_id=person_id,
                session_id=session_id,
                ts_ms=ts_ms,
                emotion_tag=str(emotion_context.get("emotion_tag") or "") or None,
                render_target_lang=render_target_lang,
                mood_state=retrieval_mood_state,
                bond_summary=bond_summary or None,
                motive_state=retrieval_motive_state or None,
            )
        )
    except Exception as retrieval_exc:
        _logger.debug("[%s] top_agent retrieval init failed: %s", request_id, retrieval_exc)

    if retrieval_task is not None:
        try:
            retrieval_summary, retrieval_evidence_items = await asyncio.wait_for(
                retrieval_task,
                timeout=_resolve_timeout(),
            )
        except (asyncio.TimeoutError, Exception) as retrieval_exc:
            retrieval_summary = {"status": "DEGRADED", "error": str(retrieval_exc)[:100]}
            _logger.debug("[%s] top_agent retrieval failed/timeout: %s", request_id, retrieval_exc)

    if bond_evidence is not None:
        retrieval_evidence_items.append(bond_evidence)
    try:
        from host_brain.app.chat_retrieval_helpers import filter_retrieval_evidence_items_for_prompt  # noqa: PLC0415

        filtered_evidence_items, prompt_filter_meta = filter_retrieval_evidence_items_for_prompt(
            user_message=sanitized_message,
            retrieval_summary=retrieval_summary,
            retrieval_evidence_items=retrieval_evidence_items,
        )
        if prompt_filter_meta.get("applied"):
            retrieval_evidence_items = filtered_evidence_items
            retrieval_summary = dict(retrieval_summary or {})
            retrieval_summary["prompt_filter"] = dict(prompt_filter_meta)
    except Exception as prompt_filter_exc:
        _logger.debug("[%s] top_agent prompt retrieval filter failed: %s", request_id, prompt_filter_exc)

    try:
        from host_brain.app.chat_retrieval_helpers import _build_recall_pack_inputs  # noqa: PLC0415
        from host_brain.app.dependencies import get_recall_pack_builder  # noqa: PLC0415

        cpb = get_recall_pack_builder()
        if retrieval_evidence_items:
            recall_results = _build_recall_pack_inputs(retrieval_evidence_items)
            if recall_results:
                from host_brain.app.schemas.recall_pack import RECALL_SLOT_PRIORITY  # noqa: PLC0415

                try:
                    pack = cpb.build(recall_results, task_type="chat", request_id=request_id)
                except TypeError:
                    pack = cpb.build(recall_results, task_type="chat")
                for slot_name in RECALL_SLOT_PRIORITY:
                    slot = pack.get_slot(slot_name)
                    if slot and str(getattr(slot, "content", "") or "").strip():
                        recall_slot_text[slot_name] = str(slot.content).strip()
    except Exception as cpb_exc:
        _logger.debug("[%s] top_agent recall_pack failed: %s", request_id, cpb_exc)

    from host_brain.app.graph_config_builder import resolve_host_llm_runtime_async  # noqa: PLC0415

    llm_runtime = await resolve_host_llm_runtime_async(
        rollout_key=session_id,
        scene_type="CHAT",
    )

    if not llm_runtime.get("available"):
        fallback_narrative = _mapping_text(
            _load_runtime_texts(),
            "llm_unavailable_fallback",
        )
        total_latency_ms = int(time.time() * 1000) - ts_ms
        try:
            asyncio.create_task(
                _post_process(
                    request_id=request_id,
                    person_id=person_id,
                    session_id=session_id,
                    user_message=sanitized_message,
                    narrative=fallback_narrative,
                    emotion_context=emotion_context,
                    bond_summary=bond_summary,
                    ts_ms=ts_ms,
                    render_target_lang=render_target_lang,
                    total_latency_ms=total_latency_ms,
                    llm_latency_ms=0,
                    first_token_ms=None,
                    provider_name=str(llm_runtime.get("provider_name") or "fallback"),
                    model_id=str(llm_runtime.get("model_id") or "llm-unavailable-fallback"),
                    buffer_stream=False,
                    delegated_agents=[],
                    retrieval_summary=(retrieval_summary or {"status": "SKIPPED", "reason_code": "LLM_UNAVAILABLE"}),
                    gov_decision=governor_result,
                    pii_ingress_stats=_pii_ingress_stats,
                    pii_egress_stats={},
                    retrieval_evidence_items=list(retrieval_evidence_items or []),
                    trace_stage_meta={
                        "top_agent_used": True,
                        "dispatch_agent_used": False,
                        "chat_agent_used": False,
                        "top_agent_mode": "llm_unavailable",
                    },
                )
            )
        except Exception as post_process_exc:
            _logger.debug("[%s] top_agent llm unavailable post_process failed: %s", request_id, post_process_exc)
        yield _emit_sse(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "LLM_UNAVAILABLE",
                "request_id": request_id,
                "narrative": fallback_narrative,
            }
        )
        return

    from host_brain.app.dispatch_agent import dispatch_agent_sse_generator  # noqa: PLC0415

    async for event in dispatch_agent_sse_generator(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        governor_result=gov,
        intent_id=intent_id,
        metrics=metrics,
        person_id=person_id,
        sanitized_message=sanitized_message,
        recall_slot_text=recall_slot_text,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        pii_ingress_stats=_pii_ingress_stats,
    ):
        yield event


async def _async_post_process(
    *,
    request_id: str,
    person_id: str,
    session_id: str,
    user_message: str,
    narrative: str,
    emotion_context: dict[str, Any],
    bond_summary: dict[str, Any] | None,
    ts_ms: int,
    render_target_lang: str,
    total_latency_ms: int = 0,
    llm_latency_ms: int = 0,
    first_token_ms: int | None = None,
    provider_name: str = "",
    model_id: str = "",
    buffer_stream: bool = False,
    delegated_agents: list[dict[str, Any]] | None = None,
    retrieval_summary: dict[str, Any] | None = None,
    gov_decision: dict[str, Any] | None = None,
    pii_ingress_stats: dict[str, Any] | None = None,
    pii_egress_stats: dict[str, Any] | None = None,
    llm_input_digest: str | None = None,
    retrieval_evidence_items: list[dict[str, Any]] | None = None,
    motive_state: Mapping[str, Any] | None = None,
    response_policy: Mapping[str, Any] | None = None,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> None:
    try:
        await _persist_post_process_audit(
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            narrative=narrative,
            emotion_context=emotion_context,
            bond_summary=bond_summary,
            ts_ms=ts_ms,
            total_latency_ms=total_latency_ms,
            llm_latency_ms=llm_latency_ms,
            first_token_ms=first_token_ms,
            provider_name=provider_name,
            model_id=model_id,
            buffer_stream=buffer_stream,
            delegated_agents=delegated_agents,
            retrieval_summary=retrieval_summary,
            gov_decision=gov_decision,
            pii_ingress_stats=pii_ingress_stats,
            pii_egress_stats=pii_egress_stats,
            llm_input_digest=llm_input_digest,
            retrieval_evidence_items=retrieval_evidence_items,
            motive_state=motive_state,
            response_policy=response_policy,
            trace_stage_meta=trace_stage_meta,
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process audit persist failed: %s", request_id, exc)

    try:
        from host_brain.airlock.quarantine import classify_ingress  # noqa: PLC0415

        await asyncio.to_thread(classify_ingress, narrative)
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process airlock classify failed: %s", request_id, exc)

    try:
        from host_brain.app.observability_persist import persist_chat_episodic_memory  # noqa: PLC0415
        from host_brain.app.dependencies import get_episodic_store  # noqa: PLC0415

        store = get_episodic_store()
        if store is not None:
            mem_user = user_message
            mem_narrative = narrative
            try:
                from host_brain.airlock.pii_filter import _check_channel, pii_scrub_if_enabled  # noqa: PLC0415

                if _check_channel("MEMORY"):
                    mem_user = pii_scrub_if_enabled(user_message, context="memory_write_user")
                    mem_narrative = pii_scrub_if_enabled(narrative, context="memory_write_ai")
            except Exception:
                pass

            await asyncio.to_thread(
                persist_chat_episodic_memory,
                request_id=request_id,
                intent_id=request_id,
                person_id=person_id,
                session_id=session_id,
                user_message=mem_user,
                assistant_reply=mem_narrative,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
                episodic_store=store,
            )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process episodic failed: %s", request_id, exc)

    try:
        await _run_persona_session_end_hooks(
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            user_message=user_message,
            emotion_context=emotion_context,
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process persona session_end failed: %s", request_id, exc)

    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent  # noqa: PLC0415

        await get_cortex_agent().post_process_bond_update(
            request_id=request_id,
            session_id=session_id,
            user_message=user_message,
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process bond failed: %s", request_id, exc)

    try:
        from host_brain.tribe.cortex_agent import get_cortex_agent as _get_cortex  # noqa: PLC0415

        ec = dict(emotion_context or {})
        _get_cortex().emit_conversation_ended(
            user_id=str(person_id or ""),
            session_id=str(session_id or ""),
            request_id=str(request_id or ""),
            emotion_tag=str(ec.get("emotion_tag") or "NEUTRAL").strip().upper(),
            emotion_intensity=float(ec.get("emotion_intensity") or 0.5),
            summary={
                "user_message": str(user_message or ""),
                "user_message_preview": str(user_message or "")[:64],
                "assistant_reply": str(narrative or "")[:512],
                "ts_ms": int(ts_ms),
            },
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process conversation_ended tribe event failed: %s", request_id, exc)

    try:
        from host_brain.app.vts_emotion_driver import _drive_stream_vts_emotion  # noqa: PLC0415

        await _drive_stream_vts_emotion(
            request_id=request_id,
            emotion_context=emotion_context,
            ts_ms=ts_ms,
        )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process vts failed: %s", request_id, exc)

    try:
        tts_enabled = str(os.environ.get("HOST_STREAM_TTS_ENABLED") or "true").strip().lower()
        tts_sentence_stream = str(os.environ.get("HOST_TTS_SENTENCE_STREAM_ENABLED") or "true").strip().lower()
        skip_bulk_tts = tts_sentence_stream in ("1", "true", "yes", "on")
        if tts_enabled in ("1", "true", "yes", "on") and narrative.strip() and not skip_bulk_tts:
            from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415
            from host_brain.app.intelligent_runtime import resolve_tts_route  # noqa: PLC0415

            base_etag = str(emotion_context.get("emotion_tag") or "NEUTRAL").strip().upper()
            tts_route = resolve_tts_route(
                text=narrative,
                emotion_context={**dict(emotion_context), "emotion_tag": base_etag},
                bond_summary=bond_summary or None,
            )
            etag = str(tts_route.get("emotion_tag") or _infer_emotion_tag_for_tts(narrative, base_etag)).strip()
            intensity_raw = emotion_context.get("emotion_intensity")
            intensity: float | None = None
            if intensity_raw is not None:
                try:
                    intensity = max(0.0, min(1.0, float(intensity_raw)))
                except (TypeError, ValueError):
                    pass
            tts_result = await post_speak(
                text=narrative,
                request_id=request_id,
                session_id=session_id,
                speed=float(tts_route.get("speed") or 1.0),
                pitch=float(tts_route.get("pitch") or 0.0),
                emotion_tag=etag,
                emotion_intensity=intensity,
                style_note=str(tts_route.get("style_note") or ""),
                lang_hint=str(tts_route.get("lang_hint") or render_target_lang),
            )

            try:
                from host_brain.app.dependencies import get_ledger as _get_tts_ledger  # noqa: PLC0415

                _get_tts_ledger().log_voice_event(
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "event_type": "TTS_SPEAK",
                        "direction": "EGRESS",
                        "status": str(getattr(tts_result, "status", "OK")),
                        "latency_ms": int(getattr(tts_result, "latency_ms", 0)),
                        "payload": {
                            "text_len": len(narrative),
                            "emotion_tag": etag,
                            "speed": float(tts_route.get("speed") or 1.0),
                            "pitch": float(tts_route.get("pitch") or 0.0),
                            "lang_hint": str(tts_route.get("lang_hint") or render_target_lang),
                        },
                        "ts_ms": int(time.time() * 1000),
                    }
                )
            except Exception:
                pass
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process tts failed: %s", request_id, exc)

    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        drift_result = await asyncio.to_thread(
            get_prefrontal_agent().run_persona_drift_cycle,
            llm_output_text=narrative,
            user_input_text=user_message,
        )
        drift_level = str(dict(drift_result or {}).get("alert_level") or "").strip().upper()
        if drift_level in ("MODERATE", "SEVERE"):
            _logger.info(
                "[%s] 人格漂移检测: level=%s dims=%s",
                request_id,
                drift_level,
                dict(drift_result or {}).get("drift_dims"),
            )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process drift_detector failed: %s", request_id, exc)

    try:
        from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415

        growth_payload = await asyncio.to_thread(
            get_prefrontal_agent().run_growth_feedback_cycle,
            session_id=session_id,
            user_message=user_message,
            assistant_reply=narrative,
        )
        if bool(dict(growth_payload or {}).get("applied")):
            _logger.info(
                "[%s] 人格成长: user=%s rules=%s",
                request_id,
                dict(growth_payload or {}).get("user_id"),
                dict(growth_payload or {}).get("triggered_rules"),
            )
    except Exception as exc:
        _logger.debug("[%s] top_agent post_process growth_feedback failed: %s", request_id, exc)


__all__ = [
    "_format_template_text",
    "_load_template_mapping",
    "_load_runtime_texts",
    "_mapping_section",
    "_mapping_text",
    "_parse_bool_env",
    "_sse",
    "_airlock_sanitize_input",
    "_airlock_sanitize_output",
    "_build_reply_preview",
    "_load_continuity_turns",
    "_detect_recent_turn_query_role",
    "_looks_like_active_task_context",
    "_pick_non_repeating_variant",
    "_maybe_build_phatic_fastpath_reply",
    "_extract_delegate_visible_text",
    "_render_delegated_result_naturally",
    "_resolve_retrieval_timeout_seconds",
    "_normalize_retrieval_summary_for_audit",
    "_coerce_trace_mapping",
    "_coerce_trace_score",
    "_coerce_trace_bool",
    "_summarize_motive_state_for_trace",
    "_summarize_response_policy_for_trace",
    "_summarize_emotion_context_for_trace",
    "_summarize_persona_resident_memory_for_trace",
    "_normalize_trace_stage_meta",
    "_resolve_envelope_confidence",
    "_build_trace_output_digest",
    "_detect_trace_fallback_used",
    "_build_trace_agent_envelope",
    "_build_trace_stage_node",
    "_persist_post_process_audit",
    "_build_subagent_gateway_from_runtime",
    "_maybe_build_task_progress_fastpath",
    "_maybe_build_recent_turn_answer",
    "_maybe_build_previous_meaning_answer",
    "_should_buffer_stream_output",
    "_extract_delegate_from_llm_output",
    "_extract_visible_text",
    "_build_retrieval_bond_summary",
    "_prefetch_retrieval_persona_inputs",
    "_resolve_persona_session_emotion",
    "_run_persona_session_end_hooks",
    "_infer_emotion_tag_for_tts",
    "_async_post_process",
]


