"""Bond 阶段积分计算 + 自动晋级引擎（M13-A）。

DevSpec §25：Bond 阶段自动晋级。

阶段定义（从低到高）：
  STRANGER → ACQUAINTANCE → FRIEND → CLOSE_FRIEND → CONFIDANT → SOULMATE

积分规则：
  - 每次 Bond 事件按 update_kind 贡献积分（可配置）
  - 积分累积到阈值时触发晋级
  - 晋级写入 bond_stage_transition_log（审计链）
  - 降级：长期无互动触发衰减（可配置）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import new_request_id, now_ms

_logger = logging.getLogger("Natsume.bond_stage_engine")

# ---------------------------------------------------------------------------
# 阶段枚举（有序）
# ---------------------------------------------------------------------------

STAGE_STRANGER = "STRANGER"
STAGE_ACQUAINTANCE = "ACQUAINTANCE"
STAGE_FRIEND = "FRIEND"
STAGE_CLOSE_FRIEND = "CLOSE_FRIEND"
STAGE_CONFIDANT = "CONFIDANT"
STAGE_SOULMATE = "SOULMATE"

STAGE_ORDER: list[str] = [
    STAGE_STRANGER,
    STAGE_ACQUAINTANCE,
    STAGE_FRIEND,
    STAGE_CLOSE_FRIEND,
    STAGE_CONFIDANT,
    STAGE_SOULMATE,
]

# 晋级所需累计积分阈值（到达该阶段需要的总积分）
DEFAULT_STAGE_THRESHOLDS: dict[str, int] = {
    STAGE_STRANGER: 0,
    STAGE_ACQUAINTANCE: 10,
    STAGE_FRIEND: 30,
    STAGE_CLOSE_FRIEND: 70,
    STAGE_CONFIDANT: 150,
    STAGE_SOULMATE: 300,
}

# 各 update_kind 的积分贡献
DEFAULT_SCORE_MAP: dict[str, int] = {
    "INTERACTION": 1,
    "DEEP_TALK": 5,
    "EMOTIONAL_SUPPORT": 8,
    "SHARED_MEMORY": 3,
    "GIFT": 4,
    "CONFLICT_RESOLVED": 6,
    "MILESTONE": 10,
    "IDLE_DECAY": -2,
}

# 趋势判断窗口（最近 N 次事件）
TREND_WINDOW = 5

S_BOND_STAGE = "S_BOND_STAGE"
TRANSITION_STATUS_PROMOTED = "PROMOTED"
TRANSITION_STATUS_DEMOTED = "DEMOTED"
TRANSITION_STATUS_STABLE = "STABLE"
UPDATE_KIND_INIT = "INIT"
UPDATE_KIND_STAGE_TRANSITION = "STAGE_TRANSITION"
STATUS_SUCCESS = "SUCCESS"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class BondScoreState:
    """用户 Bond 积分状态快照。"""
    user_id: str
    total_score: int = 0
    current_stage: str = STAGE_STRANGER
    event_count: int = 0
    last_event_ts_ms: int = 0
    recent_scores: list[int] = field(default_factory=list)  # 最近 TREND_WINDOW 次积分

    def trend(self) -> str:
        """根据最近积分趋势判断 RISING/STABLE/DECLINING。"""
        if len(self.recent_scores) < 2:
            return "STABLE"
        recent = self.recent_scores[-TREND_WINDOW:]
        positive = sum(1 for s in recent if s > 0)
        negative = sum(1 for s in recent if s < 0)
        if positive > negative and positive >= len(recent) // 2 + 1:
            return "RISING"
        if negative > positive and negative >= len(recent) // 2 + 1:
            return "DECLINING"
        return "STABLE"

    def capability_hint(self) -> str:
        """根据阶段返回能力提示（降采样，不含数值）。"""
        hints = {
            STAGE_STRANGER: "初识，保持礼貌距离",
            STAGE_ACQUAINTANCE: "相识，可分享日常",
            STAGE_FRIEND: "朋友，可深入交流",
            STAGE_CLOSE_FRIEND: "挚友，可倾诉心事",
            STAGE_CONFIDANT: "知己，深度信任",
            STAGE_SOULMATE: "灵魂伴侣，无话不谈",
        }
        return hints.get(self.current_stage, "UNKNOWN")

    def to_summary(self) -> dict[str, Any]:
        """生成 context-pack-safe 的 Bond Summary（不含数值）。"""
        return {
            "stage": self.current_stage,
            "trend": self.trend(),
            "capability_hint": self.capability_hint(),
        }


@dataclass(frozen=True)
class StageTransitionResult:
    """阶段晋级/降级结果。"""
    user_id: str
    from_stage: str
    to_stage: str
    transition_status: str  # PROMOTED / DEMOTED / STABLE
    score_delta: int
    total_score: int
    evidence_refs: list[str]
    transition_id: str
    ts_ms: int
    detail: str


# ---------------------------------------------------------------------------
# BondStageEngine
# ---------------------------------------------------------------------------

class BondStageEngine:
    """Bond 阶段积分计算 + 自动晋级引擎。

    职责：
    1. 接收 Bond 事件，按 update_kind 累积积分
    2. 判断是否触发阶段晋级/降级
    3. 晋级/降级写入审计链（bond_stage_transition_log）
    4. 提供 Bond Summary 降采样输出（不含数值）
    """

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        stage_thresholds: dict[str, int] | None = None,
        score_map: dict[str, int] | None = None,
    ) -> None:
        self._ledger = ledger
        self._thresholds = dict(stage_thresholds or DEFAULT_STAGE_THRESHOLDS)
        self._score_map = dict(score_map or DEFAULT_SCORE_MAP)
        # user_id → BondScoreState
        self._states: dict[str, BondScoreState] = {}

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def apply_event(
        self,
        *,
        user_id: str,
        update_kind: str,
        evidence_refs: list[str],
        request_id: str | None = None,
        ts_ms: int | None = None,
        emotion_intensity: float = 0.5,
    ) -> StageTransitionResult:
        """处理一次 Bond 事件，更新积分并判断阶段变化。

        M27-E：emotion_intensity > 0.7 的互动额外加权 1.5x（OPT-26）。
        """
        req_id = request_id or new_request_id()
        ts = int(ts_ms if ts_ms is not None else now_ms())
        uid = str(user_id).strip()

        state = self._get_or_create_state(uid)
        from_stage = state.current_stage

        # 计算积分增量
        base_delta = self._score_map.get(str(update_kind).strip(), 1)
        # M27-E：情感强度加权
        intensity = max(0.0, min(1.0, float(emotion_intensity)))
        if intensity > 0.7:
            delta = int(round(base_delta * 1.5))
        else:
            delta = base_delta
        state.total_score = max(0, state.total_score + delta)
        state.event_count += 1
        state.last_event_ts_ms = ts
        state.recent_scores.append(delta)
        if len(state.recent_scores) > TREND_WINDOW * 2:
            state.recent_scores = state.recent_scores[-TREND_WINDOW * 2:]

        # 判断阶段变化
        new_stage = self._compute_stage(state.total_score)
        transition_status = TRANSITION_STATUS_STABLE
        if new_stage != from_stage:
            from_idx = STAGE_ORDER.index(from_stage) if from_stage in STAGE_ORDER else 0
            to_idx = STAGE_ORDER.index(new_stage) if new_stage in STAGE_ORDER else 0
            transition_status = (
                TRANSITION_STATUS_PROMOTED if to_idx > from_idx else TRANSITION_STATUS_DEMOTED
            )
            state.current_stage = new_stage
            _logger.info(
                "Bond stage transition: user=%s %s→%s (score=%d)",
                uid, from_stage, new_stage, state.total_score,
                extra={"request_id": req_id},
            )

        transition_id = f"bond_trans_{req_id}"
        detail = (
            f"{transition_status}: {from_stage}→{new_stage} "
            f"(delta={delta:+d}, total={state.total_score})"
        )

        result = StageTransitionResult(
            user_id=uid,
            from_stage=from_stage,
            to_stage=new_stage,
            transition_status=transition_status,
            score_delta=delta,
            total_score=state.total_score,
            evidence_refs=list(evidence_refs),
            transition_id=transition_id,
            ts_ms=ts,
            detail=detail,
        )

        # 写入审计链
        self._log_transition(result, req_id)
        return result

    def get_state(self, user_id: str) -> BondScoreState:
        """获取用户当前 Bond 积分状态。"""
        return self._get_or_create_state(str(user_id).strip())

    def get_summary(self, user_id: str) -> dict[str, Any]:
        """获取 context-pack-safe 的 Bond Summary（不含数值）。"""
        return self._get_or_create_state(str(user_id).strip()).to_summary()

    def reset_state(self, user_id: str) -> None:
        """重置用户 Bond 状态（仅用于测试/管理）。"""
        uid = str(user_id).strip()
        self._states[uid] = BondScoreState(user_id=uid)

    def list_users(self) -> list[str]:
        """列出所有有 Bond 状态的用户 ID。"""
        return list(self._states.keys())

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _get_or_create_state(self, user_id: str) -> BondScoreState:
        if user_id not in self._states:
            state = BondScoreState(user_id=user_id)
            self._states[user_id] = state
            self._log_initial_state(state)
        return self._states[user_id]

    def _compute_stage(self, total_score: int) -> str:
        """根据总积分计算当前阶段（取满足阈值的最高阶段）。"""
        current = STAGE_STRANGER
        for stage in STAGE_ORDER:
            threshold = self._thresholds.get(stage, 0)
            if total_score >= threshold:
                current = stage
        return current

    def _log_transition(
        self,
        result: StageTransitionResult,
        request_id: str,
    ) -> None:
        """写入 bond_stage_transition_log 审计链。"""
        if self._ledger is None:
            return
        record = {
            "request_id": request_id,
            "transition_id": result.transition_id,
            "user_id": result.user_id,
            "from_stage": result.from_stage,
            "to_stage": result.to_stage,
            "transition_status": result.transition_status,
            "score_delta": result.score_delta,
            "total_score": result.total_score,
            "evidence_refs": result.evidence_refs,
            "stage": S_BOND_STAGE,
            "detail": result.detail,
            "ts_ms": result.ts_ms,
        }
        log_bond_stage_transition = getattr(self._ledger, "log_bond_stage_transition", None)
        if callable(log_bond_stage_transition):
            log_bond_stage_transition(record)
            return

        log_bond_update = getattr(self._ledger, "log_bond_update", None)
        if callable(log_bond_update):
            state = self._states.get(result.user_id)
            capability_hint = state.capability_hint() if state is not None else ""
            log_bond_update(
                {
                    "request_id": request_id,
                    "intent_id": None,
                    "session_id": None,
                    "seq": None,
                    "event_id": result.transition_id,
                    "user_id": result.user_id,
                    "topic_id": None,
                    "capability_hint": capability_hint,
                    "update_kind": UPDATE_KIND_STAGE_TRANSITION,
                    "status": STATUS_SUCCESS,
                    "reason_code": None,
                    "stage": S_BOND_STAGE,
                    "evidence_refs": list(result.evidence_refs),
                    "payload": {
                        "from_stage": result.from_stage,
                        "to_stage": result.to_stage,
                        "transition_status": result.transition_status,
                        "score_delta": result.score_delta,
                        "total_score": result.total_score,
                    },
                    "detail": result.detail,
                    "ts_ms": result.ts_ms,
                }
            )
            return

        log_system_event = getattr(self._ledger, "log_system_event", None)
        if callable(log_system_event):
            log_system_event(
                request_id=request_id,
                event_type="BOND_STAGE_TRANSITION",
                payload=record,
            )

    def _log_initial_state(self, state: BondScoreState) -> None:
        if self._ledger is None:
            return
        log_bond_update = getattr(self._ledger, "log_bond_update", None)
        if not callable(log_bond_update):
            return
        request_id = new_request_id()
        ts_ms = int(now_ms())
        log_bond_update(
            {
                "request_id": request_id,
                "intent_id": None,
                "session_id": None,
                "seq": None,
                "event_id": f"bond_init_{request_id}",
                "user_id": state.user_id,
                "topic_id": None,
                "capability_hint": state.capability_hint(),
                "update_kind": UPDATE_KIND_INIT,
                "status": STATUS_SUCCESS,
                "reason_code": None,
                "stage": S_BOND_STAGE,
                "evidence_refs": [],
                "payload": {
                    "current_stage": state.current_stage,
                    "total_score": state.total_score,
                    "event_count": state.event_count,
                },
                "detail": "Initialized bond state with default values",
                "ts_ms": ts_ms,
            }
        )
