"""Skill-first executive runtime core.

This module owns the canonical execution path behind:
chat_handler -> cortex_orchestrator -> executive_router -> executive_runtime.

The runtime only routes to skill families / executors. Legacy dispatch/top-level
entrypoints are intentionally not reintroduced here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, AsyncGenerator

from host_brain.app.capability_gap_detector import detect_capability_gap
from host_brain.app.relational_chat_detector import build_relational_chat_reason_code
from host_brain.app.runtime_capability_synthesizer import synthesize_runtime_capability
from host_brain.app.skill_dag_planner import SkillDagNode, SkillDagPlan, build_skill_dag_plan
from host_brain.app.skill_family_router import build_skill_route_decision
from host_brain.codex_runtime.skill_ids import CODEX_RUNTIME_RUN_MODE_BY_SKILL_ID, is_codex_runtime_skill
from host_brain.skills.executor_registry import dispatch_executor_payload


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


def _parse_bool_env(name: str, *, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(minimum), min(int(maximum), value))


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
    request_id: str,
    session_id: str,
    intent_id: str,
    emotion_context: dict[str, Any],
) -> dict[str, Any]:
    from host_brain.app.dependencies import get_episodic_store, get_semantic_recaller  # noqa: PLC0415

    try:
        episodic_store = get_episodic_store()
    except Exception as exc:  # pragma: no cover - depends on runtime services
        episodic_store = None
        _logger.debug("memory skill episodic_store unavailable: %s", exc)

    try:
        semantic_recaller = get_semantic_recaller()
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
    ) -> list[dict[str, Any]]:
        _ = tags
        if semantic_recaller is None:
            return []
        filter_metadata: dict[str, Any] = {}
        session_ref = str(session_id or "").strip()
        if session_ref:
            filter_metadata["session_id"] = session_ref
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


def _hard_rule_route(
    *,
    intent_draft: dict[str, Any],
    sanitized_message: str,
    skill_registry: Any,
    allow_plan_detection: bool = True,
) -> _RouteSelection:
    """两层路由判断。

    第一层：RULE_ROUTE_MAP 规则映射（精确匹配，无歧义词）→ 命中则直接路由
    第二层：intent_draft 提示 + 硬规则检测 → 未命中则兜底 chat
    """
    route_hint = _normalize_text(intent_draft.get("route"), lower=True)
    target_hint = _normalize_text(intent_draft.get("target_id"))
    if route_hint in {"recent_turn", "task_progress", "chat"}:
        return _RouteSelection(route_hint, target_hint, f"intent_draft:{route_hint}")

    relational_reason = build_relational_chat_reason_code(sanitized_message)
    if relational_reason:
        return _RouteSelection("chat", "", relational_reason)

    if route_hint == "plan":
        return _RouteSelection("plan", target_hint or "skill_dag", "intent_draft:plan")
    if route_hint == "skill" and target_hint:
        return _RouteSelection("skill", target_hint, "intent_draft:skill")
    if route_hint == "delegate" and target_hint:
        return _RouteSelection("delegate", target_hint, "intent_draft:delegate")

    if _looks_like_task_progress_query(sanitized_message):
        return _RouteSelection("task_progress", "", "hard_rule:task_progress")
    if _looks_like_recent_turn_query(sanitized_message):
        return _RouteSelection("recent_turn", "", "hard_rule:recent_turn")

    skill_target = _extract_skill_target(intent_draft, skill_registry)
    if skill_target:
        return _RouteSelection("skill", skill_target, "intent_draft:skill_target")

    delegate_target = _extract_delegate_target(intent_draft)
    if delegate_target:
        return _RouteSelection("delegate", delegate_target, "intent_draft:delegate_target")

    # ── 第一层：RULE_ROUTE_MAP 规则映射（精确匹配，无歧义词）──
    # 命中则直接路由，不进入 LLM 判断，也不进入 plan 路径
    try:
        from host_brain.agents.planning_agent import RULE_ROUTE_MAP  # noqa: PLC0415
        msg_lower = _normalize_text(sanitized_message, lower=True)
        for keyword, (rule_route_type, rule_target_id) in RULE_ROUTE_MAP.items():
            if keyword.lower() in msg_lower:
                # 验证 target_id 是否在 registry 中（skill 路由需要验证）
                if rule_route_type == "skill":
                    try:
                        if skill_registry.get(rule_target_id) is not None:
                            return _RouteSelection(rule_route_type, rule_target_id, f"rule_map:{keyword}")
                        else:
                            # skill 不在 registry 中（可能尚未初始化或已下线）
                            # 降级到 delegate 路由而非 chat，保留意图语义
                            _logger.debug(
                                "_hard_rule_route: skill '%s' 不在 registry，降级到 delegate 路由 (keyword=%s)",
                                rule_target_id, keyword,
                            )
                            return _RouteSelection("delegate", "intent_agent", f"rule_map:{keyword}:skill_fallback")
                    except Exception:
                        # registry 访问异常时同样降级到 delegate，不丢失意图
                        return _RouteSelection("delegate", "intent_agent", f"rule_map:{keyword}:registry_error")
                else:
                    return _RouteSelection(rule_route_type, rule_target_id, f"rule_map:{keyword}")
    except ImportError:
        pass

    if allow_plan_detection and _looks_multi_step_request(sanitized_message, intent_draft):
        return _RouteSelection("plan", "skill_dag", "hard_rule:multi_step")

    return _RouteSelection("chat", "", "default:chat")


def _should_force_complex_plan(
    *,
    route: _RouteSelection,
    user_message: str,
    intent_draft: dict[str, Any],
    llm_route_used: bool,
) -> bool:
    """第二层判断：是否强制进入 plan 路径（LangGraph 父子图）。

    改进：
    - skill/delegate 路由也需要通过 gap 检测保护，避免已能处理的请求被过度拆解
    - needs_planning() 评分指标已收紧（见 planning_agent.py），误触发率降低
    - RULE_ROUTE_MAP 命中的请求不会到达此函数（已在 _hard_rule_route 中直接路由）
    """
    if route.route_type in {"plan", "recent_turn", "task_progress"}:
        return False
    if bool(intent_draft.get("disable_auto_plan")):
        return False
    if bool(intent_draft.get("force_plan")):
        return True

    normalized_message = _normalize_text(user_message)
    min_chars = _parse_int_env(
        "HOST_EXECUTIVE_AUTO_COMPLEX_PLAN_MIN_CHARS",
        default=24,
        minimum=8,
        maximum=400,
    )
    if len(normalized_message) < min_chars:
        return False

    # 所有路由类型（包括 skill/delegate）都需要通过 gap 检测
    # 避免已有 skill/agent 能处理的请求被强制拆解为多步 DAG
    gap = detect_capability_gap(
        user_message=normalized_message,
        route_type=route.route_type,
        target_id=route.target_id,
        reason_code=route.reason_code,
        llm_route_used=llm_route_used,
    )
    if not gap.detected:
        return False

    try:
        from host_brain.app.dependencies import get_planning_agent  # noqa: PLC0415

        return bool(get_planning_agent().needs_planning(normalized_message))
    except Exception:
        return False


def _maybe_build_multi_step_plan(
    *,
    request_id: str,
    sanitized_message: str,
    intent_draft: dict[str, Any],
    skill_registry: Any,
) -> SkillDagPlan | None:
    """构建多步骤 DAG 计划。

    仅使用 intent_draft 中的显式步骤（由 LangGraph 父子图规划产生），
    不再依赖旧的 _build_planning_agent_plan_steps / _build_default_plan_steps 兜底。
    """
    explicit_steps = intent_draft.get("planned_steps")
    if not isinstance(explicit_steps, list):
        explicit_steps = intent_draft.get("steps")
    steps = [dict(item or {}) for item in explicit_steps] if isinstance(explicit_steps, list) else []
    if len(steps) <= 1:
        return None
    return build_skill_dag_plan(plan_id=f"plan::{request_id}", steps=steps)


def _format_elapsed(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _build_task_progress_sub_context(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
) -> SimpleNamespace:
    from host_brain.app.dependencies import get_runtime_task_registry  # noqa: PLC0415

    registry = get_runtime_task_registry()
    summary = registry.summarize_for_chat(
        person_id=person_id,
        session_id=session_id,
        exclude_request_id=request_id,
    )
    if not summary:
        return SimpleNamespace(
            success=False,
            result_text="当前没有可汇报的执行进度。",
            payload={},
        )

    status = _normalize_text(summary.get("status")) or "UNKNOWN"
    target_id = _normalize_text(summary.get("target_id")) or "task"
    progress = _normalize_text(summary.get("progress_message") or summary.get("detail"))
    elapsed = _format_elapsed(int(summary.get("elapsed_ms") or 0))
    result_text = f"当前任务 {target_id} 状态为 {status}，已运行 {elapsed}。"
    if progress:
        result_text += f" 当前进展：{progress}"
    result_value = _normalize_text(summary.get("result_text"))
    if result_value and status in {"COMPLETED", "FAILED"}:
        result_text += f" 结果摘要：{result_value}"
    return SimpleNamespace(
        success=True,
        result_text=result_text,
        payload=summary,
    )


def _extract_skill_args(intent_draft: dict[str, Any], request_body: Any, user_message: str) -> dict[str, Any]:
    if isinstance(intent_draft.get("args"), dict):
        return dict(intent_draft.get("args") or {})
    if isinstance(request_body, dict) and isinstance(request_body.get("args"), dict):
        return dict(request_body.get("args") or {})
    return {
        "query": user_message,
        "text": user_message,
        "user_message": user_message,
    }


def _merge_plan_node_args(
    *,
    base_intent_draft: dict[str, Any],
    node: SkillDagNode,
    task_text: str,
) -> dict[str, Any]:
    merged_args = dict(dict(base_intent_draft or {}).get("args") or {})
    node_args = dict(getattr(node, "args_payload", {}) or {})
    merged_args.update(node_args)
    normalized_target = _normalize_text(node.target_id, lower=True)
    if not _normalize_text(merged_args.get("query")):
        merged_args["query"] = task_text
    if not _normalize_text(merged_args.get("text")):
        merged_args["text"] = task_text
    if not _normalize_text(merged_args.get("user_message")):
        merged_args["user_message"] = task_text
    if normalized_target == "memory_store" and not _normalize_text(merged_args.get("content")):
        merged_args["content"] = task_text
    if is_codex_runtime_skill(normalized_target):
        if not _normalize_text(merged_args.get("task")):
            merged_args["task"] = _normalize_text(merged_args.get("problem_statement")) or task_text
        if not _normalize_text(merged_args.get("problem_statement")):
            merged_args["problem_statement"] = _normalize_text(merged_args.get("task")) or task_text
        if not _normalize_text(merged_args.get("run_mode"), lower=True):
            merged_args["run_mode"] = CODEX_RUNTIME_RUN_MODE_BY_SKILL_ID.get(normalized_target, "worker")
        if (
            _normalize_text(merged_args.get("run_mode"), lower=True) != "inspect"
            and not _normalize_text(merged_args.get("workspace_strategy"), lower=True)
            and not isinstance(merged_args.get("workspace"), dict)
        ):
            merged_args["workspace_strategy"] = "runtime_repo"
    return merged_args


def _flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [_flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("result_text", "result", "detail", "message", "summary", "output", "text"):
            text = _flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = _flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)


def _is_effective_skill_success(
    *,
    raw_status: str,
    raw_code: str,
    raw_result: Any,
    result_text: str,
) -> bool:
    status_value = _normalize_text(raw_status, lower=True)
    if status_value in _SKILL_EXEC_FAIL_STATUS:
        return False

    code_value = _normalize_text(raw_code).upper()
    if code_value in _SKILL_EXEC_FAIL_CODES:
        return False

    digest = f"{str(raw_result or '')} {str(result_text or '')}".lower()
    if any(marker in digest for marker in _SKILL_EXEC_NO_EFFECT_MARKERS):
        return False
    return True


async def _dispatch_to_sub_agent(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    target_id: str,
    task_text: str,
    user_message: str,
    context: dict[str, Any] | None = None,
) -> _ExecutionOutcome:
    from host_brain.app.sub_agent_protocol import SubAgentRequest, get_sub_agent_dispatcher  # noqa: PLC0415

    started = time.time()
    dispatcher = get_sub_agent_dispatcher()
    timeout_ms = _parse_int_env(
        "HOST_SUBAGENT_DISPATCH_TIMEOUT_MS",
        default=90_000,
        minimum=5_000,
        maximum=300_000,
    )
    response = await dispatcher.dispatch(
        SubAgentRequest(
            agent_id=target_id,
            task=task_text,
            user_message=user_message,
            request_id=request_id,
            session_id=session_id,
            context=dict(context or {}),
            timeout_ms=timeout_ms,
        )
    )
    latency_ms = int((time.time() - started) * 1000)
    return _ExecutionOutcome(
        route_type="delegate",
        target_id=target_id,
        skill_family=str(dict(context or {}).get("skill_family") or "general"),
        success=bool(response.success),
        result_text=_normalize_text(response.result),
        error_text=_normalize_text(response.error),
        latency_ms=max(latency_ms, int(response.latency_ms or 0)),
        payload={
            "audit_refs": dict(response.audit_refs or {}),
            "agent_id": response.agent_id,
        },
    )


def _log_skill_exec_row(
    *,
    request_id: str,
    intent_id: str,
    skill_name: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    approval_token_ref: str | None,
    ts_ms: int,
) -> None:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        get_ledger().log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": None,
                "turn_id": None,
                "intent_id": intent_id,
                "event_id": f"evt::{request_id}::{skill_name}",
                "skill_name": skill_name,
                "approval_token_ref": approval_token_ref,
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": result_status,
                "result_code": result_code,
                "result_digest": result_digest[:4000] if result_digest else None,
                "evidence_ref": f"skill_exec::{request_id}::{skill_name}",
                "manifest_ref": None,
                "ts_ms": ts_ms,
            }
        )
    except Exception as exc:
        _logger.debug("[%s] skill exec audit skipped skill=%s: %s", request_id, skill_name, exc)


def _execute_skill(
    *,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    skill_id: str,
    task_text: str,
    user_message: str,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
) -> _ExecutionOutcome:
    from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415

    started = time.time()
    registry = get_skill_registry()
    skill_meta = registry.require(skill_id)
    approval_token_ref = _normalize_text(dict(governor_result or {}).get("tokens", {}).get("approval_token_ref"))
    execution_intent_id = _normalize_text(intent_id) or f"intent::{request_id}::{skill_id}"

    if _normalize_text(dict(governor_result or {}).get("decision"), lower=True) not in {"", "approve"}:
        error_text = _normalize_text(governor_result.get("reason_code")) or "GOVERNOR_REJECTED"
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="REJECTED",
            result_code=error_text,
            result_digest=error_text,
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=error_text,
            latency_ms=latency_ms,
            payload={"approval_token_ref": approval_token_ref or None},
        )

    args = _extract_skill_args(intent_draft, request_body, task_text)
    if is_codex_runtime_skill(skill_id):
        args.setdefault("approval_confirmed", True)
        args.setdefault("confirmed", True)
        if approval_token_ref:
            args.setdefault("approval_token_ref", approval_token_ref)
    exec_context = {
        "request_id": request_id,
        "session_id": session_id,
        "person_id": person_id,
        "user_message": user_message,
        "task_text": task_text,
        "render_target_lang": render_target_lang,
        "mood_state": _normalize_text(dict(emotion_context or {}).get("mood_state")) or "NEUTRAL",
        "emotion_tag": _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL",
        "emotion_context": dict(emotion_context or {}),
        "skill_family": str(skill_meta.family or "general"),
    }
    if skill_id.startswith("memory_"):
        exec_context.update(
            _build_memory_exec_context(
                request_id=request_id,
                session_id=session_id,
                intent_id=execution_intent_id,
                emotion_context=emotion_context,
            )
        )

    try:
        handler_payload = skill_meta.handler(args, exec_context)
        if isinstance(handler_payload, dict):
            payload = dict(handler_payload)
            payload.setdefault("_context", exec_context)
        else:
            payload = handler_payload
        raw_result = dispatch_executor_payload(payload)
        result_text = _flatten_result_text(raw_result)
        result_mapping = _coerce_mapping(raw_result)
        raw_status = _normalize_text(result_mapping.get("status"), lower=True)
        raw_code = _normalize_text(result_mapping.get("reason_code") or result_mapping.get("result_code"))
        success = _is_effective_skill_success(
            raw_status=raw_status,
            raw_code=raw_code,
            raw_result=raw_result,
            result_text=result_text,
        )
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="SUCCESS" if success else "FAILED",
            result_code=raw_code or None,
            result_digest=(result_text or str(raw_result or "")),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=success,
            result_text=result_text,
            error_text="" if success else (raw_code or "SKILL_EXECUTION_NO_EFFECT"),
            latency_ms=latency_ms,
            payload={
                "result": raw_result,
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(skill_meta.fallback_policy or "chat_fallback"),
            },
        )
    except Exception as exc:
        latency_ms = int((time.time() - started) * 1000)
        _log_skill_exec_row(
            request_id=request_id,
            intent_id=execution_intent_id,
            skill_name=skill_id,
            result_status="FAILED",
            result_code="SKILL_EXECUTION_ERROR",
            result_digest=str(exc),
            approval_token_ref=approval_token_ref or None,
            ts_ms=int(time.time() * 1000),
        )
        return _ExecutionOutcome(
            route_type="skill",
            target_id=skill_id,
            skill_family=str(skill_meta.family or "general"),
            success=False,
            result_text="",
            error_text=str(exc),
            latency_ms=latency_ms,
            payload={
                "approval_token_ref": approval_token_ref or None,
                "fallback_policy": str(getattr(skill_meta, "fallback_policy", "chat_fallback") or "chat_fallback"),
            },
        )


def _family_default_delegate(skill_family: str) -> str:
    normalized = _normalize_text(skill_family, lower=True)
    if normalized == "code":
        return "intent_agent"
    if normalized == "translation":
        return "translate_agent"
    if normalized in {"perception", "vision"}:
        return "vision_agent"
    if normalized == "system":
        return "repair_agent"
    return "intent_agent"


def _maybe_synthesize_runtime_capability(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    route_type: str,
    target_id: str,
    reason_code: str,
    llm_route_used: bool,
) -> dict[str, Any] | None:
    gap = detect_capability_gap(
        user_message=user_message,
        route_type=route_type,
        target_id=target_id,
        reason_code=reason_code,
        llm_route_used=llm_route_used,
    )
    if not gap.detected:
        return None

    from host_brain.app.dependencies import (  # noqa: PLC0415
        get_capability_forge_service,
        get_capability_gap_telemetry,
        get_runtime_capability_registry,
    )

    forge_service = get_capability_forge_service()
    override = forge_service.get_runtime_route_override(
        request_id=request_id,
        suggested_family=str(gap.suggested_family or "reasoning"),
        missing_capability_tags=list(gap.missing_capability_tags),
        reason_code=gap.reason_code,
    )
    runtime_spec = synthesize_runtime_capability(
        request_id=request_id,
        session_id=session_id,
        user_message=user_message,
        gap_decision=gap,
        registry=get_runtime_capability_registry(),
    )

    override_target = _normalize_text(override.get("executor_target")) if isinstance(override, dict) else ""
    executor_route = runtime_spec.route_type
    executor_target = override_target or runtime_spec.target_id
    if override_target:
        executor_route = "delegate"

    get_capability_gap_telemetry().record_detected(
        request_id=request_id,
        session_id=session_id,
        runtime_capability_id=runtime_spec.runtime_capability_id,
        reason_code=gap.reason_code,
        suggested_family=gap.suggested_family,
        missing_capability_tags=gap.missing_capability_tags,
        confidence=gap.confidence,
        executor_route=executor_route,
        executor_target=executor_target,
    )

    auto_plan_result: dict[str, Any] = {}
    try:
        auto_plan_result = forge_service.maybe_auto_plan_for_gap(
            request_id=request_id,
            session_id=session_id,
            reason_code=gap.reason_code,
            suggested_family=str(gap.suggested_family or "reasoning"),
            missing_capability_tags=list(gap.missing_capability_tags),
            proposer="executive_runtime",
        )
    except Exception as exc:
        _logger.debug("[%s] capability forge auto-plan skipped: %s", request_id, exc)
        auto_plan_result = {
            "triggered": False,
            "reason": "AUTO_PLAN_EXCEPTION",
        }

    capability_forge_payload = dict(override or {})
    if auto_plan_result:
        capability_forge_payload["auto_plan"] = dict(auto_plan_result)

    return {
        "capability_gap": gap.as_dict(),
        "runtime_capability": runtime_spec.as_dict(),
        "capability_forge": capability_forge_payload,
        "executor_route": executor_route,
        "executor_target": executor_target,
        "task_text": runtime_spec.task,
    }


def _build_failure_recovery_plan(
    *,
    route_type: str,
    target_id: str,
    skill_family: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    details = dict(payload or {})
    if route_type == "skill":
        fallback_policy = _normalize_text(details.get("fallback_policy"), lower=True) or "chat_fallback"
        if fallback_policy in {"same_family", "retry_once"}:
            return {
                "route_type": "delegate",
                "target_id": _family_default_delegate(skill_family),
                "reason_code": f"skill_recovery:{fallback_policy}",
            }
        return {
            "route_type": "chat",
            "target_id": "",
            "reason_code": "skill_recovery:chat_fallback",
        }
    if route_type == "delegate" and target_id != "intent_agent":
        return {
            "route_type": "delegate",
            "target_id": "intent_agent",
            "reason_code": "delegate_recovery:intent_agent",
        }
    return None


def _build_chat_input(
    *,
    request_body,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: dict[str, Any],
    trace_stage_meta: dict[str, Any],
    sub_agent_context,
):
    from host_brain.app.chat_agent import ChatAgentInput  # noqa: PLC0415
    from host_brain.app.graph_config_builder import resolve_chat_agent_llm_runtime  # noqa: PLC0415

    resolved_llm_runtime = dict(llm_runtime or {}) if isinstance(llm_runtime, dict) else llm_runtime
    if isinstance(resolved_llm_runtime, dict) and resolved_llm_runtime:
        agent_role = _normalize_text(resolved_llm_runtime.get("agent_role"), lower=True)
        if agent_role != "chat_agent":
            resolved_llm_runtime = resolve_chat_agent_llm_runtime(
                base_runtime=resolved_llm_runtime,
            )

    return ChatAgentInput(
        request_id=request_id,
        session_id=session_id,
        user_message=sanitized_message,
        person_id=person_id,
        emotion_context=dict(emotion_context or {}),
        emotion_snapshot=dict(emotion_snapshot or {}),
        bond_summary=dict(bond_summary or {}),
        retrieval_evidence_items=list(retrieval_evidence_items or []),
        retrieval_summary=dict(retrieval_summary or {}),
        sub_agent_context=sub_agent_context,
        render_target_lang=render_target_lang,
        reply_channel=getattr(request_body, "reply_channel", None),
        suppress_edge_tts=getattr(request_body, "suppress_edge_tts", None),
        suppress_edge_vts=getattr(request_body, "suppress_edge_vts", None),
        ts_ms=ts_ms,
        intent_draft=dict(intent_draft or {}),
        trace_stage_meta=dict(trace_stage_meta or {}),
        llm_runtime=resolved_llm_runtime,
        attachments=list(getattr(request_body, "attachments", []) or []),
        multimodal_observations=dict(getattr(request_body, "multimodal_observations", {}) or {}),
        attachment_summary=str(getattr(request_body, "attachment_summary", "") or "").strip(),
    )


def _build_sub_agent_context(
    *,
    agent_id: str,
    task_text: str,
    outcome: _ExecutionOutcome,
):
    from host_brain.app.chat_agent import SubAgentContext  # noqa: PLC0415

    return SubAgentContext(
        agent_id=agent_id,
        task=task_text,
        result_text=outcome.result_text,
        success=outcome.success,
        latency_ms=outcome.latency_ms,
        error=outcome.error_text,
        payload=dict(outcome.payload or {}),
    )


def _build_plan_chat_outcome(
    *,
    plan: SkillDagPlan,
    outcome: _ExecutionOutcome,
    plan_meta: dict[str, Any],
) -> _ExecutionOutcome:
    payload = dict(outcome.payload or {})
    plan_trace = list(plan_meta.get("plan_trace") or payload.get("plan_trace") or [])
    trace_by_node_id = {
        str(item.get("node_id") or "").strip(): dict(item or {})
        for item in plan_trace
        if isinstance(item, dict) and str(item.get("node_id") or "").strip()
    }
    steps: list[dict[str, Any]] = []
    success_count = 0
    for node in list(plan.nodes or []):
        trace = trace_by_node_id.get(str(node.node_id or "").strip(), {})
        step_success = bool(trace.get("success"))
        if step_success:
            success_count += 1
        steps.append(
            {
                "step_index": int(node.step_index or 0),
                "target_id": str(node.target_id or "").strip(),
                "description": str(node.description or "").strip(),
                "task_description": str(node.task_description or "").strip(),
                "success": step_success,
                "result_text": str(trace.get("result_text") or "").strip(),
                "error": str(trace.get("error_text") or "").strip(),
            }
        )
    payload.update(
        {
            "plan": plan.as_dict(),
            "plan_trace": plan_trace,
            "steps": steps,
            "total_steps": len(steps),
            "success_count": success_count,
            "parallel_enabled": bool(plan_meta.get("parallel_enabled")),
        }
    )
    return _ExecutionOutcome(
        route_type=outcome.route_type,
        target_id=outcome.target_id,
        skill_family=outcome.skill_family,
        success=outcome.success,
        result_text=outcome.result_text,
        error_text=outcome.error_text,
        latency_ms=outcome.latency_ms,
        payload=payload,
    )


async def _execute_plan_node(
    *,
    node: SkillDagNode,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
    node_results: dict[str, _ExecutionOutcome],
) -> _ExecutionOutcome:
    dependency_context_parts: list[str] = []
    for dep_id in list(node.dependencies or ()):
        previous = node_results.get(dep_id)
        if previous and previous.result_text:
            dependency_context_parts.append(f"[{dep_id}] {previous.result_text}")
    task_text = _normalize_text(node.task_description or node.description)
    if dependency_context_parts:
        task_text = f"{task_text}\n\nDependency context:\n" + "\n".join(dependency_context_parts)

    if node.route == "skill":
        plan_intent_draft = dict(intent_draft or {})
        plan_intent_draft["args"] = _merge_plan_node_args(
            base_intent_draft=plan_intent_draft,
            node=node,
            task_text=task_text,
        )
        return await asyncio.to_thread(
            _execute_skill,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            skill_id=node.target_id,
            task_text=task_text,
            user_message=task_text,
            governor_result=governor_result,
            intent_draft=plan_intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )
    return await _dispatch_to_sub_agent(
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        target_id=node.target_id or "intent_agent",
        task_text=task_text,
        user_message=task_text,
        context={
            "skill_family": node.skill_family or "executive",
            "plan_node_id": node.node_id,
            "route_reason": node.route_reason,
        },
    )


async def _execute_skill_dag(
    *,
    plan: SkillDagPlan,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
) -> tuple[_ExecutionOutcome, dict[str, Any]]:
    explicit_steps = dict(intent_draft or {}).get("planned_steps")
    if not isinstance(explicit_steps, list):
        explicit_steps = dict(intent_draft or {}).get("steps")
    if isinstance(explicit_steps, list) and len(explicit_steps) > 1:
        return await _execute_explicit_skill_dag(
            plan=plan,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )

    """执行 Skill DAG。

    改进：
    - 优先使用 LangGraph 父子图（run_plan_graph）执行，充分利用 Send API 并行
    - 关键节点失败时中断后续批次，不继续执行依赖它的节点
    - synthesize 结果由 plan_graph.synthesize_node 语义整合，不再是简单字符串拼接
    """
    # ── 优先走 LangGraph 父子图 ──
    try:
        from host_brain.app.plan_graph import run_plan_graph  # noqa: PLC0415
        pg_result = await run_plan_graph(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            user_message=str((intent_draft or {}).get("user_message") or "").strip()
                         or str(request_body).strip()[:500] if request_body else "",
            intent_draft=dict(intent_draft or {}),
            emotion_context=dict(emotion_context or {}),
        )
        plan_trace = list(pg_result.get("plan_trace") or [])
        success = bool(pg_result.get("success"))
        final_answer = str(pg_result.get("final_answer") or "").strip()
        latency_ms = int(pg_result.get("latency_ms") or 0)

        _logger.info(
            "[%s] _execute_skill_dag: plan_graph 执行完成 success=%s latency=%dms",
            request_id, success, latency_ms,
        )

        return (
            _ExecutionOutcome(
                route_type="plan",
                target_id=plan.plan_id,
                skill_family="executive",
                success=success,
                result_text=final_answer,
                error_text="" if success else "PLAN_GRAPH_FAILED",
                latency_ms=latency_ms,
                payload={
                    "plan": plan.as_dict(),
                    "plan_trace": plan_trace,
                    "sub_tasks": list(pg_result.get("sub_tasks") or []),
                    "task_results": list(pg_result.get("task_results") or []),
                },
            ),
            {
                "plan_id": plan.plan_id,
                "plan_trace": plan_trace,
                "parallel_enabled": True,  # plan_graph 始终并行
                "engine": "langgraph_plan_graph",
            },
        )
    except Exception as pg_exc:
        _logger.warning(
            "[%s] plan_graph 执行失败，返回失败结果（不降级到 SkillDag，避免执行不同数据的计划）: %s",
            request_id, pg_exc,
        )
        # P1-1 修复：plan_graph 失败时直接返回失败结果。
        # 原降级路径使用 _maybe_build_multi_step_plan() 构建的 SkillDag（来自 intent_draft.planned_steps），
        # 而 plan_graph 使用 PlanningAgent.create_plan() 实时拆解的子任务，两者是不同的计划数据。
        # 降级实际上是在执行一个不同的计划，而非真正的"降级重试"，会产生不可预期的结果。
        return (
            _ExecutionOutcome(
                route_type="plan",
                target_id=plan.plan_id,
                skill_family="executive",
                success=False,
                result_text="",
                error_text=f"PLAN_GRAPH_EXCEPTION: {pg_exc!s}"[:200],
                latency_ms=0,
                payload={
                    "plan": plan.as_dict(),
                    "plan_trace": [{"node": "_execute_skill_dag", "status": "FAILED", "error": str(pg_exc)[:200]}],
                },
            ),
            {
                "plan_id": plan.plan_id,
                "plan_trace": [],
                "parallel_enabled": False,
                "engine": "plan_graph_failed",
            },
        )

    # ── 以下代码保留为历史参考，正常路径不会执行到此处 ──
    # （plan_graph 成功时已在 try 块中 return，失败时已在 except 块中 return）
    node_results: dict[str, _ExecutionOutcome] = {}
    plan_trace: list[dict[str, Any]] = []
    critical_failure = False  # 关键节点失败标志

    for batch in list(plan.execution_batches or []):
        # 关键节点失败后中断，不继续执行后续批次
        if critical_failure:
            _logger.warning(
                "[%s] _execute_skill_dag: 关键节点失败，中断后续批次执行",
                request_id,
            )
            break

        batch_nodes = [node for node in plan.nodes if node.node_id in batch]
        coros = [
            _execute_plan_node(
                node=node,
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                intent_id=intent_id,
                governor_result=governor_result,
                intent_draft=intent_draft,
                emotion_context=emotion_context,
                render_target_lang=render_target_lang,
                node_results=node_results,
            )
            for node in batch_nodes
        ]
        batch_results = await asyncio.gather(*coros)
        for node, outcome in zip(batch_nodes, batch_results):
            node_results[node.node_id] = outcome
            plan_trace.append(
                {
                    "node_id": node.node_id,
                    "route": node.route,
                    "target_id": node.target_id,
                    "success": outcome.success,
                    "latency_ms": outcome.latency_ms,
                    "result_text": outcome.result_text,
                    "error_text": outcome.error_text,
                }
            )
            # 检查是否为关键节点失败（有后续节点依赖它）
            if not outcome.success:
                has_dependents = any(
                    node.node_id in (n.dependencies or ())
                    for n in plan.nodes
                    if n.node_id != node.node_id
                )
                if has_dependents:
                    critical_failure = True
                    _logger.warning(
                        "[%s] 关键节点 %s 失败（有 %d 个后续节点依赖），标记中断",
                        request_id, node.node_id,
                        sum(1 for n in plan.nodes if node.node_id in (n.dependencies or ())),
                    )

    ordered_results = [node_results[node.node_id] for node in plan.nodes if node.node_id in node_results]
    success = bool(ordered_results) and all(item.success for item in ordered_results) and not critical_failure
    summary_lines: list[str] = []
    for node in plan.nodes:
        outcome = node_results.get(node.node_id)
        if outcome is None:
            continue
        body = outcome.result_text or outcome.error_text or "no output"
        summary_lines.append(f"[{node.step_index}] {node.description}: {body}")

    return (
        _ExecutionOutcome(
            route_type="plan",
            target_id=plan.plan_id,
            skill_family="executive",
            success=success,
            result_text="\n".join(summary_lines).strip(),
            error_text="" if success else "PLAN_NODE_FAILED",
            latency_ms=sum(int(item.latency_ms or 0) for item in ordered_results),
            payload={
                "plan": plan.as_dict(),
                "plan_trace": plan_trace,
            },
        ),
        {
            "plan_id": plan.plan_id,
            "plan_trace": plan_trace,
            "parallel_enabled": any(len(batch) > 1 for batch in list(plan.execution_batches or [])),
            "engine": "skill_dag_fallback",
        },
    )


async def _execute_explicit_skill_dag(
    *,
    plan: SkillDagPlan,
    request_body: Any,
    request_id: str,
    session_id: str,
    person_id: str,
    intent_id: str | None,
    governor_result: dict[str, Any],
    intent_draft: dict[str, Any],
    emotion_context: dict[str, Any],
    render_target_lang: str,
) -> tuple[_ExecutionOutcome, dict[str, Any]]:
    node_results: dict[str, _ExecutionOutcome] = {}
    plan_trace: list[dict[str, Any]] = []
    critical_failure = False

    for batch in list(plan.execution_batches or []):
        if critical_failure:
            _logger.warning(
                "[%s] _execute_explicit_skill_dag: critical node failed, stop remaining batches",
                request_id,
            )
            break

        batch_nodes = [node for node in plan.nodes if node.node_id in batch]
        coros = [
            _execute_plan_node(
                node=node,
                request_body=request_body,
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                intent_id=intent_id,
                governor_result=governor_result,
                intent_draft=intent_draft,
                emotion_context=emotion_context,
                render_target_lang=render_target_lang,
                node_results=node_results,
            )
            for node in batch_nodes
        ]
        batch_results = await asyncio.gather(*coros)
        for node, outcome in zip(batch_nodes, batch_results):
            node_results[node.node_id] = outcome
            plan_trace.append(
                {
                    "node_id": node.node_id,
                    "route": node.route,
                    "target_id": node.target_id,
                    "success": outcome.success,
                    "latency_ms": outcome.latency_ms,
                    "result_text": outcome.result_text,
                    "error_text": outcome.error_text,
                }
            )
            if not outcome.success:
                has_dependents = any(
                    node.node_id in (n.dependencies or ())
                    for n in plan.nodes
                    if n.node_id != node.node_id
                )
                if has_dependents:
                    critical_failure = True
                    _logger.warning(
                        "[%s] critical explicit-plan node failed: %s",
                        request_id,
                        node.node_id,
                    )

    ordered_results = [node_results[node.node_id] for node in plan.nodes if node.node_id in node_results]
    success = bool(ordered_results) and all(item.success for item in ordered_results) and not critical_failure
    summary_lines: list[str] = []
    for node in plan.nodes:
        outcome = node_results.get(node.node_id)
        if outcome is None:
            continue
        body = outcome.result_text or outcome.error_text or "no output"
        summary_lines.append(f"[{node.step_index}] {node.target_id or node.description}: {body}")

    return (
        _ExecutionOutcome(
            route_type="plan",
            target_id=plan.plan_id,
            skill_family="executive",
            success=success,
            result_text="\n".join(summary_lines).strip(),
            error_text="" if success else "PLAN_NODE_FAILED",
            latency_ms=sum(int(item.latency_ms or 0) for item in ordered_results),
            payload={
                "plan": plan.as_dict(),
                "plan_trace": plan_trace,
            },
        ),
        {
            "plan_id": plan.plan_id,
            "plan_trace": plan_trace,
            "parallel_enabled": any(len(batch) > 1 for batch in list(plan.execution_batches or [])),
            "engine": "skill_dag_explicit_plan",
        },
    )


async def _stream_chat_with_context(
    *,
    request_body: Any,
    route_reason: str,
    request_id: str,
    session_id: str,
    person_id: str,
    sanitized_message: str,
    emotion_context: dict[str, Any],
    emotion_snapshot: dict[str, Any],
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    ts_ms: int,
    intent_draft: dict[str, Any],
    trace_stage_meta: dict[str, Any],
    sub_agent_context,
    runtime_capability_payload: dict[str, Any] | None,
) -> AsyncGenerator[str, None]:
    from host_brain.app.chat_agent import chat_agent_sse_generator  # noqa: PLC0415
    from host_brain.app.executive_runtime import (  # noqa: PLC0415
        build_runtime_capability_handoff_extra,
        record_executive_handoff,
        stream_chat_route,
    )

    chat_input = _build_chat_input(
        request_body=request_body,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=sub_agent_context,
    )
    async for event in stream_chat_route(
        route="chat",
        request_id=request_id,
        route_reason=route_reason,
        skill_family=str(trace_stage_meta.get("skill_family") or "social"),
        runtime_capability_handoff_extra=build_runtime_capability_handoff_extra(runtime_capability_payload),
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
        chat_agent_sse_generator_fn=chat_agent_sse_generator,
        chat_input=chat_input,
    ):
        yield event


async def _executive_runtime_core_impl(
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
    person_id: str,
    sanitized_message: str,
    bond_summary: dict[str, Any],
    retrieval_evidence_items: list[dict[str, Any]],
    retrieval_summary: dict[str, Any],
    render_target_lang: str,
    llm_runtime: dict[str, Any] | None,
    pii_ingress_stats: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    from host_brain.app.chat_agent import _sse  # noqa: PLC0415
    from host_brain.app.dependencies import get_runtime_task_registry, get_skill_registry  # noqa: PLC0415
    from host_brain.app.executive_runtime import (  # noqa: PLC0415
        record_executive_handoff,
        record_runtime_capability_execution,
        try_handle_recent_turn_direct_route,
        try_handle_task_progress_direct_route,
    )

    del metrics

    skill_registry = get_skill_registry()
    llm_route_used = bool(
        dict(intent_draft or {}).get("llm_route_used")
        or dict(intent_draft or {}).get("route_source") == "llm"
    )

    # ── Phase B：器官族状态并行激活注入路由决策 ──────────────────────────
    # B-1: LimbicAgent motive_state → 情绪驱动路由调整
    # B-2: ImmuneAgent 风险等级 → 高风险时强制 chat 快速回复
    # B-3: ProprioceptionAgent 认知负荷 → 高负荷时强制 chat 快速回复
    # B-4: PrefrontalAgent 漂移状态 → 漂移中强制 chat 路由
    # 四路并行读取，减少串行等待（真实神经系统是并行激活的）
    _organ_route_override: str | None = None
    _organ_route_reason: str | None = None
    _organ_route_meta: dict[str, Any] = {}

    def _fetch_immune_state() -> dict[str, Any]:
        try:
            from host_brain.tribe.immune_agent import get_immune_agent  # noqa: PLC0415
            return get_immune_agent().export_runtime_state()
        except Exception:
            return {}

    def _fetch_proprioception_state() -> dict[str, Any]:
        try:
            from host_brain.tribe.proprioception_agent import get_proprioception_agent  # noqa: PLC0415
            return get_proprioception_agent().export_runtime_state()
        except Exception:
            return {}

    def _fetch_limbic_motive() -> dict[str, Any]:
        try:
            from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
            _limbic = get_limbic_agent()
            if hasattr(_limbic, "compute_motive_state"):
                return dict(_limbic.compute_motive_state() or {})
        except Exception:
            pass
        return {}

    def _fetch_prefrontal_state() -> dict[str, Any]:
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
            return get_prefrontal_agent().export_runtime_state()
        except Exception:
            return {}

    import asyncio as _asyncio  # noqa: PLC0415
    import concurrent.futures as _futures  # noqa: PLC0415

    _loop = _asyncio.get_event_loop()
    with _futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="organ_fetch") as _pool:
        _immune_fut = _loop.run_in_executor(_pool, _fetch_immune_state)
        _prop_fut = _loop.run_in_executor(_pool, _fetch_proprioception_state)
        _limbic_fut = _loop.run_in_executor(_pool, _fetch_limbic_motive)
        _pf_fut = _loop.run_in_executor(_pool, _fetch_prefrontal_state)
        _immune_state, _prop_state, _motive_state, _pf_state = await _asyncio.gather(
            _immune_fut, _prop_fut, _limbic_fut, _pf_fut,
            return_exceptions=True,
        )

    # 异常降级：gather 返回 Exception 时用空 dict 代替
    if isinstance(_immune_state, Exception):
        _immune_state = {}
    if isinstance(_prop_state, Exception):
        _prop_state = {}
    if isinstance(_motive_state, Exception):
        _motive_state = {}
    if isinstance(_pf_state, Exception):
        _pf_state = {}

    # B-2: ImmuneAgent 风险门控（最高优先级，先判断）
    _immune_risk = str(_immune_state.get("last_risk_level") or "none").strip().lower()
    _immune_decision = str(_immune_state.get("last_decision") or "ALLOW").strip().upper()
    _organ_route_meta["immune_risk_level"] = _immune_risk
    _organ_route_meta["immune_last_decision"] = _immune_decision
    if _immune_risk in {"critical", "high"} and _immune_decision in {"REPORT", "BLOCK"}:
        _organ_route_override = "chat"
        _organ_route_reason = f"immune_gate:risk={_immune_risk}:decision={_immune_decision}"

    # B-3: ProprioceptionAgent 认知负荷
    _cognitive_load = float(_prop_state.get("cognitive_load_index") or 0.0)
    _interrupt_freq = float(_prop_state.get("interrupt_frequency") or 0.0)
    _organ_route_meta["cognitive_load_index"] = round(_cognitive_load, 3)
    _organ_route_meta["interrupt_frequency"] = round(_interrupt_freq, 3)
    if _organ_route_override is None and _cognitive_load > 0.85:
        _organ_route_override = "chat"
        _organ_route_reason = f"proprioception:cognitive_load={_cognitive_load:.2f}"

    # B-1: LimbicAgent motive_state
    _organ_route_meta["motive_withdraw"] = round(float(_motive_state.get("withdraw", 0.0)), 3)
    _organ_route_meta["motive_care"] = round(float(_motive_state.get("care", 0.0)), 3)
    _organ_route_meta["motive_continuity"] = round(float(_motive_state.get("continuity", 0.0)), 3)
    if _organ_route_override is None:
        _withdraw = float(_motive_state.get("withdraw", 0.0))
        if _withdraw > 0.80:
            _organ_route_override = "chat"
            _organ_route_reason = f"limbic:withdraw={_withdraw:.2f}"

    # B-4: PrefrontalAgent 人格漂移状态
    _drift_alert = str(_pf_state.get("alert_level") or "NONE").strip().upper()
    _drift_score = float(_pf_state.get("drift_score") or 0.0)
    _organ_route_meta["prefrontal_drift_alert"] = _drift_alert
    _organ_route_meta["prefrontal_drift_score"] = round(_drift_score, 4)
    if _organ_route_override is None and _drift_alert in {"HIGH", "CRITICAL"}:
        _organ_route_override = "chat"
        _organ_route_reason = f"prefrontal:drift_alert={_drift_alert}:score={_drift_score:.3f}"
        _logger.info(
            "[%s] prefrontal drift override: alert=%s score=%.3f → force chat",
            request_id, _drift_alert, _drift_score,
        )

    # ── Phase D：PrefrontalAgent 意图规划（小模型，<500ms）────────────────
    # 基于器官族完整状态产出"我要做什么"的决策，结果注入 intent_draft 供 chat_agent 使用
    # 仅在 PREFRONTAL_PLAN_ENABLED=true 时启用（默认关，逐步灰度）
    _prefrontal_plan: dict[str, Any] = {}
    # OPT-4 第三批：env 读取统一走 CoreSettings
    from host_brain.app.settings import get_core_settings as _get_core_settings  # noqa: PLC0415
    _prefrontal_plan_enabled = bool(_get_core_settings().prefrontal_plan_enabled)
    if _prefrontal_plan_enabled:
        try:
            from host_brain.tribe.prefrontal_agent import get_prefrontal_agent  # noqa: PLC0415
            _pf_agent = get_prefrontal_agent()
            # 提取 emotion 信息
            _emotion_tag = str((emotion_context or {}).get("emotion_tag") or "NEUTRAL")
            _valence = float((emotion_context or {}).get("valence") or 0.0)
            _arousal = float((emotion_context or {}).get("arousal") or 0.0)
            # 提取 bond 信息
            _bond_stage = str((bond_summary or {}).get("stage") or "STRANGER")
            _bond_intimacy = float((bond_summary or {}).get("intimacy_score") or 0.0)
            # 提取 recall 摘要
            _recall_summary = str((retrieval_summary or {}).get("summary") or "")
            _prefrontal_plan = _pf_agent.plan_response(
                user_message=sanitized_message,
                motive_state=_motive_state,
                recall_summary=_recall_summary,
                bond_stage=_bond_stage,
                bond_intimacy=_bond_intimacy,
                immune_risk_level=_immune_risk,
                drift_alert_level=_drift_alert,
                emotion_tag=_emotion_tag,
                valence=_valence,
                arousal=_arousal,
                session_id=session_id,
                request_id=request_id,
                timeout_ms=500,
            )
            _organ_route_meta["prefrontal_plan_action"] = _prefrontal_plan.get("action")
            _organ_route_meta["prefrontal_plan_source"] = _prefrontal_plan.get("source")
            _organ_route_meta["prefrontal_plan_latency_ms"] = _prefrontal_plan.get("latency_ms")
            # plan_response 的 action 覆盖路由（仅当 organ_route_override 未设置时）
            _plan_action = str(_prefrontal_plan.get("action") or "").strip().lower()
            if _organ_route_override is None and _plan_action in {"skill", "delegate"}:
                _plan_target = str(_prefrontal_plan.get("target") or "").strip()
                if _plan_target:
                    _organ_route_override = _plan_action
                    _organ_route_reason = f"prefrontal_plan:{_plan_action}:{_plan_target}"
                    _logger.info(
                        "[%s] prefrontal_plan override: action=%s target=%s",
                        request_id, _plan_action, _plan_target,
                    )
            # 将 prefrontal_task 注入 intent_draft，供 chat_agent 作为语言化目标
            if isinstance(intent_draft, dict):
                intent_draft = dict(intent_draft)
            else:
                intent_draft = {}
            if _prefrontal_plan.get("task"):
                intent_draft["prefrontal_task"] = _prefrontal_plan.get("task")
            if _prefrontal_plan.get("tone_hint"):
                intent_draft["prefrontal_tone_hint"] = _prefrontal_plan.get("tone_hint")
        except Exception as _pf_plan_exc:
            _logger.debug("[%s] prefrontal plan_response 跳过: %s", request_id, _pf_plan_exc)

    route = _hard_rule_route(
        intent_draft=dict(intent_draft or {}),
        sanitized_message=sanitized_message,
        skill_registry=skill_registry,
    )

    # 应用器官族路由覆盖（仅覆盖 skill/delegate/plan，不覆盖 recent_turn/task_progress）
    if _organ_route_override is not None and route.route_type not in {"recent_turn", "task_progress"}:
        _logger.info(
            "[%s] organ_route_override: %s → %s (reason=%s)",
            request_id, route.route_type, _organ_route_override, _organ_route_reason,
        )
        route = _RouteSelection(_organ_route_override, "", _organ_route_reason or "organ_override")

    # B-4: 器官族路由影响写入 trace_stage_meta（可观测性）
    if _organ_route_meta:
        _organ_route_meta["organ_route_override"] = _organ_route_override
        _organ_route_meta["organ_route_reason"] = _organ_route_reason
        if isinstance(intent_draft, dict):
            intent_draft = dict(intent_draft)
            intent_draft.setdefault("_organ_route_meta", _organ_route_meta)

    if _should_force_complex_plan(
        route=route,
        user_message=sanitized_message,
        intent_draft=dict(intent_draft or {}),
        llm_route_used=llm_route_used,
    ):
        route = _RouteSelection("plan", "skill_dag", "auto_plan:complex_task")

    _bio_ops_runtime_trace: dict[str, Any] = {}
    try:
        from host_brain.bio_ops.runtime_trace import build_bio_ops_runtime_trace  # noqa: PLC0415

        _bio_ops_runtime_trace = build_bio_ops_runtime_trace(
            message=sanitized_message,
            route_type=route.route_type,
            target_id=route.target_id,
            intent_draft=dict(intent_draft or {}),
            emotion_context=dict(emotion_context or {}),
            runtime_signals={
                "current_hour": int(time.localtime().tm_hour),
                "codex_queue_size": 0,
                "tts_status": "ok",
                "user_stress": str((emotion_context or {}).get("stress_level") or ""),
                "explicit_help_request": route.route_type in {"skill", "delegate", "plan"},
            },
        )
        if isinstance(intent_draft, dict):
            intent_draft = dict(intent_draft)
            intent_draft["_bio_ops_runtime_trace"] = _bio_ops_runtime_trace
    except Exception as _bio_ops_exc:
        _bio_ops_runtime_trace = {
            "source": "bio_ops.runtime_trace",
            "proposal_only": True,
            "status": "DEGRADED",
            "reason_code": "BIO_OPS_RUNTIME_TRACE_FAILED",
            "detail": str(_bio_ops_exc)[:200],
        }
    direct_recent = await try_handle_recent_turn_direct_route(
        route=route.route_type,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        render_target_lang=render_target_lang,
        route_reason=route.reason_code,
        ts_ms=ts_ms,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        governor_result=governor_result,
        pii_ingress_stats=pii_ingress_stats,
        sse_fn=_sse,
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
    )
    if direct_recent is not None:
        for event in direct_recent:
            yield event
        return

    direct_progress = await try_handle_task_progress_direct_route(
        route=route.route_type,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        route_reason=route.reason_code,
        ts_ms=ts_ms,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        governor_result=governor_result,
        pii_ingress_stats=pii_ingress_stats,
        render_target_lang=render_target_lang,
        fallback_no_summary_text="当前没有可汇报的执行进度。",
        build_task_progress_sub_context_fn=_build_task_progress_sub_context,
        sse_fn=_sse,
        record_dispatch_handoff_fn=lambda **kwargs: record_executive_handoff(
            request_id=request_id,
            session_id=session_id,
            **kwargs,
        ),
    )
    if direct_progress is not None:
        for event in direct_progress:
            yield event
        return

    runtime_task_registry = get_runtime_task_registry()
    runtime_task = runtime_task_registry.create_task(
        task_kind=route.route_type,
        target_id=route.target_id,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        user_message=sanitized_message,
        source="executive_runtime",
        detail=route.reason_code,
        progress_message=f"route={route.route_type}",
        meta={
            "intent_id": intent_id,
            "render_target_lang": render_target_lang,
        },
    )

    runtime_capability_payload = None
    relational_reason = build_relational_chat_reason_code(sanitized_message)
    if route.route_type == "chat":
        # Phase D: PREFRONTAL_PLAN_ENABLED 时，前额叶已规划，跳过 runtime_capability 合成
        _skip_capability_synthesis = (
            _prefrontal_plan_enabled
            and bool(_prefrontal_plan.get("action"))
        )
        if not _skip_capability_synthesis:
            runtime_capability_payload = _maybe_synthesize_runtime_capability(
                request_id=request_id,
                session_id=session_id,
                user_message=sanitized_message,
                route_type=route.route_type,
                target_id=route.target_id,
                reason_code=route.reason_code,
                llm_route_used=llm_route_used,
            )
        else:
            _logger.debug(
                "[%s] Phase D: prefrontal_plan 已存在，跳过 runtime_capability 合成 action=%s",
                request_id, _prefrontal_plan.get("action"),
            )
        if runtime_capability_payload is not None and not relational_reason:
            route = _RouteSelection(
                route_type=_normalize_text(runtime_capability_payload.get("executor_route"), lower=True) or "delegate",
                target_id=_normalize_text(runtime_capability_payload.get("executor_target")) or "intent_agent",
                reason_code="capability_gap:runtime_synthesized",
            )
            runtime_task_registry.update_task(
                runtime_task.task_id,
                task_kind=route.route_type,
                target_id=route.target_id,
                detail=route.reason_code,
                progress_message="runtime capability synthesized",
                meta={
                    **dict(runtime_task.meta or {}),
                    "runtime_capability_payload": runtime_capability_payload,
                },
            )
        elif relational_reason:
            route = _RouteSelection("chat", "", relational_reason)

    plan = None
    if route.route_type == "plan":
        plan = _maybe_build_multi_step_plan(
            request_id=request_id,
            sanitized_message=sanitized_message,
            intent_draft=dict(intent_draft or {}),
            skill_registry=skill_registry,
        )
        if plan is None:
            route = _RouteSelection("delegate", "intent_agent", "plan_fallback:intent_agent")

    yield _sse(
        {
            "phase": "EXECUTIVE_ROUTE",
            "done": False,
            "request_id": request_id,
            "route_type": route.route_type,
            "target_id": route.target_id or None,
            "reason_code": route.reason_code,
            "runtime_task_id": runtime_task.task_id,
            "runtime_capability_id": dict(runtime_capability_payload or {}).get("runtime_capability", {}).get("runtime_capability_id"),
            "bio_ops_runtime_trace": _bio_ops_runtime_trace,
        }
    )

    if route.route_type == "chat":
        async for event in _stream_chat_with_context(
            request_body=request_body,
            route_reason=route.reason_code,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            sanitized_message=sanitized_message,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            retrieval_summary=retrieval_summary,
            render_target_lang=render_target_lang,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            trace_stage_meta={
                "cortex_orchestrator_used": True,
                "executive_router_used": True,
                "skill_route_type": "chat",
                "skill_family": "social",
                "skill_route_reason": route.reason_code,
                "bio_ops_runtime_trace": _bio_ops_runtime_trace,
            },
            sub_agent_context=None,
            runtime_capability_payload=runtime_capability_payload,
        ):
            yield event
        runtime_task_registry.mark_completed(
            runtime_task.task_id,
            detail="chat route completed",
            result_text="chat stream completed",
            result_code="CHAT_COMPLETED",
        )
        return

    if plan is not None:
        runtime_task_registry.update_task(
            runtime_task.task_id,
            progress_message=f"executing plan {plan.plan_id}",
            meta={
                **dict(runtime_task.meta or {}),
                "plan": plan.as_dict(),
            },
        )
        plan_outcome, plan_meta = await _execute_skill_dag(
            plan=plan,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )
        plan_chat_outcome = _build_plan_chat_outcome(
            plan=plan,
            outcome=plan_outcome,
            plan_meta=plan_meta,
        )
        trace_stage_meta = {
            "cortex_orchestrator_used": True,
            "executive_router_used": True,
            "skill_route_type": "plan",
            "skill_family": "executive",
            "skill_route_reason": route.reason_code,
            "plan_id": plan.plan_id,
            "parallel_enabled": bool(plan_meta.get("parallel_enabled")),
            # Phase B-4: 器官族路由影响可观测性字段
            "organ_route_override": _organ_route_override,
            "organ_route_reason": _organ_route_reason,
            "organ_route_meta": _organ_route_meta if _organ_route_meta else None,
            "bio_ops_runtime_trace": _bio_ops_runtime_trace,
        }
        if plan_outcome.success:
            runtime_task_registry.mark_completed(
                runtime_task.task_id,
                detail="plan completed",
                result_text=plan_outcome.result_text,
                result_code="PLAN_COMPLETED",
            )
        else:
            runtime_task_registry.mark_failed(
                runtime_task.task_id,
                detail="plan failed",
                error=plan_outcome.error_text,
                result_code="PLAN_FAILED",
                result_text=plan_outcome.result_text,
            )
        async for event in _stream_chat_with_context(
            request_body=request_body,
            route_reason=route.reason_code,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            sanitized_message=sanitized_message,
            emotion_context=emotion_context,
            emotion_snapshot=emotion_snapshot,
            bond_summary=bond_summary,
            retrieval_evidence_items=retrieval_evidence_items,
            retrieval_summary=retrieval_summary,
            render_target_lang=render_target_lang,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            intent_draft=intent_draft,
            trace_stage_meta=trace_stage_meta,
            sub_agent_context=_build_sub_agent_context(
                agent_id="plan_execution",
                task_text=sanitized_message,
                outcome=plan_chat_outcome,
            ),
            runtime_capability_payload=runtime_capability_payload,
        ):
            yield event
        return

    route_decision = build_skill_route_decision(
        route_type=route.route_type,
        target_id=route.target_id,
        reason_code=route.reason_code,
        skill_registry=skill_registry,
    )
    task_text = _normalize_text(dict(runtime_capability_payload or {}).get("task_text")) or sanitized_message
    record_executive_handoff(
        request_id=request_id,
        session_id=session_id,
        handoff_route=route.route_type,
        handoff_target=route.target_id or None,
        handoff_reason=route.reason_code,
        extra={
            "skill_family": route_decision.skill_family,
            "skill_id": route_decision.skill_id,
        },
    )

    if route.route_type == "skill":
        outcome = await asyncio.to_thread(
            _execute_skill,
            request_body=request_body,
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            intent_id=intent_id,
            skill_id=route.target_id,
            task_text=task_text,
            user_message=sanitized_message,
            governor_result=governor_result,
            intent_draft=intent_draft,
            emotion_context=emotion_context,
            render_target_lang=render_target_lang,
        )
    else:
        outcome = await _dispatch_to_sub_agent(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            target_id=route.target_id or "intent_agent",
            task_text=task_text,
            user_message=sanitized_message,
            context={
                "skill_family": route_decision.skill_family or "general",
                "route_reason": route.reason_code,
            },
        )

    if runtime_capability_payload is not None:
        record_runtime_capability_execution(
            request_id=request_id,
            runtime_capability_payload=runtime_capability_payload,
            success=outcome.success,
            latency_ms=outcome.latency_ms,
            error_text=outcome.error_text or None,
            executor_route=route.route_type,
            executor_target=route.target_id or "",
            actor="executive_runtime",
        )

    if not outcome.success:
        recovery_plan = _build_failure_recovery_plan(
            route_type=route.route_type,
            target_id=route.target_id,
            skill_family=route_decision.skill_family or "general",
            payload=outcome.payload,
        )
        if recovery_plan:
            runtime_task_registry.mark_recovering(
                runtime_task.task_id,
                detail=recovery_plan["reason_code"],
                recovery_plan=recovery_plan,
            )
            if recovery_plan["route_type"] == "delegate":
                recovered = await _dispatch_to_sub_agent(
                    request_id=request_id,
                    session_id=session_id,
                    person_id=person_id,
                    target_id=recovery_plan["target_id"],
                    task_text=task_text,
                    user_message=sanitized_message,
                    context={
                        "skill_family": route_decision.skill_family or "general",
                        "route_reason": recovery_plan["reason_code"],
                    },
                )
                runtime_task_registry.attach_recovery_result(
                    runtime_task.task_id,
                    recovery_status="SUCCESS" if recovered.success else "FAILED",
                    recovery_result_text=recovered.result_text,
                    recovery_error=recovered.error_text,
                )
                if recovered.success:
                    outcome = recovered

    if outcome.success:
        runtime_task_registry.mark_completed(
            runtime_task.task_id,
            detail=f"{route.route_type} completed",
            result_text=outcome.result_text,
            result_code="EXECUTED",
        )
    else:
        runtime_task_registry.mark_failed(
            runtime_task.task_id,
            detail=f"{route.route_type} failed",
            error=outcome.error_text,
            result_code="EXECUTION_FAILED",
            result_text=outcome.result_text,
        )

    trace_stage_meta = {
        "cortex_orchestrator_used": True,
        "executive_router_used": True,
        "skill_route_type": route.route_type,
        "skill_family": route_decision.skill_family or "general",
        "skill_id": route_decision.skill_id,
        "skill_route_reason": route.reason_code,
        # Phase B-4: 器官族路由影响可观测性字段
        "organ_route_override": _organ_route_override,
        "organ_route_reason": _organ_route_reason,
        "organ_route_meta": _organ_route_meta if _organ_route_meta else None,
        "bio_ops_runtime_trace": _bio_ops_runtime_trace,
    }
    async for event in _stream_chat_with_context(
        request_body=request_body,
        route_reason=route.reason_code,
        request_id=request_id,
        session_id=session_id,
        person_id=person_id,
        sanitized_message=sanitized_message,
        emotion_context=emotion_context,
        emotion_snapshot=emotion_snapshot,
        bond_summary=bond_summary,
        retrieval_evidence_items=retrieval_evidence_items,
        retrieval_summary=retrieval_summary,
        render_target_lang=render_target_lang,
        llm_runtime=llm_runtime,
        ts_ms=ts_ms,
        intent_draft=intent_draft,
        trace_stage_meta=trace_stage_meta,
        sub_agent_context=_build_sub_agent_context(
            agent_id=route.target_id or route.route_type,
            task_text=task_text,
            outcome=outcome,
        ),
        runtime_capability_payload=runtime_capability_payload,
    ):
        yield event


__all__ = [
    "_hard_rule_route",
    "_maybe_build_multi_step_plan",
    "_should_force_complex_plan",
    "_build_task_progress_sub_context",
    "_dispatch_to_sub_agent",
    "_execute_skill",
    "_maybe_synthesize_runtime_capability",
    "_build_failure_recovery_plan",
    "_executive_runtime_core_impl",
]
