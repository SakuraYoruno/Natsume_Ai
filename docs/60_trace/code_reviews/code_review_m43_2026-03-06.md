# M43 代码审查报告 — 启动编排收口

**里程碑**：M43
**日期**：2026-03-06
**审查人**：AI Coding Agent
**全量测试结果**：2453 passed, 10 skipped, 0 failed
**新增测试**：26 个（T-MIG-VER × 4 + T-MIG-GUARD × 7 + T-SNAP × 7 + T-REPLAY × 8）

---

## 里程碑目标

完成系统启动链路的最后收口，构建"运维链路"的完整闭环：

```
启动时 → StartupHealthSnapshot（快照冻结）
迁移时 → SQLiteMigrationVersionGuard（版本守卫）
运行时 → ReplayReport（回放报告）
```

---

## 变更摘要

### TASK-0431 M43-A：SQLite 迁移目录版本连续性修复

**文件**：`host_brain/audit/migrations/`

**问题**：
1. **重复版本号**：存在两个 `0017_*` 文件（`0017_canon_proposal_id_unique.sql` 和 `0017_canon_proposal_log_unique.sql`），内容不同
2. **版本跳跃**：0018→0021，缺少 0019 和 0020（M41 新增的 `subgraph_trace` 和 `llm_call_log` 表没有对应迁移文件）

**修复**：
- 合并两个 `0017_*` 文件内容到 `0017_canon_proposal_id_unique.sql`（包含 `proposal_id` 和 `change_id` 两个 UNIQUE INDEX）
- 删除 `0017_canon_proposal_log_unique.sql`
- 新增 `0019_subgraph_trace.sql`：补充 M41 新增的 `subgraph_trace` 表和索引
- 新增 `0020_llm_call_log.sql`：补充 M41 新增的 `llm_call_log` 表和索引

**修复后迁移目录版本序列**：0001~0022, 0039, 0040（0023~0038 为历史遗留跳跃，不在本次修复范围内）

---

### TASK-0432 M43-B：SQLiteMigrationVersionGuard — SQLite Schema 版本守卫

**文件**：`host_brain/audit/migration_guard.py`（新增）

**变更内容**：
- 新增 `SQLiteMigrationAsset(dataclass, frozen=True)`：迁移文件资产
- 新增 `SQLiteMigrationGuardReport(dataclass, frozen=True)`：版本守卫报告
- 新增 `SQLiteMigrationVersionGuard` 类，提供：
  - `discover_assets() -> (assets, duplicate_versions)`：发现并排序迁移文件，检测重复版本号
  - `check_version_continuity(assets) -> missing_versions`：检查版本连续性
  - `build_guard_report(request_id, ts_ms) -> SQLiteMigrationGuardReport`：生成版本守卫报告

**报告状态映射**：

| 情况 | status | reason_code |
|------|--------|-------------|
| 目录不存在 | FAILED | MIGRATION_ASSET_MISSING |
| 重复版本号 | FAILED | MIGRATION_SCHEMA_DRIFT |
| 版本跳跃 | DEGRADED | MIGRATION_ASSET_MISSING |
| 一切正常 | READY | None |

**设计原则**：只做静态检查，不执行任何 SQL（SQLite 迁移由 `SQLiteLedger` 自身管理）。

---

### TASK-0433 M43-C：StartupHealthSnapshot — 启动健康快照冻结

**文件**：`host_brain/app/startup_snapshot.py`（新增）

**变更内容**：
- 新增 `StartupHealthSnapshot(dataclass, frozen=True)`：不可变启动快照
- 新增 `build_startup_snapshot(startup_report, migration_guard_report, bond_migration_summary) -> StartupHealthSnapshot`
- 新增 `snapshot_to_dict(snapshot) -> dict`：序列化为可审计字典
- 新增 `verify_snapshot_hash(snapshot) -> bool`：验证快照完整性

**关键设计**：
- **不可变**：`frozen=True`，快照一旦创建不可修改
- **可验证**：`snapshot_hash` 是内容的 SHA256（不含 `snapshot_hash` 字段本身），防止篡改
- **向后兼容**：`migration_guard_report` 和 `bond_migration_summary` 均为可选参数

---

### TASK-0434 M43-D：ReplayReport — request_id 单键回放报告

**文件**：`tools/replay/replay_report.py`（新增）

**变更内容**：
- 新增 `ReplayReport(dataclass, frozen=True)`：不可变回放报告
- 新增 `build_replay_report(observability_report) -> ReplayReport`：从可观测性报告构建回放报告
- 新增 `format_replay_report(report) -> str`：格式化为人类可读文本

**关键字段**：
- `key_events`：`intent_log` + `governor_log` + `skill_exec_log` 的关键事件
- `anomalies`：`REJECT` 决策 + `error_log` 中的异常事件
- `source_summary`：各审计源的事件数量摘要
- `report_hash`：报告内容的 SHA256（不含本字段）

---

## 架构影响分析

### 新增文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `host_brain/audit/migration_guard.py` | ~200 | SQLiteMigrationVersionGuard |
| `host_brain/app/startup_snapshot.py` | ~120 | StartupHealthSnapshot |
| `tools/replay/replay_report.py` | ~180 | ReplayReport |
| `host_brain/audit/migrations/0019_subgraph_trace.sql` | ~20 | M41 subgraph_trace 表迁移 |
| `host_brain/audit/migrations/0020_llm_call_log.sql` | ~20 | M41 llm_call_log 表迁移 |
| `tests/unit/test_startup_orchestration_m43.py` | ~300 | 26 个 M43 测试 |

### 修改文件

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `host_brain/audit/migrations/0017_canon_proposal_id_unique.sql` | 内容合并 | 合并两个 0017 文件，新增 `change_id` UNIQUE INDEX |
| `host_brain/audit/migrations/0017_canon_proposal_log_unique.sql` | **删除** | 内容已合并到 `0017_canon_proposal_id_unique.sql` |

### 可观测性闭环

```
系统启动
    │
    ├─ build_startup_report()
    │       │
    │       └─ build_startup_snapshot()  ← M43-C
    │               │
    │               ├─ boot_status
    │               ├─ service_summary
    │               ├─ migration_guard_report  ← M43-B
    │               └─ snapshot_hash（SHA256 防篡改）
    │
    ├─ SQLiteMigrationVersionGuard.build_guard_report()  ← M43-B
    │       │
    │       ├─ READY：迁移目录版本连续
    │       ├─ DEGRADED：版本跳跃（不阻断启动）
    │       └─ FAILED：重复版本号 / 目录不存在
    │
运行时
    │
    └─ build_request_observability_report()
            │
            └─ build_replay_report()  ← M43-D
                    │
                    ├─ key_events（intent/governor/skill）
                    ├─ anomalies（REJECT + error_log）
                    └─ report_hash（SHA256 防篡改）
```

---

## 测试覆盖

| 测试组 | 测试数 | 覆盖内容 |
|--------|--------|---------|
| T-MIG-VER | 4 | 迁移目录版本连续性（连续/排序/重复/跳跃） |
| T-MIG-GUARD | 7 | SQLiteMigrationVersionGuard 报告生成（READY/FAILED/DEGRADED/缺目录） |
| T-SNAP | 7 | StartupHealthSnapshot（构建/状态/哈希/不可变/可选字段/序列化） |
| T-REPLAY | 8 | ReplayReport（构建/时间线/关键事件/异常/格式化/哈希） |
| **合计** | **26** | |

**全量测试**：2453 passed, 10 skipped, 0 failed（无回归）

---

## 结论

M43 启动编排收口完整落地，所有 5 个任务（TASK-0431~0435）全部 DONE，26 个新增测试全部通过，全量测试无回归。

**M0~M43 里程碑全部完成**，系统具备：
- **执行链路**（M0~M42）：意图解析 → Governor 分层治理 → Skill 执行 → Evidence Plane
- **运维链路**（M43）：启动快照 → 迁移版本守卫 → 回放报告
