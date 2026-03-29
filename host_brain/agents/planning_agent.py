"""任务规划代理（Phase XI 方案 4）。

职责：对复杂请求做 ReAct 式任务分解——Reasoning + Acting 交替。
设计：先规划步骤，再逐步执行，中间可追问用户。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

_logger = logging.getLogger("Natsume.agents.planning")


# ---------------------------------------------------------------------------
# 步骤状态
# ---------------------------------------------------------------------------
class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_INPUT = "needs_input"    # 需要用户补充信息
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class PlanStep:
    """任务计划中的单个步骤。"""
    step_id: int
    description: str
    action_type: str  # "reason" / "act" / "ask" / "synthesize"
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    skill_name: str | None = None  # 需要调用的技能
    depends_on: list[int] = field(default_factory=list)  # 依赖的前置步骤

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "action_type": self.action_type,
            "status": self.status.value,
            "skill_name": self.skill_name,
            "depends_on": list(self.depends_on),
            "has_result": bool(self.result),
        }


@dataclass
class TaskPlan:
    """完整的任务计划。"""
    task_id: str
    original_request: str
    steps: list[PlanStep]
    created_ts_ms: int = 0
    status: str = "planning"  # planning / executing / completed / failed
    current_step: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "original_request": self.original_request[:100],
            "status": self.status,
            "total_steps": len(self.steps),
            "current_step": self.current_step,
            "completed_steps": sum(
                1 for s in self.steps if s.status == StepStatus.COMPLETED
            ),
            "steps": [s.as_dict() for s in self.steps],
        }


@dataclass
class PlanExecutionResult:
    """计划执行结果。"""
    task_id: str
    success: bool
    steps_completed: int
    steps_total: int
    final_response: str
    needs_user_input: bool = False
    pending_question: str = ""
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "needs_user_input": self.needs_user_input,
            "pending_question": self.pending_question[:200] if self.pending_question else "",
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# 任务复杂度判定
# ---------------------------------------------------------------------------
_COMPLEX_INDICATORS = frozenset({
    "计划", "安排", "规划", "步骤", "流程",
    "先…再…", "首先", "然后", "接着", "最后",
    "帮我", "需要", "想要", "要求",
    "对比", "比较", "分析", "评估", "调研",
})

_MULTI_PART_PATTERN_WORDS = frozenset({
    "和", "并且", "同时", "另外", "还有", "以及",
})


# ---------------------------------------------------------------------------
# PlanningAgent
# ---------------------------------------------------------------------------
class PlanningAgent:
    """ReAct 式任务规划代理。

    用法::

        agent = PlanningAgent()

        # 1. 判断是否需要规划
        if agent.needs_planning("帮我计划周末行程，看电影再吃饭"):
            # 2. 生成计划
            plan = agent.create_plan(
                task_id="task_001",
                request="帮我计划周末行程，看电影再吃饭",
            )
            # 3. 逐步执行（或交给 Supervisor）
            for step in plan.steps:
                print(f"Step {step.step_id}: {step.description}")
    """

    def __init__(
        self,
        *,
        max_steps: int = 8,
        complexity_threshold: int = 2,
    ) -> None:
        self._max_steps = int(max_steps)
        self._complexity_threshold = int(complexity_threshold)

    def needs_planning(self, request: str) -> bool:
        """判断请求是否需要多步规划（vs 单轮直接回复）。"""
        text = str(request or "").strip()
        if len(text) < 15:
            return False

        score = 0
        text_lower = text.lower()

        # 复杂度指标
        for indicator in _COMPLEX_INDICATORS:
            if indicator in text_lower:
                score += 1

        # 多部分请求
        for word in _MULTI_PART_PATTERN_WORDS:
            if word in text_lower:
                score += 1

        # 长度
        if len(text) > 80:
            score += 1

        # 多个问号/句号
        if text.count("？") + text.count("?") > 1:
            score += 1
        if text.count("。") + text.count(".") > 2:
            score += 1

        return score >= self._complexity_threshold

    def create_plan(
        self,
        *,
        task_id: str,
        request: str,
        available_skills: list[str] | None = None,
    ) -> TaskPlan:
        """将复杂请求分解为执行步骤。"""
        text = str(request or "").strip()
        steps: list[PlanStep] = []

        # 1. 理解阶段（Reason）
        steps.append(PlanStep(
            step_id=1,
            description="理解用户意图，识别核心需求和约束条件",
            action_type="reason",
        ))

        # 2. 分析子任务
        sub_tasks = self._decompose_request(text)

        # 3. 为每个子任务生成步骤
        for i, sub_task in enumerate(sub_tasks[:self._max_steps - 2]):
            step_id = i + 2
            # 判断是否需要追问
            if sub_task.get("needs_info"):
                steps.append(PlanStep(
                    step_id=step_id,
                    description=f"向用户确认：{sub_task['question']}",
                    action_type="ask",
                    depends_on=[1],
                ))
            else:
                steps.append(PlanStep(
                    step_id=step_id,
                    description=sub_task["description"],
                    action_type="act",
                    skill_name=sub_task.get("skill"),
                    depends_on=[step_id - 1] if step_id > 2 else [1],
                ))

        # 4. 综合阶段（Synthesize）
        steps.append(PlanStep(
            step_id=len(steps) + 1,
            description="综合所有结果，生成最终回复",
            action_type="synthesize",
            depends_on=[s.step_id for s in steps[1:]],
        ))

        plan = TaskPlan(
            task_id=task_id,
            original_request=text,
            steps=steps,
            created_ts_ms=int(time.time() * 1000),
            status="planning",
        )

        _logger.info(
            "PlanningAgent: created plan %s with %d steps for: %s",
            task_id, len(steps), text[:60],
        )
        return plan

    def advance_step(
        self,
        plan: TaskPlan,
        *,
        step_result: str = "",
        user_input: str = "",
    ) -> PlanStep | None:
        """推进计划到下一步。返回下一个待执行步骤，或 None（已完成）。"""
        if plan.current_step > 0 and plan.current_step <= len(plan.steps):
            current = plan.steps[plan.current_step - 1]
            current.status = StepStatus.COMPLETED
            current.result = step_result

        # 找下一个 pending 步骤
        for step in plan.steps:
            if step.status == StepStatus.PENDING:
                # 检查依赖
                deps_met = all(
                    plan.steps[d - 1].status == StepStatus.COMPLETED
                    for d in step.depends_on
                    if 0 < d <= len(plan.steps)
                )
                if deps_met:
                    step.status = StepStatus.IN_PROGRESS
                    plan.current_step = step.step_id
                    plan.status = "executing"
                    return step

        # 所有步骤完成
        plan.status = "completed"
        return None

    def _decompose_request(self, text: str) -> list[dict[str, Any]]:
        """将请求分解为子任务。"""
        sub_tasks: list[dict[str, Any]] = []

        # 按"和""并且""然后"等连接词拆分
        parts: list[str] = [text]
        for sep in ("，然后", "，再", "，并且", "，同时", "，还要", "，另外", "和"):
            new_parts: list[str] = []
            for part in parts:
                new_parts.extend(part.split(sep))
            parts = new_parts

        for part in parts:
            part = part.strip()
            if not part or len(part) < 3:
                continue
            # 检查是否缺少关键信息
            needs_info = any(
                vague in part
                for vague in ("什么样的", "哪种", "具体", "偏好", "类型")
            )
            sub_tasks.append({
                "description": part[:100],
                "needs_info": needs_info,
                "question": f"关于「{part[:30]}」，你有什么具体偏好吗？" if needs_info else "",
                "skill": None,
            })

        return sub_tasks if sub_tasks else [{"description": text[:100], "needs_info": False}]
