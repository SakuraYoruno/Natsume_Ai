"""Constants, dataclasses, and compiled regex for the executive runtime.

This module owns all shared pattern tuples, compiled regex, status sets,
and the _RouteSelection / _ExecutionOutcome dataclasses that the rest
of the executive/ package depends on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---- Pattern tuples (message-intent markers) ----

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

_SIMPLE_TECH_ADVICE_TERMS = (
    "报错", "错误", "异常", "失败", "缺包", "导入", "端口", "日志", "启动",
    "连接不上", "连不上", "没响应", "无响应", "排查", "怎么查", "怎么排查",
    "该怎么", "怎么办", "如何", "先看", "看什么",
    "module not found", "modulenotfounderror", "no module named",
    "openai_api_key", "环境变量", "neo4j", "bolt:/", "chroma", "chromadb",
    "http:/", "https:/",
    "why", "error", "port", "timeout",
)

_SIMPLE_TECH_ACTION_BLOCKERS = (
    "开始处理", "开始做", "继续做", "直接改", "帮我改",
    "修改", "修复", "实现", "重构", "删除", "清理", "整理仓库",
    "提交", "跑测试", "执行", "接入", "迁移", "写代码",
)

# ---- Compiled regex ----

_ACTION_SPLIT_RE = re.compile(r"(?:然后|并且|同时|接着|之后|再把|再去|再来)")
_SIMPLE_TASK_SPLIT_RE = re.compile(
    r"(?:下一步|接下来|后面|任务).{0,12}(?:拆成|分成|列成).{0,8}(?:三|3)件",
    re.IGNORECASE,
)

# ---- Status / code sets ----

_SKILL_EXEC_FAIL_STATUS = {"failed", "rejected", "degraded", "error"}
_SKILL_EXEC_FAIL_CODES = {
    "SCHED_NO_CONTENT", "SKILL_EXECUTION_FAILED",
    "SKILL_EXECUTION_ERROR", "MEMORY_WRITER_UNAVAILABLE",
    "MEMORY_STORE_UNAVAILABLE",
}
_SKILL_EXEC_NO_EFFECT_MARKERS = (
    "no memory writer available", "no memory store available",
    "no memory backend available", "skipping diary",
    "no conversations on",
)

# ---- Dataclasses ----

@dataclass(frozen=True)
class _RouteSelection:
    """Routing decision produced by _hard_rule_route / _should_force_complex_plan."""

    route_type: str
    target_id: str
    reason_code: str


@dataclass(frozen=True)
class _ExecutionOutcome:
    """Result of executing a skill, sub-agent, or DAG node."""

    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict[str, Any]
