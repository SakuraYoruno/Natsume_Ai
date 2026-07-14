"""Skill-first executive runtime core.

This module owns the canonical execution path behind:
chat_handler -> cortex_orchestrator -> executive_router -> executive_runtime.

The runtime only routes to skill families / executors. Legacy dispatch/top-level
entrypoints are intentionally not reintroduced here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Mapping

from host_brain.executive_runtime.capability_gap import detect_capability_gap
from host_brain.executive_runtime.ports import ExecutiveRuntimeApplicationPorts
from host_brain.persona.relational_chat import build_relational_chat_reason_code
from host_brain.executive_runtime.capability_synthesis import synthesize_runtime_capability
from host_brain.executive_runtime.skill_dag import SkillDagNode, SkillDagPlan, build_skill_dag_plan
from host_brain.executive_runtime.skill_routing import build_skill_route_decision
from host_brain.core.orchestration_settings import get_orchestration_settings
from host_brain.agent_runtime.skill_ids import AGENT_RUN_MODE_BY_SKILL_ID, is_agent_runtime_skill
from host_brain.skills.executor_registry import (
    bind_validated_skill_authority,
    dispatch_executor_payload,
)


_logger = logging.getLogger("Natsume.executive_runtime_core")

_RECENT_TURN_PATTERNS = (
    "刚刚",
    "上句",
    "上一句",
    "上一条",
    "刚才说",
    "我刚才说",
    "你刚才说",
    "我们刚才",
)

_TASK_PROGRESS_PATTERNS = (
    "进度",
    "做到哪",
    "做完了吗",
    "还在跑",
    "执行到哪",
    "任务状态",
    "现在怎么样",
    "还没好",
)

_MULTI_STEP_MARKERS = (
    "然后",
    "并且",
    "同时",
    "接着",
    "再把",
    "之后",
)

_ACTION_SPLIT_RE = re.compile(r"(?:然后|并且|同时|接着|之后|再把|再去|再来)")
_SIMPLE_TECH_ADVICE_TERMS = (
    "报错",
    "错误",
    "异常",
    "失败",
    "缺包",
    "导入",
    "端口",
    "日志",
    "启动",
    "连接不上",
    "连不上",
    "没响应",
    "无响应",
    "排查",
    "怎么查",
    "怎么排查",
    "该怎么",
    "怎么办",
    "如何",
    "先看",
    "看什么",
    "module not found",
    "modulenotfounderror",
    "no module named",
    "openai_api_key",
    "环境变量",
    "neo4j",
    "bolt://",
    "chroma",
    "chromadb",
    "http://",
    "https://",
    "why",
    "error",
    "port",
    "timeout",
)
_SIMPLE_TECH_ACTION_BLOCKERS = (
    "开始处理",
    "开始做",
    "继续做",
    "直接改",
    "帮我改",
    "修改",
    "修复",
    "实现",
    "重构",
    "删除",
    "清理",
    "整理仓库",
    "提交",
    "跑测试",
    "执行",
    "接入",
    "迁移",
    "写代码",
)
_SIMPLE_TASK_SPLIT_RE = re.compile(r"(?:下一步|接下来|后面|任务).{0,12}(?:拆成|分成|列成).{0,8}(?:三|3)件", re.IGNORECASE)
_SKILL_EXEC_FAIL_STATUS = {"failed", "rejected", "degraded", "error"}
_SKILL_EXEC_FAIL_CODES = {
    "SCHED_NO_CONTENT",
    "SKILL_EXECUTION_FAILED",
    "SKILL_EXECUTION_ERROR",
    "MEMORY_WRITER_UNAVAILABLE",
    "MEMORY_STORE_UNAVAILABLE",
}
_SKILL_EXEC_NO_EFFECT_MARKERS = (
    "no memory writer available",
    "no memory store available",
    "no memory backend available",
    "skipping diary",
    "no conversations on",
)


@dataclass(frozen=True)
class _RouteSelection:
    route_type: str
    target_id: str
    reason_code: str


@dataclass(frozen=True)
class _ExecutionOutcome:
    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict[str, Any]


def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text


def _run_awaitable_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    result_box: dict[str, Any] = {}
    error_box: dict[str, BaseException] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result_box["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            error_box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=_worker, name="memory-skill-awaitable", daemon=True)
    thread.start()
    done.wait()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")


def _build_memory_exec_context(
    *,
    ports: ExecutiveRuntimeApplicationPorts,
    request_id: str,
    session_id: str,
    intent_id: str,
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    try:
        episodic_store = ports.get_episodic_store()
    except Exception as exc:  # pragma: no cover - depends on runtime services
        episodic_store = None
        _logger.debug("memory skill episodic_store unavailable: %s", exc)

    try:
        semantic_recaller = ports.get_semantic_recaller()
    except Exception as exc:  # pragma: no cover - depends on runtime services
        semantic_recaller = None
        _logger.debug("memory skill semantic_recaller unavailable: %s", exc)

    if episodic_store is None and semantic_recaller is None:
        return {}

    default_emotion_tag = _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL"

    def _episodic_writer(
        *,
        content: str,
        tags: list[str] | None = None,
        importance: float | None = None,
        source: str | None = None,
        emotion_tag: str | None = None,
        session_id: str | None = None,
    ) -> str:
        _ = importance
        if episodic_store is None:
            raise RuntimeError("episodic store unavailable")
        evidence_refs = [f"request:{request_id}"]
        session_ref = str(session_id or "").strip()
        if session_ref:
            evidence_refs.append(f"session:{session_ref}")
        source_ref = str(source or "").strip()
        if source_ref:
            evidence_refs.append(f"source:{source_ref}")
        for tag in list(tags or []):
            tag_text = str(tag or "").strip()
            if tag_text:
                evidence_refs.append(f"tag:{tag_text}")
        result = episodic_store.write_memory(
            text=str(content or "").strip(),
            evidence_refs=evidence_refs,
            request_id=request_id,
            intent_id=intent_id,
            emotion_tag=str(emotion_tag or default_emotion_tag).strip().upper() or default_emotion_tag,
        )
        if not bool(result.success):
            raise RuntimeError(str(result.reason_code or result.detail or "memory write failed"))
        return str(result.memory_id or "")

    def _semantic_recaller_wrapper(
        *,
        query: str,
        top_k: int = 5,
        threshold: float = 0.6,
        tags: list[str] | None = None,
        session_id: str | None = None,
        search_scope: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = tags
        if semantic_recaller is None:
            return []
        filter_metadata: dict[str, Any] = {}
        session_ref = str(session_id or "").strip()
        if session_ref:
            filter_metadata["session_id"] = session_ref
        if str(search_scope or "").strip().lower() == "long_term":
            filter_metadata["memory_layer"] = "long_term"
        recall_result = _run_awaitable_sync(
            semantic_recaller.recall(
                str(query or ""),
                top_k=int(top_k or 5),
                threshold=float(threshold) if threshold is not None else None,
                filter_metadata=filter_metadata or None,
            )
        )
        hits = list(getattr(recall_result, "hits", []) or [])
        normalized_hits: list[dict[str, Any]] = []
        for hit in hits:
            normalized_hits.append(
                {
                    "doc_id": str(getattr(hit, "doc_id", "") or ""),
                    "content": str(getattr(hit, "content", "") or ""),
                    "score": float(getattr(hit, "score", 0.0) or 0.0),
                    "metadata": dict(getattr(hit, "metadata", {}) or {}),
                    "source": str(getattr(hit, "source", "") or ""),
                }
            )
        return normalized_hits

    return {
        "episodic_store": episodic_store,
        "episodic_writer": _episodic_writer,
        "semantic_recaller": _semantic_recaller_wrapper,
    }


def _coerce_mapping(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _iter_candidate_keys() -> tuple[str, ...]:
    return (
        "route",
        "target_id",
        "action",
    )


def _looks_like_recent_turn_query(user_message: str) -> bool:
    message = _normalize_text(user_message)
    return bool(message) and any(marker in message for marker in _RECENT_TURN_PATTERNS)


def _looks_like_task_progress_query(user_message: str) -> bool:
    message = _normalize_text(user_message)
    return bool(message) and any(marker in message for marker in _TASK_PROGRESS_PATTERNS)


def _looks_multi_step_request(user_message: str, intent_draft: dict[str, Any]) -> bool:
    if isinstance(intent_draft.get("planned_steps"), list) and len(intent_draft.get("planned_steps") or []) > 1:
        return True
    if isinstance(intent_draft.get("steps"), list) and len(intent_draft.get("steps") or []) > 1:
        return True
    message = _normalize_text(user_message)
    return bool(message) and any(marker in message for marker in _MULTI_STEP_MARKERS)


def _extract_skill_target(intent_draft: dict[str, Any], skill_registry: Any) -> str:
    candidate = _normalize_text(intent_draft.get("target_id"))
    if candidate:
        try:
            if skill_registry.get(candidate) is not None:
                return candidate
        except Exception:
            pass
    candidate = _normalize_text(intent_draft.get("action"))
    if candidate and candidate not in {"exec_intent", "narrative_only", "noop", "work_code", "work_translate", "task_progress", "recent_turn"}:
        try:
            if skill_registry.get(candidate) is not None:
                return candidate
        except Exception:
            pass
    return ""


def _extract_delegate_target(intent_draft: dict[str, Any]) -> str:
    candidate = _normalize_text(intent_draft.get("target_id"))
    if candidate.endswith("_agent"):
        return candidate
    candidate = _normalize_text(intent_draft.get("action"))
    if candidate.endswith("_agent"):
        return candidate
    return ""


def _surface_control_requests_memory_recall(surface_task_control: Any, intent_draft: dict[str, Any]) -> bool:
    if not isinstance(surface_task_control, dict) or not surface_task_control:
        return False
    task_mode = _normalize_text(surface_task_control.get("task_mode"), lower=True)
    response_move = _normalize_text(surface_task_control.get("response_move"), lower=True)
    target_hint = _normalize_text(intent_draft.get("target_id") or intent_draft.get("skill_id"), lower=True)
    action_hint = _normalize_text(intent_draft.get("action"), lower=True)
    recall_markers = {
        "memory_recall",
        "memory_followup",
        "recall_memory",
        "recall_preference",
        "recall_relationship",
    }
    if task_mode in recall_markers or response_move in recall_markers:
        return True
    return target_hint == "memory_recall" or action_hint == "memory_recall"


def _message_requests_memory_recall(user_message: str) -> bool:
    message = _normalize_text(user_message)
    if message and (
        any(token in message for token in ("回忆检查", "记忆验收", "同会话验收", "新会话验收", "最终同会话验收"))
        or ("验收" in message and any(token in message for token in ("列出", "复述", "回答", "说出")))
        or (
            any(token in message for token in ("测试代号", "海边灯塔", "预热指标", "推荐格式"))
            and any(token in message for token in ("是什么", "指什么", "列出", "复述", "当前", "刚才", "刚刚"))
        )
    ):
        return True
    try:
        from host_brain.memory.life_memory_query import (  # noqa: PLC0415
            CONTRACT_CORRECTION,
            CONTRACT_EVIDENCE_REVIEW,
            CONTRACT_EXPLICIT_MEMORY,
            CONTRACT_FAILURE_DEBUG,
            CONTRACT_OPEN_LOOP,
            CONTRACT_PREFERENCE,
            CONTRACT_SELF_CHANGE,
            plan_life_memory_query,
        )

        plan = plan_life_memory_query(user_message)
    except Exception:
        return False
    if not bool(getattr(plan, "should_recall", False)):
        return False
    contract_id = str(getattr(plan, "recall_contract_id", "") or "")
    if contract_id == CONTRACT_FAILURE_DEBUG:
        message = _normalize_text(user_message)
        message_lower = message.lower()
        return any(
            marker in message or marker.lower() in message_lower
            for marker in (
                "失败原因",
                "为什么失败",
                "上次",
                "之前",
                "刚才",
                "那次",
                "那个",
                "DeepSeek",
                "Gemini",
                "Gateway",
                "网关",
                "状态库",
            )
        )
    return contract_id in {
        CONTRACT_CORRECTION,
        CONTRACT_EVIDENCE_REVIEW,
        CONTRACT_EXPLICIT_MEMORY,
        CONTRACT_OPEN_LOOP,
        CONTRACT_PREFERENCE,
        CONTRACT_SELF_CHANGE,
    }



__all__ = [name for name in globals() if not name.startswith("__")]
