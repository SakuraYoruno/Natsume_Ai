"""plan_graph.py — LangGraph 父子图规划执行模块。

架构：
  第一层：RULE_ROUTE_MAP 规则映射（planning_agent.py）→ 命中则直接路由
  第二层：LLM 节点判断（executive_runtime_core._should_force_complex_plan）
  规划执行：本模块 LangGraph 父子图

父图结构：
  START → plan_decompose_node（LLM 拆解子任务）
        → [Send API] 并行子图（每个子任务一个 task_exec_subgraph）
        → synthesize_node（LLM 综合所有结果）
        → END

子图结构（task_exec_subgraph）：
  START → route_node（规则路由 or 委托子 Agent）→ END

关键设计：
- 使用 LangGraph Send API 实现真正的并行子图（非 asyncio.gather 手动并发）
- synthesize 步骤不再被过滤，由 synthesize_node 做 LLM 语义整合
- 关键节点失败时通过 interrupt_before 中断，不继续执行后续节点
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from typing import Annotated, Any, Mapping, TypedDict
from urllib.parse import quote

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from host_brain.conversation import build_chat_messages, conversation_max_turns
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

_logger = logging.getLogger("Natsume.plan_graph")

CANONICAL_PLANNER_SHADOW_ONLY = "CANONICAL_PLANNER_SHADOW_ONLY"


def _shadow_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _load_prompt_templates() -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged("plan_graph.synthesize.prompt_templates")
    return dict(raw) if isinstance(raw, dict) else {}


class _SafeTemplateDict(dict[str, str]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict({str(key): "" if value is None else str(value) for key, value in kwargs.items()})
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _build_synthesize_prompts(*, user_message: str, sub_results_text: str) -> tuple[str, str]:
    templates = _load_prompt_templates()
    system_prompt = str(templates.get("system_prompt") or "").strip()
    user_prompt_template = str(templates.get("user_prompt_template") or "").strip()
    if not system_prompt or not user_prompt_template:
        return "", ""
    user_prompt = _format_template_text(
        user_prompt_template,
        user_message=user_message,
        sub_results_text=sub_results_text,
    ).strip()
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# 状态定义
# ---------------------------------------------------------------------------

def _merge_task_results(left: list[dict], right: list[dict]) -> list[dict]:
    """合并子任务结果列表（Reducer）。"""
    return list(left or []) + list(right or [])


class PlanGraphState(TypedDict):
    """父图状态。"""
    request_id: str
    session_id: str
    person_id: str
    user_message: str                          # 原始用户消息
    sub_tasks: list[dict[str, Any]]            # 拆解后的子任务列表
    task_results: Annotated[list[dict[str, Any]], _merge_task_results]  # 子图并行结果（Reducer 合并）
    final_answer: str                          # synthesize_node 生成的最终回复
    plan_trace: list[dict[str, Any]]           # 执行追踪
    intent_draft: dict[str, Any]               # 来自上游的 intent_draft
    emotion_context: dict[str, Any]            # 情感上下文
    failed: bool                               # 是否有关键节点失败
    graph_id: str                              # canonical graph identity（opt-in）
    graph_revision: int                        # canonical graph revision（opt-in）
    execution_batches: list[list[str]]         # upstream dependency batches（opt-in）
    planner_shadow_comparison: dict[str, Any]  # read-only comparison payload


class TaskState(TypedDict):
    """子图状态（每个子任务独立）。"""
    request_id: str
    session_id: str
    person_id: str
    task_index: int                            # 子任务序号（0-based）
    task_description: str                      # 子任务描述
    route_type: str                            # "skill" / "delegate" / "chat"
    target_id: str                             # skill_id 或 agent_id
    dependency_context: str                    # 前置任务的结果（依赖上下文）
    result: str                                # 执行结果
    success: bool
    latency_ms: int
    error: str


def canonical_planner_adapter_enabled(explicit: bool | None = None) -> bool:
    """Resolve the opt-in switch; production behavior remains legacy by default."""

    if explicit is not None:
        return bool(explicit)
    from host_brain.app.settings import get_core_settings  # noqa: PLC0415

    return bool(get_core_settings().canonical_planner_adapter_enabled)


def _coerce_parent_work_order(value: Any) -> Any | None:
    if value is None:
        return None
    from host_brain.agent_runtime.work_order import WorkOrderV3  # noqa: PLC0415

    if isinstance(value, WorkOrderV3):
        value.validate()
        return value
    if isinstance(value, Mapping):
        return WorkOrderV3.from_mapping(value)
    from host_brain.agent_runtime.contract_errors import (  # noqa: PLC0415
        SUB_AGENT_LINEAGE_INVALID,
        ContractViolation,
    )

    raise ContractViolation(
        SUB_AGENT_LINEAGE_INVALID,
        "canonical planner parent_work_order must be a WorkOrder mapping",
    )


def _coerce_existing_plan(
    *,
    canonical_plan: Any | None,
    upstream_plan: Any | None,
    parent_work_order: Any | None,
) -> Any:
    from host_brain.agent_runtime.contract_errors import (  # noqa: PLC0415
        TASK_GRAPH_INVALID,
        ContractViolation,
    )
    from host_brain.agent_runtime.planner_adapter import (  # noqa: PLC0415
        CanonicalPlannerOutput,
        GraphRevisionIdentity,
        adapt_skill_dag_plan,
    )

    if isinstance(canonical_plan, CanonicalPlannerOutput):
        canonical_plan.validate()
        return canonical_plan
    if isinstance(canonical_plan, Mapping):
        return CanonicalPlannerOutput.from_mapping(canonical_plan)
    if upstream_plan is None:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "canonical planner mode requires an upstream plan; second planning is forbidden",
        )
    if parent_work_order is None:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "upstream SkillDag adaptation requires parent WorkOrder lineage",
        )
    source_plan_id = str(getattr(upstream_plan, "plan_id", "") or "").strip()
    if not source_plan_id:
        raise ContractViolation(
            TASK_GRAPH_INVALID,
            "upstream plan does not expose SkillDagPlan.plan_id",
        )
    identity = GraphRevisionIdentity(
        graph_id=str(parent_work_order.task_graph_id or source_plan_id).strip(),
        revision=max(1, int(parent_work_order.graph_revision or 1)),
        root_work_order_id=parent_work_order.root_work_order_id,
    )
    return adapt_skill_dag_plan(
        upstream_plan,
        identity=identity,
        planner_ref=f"planner://skill-dag/{source_plan_id}",
        created_at=int(
            dict(parent_work_order.metadata or {}).get("created_ts_ms")
            or dict(parent_work_order.metadata or {}).get("ts_ms")
            or 0
        ),
    )


def prepare_existing_plan_graph_state(
    *,
    plan: Any,
    parent_work_order: Any | None,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    intent_draft: Mapping[str, Any] | None = None,
    emotion_context: Mapping[str, Any] | None = None,
    shadow_comparison: Mapping[str, Any] | None = None,
) -> PlanGraphState:
    """Project an existing canonical plan into PlanGraph state without replanning."""

    from host_brain.agent_runtime.contract_errors import (  # noqa: PLC0415
        SUB_AGENT_LINEAGE_INVALID,
        ContractViolation,
    )
    from host_brain.agent_runtime.planner_adapter import (  # noqa: PLC0415
        CanonicalPlannerOutput,
        build_child_work_order_for_plan_node,
    )

    canonical = (
        plan
        if isinstance(plan, CanonicalPlannerOutput)
        else CanonicalPlannerOutput.from_mapping(plan if isinstance(plan, Mapping) else {})
    )
    canonical.validate()
    parent = _coerce_parent_work_order(parent_work_order)
    if parent is not None and parent.root_work_order_id != canonical.identity.root_work_order_id:
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "PlanGraph parent WorkOrder root differs from canonical graph root",
        )
    if parent is not None and parent.task_graph_id is not None and (
        parent.task_graph_id != canonical.identity.graph_id
        or parent.graph_revision != canonical.identity.revision
    ):
        raise ContractViolation(
            SUB_AGENT_LINEAGE_INVALID,
            "PlanGraph parent WorkOrder graph identity differs from canonical revision",
        )

    sub_tasks: list[dict[str, Any]] = []
    for task_index, node in enumerate(canonical.nodes):
        child_projection: dict[str, Any] = {}
        request_projection: dict[str, Any] = {}
        if node.route_type in {"delegate", "chat"}:
            if parent is None:
                raise ContractViolation(
                    SUB_AGENT_LINEAGE_INVALID,
                    f"sub-agent node {node.node_id} requires parent WorkOrder lineage",
                )
            child = build_child_work_order_for_plan_node(
                parent_work_order=parent,
                plan=canonical,
                node_id=node.node_id,
            )
            child_payload = child.to_dict()
            from host_brain.app.sub_agent_protocol import (  # noqa: PLC0415
                build_sub_agent_request_from_child_work_order,
            )

            request = build_sub_agent_request_from_child_work_order(
                child_work_order=child,
                agent_id=node.target_id,
                request_id=f"{request_id}::{node.node_id}",
                session_id=session_id,
                context_refs=node.dependency_refs,
                context={
                    "source": "plan_graph_canonical_shadow",
                    "graph_id": canonical.identity.graph_id,
                    "graph_revision": canonical.identity.revision,
                },
            )
            request_contract = dict(
                (request.context or {}).get("canonical_sub_agent_request") or {}
            )
            child_projection = {
                "work_order_ref": (
                    "artifact://work-order/"
                    f"{quote(child.work_order_id, safe='')}"
                ),
                "work_order_digest": _shadow_digest(child_payload),
                "work_order_id": child.work_order_id,
                "root_work_order_id": child.root_work_order_id,
                "parent_work_order_id": child.parent_work_order_id,
                "task_graph_id": child.task_graph_id,
                "graph_revision": child.graph_revision,
                "task_node_id": child.task_node_id,
            }
            request_projection = {
                "agent_id": request.agent_id,
                "request_ref": (
                    "artifact://sub-agent-request/"
                    f"{quote(request.request_id, safe='')}"
                ),
                "request_digest": _shadow_digest(request_contract),
                "context_refs": list(request_contract.get("context_refs") or []),
            }
        sub_tasks.append(
            {
                "task_index": task_index,
                "node_id": node.node_id,
                "task_description_digest": _shadow_digest(node.task_description),
                "route_type": node.route_type,
                "target_id": node.target_id,
                "depends_on": list(node.dependencies),
                "dependency_refs": list(node.dependency_refs),
                "dependency_context": "\n".join(node.dependency_refs),
                "allow_parallel": node.allow_parallel,
                "graph_id": canonical.identity.graph_id,
                "graph_revision": canonical.identity.revision,
                "child_work_order_projection": child_projection,
                "sub_agent_request_projection": request_projection,
            }
        )

    return {
        "request_id": str(request_id or ""),
        "session_id": str(session_id or ""),
        "person_id": str(person_id or ""),
        "user_message": str(user_message or ""),
        "sub_tasks": sub_tasks,
        "task_results": [],
        "final_answer": "",
        "plan_trace": [
            {
                "node": "consume_existing_plan",
                "status": "OK",
                "source_kind": canonical.source_kind,
                "source_plan_id": canonical.source_plan_id,
                "graph_id": canonical.identity.graph_id,
                "graph_revision": canonical.identity.revision,
                "sub_task_count": len(sub_tasks),
                "replanned": False,
            }
        ],
        "intent_draft": dict(intent_draft or {}),
        "emotion_context": dict(emotion_context or {}),
        "failed": not bool(sub_tasks),
        "graph_id": canonical.identity.graph_id,
        "graph_revision": canonical.identity.revision,
        "execution_batches": [list(batch) for batch in canonical.execution_batches],
        "planner_shadow_comparison": dict(shadow_comparison or {}),
    }


# ---------------------------------------------------------------------------
# 父图节点
# ---------------------------------------------------------------------------

def _plan_decompose_node(state: PlanGraphState) -> dict[str, Any]:
    """LLM 拆解子任务节点。

    使用 PlanningAgent.create_plan() 拆解用户请求为子任务列表。
    拆解结果写入 state.sub_tasks。
    """
    ts_start = int(time.time() * 1000)
    user_message = str(state.get("user_message") or "").strip()
    request_id = str(state.get("request_id") or "")

    try:
        from host_brain.app.dependencies import get_planning_agent  # noqa: PLC0415
        from host_brain.app.skill_family_router import build_skill_route_decision  # noqa: PLC0415

        planning_agent = get_planning_agent()
        plan = planning_agent.create_plan(
            task_id=f"plan_graph::{request_id}",
            request=user_message,
        )

        # 只取 act 步骤（reason/synthesize 由图节点处理）
        act_steps = [
            step for step in (plan.steps or [])
            if getattr(step, "action_type", "") == "act"
            and str(getattr(step, "description", "") or "").strip()
        ]

        sub_tasks: list[dict[str, Any]] = []
        for i, step in enumerate(act_steps):
            description = str(step.description or "").strip()
            skill_name = str(step.skill_name or "").strip()

            # 路由决策
            route_type = "delegate"
            target_id = "intent_agent"
            if skill_name:
                try:
                    from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415
                    registry = get_skill_registry()
                    if registry.get(skill_name) is not None:
                        route_type = "skill"
                        target_id = skill_name
                    elif skill_name.endswith("_agent"):
                        route_type = "delegate"
                        target_id = skill_name
                except Exception:
                    pass

            sub_tasks.append({
                "task_index": i,
                "task_description": description,
                "route_type": route_type,
                "target_id": target_id,
                "depends_on": list(getattr(step, "depends_on", []) or []),
                "allow_parallel": not bool(getattr(step, "depends_on", [])),
            })

        latency_ms = int(time.time() * 1000) - ts_start
        _logger.info(
            "[%s] plan_decompose_node: 拆解为 %d 个子任务，耗时 %dms",
            request_id, len(sub_tasks), latency_ms,
        )

        return {
            "sub_tasks": sub_tasks,
            "plan_trace": [{
                "node": "plan_decompose_node",
                "status": "OK",
                "sub_task_count": len(sub_tasks),
                "latency_ms": latency_ms,
            }],
            "failed": len(sub_tasks) == 0,
        }

    except Exception as exc:
        latency_ms = int(time.time() * 1000) - ts_start
        _logger.warning("[%s] plan_decompose_node 失败: %s", request_id, exc)
        return {
            "sub_tasks": [],
            "plan_trace": [{
                "node": "plan_decompose_node",
                "status": "FAILED",
                "error": str(exc)[:200],
                "latency_ms": latency_ms,
            }],
            "failed": True,
        }


def _route_to_tasks(state: PlanGraphState) -> list[Send] | str:
    """条件边：将每个子任务 Send 到独立子图并行执行。

    若拆解失败（failed=True 或 sub_tasks 为空），直接跳到 synthesize_node。
    """
    if state.get("failed") or not state.get("sub_tasks"):
        return "synthesize_node"

    sends: list[Send] = []
    for task in state["sub_tasks"]:
        task_state: TaskState = {
            "request_id": str(state.get("request_id") or ""),
            "session_id": str(state.get("session_id") or ""),
            "person_id": str(state.get("person_id") or ""),
            "task_index": int(task.get("task_index", 0)),
            "task_description": str(task.get("task_description") or ""),
            "route_type": str(task.get("route_type") or "delegate"),
            "target_id": str(task.get("target_id") or "intent_agent"),
            "dependency_context": str(task.get("dependency_context") or ""),
            "result": "",
            "success": False,
            "latency_ms": 0,
            "error": "",
        }
        sends.append(Send("task_exec_subgraph", task_state))

    return sends


def _synthesize_node(state: PlanGraphState) -> dict[str, Any]:
    """综合所有子任务结果，通过 LLM 语义整合生成最终回复。

    改进（P1-2）：
    - 优先调用 LLM 做真正的语义整合，将所有子任务结果作为上下文，生成连贯的最终回复
    - LLM 调用失败时降级为结构化拼接（保证可用性）
    - 对 task_results 做去重合并（P1-5），防止 Reducer 并发写入重复条目
    """
    ts_start = int(time.time() * 1000)
    request_id = str(state.get("request_id") or "")
    task_results = list(state.get("task_results") or [])

    # P1-5：去重合并 —— 按 task_index 去重，保留最后一条（并发写入时可能有重复）
    deduped: dict[int, dict] = {}
    for r in task_results:
        idx = int(r.get("task_index", 0))
        deduped[idx] = r
    task_results_sorted = sorted(deduped.values(), key=lambda r: int(r.get("task_index", 0)))

    # 检查是否有失败节点
    failed_tasks = [r for r in task_results_sorted if not r.get("success")]
    success_tasks = [r for r in task_results_sorted if r.get("success")]
    overall_success = len(failed_tasks) == 0 and len(success_tasks) > 0

    # 构建结构化摘要（作为 LLM 输入上下文，也作为降级输出）
    parts: list[str] = []
    for r in task_results_sorted:
        idx = int(r.get("task_index", 0)) + 1
        desc = str(r.get("task_description") or f"子任务{idx}")[:60]
        result_text = str(r.get("result") or "").strip()
        if r.get("success") and result_text:
            parts.append(f"[子任务{idx}] {desc}：{result_text}")
        elif not r.get("success"):
            error = str(r.get("error") or "执行失败")[:100]
            parts.append(f"[子任务{idx}] {desc}：⚠️ {error}")

    if not parts and state.get("failed"):
        fallback_answer = "任务拆解失败，无法执行多步规划。"
    elif not parts:
        fallback_answer = "所有子任务均无输出。"
    else:
        fallback_answer = "\n".join(parts)

    # ── 尝试 LLM 语义整合 ──
    user_message = str(state.get("user_message") or "").strip()
    llm_used = False
    model_attempt_trace: dict[str, Any] = {}
    final_answer = fallback_answer

    if parts and user_message:
        try:
            from host_brain.model_runtime.invocation import complete_compiled_prompt_sync  # noqa: PLC0415
            from host_brain.model_runtime.live_prompts import PLAN_SYNTHESIZE_PROMPT_ID  # noqa: PLC0415
            from host_brain.model_runtime.trace import model_attempt_result_trace  # noqa: PLC0415

            sub_results_text = "\n".join(parts)
            system_prompt, user_prompt = _build_synthesize_prompts(
                user_message=user_message,
                sub_results_text=sub_results_text,
            )

            if system_prompt and user_prompt:
                try:
                    from host_brain.app.dependencies import get_conversation_history  # noqa: PLC0415

                    history_window = get_conversation_history().fetch_window_for_messages(
                        session_id=str(state.get("session_id") or "").strip(),
                        max_turns=conversation_max_turns(),
                        exclude_request_id=request_id,
                        render_target_lang="zh",
                    )
                except Exception:
                    history_window = None

                synth_system_prompt = system_prompt
                if history_window and str(history_window.older_summary or "").strip():
                    synth_system_prompt = (
                        f"{system_prompt}\n\n【更早对话摘要】\n{str(history_window.older_summary).strip()}"
                    )

                messages = build_chat_messages(
                    system_prompt=synth_system_prompt,
                    history_turns=list(getattr(history_window, "recent_turns", []) or []),
                    user_message=user_prompt,
                )
                result = complete_compiled_prompt_sync(
                    prompt_id=PLAN_SYNTHESIZE_PROMPT_ID,
                    request_id=request_id,
                    scene_type="PLAN_SYNTHESIZE",
                    cognitive_tier="B",
                    deadline_ms=8_000,
                    messages=messages,
                )
                model_attempt_trace = model_attempt_result_trace(result)
                llm_text = str(result.content or "").strip() if result.success else ""
                if llm_text:
                    final_answer = llm_text
                    llm_used = True
                    _logger.info(
                        "[%s] synthesize_node: LLM 语义整合成功，输出 %d 字符",
                        request_id, len(final_answer),
                    )

        except Exception as llm_exc:
            _logger.warning(
                "[%s] synthesize_node: LLM 语义整合失败，降级为结构化拼接: %s",
                request_id, llm_exc,
            )
            final_answer = fallback_answer

    latency_ms = int(time.time() * 1000) - ts_start

    _logger.info(
        "[%s] synthesize_node: 综合 %d 个子任务结果（成功=%d，失败=%d，llm_used=%s），耗时 %dms",
        request_id, len(task_results_sorted), len(success_tasks), len(failed_tasks), llm_used, latency_ms,
    )

    return {
        "final_answer": final_answer,
        "failed": not overall_success,
        "plan_trace": [{
            "node": "synthesize_node",
            "status": "OK" if overall_success else "PARTIAL_FAILED",
            "success_count": len(success_tasks),
            "failed_count": len(failed_tasks),
            "llm_used": llm_used,
            "model_attempt": model_attempt_trace,
            "latency_ms": latency_ms,
        }],
    }


# ---------------------------------------------------------------------------
# 子图节点
# ---------------------------------------------------------------------------

def _task_route_node(state: TaskState) -> dict[str, Any]:
    """子图执行节点：根据 route_type 路由到 skill 或 sub_agent。

    关键改进：
    - skill 路由：同步调用 _execute_skill（通过 asyncio.to_thread 包装）
    - delegate 路由：通过 SubAgentDispatcher.dispatch() 调用子 Agent
    - 失败时返回 success=False，synthesize_node 会标注失败信息
    """
    ts_start = int(time.time() * 1000)
    request_id = str(state.get("request_id") or "")
    task_description = str(state.get("task_description") or "").strip()
    route_type = str(state.get("route_type") or "delegate")
    target_id = str(state.get("target_id") or "intent_agent")
    task_index = int(state.get("task_index", 0))

    try:
        if route_type == "skill":
            # skill 路由：调用 skill executor
            from host_brain.skills.executor_registry import dispatch_executor_payload  # noqa: PLC0415
            result_payload = dispatch_executor_payload(
                skill_id=target_id,
                payload={
                    "query": task_description,
                    "text": task_description,
                    "request_id": request_id,
                },
                context={
                    "request_id": request_id,
                    "session_id": str(state.get("session_id") or ""),
                    "source": "plan_graph",
                },
            )
            result_text = str(result_payload.get("result") or result_payload.get("output") or "").strip()
            success = bool(result_text)

        else:
            # delegate 路由：调用子 Agent（同步包装）
            # 注意：_task_route_node 是同步函数，运行在 asyncio.to_thread 的线程池中。
            # 不能使用 run_coroutine_threadsafe 提交到外层 loop（会死锁：外层 loop 在等
            # to_thread 完成，to_thread 在等外层 loop 执行协程）。
            # 正确做法：在当前线程中用 asyncio.run() 创建独立的新事件循环执行协程。
            from host_brain.app.sub_agent_protocol import SubAgentRequest, get_sub_agent_dispatcher  # noqa: PLC0415
            dispatcher = get_sub_agent_dispatcher()
            req = SubAgentRequest(
                agent_id=target_id,
                task=task_description,
                user_message=task_description,
                request_id=request_id,
                session_id=str(state.get("session_id") or ""),
                context={"source": "plan_graph", "task_index": task_index},
                timeout_ms=30_000,
            )
            # asyncio.run() 在当前线程创建全新的独立事件循环，不与外层 loop 交互，无死锁风险
            resp = asyncio.run(dispatcher.dispatch(req))
            result_text = str(resp.result or "").strip()
            success = resp.success

        latency_ms = int(time.time() * 1000) - ts_start
        _logger.info(
            "[%s] task_exec_subgraph[%d]: route=%s target=%s success=%s latency=%dms",
            request_id, task_index, route_type, target_id, success, latency_ms,
        )

        return {
            "result": result_text,
            "success": success,
            "latency_ms": latency_ms,
            "error": "" if success else "no_output",
        }

    except Exception as exc:
        latency_ms = int(time.time() * 1000) - ts_start
        _logger.warning(
            "[%s] task_exec_subgraph[%d] 失败: route=%s target=%s error=%s",
            request_id, task_index, route_type, target_id, exc,
        )
        return {
            "result": "",
            "success": False,
            "latency_ms": latency_ms,
            "error": str(exc)[:200],
        }


def _collect_task_result(state: TaskState) -> dict[str, Any]:
    """将子图结果收集到父图的 task_results（通过 Reducer 合并）。"""
    return {
        "task_results": [{
            "task_index": int(state.get("task_index", 0)),
            "task_description": str(state.get("task_description") or ""),
            "route_type": str(state.get("route_type") or ""),
            "target_id": str(state.get("target_id") or ""),
            "result": str(state.get("result") or ""),
            "success": bool(state.get("success", False)),
            "latency_ms": int(state.get("latency_ms", 0)),
            "error": str(state.get("error") or ""),
        }],
    }


# ---------------------------------------------------------------------------
# 子图构建
# ---------------------------------------------------------------------------

def _build_task_exec_subgraph() -> Any:
    """构建子任务执行子图。

    子图：START → task_route_node → collect_result → END
    """
    sub = StateGraph(TaskState)
    sub.add_node("task_route_node", RunnableLambda(_task_route_node))
    sub.add_node("collect_result", RunnableLambda(_collect_task_result))
    sub.add_edge(START, "task_route_node")
    sub.add_edge("task_route_node", "collect_result")
    sub.add_edge("collect_result", END)
    return sub.compile()


# ---------------------------------------------------------------------------
# 父图构建
# ---------------------------------------------------------------------------

def build_plan_graph() -> Any:
    """构建 LangGraph 父子图规划执行图。

    父图结构：
      START
        → plan_decompose_node（LLM 拆解子任务）
        → [条件边 _route_to_tasks]
            → Send("task_exec_subgraph", task_state) × N（并行）
            或 → synthesize_node（若拆解失败）
        → synthesize_node（综合所有子任务结果）
        → END

    Returns:
        编译后的 LangGraph 父图
    """
    task_exec_subgraph = _build_task_exec_subgraph()

    graph = StateGraph(PlanGraphState)

    # 父图节点
    graph.add_node("plan_decompose_node", RunnableLambda(_plan_decompose_node))
    graph.add_node("task_exec_subgraph", task_exec_subgraph)
    graph.add_node("synthesize_node", RunnableLambda(_synthesize_node))

    # 边
    graph.add_edge(START, "plan_decompose_node")
    graph.add_conditional_edges(
        "plan_decompose_node",
        _route_to_tasks,
        ["task_exec_subgraph", "synthesize_node"],
    )
    graph.add_edge("task_exec_subgraph", "synthesize_node")
    graph.add_edge("synthesize_node", END)

    compiled = graph.compile()
    _logger.info("build_plan_graph: LangGraph 父子图构建成功")
    return compiled


# ---------------------------------------------------------------------------
# 入口函数（供 executive_runtime_core 调用）
# ---------------------------------------------------------------------------

_plan_graph_instance: Any = None
_plan_graph_lock = threading.Lock()

def get_plan_graph() -> Any:
    """获取全局单例父子图（懒加载，线程安全）。"""
    global _plan_graph_instance
    if _plan_graph_instance is None:
        with _plan_graph_lock:
            # 双重检查锁定（Double-Checked Locking）
            if _plan_graph_instance is None:
                _plan_graph_instance = build_plan_graph()
    return _plan_graph_instance


def _build_existing_plan_shadow_result(
    *,
    initial_state: PlanGraphState,
    canonical_plan: Any,
) -> dict[str, Any]:
    """Return PlanGraph's existing-plan shadow view without dispatching work."""

    canonical_plan.validate()
    plan_trace = list(initial_state.get("plan_trace") or [])
    for batch_index, batch in enumerate(list(initial_state.get("execution_batches") or [])):
        plan_trace.append(
            {
                "node": "shadow_existing_plan_batch",
                "status": "READY",
                "batch_index": batch_index,
                "node_ids": list(batch or []),
                "parallel": len(list(batch or [])) > 1,
            }
        )
    return {
        "success": False,
        "final_answer": "",
        "sub_tasks": list(initial_state.get("sub_tasks") or []),
        "task_results": [],
        "plan_trace": plan_trace,
        "latency_ms": 0,
        "graph_id": str(initial_state.get("graph_id") or ""),
        "graph_revision": int(initial_state.get("graph_revision") or 0),
        "parallel_enabled": any(
            len(list(batch or [])) > 1
            for batch in list(initial_state.get("execution_batches") or [])
        ),
        "planner_shadow_comparison": dict(
            initial_state.get("planner_shadow_comparison") or {}
        ),
        "canonical_plan_input": canonical_plan.to_shadow_projection(),
        "shadow_only": True,
        "shadow_status": "READY",
        "external_side_effect_count": 0,
        "reason_code": CANONICAL_PLANNER_SHADOW_ONLY,
    }


async def run_plan_graph(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    intent_draft: dict[str, Any] | None = None,
    emotion_context: dict[str, Any] | None = None,
    upstream_plan: Any | None = None,
    canonical_plan: Any | None = None,
    parent_work_order: Any | None = None,
    canonical_planner_enabled: bool | None = None,
) -> dict[str, Any]:
    """异步执行 legacy 父子图，或生成 opt-in canonical shadow 结果。

    canonical adapter 开关只验证和投影上游计划；它不会调度 skill、
    sub-agent 或 synthesize LLM。真实执行切流留给后续 scheduler 任务。

    Returns:
        {
            "success": bool,
            "final_answer": str,       # synthesize_node 生成的最终回复
            "sub_tasks": list,         # 拆解的子任务列表
            "task_results": list,      # 每个子任务的执行结果
            "plan_trace": list,        # 执行追踪
            "latency_ms": int,
        }
    """
    ts_start = int(time.time() * 1000)

    try:
        if canonical_planner_adapter_enabled(canonical_planner_enabled):
            from host_brain.agent_runtime.planner_adapter import (  # noqa: PLC0415
                CanonicalPlannerOutput,
                build_planner_shadow_comparison,
                validate_same_graph_revision,
            )

            parent = _coerce_parent_work_order(parent_work_order)
            source_plan = _coerce_existing_plan(
                canonical_plan=canonical_plan,
                upstream_plan=upstream_plan,
                parent_work_order=parent,
            )
            # Serialize then parse at the PlanGraph boundary.  This proves the
            # consumer did not mint a second graph identity or revision.
            consumer_plan = CanonicalPlannerOutput.from_mapping(
                source_plan.to_plan_graph_input()
            )
            validate_same_graph_revision(source_plan, consumer_plan)
            shadow_comparison = build_planner_shadow_comparison(
                source_plan,
                consumer_plan,
            )
            canonical_state = prepare_existing_plan_graph_state(
                plan=consumer_plan,
                parent_work_order=parent,
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                user_message=user_message,
                intent_draft=intent_draft,
                emotion_context=emotion_context,
                shadow_comparison=shadow_comparison,
            )
            return _build_existing_plan_shadow_result(
                initial_state=canonical_state,
                canonical_plan=consumer_plan,
            )

        graph = get_plan_graph()
        initial_state: PlanGraphState = {
            "request_id": request_id,
            "session_id": session_id,
            "person_id": person_id,
            "user_message": user_message,
            "sub_tasks": [],
            "task_results": [],
            "final_answer": "",
            "plan_trace": [],
            "intent_draft": dict(intent_draft or {}),
            "emotion_context": dict(emotion_context or {}),
            "failed": False,
            "graph_id": "",
            "graph_revision": 0,
            "execution_batches": [],
            "planner_shadow_comparison": {},
        }
        # LangGraph 图执行（同步 invoke 包装为异步）
        final_state = await asyncio.to_thread(
            graph.invoke,
            initial_state,
            {"configurable": {"thread_id": request_id}},
        )

        latency_ms = int(time.time() * 1000) - ts_start
        _logger.info(
            "[%s] run_plan_graph 完成: success=%s latency=%dms",
            request_id, not final_state.get("failed"), latency_ms,
        )

        return {
            "success": not bool(final_state.get("failed")),
            "final_answer": str(final_state.get("final_answer") or ""),
            "sub_tasks": list(final_state.get("sub_tasks") or []),
            "task_results": list(final_state.get("task_results") or []),
            "plan_trace": list(final_state.get("plan_trace") or []),
            "latency_ms": latency_ms,
        }

    except Exception as exc:
        latency_ms = int(time.time() * 1000) - ts_start
        _logger.warning("[%s] run_plan_graph 失败: %s", request_id, exc)
        return {
            "success": False,
            "final_answer": f"规划执行失败：{exc!s}",
            "sub_tasks": [],
            "task_results": [],
            "plan_trace": [{"node": "run_plan_graph", "status": "FAILED", "error": str(exc)[:200]}],
            "latency_ms": latency_ms,
            "reason_code": str(getattr(exc, "reason_code", "PLAN_GRAPH_FAILED")),
        }


__all__ = [
    "PlanGraphState",
    "TaskState",
    "CANONICAL_PLANNER_SHADOW_ONLY",
    "build_plan_graph",
    "canonical_planner_adapter_enabled",
    "get_plan_graph",
    "prepare_existing_plan_graph_state",
    "run_plan_graph",
]
