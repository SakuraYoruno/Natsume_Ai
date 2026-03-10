"""
memory_types.py —— 记忆系统核心枚举与数据结构

定义记忆写入宪法所需的所有类型约束。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


# ---------------------------------------------------------------------------
# 枚举：Canon 提案类型
# ---------------------------------------------------------------------------

class ProposalType(str, Enum):
    """Canon 提案的内容类型，限定写入 WorldCanon 的数据分类。"""
    FACT_CANDIDATE = "FACT_CANDIDATE"
    """可验证的客观事实候选"""
    PREFERENCE_CANDIDATE = "PREFERENCE_CANDIDATE"
    """用户偏好候选"""
    STYLE_CANDIDATE = "STYLE_CANDIDATE"
    """风格/语气候选"""
    RELATION_CANDIDATE = "RELATION_CANDIDATE"
    """关系图谱候选"""
    NARRATIVE_ONLY = "NARRATIVE_ONLY"
    """仅叙事内容，禁止写入 Canon 或 Bond 积分"""


# ---------------------------------------------------------------------------
# 枚举：提案发起方类别
# ---------------------------------------------------------------------------

class ProposerClass(str, Enum):
    """区分请求/提案的发起方，用于权限分级。"""
    USER = "USER"
    """由真实用户交互触发"""
    SCHEDULER = "SCHEDULER"
    """由 NatsumeScheduler 自动触发，受更严格限制"""


# ---------------------------------------------------------------------------
# 枚举：风险等级
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    """操作/工具/路由的风险等级。"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ---------------------------------------------------------------------------
# 枚举：证据可信度等级
# ---------------------------------------------------------------------------

class EvidenceLevel(str, Enum):
    """记忆内容的证据可信度，影响蒸馏资格。"""
    VERIFIED = "VERIFIED"
    """已验证的事实"""
    CANDIDATE = "CANDIDATE"
    """候选，需要频次或里程碑确认"""
    MILESTONE = "MILESTONE"
    """用户明确标记的里程碑事件，可直接蒸馏"""
    NARRATIVE_ONLY = "NARRATIVE_ONLY"
    """纯叙事/梦境/假设内容，禁止蒸馏为语义记忆"""


# ---------------------------------------------------------------------------
# 数据类：情节记忆记录
# ---------------------------------------------------------------------------

@dataclass
class EpisodicRecord:
    """写入 EpisodicStore 的单条情节记忆记录。"""
    session_id: str
    """所属会话 ID（情绪标签以 session 粒度聚合）"""
    content: str
    """记忆内容文本"""
    evidence_level: EvidenceLevel = EvidenceLevel.CANDIDATE
    """证据可信度等级"""
    emotion_tags: List[str] = field(default_factory=list)
    """情绪标签列表（session 粒度聚合，不以单轮覆盖）"""
    fact_tags: List[str] = field(default_factory=list)
    """事实标签列表"""
    preference_tags: List[str] = field(default_factory=list)
    """偏好标签列表"""
    turn_count: int = 0
    """本 session 的对话轮次数"""
    created_at: datetime = field(default_factory=datetime.utcnow)
    source_hash: Optional[str] = None
    """内容哈希，用于幂等保护"""

    def has_qualifying_tags(self) -> bool:
        """判断是否满足写入 EpisodicStore 的标签条件。"""
        return bool(self.emotion_tags or self.fact_tags or self.preference_tags)

    def is_eligible_for_write(self) -> bool:
        """判断是否满足写入 EpisodicStore 的完整条件（轮次 ≥ 2 且有标签）。"""
        return self.turn_count >= 2 and self.has_qualifying_tags()


# ---------------------------------------------------------------------------
# 数据类：Canon 提案
# ---------------------------------------------------------------------------

@dataclass
class CanonProposal:
    """DiaryWriter / LongTermDistiller 提交的 Canon 写入提案。"""
    proposal_type: ProposalType
    """提案类型，必填"""
    content: str
    """提案内容"""
    source_hash: str
    """内容哈希，用于幂等保护（相同 hash 拒绝重复写入）"""
    proposer_class: ProposerClass = ProposerClass.USER
    """发起方类别"""
    evidence_level: EvidenceLevel = EvidenceLevel.CANDIDATE
    """证据可信度"""
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)
    """附加元数据"""
