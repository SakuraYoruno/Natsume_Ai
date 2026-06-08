受众：MASTER
标题：架构图册总目录（图驱动心智模型）

版本：v1.0
日期：2026-05-30
归属：docs/50_project/arch/diagrams/

---

定位：本目录是**视觉版**架构索引——给"5 秒拿到全局心智模型"和"对话/PPT 直接复用"两个目标服务。
所有图均以当前代码为锚点（git head 之时），与 `docs/50_project/arch/CURRENT_CODE_ARCHITECTURE.md` (v3.0) 同源；若发生分歧，**以代码 + v3.0 主文档为准**。

## 0. 使用顺序

| 顺序 | 图 | 一句话 |
|---|---|---|
| 1 | [`01_runtime_topology.md`](./01_runtime_topology.md) | **5 个运行面**怎么互联（Host Main / Obs / Edge / Gateway / Monitor UI） |
| 2 | [`02_main_chain_flow.md`](./02_main_chain_flow.md) | **`/v1/chat` 主链全流程**，HTTP→SSE 三阶段，含媒体分支 |
| 3 | [`03_dataflow_recall.md`](./03_dataflow_recall.md) | **召回与记忆数据流**：surface_memory_recall → RecallFusion → 多 store |
| 4 | [`04_executive_layers.md`](./04_executive_layers.md) | **Executive 三层**：router 薄壳 / runtime 路由薄层 / runtime_core 决策核心 |
| 5 | [`05_tribe_topology.md`](./05_tribe_topology.md) | **23 family 拓扑**：脑区、内分泌、外周与执行 |
| 6 | [`06_input_output_routes.md`](./06_input_output_routes.md) | **输入回流与出站**：Gateway / `/v1/host/input` / OutboundDispatcher |
| 7 | [`07_observability_topology.md`](./07_observability_topology.md) | **Observability**：独立 ASGI、warm cache、Monitor UI 信息架构 |

## 1. 渲染建议

- 所有图均以 **mermaid** 撰写，可在 GitHub / VS Code Mermaid 预览 / Monitor UI 中直接渲染。
- 大图配套**两段说明**：
  - "怎么读这张图"（节点角色 + 主路径）
  - "代码锚点"（精确到文件路径与函数名）

## 2. 维护规则

1. 图册与 `CURRENT_CODE_ARCHITECTURE.md` 同周期更新；**主文档先动，图随后改**。
2. 每张图必须保留"代码锚点"小节，便于回溯到当前实际代码事实，避免图过期。
3. 如某图涉及"已废弃模块"（旧 fastapi_app / 旧 Agentic RAG / 旧 episodic 主作者），必须显式标注 ❌ 已废弃，不得保留为活动节点。
4. 大改架构时 → 同步进 Batch（n+1）文档重写流程。

## 3. 不变量小抄（图与文档共用口径）

| # | 不变量 |
|---|---|
| 1 | Host Main 入口 = `asgi.py + app_factory.py + router_registry.py` 三件套 |
| 2 | normal chat 主作者 = `surface_main_chain.py` (SurfaceMainChain) |
| 3 | 媒体最终作者 = `reply_modality_router`（不是 surface chain） |
| 4 | 召回路径 = `RecallFusionServiceContract → surface_memory_recall → MemoryRecordPacket` |
| 5 | 记忆 SSOT = `LifeMemoryStore` |
| 6 | Tribe = 23 family，灰度全数删除 |
| 7 | Host Main 与 Observability Backend 是**两个独立 ASGI app** |
| 8 | SSE 三阶段：`open` → `stream` → `final/end` |
