"""任务规划代理（Phase XI 方案 4）。

职责：对复杂请求做 ReAct 式任务分解——Reasoning + Acting 交替。
设计：先规划步骤，再逐步执行，中间可追问用户。

第一层判断：RULE_ROUTE_MAP 规则映射表（精确匹配，无歧义词）
第二层判断：LLM 节点（由 executive_runtime_core 调用）
规划执行：LangGraph 父子图（plan_graph.py）
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
    step_results: list[dict[str, Any]] = field(default_factory=list)
    executed_skills: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "needs_user_input": self.needs_user_input,
            "pending_question": self.pending_question[:200] if self.pending_question else "",
            "elapsed_ms": round(self.elapsed_ms, 1),
            "step_results": list(self.step_results[:6]),
            "executed_skills": list(self.executed_skills[:8]),
        }

# ---------------------------------------------------------------------------
# 第一层：规则映射表（精确匹配，无歧义词）
# 只收录语义明确的多步/复杂任务指示词，不收录"帮我"/"需要"等泛化词
# ---------------------------------------------------------------------------

# 明确的多步连接词（用于 _decompose_request 切分）
# 注意：不包含"和"——"和"在"A和B的差异"中是连接词而非分隔符，会导致误切
_MULTI_STEP_SEPARATORS: tuple[str, ...] = (
    "，然后", "，再", "，并且", "，同时", "，还要", "，另外",
    "；然后", "；再", "；并且", "；同时",
    "接着", "之后再", "完成后",
)

# 明确的复杂任务指示词（语义无歧义）
_COMPLEX_INDICATORS = frozenset({
    # 规划类
    "计划", "安排", "规划", "步骤", "流程",
    # 顺序类（明确多步）
    "首先", "然后", "接着", "最后", "第一步", "第二步",
    # 分析对比类
    "对比", "比较", "分析", "评估", "调研", "总结并",
    # 明确多任务
    "同时做", "并行", "一边.*一边",
})

# 明确的多部分连接词（不含"和"）
_MULTI_PART_PATTERN_WORDS = frozenset({
    "并且", "同时", "另外", "还有", "以及", "还需要", "还要",
})

# ---------------------------------------------------------------------------
# 第一层规则路由映射表（供 executive_runtime_core 直接使用）
# 格式：{关键词: (route_type, target_id_hint)}
# 命中则直接路由，无需进入 LLM 判断
# ---------------------------------------------------------------------------
RULE_ROUTE_MAP: dict[str, tuple[str, str]] = {
    # ── 情绪/心情 → emotion_agent ──────────────────────────────────────────
    "情绪": ("delegate", "emotion_agent"),
    "心情": ("delegate", "emotion_agent"),
    "感受": ("delegate", "emotion_agent"),
    "开心": ("delegate", "emotion_agent"),
    "难过": ("delegate", "emotion_agent"),
    "伤心": ("delegate", "emotion_agent"),
    "焦虑": ("delegate", "emotion_agent"),
    "压力": ("delegate", "emotion_agent"),
    "烦躁": ("delegate", "emotion_agent"),
    "孤独": ("delegate", "emotion_agent"),
    "委屈": ("delegate", "emotion_agent"),
    # ── 关系/亲密 → bond_query skill ──────────────────────────────────────
    "关系阶段": ("skill", "bond_query"),
    "亲密度": ("skill", "bond_query"),
    "好感度": ("skill", "bond_query"),
    "关系进展": ("skill", "bond_query"),
    "我们的关系": ("skill", "bond_query"),
    # ── 记忆/回忆 → memory_recall skill ───────────────────────────────────
    "回忆一下": ("skill", "memory_recall"),
    "记得吗": ("skill", "memory_recall"),
    "你还记得": ("skill", "memory_recall"),
    "之前说过": ("skill", "memory_recall"),
    "上次提到": ("skill", "memory_recall"),
    "我说过": ("skill", "memory_recall"),
    # ── 状态/运行 → read_only_status skill ────────────────────────────────
    "运行状态": ("skill", "read_only_status"),
    "系统状态": ("skill", "read_only_status"),
    "当前状态": ("skill", "read_only_status"),
    "健康状态": ("skill", "read_only_status"),
    # ── 总结/摘要 → session_summary skill ─────────────────────────────────
    "总结一下": ("skill", "session_summary"),
    "回顾一下": ("skill", "session_summary"),
    "今天聊了": ("skill", "session_summary"),
    "这次对话": ("skill", "session_summary"),
    "帮我整理": ("skill", "session_summary"),
    # ── 翻译 → translate_agent ────────────────────────────────────────────
    "翻译成": ("delegate", "translate_agent"),
    "翻译一下": ("delegate", "translate_agent"),
    "用英文说": ("delegate", "translate_agent"),
    "用中文说": ("delegate", "translate_agent"),
    "translate to": ("delegate", "translate_agent"),
    "how do you say": ("delegate", "translate_agent"),
    # ── 代码 → code_agent ─────────────────────────────────────────────────
    "帮我写代码": ("delegate", "code_agent"),
    "帮我写一段": ("delegate", "code_agent"),
    "帮我实现": ("delegate", "code_agent"),
    "写个函数": ("delegate", "code_agent"),
    "写个脚本": ("delegate", "code_agent"),
    "debug": ("delegate", "code_agent"),
    "报错": ("delegate", "code_agent"),
    "代码报错": ("delegate", "code_agent"),
    "怎么修复这个": ("delegate", "code_agent"),
    "语法错误": ("delegate", "code_agent"),
    # ── 任务进度查询 → task_progress（内置路由，无需 target_id）─────────────
    "任务进度": ("task_progress", ""),
    "执行进度": ("task_progress", ""),
    "做到哪了": ("task_progress", ""),
    "完成了吗": ("task_progress", ""),
}


# ---------------------------------------------------------------------------
# PlanningAgent
# ---------------------------------------------------------------------------
class PlanningAgent:
    """ReAct 式任务规划代理。

    判断流程（两层）：
    1. 第一层：RULE_ROUTE_MAP 精确规则匹配 → 命中则直接路由（由 executive_runtime_core 调用）
    2. 第二层：needs_planning() 评分 + LLM 节点判断 → 判定为多步则进入 LangGraph 父子图

    规划执行：
    - 使用 LangGraph 父子图（app/plan_graph.py）
    - 父图：plan_decompose_node（LLM 拆解）→ Send API 并行子图 → synthesize_node
    - 子图：每个子任务独立的 task_exec_subgraph
    """

    def __init__(
        self,
        *,
        max_steps: int = 8,
        complexity_threshold: int = 2,
    ) -> None:
        self._max_steps = int(max_steps)
        self._complexity_threshold = int(complexity_threshold)

    @staticmethod
    def rule_route(request: str) -> tuple[str, str] | None:
        """第一层：规则映射表快速判断。

        Returns:
            (route_type, target_id) 若命中规则；None 若未命中（进入第二层 LLM 判断）
        """
        text = str(request or "").strip().lower()
        for keyword, route_info in RULE_ROUTE_MAP.items():
            if keyword.lower() in text:
                return route_info
        return None

    def needs_planning(self, request: str) -> bool:
        """第二层：判断请求是否需要多步规划（vs 单轮直接回复）。

        注意：此方法只在第一层规则未命中时调用。
        评分指标已收紧，移除了"帮我"/"需要"等泛化词，避免误触发。
        """
        text = str(request or "").strip()
        if len(text) < 15:
            return False

        score = 0
        text_lower = text.lower()

        # 明确的复杂度指标（无歧义词）
        for indicator in _COMPLEX_INDICATORS:
            if indicator in text_lower:
                score += 1

        # 明确的多部分连接词（不含"和"）
        for word in _MULTI_PART_PATTERN_WORDS:
            if word in text_lower:
                score += 1

        # 长度（适当提高阈值，减少误触发）
        if len(text) > 120:
            score += 1

        # 多个问号（真正的多问题请求）
        if text.count("？") + text.count("?") > 1:
            score += 1

        # 多个句号（多个独立句子）
        if text.count("。") + text.count(".") > 3:
            score += 1

        return score >= self._complexity_threshold

    def create_plan(
        self,
        *,
        task_id: str,
        request: str,
        available_skills: list[str] | None = None,
    ) -> TaskPlan:
        """将复杂请求分解为执行步骤。

        步骤结构：
        1. reason（固定）：理解意图
        2~N. act：每个子任务
        N+1. synthesize（固定）：综合所有结果
        """
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

        # 顺序依赖指示词：含这些词的子任务说明它依赖前一步的结果，不能并行
        _SEQUENTIAL_INDICATORS = frozenset({
            "然后", "再", "接着", "之后", "完成后", "基于此", "在此基础上",
            "根据以上", "根据上面", "根据结果", "接下来", "随后", "继续",
        })

        # 3. 为每个子任务生成步骤
        prev_act_step_id: int | None = None  # 记录上一个 act 步骤的 step_id
        for i, sub_task in enumerate(sub_tasks[:self._max_steps - 2]):
            step_id = i + 2
            selected_skill = self._select_skill_name(
                sub_task["description"],
                available_skills=available_skills,
            )
            # 判断是否需要追问
            if sub_task.get("needs_info"):
                steps.append(PlanStep(
                    step_id=step_id,
                    description=f"向用户确认：{sub_task['question']}",
                    action_type="ask",
                    depends_on=[1],
                ))
            else:
                desc_lower = sub_task["description"].lower()
                # 顺序依赖推断：
                # - 第一个 act 步骤始终依赖 reason（step_id=1）
                # - 后续 act 步骤：若描述含顺序词，则依赖前一个 act 步骤（串行）
                #                  否则只依赖 reason（允许并行）
                if step_id == 2:
                    depends_on = [1]
                elif prev_act_step_id is not None and any(
                    word in desc_lower for word in _SEQUENTIAL_INDICATORS
                ):
                    depends_on = [prev_act_step_id]
                else:
                    depends_on = [1]

                steps.append(PlanStep(
                    step_id=step_id,
                    description=sub_task["description"],
                    action_type="act",
                    skill_name=selected_skill or sub_task.get("skill"),
                    depends_on=depends_on,
                ))
                prev_act_step_id = step_id

        # 4. 综合阶段（Synthesize）—— 保留，不再被过滤掉
        synthesize_step = PlanStep(
            step_id=len(steps) + 1,
            description="综合所有执行结果，生成完整的最终回复",
            action_type="synthesize",
            depends_on=[s.step_id for s in steps if s.action_type == "act"],
        )
        steps.append(synthesize_step)

        plan = TaskPlan(
            task_id=task_id,
            original_request=text,
            steps=steps,
            created_ts_ms=int(time.time() * 1000),
            status="planning",
        )

        _logger.info(
            "PlanningAgent: created plan %s with %d steps (act=%d) for: %s",
            task_id, len(steps),
            sum(1 for s in steps if s.action_type == "act"),
            text[:60],
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
        """将请求分解为子任务。

        只使用语义明确的多步连接词切分，不使用"和"（会误切"A和B的差异"）。
        """
        sub_tasks: list[dict[str, Any]] = []

        # 只按明确的多步连接词拆分，不含"和"
        parts: list[str] = [text]
        for sep in _MULTI_STEP_SEPARATORS:
            new_parts: list[str] = []
            for part in parts:
                new_parts.extend(part.split(sep))
            parts = new_parts

        # 语义保护：如果切分后某段太短（< 5字），可能是误切，合并回去
        merged_parts: list[str] = []
        buffer = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(part) < 5 and buffer:
                # 太短，合并到前一段
                buffer = buffer + part
            else:
                if buffer:
                    merged_parts.append(buffer)
                buffer = part
        if buffer:
            merged_parts.append(buffer)

        for part in merged_parts:
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

    @staticmethod
    def _select_skill_name(
        description: str,
        *,
        available_skills: list[str] | None = None,
    ) -> str | None:
        """从 available_skills 中匹配最合适的 skill。

        优先从 skill_registry 动态传入的 available_skills 中匹配，
        不再依赖硬编码关键词映射表。
        """
        available = {
            str(name or "").strip().lower(): str(name or "").strip()
            for name in list(available_skills or [])
            if str(name or "").strip()
        }
        if not available:
            return None

        text = str(description or "").strip().lower()

        # 直接名称匹配（优先）
        for skill_lower, skill_orig in available.items():
            if skill_lower in text or text in skill_lower:
                return skill_orig

        # 语义关键词匹配（从 RULE_ROUTE_MAP 派生，保持一致性）
        for keyword, (route_type, target_id) in RULE_ROUTE_MAP.items():
            if route_type == "skill" and keyword.lower() in text:
                resolved = available.get(target_id.lower())
                if resolved:
                    return resolved

        return None

    # ------------------------------------------------------------------
    # execute_plan() 已废弃（Deprecated）
    # 实际执行由 LangGraph 父子图（app/plan_graph.py）负责
    # 保留此方法仅为向后兼容，不应在新代码中调用
    # ------------------------------------------------------------------
    def execute_plan(
        self,
        plan: TaskPlan,
        *,
        skill_runners: dict[str, Callable[..., Any]] | None = None,
        base_context: dict[str, Any] | None = None,
    ) -> PlanExecutionResult:
        """[已废弃] 同步执行计划。

        此方法已被 LangGraph 父子图（app/plan_graph.py）替代，
        不应在新代码中调用。保留仅为向后兼容。
        """
        _logger.warning(
            "PlanningAgent.execute_plan() 已废弃，请使用 app/plan_graph.py 中的 "
            "LangGraph 父子图执行规划。task_id=%s",
            plan.task_id,
        )
        # 返回空结果，不执行任何逻辑
        return PlanExecutionResult(
            task_id=plan.task_id,
            success=False,
            steps_completed=0,
            steps_total=len(plan.steps),
            final_response="execute_plan_deprecated",
            elapsed_ms=0.0,
        )
