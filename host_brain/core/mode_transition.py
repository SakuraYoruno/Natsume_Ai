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

成功切换由 durable HardState journal 形成唯一审计，失败尝试写入 legacy
诊断日志；查询历史优先读取 durable journal。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping

from host_brain.contracts.host_hard_state import (
    HOST_MODE_TRANSITIONS,
    HostHardStateCommitV1,
)
from host_brain.contracts.host_mode_policy import (
    DEFAULT_HOST_MODE_AUTO_RULES,
    HostModePolicyPort,
)
from host_brain.core.ids import now_ms, new_event_id
from host_brain.core.metabolism import (
    ALLOWED_SYS_MODES,
    HardState,
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
VALID_TRANSITIONS = HOST_MODE_TRANSITIONS

# 自动切换规则默认开关
DEFAULT_AUTO_RULES: dict[str, bool] = dict(DEFAULT_HOST_MODE_AUTO_RULES)

# 空闲超时阈值（毫秒）：2 小时
DEFAULT_IDLE_THRESHOLD_MS = 2 * 60 * 60 * 1000

# 深夜时段：23:00 - 06:00
NIGHT_HOUR_START = 23
NIGHT_HOUR_END = 6


def _mode_policy_operation_id(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    observed_at_ms: int,
) -> str:
    canonical = json.dumps(
        {
            "event_type": str(event_type or "").strip(),
            "observed_at_ms": int(observed_at_ms),
            "payload": dict(payload or {}),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "host-mode-policy://sha256/" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _mode_transition_event_id(operation_id: str) -> str:
    return "host-mode-transition://sha256/" + hashlib.sha256(
        str(operation_id).encode("utf-8")
    ).hexdigest()


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
        set_sys_mode: Callable[..., dict[str, Any]],
        history_provider: Callable[..., list[dict[str, Any]]] | None = None,
        exact_history_provider: (
            Callable[..., HostHardStateCommitV1 | None] | None
        ) = None,
        ledger: Any | None = None,
        auto_rules: dict[str, bool] | None = None,
        mode_policy: HostModePolicyPort | None = None,
        idle_threshold_ms: int = DEFAULT_IDLE_THRESHOLD_MS,
    ) -> None:
        self._get_hard_state = get_hard_state
        self._set_sys_mode = set_sys_mode
        self._ledger = ledger
        self._history_provider = history_provider
        self._exact_history_provider = exact_history_provider
        self._mode_policy = mode_policy
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
        if self._mode_policy is not None:
            snapshot = self._mode_policy.snapshot()
            self._auto_rules = dict(snapshot.auto_rules)
            self._last_interaction_ts_ms = int(
                snapshot.last_interaction_ts_ms
            )

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
        event_id: str | None = None,
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
        explicit_event_id = str(event_id or "").strip()
        resolved_event_id = explicit_event_id or new_event_id()
        target = str(target_mode).strip().lower()

        # 获取当前模式
        try:
            current_state = self._get_hard_state()
            from_mode = current_state.sys_mode
        except Exception as exc:
            logger.error("Failed to get current hard_state: %s", exc)
            return self._record_event(ModeTransitionEvent(
                event_id=resolved_event_id,
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
                event_id=resolved_event_id,
                from_mode=from_mode,
                to_mode=target,
                source=source.value,
                reason=reason,
                ts_ms=ts,
                success=False,
                error=f"invalid target mode: {target}",
            ))

        # 相同模式不切换
        if from_mode == target and not explicit_event_id:
            return {
                "event_id": None,
                "from_mode": from_mode,
                "to_mode": target,
                "source": source.value,
                "reason": reason,
                "ts_ms": ts,
                "success": True,
                "error": None,
                "no_change": True,
                "transition_committed": False,
            }

        # 切换路径合法性检查
        allowed_targets = VALID_TRANSITIONS.get(from_mode, set())
        if from_mode != target and target not in allowed_targets:
            return self._record_event(ModeTransitionEvent(
                event_id=resolved_event_id,
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
            result = self._set_sys_mode(
                target,
                expected_from_mode=from_mode,
                event_id=resolved_event_id,
                source=source.value,
                reason=reason,
                ts_ms=ts,
            )
            if (
                not isinstance(result, dict)
                or result.get("to_mode") != target
                or result.get("event_id") != resolved_event_id
                or (
                    not bool(result.get("exact_replay"))
                    and result.get("from_mode") != from_mode
                )
            ):
                raise RuntimeError("MODE_TRANSITION_COMMIT_IDENTITY_INVALID")
            success = True
            error = None
            logger.info(
                "Mode transition: %s → %s (source=%s, reason=%s)",
                from_mode, target, source.value, reason,
            )
        except Exception as exc:
            if from_mode == target and "NOOP_TRANSITION_REJECTED" in str(exc):
                return {
                    "event_id": None,
                    "request_event_id": resolved_event_id,
                    "from_mode": from_mode,
                    "to_mode": target,
                    "source": source.value,
                    "reason": reason,
                    "ts_ms": ts,
                    "success": True,
                    "error": None,
                    "no_change": True,
                    "transition_committed": False,
                }
            success = False
            error = str(exc)
            logger.error("Mode transition failed: %s → %s: %s", from_mode, target, exc)

        canonical_event = ModeTransitionEvent(
            event_id=resolved_event_id,
            from_mode=(
                str(result.get("from_mode")) if success else from_mode
            ),
            to_mode=(str(result.get("to_mode")) if success else target),
            source=source.value,
            reason=reason,
            ts_ms=(int(result.get("ts_ms") or ts) if success else ts),
            success=success,
            error=error,
        )
        if success and bool(result.get("exact_replay")):
            # 精确重放只返回已经持久化的 canonical receipt。不能再次写入
            # 进程内历史或 legacy error_log，否则同一事件会被观测成两次切换。
            recorded = canonical_event.as_dict()
            recorded["success"] = True
            recorded["transition_committed"] = True
        else:
            recorded = self._record_event(
                canonical_event,
                write_legacy_audit=not success,
            )
        if success:
            for key in (
                "revision",
                "event_digest",
                "exact_replay",
                "current_mode",
                "current_revision",
                "current_event_digest",
            ):
                recorded[key] = result.get(key)
        return recorded

    # ── 事件驱动自动切换 ──────────────────────────────────────────────────

    def on_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        operation_id: str | None = None,
        expected_policy_revision: int | None = None,
        observed_at_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """处理外部事件，触发自动模式切换。

        Args:
            event_type: 事件类型
            payload: 事件载荷

        Returns:
            切换结果字典，或 None（不触发切换）
        """
        event_name = str(event_type or "").strip()
        event_payload = dict(payload or {})
        observation_is_frozen = observed_at_ms is not None
        observed = int(
            observed_at_ms if observed_at_ms is not None else now_ms()
        )
        operation = str(operation_id or "").strip() or _mode_policy_operation_id(
            event_name,
            event_payload,
            observed_at_ms=observed,
        )
        policy_snapshot = (
            self._mode_policy.snapshot()
            if self._mode_policy is not None
            else None
        )
        if self._mode_policy is not None:
            existing = self._mode_policy.get_operation(operation)
            if existing is not None:
                stored = dict(existing.event_payload)
                if (
                    existing.event_kind != "EVENT_SUBMITTED"
                    or stored.get("input")
                    != {
                        "event_type": event_name,
                        "payload": event_payload,
                    }
                ):
                    raise RuntimeError("HOST_MODE_POLICY_IDEMPOTENCY_DRIFT")
                prior = stored.get("outcome")
                return dict(prior) if isinstance(prior, Mapping) else None
        rules = (
            dict(policy_snapshot.auto_rules)
            if policy_snapshot is not None
            else dict(self._auto_rules)
        )
        prior_interaction = (
            int(policy_snapshot.last_interaction_ts_ms)
            if policy_snapshot is not None
            else int(self._last_interaction_ts_ms)
        )
        interaction_events = {
            "user_message_received",
            "voice_input_received",
            "wechat_message_received",
        }
        next_interaction = (
            observed if event_name in interaction_events else prior_interaction
        )

        current_mode = self._get_current_mode()
        transition: dict[str, Any] | None = None
        transition_event_id = _mode_transition_event_id(operation)

        # ── 游戏进程检测 → gaming ──
        if event_name == "gaming_process_detected" and rules.get("gaming_detect"):
            if current_mode == "normal":
                process_name = event_payload.get("process_name", "unknown")
                transition = self.switch_mode(
                    "gaming",
                    source=TransitionSource.AUTO_GAMING,
                    reason=f"检测到游戏进程: {process_name}",
                    event_id=transition_event_id,
                )

        # ── 游戏进程退出 → normal ──
        elif event_name == "gaming_process_exited" and rules.get("gaming_exit"):
            if current_mode == "gaming":
                transition = self.switch_mode(
                    "normal",
                    source=TransitionSource.AUTO_GAMING,
                    reason="游戏进程已退出",
                    event_id=transition_event_id,
                )

        # ── 收到用户消息 → 从 sleep 唤醒 ──
        elif event_name in interaction_events:
            if current_mode == "sleep" and rules.get("wakeup_on_msg"):
                channel = event_payload.get("channel", event_name)
                transition = self.switch_mode(
                    "normal",
                    source=TransitionSource.AUTO_WAKEUP,
                    reason=f"收到用户消息，从 sleep 唤醒 (channel={channel})",
                    event_id=transition_event_id,
                )

        # ── 空闲超时 → sleep ──
        elif event_name == "idle_check":
            if current_mode == "normal" and rules.get("idle_to_sleep"):
                idle_ms = observed - prior_interaction
                if (
                    idle_ms >= self._idle_threshold_ms
                    and self._is_night_time(
                        observed_at_ms=(
                            observed if observation_is_frozen else None
                        )
                    )
                ):
                    transition = self.switch_mode(
                        "sleep",
                        source=TransitionSource.AUTO_IDLE,
                        reason=f"空闲 {idle_ms // 60000} 分钟 + 深夜时段，自动进入 sleep",
                        event_id=transition_event_id,
                    )

        # ── 早间唤醒 ──
        elif event_name == "morning_check":
            if current_mode == "sleep" and rules.get("morning_wakeup"):
                observed_time = (
                    datetime.datetime.fromtimestamp(observed / 1000)
                    if observation_is_frozen
                    else datetime.datetime.now()
                )
                if 6 <= observed_time.hour < 10:
                    transition = self.switch_mode(
                        "normal",
                        source=TransitionSource.AUTO_WAKEUP,
                        reason=f"早间唤醒 ({observed_time.strftime('%H:%M')})",
                        event_id=transition_event_id,
                    )

        if self._mode_policy is not None:
            expected = (
                int(expected_policy_revision)
                if expected_policy_revision is not None
                else int(policy_snapshot.revision)
            )
            commit = self._mode_policy.record_event(
                operation_id=operation,
                expected_revision=expected,
                event_kind="EVENT_SUBMITTED",
                input_payload={
                    "event_type": event_name,
                    "payload": event_payload,
                },
                event_payload={
                    "input": {
                        "event_type": event_name,
                        "payload": event_payload,
                    },
                    "outcome": transition,
                },
                last_interaction_ts_ms=next_interaction,
                observed_at_ms=observed,
            )
            self._auto_rules = dict(commit.snapshot.auto_rules)
            self._last_interaction_ts_ms = int(
                commit.snapshot.last_interaction_ts_ms
            )
        else:
            self._auto_rules = rules
            self._last_interaction_ts_ms = next_interaction
        return transition

    # ── 查询接口 ─────────────────────────────────────────────────────────

    def get_current_status(self) -> dict[str, Any]:
        """获取当前模式状态 + 自动切换规则。"""
        current_mode = self._get_current_mode()
        policy_snapshot = (
            self._mode_policy.snapshot()
            if self._mode_policy is not None
            else None
        )
        rules = (
            dict(policy_snapshot.auto_rules)
            if policy_snapshot is not None
            else dict(self._auto_rules)
        )
        last_interaction = (
            int(policy_snapshot.last_interaction_ts_ms)
            if policy_snapshot is not None
            else int(self._last_interaction_ts_ms)
        )
        idle_ms = now_ms() - last_interaction
        return {
            "sys_mode": current_mode,
            "auto_rules": rules,
            "idle_ms": idle_ms,
            "idle_minutes": round(idle_ms / 60000, 1),
            "last_interaction_ts_ms": last_interaction,
            "policy_revision": (
                policy_snapshot.revision
                if policy_snapshot is not None
                else None
            ),
            "policy_event_digest": (
                policy_snapshot.event_digest
                if policy_snapshot is not None
                else None
            ),
            "is_night_time": self._is_night_time(),
            "valid_transitions": list(VALID_TRANSITIONS.get(current_mode, set())),
            "ts_ms": now_ms(),
        }

    def get_mode_policy_operation(self, operation_id: str) -> Any | None:
        """Reopen one exact durable policy operation for effect reconciliation."""

        operation = str(operation_id or "").strip()
        if not operation:
            raise RuntimeError("HOST_MODE_POLICY_OPERATION_ID_INVALID")
        if self._mode_policy is None:
            raise RuntimeError("HOST_MODE_POLICY_DURABLE_AUTHORITY_UNAVAILABLE")
        return self._mode_policy.get_operation(operation)

    def get_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近的模式切换历史。"""
        with self._history_lock:
            recent = self._history[-limit:]
        local = [e.as_dict() for e in reversed(recent)]
        if self._history_provider is None:
            return local
        durable = list(self._history_provider(limit=limit))
        merged = {str(item.get("event_id") or ""): dict(item) for item in local}
        for item in durable:
            merged[str(item.get("event_id") or "")] = dict(item)
        return sorted(
            merged.values(),
            key=lambda item: int(item.get("ts_ms") or 0),
            reverse=True,
        )[: max(0, int(limit))]

    def reopen_transition_commit(
        self,
        *,
        event_id: str,
        target_mode: str,
        source: str,
        reason: str,
    ) -> HostHardStateCommitV1 | None:
        """Reopen an exact durable transition without executing it again."""

        provider = self._exact_history_provider
        if provider is None:
            raise RuntimeError("HARD_STATE_EXACT_HISTORY_UNAVAILABLE")
        commit = provider(
            event_id=event_id,
            target_mode=target_mode,
            source=source,
            reason=reason,
        )
        if commit is not None and not isinstance(commit, HostHardStateCommitV1):
            raise RuntimeError("HARD_STATE_EXACT_HISTORY_INVALID")
        return commit

    def update_auto_rules(
        self,
        rules: dict[str, bool],
        *,
        operation_id: str | None = None,
        expected_policy_revision: int | None = None,
        observed_at_ms: int | None = None,
    ) -> dict[str, bool]:
        """更新自动切换规则开关。"""
        if self._mode_policy is not None:
            snapshot = self._mode_policy.snapshot()
            observed = int(
                observed_at_ms if observed_at_ms is not None else now_ms()
            )
            operation = (
                str(operation_id or "").strip()
                or _mode_policy_operation_id(
                    "auto_rules.update",
                    dict(rules or {}),
                    observed_at_ms=observed,
                )
            )
            commit = self._mode_policy.update_auto_rules(
                rules,
                operation_id=operation,
                expected_revision=(
                    int(expected_policy_revision)
                    if expected_policy_revision is not None
                    else snapshot.revision
                ),
                observed_at_ms=observed,
            )
            self._auto_rules = dict(commit.snapshot.auto_rules)
            self._last_interaction_ts_ms = int(
                commit.snapshot.last_interaction_ts_ms
            )
            return dict(commit.event.auto_rules)
        for key, value in rules.items():
            if key in self._auto_rules:
                self._auto_rules[key] = bool(value)
                logger.info("Auto rule updated: %s = %s", key, value)
        return dict(self._auto_rules)

    def update_last_interaction(
        self,
        ts_ms: int | None = None,
        *,
        operation_id: str | None = None,
    ) -> None:
        """更新最后互动时间戳。"""
        observed = int(ts_ms if ts_ms is not None else now_ms())
        if self._mode_policy is not None:
            snapshot = self._mode_policy.snapshot()
            operation = (
                str(operation_id or "").strip()
                or _mode_policy_operation_id(
                    "interaction.record",
                    {"ts_ms": observed},
                    observed_at_ms=observed,
                )
            )
            commit = self._mode_policy.record_event(
                operation_id=operation,
                expected_revision=snapshot.revision,
                event_kind="INTERACTION_RECORDED",
                input_payload={"ts_ms": observed},
                event_payload={"interaction_recorded": True},
                last_interaction_ts_ms=observed,
                observed_at_ms=observed,
            )
            self._last_interaction_ts_ms = int(
                commit.snapshot.last_interaction_ts_ms
            )
            self._auto_rules = dict(commit.snapshot.auto_rules)
            return
        self._last_interaction_ts_ms = observed

    # ── 内部方法 ─────────────────────────────────────────────────────────

    def _get_current_mode(self) -> str:
        """获取当前 sys_mode。"""
        try:
            return self._get_hard_state().sys_mode
        except Exception as exc:
            logger.error("HardState mode authority unavailable: %s", exc)
            raise RuntimeError("HARD_STATE_MODE_AUTHORITY_UNAVAILABLE") from exc

    def _is_night_time(self, *, observed_at_ms: int | None = None) -> bool:
        """判断当前是否为深夜时段 (23:00 - 06:00)。"""
        moment = (
            datetime.datetime.fromtimestamp(int(observed_at_ms) / 1000)
            if observed_at_ms is not None
            else datetime.datetime.now()
        )
        hour = moment.hour
        return hour >= NIGHT_HOUR_START or hour < NIGHT_HOUR_END

    def _record_event(
        self,
        event: ModeTransitionEvent,
        *,
        write_legacy_audit: bool = True,
    ) -> dict[str, Any]:
        """记录切换事件到内存历史 + 审计日志。"""
        # 内存历史
        with self._history_lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        # 审计日志
        if write_legacy_audit and self._ledger is not None:
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
        result["transition_committed"] = bool(event.success)
        return result
