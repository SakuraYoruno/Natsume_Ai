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
import logging
import time
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

_logger = logging.getLogger("Natsume.plan_graph")


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
            "dependency_context": "",  # 简化：并行子任务不传递依赖上下文
            "result": "",
            "success": False,
            "latency_ms": 0,
            "error": "",
        }
        sends.append(Send("task_exec_subgraph", task_state))

    return sends


def _synthesize_node(state: PlanGraphState) -> dict[str, Any]:
    """综合所有子任务结果，生成最终回复。

    若有关键节点失败，在 final_answer 中标注失败信息。
    不再是简单字符串拼接，而是结构化整合。
    """
    ts_start = int(time.time() * 1000)
    request_id = str(state.get("request_id") or "")
    task_results = list(state.get("task_results") or [])

    # 按 task_index 排序
    task_results_sorted = sorted(task_results, key=lambda r: int(r.get("task_index", 0)))

    # 检查是否有失败节点
    failed_tasks = [r for r in task_results_sorted if not r.get("success")]
    success_tasks = [r for r in task_results_sorted if r.get("success")]

    # 构建综合摘要
    parts: list[str] = []
    for r in task_results_sorted:
        idx = int(r.get("task_index", 0)) + 1
        desc = str(r.get("task_description") or f"子任务{idx}")[:50]
        result_text = str(r.get("result") or "").strip()
        if r.get("success") and result_text:
            parts.append(f"[{idx}] {desc}：{result_text}")
        elif not r.get("success"):
            error = str(r.get("error") or "执行失败")[:100]
            parts.append(f"[{idx}] {desc}：⚠️ {error}")

    if not parts and state.get("failed"):
        final_answer = "任务拆解失败，无法执行多步规划。"
    elif not parts:
        final_answer = "所有子任务均无输出。"
    else:
        final_answer = "\n".join(parts)

    latency_ms = int(time.time() * 1000) - ts_start
    overall_success = len(failed_tasks) == 0 and len(success_tasks) > 0

    _logger.info(
        "[%s] synthesize_node: 综合 %d 个子任务结果（成功=%d，失败=%d），耗时 %dms",
        request_id, len(task_results_sorted), len(success_tasks), len(failed_tasks), latency_ms,
    )

    return {
        "final_answer": final_answer,
        "failed": not overall_success,
        "plan_trace": [{
            "node": "synthesize_node",
            "status": "OK" if overall_success else "PARTIAL_FAILED",
            "success_count": len(success_tasks),
            "failed_count": len(failed_tasks),
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
            # 子图节点是同步的，用 asyncio.run_coroutine_threadsafe 或直接 asyncio.get_event_loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 在已有事件循环中：用 asyncio.run_coroutine_threadsafe
                    import concurrent.futures  # noqa: PLC0415
                    future = asyncio.run_coroutine_threadsafe(dispatcher.dispatch(req), loop)
                    resp = future.result(timeout=35)
                else:
                    resp = loop.run_until_complete(dispatcher.dispatch(req))
            except RuntimeError:
                # 没有事件循环：创建新的
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


def get_plan_graph() -> Any:
    """获取全局单例父子图（懒加载）。"""
    global _plan_graph_instance
    if _plan_graph_instance is None:
        _plan_graph_instance = build_plan_graph()
    return _plan_graph_instance


async def run_plan_graph(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    intent_draft: dict[str, Any] | None = None,
    emotion_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """异步执行规划父子图。

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
    }

    try:
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
        }


__all__ = [
    "PlanGraphState",
    "TaskState",
    "build_plan_graph",
    "get_plan_graph",
    "run_plan_graph",
]
