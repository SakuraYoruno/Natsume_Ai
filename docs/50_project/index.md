受众：MASTER
标题：Flow 资产追溯索引（按环境）

版本：v0.1
定位：记录 flows/active.pointer 指向的“当前生效 Flow 导出物”，以及最小变更摘要；用于与 active.pointer 形成双锚点，支撑 T-Flow-02（索引一致性）回归口径。

引用：
- 需求：FR-FLOW-001
- 任务：TASK-0002（以及后续 Flow 相关任务）
- 测试：T-Flow-01、T-Flow-02

---

## 1. 一致性规则（必须长期保持）
1) 本文件按 dev/staging/prod 三环境分别给出“当前生效导出物”。
2) 本文件每个环境的 current_export 必须与 flows/active.pointer 对应键的值完全一致（字符串级一致）。
3) 允许额外记录：导出时间、简短摘要、关联的 FR/TASK/T；但不得把“当前生效导出物”写成与 active.pointer 不同的路径。

---

## 2. 当前生效版本（应与 flows/active.pointer 完全一致）

### 2.1 dev
- current_export：<填入 flows/exports 下的相对路径或文件名>
- updated_ts：<YYYY-MM-DD>
- change_summary：<一句话，≤80 字>
- refs：FR-FLOW-001；TASK-0002；T-Flow-01~02

### 2.2 staging
- current_export：<填入 flows/exports 下的相对路径或文件名>
- updated_ts：<YYYY-MM-DD>
- change_summary：<一句话，≤80 字>
- refs：FR-FLOW-001；TASK-0002；T-Flow-01~02

### 2.3 prod
- current_export：<填入 flows/exports 下的相对路径或文件名>
- updated_ts：<YYYY-MM-DD>
- change_summary：<一句话，≤80 字>
- refs：FR-FLOW-001；TASK-0002；T-Flow-01~02

---

## 3. 变更记录（可选但建议）
- <YYYY-MM-DD>：<环境>：<current_export 变化：old → new>（refs：TASK-xxxx / T-Flow-xx）