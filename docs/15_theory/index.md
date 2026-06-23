受众：MASTER
标题：理论与架构原理目录

# 理论与架构原理目录

版本：v1.3
维护日期：2026-06-23
标签：当前事实 + 历史沉淀

本目录大量存放理论与契约分析文档。新成员先读核心契约章节，再按任务回溯历史决策。
契约文档（`*_CONTRACT.md` 与 `architecture_overview.md`）已按 2026-06-23 当前代码事实校准：SurfaceMainChain / ReplyEnvelope 为最终表达收口，LifeMemoryStore 为记忆 SSOT，StyleCanon 与旧 Agentic RAG 不再作为活动主链。其余理论说明与历史方案保留为沉淀。

## 5分钟入门（给新成员）
1. 先读：`architecture_overview.md`（v3.0），快速建立整体心智模型。
2. 按层读契约：`APP_AUDIT_SKILLS_CONTRACT.md` → `CORE_LAYER_MODULE_REFERENCE.md` → `RETRIEVAL_LAYER_CONTRACT.md` → `TOOLS_MEMORY_LAYER_CONTRACT.md` → `GOVERNOR_SCHEDULER_CONTRACT.md` → `INTEGRATIONS_LAYER_CONTRACT.md` → `PERIPHERAL_LAYERS_CONTRACT.md`。
3. 涉及主链变更时，再回到 `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` (v3.3) 校验一致性。
4. 理论与原理回顾：`Theory.md`（v1.4.0）、`SOUL_SYSTEM_FULL_UPGRADE_PATH.md`、`soul_emotion_optimization.md`。
5. LangGraph 相关历史方案：`LangGraph_Governed_Runtime_Solution.md`、`LANGGRAPH_CONSTRAINT_CONFLICT_ANALYSIS.md`。

## 当前代码事实契约文档（v3.0）
- `architecture_overview.md`（v3.2）：整体架构全景与运行面总览
- `APP_AUDIT_SKILLS_CONTRACT.md`（v3.2）：app / audit / skills / observability 层
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
