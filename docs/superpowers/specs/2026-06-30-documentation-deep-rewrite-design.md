受众：MASTER
标题：文档深度重写 — 设计规格

# 文档深度重写 — 设计规格

版本：v1.0
日期：2026-06-30
状态：已批准，进入实施

## 1. 目标

以代码为唯一真源，逐模块深度重写所有核心文档。消除多轮迭代累积的文档-代码失配。

## 2. 代码事实基线（不可变真源）

以下数据来自 2026-06-30 代码探索：

| 维度 | 值 |
|------|-----|
| `host_brain/` 总 Python 文件 | 788 |
| `host_brain/app/` | 228 文件（41 个 route 文件，~30K 行） |
| `host_brain/persona/` | 75 文件（根 55 + surface 11 + scoring 6），120+ 类 |
| `host_brain/pentest/` | 124 文件（根 50 + agent 31 + adversarial 9 + swarm 8 + online 5 + tools 6 + data 5 + report 4 + utils 6） |
| `host_brain/runtime/` | 6 文件，5,550 行 |
| `host_brain/tribe/` | 34 文件，23,534 行，23 个 agent class |
| `host_brain/memory/` | 22 文件，16,116 行（含 life_memory/ 子包 7 文件） |
| `host_brain/governor/` | 10 文件，2,617 行 |
| `host_brain/skills/impl/` | 25 executor 文件 |
| `edge_client/` | 80 文件 |
| Monitor UI 路由 | 37 条，7 个导航组 |
| Digital Life API 端点 | 18 个（含 3 个 proactive 端点） |
| Pentest ToolSpec | 88 |
| Pentest Executor | 92 |

## 3. 分层实施计划

### 第 1 层：架构框架层
**文档：** `CURRENT_CODE_ARCHITECTURE.md` (v4.1→v4.2), `dataflow_report.md` (v3.3→v4.0)

**CURRENT_CODE_ARCHITECTURE.md 变更：**
- §3 Host Main 装配：补充 obs_routes 注册方式说明
- §4 Host 路由前缀：补充 proactive 端点（/v1/digital-life/proactive/*）、/obs/proactive-autonomy
- §6 Persona/Surface：重写为实际管线分解（surface_main_chain → surface_runtime → surface_memory_recall → surface_packet_compiler → candidate_scoring），补充 scoring 子包 6 文件管线
- §7 Pentest：补充双规划器共存（MCTS + ReAct + CognitiveEngine）、存根报告标注、agent/ 内部结构
- §8 Tribe：更新为 34 文件实际规模，补充 agent 大小分布
- §9 Monitor UI：补充 37 路由完整分组表（含 ProactiveAutonomyDashboard、SecurityPentest）
- 新增 §10 Digital Life：18 端点表 + proactive 子体系

**dataflow_report.md 变更：**
- 补充 Pentest agent cognitive loop 数据流（CognitiveEngine → ToolRegistry → executor → observe → EvolutionCore）
- 补充 Adversarial game loop（RedMutation → BlueDetector → BlueLearner → scoring）
- 补充 Swarm coordinator 消息流（MessageBus → TaskDistributor → agent lifecycle）
- 补充 Online safety gate 三层控制流
- 补充 Persona surface scoring 管线数据流
- 补充 Proactive autonomy 回路（digital_life → proactive_voice → edge push）
- 更新记忆层：life_memory/ 子包分解结构

### 第 2 层：模块参考层
**文档：** `CORE_MODULES_DETAILED_REFERENCE.md` (v4.1→v4.2), `CAPABILITY.md` (v2.2→v2.3), `PENTEST_MODULES_ARCHITECTURE.md` (v1.0→v1.1)

**CORE_MODULES_DETAILED_REFERENCE.md 变更：**
- §3 Persona：补齐遗漏的 25+ 文件（proactive_voice、emotional_proactive、self_model、evolution_orchestrator、inner_activity_agent 等）
- §3.4 surface/：补充 scoring/ 6 文件管线描述
- §4 Memory：更新为 life_memory/ 子包 7 文件结构
- §5 Runtime：补充 autonomous_self 的 8 维权重的自适应特质
- §6 Tribe：补充 tribe_state、cortex_route_decider、edge_command_pusher 等遗漏文件
- §9 Pentest：补齐 agent/ 遗漏的 10 文件（online_research、strategy_escalator、biz_logic_probe 等）

**CAPABILITY.md 变更：**
- §9 数据文件：确认当前 5 个数据文件 + 7 个 JSON/TXT 数据资产
- §10 子系统：更新 agent/ 为完整的 31 文件清单
- 新增 §12：agent/ 内部模块能力矩阵（cognitive_engine、belief_model、goal_model 等核心模块的能力描述）

**PENTEST_MODULES_ARCHITECTURE.md 变更：**
- §6 自主 Agent：补齐遗漏的 10 文件（online_research、strategy_escalator、biz_logic_probe、action_generator、vector_store、reward、compat、budget、llm_brain、__init__）
- 新增双规划器说明（MCTS + ReAct + CognitiveEngine 三重路径）
- §10 Data/Report：区分存根（json_report 5行、markdown_report 20行）与实际实现（phase6_report 492行）
- 新增重复类型文档化（AgentRole 双定义、AuthorizationProof 双定义）

### 第 3 层：入口与索引层
**文档：** `README.md`, `DOCMAP.md` (v5.3→v5.4)

**README.md 变更：**
- 核验所有运行面端口/入口与代码一致
- 补充 Digital Life proactive 路由
- 更新 Pentest 事实（双规划器、agent/ 内部结构）
- 验证 Monitor UI 路由数量

**DOCMAP.md 变更：**
- 更新 LIVING 文档版本号到第 1-2 层完成后的版本
- 验证所有文档路径引用

### 第 4 层：契约与叙事层
**文档：** `DevSpec.md` (v2.2→v2.3), `PRD.md` (v2.2→v2.3), `Soul.md`, 7 份 theory 契约

**DevSpec.md 变更：**
- 补充 Pentest agent 内部模块工程契约（CognitiveEngine、ToolRegistry、EvolutionCore 接口）
- 补充 Persona surface/scoring 管线契约
- 补充 Digital Life proactive 回路契约
- 补充 Monitor UI 37 路由契约

**PRD.md 变更：**
- FR-SURFACE：更新为 surface/ 子包 + scoring 管线
- 新增 FR-PENTEST：描述 Pentest 双规划器 + agent 内部模块
- 新增 FR-PROACTIVE：描述 proactive autonomy 体系

**Soul.md：**
- 验证与 personality_traits.py（7 维特质向量）、bond_aware_style.py（4 阶段）的一致性

**7 份 theory 契约：**
- 以代码为真源逐份核验接口签名

## 4. 不变量

1. 代码是唯一真源。文档描述与代码冲突时，以代码为准。
2. 所有 .md 文件保留 `受众：` 和 `标题：` 头部。
3. 不删除任何 [ARCHIVE] 标记的历史文档。
4. 每层完成后呈现 diff 供审查。
5. [LIVING] 文档的版本号在每次修改后递增。

## 5. 交付物

每层交付：
- 重写后的完整 .md 文件
- 变更说明（哪些代码事实驱动了哪些文档变更）
- 行数变化估算
