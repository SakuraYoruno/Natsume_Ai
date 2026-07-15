受众：MASTER
标题：理论与架构原理目录

# 理论与架构原理目录

版本：v2.0
维护日期：2026-07-15
标签：理论参考 + 历史沉淀

本目录存放理论与历史契约分析，不承担当前代码状态。新成员应先读 `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`、`TARGET_ARCHITECTURE.md` 与 `ARCHITECTURE_GAP_MATRIX.md`，再按主题使用本目录材料。

## 5分钟入门（给新成员）
1. 先读：`docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md`，建立当前事实。
2. 再读：`docs/50_project/arch/TARGET_ARCHITECTURE.md` 与 `ARCHITECTURE_GAP_MATRIX.md`，区分目标和差距。
3. 按主题阅读本目录；任何路径或状态都必须回到代码与当前事实文档复核。
4. 理论与原理回顾：`Theory.md`（v1.4.0）、`SOUL_SYSTEM_FULL_UPGRADE_PATH.md`、`soul_emotion_optimization.md`。
5. LangGraph 相关历史方案：`LangGraph_Governed_Runtime_Solution.md`、`LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS.md`。

## 理论契约参考（非当前状态）
- `CORE_LAYER_MODULE_REFERENCE.md`（v3.0）：core / prompt_context 模块
- `RETRIEVAL_LAYER_CONTRACT.md`（v3.0，**已重写**）：RecallFusionServiceContract 与 surface_memory_recall
- `TOOLS_MEMORY_LAYER_CONTRACT.md`（v3.2，**已重写**）：tools / memory / life_memory SSOT
- `GOVERNOR_SCHEDULER_CONTRACT.md`（v3.0）：Governor R1-R8 与两套 Scheduler
- `INTEGRATIONS_LAYER_CONTRACT.md`（v3.0）：integrations / graph_* 与 LLM Gateway
- `PERIPHERAL_LAYERS_CONTRACT.md`（v2.3）：airlock / bond / channels / vision / lsp / mcp

## 理论与方案沉淀
- `Theory.md`：理论框架与原理解释（不作为运行时事实文档）
- `SOUL_SYSTEM_FULL_UPGRADE_PATH.md`：人格系统升级路径
- `soul_emotion_optimization.md`：情绪与人格优化记录
- `LangGraph_Governed_Runtime_Solution.md`：LangGraph 治理运行时方案
- `LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS.md`：LangGraph 约束冲突分析
