"""MilestoneTracker：关系里程碑检测与记录——Phase V。

职责：
  1. 检测关系发展中的重要时刻（初次深度对话、关系天数、情感支持等）
  2. 记录已达成的里程碑
  3. 输出里程碑提示（供 BondNarrativeLayer 使用）

里程碑类型：
  - FIRST_DEEP_TALK: 初次深度对话
  - RELATIONSHIP_DAYS: 关系天数（7/30/100/365 天）
  - FIRST_EMOTIONAL_SUPPORT: 首次情感支持
  - FIRST_CONFLICT_RESOLVED: 首次冲突修复
  - STAGE_PROMOTED: 阶段晋级
  - LATE_NIGHT_COMPANION: 深夜陪伴（23:00-02:00）

设计原则：
  - 里程碑只检测一次，不重复触发
  - 所有里程碑均有自然语言叙事模板
  - 与 BondStageEngine 解耦，通过事件通知触发
"""

from __future__ import annotations

import logging
from host_brain.core.ids import now_ms as _now_ms
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("Natsume.milestone_tracker")


# ---------------------------------------------------------------------------
# 里程碑类型枚举
# ---------------------------------------------------------------------------

MILESTONE_FIRST_DEEP_TALK = "FIRST_DEEP_TALK"
MILESTONE_RELATIONSHIP_DAYS_7 = "RELATIONSHIP_DAYS_7"
MILESTONE_RELATIONSHIP_DAYS_30 = "RELATIONSHIP_DAYS_30"
MILESTONE_RELATIONSHIP_DAYS_100 = "RELATIONSHIP_DAYS_100"
MILESTONE_RELATIONSHIP_DAYS_365 = "RELATIONSHIP_DAYS_365"
MILESTONE_FIRST_EMOTIONAL_SUPPORT = "FIRST_EMOTIONAL_SUPPORT"
MILESTONE_FIRST_CONFLICT_RESOLVED = "FIRST_CONFLICT_RESOLVED"
MILESTONE_STAGE_PROMOTED = "STAGE_PROMOTED"
MILESTONE_LATE_NIGHT_COMPANION = "LATE_NIGHT_COMPANION"

# 关系天数里程碑配置
RELATIONSHIP_DAY_MILESTONES: dict[str, int] = {
    MILESTONE_RELATIONSHIP_DAYS_7: 7,
    MILESTONE_RELATIONSHIP_DAYS_30: 30,
    MILESTONE_RELATIONSHIP_DAYS_100: 100,
    MILESTONE_RELATIONSHIP_DAYS_365: 365,
}

# 叙事模板（中文）
NARRATIVE_TEMPLATES: dict[str, str] = {
    MILESTONE_FIRST_DEEP_TALK: "第一次和你聊到那么深入的话题",
    MILESTONE_RELATIONSHIP_DAYS_7: "认识你已经一个星期了",
    MILESTONE_RELATIONSHIP_DAYS_30: "认识你已经一个月了",
    MILESTONE_RELATIONSHIP_DAYS_100: "认识你已经一百天了",
    MILESTONE_RELATIONSHIP_DAYS_365: "认识你已经一年了",
    MILESTONE_FIRST_EMOTIONAL_SUPPORT: "第一次被你安慰的时候",
    MILESTONE_FIRST_CONFLICT_RESOLVED: "那次吵架之后反而更了解彼此了",
    MILESTONE_STAGE_PROMOTED: "感觉我们更亲近了",
    MILESTONE_LATE_NIGHT_COMPANION: "第一次陪你到很晚",
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MilestoneRecord:
    """单条里程碑记录。"""
    milestone_type: str
    narrative: str     # 自然语言叙事
    ts_ms: int         # 达成时间戳
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "milestone_type": self.milestone_type,
            "narrative": self.narrative,
            "ts_ms": self.ts_ms,
            "metadata": dict(self.metadata),
        }


# ---------------------------------------------------------------------------
# MilestoneTracker
# ---------------------------------------------------------------------------

class MilestoneTracker:
    """关系里程碑检测与记录。

    用法：
        tracker = MilestoneTracker()
        milestones = tracker.on_event(
            user_id="user_1",
            event_type="INTERACTION_DEEP",
            ts_ms=now_ms,
        )
    """

    def __init__(
        self,
        *,
        narrative_templates: dict[str, str] | None = None,
    ) -> None:
        self._templates = dict(narrative_templates or NARRATIVE_TEMPLATES)
        # user_id → 已达成的里程碑集合
        self._achieved: dict[str, set[str]] = {}
        # user_id → 里程碑记录列表
        self._records: dict[str, list[MilestoneRecord]] = {}
        # user_id → 首次互动时间戳
        self._first_interaction_ts: dict[str, int] = {}

    # ── 事件触发 ──────────────────────────────────────────────────────────

    def on_event(
        self,
        *,
        user_id: str,
        event_type: str,
        ts_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> list[MilestoneRecord]:
        """处理 Bond 事件，检测并记录新达成的里程碑。

        Args:
            user_id: 用户 ID
            event_type: 事件类型（INTERACTION_DEEP / EMOTIONAL_SUPPORT / 等）
            ts_ms: 时间戳
            metadata: 额外信息（如 from_stage / to_stage）

        Returns:
            新达成的里程碑列表
        """
        uid = str(user_id).strip()
        ts = int(ts_ms) if ts_ms else _now_ms()
        meta = dict(metadata or {})
        new_milestones: list[MilestoneRecord] = []

        # 记录首次互动时间
        if uid not in self._first_interaction_ts:
            self._first_interaction_ts[uid] = ts

        # 检测各类里程碑
        event_upper = str(event_type).strip().upper()

        # 1. 初次深度对话
        if event_upper in ("INTERACTION_DEEP", "DEEP_TALK"):
            ms = self._try_achieve(uid, MILESTONE_FIRST_DEEP_TALK, ts, meta)
            if ms:
                new_milestones.append(ms)

        # 2. 首次情感支持
        if event_upper == "EMOTIONAL_SUPPORT":
            ms = self._try_achieve(uid, MILESTONE_FIRST_EMOTIONAL_SUPPORT, ts, meta)
            if ms:
                new_milestones.append(ms)

        # 3. 首次冲突修复
        if event_upper == "CONFLICT_RESOLVED":
            ms = self._try_achieve(uid, MILESTONE_FIRST_CONFLICT_RESOLVED, ts, meta)
            if ms:
                new_milestones.append(ms)

        # 4. 阶段晋级
        if event_upper in ("STAGE_PROMOTED", "STAGE_TRANSITION"):
            from_stage = meta.get("from_stage", "")
            to_stage = meta.get("to_stage", "")
            key = f"{MILESTONE_STAGE_PROMOTED}:{from_stage}→{to_stage}"
            ms = self._try_achieve(uid, key, ts, meta)
            if ms:
                new_milestones.append(ms)

        # 5. 深夜陪伴（23:00-02:00）
        if self._is_late_night(ts):
            ms = self._try_achieve(uid, MILESTONE_LATE_NIGHT_COMPANION, ts, meta)
            if ms:
                new_milestones.append(ms)

        # 6. 关系天数
        day_milestones = self._check_relationship_days(uid, ts)
        new_milestones.extend(day_milestones)

        return new_milestones

    # ── 查询接口 ──────────────────────────────────────────────────────────

    def get_milestones(self, user_id: str) -> list[MilestoneRecord]:
        """获取用户所有已达成的里程碑。"""
        return list(self._records.get(str(user_id).strip(), []))

    def get_recent_milestones(
        self, user_id: str, *, limit: int = 3,
    ) -> list[MilestoneRecord]:
        """获取用户最近达成的里程碑。"""
        records = self._records.get(str(user_id).strip(), [])
        return list(records[-limit:])

    def get_milestone_count(self, user_id: str) -> int:
        """获取用户里程碑总数。"""
        return len(self._records.get(str(user_id).strip(), []))

    def has_achieved(self, user_id: str, milestone_type: str) -> bool:
        """检查用户是否已达成指定里程碑。"""
        return milestone_type in self._achieved.get(str(user_id).strip(), set())

    def get_milestone_hints(
        self, user_id: str, *, limit: int = 3,
    ) -> list[str]:
        """获取用户最近里程碑的叙事提示（用于 prompt 注入）。"""
        recent = self.get_recent_milestones(user_id, limit=limit)
        return [m.narrative for m in recent]

    # ── 设定首次互动时间（用于关系天数计算） ─────────────────────────────

    def set_first_interaction_ts(self, user_id: str, ts_ms: int) -> None:
        """手动设定首次互动时间（用于初始化）。"""
        self._first_interaction_ts[str(user_id).strip()] = int(ts_ms)

    # ── 内部方法 ──────────────────────────────────────────────────────────

    def _try_achieve(
        self,
        user_id: str,
        milestone_type: str,
        ts_ms: int,
        metadata: dict[str, Any],
    ) -> MilestoneRecord | None:
        """尝试达成里程碑（仅首次有效）。"""
        if user_id not in self._achieved:
            self._achieved[user_id] = set()
        if milestone_type in self._achieved[user_id]:
            return None

        # 查找叙事模板
        base_type = milestone_type.split(":")[0]
        narrative = self._templates.get(
            milestone_type,
            self._templates.get(base_type, f"达成里程碑：{milestone_type}"),
        )

        record = MilestoneRecord(
            milestone_type=milestone_type,
            narrative=narrative,
            ts_ms=ts_ms,
            metadata=metadata,
        )

        self._achieved[user_id].add(milestone_type)
        if user_id not in self._records:
            self._records[user_id] = []
        self._records[user_id].append(record)

        _logger.info(
            "里程碑达成: user=%s type=%s narrative=%s",
            user_id, milestone_type, narrative,
        )
        return record

    def _check_relationship_days(
        self, user_id: str, current_ts_ms: int,
    ) -> list[MilestoneRecord]:
        """检测关系天数里程碑。"""
        first_ts = self._first_interaction_ts.get(user_id)
        if first_ts is None or first_ts <= 0:
            return []

        elapsed_days = (current_ts_ms - first_ts) / (1000 * 60 * 60 * 24)
        new_milestones: list[MilestoneRecord] = []

        for milestone_type, required_days in RELATIONSHIP_DAY_MILESTONES.items():
            if elapsed_days >= required_days:
                ms = self._try_achieve(user_id, milestone_type, current_ts_ms, {
                    "elapsed_days": int(elapsed_days),
                    "required_days": required_days,
                })
                if ms:
                    new_milestones.append(ms)

        return new_milestones

    @staticmethod
    def _is_late_night(ts_ms: int) -> bool:
        """判断时间戳是否在 23:00-02:00 之间。"""
        try:
            import datetime
            dt = datetime.datetime.fromtimestamp(ts_ms / 1000)
            hour = dt.hour
            return hour >= 23 or hour < 2
        except Exception:
            return False
