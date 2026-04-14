# Skill-First Humanoid Execution Plan Stage 7N (2026-04-13)

## 目标

- 在 Stage7L/7M 的“病灶热区 + 演化趋势”之下继续下钻。
- 让总览不仅知道“哪里在出问题”，还能知道“最可能是哪套底层系统在导致病灶持续出现”。

## 范围

- 后端新增：
  - `GET /obs/input/humanoid-loop-source-correlation`
- 前端增强：
  - `DigitalLifeOverview` 新增“源头归因 / 系统关联”面板

## 后端设计

- 输入来源：
  - `voice_event_log`
  - `channel_io_log`
  - `governor_log`
  - `error_log`
  - `skill_exec_log`
  - `llm_node_call_log`
- 计算路径：
  - 复用 `humanoid-loop-hotspots` 作为上游病灶输入
  - 聚合 Gateway / Governor / Errors / Subsystems / Services 五类源头快照
  - 按热点根因为不同源头系统打分
  - 产出 primary source 与 source cards

## 输出结构

- `summary`
- `primary_source_system`
- `primary_source_label`
- `items[]`
  - `source_id`
  - `source_label`
  - `source_kind`
  - `confidence_pct`
  - `impact_level`
  - `source_status`
  - `correlated_hotspots[]`
  - `evidence[]`
  - `narrative`
  - `operator_focus`
  - `recommended_view_path`
  - `recommended_view_label`

## 验收标准

1. `EXECUTION_FAILED` 能稳定归因到 `errors / subsystems / services` 中的主源头。
2. `COMPLETED_WITHOUT_HUMANOID_TOUCH / ACK_PATH_MISSING / PROGRESS_PATH_MISSING` 能稳定归因到 `gateway`。
3. `GOVERNOR_BLOCKED` 能稳定归因到 `governor`。
4. 前端总览能展示 primary source、证据 chips、关联热点与钻取入口。
