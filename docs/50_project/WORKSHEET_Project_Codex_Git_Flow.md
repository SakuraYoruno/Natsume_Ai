受众：PROJECT
标题：WORKSHEET（Project×Codex×Git×Flow 协作工作表）

# Nastume Ai 分工单（Project × Codex × Git × Flow）

版本：v1.1.1
适用范围：Host_Brain + Edge_Client +（可选）Cloud Exocortex + ClawdBot +（可选）MCP Providers  
目标：把“长讨论/规范/文档”与“代码实施/修复/版本化交付”彻底分离，避免实现层参与长讨论，同时保证可审计、可回滚、可复现。

---

## 0. 基本原则（必须遵守）

1）真相源  
- 规范与设计真相源：ChatGPT Project（白皮书/PRD/DevSpec/契约/验收）。  
- 代码与配置资产真相源：Git 仓库（Host/Edge 代码、Flow 导出物、脚本、测试、Schema、fixtures）。

2）单向决策链  
- 架构决策、协议变更、reason_code 变更：只能由 Project 产出并记录（以文档版本与 Trace 为准）。  
- 代码实现与版本化交付：只能由 Codex 在仓库落地（以“任务卡/DevTasks”为入口）。

3）禁止长讨论进入实现层  
- 实现层不做方案权衡、不做开放式探索、不做跨模块架构辩论。  
- 实现层只执行“任务单”（Task Sheet）；任务单不完整则返回缺项清单，不进入讨论。

4）版本与可回滚  
- 任何有影响的变更必须可回滚，并能指回一个明确的“基线版本标识”（例如版本号/变更集标识/发布基线）。  
- 协议/Schema 的破坏性变更必须 bump 版本号并提供迁移说明（以 DevSpec/Contracts 为准）。

---

## 1. 角色与职责划分（RACI）

说明：R=负责执行，A=最终负责/拍板，C=需咨询，I=需知会。

### 1.1 ChatGPT Project（规范与文档层）
职责范围（A/R）
- 架构：组件边界、拓扑、数据流/控制流、单向链路与不变量。  
- 规范：白皮书条款落地、reason_code 规范、日志规范、权限/风险模型、RAG 主权规则。  
- 文档：PRD、DevSpec、Test Plan、Release/Rollback、Schema/协议说明。  
- 模块边界：Host/Edge/Skills/ToolBroker/通道外显的接口契约（字段级）。  
- 状态机：sys_mode、Governor 流程状态、票据/确认/撤销（undo）。  
- 协议定义：context_pack_json、LLM 输出 JSON、Intent 草案、审计记录格式、版本策略。  
- 验收标准：指标阈值、测试用例集（T-xx）、失败模式与降级策略。

RACI
- 架构/规范/文档：Project=A/R  
- 协议/Schema：Project=A/R，实现层=C，仓库=I  
- 版本策略：Project=A/R，实现层=C  
- 任务拆解：Project=R，实现层=C（以 DevSpec 契约为唯一来源）

交付物（必须）
- docs/20_prd/PRD.md  
- docs/30_devspec/DevSpec.md  
- docs/30_devspec/DevTasks.md  
- tests/TESTPLAN.md（只列 T-xx 与断言口径）  
- docs/reason_codes.md（或由 DevSpec/Contracts 等价承载）  
- docs/60_trace/requirements_trace.md（或等价追溯表）

### 1.2 Codex（仓库实现层）
职责范围（R）
- 代码实现：按 DevSpec/DevTasks 落地模块、契约、日志与测试。  
- 局部重构：限定范围、文件级替换、保持契约一致。  
- 缺陷修复：复现→修复→补齐回归断言（T-xx）→版本化交付。  
- 自动化与静态扫描：落地“禁止旁路/单写者/契约稳定性”规则。  
- 配置资产落地：Flow 导出物、环境指针与 Trace 索引一致性相关的工程落点与回归断言。

严格禁止（必须）
- 不参与长讨论、不做架构拍板、不做“需求澄清辩论”。  
- 不做跨模块大改（除非任务单明确授权并给出迁移口径）。  
- 不自行引入新依赖/新组件（除非任务单允许且写明风险与回滚口径）。

每次交付必须输出（不可省略）
1) 变更文件清单（新增/修改/删除）。  
2) 关键实现点（≤10 条，描述行为变化）。  
3) 覆盖/新增的测试编号（T-xx）与关键断言点（字段/拒绝码/审计记录）。  
4) 版本影响（是否 bump schema/protocol/flow 资产指针）。  
5) 风险与回滚策略（描述口径：回到哪个基线、哪些契约/数据受影响）。  
6) 建议更新的文档条目清单（由 Project 统一维护与合并）。

### 1.3 Git 仓库（真相源与审计层）
职责范围（A/R）
- 保存并审计：所有代码、脚本、测试、Schema、Flow 资产、fixtures、静态扫描规则。  
- 发布与回滚：以“可追溯版本标识”作为发布与回滚的唯一锚点（具体流程由 Project 的 Runbook/Release 口径定义）。  
- 追溯：任何行为变化必须能定位到：变更集标识 + 覆盖的 FR/T + 关键审计字段口径。

仓库最低要求（必须具备）
- README.md（启动、依赖、env、健康检查口径）  
- .env.example（变量名+示例，不放真实密钥）  
- docs/（白皮书/PRD/DevSpec/DevTasks/AGENTS/Trace 等）  
- flows/exports + flows/active.pointer + docs/60_trace/flows/index.md  
- scripts/（启动与 QA 脚本：仅作为资产，不在本文件定义命令）  
- tests/（contract/e2e/fixtures/TESTPLAN）

### 1.4 Flow 资产编辑器（可选）
定位：编辑器仅用于编辑/校对 Flow（LangGraph 图/配置）的内容表达与结构一致性；不作为真相源。  
硬约束：
- Flow 的“生效版本”只能由仓库中的 flows/active.pointer 与 Trace 索引共同确定。  
- 编辑器内任何修改，必须以“导出物”形式落到 flows/exports，并更新 active.pointer 与 docs/60_trace/flows/index.md。  
- Flow 资产不承载执行事实；执行事实仍以 Host 审计链为准。

---

## 2. 标准流程（讨论 → 规范 → 任务单 → 实施 → 验收）

### 2.1 设计阶段（只在 Project 内完成）
输出必须包含：
- 影响面分析（Host/Edge/Cloud/DB/Flow/协议）。  
- DevSpec 更新（接口契约、日志点、reason_code、验收口径）。  
- DevTasks 更新（可执行任务条目：输入/输出/验收/T-xx/路径）。  
- Task Sheet（下发给实现层的最小执行输入）。

### 2.2 实施阶段（只在 Codex + Git 完成）
实现层必须：
- 只在任务单范围内改动；保证契约/测试/fixtures 同步更新。  
- 产出“交付回报（Delivery Report）”，以测试编号与断言口径描述覆盖。

### 2.3 验收阶段（Project 负责判定）
Project 必须：
- 对照 Test Plan/阈值验收（以 T-xx 与断言为准）。  
- 通过后记录决策与版本更新（必要时 bump 版本号与迁移说明）。  
- 对涉及 Flow 的变更，核对 active.pointer 与 flows/index 的一致性。

---

## 3. Project → Codex 任务单（Task Sheet）模板（必须使用）

```text
[Task Sheet]
ID: TS-YYYYMMDD-XXX
目标（一句话）:
范围（必须具体到目录/文件）:
约束（引用不变量编号 I-…）:
接口契约（schema_version/protocol_version/字段名）:
变更清单（新增/修改/删除文件）:
实现要求（必须/禁止）:
日志点（必须新增/修改哪些审计字段；evidence_refs 口径）:
reason_code（涉及拒绝/降级必须列出）:
验收（测试编号 T-xx + 关键断言点）:
回滚（目标基线版本标识 + 受影响的数据/契约口径）:
备注（可选，<=5 行）: