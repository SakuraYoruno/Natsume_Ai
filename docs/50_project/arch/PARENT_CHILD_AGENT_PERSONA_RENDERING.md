受众：MASTER
标题：Phase XIV — 顶级 Agent 架构重构方案 v4（最终方案）

# Phase XIV — 顶级 Agent 架构重构方案 v4

## 修订历史

| 版本 | 日期 | 内容 | 废弃原因 |
|------|------|------|---------|
| v1 | 2026-03-31 | 后置渲染补丁 | 治标不治本 |
| v2 | 2026-03-31 | 顶级 Agent + Function Calling | 违反白皮书 6 条 |
| v3 | 2026-03-31 | Renderer 指令升级 | 只改 20 行但不解决架构臃肿 |
| **v4** | **2026-03-31** | **顶级 Agent + 治理下沉 + 审计精简** | **最终方案** |

---

## 1. 核心洞察

### 1.1 白皮书的治理对象是"执行动作"，不是"说话"

白皮书 §5 单向链：
> 输入 → 语义内核 → Governor → **Skills/Actuators（执行）** → Audit

§10 三联画只审计三件事：
1. **intent_log** — 语义内核提出了什么执行提案
2. **governor_log** — Host 批准/拒绝了什么执行提案
3. **skill_exec_log** — 技能实际执行了什么

**闲聊不是执行。** 如果没有 Skills/Actuators 需要触发，Governor 和 Audit 不在链路上。

### 1.2 顶级 Agent 调用子 Agent ≠ 绕过治理

哥哥的核心论点：**子 Agent 内部已有治理，顶级 Agent 只是调度者，不需要被治理约束。**

类比：公司 CEO 决定"这个项目交给 A 部门做"——这个决定本身不需要审计委员会批准。但 A 部门实际执行项目时，该走的审批流程一个不少。

### 1.3 当前架构的荒谬

一条"你好"要过 10 个节点、产生 35-40 个 metadata key、8-15 个 audit_tags、3-5 条 SQLite 写入、3-8 KB 的 COMMIT audit_refs。白皮书对此的要求是：**零**。

---

## 2. 新架构总览

```
用户消息
  │
  ▼
┌──────────────────────────────────────────────────────┐
│            顶级 Agent（夏目蓝本人）                      │
│                                                      │
│  常驻 prompt：角色人设 + 说话风格 + 台词参考             │
│              + Bond + 情感 + 检索上下文                 │
│                                                      │
│  ┌─ 入口安全 ──────────────────────────────────────┐  │
│  │ Airlock 入口净化（null byte / 空消息 / 注入检测） │  │
│  └─────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ 核心 LLM 调用 ────────────────────────────────┐  │
│  │                                                │  │
│  │  LLM 同时完成：                                 │  │
│  │    1) 意图识别（闲聊 vs 执行）                   │  │
│  │    2) 闲聊 → 直接生成回复                        │  │
│  │    3) 执行 → 输出子 Agent 调用指令               │  │
│  │                                                │  │
│  └────────────────────────────────────────────────┘  │
│            │                        │                │
│        闲聊路径                  执行路径              │
│            │                        │                │
│            ▼                        ▼                │
│  ┌─ 直接回复 ──┐       ┌─ 子 Agent 调度 ──────────┐  │
│  │ 拟人化回复   │       │                          │  │
│  │（天然带人设） │       │  code_agent ──┐          │  │
│  └──────────────┘       │  translate_agent │ 并行   │  │
│                         │  intent_agent ──┘          │  │
│                         │  vision_agent              │  │
│                         │                          │  │
│                         │  每个子 Agent 内部：       │  │
│                         │  ├─ Governor（审批执行）   │  │
│                         │  ├─ 执行任务              │  │
│                         │  ├─ Audit（三联画）        │  │
│                         │  └─ 返回结果              │  │
│                         └──────────────────────────┘  │
│                                    │                 │
│                                    ▼                 │
│                         ┌─ 拟人化回复 ──────────┐    │
│                         │ 顶级 Agent 收到结果    │    │
│                         │ 以夏目蓝的方式组织语言  │    │
│                         │（无需额外 LLM 调用）    │    │
│                         └────────────────────────┘    │
│                                                      │
│  ┌─ 出口安全 ──────────────────────────────────────┐  │
│  │ Airlock 出口净化（安全词 / null byte / 长度）     │  │
│  └─────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─ 异步后处理（不阻塞回复）──────────────────────┐  │
│  │ • 记忆写入（EpisodicStore）                     │  │
│  │ • Bond 关系更新                                 │  │
│  │ • VTS 情感驱动                                  │  │
│  │ • TTS 语音播报                                  │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## 3. 闲聊路径详细设计（80% 的交互）

### 3.1 流程

```
用户消息
  → Airlock 入口净化（<1ms，纯规则）
  → RecallFusion 检索（并行启动，不阻塞 LLM）
  → LLM 流式生成（prompt 含角色人设 + 风格 + 检索结果 + 情感 + Bond）
  → Airlock 出口净化（<1ms，纯规则）
  → SSE 流式推送给前端
  → 异步后处理（记忆 / Bond / VTS / TTS）
```

### 3.2 不经过的组件

| 组件 | 为什么不需要 |
|------|------------|
| Governor R1-R7 | 闲聊不是执行动作 |
| 三联画审计 | 没有执行，没有审计对象 |
| LangGraph 图引擎 | 不需要多节点编排 |
| pre_intent_classifier | LLM 自己判断 |
| SceneRouter | 不需要路由 |
| RoutingMatrix | 闲聊固定用一个模型 |
| PermissionGuard | 没有工具调用 |
| QAContractNode 完整版 | 出口净化足够 |
| AuditNode | 无审计对象 |
| CanonProposalNode | 闲聊不触发 Canon 更新 |

### 3.3 保留的组件

| 组件 | 作用 | 位置 |
|------|------|------|
| Airlock 入口 | 安全基本功 | 入口同步 |
| RecallFusion | 提供上下文 | 与 LLM 并行 |
| 情感上下文 | 调制语气 | prompt 注入 |
| Bond 上下文 | 调制亲密度 | prompt 注入 |
| character_profile | 角色人设 | prompt 常驻 |
| style_canon v5 | 说话风格 | prompt 常驻 |
| persona_examples | 台词参考 | prompt 注入 |
| Airlock 出口 | 安全过滤 | 出口同步 |
| 记忆写入 | 长期记忆 | 异步不阻塞 |
| Bond 更新 | 关系维护 | 异步不阻塞 |
| VTS/TTS | 表情/语音 | 异步不阻塞 |

### 3.4 延迟对比

| | 现在 | 新架构 |
|--|------|--------|
| 同步阻塞 | 10 节点串行 + LLM | Airlock(<1ms) + LLM + Airlock(<1ms) |
| 异步 | 无 | 记忆 + Bond + VTS + TTS |
| 预估首字延迟 | ~2s | **~0.8-1s**（快一倍） |

---

## 4. 执行路径详细设计（20% 的交互）

### 4.1 顶级 Agent 如何知道要调子 Agent？

**LLM 自己判断。** 顶级 Agent 的 prompt 里包含子 Agent 的能力描述，LLM 在生成回复时自然地决定：

```
场景 A：用户说"帮我写个快排"
  LLM 判断：需要代码能力 → 回复中包含 [DELEGATE:code_agent] 标记
  → 解析标记 → 调用 code_agent → 拿到结果 → 顶级 Agent 组织最终回复

场景 B：用户说"今天好累"
  LLM 判断：闲聊 → 直接回复"辛苦了呢，要不要先休息一下？"
  → 不触发任何子 Agent
```

### 4.2 子 Agent 调用协议

```python
class SubAgentRequest:
    agent_id: str           # "code_agent" / "translate_agent" / "intent_agent" / "vision_agent"
    task: str               # 自然语言任务描述
    context: dict           # 用户消息、会话上下文等
    timeout_ms: int = 30000 # 超时

class SubAgentResponse:
    success: bool
    result: str             # 技术产出（代码/翻译/执行结果）
    audit_refs: dict        # 三联画审计摘要（仅执行型子 Agent 才有）
    latency_ms: int
```

### 4.3 子 Agent 内部治理（白皮书合规）

每个子 Agent 内部是一个**轻量级治理闭环**：

```
子 Agent 收到任务
  → Governor 审批（仅针对执行动作，规则精简）
    ├─ APPROVE → 执行 → skill_exec_log → 返回结果
    └─ REJECT  → 返回拒绝原因 → 顶级 Agent 组织拒绝回复
```

**子 Agent 不需要**：
- 角色人设 / 说话风格（它只管"做事"，不管"说话"）
- RecallFusion（顶级 Agent 已检索）
- 情感系统（它不面向用户）
- 出口净化（顶级 Agent 统一做）
- 记忆写入（顶级 Agent 统一做）

### 4.4 拟人化回复生成

执行路径的拟人化有两种方式（按复杂度递增）：

**方式 A：单次 LLM 调用（推荐）**

顶级 Agent 的一次 LLM 调用中，同时完成"调度子 Agent"和"组织回复"：

```
LLM 输出（结构化 JSON）：
{
  "delegate": {"agent": "code_agent", "task": "写快排算法"},
  "reply_template": "好的，这就帮哥哥写~\n{code_result}\n写好啦，要不要七海解释一下？"
}
```

子 Agent 返回代码后，直接填入 `reply_template` 的 `{code_result}` 占位符。

**零额外 LLM 调用。**

**方式 B：两次 LLM 调用（复杂任务）**

如果子 Agent 返回的结果需要复杂的解读/总结：

1. 第一次 LLM：判断意图 + 调度子 Agent
2. 子 Agent 执行并返回结果
3. 第二次 LLM：以夏目蓝的身份，基于子 Agent 结果组织最终回复

prompt 里已有完整角色人设，天然拟人化。

### 4.5 子 Agent 并行

```
用户："帮我写个快排，顺便翻译成英文"

顶级 Agent：
  → 同时调度 code_agent + translate_agent
  → asyncio.gather([code_agent.run(), translate_agent.run()])
  → 两个结果合并 → 组织回复
```

---

## 5. 审计精简方案

### 5.1 新的审计原则

**只审计"有后果的事"。**

| 事件类型 | 是否审计 | 审计内容 |
|---------|---------|---------|
| 闲聊回复 | ❌ | — |
| 子 Agent 执行代码 | ✅ | intent_log + governor_log + skill_exec_log |
| 子 Agent 执行命令 | ✅ | 同上 |
| 子 Agent 翻译 | ❌ | 翻译不是执行动作 |
| Gateway 发送消息 | ✅ | channel_io_log |
| 记忆写入 | ❌ | 功能性操作，不是治理对象 |
| Bond 更新 | ❌ | 同上 |
| Governor 拒绝 | ✅ | governor_log（仅记录拒绝事件） |
| 安全事件 | ✅ | error_log（注入检测、安全词泄露） |

### 5.2 SQLite 表精简

| 保留 | 原因 |
|------|------|
| intent_log | 三联画 ① |
| governor_log | 三联画 ② |
| skill_exec_log | 三联画 ③ |
| error_log | 安全事件 + 系统错误 |
| channel_io_log | Gateway 发送记录 |

| 废弃/合并 | 原因 |
|-----------|------|
| llm_node_call_log | 运维可观测性，改用 structured logging |
| graph_run_trace | LangGraph 废弃后不需要 |
| canon_proposal_log | 移入 memory_ops_log |
| 20+ 张其他表 | 合并到 error_log 或改用日志 |

**从 25+ 张表 → 5 张表。**

### 5.3 COMMIT SSE 事件精简

**现在（16-18 个字段，3-8 KB）：**
```json
{
  "audit_refs": {
    "intent_id": "...",
    "governor_stage": "...",
    "thread_id": "...",
    "retrieval": { /* 大字典 */ },
    "llm": { /* 大字典，含完整 nodes 数组 */ },
    "prompt_context_validation": { ... },
    "prompt_context_observability": { ... },
    "scene_type": "...",
    "scene_type_source": "...",
    "route_decision": { ... },
    "emotion_context": { ... },
    "emotion_snapshot": { ... },
    "vts": { ... },
    "tts": { ... },
    "episodic_memory": { ... },
    "response_intelligence": { ... },
    "persona_rendering": { ... },
    "trace_metrics": { ... }
  }
}
```

**新方案（3-5 个字段，<1 KB）：**
```json
{
  "audit_refs": {
    "request_id": "...",
    "latency_ms": 800,
    "path": "chat_direct",
    "emotion_tag": "HAPPY",
    "delegated_agents": []
  }
}
```

执行路径时多一个 `sub_agent_results` 摘要：
```json
{
  "audit_refs": {
    "request_id": "...",
    "latency_ms": 3200,
    "path": "delegate",
    "delegated_agents": ["code_agent"],
    "sub_agent_audit": {
      "code_agent": {
        "governor": "APPROVE",
        "exec_status": "OK",
        "latency_ms": 2400
      }
    }
  }
}
```

### 5.4 audit_tags 精简

**现在：53 个追加点，每次 8-15 个。**

**新方案：仅保留安全和治理相关（~8 个可能的 tag）：**

| 保留 | 用途 |
|------|------|
| `AIRLOCK_BLOCKED` | 入口安全拦截 |
| `GOVERNOR_REJECT:{reason}` | 子 Agent 执行被拒 |
| `GOVERNOR_APPROVE` | 子 Agent 执行被批准 |
| `SKILL_EXEC:{agent}:{status}` | 执行结果 |
| `SAFETY_WARN:{type}` | 安全告警（注入/泄露） |
| `DELEGATE:{agent}` | 子 Agent 调度记录 |

**砍掉的全部内容**：`EMOTION_*`（8种）、`RECALL_*`（6种）、`QA_*`（5种）、`MEMORY_GUARD_*`（4种）、`STATE_ARBITER_*`、`ROUTING_*`、`PERMISSION_*`、`GRAPH_EXECUTOR_*`、`CTX_COMPRESSED_*`、`SESSION_OVERRIDE_*`、`CANON_*` 等。

---

## 6. 实施分阶段

### Phase 1+2（一步到位）：顶级 Agent + 子 Agent 调度

**目标**：闲聊路径直通 + 执行路径走子 Agent，全面替代旧链路。

**做什么**：
1. 新建 `host_brain/app/top_agent.py` — 顶级 Agent 入口
2. 新建 `host_brain/app/top_agent_prompt.py` — prompt 构建（复用现有 prompt 组件）
3. 新建 `host_brain/app/sub_agent_protocol.py` — SubAgentRequest/Response + 调度器
4. 新建 `host_brain/app/sub_agent_governor.py` — 子 Agent 内部轻量级 Governor
5. 改造 `chat_handler.py` — 添加开关 `HOST_TOP_AGENT_ENABLED`，开启后走新链路
6. 适配现有 handler（code/translate/intent）为子 Agent
7. 子 Agent 内部保留 LangGraph 图引擎（翻译→校对→润色等多节点）
8. Airlock 入口/出口 → LLM 流式 → 异步后处理
9. COMMIT SSE 精简为 3-5 个字段
10. 闲聊 Governor 极轻量版（速率限制 + sys_mode）

**验证**：闲聊 + 代码 + 翻译 + 执行全面测试。有问题直接 git 回退。

### Phase 3：废弃旧链路 + 审计精简

**目标**：彻底移除旧组件。

**做什么**：
1. 移除 MainChain 10 节点 + LangGraph 图引擎
2. 移除 SceneRouter / pre_intent_classifier
3. SQLite 表从 25+ → 5
4. 移除 53 个冗余 audit_tags
5. 清理 `chain_nodes.py`（2137 行 → 删除或归档）

**验证**：所有场景正常，代码量大幅减少，审计数据干净。

### Phase 4：优化（可选）

- 闲聊路径的 RecallFusion 简化（不需要七路全开）
- 情感系统精简（砍掉 LLM 级联推理，只保留轻量规则）
- 子 Agent 并行调度
- prompt 缓存优化

---

## 7. 代码量估算

### 新增

| 文件 | 预估行数 | 职责 |
|------|---------|------|
| `top_agent.py` | ~300 | 顶级 Agent 入口：Airlock → LLM → Airlock → 异步后处理 |
| `top_agent_prompt.py` | ~200 | prompt 构建（复用 prompt_builder 的组件） |
| `sub_agent_protocol.py` | ~100 | SubAgentRequest/Response + 调度器 |
| `sub_agent_governor.py` | ~150 | 子 Agent 内部轻量级 Governor |

**总新增：~750 行**

### 废弃

| 文件/模块 | 行数 | 说明 |
|-----------|------|------|
| `chain_nodes.py` | 2137 | 10 节点全部废弃 |
| `chain_types.py` | ~200 | ChainContext 等类型 |
| `main_chain.py` | ~600 | MainChain 编排 |
| `scene_router.py` 大部分 | ~400 | 场景路由 |
| `graph_config_builder.py` 的图构建部分 | ~600 | build_chat_graph / build_supervised_graph / build_intent_graph |
| `chat_handler_complex.py` | 963 | 复杂场景处理器 |
| 各种 handler 的独立管线 | ~500 | 不再需要独立管线 |

**总废弃：~5400 行**

**净减少：约 4600 行代码。**

---

## 8. 对白皮书的合规分析

| 白皮书条款 | v4 是否合规 | 说明 |
|-----------|-----------|------|
| §5 单向链 | ✅ | 执行路径在子 Agent 内部走完整单向链 |
| §10 三联画 | ✅ | 子 Agent 内部产生 intent_log + governor_log + skill_exec_log |
| §4.2 语义内核禁止事项 | ✅ | 顶级 Agent 不直接触发执行，委托子 Agent |
| §0.4 输出封装 | ✅ | 子 Agent 内部的 LLM 调用遵守 Strict JSON |
| Rule MA-1 LLM 不直接控图 | ✅ | 没有图了。子 Agent 调度通过结构化标记，不是 LLM 直接控制 |
| §11.8 严禁 | ✅ | 顶级 Agent 不是"纯 LLM node"——它是完整的应用层，包含 Airlock/检索/后处理 |
| §1 主权 | ✅ | Host 主权通过子 Agent 内部的 Governor 行使 |
| I1 skill_exec 必须有 APPROVE | ✅ | 子 Agent 内部执行 |
| I13 输出封装 | ✅ | 顶级 Agent 面向用户的输出不需要 JSON 封装（那是内部节点间的约束） |

**核心论点**：白皮书约束的是"执行动作的治理路径"，不是"回复消息的处理路径"。顶级 Agent 负责"说话"，子 Agent 负责"做事"。"做事"走完整治理，"说话"不需要治理。

---

## 9. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| 顶级 Agent 误判意图（该调子 Agent 没调） | 中 | LLM 意图识别准确率远高于关键词匹配；可用 few-shot 示例提高 |
| 子 Agent 执行结果质量下降 | 低 | 子 Agent 复用现有 handler，执行能力不变 |
| 闲聊没有 Governor 导致安全问题 | 极低 | Airlock 入口/出口净化 + LLM 自身安全能力 + 出口安全词检测 |
| 灰度期间两套链路并存增加复杂度 | 中 | `HOST_TOP_AGENT_ENABLED` 开关，可随时回退 |
| 记忆写入异步化导致丢失 | 低 | 异步任务有 fire-and-forget + 日志记录，不影响用户体验 |

---

## 10. 决策记录（2026-03-31 哥哥确认）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 开关名 | `HOST_TOP_AGENT_ENABLED`（默认 false）|
| 2 | 子 Agent 调度方式 | 默认方式 A（结构化标记 + 模板填充），复杂场景 fallback 方式 B |
| 3 | 闲聊 Governor | 保留极轻量版（仅速率限制 + sys_mode 检查，~10 行） |
| 4 | v1 补丁代码 | Phase 3 统一清理时删除 |
| 5 | LangGraph | **保留为子 Agent 内部能力**。子 Agent（如翻译 Agent）需要多 LLM 节点（翻译→校对→润色），内部图引擎有存在价值 |
| 6 | 实施节奏 | **Phase 1 + 2 一步到位**，有问题直接 git 回退 |

---

## 11. 一句话总结

**夏目蓝自己说话不需要审批。她叫别人做事的时候，做事的人自己负责过审批。**
