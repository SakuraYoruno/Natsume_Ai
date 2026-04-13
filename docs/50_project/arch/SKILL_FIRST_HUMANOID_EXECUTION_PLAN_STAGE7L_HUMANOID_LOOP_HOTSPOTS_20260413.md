# Skill-First Humanoid Execution Plan Stage 7L (2026-04-13)

## 目标

- 在 Stage7K 的 request 级根因卡之上，再向上收一层“系统级病灶热区”。
- 让数字生命体总览先告诉操作者“最近主要坏在哪个器官系统”，再下钻到具体 request 和修复动作。

## 范围

- 后端新增：
  - `GET /obs/input/humanoid-loop-hotspots`
- 前端增强：
  - `DigitalLifeOverview` 新增“病灶热区 / 器官聚类”面板

## 后端设计

- 输入来源：
  - 复用 `GET /obs/input/humanoid-loop-diagnostics` 的 diagnostics 列表
- 聚类维度：
  - `organ_system`
  - `root_cause_code`
  - `suspected_breakpoint`
- 目标输出：
  - 器官系统标签
  - request_count / share_pct
  - narrative
  - operator_focus
  - representative_requests[]
  - recommended_view_path

## 器官映射

- `ACK_PATH_MISSING` / `COMPLETED_WITHOUT_HUMANOID_TOUCH`
  - `language_feedback` / `语言回执系统`
- `PROGRESS_PATH_MISSING`
  - `somatic_progress` / `体感进度系统`
- `EXECUTION_FAILED`
  - `motor_execution` / `运动执行系统`
- `DEVIATION_NOT_RECOVERED`
  - `immune_realign` / `免疫纠偏系统`
- `GOVERNOR_BLOCKED`
  - `prefrontal_governor` / `前额叶门控系统`

## 验收标准

1. hotspots 端点能把多条 request 级异常稳定聚成系统级病灶。
2. 前端总览能先看到主病灶，再下钻到 request。
3. 聚类结果对外可解释，不能只是“数量排名”，还要带器官叙事与操作者关注点。
