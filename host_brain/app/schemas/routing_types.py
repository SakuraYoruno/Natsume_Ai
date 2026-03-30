"""
routing_types.py —— 模型路由相关枚举与数据结构

定义 LlmGateway 路由决策所需的所有类型约束。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .memory_types import RiskLevel


# ---------------------------------------------------------------------------
# 枚举：任务类型
# ---------------------------------------------------------------------------

class TaskType(str, Enum):
    """请求的任务类型，决定路由目标和生成参数。"""
    CHAT = "chat"
    """普通聊天对话"""
    REASONING = "reasoning"
    """复杂推理任务"""
    CODE = "code"
    """代码辅助"""
    TRANSLATE = "translate"
    """翻译任务"""
    DREAM = "dream"
    """梦境生成（NARRATIVE_ONLY，独立路由）"""
    DIARY = "diary"
    """日记生成（独立路由）"""


# ---------------------------------------------------------------------------
# 枚举：延迟预算
# ---------------------------------------------------------------------------

class LatencyBudget(str, Enum):
    """请求的最大可接受延迟预算。"""
    FAST = "<2s"
    """实时响应，优先本地模型"""
    NORMAL = "<5s"
    """普通响应"""
    SLOW = "<30s"
    """允许较长等待，适合复杂推理"""


# ---------------------------------------------------------------------------
# 枚举：模型目标标识
# ---------------------------------------------------------------------------

class ModelTarget(str, Enum):
    """可路由的模型目标。"""
    HOST_14B = "host_14b"
    """本地 Ollama 14B 模型（qwen3:14b）"""
    CLOUD = "cloud"
    """云端 API（Gemini Primary）"""
    CLOUD_CN = "cloud_cn"
    """国内云端 API（DeepSeek，低延迟，Gaming 场景首选）"""
    SKILLS = "skills"
    """专用 Skills 路由（dream/diary）"""


# ---------------------------------------------------------------------------
# 数据类：路由决策记录
# ---------------------------------------------------------------------------

@dataclass
class RouteDecision:
    """LlmGateway 的路由决策记录，写入 llm_route_log。"""
    task_type: TaskType
    """任务类型"""
    selected_model: ModelTarget
    """最终选定的模型"""
    latency_budget: LatencyBudget = LatencyBudget.NORMAL
    """延迟预算"""
    risk_level: RiskLevel = RiskLevel.LOW
    """风险等级"""
    context_length: int = 0
    """上下文 token 数"""
    requires_structured_output: bool = False
    """是否需要结构化输出"""
    is_fallback: bool = False
    """是否为降级路由"""
    fallback_reason: Optional[str] = None
    """降级原因（is_fallback=True 时必填）"""
    original_model: Optional[ModelTarget] = None
    """降级前的原始目标模型"""
    created_at: datetime = field(default_factory=datetime.utcnow)
    """决策时间戳"""
