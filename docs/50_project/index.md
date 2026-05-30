受众：MASTER
标题：Flow 资产追溯索引（按环境）

版本：v0.4
日期：2026-04-19
定位：记录 flows/active.pointer 指向的“当前生效 Flow 导出物”，以及最小变更摘要；用于与 active.pointer 形成双锚点，支撑 T-Flow-02（索引一致性）回归口径。

当前代码说明（2026-04-19）：
- `flows/active.pointer` 当前仍是占位型 Flow 资产指针。
- 现行 `/v1/chat` 主链不直接由这些导出 Flow 驱动；当前运行事实请看 `README.md`、`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` 与 `docs/60_trace/flows/index.md`。

引用：
- 需求：FR-FLOW-001
- 任务：TASK-0002（以及后续 Flow 相关任务）
- 测试：T-Flow-01、T-Flow-02

## 5分钟入门（给新成员）

1. 先读：`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`（当前代码入口）和 `docs/50_project/arch/dataflow_report.md`（数据回流）。
2. 再读：`docs/50_project/PROJECT_BRIEF.md`（项目目标）与 `docs/50_project/PROJECT_TASKS.md`（当前任务入口）。
3. 查本文件第 2 节（当前事实/历史归档）确认主链依据，再对照 `docs/60_trace/flows/index.md` 与 `flows/active.pointer` 核验环境绑定。

---

## 0. 相关入口（架构/链路）

- 当前代码架构：`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`
- 核心模块详解：`docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`
- 数据流全景：`docs/50_project/arch/dataflow_report.md`
- 仓库总览入口：`README.md`

---

## 1. 一致性规则（必须长期保持）
1) 本文件按 dev/staging/prod 三环境分别给出“当前生效导出物”。
2) 本文件每个环境的 current_export 必须与 flows/active.pointer 对应键的值完全一致（字符串级一致）。
3) 允许额外记录：导出时间、简短摘要、关联的 FR/TASK/T；但不得把“当前生效导出物”写成与 active.pointer 不同的路径。

---

## 2. 当前事实 / 历史归档标签（2026-05-30）

### 2.1 当前事实（可用于主链判断）
- `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`：当前代码事实总览（默认入口）。
- `docs/50_project/arch/CORE_MODULES_DETAILED_REFERENCE.md`：核心模块定位与主链覆盖。
- `docs/50_project/arch/dataflow_report.md`：数据回流与主链时序说明。
- `docs/50_project/PROJECT_BRIEF.md` / `docs/50_project/PROJECT_TASKS.md`：Project 侧事实协作入口与任务分发口径。

### 2.2 历史归档（回溯读法）
- `docs/50_project/arch/` 下除上面 3 个事实入口外，当前目录中多数文档属于历史计划、阶段总结、验收清单或任务沉淀；需按时间先后查证，默认不作为主链决策依据。
- `docs/50_project/guides/` 与 `docs/50_project/phases/`：流程化实践文档，包含过往节点评审与验收记录；用于回溯与复用，不作为当前唯一入口。
- `flows/active.pointer`、`flows/exports/`：仅作为配置资产版本锚点，需与本索引一起联合核验。

## 3. 当前生效版本（应与 flows/active.pointer 完全一致）

### 3.1 dev
- current_export：flows/exports/2026-02-28_dev_skeleton/flow.json
- updated_ts：2026-02-28
- change_summary：bootstrap skeleton flow（dev）
- refs：FR-FLOW-001；TASK-0002；T-Flow-01~02

### 3.2 staging
- current_export：flows/exports/2026-02-28_staging_skeleton/flow.json
- updated_ts：2026-02-28
- change_summary：bootstrap skeleton flow（staging）
- refs：FR-FLOW-001；TASK-0002；T-Flow-01~02

### 3.3 prod
- current_export：flows/exports/2026-02-28_prod_skeleton/flow.json
- updated_ts：2026-02-28
- change_summary：bootstrap skeleton flow（prod）
- refs：FR-FLOW-001；TASK-0002；T-Flow-01~02

---

## 4. 变更记录（可选但建议）
- 2026-03-04：dev/staging/prod：补全 current_export 与 active.pointer 一致性占位信息（refs：TASK-0002 / T-Flow-01~02）
- 2026-03-18：补充“相关入口（架构/链路）”导航，联动当前代码架构与数据流文档。
- 2026-04-19：补充当前代码说明，明确 `/v1/chat` 主链不直接由导出 Flow 驱动。
