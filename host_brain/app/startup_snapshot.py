"""启动健康快照冻结（M43-C）。

在系统启动时将 build_startup_report() 的结果冻结为不可变快照，
供后续回放/演练/CI 引用。快照包含启动时间戳、服务状态摘要、迁移版本守卫报告。

设计原则：
  - 不可变（frozen=True）：快照一旦创建不可修改
  - 可验证（snapshot_hash）：SHA256 哈希确保快照完整性
  - 向后兼容：migration_guard_report 和 bond_migration_summary 均为可选
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any

from host_brain.core.ids import now_ms


_current_snapshot_lock = threading.Lock()
_current_snapshot: "StartupHealthSnapshot | None" = None


@dataclass(frozen=True)
class StartupHealthSnapshot:
    """启动健康快照（不可变）。

    Attributes:
        request_id: 启动请求 ID
        ts_ms: 快照创建时间戳（毫秒）
        boot_status: 启动状态（READY / DEGRADED / ACCEPTANCE_PENDING_EXTERNAL）
        service_summary: 服务状态摘要（来自 startup_report["summary"]）
        migration_guard_report: SQLite 迁移版本守卫报告（可选）
        bond_migration_summary: Bond 迁移摘要（可选）
        snapshot_hash: 快照内容的 SHA256 哈希（不含本字段）
    """

    request_id: str
    ts_ms: int
    boot_status: str
    service_summary: dict[str, Any]
    migration_guard_report: dict[str, Any] | None
    bond_migration_summary: dict[str, Any] | None
    snapshot_hash: str


def build_startup_snapshot(
    startup_report: dict[str, Any],
    *,
    migration_guard_report: dict[str, Any] | None = None,
    bond_migration_summary: dict[str, Any] | None = None,
    ts_ms: int | None = None,
) -> StartupHealthSnapshot:
    """从启动报告构建不可变快照。

    Args:
        startup_report: build_startup_report() 的返回值
        migration_guard_report: SQLite 迁移版本守卫报告（可选）
        bond_migration_summary: Bond 迁移摘要（可选）
        ts_ms: 快照时间戳（可选，默认当前时间）

    Returns:
        StartupHealthSnapshot（不可变）
    """
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    request_id = str(startup_report.get("request_id") or "").strip() or f"snap::{now_value}"
    summary = dict(startup_report.get("summary") or {})
    boot_status = str(summary.get("boot_status") or "DEGRADED").strip().upper()

    # 构建快照内容（不含 snapshot_hash）
    content: dict[str, Any] = {
        "request_id": request_id,
        "ts_ms": now_value,
        "boot_status": boot_status,
        "service_summary": summary,
        "migration_guard_report": migration_guard_report,
        "bond_migration_summary": bond_migration_summary,
    }

    # 计算 SHA256 哈希
    snapshot_hash = _sha256_dict(content)

    return StartupHealthSnapshot(
        request_id=request_id,
        ts_ms=now_value,
        boot_status=boot_status,
        service_summary=summary,
        migration_guard_report=migration_guard_report,
        bond_migration_summary=bond_migration_summary,
        snapshot_hash=snapshot_hash,
    )


def snapshot_to_dict(snapshot: StartupHealthSnapshot) -> dict[str, Any]:
    """将快照序列化为可审计的字典。

    Args:
        snapshot: StartupHealthSnapshot 实例

    Returns:
        包含所有字段的字典（含 snapshot_hash）
    """
    return {
        "request_id": snapshot.request_id,
        "ts_ms": snapshot.ts_ms,
        "boot_status": snapshot.boot_status,
        "service_summary": dict(snapshot.service_summary),
        "migration_guard_report": snapshot.migration_guard_report,
        "bond_migration_summary": snapshot.bond_migration_summary,
        "snapshot_hash": snapshot.snapshot_hash,
    }


def set_current_startup_snapshot(snapshot: StartupHealthSnapshot) -> None:
    """Publish the immutable snapshot produced by the startup source job."""

    if not isinstance(snapshot, StartupHealthSnapshot):
        raise TypeError("snapshot must be a StartupHealthSnapshot")
    global _current_snapshot
    with _current_snapshot_lock:
        _current_snapshot = snapshot


def peek_current_startup_snapshot() -> StartupHealthSnapshot | None:
    """Peek the startup snapshot; never rebuild probes or initialize stores."""

    with _current_snapshot_lock:
        return _current_snapshot


def verify_snapshot_hash(snapshot: StartupHealthSnapshot) -> bool:
    """验证快照哈希是否与内容一致。

    Args:
        snapshot: StartupHealthSnapshot 实例

    Returns:
        True 表示哈希一致，False 表示快照已被篡改
    """
    content: dict[str, Any] = {
        "request_id": snapshot.request_id,
        "ts_ms": snapshot.ts_ms,
        "boot_status": snapshot.boot_status,
        "service_summary": dict(snapshot.service_summary),
        "migration_guard_report": snapshot.migration_guard_report,
        "bond_migration_summary": snapshot.bond_migration_summary,
    }
    expected_hash = _sha256_dict(content)
    return snapshot.snapshot_hash == expected_hash


def _sha256_dict(data: dict[str, Any]) -> str:
    """计算字典内容的 SHA256 哈希（JSON 序列化，key 排序）。"""
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
