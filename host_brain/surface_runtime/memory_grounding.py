"""Canonical runtime responsibility extracted from the former app hotspot."""

from __future__ import annotations

from typing import Any
from collections.abc import AsyncGenerator, Mapping, Sequence
import re

from host_brain.surface_runtime.common import (
    _clean_list,
    _safe_mapping,
)
from host_brain.surface_runtime.eligibility import _has_multimodal_payload
from host_brain.surface_runtime.policies import (
    BAD_TEASE_PREFIX_RE as _BAD_TEASE_PREFIX_RE,
    CURRENT_COVER_CLAIM_RE as _CURRENT_COVER_CLAIM_RE,
    MAIN_CHAIN_META_CHAT_RE as _MAIN_CHAIN_META_CHAT_RE,
    PERCEPTION_EVIDENCE_REQUIRED_RE as _PERCEPTION_EVIDENCE_REQUIRED_RE,
    PERCEPTION_PRODUCTION_TASK_RE as _PERCEPTION_PRODUCTION_TASK_RE,
)
from host_brain.surface_runtime.subagent_context import (
    _surface_sub_agent_id,
    _surface_sub_agent_result_text,
)
def _apply_surface_sub_agent_result_guard(text: str, *, agent_input: Any) -> tuple[str, dict[str, Any]]:
    result_text = _surface_sub_agent_result_text(agent_input)
    agent_id = _surface_sub_agent_id(agent_input)
    if not agent_id or not result_text:
        return text, {"applied": False}

    candidate = str(text or "").strip()
    result_text = result_text.strip()
    if _looks_like_sub_agent_refusal(result_text):
        return candidate, {"applied": False, "agent_id": agent_id, "reason_code": "SUB_AGENT_RESULT_REFUSAL"}

    if not candidate:
        return result_text[:4000], {
            "applied": True,
            "agent_id": agent_id,
            "reason_code": "EMPTY_SURFACE_RESULT_REPLACED",
        }

    if _looks_like_surface_ignored_sub_agent_result(candidate):
        return result_text[:4000], {
            "applied": True,
            "agent_id": agent_id,
            "reason_code": "SURFACE_IGNORED_SUB_AGENT_RESULT",
        }
    if _looks_like_surface_omitted_sub_agent_result(candidate, result_text=result_text, agent_id=agent_id):
        return result_text[:4000], {
            "applied": True,
            "agent_id": agent_id,
            "reason_code": "SURFACE_OMITTED_SUB_AGENT_RESULT",
        }

    return candidate, {"applied": False}


def _looks_like_sub_agent_refusal(text: str) -> bool:
    return bool(
        re.search(
            r"(sorry|cannot|can't|can not|prohibit|refuse|抱歉|不能|无法|做不了|拒绝|不允许|安全指南)",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def _looks_like_surface_ignored_sub_agent_result(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    return bool(
        re.search(
            r"(已安排|正在(后台)?(处理|扫描|执行)|等待(后台)?|稍后|做不了|不能帮|不能执行|无法执行|"
            r"sorry|cannot|can't|can not)",
            normalized,
            re.IGNORECASE,
        )
    )


def _looks_like_surface_omitted_sub_agent_result(text: str, *, result_text: str, agent_id: str) -> bool:
    candidate = str(text or "").strip()
    result = str(result_text or "").strip()
    if not candidate or not result:
        return True

    if agent_id == "pentest_agent" and re.search(r"(只读扫描|端口|HTTP|finding|数据暴露|安全配置|exploit)", result, re.IGNORECASE):
        required_markers = (
            "只读",
            "扫描",
            "端口",
            "开放",
            "未开放",
            "HTTP",
            "状态",
            "secret",
            "数据暴露",
            "安全配置",
            "finding",
            "exploit",
            "写入",
            "破坏性",
        )
        if not any(marker.lower() in candidate.lower() for marker in required_markers):
            return True

    result_tokens = _sub_agent_result_anchor_tokens(result)
    if not result_tokens:
        return False
    candidate_lower = candidate.lower()
    matched = sum(1 for token in result_tokens if token.lower() in candidate_lower)
    if len(result_tokens) == 1:
        return matched == 0 and len(candidate) < 120
    return matched == 0 and len(candidate) < 180


def _sub_agent_result_anchor_tokens(text: str) -> tuple[str, ...]:
    tokens = re.findall(
        r"(?:\d{1,3}\.){3}\d{1,3}|https?://[^\s，。；]+|\b\d{1,5}/tcp\b|\b\d{2,5}\b",
        str(text or ""),
        re.IGNORECASE,
    )
    return tuple(dict.fromkeys(token.strip() for token in tokens if token.strip()))[:8]


def _sanitize_main_chain_meta_reply(text: str, *, agent_input: Any) -> str:
    message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not _MAIN_CHAIN_META_CHAT_RE.search(message):
        return text
    cleaned = _BAD_TEASE_PREFIX_RE.sub("", str(text or "").strip(), count=1).strip()
    if not cleaned:
        cleaned = str(text or "").strip()
    if any(marker in message for marker in ("耗时", "延迟", "哪一段", "哪段", "先怀疑")) and not re.search(
        r"(路由|surface|入口|thalamus|fallback|模型|生成|检索|回退)",
        cleaned,
        re.IGNORECASE,
    ):
        return cleaned
    if any(marker in message for marker in ("体感", "判断", "主链效果", "主链表现")) and len(cleaned) < 18:
        return cleaned
    return cleaned


def _surface_memory_packet_for_output_guard(
    *,
    agent_input: Any,
    trace_stage_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for container in (
        _safe_mapping(trace_stage_meta),
        _safe_mapping(getattr(agent_input, "trace_stage_meta", None)),
        _safe_mapping(getattr(agent_input, "retrieval_summary", None)),
    ):
        raw = container.get("surface_memory_recall_packet") or container.get("memory_recall_packet")
        if isinstance(raw, Mapping):
            candidates.append(dict(raw))
    if not candidates:
        return {}
    candidates.sort(key=_surface_memory_packet_diagnostic_score, reverse=True)
    return candidates[0]


def _surface_memory_packet_diagnostic_score(packet: Mapping[str, Any]) -> int:
    trace = _safe_mapping(packet.get("trace"))
    readiness = _safe_mapping(trace.get("recall_answer_readiness"))
    score = 0
    if readiness:
        score += 8
    if str(packet.get("reason_code") or "").strip():
        score += 4
    if "degraded" in packet:
        score += 2
    if trace:
        score += 2
    if list(packet.get("facts") or []):
        score += 1
    return score


def _surface_memory_readiness(packet: Mapping[str, Any]) -> dict[str, Any]:
    trace = _safe_mapping(packet.get("trace"))
    return _safe_mapping(trace.get("recall_answer_readiness"))


def _surface_memory_missing_detail_selection(readiness: Mapping[str, Any]) -> bool:
    contract_id = str(readiness.get("recall_contract_id") or "").strip()
    query_kind = str(readiness.get("query_kind") or "").strip()
    response_mode = str(readiness.get("response_mode") or "").strip()
    reasons = {str(item or "").strip() for item in list(readiness.get("reasons") or []) if str(item or "").strip()}
    detail = _safe_mapping(readiness.get("detail_evidence"))
    detail_basis = str(detail.get("basis") or "").strip()
    is_selection = contract_id == "anaphoric_selection" or query_kind == "anaphoric_selection"
    missing_detail = (
        "missing_detail_evidence" in reasons
        or response_mode == "fetch_or_request_detail_evidence"
        or (
            bool(detail.get("required"))
            and detail_basis not in {"body_evidence", "lightnovel_deep_review", "generic_evidence_review"}
        )
    )
    return bool(is_selection and missing_detail)


def _surface_memory_card_fact_lines(packet: Mapping[str, Any], *, limit: int = 4) -> list[str]:
    facts = "\n".join(str(item or "") for item in list(packet.get("facts") or []))
    lines: list[str] = []
    for part in re.split(r"[；;]\s*(?=\d+\.)", facts):
        match = re.search(r"(?:^|[；;]\s*)\d+\.\s*([^｜\n；;]+)", part)
        if not match:
            continue
        title = re.sub(r"^(今天|今日|昨天|前天|三天前|四天前|五天前|本次|这次)\s*推荐\s*[A-ZＡ-Ｚ一二三四五六七八九十0-9]*\s*[:：]\s*", "", match.group(1).strip())
        title = re.sub(r"^推荐\s*[A-ZＡ-Ｚ一二三四五六七八九十0-9]*\s*[:：]\s*", "", title).strip(" 《》")
        reason_match = re.search(r"｜看点[:：]\s*([^｜；;]+)", part)
        intro_match = re.search(r"｜简介[:：]\s*([^｜；;]+)", part)
        bits = [title]
        if reason_match:
            bits.append(f"看点：{reason_match.group(1).strip()[:70]}")
        if intro_match:
            bits.append(f"简介：{intro_match.group(1).strip()[:90]}")
        line = "｜".join(bit for bit in bits if bit)
        if line:
            lines.append(line)
        if len(lines) >= max(1, int(limit)):
            break
    if lines:
        return _clean_list(lines)
    trace = _safe_mapping(packet.get("trace"))
    for key in ("selected_collection_items", "selected_entity_items"):
        for item in list(trace.get(key) or []):
            item_map = _safe_mapping(item)
            title = str(item_map.get("title") or item_map.get("raw_title") or item_map.get("item_title") or "").strip()
            if title:
                lines.append(title)
            if len(lines) >= max(1, int(limit)):
                return _clean_list(lines)
    return _clean_list(lines)


def _surface_perception_evidence_gate_text(agent_input: Any) -> str:
    if _has_multimodal_payload(agent_input):
        return ""
    message = str(getattr(agent_input, "user_message", "") or "").strip()
    if not message:
        return ""
    if _PERCEPTION_PRODUCTION_TASK_RE.search(message) and not _CURRENT_COVER_CLAIM_RE.search(message):
        return ""
    if "《" in message and "》" in message and not re.search(r"(图|图片|照片|截图|封面|画面|看起来)", message):
        return ""
    if not (_PERCEPTION_EVIDENCE_REQUIRED_RE.search(message) or _CURRENT_COVER_CLAIM_RE.search(message)):
        return ""
    return "我这边没拿到图、附件或明确书名证据，不能装作看见了。你把图片或书名发过来，我再认真看。"
