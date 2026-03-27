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
    # M28-C：三级互动分类（OPT-7）
    "INTERACTION_MINIMAL": 1,    # 简短互动（< 10 字，无问号）
    "INTERACTION_ENGAGED": 2,    # 普通互动（> 50 字或含问号）
    "INTERACTION_DEEP": 5,       # 深度互动（emotion_intensity > 0.8 且 > 100 字）
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

# M28-H：阶段名称中文映射（OPT-21）
_STAGE_CN_MAP: dict[str, str] = {
    STAGE_STRANGER: "陌生人",
    STAGE_ACQUAINTANCE: "相识",
    STAGE_FRIEND: "朋友",
    STAGE_CLOSE_FRIEND: "挚友",
    STAGE_CONFIDANT: "知己",
    STAGE_SOULMATE: "灵魂伴侣",
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

# M29-A：BondEmotionalTexture 情感质感层（OPT-2）
@dataclass
class BondEmotionalTexture:
    """Bond 情感质感层——感知互动节奏和关系温度（M29-A OPT-2）。

    Attributes:
        avg_response_gap_ms: 平均回复间隔（毫秒），越小表示互动越频繁
        recent_emotion_valence: 近期情感均值（[-1.0, 1.0]），正值=积极
        intimacy_temperature: 亲密温度（[0.0, 1.0]），综合节奏+情感计算
    """
    avg_response_gap_ms: float = 30000.0   # 默认 30 秒
    recent_emotion_valence: float = 0.0
    intimacy_temperature: float = 0.5

    def update(
        self,
        *,
        response_gap_ms: float | None = None,
        emotion_valence: float | None = None,
    ) -> "BondEmotionalTexture":
        """更新质感层并重新计算 intimacy_temperature。"""
        new_gap = self.avg_response_gap_ms
        new_valence = self.recent_emotion_valence
        if response_gap_ms is not None:
            # 指数移动平均（α=0.3）
            new_gap = self.avg_response_gap_ms * 0.7 + float(response_gap_ms) * 0.3
        if emotion_valence is not None:
            new_valence = self.recent_emotion_valence * 0.7 + float(emotion_valence) * 0.3
            new_valence = max(-1.0, min(1.0, new_valence))
        # intimacy_temperature：节奏贡献 50%（gap 越小越高），情感贡献 50%
        gap_score = max(0.0, 1.0 - new_gap / 60000.0)  # 60s 以上 → 0
        valence_score = (new_valence + 1.0) / 2.0       # [-1,1] → [0,1]
        temperature = round(gap_score * 0.5 + valence_score * 0.5, 4)
        return BondEmotionalTexture(
            avg_response_gap_ms=round(new_gap, 1),
            recent_emotion_valence=round(new_valence, 4),
            intimacy_temperature=temperature,
        )

    def style_hint(self) -> str:
        """根据 intimacy_temperature 返回风格提示（用于 to_summary）。"""
        if self.intimacy_temperature >= 0.7:
            return "温暖亲密，可以更主动表达情感"
        if self.intimacy_temperature >= 0.4:
            return "关系融洽，保持自然互动"
        return "关系较疏远，保持礼貌距离"


# M29-G：BondRiftState 裂痕-修复双轨机制（OPT-11）
@dataclass
class BondRiftState:
    """Bond 裂痕状态（M29-G OPT-11）。

    Attributes:
        rift_level: 裂痕程度（[0.0, 1.0]），0=无裂痕，1=严重裂痕
        repair_progress: 修复进度（[0.0, 1.0]），1.0=完全修复
        rift_cause: 裂痕原因描述
    """
    rift_level: float = 0.0
    repair_progress: float = 0.0
    rift_cause: str = ""

    def is_active(self) -> bool:
        """裂痕是否处于活跃状态（未完全修复）。"""
        return self.rift_level > 0.0 and self.repair_progress < 1.0

    def repair_hint(self) -> str:
        """返回修复风格提示（用于 to_summary）。"""
        if not self.is_active():
            return ""
        if self.repair_progress < 0.3:
            return "关系出现裂痕，需要温柔道歉和主动关心"
        if self.repair_progress < 0.7:
            return "正在修复关系，保持温柔耐心"
        return "关系即将修复，继续温暖互动"


@dataclass
class BondScoreState:
    """用户 Bond 积分状态快照。"""
    user_id: str
    total_score: int = 0
    current_stage: str = STAGE_STRANGER
    event_count: int = 0
    last_event_ts_ms: int = 0
    recent_scores: list[int] = field(default_factory=list)  # 最近 TREND_WINDOW 次积分
    # M29-A：情感质感层
    texture: BondEmotionalTexture = field(default_factory=BondEmotionalTexture)
    # M29-G：裂痕状态
    rift: "BondRiftState | None" = None

    def trend(self) -> str:
        """根据最近积分趋势判断，返回中文语义描述（M28-F OPT-14）。

        返回值：上升/快速上升/稳定/下降/快速下降
        """
        if len(self.recent_scores) < 2:
            return "稳定"
        recent = self.recent_scores[-TREND_WINDOW:]
        positive = sum(1 for s in recent if s > 0)
        negative = sum(1 for s in recent if s < 0)
        total = len(recent)
        if positive > negative and positive >= total // 2 + 1:
            # 判断是否快速上升（平均积分 > 3）
            avg = sum(s for s in recent if s > 0) / max(positive, 1)
            return "快速上升" if avg >= 3 else "上升"
        if negative > positive and negative >= total // 2 + 1:
            avg_neg = abs(sum(s for s in recent if s < 0)) / max(negative, 1)
            return "快速下降" if avg_neg >= 3 else "下降"
        return "稳定"

    def trend_intensity(self) -> str:
        """返回趋势强度描述（M28-F OPT-14）。"""
        t = self.trend()
        if "快速" in t:
            return "强"
        if t in ("上升", "下降"):
            return "中"
        return "弱"

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
        """生成 context-pack-safe 的 Bond Summary（不含数值，全中文）。

        M28-H（OPT-21）：所有字段值统一为中文，消除英中文枚举混用。
        M29-A（OPT-2）：加入 texture 风格提示。
        M29-G（OPT-11）：裂痕期间加入修复风格提示。
        """
        stage_cn = _STAGE_CN_MAP.get(self.current_stage, "未知")
        summary: dict[str, Any] = {
            "stage": stage_cn,
            "trend": self.trend(),
            "capability_hint": self.capability_hint(),
            "shared_memory_count": 0,  # Phase V：由 BondNarrativeLayer 填充
            "milestone_hint": "",      # Phase V：由 MilestoneTracker 填充
        }
        # M29-A：texture 风格提示
        if self.texture.intimacy_temperature >= 0.7:
            summary["style_hint"] = self.texture.style_hint()
        # M29-G：裂痕修复提示
        if self.rift is not None and self.rift.is_active():
            summary["repair_hint"] = self.rift.repair_hint()
        # Phase V：里程碑和共同记忆由外部调用者注入
        return summary

    def inject_narrative(
        self,
        *,
        milestone_hint: str = "",
        shared_memory_count: int = 0,
    ) -> None:
        """Phase V：注入叙事层数据到 BondScoreState（便于 to_summary 输出）。"""
        self._milestone_hint = milestone_hint
        self._shared_memory_count = shared_memory_count


@dataclass(frozen=True)
class BondStateSnapshot:
    user_id: str
    stage: str
    score: float
    label: str
    interaction_count: int


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
        milestone_tracker: Any | None = None,
    ) -> None:
        self._ledger = ledger
        self._thresholds = dict(stage_thresholds or DEFAULT_STAGE_THRESHOLDS)
        self._score_map = dict(score_map or DEFAULT_SCORE_MAP)
        # user_id → BondScoreState
        self._states: dict[str, BondScoreState] = {}
        # Phase IX：可选注入 MilestoneTracker（接通里程碑→人格成长断路）
        self._milestone_tracker = milestone_tracker
        # Phase IX：缓存 PersonaGrowthTracker 实例（修复每次 new 导致 one_time 状态丢失）
        self._persona_growth_tracker: Any | None = None

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
        # 修复审计问题 #5：先保存旧时间戳，再更新，确保 response_gap_ms 能正确计算
        _prev_event_ts_ms = state.last_event_ts_ms
        state.last_event_ts_ms = ts
        state.recent_scores.append(delta)
        if len(state.recent_scores) > TREND_WINDOW * 2:
            state.recent_scores = state.recent_scores[-TREND_WINDOW * 2:]

        # 修复审计问题 #5：更新 BondEmotionalTexture，激活亲密温度功能
        # emotion_intensity 映射到 valence：[0,1] → [-1,1]（0.5 为中性）
        emotion_valence = (intensity - 0.5) * 2.0
        # 使用事件间隔估算回复节奏（若有上次事件时间戳）
        response_gap_ms: float | None = None
        if _prev_event_ts_ms > 0 and ts > _prev_event_ts_ms:
            response_gap_ms = float(ts - _prev_event_ts_ms)
        state.texture = state.texture.update(
            response_gap_ms=response_gap_ms,
            emotion_valence=emotion_valence,
        )

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

        # Phase IV：阶段晋级时触发人格成长
        if transition_status == TRANSITION_STATUS_PROMOTED:
            self._trigger_persona_growth(
                user_id=uid,
                from_stage=from_stage,
                to_stage=new_stage,
                ts_ms=ts,
            )

        # Phase IX：里程碑检测 + 里程碑人格成长（接通 MilestoneTracker→PersonaGrowthTracker 断路）
        if self._milestone_tracker is not None:
            try:
                new_milestones = self._milestone_tracker.on_event(
                    user_id=uid,
                    event_type=str(update_kind).strip().upper(),
                    ts_ms=ts,
                    metadata={
                        "from_stage": from_stage,
                        "to_stage": new_stage,
                        "transition_status": transition_status,
                    },
                )
                for ms in new_milestones:
                    self._trigger_milestone_growth(uid, ms.milestone_type, ts)
            except Exception as _ms_exc:
                _logger.warning(
                    "里程碑检测失败（降级跳过）: user=%s error=%s", uid, _ms_exc,
                )

        return result

    # ── Phase IV：人格成长触发 ────────────────────────────────────────────

    def _trigger_persona_growth(
        self,
        *,
        user_id: str,
        from_stage: str,
        to_stage: str,
        ts_ms: int,
    ) -> None:
        """阶段晋级时触发 PersonaGrowthTracker。

        Phase IX 改进：缓存 PersonaGrowthTracker 实例，修复每次 new 导致
        _triggered_rules 内存状态丢失的问题。
        降级处理：如果 PersonaGrowthTracker 不可用，仅记录日志。
        """
        try:
            tracker = self._get_persona_growth_tracker()
            if tracker is None:
                return
            result = tracker.on_stage_promoted(
                user_id=user_id,
                from_stage=from_stage,
                to_stage=to_stage,
                ts_ms=ts_ms,
            )
            _logger.info(
                "人格成长触发: user=%s %s→%s applied=%s reason=%s",
                user_id, from_stage, to_stage, result.applied, result.reason,
            )
        except Exception as exc:
            _logger.warning(
                "人格成长触发失败（降级跳过）: user=%s %s→%s error=%s",
                user_id, from_stage, to_stage, exc,
            )

    def _trigger_milestone_growth(
        self,
        user_id: str,
        milestone_type: str,
        ts_ms: int,
    ) -> None:
        """Phase IX：里程碑事件触发人格成长——接通 MilestoneTracker→PersonaGrowthTracker。"""
        try:
            tracker = self._get_persona_growth_tracker()
            if tracker is None:
                return
            # milestone_type 可能包含阶段后缀（如 "STAGE_PROMOTED:FRIEND→CLOSE_FRIEND"）
            # 提取基础类型作为里程碑键
            base_type = milestone_type.split(":")[0].lower()
            result = tracker.on_milestone(
                user_id=user_id,
                milestone_key=base_type,
                ts_ms=ts_ms,
            )
            if result.applied:
                _logger.info(
                    "里程碑人格成长触发: user=%s milestone=%s applied=%s",
                    user_id, milestone_type, result.applied,
                )
        except Exception as exc:
            _logger.warning(
                "里程碑人格成长失败（降级跳过）: user=%s milestone=%s error=%s",
                user_id, milestone_type, exc,
            )

    def _get_persona_growth_tracker(self) -> Any:
        """获取缓存的 PersonaGrowthTracker 实例（延迟创建+复用）。"""
        if self._persona_growth_tracker is not None:
            return self._persona_growth_tracker
        try:
            from host_brain.persona.persona_growth import PersonaGrowthTracker  # noqa: PLC0415
            self._persona_growth_tracker = PersonaGrowthTracker()
            return self._persona_growth_tracker
        except Exception as exc:
            _logger.warning("PersonaGrowthTracker 不可用: %s", exc)
            return None

    def get_state(self, user_id: str) -> BondScoreState:
        """获取用户当前 Bond 积分状态。"""
        return self._get_or_create_state(str(user_id).strip())

    def get_bond_state(self, *, session_id: str | None = None, user_id: str | None = None) -> BondStateSnapshot:
        identity = str(user_id or session_id or "").strip()
        state = self._get_or_create_state(identity)
        return BondStateSnapshot(
            user_id=state.user_id,
            stage=state.current_stage,
            score=float(state.total_score),
            label=state.capability_hint(),
            interaction_count=int(state.event_count),
        )

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
            state = self._restore_state_from_log(user_id)
            if state is None:
                state = BondScoreState(user_id=user_id)
                self._states[user_id] = state
                self._log_initial_state(state)
            else:
                self._states[user_id] = state
        return self._states[user_id]

    def _restore_state_from_log(self, user_id: str) -> "BondScoreState | None":
        """从 bond_update_log 恢复历史积分状态，避免重启后积分清零。

        策略：
        1. 优先读取最近一条 STAGE_TRANSITION 记录的 total_score（最可靠）
        2. 若无 STAGE_TRANSITION，则累加所有非 INIT/STAGE_TRANSITION 事件的积分
        3. 若无任何历史记录，返回 None（走正常 INIT 流程）
        """
        if self._ledger is None:
            return None
        try:
            db_path = getattr(self._ledger, "_db_path", None) or getattr(self._ledger, "db_path", None)
            if not db_path:
                return None
            import sqlite3 as _sqlite3
            conn = _sqlite3.connect(db_path)
            try:
                # 优先：读最近一条 STAGE_TRANSITION 的 payload.total_score
                # 注意：STAGE_TRANSITION 可能写入 bond_stage_transition_log 或 bond_update_log，
                # 需要同时查两张表，取最新的一条
                row = None
                # 先尝试 bond_stage_transition_log（log_bond_stage_transition 路径）
                try:
                    row = conn.execute(
                        "SELECT payload, ts_ms FROM bond_stage_transition_log "
                        "WHERE transition_status IN ('PROMOTED','DEMOTED','STABLE') "
                        "AND user_id=? "
                        "ORDER BY ts_ms DESC LIMIT 1",
                        (user_id,),
                    ).fetchone()
                except Exception:
                    pass  # 表不存在时降级到 bond_update_log
                # 再尝试 bond_update_log（log_bond_update 路径）
                if row is None:
                    row = conn.execute(
                        "SELECT payload, ts_ms FROM bond_update_log "
                        "WHERE update_kind='STAGE_TRANSITION' AND status='SUCCESS' "
                        "AND user_id=? "
                        "ORDER BY ts_ms DESC LIMIT 1",
                        (user_id,),
                    ).fetchone()

                import json as _json

                # 策略1：优先从最近一条 STAGE_TRANSITION 的 payload 中读取 total_score
                # 这是最可靠的恢复方式，避免累加历史负分事件导致积分偏低
                if row is not None:
                    try:
                        payload_data = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
                        saved_score = int(payload_data.get("total_score", 0))
                        saved_ts = int(row[1] or 0)
                        if saved_score > 0:
                            # 在 STAGE_TRANSITION 之后的增量事件也需要累加
                            delta_rows = conn.execute(
                                "SELECT update_kind, COUNT(*) FROM bond_update_log "
                                "WHERE update_kind NOT IN ('INIT','STAGE_TRANSITION') "
                                "AND status IN ('OK','SUCCESS') "
                                "AND ts_ms > ? "
                                "AND user_id=? "
                                "GROUP BY update_kind",
                                (saved_ts, user_id),
                            ).fetchall()
                            delta_score = 0
                            delta_count = 0
                            last_ts = saved_ts
                            for kind, cnt in delta_rows:
                                base = self._score_map.get(str(kind).strip(), 1)
                                delta_score += base * cnt
                                delta_count += cnt
                            total_score = max(0, saved_score + delta_score)
                            event_count_row = conn.execute(
                                "SELECT COUNT(*), MAX(ts_ms) FROM bond_update_log "
                                "WHERE update_kind NOT IN ('INIT','STAGE_TRANSITION') "
                                "AND status IN ('OK','SUCCESS') "
                                "AND user_id=?",
                                (user_id,),
                            ).fetchone()
                            event_count = int(event_count_row[0] or 0) if event_count_row else delta_count
                            if event_count_row and event_count_row[1]:
                                last_ts = max(last_ts, int(event_count_row[1]))
                            stage = self._compute_stage(total_score)
                            state = BondScoreState(
                                user_id=user_id,
                                total_score=total_score,
                                current_stage=stage,
                                event_count=event_count,
                                last_event_ts_ms=last_ts,
                            )
                            _logger.info(
                                "Bond 状态从 STAGE_TRANSITION 恢复: user=%s stage=%s score=%d events=%d",
                                user_id, stage, total_score, event_count,
                            )
                            return state
                    except Exception as _parse_exc:
                        _logger.warning("Bond STAGE_TRANSITION payload 解析失败，降级为累加模式: %s", _parse_exc)

                # 策略2：无 STAGE_TRANSITION 时，累加所有历史事件积分（排除负分衰减事件）
                event_rows = conn.execute(
                    "SELECT update_kind, COUNT(*), MAX(ts_ms) FROM bond_update_log "
                    "WHERE update_kind NOT IN ('INIT','STAGE_TRANSITION','IDLE_DECAY') "
                    "AND status IN ('OK','SUCCESS') "
                    "AND user_id=? "
                    "GROUP BY update_kind",
                    (user_id,),
                ).fetchall()
                total_score = 0
                event_count = 0
                last_ts = 0
                for kind, cnt, max_ts in event_rows:
                    base = self._score_map.get(str(kind).strip(), 1)
                    total_score += base * cnt
                    event_count += cnt
                    if max_ts and int(max_ts) > last_ts:
                        last_ts = int(max_ts)
                if total_score > 0:
                    stage = self._compute_stage(total_score)
                    state = BondScoreState(
                        user_id=user_id,
                        total_score=total_score,
                        current_stage=stage,
                        event_count=event_count,
                        last_event_ts_ms=last_ts,
                    )
                    _logger.info(
                        "Bond 状态从日志恢复: user=%s stage=%s score=%d events=%d",
                        user_id, stage, total_score, event_count,
                    )
                    return state
            finally:
                conn.close()
        except Exception as exc:
            _logger.warning("Bond 状态恢复失败（降级为新状态）: %s", exc)
        return None

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
