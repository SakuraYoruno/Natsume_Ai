"""ModeTransitionService — 智能模式切换服务。

统一管理 sys_mode 的手动切换和自动切换：
  - 手动切换：通过 API 直接切换
  - 自动切换：基于规则引擎的事件驱动切换

自动切换触发器：
  - 🎮 gaming_process_detected → normal → gaming
  - 🎮 gaming_process_exited → gaming → normal
  - 💤 idle_timeout → normal → sleep（长时间空闲 + 深夜时段）
  - ☀️ morning_wakeup → sleep → normal（首次日间互动）
  - 📱 user_message_received → sleep → normal（收到任何用户消息）

每次切换写入审计日志，支持查询切换历史。
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from host_brain.core.ids import now_ms, new_event_id
from host_brain.core.metabolism import (
    ALLOWED_SYS_MODES,
    HardState,
    evolve_hard_state,
)

logger = logging.getLogger("Natsume.mode_transition")


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

class TransitionSource(str, Enum):
    """切换来源。"""
    MANUAL = "manual"           # API / WebUI 手动切换
    AUTO_GAMING = "auto_gaming"  # 游戏进程检测
    AUTO_IDLE = "auto_idle"      # 空闲超时
    AUTO_WAKEUP = "auto_wakeup"  # 早间唤醒 / 用户消息
    AUTO_SCHEDULE = "auto_schedule"  # 定时计划
    SKILL = "skill"              # Skill 触发（如 focus_mode）


# 合法的模式切换路径（from_mode → to_mode 集合）
# None 表示允许从任意模式切换
VALID_TRANSITIONS: dict[str | None, set[str]] = {
    "normal":      {"focus", "gaming", "sleep", "maintenance", "locked"},
    "focus":       {"normal"},
    "gaming":      {"normal"},
    "sleep":       {"normal"},
    "maintenance": {"normal", "locked"},
    "locked":      {"normal", "maintenance"},
}

# 自动切换规则默认开关
DEFAULT_AUTO_RULES: dict[str, bool] = {
    "gaming_detect":   True,   # 检测到游戏进程 → gaming
    "gaming_exit":     True,   # 游戏进程退出 → normal
    "idle_to_sleep":   True,   # 长时间空闲 → sleep
    "wakeup_on_msg":   True,   # 收到消息 → normal（从 sleep）
    "morning_wakeup":  True,   # 早间首次互动 → normal
}

# 空闲超时阈值（毫秒）：2 小时
DEFAULT_IDLE_THRESHOLD_MS = 2 * 60 * 60 * 1000

# 深夜时段：23:00 - 06:00
NIGHT_HOUR_START = 23
NIGHT_HOUR_END = 6


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModeTransitionEvent:
    """模式切换事件记录。"""
    event_id: str
    from_mode: str
    to_mode: str
    source: str           # TransitionSource.value
    reason: str           # 切换原因说明
    ts_ms: int
    success: bool
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "from_mode": self.from_mode,
            "to_mode": self.to_mode,
            "source": self.source,
            "reason": self.reason,
            "ts_ms": self.ts_ms,
            "success": self.success,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# ModeTransitionService
# ---------------------------------------------------------------------------

class ModeTransitionService:
    """智能模式切换服务——统一管理手动和自动模式切换。

    用法::

        service = ModeTransitionService(
            get_hard_state=lambda: current_hard_state,
            set_sys_mode=lambda mode: ...,
            ledger=ledger,
        )

        # 手动切换
        result = service.switch_mode("gaming", source=TransitionSource.MANUAL, reason="用户手动切换")

        # 自动切换事件
        service.on_event("gaming_process_detected", {"process_name": "genshin.exe"})
        service.on_event("user_message_received", {"channel": "wechat"})
    """

    def __init__(
        self,
        *,
        get_hard_state: Callable[[], HardState],
        set_sys_mode: Callable[[str], dict[str, Any]],
        ledger: Any | None = None,
        auto_rules: dict[str, bool] | None = None,
        idle_threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS,
    ) -> None:
        self._get_hard_state = get_hard_state
        self._set_sys_mode = set_sys_mode
        self._ledger = ledger
        self._auto_rules = dict(DEFAULT_AUTO_RULES)
        if auto_rules:
            self._auto_rules.update(auto_rules)
        self._idle_threshold_ms = int(idle_threshold_ms)

        # 切换历史（内存环形缓冲，最近 100 条）
        self._history: list[ModeTransitionEvent] = []
        self._history_lock = threading.Lock()
        self._max_history = 100

        # 上次互动时间（由外部更新）
        self._last_interaction_ts_ms: int = now_ms()

        logger.info(
            "ModeTransitionService initialized (auto_rules=%s, idle_threshold_ms=%d)",
            self._auto_rules,
            self._idle_threshold_ms,
        )

    # ── 核心切换 ──────────────────────────────────────────────────────────

    def switch_mode(
        self,
        target_mode: str,
        *,
        source: TransitionSource = TransitionSource.MANUAL,
        reason: str = "",
    ) -> dict[str, Any]:
        """切换 sys_mode。

        Args:
            target_mode: 目标模式
            source: 切换来源
            reason: 切换原因

        Returns:
            {"success": bool, "from_mode": str, "to_mode": str, "event_id": str, ...}
        """
        ts = now_ms()
        event_id = new_event_id()
        target = str(target_mode).strip().lower()

        # 获取当前模式
        try:
            current_state = self._get_hard_state()
            from_mode = current_state.sys_mode
        except Exception as exc:
            logger.error("Failed to get current hard_state: %s", exc)
            return self._record_event(ModeTransitionEvent(
                event_id=event_id,
                from_mode="unknown",
                to_mode=target,
                source=source.value,
                reason=reason,
                ts_ms=ts,
                success=False,
                error=f"get_hard_state failed: {exc}",
            ))

        # 目标模式合法性检查
        if target not in ALLOWED_SYS_MODES:
            return self._record_event(ModeTransitionEvent(
                event_id=event_id,
                from_mode=from_mode,
                to_mode=target,
                source=source.value,
                reason=reason,
                ts_ms=ts,
                success=False,
                error=f"invalid target mode: {target}",
            ))

        # 相同模式不切换
        if from_mode == target:
            return self._record_event(ModeTransitionEvent(
                event_id=event_id,
                from_mode=from_mode,
                to_mode=target,
                source=source.value,
                reason=reason,
                ts_ms=ts,
                success=True,
                error=None,
            ))

        # 切换路径合法性检查
        allowed_targets = VALID_TRANSITIONS.get(from_mode, set())
        if target not in allowed_targets:
            return self._record_event(ModeTransitionEvent(
                event_id=event_id,
                from_mode=from_mode,
                to_mode=target,
                source=source.value,
                reason=reason,
                ts_ms=ts,
                success=False,
                error=f"transition {from_mode} → {target} not allowed",
            ))

        # 执行切换
        try:
            result = self._set_sys_mode(target)
            success = True
            error = None
            logger.info(
                "Mode transition: %s → %s (source=%s, reason=%s)",
                from_mode, target, source.value, reason,
            )
        except Exception as exc:
            success = False
            error = str(exc)
            logger.error("Mode transition failed: %s → %s: %s", from_mode, target, exc)

        return self._record_event(ModeTransitionEvent(
            event_id=event_id,
            from_mode=from_mode,
            to_mode=target,
            source=source.value,
            reason=reason,
            ts_ms=ts,
            success=success,
            error=error,
        ))

    # ── 事件驱动自动切换 ──────────────────────────────────────────────────

    def on_event(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """处理外部事件，触发自动模式切换。

        Args:
            event_type: 事件类型
            payload: 事件载荷

        Returns:
            切换结果字典，或 None（不触发切换）
        """
        payload = payload or {}

        # 更新最后互动时间
        if event_type in ("user_message_received", "voice_input_received", "wechat_message_received"):
            self._last_interaction_ts_ms = now_ms()

        current_mode = self._get_current_mode()

        # ── 游戏进程检测 → gaming ──
        if event_type == "gaming_process_detected" and self._auto_rules.get("gaming_detect"):
            if current_mode == "normal":
                process_name = payload.get("process_name", "unknown")
                return self.switch_mode(
                    "gaming",
                    source=TransitionSource.AUTO_GAMING,
                    reason=f"检测到游戏进程: {process_name}",
                )

        # ── 游戏进程退出 → normal ──
        if event_type == "gaming_process_exited" and self._auto_rules.get("gaming_exit"):
            if current_mode == "gaming":
                return self.switch_mode(
                    "normal",
                    source=TransitionSource.AUTO_GAMING,
                    reason="游戏进程已退出",
                )

        # ── 收到用户消息 → 从 sleep 唤醒 ──
        if event_type in ("user_message_received", "voice_input_received", "wechat_message_received"):
            if current_mode == "sleep" and self._auto_rules.get("wakeup_on_msg"):
                channel = payload.get("channel", event_type)
                return self.switch_mode(
                    "normal",
                    source=TransitionSource.AUTO_WAKEUP,
                    reason=f"收到用户消息，从 sleep 唤醒 (channel={channel})",
                )

        # ── 空闲超时 → sleep ──
        if event_type == "idle_check":
            if current_mode == "normal" and self._auto_rules.get("idle_to_sleep"):
                idle_ms = now_ms() - self._last_interaction_ts_ms
                if idle_ms >= self._idle_threshold_ms and self._is_night_time():
                    return self.switch_mode(
                        "sleep",
                        source=TransitionSource.AUTO_IDLE,
                        reason=f"空闲 {idle_ms // 60000} 分钟 + 深夜时段，自动进入 sleep",
                    )

        # ── 早间唤醒 ──
        if event_type == "morning_check":
            if current_mode == "sleep" and self._auto_rules.get("morning_wakeup"):
                now = datetime.datetime.now()
                if 6 <= now.hour < 10:
                    return self.switch_mode(
                        "normal",
                        source=TransitionSource.AUTO_WAKEUP,
                        reason=f"早间唤醒 ({now.strftime('%H:%M')})",
                    )

        return None

    # ── 查询接口 ─────────────────────────────────────────────────────────

    def get_current_status(self) -> dict[str, Any]:
        """获取当前模式状态 + 自动切换规则。"""
        current_mode = self._get_current_mode()
        idle_ms = now_ms() - self._last_interaction_ts_ms
        return {
            "sys_mode": current_mode,
            "auto_rules": dict(self._auto_rules),
            "idle_ms": idle_ms,
            "idle_minutes": round(idle_ms / 60000, 1),
            "last_interaction_ts_ms": self._last_interaction_ts_ms,
            "is_night_time": self._is_night_time(),
            "valid_transitions": list(VALID_TRANSITIONS.get(current_mode, set())),
            "ts_ms": now_ms(),
        }

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的模式切换历史。"""
        with self._history_lock:
            recent = self._history[-limit:]
        return [e.as_dict() for e in reversed(recent)]

    def update_auto_rules(self, rules: dict[str, bool]) -> dict[str, bool]:
        """更新自动切换规则开关。"""
        for key, value in rules.items():
            if key in self._auto_rules:
                self._auto_rules[key] = bool(value)
                logger.info("Auto rule updated: %s = %s", key, value)
        return dict(self._auto_rules)

    def update_last_interaction(self, ts_ms: int | None = None) -> None:
        """更新最后互动时间戳。"""
        self._last_interaction_ts_ms = int(ts_ms or now_ms())

    # ── 内部方法 ─────────────────────────────────────────────────────────

    def _get_current_mode(self) -> str:
        """获取当前 sys_mode。"""
        try:
            return self._get_hard_state().sys_mode
        except Exception:
            return "normal"

    def _is_night_time(self) -> bool:
        """判断当前是否为深夜时段 (23:00 - 06:00)。"""
        hour = datetime.datetime.now().hour
        return hour >= NIGHT_HOUR_START or hour < NIGHT_HOUR_END

    def _record_event(self, event: ModeTransitionEvent) -> dict[str, Any]:
        """记录切换事件到内存历史 + 审计日志。"""
        # 内存历史
        with self._history_lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        # 审计日志
        if self._ledger is not None:
            try:
                self._ledger.log_error(
                    request_id=event.event_id,
                    stage="MODE_TRANSITION",
                    reason_code=f"MODE_{event.from_mode.upper()}_TO_{event.to_mode.upper()}",
                    detail=(
                        f"source={event.source}, "
                        f"reason={event.reason}, "
                        f"success={event.success}"
                        + (f", error={event.error}" if event.error else "")
                    ),
                )
            except Exception as exc:
                logger.warning("Failed to log mode transition: %s", exc)

        result = event.as_dict()
        result["success"] = event.success
        return result
