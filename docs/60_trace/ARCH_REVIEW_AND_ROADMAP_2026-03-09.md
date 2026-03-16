# Natsume_Ai 架构评审与优化路线图

**文档版本**：v1.0
**日期**：2026-03-09
**状态**：待决策
**作者**：架构评审（基于全量文档扫描）

---

## 目录

1. [当前架构核心问题诊断](#一当前架构核心问题诊断)
2. [三个优化方向可行性分析](#二三个优化方向可行性分析)
3. [流式Chat的正确架构设计](#三流式chat的正确架构设计)
4. [新架构分层方案](#四新架构分层方案)
5. [代码取舍建议](#五代码取舍建议)
6. [优先级路线图](#六优先级路线图)

---

## 一、当前架构核心问题诊断

### 🔴 问题1：流式 Chat 绕过了 Graph，是一个"残缺路径"

当前路由：
```
GET /v1/chat/stream  →  绕过 Graph，直接 astream_llm_chunks
```

流式 Chat 完全跳过了：
- ❌ 六路召回（Episodic / StyleCanon / WorldCanon / Bond / 全文检索 / 重排）
- ❌ 情感层（`emotion_context.py` 存在但未接入流式路径）
- ❌ Governor 治理层
- ❌ 审计链写入
- ❌ 两段式输出（中文语义内核 → 日文渲染）

**本质：流式 Chat 现在只是一个"裸 LLM 调用"，与整个架构设计完全脱节。**

---

### 🔴 问题2：模型路由交给 FastAPI 是职责错位

当前 `pre_intent_classifier` 运行在 FastAPI 端点层，由 FastAPI 决定走哪条 Graph 路径。问题如下：

1. **FastAPI 是 I/O 层，不是决策层**。路由决策属于 Host 主权域，应在 Graph 入口或 Governor 层做，而不是在 HTTP 路由层
2. **场景分类逻辑散落在 HTTP handler 里**，导致流式路径根本没有场景分类
3. **违反权力分立原则**：解释器（LLM）和仲裁者（Host）之间的路由决策被下放到了 I/O 层

---

### 🔴 问题3：情感层（emotion_context.py）是孤岛

`host_brain/app/emotion_context.py` 存在，但：
- 流式路径完全没有接入
- Graph 路径中情感层接入情况不明确
- VTS 情感驱动（emotion_tag → Live2D）与 LLM 输出的情感信号之间缺乏闭环

---

### 🟡 问题4：代码路线过于复杂，维护成本高

当前 `host_brain/` 有 25+ 个子模块，`integrations/` 下有 15+ 个 graph 相关文件：
- 测试文件散落在根目录（`test_*.py`）
- `tools/` 下有大量 `_diag_*.py` 脚本，说明调试成本很高
- `obs/` 和 `observability/` 两个目录功能重叠
- 没有统一的 Skills 封装层

---

## 二、三个优化方向可行性分析

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

## 三、流式Chat的正确架构设计

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

## 四、新架构分层方案

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

## 五、代码取舍建议

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

## 六、优先级路线图

### P0（立即做）— 最大功能缺口

| 任务 | 描述 | 预估工作量 |
|------|------|-----------|
| **流式 Chat 接入六路召回** | 当前流式输出是"无记忆的裸 LLM"，这是最大的功能缺口 | 3-5天 |
| **流式 Chat 接入情感层** | emotion_context → VTS 驱动，让流式输出也能驱动 Live2D | 1-2天 |

### P1（近期做）— 体验提升

| 任务 | 描述 | 预估工作量 |
|------|------|-----------|
| **Host 端部署 Qwen3 8B** | 替代云端 CHAT 场景，降低延迟和成本 | 2-3天（含量化调优） |
| **Gaming 模式切换到 Edge 3B** | 而不是退化到模板，保持互动质量 | 1-2天 |
| **SceneRouter 下沉到 Graph 入口** | 从 FastAPI 层移出，成为 Graph 的第一个节点 | 2天 |

### P2（中期做）— 架构优化

| 任务 | 描述 | 预估工作量 |
|------|------|-----------|
| **Skills 层 OpenClaw 风格封装** | 统一 `@skill` 注册，支持 Cursor/PyCharm/Codex 调用 | 5-7天 |
| **obs/ 和 observability/ 合并** | 消除重复，降低维护成本 | 1天 |
| **诊断工具整合** | 将 `_diag_*.py` 系列整合为统一诊断入口 | 1-2天 |

### P3（长期做）— 完整闭环

| 任务 | 描述 |
|------|------|
| **Bond 更新异步化** | 所有路径的 Bond 更新统一走后台异步队列 |
| **情感层全路径接入** | Host 8B / Edge 3B / Cloud Graph 三条路径都接入情感层 |
| **MCP 工具注册与 Skills 层合并** | 消除两套体系，统一工具调用入口 |

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

*文档生成时间：2026-03-09 18:20*
*下一步：确认 P0 任务的实施方案，开始流式 Chat 召回接入的代码设计*
