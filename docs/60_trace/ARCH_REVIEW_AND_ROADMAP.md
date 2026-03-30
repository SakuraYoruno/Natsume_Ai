受众：MASTER
标题：Natsume_Ai 架构评审与优化路线图

# Natsume_Ai 架构评审与优化路线图

**文档版本**：v3.0
**创建日期**：2026-03-09
**最后更新**：2026-03-30
**状态**：Phase X-XIII 已完成，进入打磨阶段
**作者**：架构评审（基于全量文档扫描 + 代码实际验证）

---

## 目录

1. [多智能体重构阶段总结（已完成）](#一多智能体重构阶段总结已完成)
2. [当前架构核心问题诊断](#二当前架构核心问题诊断)
3. [三个优化方向可行性分析](#三三个优化方向可行性分析)
4. [流式Chat的正确架构设计](#四流式chat的正确架构设计)
5. [新架构分层方案](#五新架构分层方案)
6. [代码取舍建议](#六代码取舍建议)
7. [优先级路线图（当前阶段）](#七优先级路线图当前阶段)

---

## 一、多智能体重构阶段总结（已完成）

> **P15 终点封板（2026-03-17）：M-MA1 ～ M-MA6 全部阶段目标已达成，220 用例全通过。**

| 阶段 | 内容 | 对应 P 系列 | 状态 |
|------|------|------------|------|
| M-MA1 | 图拆分（三图架构） | P1～P4 | ✅ 封板 |
| M-MA2 | 概念收口 + 开关收敛 | P5～P6 | ✅ 封板 |
| M-MA3 | 治理引擎 + 审计 | P7～P8 | ✅ 封板 |
| M-MA4 | Agent Handler 骨架 + JudgeAgent | P9～P11 | ✅ 封板 |
| M-MA5 | 真正的多 Agent 运行时（动态路由） | P12 | ✅ 封板 |
| M-MA6 | ReAct Agent 实现 + 动态路由终点 | P13～P15 | ✅ **P15 终点封板** |

**第十二轮正式基线**：P15，已封板（2026-03-17）

多智能体规划重构升级阶段**正式结束**。下一阶段进入**功能完整性补全与体验优化**。

---

## 二、架构核心问题诊断（2026-03-30 状态更新）

### ✅ 问题1：流式 Chat 绕过了 Graph（**已解决** — Phase X-XIII）

> 2026-03-19 ～ 2026-03-30 期间已完整实现。

**当前实现**（`chat_stream_handler.py` L373-1391）：
- ✅ 完整五路召回（Episodic / WorldCanon / Bond / ArtifactVault / SqliteLedger）通过 `orchestrator.arun()`
- ✅ StateArbiter 四路注入（Bond fact / Persona trait / Hard state / Canon style）
- ✅ RecallPack 构建 + AdaptivePromptBudget 动态预算
- ✅ 情感层深度接入：VTS 表情实时同步 + 过渡帧动画（L908-1030）
- ✅ 后台异步写入（Bond / 审计 / Episodic）全路径队列优先

**Phase XIII 变更**：六路召回精简为五路（style_canon / persona_utterance 走独立通道绕过 Reranker，常驻注入 prompt 前 3-4 位）。

---

### ✅ 问题2：SceneRouter 已下沉到 Graph（**已解决**）

`main_chain.py` L2352-2760：SceneRouter 定义在 Graph 层，是 Graph 的第一个节点。FastAPI 层只做 I/O 透传。

---

### ✅ 问题3：情感层已全路径接入（**已解决**）

- ✅ 流式路径：VTS 表情实时同步 + 过渡帧动画
- ✅ emotion_context 影响 Prompt 构建
- ✅ 情感 LLM 推理（`HOST_EMOTION_LLM_INFERENCE_ENABLED` 可选开关）

---

### 🟡 问题4：代码复杂度（部分改善，仍需打磨）

- ⚠️ `main_chain.py` 159KB 单文件需要拆分
- ⚠️ `obs_routes.py` 238KB/6460行 需要拆分
- ✅ obs/ 和 observability/ 实际分工明确（obs_routes = API 层，observability/ = 业务逻辑层），不需要合并
- ✅ Skills Hub 已实现 OpenClaw 风格 SKILL.md 安装式架构
- ✅ MCP 和 Skills 已有 `mcp_skill_bridge.py` 桥接

---

## 三、三个优化方向可行性分析

### 方向A：Host 端新增 Qwen3 8B 做日常交流

**可行性：高，但需要明确定位**

**建议定位：**

| 场景 | 模型 | 特点 |
|------|------|------|
| 日常闲聊（非 Gaming） | Host: Qwen3 8B（本地） | 低延迟，有情感层，完整召回 |
| 打游戏时 | Edge: Qwen3 3B（Ollama） | 极简回复，不抢显存 |
| 复杂任务（代码/翻译/执行） | Cloud: 完整 Graph | 9节点链路，治理层 |

**优势：**
- Qwen3 8B 日常对话质量远超 3B，支持中日双语
- 本地推理无网络延迟，首字节 < 500ms
- 可完整接入情感层、Bond 更新、审计链
- Gaming 模式下自动降级到 Edge 3B，不抢显存

**风险：**
- 8B 模型需要约 6-8GB VRAM（量化后约 4-5GB）
- 需要与游戏显存竞争，需严格的 `sys_mode=GAMING` 门控

---

### 方向B：打游戏时切换到 Edge Qwen3 3B 做简要互动

**可行性：高，且已有基础设施**

`ModelPlane.md` 第 4.4 节已定义 Gaming 模式：
> Host 强制 NO_LOCAL_GPU：仅允许模板/规则引擎/CPU 兜底

但当前实现是"退化到模板"，而不是"切换到 Edge 3B"。

**建议改进：**
- Gaming 模式下，将 CHAT 场景路由到 Edge 3B（已有 `edge_client/fallback_model.py`）
- Edge 3B 只做**极简互动**：确认收到、简短回应、不触发复杂推理
- 不写入 Bond 更新，不触发 VTS 复杂动作，只做基础 TTS 输出

---

### 方向C：仿照 OpenClaw 封装 Skills，调用 Cursor/PyCharm/Codex

**这是最有价值的方向，也是当前最缺失的部分**

当前 `host_brain/skills/` 目录存在，但 Skills 层没有被统一封装成可调用的工具接口。

**OpenClaw 风格的 Skills 封装形式：**

```python
class SkillRegistry:
    @skill("open_cursor", description="在 Cursor 中打开文件或项目")
    async def open_cursor(self, path: str) -> SkillResult: ...

    @skill("run_pycharm_action", description="触发 PyCharm 的 Action")
    async def run_pycharm_action(self, action_id: str) -> SkillResult: ...

    @skill("codex_task", description="向 Codex 提交编程任务")
    async def codex_task(self, task: str, context: str) -> SkillResult: ...
```

**当前问题：**
- Skills 层没有统一的 `@skill` 装饰器注册机制
- 工具调用散落在各处
- MCP 工具注册（`mcp/tool_registry.py`）和 Skills 层是两套体系，需要合并

---

## 四、流式Chat的正确架构设计

**核心原则：流式也要完成 Graph 的职能**

```
用户输入
    │
    ▼
[1] 六路召回（并行，~200ms）
    ├── Episodic 向量召回
    ├── StyleCanon 检索
    ├── WorldCanon 检索
    ├── Bond 摘要读取
    ├── 全文检索
    └── 重排融合
    │
    ▼
[2] ContextPack 构建（~50ms）
    │
    ▼
[3] 情感层读取（emotion_context，~10ms）
    │
    ▼
[4] 流式 LLM 调用（开始 SSE 输出）← 首字节在这里，用户感知延迟 ~300-500ms
    │
    ▼（后台异步，不阻塞流式输出）
[5] 后台异步完成
    ├── Governor 审计写入
    ├── Bond 更新
    ├── Episodic 写入
    └── VTS 情感驱动
```

**关键设计原则：**
- 召回和 ContextPack 构建在流式输出**前**完成（保证上下文质量）
- 审计写入在**后台异步**进行（不阻塞首字节，保证响应速度）
- 情感层在召回后、LLM 调用前读取（情感状态影响 Prompt 构建）

---

## 五、新架构分层方案

### 场景路由总图

```
用户输入
    │
    ▼
[SceneRouter] ← 下沉到 Graph 入口，从 FastAPI 层移出
    │
    ├── 日常闲聊（非Gaming）
    │       └── Host Qwen3 8B
    │               ├── 完整六路召回
    │               ├── 情感层接入
    │               ├── 流式输出
    │               └── 后台异步写入
    │
    ├── Gaming 模式
    │       └── Edge Qwen3 3B
    │               ├── 极简回复（不抢显存）
    │               ├── 基础 TTS
    │               └── 不写 Bond / 不触发 VTS 复杂动作
    │
    ├── 复杂任务（代码/翻译/执行）
    │       └── Cloud 完整 Graph
    │               ├── 9节点链路
    │               ├── Governor 治理层
    │               └── 完整审计链
    │
    └── 工具调用
            └── Skills Layer（OpenClaw 风格）
                    ├── Cursor 操作
                    ├── PyCharm Action
                    └── Codex 任务提交
```

### 分层职责定义

| 层级 | 职责 | 当前状态 | 目标状态 |
|------|------|----------|----------|
| FastAPI I/O 层 | 接收请求、返回响应、SSE 流 | 承担了路由决策 | 纯 I/O，不做决策 |
| SceneRouter 层 | 场景分类、模型选择 | 散落在 FastAPI handler | 下沉为 Graph 第一节点 |
| Host Qwen3 8B 层 | 日常对话，本地推理 | 不存在 | 新增 |
| Edge Qwen3 3B 层 | Gaming 模式极简回复 | 退化到模板 | 接入 Edge 3B |
| Cloud Graph 层 | 复杂任务完整链路 | 现有 9 节点 Graph | 保留，仅用于复杂场景 |
| Skills 层 | 工具调用封装 | 散落，无统一注册 | OpenClaw 风格统一封装 |
| 情感层 | emotion_context → VTS | 孤岛，未接入流式 | 接入所有路径 |

---

## 六、代码取舍建议

### 可以砍掉/简化的部分

| 模块 | 建议 | 原因 |
|------|------|------|
| `app/chat_handler.py`（同步 Graph） | **保留但降级**为复杂任务专用 | 流式路径覆盖日常场景后，同步 Graph 只用于代码/翻译/执行 |
| `integrations/graph_streaming.py` | **重构**：让流式也走召回 | 当前流式绕过了所有召回，需重新设计 |
| `app/chat_stream_handler.py` | **重构**：接入 ContextPack | 当前是裸 LLM 调用 |
| `tools/_diag_*.py` 系列 | **整合**到统一诊断工具 | 太分散，维护成本高 |
| `obs/` 和 `observability/` | **合并**为一个模块 | 两个目录功能重叠 |
| FastAPI 中的 `pre_intent_classifier` | **下沉**到 Graph 入口节点 | 职责错位，应在 Graph 层做路由 |

### 不能砍的部分

| 模块 | 原因 |
|------|------|
| `governor/` | 主权核心，不可动 |
| `audit/` | 审计链，不可动 |
| `retrieval/` | 六路召回，是核心价值 |
| `bond/` | 关系动力学，是差异化能力 |
| `memory/` | Canon 体系，是长期一致性保障 |
| `emotion_context.py` | 情感层，需要扩大接入范围而非删除 |

---

## 七、优先级路线图（2026-03-30 状态更新）

> **当前节点**：Phase X-XIII 已全部完成（2026-03-19 ～ 2026-03-30），进入打磨阶段。

### P0（原"最大功能缺口"）— ✅ **全部已完成**

| 任务 | 描述 | 状态 | 完成方式 |
|------|------|------|----------|
| **流式 Chat 接入五路召回** | chat_stream_handler.py 接入 RetrievalOrchestrator + StateArbiter + RecallPack | ✅ 已完成 | Phase X Agentic RAG |
| **流式 Chat 接入情感层** | VTS 表情实时同步 + 过渡帧动画 + 情感影响生成参数 | ✅ 已完成 | chat_stream_handler.py L908-1030 |

### P1（原"体验提升"）— ✅ **全部已完成**

| 任务 | 描述 | 状态 | 完成方式 |
|------|------|------|----------|
| **Host 端部署 Qwen3 14B** | 默认模型升级为 qwen3:14b（非原计划的 8B），Ollama 本地推理 | ✅ 已完成 | graph_config_builder.py L119，含健康探测+自动降级 |
| **Gaming 模式切换到 Edge 3B** | sys_mode=gaming 时路由到 Edge Qwen3 3B | ✅ 已完成 | chat_handler_edge.py L290-349（需 `HOST_GAMING_EDGE_CHAT_ENABLED=true`） |
| **SceneRouter 下沉到 Graph 入口** | 从 FastAPI 层移出，成为 Graph 的第一个节点 | ✅ 已完成 | main_chain.py L2352-2760 |

### P2（原"架构优化"）— ✅ **全部已完成**

| 任务 | 描述 | 状态 | 完成方式 |
|------|------|------|----------|
| **Skills 层 OpenClaw 风格封装** | SKILL.md 安装式架构 + Hub 目录自动发现注册 | ✅ 已完成 | host_brain/skills/hub/ + install_skill() |
| **obs/ 和 observability/ 合并** | 经代码审查确认分工明确：obs_routes=API 层，observability/=业务逻辑层 | ✅ 不需要合并 | 保持现状 |
| **MCP 工具注册与 Skills 层桥接** | mcp_skill_bridge.py 双向桥接 | ✅ 已完成 | host_brain/skills/mcp_skill_bridge.py |

### P3（原"长期做"）— ✅ **全部已完成**

| 任务 | 描述 | 状态 | 完成方式 |
|------|------|------|----------|
| **Bond 更新异步化** | 4 条路径（stream/complex/gateway/system）全用 queue+fallback | ✅ 已完成 | 异步队列优先，同步兜底 |
| **情感层全路径接入** | Host 14B / Edge 3B / Cloud Graph 三条路径都接入 | ✅ 已完成 | 详见问题 3 状态 |
| **MCP 工具注册与 Skills 层合并** | 同 P2 | ✅ 已完成 | mcp_skill_bridge.py |

### 新增：当前待办（打磨阶段）

| 任务 | 优先级 | 描述 |
|------|--------|------|
| **main_chain.py 拆分** | 中 | 159KB 单文件需拆分为多个模块 |
| **obs_routes.py 拆分** | 中 | 238KB/6460行/50个端点需拆分为按职责分组的路由模块 |
| **QueryPlanner 测试补全** | 低 | 当前覆盖 3/9 意图（33%），需补 chat/recall_memory/emotion/knowledge/tool/complex |
| **TODO 标记清理** | 低 | 212+ 处 TODO 中很多已实现但注释未清理 |
| **ARCH_REVIEW_AND_ROADMAP 架构图刷新** | 低 | 第四/五节的架构图需更新为 Phase XIII 实际架构 |
| **真机端到端验证** | 高 | Phase X-XIII 改动需 RUN 端真机跑一遍 |

---

## 附录：关键决策点

### 决策1：是否保留当前的 Cloud Graph 作为主路径？

**建议：降级为复杂任务专用**

当前 Cloud Graph 是所有请求的主路径，但：
- 日常闲聊不需要 9 节点完整链路
- 云端调用有网络延迟（200-500ms 额外开销）
- 成本高

**结论**：Cloud Graph 保留，但仅用于代码生成、翻译、复杂推理等场景。日常闲聊迁移到 Host Qwen3 8B。

---

### 决策2：流式 Chat 的召回是否会影响首字节延迟？

**不会，如果设计正确**

六路召回可以并行执行，总耗时约 200ms（向量检索 + 重排）。这个时间在用户感知上是可接受的"思考时间"。关键是：
- 召回在 LLM 调用**前**完成（保证质量）
- 审计写入在 LLM 调用**后**异步完成（不阻塞输出）
- 首字节延迟 = 召回时间（200ms）+ LLM 首 token 时间（100-300ms）= 300-500ms

这与当前裸 LLM 调用的首字节延迟（100-300ms）相比，增加了约 200ms，但换来了完整的上下文质量。

---

### 决策3：Qwen3 8B 量化方案

| 量化方式 | VRAM 占用 | 质量损失 | 推荐场景 |
|---------|-----------|---------|---------|
| FP16 | ~16GB | 无 | 高端 GPU（RTX 4090） |
| Q8_0 | ~8GB | 极小 | 中端 GPU（RTX 3080/4070） |
| Q4_K_M | ~5GB | 小 | 主流 GPU（RTX 3070/4060） |
| Q4_0 | ~4.5GB | 中 | 显存紧张时的兜底 |

**建议**：优先使用 Q4_K_M，在 Gaming 模式下卸载模型释放显存，切换到 Edge 3B。

---

## 变更记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v1.0 | 2026-03-09 | 初版，基于全量文档扫描生成，状态：待决策 |
| v2.0 | 2026-03-19 | 更新：P15 终点封板完成（M-MA1～M-MA6 全部达成，220 用例全通过）；路线图状态同步；移除已过时的"待决策"标注 |
| v3.0 | 2026-03-30 | **大更新**：基于代码实际验证（非文档推测），确认 P0-P3 路线图全部已完成。Phase X Agentic RAG + Phase XIII 风格重构后，流式 Chat 已完整接入五路召回+情感层+VTS。Host 14B / Gaming Edge 3B / Skills Hub / Bond 异步化全部落地。更新问题诊断（4 个中 3 个已解决）。新增打磨阶段待办清单。|

---

*文档生成时间：2026-03-09 18:20*
*v3.0 更新时间：2026-03-30 16:20*
*当前阶段：Phase X-XIII 全部完成，进入代码质量打磨和真机验证阶段*
