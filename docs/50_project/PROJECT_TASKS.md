受众：PROJECT
标题：Project Tasks（只产出文档，不涉及代码）

版本：v2.0
日期：2026-03-27

任务规则：
- 只修改 docs/ 目录下的文档体系（白皮书/PRD/DevSpec/DevTasks/AGENTS/Project/Trace），以及你产出的文档本身；不得触碰仓库代码与运行资产。
- 不要生成"指令性句式/终端指令片段"（尤其是让实现层去执行的口吻）。
- 每次输出必须给出：变更点列表 + 受影响章节/条目（可直接粘贴替换） + 版本号策略建议。

---

## 已完成任务

TASK-P001：白皮书工程细节迁移到 DevSpec ✅ DONE
- 白皮书只保留宪法/人格/风格层级/真相层级/不变量
- PRD 增补缺失的用户故事与验收标准

TASK-P002：PRD 扩写标准化 ✅ DONE（v1.5 基线）
- 每个功能包含用户故事、黑盒输入输出、约束、验收标准

TASK-P003：Trace 覆盖率维护 ✅ DONE
- requirements_trace.md 已建立

TASK-P004：文档大清理（2026-03-27） ✅ DONE
- 删除 27 个过时文档（UIA 10 + 占位 15 + 取代 2）
- 移动 3 个文件到正确目录 + 加门禁头
- DOCMAP v3.0（114 个文档，合规率 97%）

---

## 当前任务

TASK-P005：核心文档对齐更新
- 状态：进行中
- 目标：将所有停留在 3 月初的核心文档更新到当前架构状态
- 子任务：
  - ✅ ModelPlane.md v1.2→v1.3（清理废弃开关、增加 Host 8B、Gateway 场景）
  - ✅ ENV_EXTERNAL_SERVICES_CHECKLIST.md v2.0→v3.0（补 Gateway/Skills Hub 检查项）
  - ✅ ExternalServices_Config.md v2.0→v3.0（补 Gateway/平台凭据分层）
  - ✅ DevTasks.md v1.8→v1.9（补最近完成任务摘要）
  - ⏳ architecture_overview.md（补 Gateway 六平台 + 通话链路）
  - ⏳ dataflow_report.md（补 Gateway 数据流 + 清理 UIA 引用）
  - ⏳ AGENTS.md v1.3→v1.4（更新 Codex 协作规范）
  - ⏳ 15_theory/ 7 个合约文档批量对齐

TASK-P006：文档门禁合规扫尾
- 状态：待启动
- 目标：将剩余 3% 不合规文档补全门禁头
- 范围：PRD.md 补日期、CURSOR.md 补版本号和日期

---

## 维护规则

- 每次文档架构调整后更新本任务清单
- 已完成任务移到"已完成"区，保留历史记录
- DOCMAP 每次文档变更后同步更新
