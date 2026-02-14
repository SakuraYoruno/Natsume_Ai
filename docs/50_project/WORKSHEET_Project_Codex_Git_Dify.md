受众：PROJECT

标题：WORKSHEET（Project×Codex×Git×Dify 协作工作表）

# Natsume Ai 分工单（Project × Codex CLI × Git × Dify UI）

版本：v1.0  
适用范围：Host_Brain + Edge_Client + Dify（Ego：叙事/Intent）  
目标：把“长讨论/规范/文档”与“代码实施/修复/提交”彻底分离，避免 CLI 参与长讨论，同时保证可审计、可回滚、可复现。

---

## 0. 基本原则（必须遵守）

1）真相源  
- 规范与设计真相源：ChatGPT Project（白皮书/PRD/DevSpec/契约/验收）  
- 代码与工件真相源：Git 仓库（Host/Edge 代码、Dify 导出 DSL、脚本、测试、Schema）

2）单向决策链  
- 架构决策、协议变更、reason_code 变更：只能由 Project 产出并记录  
- 代码实现与提交：只能由 Codex CLI 在仓库执行

3）禁止长讨论进入 CLI  
- CLI 不做方案权衡、不做开放式探索、不做跨模块架构辩论  
- CLI 只执行“任务单”（Task Sheet）；任务单不完整则返回缺项清单，不进入讨论

4）版本与可回滚  
- 任何有影响的变更必须可回滚（tag/commit），并在 Runbook 写明回滚步骤  
- 协议/Schema 的破坏性变更必须 bump 版本号并提供迁移说明

---

## 1. 角色与职责划分（RACI）

说明：R=负责执行，A=最终负责/拍板，C=需咨询，I=需知会

### 1.1 ChatGPT Project（规范与文档层）
职责范围（A/R）
- 架构：组件边界、拓扑、数据流/控制流、单向链路与不变量  
- 规范：白皮书条款落地、reason_code 规范、日志规范、权限/风险模型、RAG 主权规则  
- 文档：PRD、Dev Spec、Runbook、Test Plan、Release/Rollback、Schema/协议说明  
- 模块边界：Host/Dify/Edge/Skills 的接口契约（字段级）  
- 状态机：sys_mode、Governor 流程状态、票据/确认/撤销（undo）  
- 协议定义：context_pack_json、Dify 输出 JSON、Intent 草案、Audit 记录格式、版本策略  
- 验收标准：指标阈值、测试用例集、失败模式与降级策略

RACI
- 架构/规范/文档：Project=A/R  
- 协议/Schema：Project=A/R，CLI=C，Git=I  
- 版本策略：Project=A/R，CLI=C  
- 任务拆解：Project=R，CLI=C

交付物（必须）
- docs/PRD.md  
- docs/DEV_SPEC.md  
- docs/RUNBOOK.md  
- docs/TEST_PLAN.md  
- schemas/*.json（或等价说明文档）  
- docs/reason_codes.md  
- docs/decision_log.md（每次大改必写）

### 1.2 Codex CLI（仓库执行层）
职责范围（R）
- 代码生成：按 Dev Spec 实现模块/脚本/测试  
- 局部重构：限定范围、文件级替换、保持可运行  
- bug 修复：复现→修复→回归测试→提交  
- 文件级修改：按清单改指定文件，输出完整文件或完整 diff  
- 自动化：启动脚本、QA 脚本、导出物落盘工具、CI 基础  
- 工件同步：把 Dify Export DSL、prompts、Code 节点脚本纳入 Git（若提供导出文件）

严格禁止（必须）
- 不参与长讨论、不做架构拍板、不做“需求澄清辩论”  
- 不做跨模块大改（除非任务单明确授权并给迁移方案）  
- 不自行引入新依赖/新组件（除非任务单允许）

CLI 每次交付必须输出（不可省略）
1) 变更文件清单（新增/修改/删除）  
2) 关键实现点（≤10 条）  
3) 运行/验收命令 + 预期断言  
4) 版本影响（是否 bump schema/protocol）  
5) 风险与回滚建议  
6) 建议更新的文档条目（让 Project 同步）

### 1.3 Git 仓库（真相源与审计层）
职责范围（A/R）
- 保存并审计：所有代码、脚本、测试、Schema、Dify DSL 导出物、prompts、Code 节点脚本  
- 分支与发布：Host 验证后打 tag；Edge 按 tag 拉取  
- 回滚：任何变更必须能回到 baseline tag

仓库最低要求（必须具备）
- README.md（启动、依赖、env、健康检查）  
- .env.example（变量名+示例，不放真实密钥）  
- docs/（白皮书/PRD/DevSpec/Runbook/TestPlan/ReasonCodes）  
- dify/flows（导出 DSL）+ dify/prompts + dify/code_nodes  
- scripts/（启动与QA脚本）  
- tests/（压测与汇总脚本）

### 1.4 Dify UI（编辑器层，仅操作，不作为真相源）
职责范围（R）
- 仅用于编辑/调试工作流  
- 每次稳定后必须 Export DSL，提交到 Git

严格禁止
- 禁用知识库检索节点（RAG 主权在 Host）  
- 禁用 Tool/HTTP 执行节点（Dify 不得直通执行）

---

## 2. 标准流程（讨论 → 规范 → 任务单 → 实施 → 验收）

### 2.1 设计阶段（只在 Project 内完成）
输出必须包含：
- 影响面分析（Host/Edge/Dify/DB/协议）  
- Dev Spec 更新（接口契约、日志点、reason_code、验收）  
- Task Sheet（下发给 CLI）

### 2.2 实施阶段（只在 Codex CLI + Git 完成）
CLI 必须：
- 新建分支/提交  
- 修改指定文件  
- 运行验收命令并满足断言  
- 输出 Delivery Report

### 2.3 验收阶段（Project 负责判定）
Project 必须：
- 对照 Test Plan/阈值验收  
- 通过后更新 decision_log，必要时升级版本号

---

## 3. Project → CLI 任务单（Task Sheet）模板（必须使用）

```text
[Task Sheet]
ID: TS-YYYYMMDD-XXX
目标（一句话）:
范围（必须具体到目录/文件）:
约束（引用白皮书条款/不变量编号）:
接口契约（schema_version/protocol_version/字段名）:
变更清单（新增/修改/删除文件）:
实现要求（必须/禁止）:
日志点（必须新增/修改哪些 log 字段）:
reason_code（涉及拒绝/降级必须列出）:
验收（命令 + 预期断言）:
回滚（如何回到上一个 tag/commit）:
备注（可选，<=5 行）:
```

---

## 4. CLI → Project 交付回报（Delivery Report）模板（必须使用）

```text
[Delivery Report]
Task Sheet ID:
分支/commit:
变更文件清单（新增/修改/删除）:
关键实现点（<=10条）:
运行/验收命令:
验收结果（关键输出/断言）:
版本影响（是否 bump schema/protocol，若是写新版本）:
风险与已知限制:
回滚建议:
建议同步的文档条目（PRD/DevSpec/Runbook/TestPlan/Schema/ReasonCodes）:
```

---

## 5. “长讨论”隔离规则（强制）

1) 讨论只发生在 Project：架构、规范、协议、reason_code、验收阈值  
2) CLI 只执行 Task Sheet，不扩展范围  
3) CLI 遇到信息缺失：只返回缺项清单，不猜测

缺项清单格式（CLI 返回）
- 缺失字段：xxx  
- 需要的最小补充：xxx  
- 影响：无法保证 xxx 验收
