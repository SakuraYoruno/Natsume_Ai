受众：MASTER
标题：理论体系目录

# 理论体系目录

版本：v3.1
维护日期：2026-07-26
目录状态：`ONE_CANONICAL_THEORY`

本目录回答“为什么这样设计、各概念如何协作、哪些边界不可破坏”。它不承担当前代码状态、目标完成状态或任务排期。

## 权威阅读链

1. [核心理论：状态、记忆、人格与行动](Theory.md)：本目录唯一当前理论总纲。
2. [当前代码架构事实](../50_project/arch/CURRENT_CODE_ARCHITECTURE.md)：代码现在真实是什么。
3. [目标架构](../50_project/arch/TARGET_ARCHITECTURE.md)：最终必须成立的架构边界。
4. [架构差距矩阵](../50_project/arch/ARCHITECTURE_GAP_MATRIX.md)：当前与目标之间的差距。
5. [下一代演化执行合同](../30_devspec/NEXT_GENERATION_EVOLUTION_EXECUTION.md)：唯一活动实施顺序与 Gate。

不要从理论原则直接推导“代码已经完成”，也不要从历史测试 PASS 推导“当前完整架构成立”。

## 核心理论覆盖范围

[Theory.md](Theory.md) 已统一解释：

- 语言、模型、候选与执行权力的分离；
- 命令、事件、快照、观察、投影、提案和 Receipt 的状态本体；
- Digital Life 的确定性演化与因果连续性；
- Working Context、LifeMemory、World Fact、Canon、Persona 的边界；
- Identity、Persona、Relationship、Affect 的不同时间尺度与权限；
- World、Scene 与 Planning Virtual State 的隔离；
- Governor、Grant、Review、Budget 与 authority lineage；
- PlanningSearch、不可变 TaskGraph、retry 与 replan；
- Verification、Acceptance、effect intent 与外部 Receipt；
- 签名证据与执行引用隔离、higher-fence Operation 重获与 exact replay；
- Feedback、Capability、Knowledge 和 Identity 的长期演化门禁；
- 故障、降级、恢复、不变量与反模式。

## 按问题阅读

| 想回答的问题 | 先读 |
|---|---|
| “Natsume 与普通 LLM Agent 的根本区别是什么？” | [Theory.md 第 2～3 节](Theory.md#2-系统命题数字生命不是模型而是受治理的连续状态系统) |
| “状态、事实、投影和提案怎么区分？” | [Theory.md 第 4 节](Theory.md#4-状态本体先区分语义角色再讨论存储) |
| “记忆为什么不能直接当真相？” | [Theory.md 第 6 节](Theory.md#6-记忆有来源的经历而不是模型的自我叙述) |
| “人格、关系和情感怎样影响回答？” | [Theory.md 第 7 节](Theory.md#7-personaidentityrelationship-与-affect) |
| “World、Scene、MCTS 虚拟状态有什么区别？” | [Theory.md 第 8～10 节](Theory.md#8-worldscene-与规划虚拟状态) |
| “系统凭什么说任务完成了？” | [Theory.md 第 11 节](Theory.md#11-governed-execution从承诺到事实的唯一道路) |
| “人格和能力怎样安全成长？” | [Theory.md 第 13 节](Theory.md#13-长期演化变化必须比运行更慢更可证) |
| “如何从代码核查这些理论？” | [Theory.md 第 15～17 节](Theory.md#15-关键不变量与验证方法) |

## 已删除的旧材料

旧层级快照、LangGraph 方案、Soul 升级计划和情绪设计日志已从当前目录删除。它们混合了已删除路径、旧模块数量、旧 authority、实现伪代码和过期完成结论；Git 历史足以承担追溯，不再为这些材料建立活动索引或历史副本。

需要理解现有实现时使用 [功能模块地图](../50_project/arch/FUNCTIONAL_MODULES.md) 与 [关键数据流](../50_project/arch/DATA_FLOW_ARCHITECTURE.md)，不要从旧提交恢复这些快照作为当前合同。

## 文档维护规则

1. 稳定理论只写长期成立的不变量、概念关系和验收性质，不写测试数量、代码行数或短期任务状态。
2. 当前事实只进入 `CURRENT_CODE_ARCHITECTURE.md` 或专门的当前模块文档。
3. 目标能力只进入 `TARGET_ARCHITECTURE.md`，执行波次只进入正式执行合同。
4. 任何代码引用都必须指向真实路径；实现被删除时同步修正文档。
5. 新理论概念必须说明 authority、输入、输出、时间语义、证据、失败方式和禁止越权范围。
6. 已删除的旧方案不得恢复为当前调度依据，也不得用旧 PASS 覆盖当前差距。
