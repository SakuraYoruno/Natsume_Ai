受众：PROJECT
标题：Project Tasks（只产出文档，不涉及代码）

任务规则：
- 只修改 docs/10_whitepaper、docs/20_prd、docs/60_trace（以及你产出的文档本身）
- 不要生成“请去实现/请提交/请跑测试”这种句子
- 每次输出必须给出：变更点列表 + 受影响章节 + 版本号策略建议

TASK-P001：把白皮书中的“工程实现细节”迁移到 DevSpec（文档层迁移）
- 输入：docs/10_whitepaper/whitepaper_constitution_persona.md
- 输出：
  - 白皮书只保留宪法/人格/风格层级/真相层级/不变量
  - PRD 增补缺失的用户故事与验收标准
  - Trace 更新：需求 → 章节 → 测试编号（黑盒）

TASK-P002：PRD 扩写标准化
- 每个功能至少包含：
  - 用户故事（CN）
  - 黑盒输入/输出
  - 约束（risk_level、sys_mode、可撤销性）
  - 验收标准（可测试）

TASK-P003：Trace 覆盖率维护
- 输出 requirements_trace.md/.csv，列出每条需求至少对应：
  - DevSpec 章节
  - 最小测试编号（T-xx）
