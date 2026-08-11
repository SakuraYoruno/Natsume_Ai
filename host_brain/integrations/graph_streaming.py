"""Phase-Locked Streaming 核心实现（M38-A）。

实现 Phase-Locked Streaming 三类流（ModelPlane.md §10 Rule 5）：
- Presence Stream（在场流）：非权威，只向 UI 单向输出，不允许作为下游节点输入
- Commit Stream（提交流）：权威，只在 S1 完成并经过 Governor 后产生
- Render Stream（渲染流）：只能读取 Commit Stream 对应的快照

设计原则（见 docs/15_theory/Theory.md 的提交与表达边界）：
- Presence 事件不能流入 Commit/Render
- Render 不能消费未提交状态（commit_snapshot 为 None 时抛出 PhaseViolationError）
- Commit 只在 committed_route_token = GOV_OK_RENDER 时产生
- PhaseLockGuard 验证 Render 前置条件（intent_commit 未变化）
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from host_brain.integrations.graph_contract import (
    STREAM_EVENT_CHUNK,
    STREAM_EVENT_NODE_END,
    STREAM_EVENT_NODE_START,
    STREAM_EVENT_PROGRESS,
    STREAM_EVENT_RENDER_PRECONDITION_FAILED,
    STREAM_PHASE_COMMIT,
    STREAM_PHASE_PRESENCE,
    STREAM_PHASE_RENDER,
    RENDER_ALLOWED_FIELDS,
    RENDER_ALLOWED_FIELD_PREFIX,
    RENDER_GUARD_STATUS_PASSED,
    RENDER_GUARD_STATUS_FAILED,
)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class PhaseViolationError(Exception):
    """Phase-Lock 违规异常：在不满足前置条件时尝试进入下一阶段。"""
    pass


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class StreamPhase(str, Enum):
    """流阶段枚举。"""
    PRESENCE = STREAM_PHASE_PRESENCE
    COMMIT = STREAM_PHASE_COMMIT
    RENDER = STREAM_PHASE_RENDER


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class PresenceEvent:
    """在场流事件（非权威，只向 UI 输出）。"""
    phase: str = STREAM_PHASE_PRESENCE
    node_id: str = ""
    event_type: str = STREAM_EVENT_NODE_START
    chunk: str = ""
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "node_id": self.node_id,
            "event_type": self.event_type,
            "chunk": self.chunk,
            "ts_ms": self.ts_ms,
        }


@dataclass
class CommitSnapshot:
    """提交快照（权威，只在 GOV_OK_RENDER 时产生）。"""
    intent_commit: str = ""
    s1_output_hash: str = ""
    locked_fields: dict[str, Any] = field(default_factory=dict)
    render_input_snapshot_ref: str = ""
    committed_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_commit": self.intent_commit,
            "s1_output_hash": self.s1_output_hash,
            "locked_fields": self.locked_fields,
            "render_input_snapshot_ref": self.render_input_snapshot_ref,
            "committed_at_ms": self.committed_at_ms,
        }


@dataclass
class RenderEvent:
    """渲染流事件（只能消费 Commit 快照）。"""
    phase: str = STREAM_PHASE_RENDER
    snapshot_ref: str = ""
    chunk: str = ""
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "snapshot_ref": self.snapshot_ref,
            "chunk": self.chunk,
            "ts_ms": self.ts_ms,
        }


# ---------------------------------------------------------------------------
# 发射器
# ---------------------------------------------------------------------------

class PresenceStreamEmitter:
    """在场流发射器（非权威，只向 UI 单向输出）。

    Presence 事件不允许作为下游节点输入，不包含 committed_* 字段。
    """

    def emit(
        self,
        *,
        node_id: str,
        event_type: str = STREAM_EVENT_NODE_START,
        chunk: str = "",
    ) -> PresenceEvent:
        """发射一条在场流事件。

        Args:
            node_id: 当前节点 ID
            event_type: 事件类型（NODE_START / NODE_END / CHUNK / PROGRESS）
            chunk: 临时 narrative chunk（可为空）

        Returns:
            PresenceEvent（phase = PRESENCE）
        """
        return PresenceEvent(
            phase=STREAM_PHASE_PRESENCE,
            node_id=str(node_id or ""),
            event_type=str(event_type or STREAM_EVENT_NODE_START),
            chunk=str(chunk or ""),
            ts_ms=int(time.time() * 1000),
        )


class CommitStreamEmitter:
    """提交流发射器（权威，只在 GOV_OK_RENDER 时产生）。

    只有 committed_route_token = GOV_OK_RENDER 时才允许生成 CommitSnapshot。
    """

    def commit(
        self,
        *,
        committed_route_token: str | None,
        payload: dict[str, Any],
        request_id: str = "",
    ) -> CommitSnapshot:
        """生成提交快照。

        Args:
            committed_route_token: Governor 已提交的路由令牌
            payload: 当前节点输出 payload
            request_id: 请求 ID（用于生成 snapshot_ref）

        Returns:
            CommitSnapshot

        Raises:
            PhaseViolationError: committed_route_token 不是 GOV_OK_RENDER 时
        """
        token = str(committed_route_token or "").strip()
        if token != "GOV_OK_RENDER":
            raise PhaseViolationError(
                f"Commit Stream 只允许在 committed_route_token = GOV_OK_RENDER 时产生，"
                f"当前 token = {token!r}"
            )

        # 计算 s1_output_hash（payload 的 SHA-256）
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        s1_output_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

        # 提取 intent_commit（从 payload 中获取）
        intent_commit = str(payload.get("intent_commit") or payload.get("intent_draft") or "").strip()

        # 锁定字段（不允许 Render 修改的字段）
        locked_fields = {
            k: v for k, v in payload.items()
            if k in {"intent_commit", "intent_draft", "s1_output", "route_decision"}
        }

        # 生成 render_input_snapshot_ref
        committed_at_ms = int(time.time() * 1000)
        snapshot_ref = f"snapshot::{request_id}::{committed_at_ms}"

        return CommitSnapshot(
            intent_commit=intent_commit,
            s1_output_hash=s1_output_hash,
            locked_fields=locked_fields,
            render_input_snapshot_ref=snapshot_ref,
            committed_at_ms=committed_at_ms,
        )


class RenderStreamEmitter:
    """渲染流发射器（只能消费 Commit 快照）。

    Render 不能消费未提交状态，commit_snapshot 为 None 时抛出 PhaseViolationError。
    """

    def render(
        self,
        *,
        commit_snapshot: CommitSnapshot | dict[str, Any] | None,
        chunk: str = "",
    ) -> RenderEvent:
        """生成渲染流事件。

        Args:
            commit_snapshot: 提交快照（CommitSnapshot 或字典）
            chunk: 渲染 chunk

        Returns:
            RenderEvent

        Raises:
            PhaseViolationError: commit_snapshot 为 None 时
        """
        if commit_snapshot is None:
            raise PhaseViolationError(
                "Render Stream 只能消费 Commit 快照，commit_snapshot 为 None"
            )

        # 支持字典和 CommitSnapshot 两种输入
        if isinstance(commit_snapshot, dict):
            snapshot_ref = str(commit_snapshot.get("render_input_snapshot_ref") or "")
        else:
            snapshot_ref = str(commit_snapshot.render_input_snapshot_ref or "")

        return RenderEvent(
            phase=STREAM_PHASE_RENDER,
            snapshot_ref=snapshot_ref,
            chunk=str(chunk or ""),
            ts_ms=int(time.time() * 1000),
        )


# ---------------------------------------------------------------------------
# Phase-Lock 守卫
# ---------------------------------------------------------------------------

class PhaseLockGuard:
    """分相锁守卫：验证 Render 前置条件。

    确保 Render Stream 只能在 Commit 快照存在且 intent_commit 未变化时开始。
    """

    def validate_render_precondition(
        self,
        *,
        commit_snapshot: CommitSnapshot | dict[str, Any] | None,
        current_intent_commit: str | None,
    ) -> bool:
        """验证 Render 前置条件。

        Args:
            commit_snapshot: 提交快照（CommitSnapshot 或字典）
            current_intent_commit: 当前 intent_commit（来自最新 payload）

        Returns:
            True：前置条件满足（可以开始渲染）
            False：前置条件不满足（intent_commit 已变化或 commit_snapshot 为 None）
        """
        if commit_snapshot is None:
            return False

        # 提取 commit_snapshot 中的 intent_commit
        if isinstance(commit_snapshot, dict):
            snapshot_intent = str(commit_snapshot.get("intent_commit") or "")
        else:
            snapshot_intent = str(commit_snapshot.intent_commit or "")

        current = str(current_intent_commit or "")

        # intent_commit 必须与快照一致（未变化）
        return snapshot_intent == current

    def build_precondition_failed_event(
        self,
        *,
        node_id: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """构建 Render 前置条件失败的降级通知事件（写入 presence_events）。

        Args:
            node_id: 当前节点 ID
            reason: 失败原因

        Returns:
            PresenceEvent 字典（event_type = RENDER_PRECONDITION_FAILED）
        """
        return {
            "phase": STREAM_PHASE_PRESENCE,
            "node_id": str(node_id or ""),
            "event_type": STREAM_EVENT_RENDER_PRECONDITION_FAILED,
            "chunk": str(reason or "Render 前置条件不满足"),
            "ts_ms": int(time.time() * 1000),
        }


# ---------------------------------------------------------------------------
# M40-B：RenderGuard — 渲染输出授权守卫
# ---------------------------------------------------------------------------

@dataclass
class RenderGuardResult:
    """渲染守卫结果：RenderGuard.validate_render_output() 返回值。"""
    passed: bool
    violated_fields: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violated_fields": list(self.violated_fields),
            "reason": self.reason,
        }


class RenderGuard:
    """渲染输出授权守卫（M40-B）。

    验证 render 输出只修改允许的派生字段，不允许修改 locked_fields。

    设计原则（见 docs/15_theory/Theory.md 的提交与表达边界）：
    - render 输出只能修改 RENDER_ALLOWED_FIELDS 中的字段
    - render 输出只能修改 display_ 前缀的字段
    - 不允许修改 locked_fields（来自 commit_snapshot）
    - 不允许修改 intent_commit、s1_output_hash
    """

    # 绝对禁止修改的字段（无论 locked_fields 如何）
    _FORBIDDEN_FIELDS: frozenset[str] = frozenset({
        "intent_commit",
        "s1_output_hash",
        "committed_route_token",
        "committed_route_token_detail",
    })

    def is_allowed_render_field(self, field_name: str) -> bool:
        """判断字段是否允许被 render 输出修改。

        Args:
            field_name: 字段名

        Returns:
            True：允许修改；False：不允许修改
        """
        name = str(field_name or "")
        # 允许的固定字段集合
        if name in RENDER_ALLOWED_FIELDS:
            return True
        # 允许的前缀字段
        if name.startswith(RENDER_ALLOWED_FIELD_PREFIX):
            return True
        return False

    def validate_render_output(
        self,
        *,
        render_output: dict[str, Any] | None,
        commit_snapshot: "CommitSnapshot | dict[str, Any] | None",
    ) -> RenderGuardResult:
        """验证 render 输出只修改允许的派生字段。

        Args:
            render_output: render 节点的输出字典
            commit_snapshot: 提交快照（CommitSnapshot 或字典）

        Returns:
            RenderGuardResult（passed=True 或 passed=False + violated_fields）
        """
        # commit_snapshot 为 None：无法校验，fail closed
        if commit_snapshot is None:
            return RenderGuardResult(
                passed=False,
                violated_fields=[],
                reason="commit_snapshot 为 None，无法校验 render 输出",
            )

        # render_output 为空：无修改，通过
        if not render_output:
            return RenderGuardResult(
                passed=True,
                violated_fields=[],
                reason="render 输出为空，校验通过",
            )

        # 提取 locked_fields（来自 commit_snapshot）
        if isinstance(commit_snapshot, dict):
            locked_fields: dict[str, Any] = dict(commit_snapshot.get("locked_fields") or {})
        else:
            locked_fields = dict(getattr(commit_snapshot, "locked_fields", {}) or {})

        violated: list[str] = []

        for field_name in render_output:
            name = str(field_name or "")

            # 绝对禁止修改的字段
            if name in self._FORBIDDEN_FIELDS:
                violated.append(name)
                continue

            # locked_fields 中的字段不允许修改
            if name in locked_fields:
                violated.append(name)
                continue

            # 不在允许列表中的字段
            if not self.is_allowed_render_field(name):
                violated.append(name)

        if violated:
            return RenderGuardResult(
                passed=False,
                violated_fields=violated,
                reason=f"render 输出包含 {len(violated)} 个违规字段: {', '.join(violated)}",
            )

        return RenderGuardResult(
            passed=True,
            violated_fields=[],
            reason="render 输出校验通过，所有字段均为允许的派生字段",
        )
