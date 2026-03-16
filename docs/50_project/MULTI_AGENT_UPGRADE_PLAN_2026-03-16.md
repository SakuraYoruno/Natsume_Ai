受众：MASTER
标题：多 Agent 编排升级详细实施方案

版本：v1.0
日期：2026-03-16
定位：基于当前 Natsume AI 单 agent 多节点路由架构，制定分阶段升级为"主 agent + 多专家子 agent"编排体系的可落地实施方案。
关联文档：
- docs/50_project/AGENT_ORCHESTRATION_DETAIL_2026-03-09.md（当前编排现状）
- docs/30_devspec/ModelPlane.md（模型平面规格与治理约束）
- docs/15_theory/LangGraph_Governed_Runtime_Solution_2026-03-06.md（Governed Runtime 理论基础）

---

## 一、升级背景与目标

### 1.1 当前架构现状

当前系统本质是**单 agent + 受控多节点图路由**，不是多个 agent 各自独立运行的协作体系。

核心特征：
- LangGraph 物理拓扑只有 `START → step → finish → END` 单步循环
- `chat_direct_ja`、`core_reasoner_primary` 等是 `step` 内部通过 `pending_node_id + edge_map` 驱动的**逻辑节点**，不是独立 agent
- 场景路由由 `pre_intent_classifier` 在入口完成，之后走固定节点链
- 所有场景共用同一个执行上下文，没有 agent 间的 handoff 契约

当前各场景节点链：

| 场景 | 节点链 |
|------|--------|
| `SCENE_CHAT` | `chat_direct_ja`（单节点快路径） |
| `SCENE_WORK_CODE` | `core_reasoner_primary → workbench_code_assist → consistency_verifier → renderer_ja_c_style` |
| `SCENE_WORK_TRANSLATE` | `core_reasoner_primary → workbench_translate → consistency_verifier → renderer_ja_c_style` |
| `SCENE_EXEC_INTENT` | `core_reasoner_primary → planner_constraints → tool_planner → safety_verifier → consistency_verifier → renderer_ja_c_style` |
| `SCENE_VISION_UI` | 同 `SCENE_EXEC_INTENT` |

### 1.2 升级目标

升级为：**Host 主权 + Supervisor 主 agent + 多专家子 agent + Judge 收口**

目标特征：
- 主 agent（Supervisor）负责任务拆解、子 agent 选派、预算控制、停止条件
- 子 agent 各自专注单一职责，先以单 LLM 节点形态落地
- Judge/Merge 统一收口，裁判汇总
- Host Governor 保留最终裁决、commit、审计闭锁权
- 聊天场景继续保留快路径，不默认进重审查链

### 1.3 硬性约束（不可突破）

来自 `ModelPlane.md` 的治理约束，升级过程中必须严格遵守：

1. **LangGraph 定位固定为 governed runtime，不是 autonomous orchestrator**
2. **`committed_route_token` 只能由 Host Governor 写入**，任何 agent 不得直接写
3. **条件边只能基于 `committed_route_token`**，不能基于 LLM 原始输出
4. **model node 不得直接输出 `__next_node__`**，只有 trusted handler 可以写控制字段
5. **子 agent 不得自闭环有副作用的工具调用**

---

## 二、目标架构设计

### 2.1 整体架构图

```
用户请求
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Airlock / Governor（不变）                                  │
│  - 速率限制、防重放、sys_mode 检查                           │
│  - 写入 committed_route_token                               │
└─────────────────────────────────────────────────────────────┘
    │ GOV_OK_RENDER
    ▼
┌─────────────────────────────────────────────────────────────┐
│  Supervisor Handler（新增）                                  │
│  - 读取 scene_type + 用户意图                               │
│  - LLM 产出"分派提案"（不直接控图）                          │
│  - Handler 校验提案 → 编译为 agent_runtime.plan             │
│  - 写入 __next_node__（trusted handler 专属权）              │
└─────────────────────────────────────────────────────────────┘
    │
    ├──────────────────────────────────────────────────────────┐
    │                                                          │
    ▼                                                          ▼
SCENE_CHAT                                          AUTO / 其他场景
    │                                                          │
    ▼                                                          ▼
chat_agent                                         code_agent / translate_agent
（快路径，不过 Supervisor）                          intent_agent / retrieval_agent
    │                                                          │
    ▼                                                          ▼
light_guard（可选）                                  judge_agent（统一收口）
    │                                                          │
    └──────────────────────┬───────────────────────────────────┘
                           │
                           ▼
                    renderer_ja_c_style
                           │
                           ▼
                    Host Commit / Audit（不变）
```

### 2.2 角色定义与职责

| 角色 | 形态（第一阶段） | 复用现有节点 | 可先单节点 | 职责 |
|------|----------------|-------------|-----------|------|
| **Supervisor** | handler + LLM proposal | 新增 handler | **否** | 选子 agent、设预算、决定停止条件 |
| **ChatAgent** | model node | `chat_direct_ja` | **是** | 普通聊天快路径 |
| **CodeAgent** | model node | `workbench_code_assist` | **是** | 代码草案生成 |
| **TranslateAgent** | model node | `workbench_translate` | **是** | 翻译草案生成 |
| **IntentAgent** | 多节点链（暂不简化） | `core_reasoner_primary + planner_constraints + tool_planner` | **否** | 执行意图，涉及工具调用 |
| **SafetyAgent** | model node / 现有链 | `safety_verifier` | 条件启用 | 仅中高风险 / 执行场景开启 |
| **JudgeAgent** | model node | `consistency_verifier` | **是** | 接收子 agent 输出、裁判、归并 |
| **Renderer** | model node | `renderer_ja_c_style` | **是** | 最终成文输出 |

### 2.3 数据契约设计

#### agent_runtime 结构（第一阶段放入 payload）

```json
{
  "agent_runtime": {
    "plan_id": "plan_req_xxx_001",
    "active_agent_id": "code_agent",
    "agent_hops": 1,
    "max_hops": 2,
    "risk_mode": "guarded",
    "need_judge": true,
    "budget": {
      "max_hops": 2,
      "per_agent_timeout_ms": 12000,
      "total_budget_ms": 30000
    },
    "artifacts": {
      "code_agent": {
        "artifact_ref": "artifact://req_xxx/code_draft_01",
        "summary": "生成了修复方案与代码建议",
        "confidence": 0.83,
        "risk_flags": []
      }
    },
    "handoff_history": [
      {
        "from": "supervisor",
        "to": "code_agent",
        "reason": "用户请求编码帮助",
        "ts_ms": 1710000000000
      }
    ]
  }
}
```

#### 子 agent 输出 artifact contract

```json
{
  "agent_id": "code_agent",
  "artifact_kind": "draft_answer",
  "draft_summary": "生成了修复方案与代码建议",
  "artifact_ref": "artifact://req_xxx/code_draft_01",
  "confidence": 0.83,
  "risk_flags": [],
  "handoff_recommendation": "judge"
}
```

**注意**：`handoff_recommendation` 只是建议，真正下一跳由 handler 编译决定，子 agent 不直接控图。

#### Supervisor 分派提案（LLM 输出，不直接控图）

```json
{
  "selected_agent": "code_agent",
  "reason": "用户明确要写代码",
  "risk_mode": "guarded",
  "need_judge": true,
  "budget": {
    "max_hops": 2,
    "timeout_ms": 12000
  }
}
```

Handler 校验此提案后，才写入 `__next_node__` 和 `agent_runtime.plan`。

---

## 三、各场景升级后链路

### 3.1 纯聊天（SCENE_CHAT）

**升级前：**
```
chat_direct_ja → END
```

**升级后：**
```
chat_direct_ja（chat_agent）→ [light_guard 条件插入] → renderer → END
```

**策略：**
- 显式 `SCENE_CHAT` **继续保留快路径**，不默认经过 Supervisor
- 不默认经过 `safety_verifier`
- 只保留：基础边界约束、输出轻校验、高风险时条件插入安全节点
- 聊天应是"**轻治理 + 强边界**"，不是"执行级重审查"

### 3.2 代码场景（SCENE_WORK_CODE）

**升级前：**
```
core_reasoner_primary → workbench_code_assist → consistency_verifier → renderer_ja_c_style
```

**升级后：**
```
supervisor_dispatch → code_agent（workbench_code_assist）→ judge_agent（consistency_verifier）→ renderer_ja_c_style
```

**变化点：**
- `core_reasoner_primary` 的职责并入 Supervisor 的 LLM proposal 阶段
- `workbench_code_assist` 升格为 `CodeAgent`，只产出草案
- `consistency_verifier` 升格为 `JudgeAgent`，统一收口

### 3.3 翻译场景（SCENE_WORK_TRANSLATE）

**升级前：**
```
core_reasoner_primary → workbench_translate → consistency_verifier → renderer_ja_c_style
```

**升级后：**
```
supervisor_dispatch → translate_agent（workbench_translate）→ judge_agent（consistency_verifier）→ renderer_ja_c_style
```

### 3.4 执行意图场景（SCENE_EXEC_INTENT）

**升级前：**
```
core_reasoner_primary → planner_constraints → tool_planner → safety_verifier → consistency_verifier → renderer_ja_c_style
```

**升级后（第一阶段，保留重链）：**
```
supervisor_dispatch → intent_agent_chain（core_reasoner_primary + planner_constraints + tool_planner）→ safety_verifier → judge_agent → renderer_ja_c_style
```

**第二阶段（子图化）：**
```
supervisor_dispatch → intent_agent_subgraph → safety_verifier → judge_agent → renderer_ja_c_style
```

**策略：**
- 第一阶段**不简化执行意图的安全链**，保留 `safety_verifier`
- 第二阶段再把 `intent_agent_chain` 封装为透明子图

### 3.5 自动分流（SCENE_TYPE_AUTO）

**升级后：**
```
supervisor_dispatch → [chat_agent | code_agent | translate_agent | intent_agent] → judge_agent → renderer_ja_c_style
```

---

## 四、分阶段实施路径

### 第一阶段：语义多 agent 化（不改 runtime 结构）

**目标：** 让系统"看起来是多 agent"，实际仍跑在当前单图 step-loop 上

**时间估算：** 1～2 周

**具体工作：**

#### 4.1.1 新增 supervisor_dispatch handler

文件：`host_brain/app/handlers/supervisor_dispatch_handler.py`（新建）

职责：
- 读取 `scene_type`、`payload.user_input`、`agent_runtime` 预算
- 调用 LLM 产出分派提案（JSON 格式）
- 校验提案合法性（agent_id 在 allowlist 内、risk_mode 合规）
- 写入 `payload["agent_runtime"]["plan"]`
- 写入 `__next_node__`（trusted handler 专属权）

关键约束：
- LLM 输出只是提案，handler 负责校验和编译
- 不得直接把 LLM 输出的 `selected_agent` 写入 `__next_node__`，必须经过 allowlist 校验

#### 4.1.2 更新 graph_config_builder.py

文件：`host_brain/app/graph_config_builder.py`

变更：
- 为 `SCENE_TYPE_AUTO` 配置 `supervisor_dispatch` 入口节点
- 保留显式 `SCENE_CHAT` 的 `CHAT_DIRECT_NODE_ID` 快路径（不变）
- 为 `SCENE_WORK_CODE`、`SCENE_WORK_TRANSLATE` 在节点链前插入 `supervisor_dispatch`（可选，灰度控制）

#### 4.1.3 更新 graph_runtime.py

文件：`host_brain/integrations/graph_runtime.py`

变更：
- 初始化 `payload["agent_runtime"]` 结构
- 注入默认预算（`max_hops`、`per_agent_timeout_ms`、`risk_mode`）
- 打上多 agent 开关标记（`multi_agent_enabled`）

#### 4.1.4 硬化 graph_executor.py 的控制字段剥离

文件：`host_brain/integrations/graph_executor.py`

变更：
- 增加 **model output 控制字段剥离**：所有 model node 输出统一剥离以下字段：
  - `__next_node__`
  - `committed_*`
  - `branch_specs`
  - 其他运行时控制键
- 增加 **trusted handler allowlist**：只有 allowlist 内的 handler 可以写控制字段
- 在 `presence_events` 中增加 agent 切换事件（只用于观测，不反向作为图输入）：
  - `AGENT_SELECTED`
  - `AGENT_START`
  - `AGENT_END`
  - `AGENT_HANDOFF`

#### 4.1.5 优化 chat_handler.py 的误路由问题

文件：`host_brain/app/chat_handler.py`

变更：
- 降低 `pre_intent_classifier` 的误升率（普通聊天被误打成 `EXEC_INTENT` 的比例）
- 强化 `narrative_only` 快通道
- 显式聊天的 fast-pass 不经过 Supervisor

**第一阶段产物：**
- `chat_agent`（= `chat_direct_ja`，快路径不变）
- `code_agent`（= `workbench_code_assist`，语义升格）
- `translate_agent`（= `workbench_translate`，语义升格）
- `judge_agent`（= `consistency_verifier`，统一收口）
- `supervisor_dispatch`（新增 handler）
- `renderer`（= `renderer_ja_c_style`，不变）

---

### 第二阶段：复杂 agent 透明子图化

**目标：** 让复杂场景有真正的"agent 内部链路"，同时保持审计透明

**时间估算：** 2～3 周

**具体工作：**

#### 4.2.1 封装 intent_agent_subgraph

文件：`host_brain/integrations/graph_subgraph.py`（扩展现有）

将 `SCENE_EXEC_INTENT` 的节点链封装为透明子图：
- 子图内部：`core_reasoner_primary → planner_constraints → tool_planner`
- 子图输出：`subgraph_commit_summary`（必须）
- 子图不得写 `committed_*`
- 子图所有节点全 trace

#### 4.2.2 封装 code_refine_subgraph（可选）

将代码场景的多步精炼封装为子图：
- 子图内部：`code_draft → code_review → code_refine`
- 适用于需要多轮精炼的复杂代码任务

#### 4.2.3 更新 GraphState（稳定后）

文件：`host_brain/integrations/graph_contract.py`

等 agent_runtime schema 稳定后，把以下字段提升为 `GraphState` 顶层：
- `active_agent_id`
- `handoff_history`
- `agent_budget`
- `agent_artifacts`
- `judge_result`

**第一阶段先放 `payload["agent_runtime"]`，不急着扩顶层。**

---

### 第三阶段：只读并行 agent

**目标：** 对只读任务开放并行子 agent，提升检索增强和候选比对能力

**时间估算：** 2～3 周

**前提：** 第一、二阶段稳定运行 2 周以上

**具体工作：**

利用现有 `branch_specs`、`run_parallel_branches()`、`barrier_join()` 骨架，开放：
- 多路检索摘要并行
- 多候选回答比较
- 多翻译候选比对

**严格限制：**
- **不开放带副作用的并行 agent**
- 并行 agent 只产出草案，由 JudgeAgent 统一裁判

---

### 第四阶段：预算、灰度开关与监控

**目标：** 完善可观测性、灰度控制和成本管理

**时间估算：** 1 周

**具体工作：**

#### 4.4.1 灰度开关

在 `.env` / 配置中增加：
```
MULTI_AGENT_ENABLED=false
SUPERVISOR_AUTO_ENABLED=false
INTENT_SUBGRAPH_ENABLED=false
READONLY_PARALLEL_ENABLED=false
```

#### 4.4.2 灰度上线顺序

1. `SCENE_WORK_CODE`（最安全，副作用最小）
2. `SCENE_WORK_TRANSLATE`
3. `SCENE_TYPE_AUTO`
4. `SCENE_EXEC_INTENT`
5. `SCENE_CHAT`（最后，且大概率长期保留快路径）

#### 4.4.3 监控指标

| 指标 | 说明 | 告警阈值 |
|------|------|---------|
| 聊天 p95 延迟 | 显式 SCENE_CHAT 不得明显变差 | 较基线 +20% |
| 误分流率 | 普通聊天被打进 EXEC_INTENT 的比例 | < 5% |
| agent hop 数 | 平均 hop 数，控制成本 | AUTO 场景 ≤ 2.5 |
| 模型调用总数 | 每请求平均调用次数 | ≤ 4 次 |
| 子图失败率 | 子图内部节点失败比例 | < 2% |
| handoff 追踪完整率 | 每次 handoff 都有 trace | 100% |

---

## 五、文件改动清单

### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | Supervisor 主 agent handler |
| `host_brain/app/agents/__init__.py` | agent 模块入口 |
| `host_brain/app/agents/agent_registry.py` | agent allowlist 与注册表 |
| `host_brain/app/agents/agent_contract.py` | artifact contract 数据结构定义 |

### 修改文件

| 文件路径 | 改动说明 |
|---------|---------|
| `host_brain/app/graph_config_builder.py` | 新增 supervisor_dispatch 节点；AUTO 场景入口；保留 CHAT 快路径 |
| `host_brain/integrations/graph_runtime.py` | 初始化 agent_runtime；注入默认预算；多 agent 开关 |
| `host_brain/integrations/graph_executor.py` | model output 控制字段剥离；trusted handler allowlist；agent 切换事件 |
| `host_brain/integrations/graph_subgraph.py` | 新增 intent_agent_subgraph 工厂（第二阶段） |
| `host_brain/integrations/graph_contract.py` | 新增 agent_id allowlist 校验；handoff target 合法性校验（第二阶段提升 GraphState 字段） |
| `host_brain/app/chat_handler.py` | 降低误升率；强化 narrative_only 快通道 |
| `host_brain/app/graph/main_chain.py` | 补充多 agent 观测标签；agent latency 统计（外层主链顺序不动） |
| `docs/30_devspec/ModelPlane.md` | 补充 Multi-Agent Governed Extension 章节 |

---

## 六、关键设计原则（必须遵守）

### 6.1 主 agent 不直接拥有路由权，只有"提案权"

```
Supervisor LLM 输出 → 分派提案（JSON）
                              ↓
                    Supervisor Handler 校验
                              ↓
                    写入 agent_runtime.plan
                              ↓
                    写入 __next_node__（handler 专属）
```

**LLM 不直接写 `__next_node__`，更不能写 `committed_route_token`。**

### 6.2 子 agent 只产出"工件"，不直接控制流程

子 agent 输出统一为 artifact contract：
- 结果草案
- 摘要
- 置信度
- 风险提示
- 下一步**建议**（不是命令）

真正下一跳由 handler / judge 编译决定。

### 6.3 不要重载 committed_route_token

`committed_route_token` 是 Governor 级治理令牌，多 agent 内部 handoff **不复用它**。

agent 内部路由状态维护在：
- `payload["agent_runtime"]["dispatch_plan"]`
- `payload["agent_runtime"]["active_agent_id"]`
- `payload["agent_runtime"]["handoff_history"]`

### 6.4 聊天场景的安全分层

| 安全层 | 聊天是否默认开启 | 说明 |
|--------|----------------|------|
| 边界安全（不泄露、不伪执行、不越权） | **是** | 所有场景必须 |
| 基础输出轻校验 | **是** | 所有场景必须 |
| safety_verifier | **否（条件触发）** | 仅高风险聊天时插入 |
| planner_constraints | **否** | 仅执行意图场景 |
| tool_planner | **否** | 仅执行意图场景 |
| approval / confirm / token 审查 | **否** | 仅执行意图场景 |

---

## 七、必须避免的设计

以下设计**严禁**在升级过程中引入：

1. **主 agent 变成纯 LLM node**（主 agent 必须是 handler + LLM proposal）
2. **子 agent 之间自由长对话**（子 agent 只产出工件，不互相对话）
3. **model node 直接输出 `__next_node__`**（只有 trusted handler 可以写）
4. **任何 agent 写 `committed_route_token`**（只有 Host Governor 可以写）
5. **对聊天默认套执行级安全链**（聊天应是轻治理 + 强边界）
6. **带副作用的并行 agent**（第三阶段只开放只读并行）
7. **子 agent 直接 commit**（最终提交只留在 Host）
8. **agent 之间投票后直接输出**（必须经过 Judge + Host 收口）

---

## 八、验收标准

### 8.1 功能验收

| 验收项 | 标准 |
|--------|------|
| 聊天快路径保留 | 显式 SCENE_CHAT 仍走 `chat_direct_ja`，不经过 Supervisor |
| Supervisor 分派正确 | AUTO 场景能正确选派 code/translate/intent agent |
| 子 agent 输出合规 | 所有子 agent 输出符合 artifact contract，不含控制字段 |
| Judge 收口完整 | 所有非聊天场景都经过 JudgeAgent |
| handoff 可追踪 | 每次 handoff 都有完整 trace 记录 |
| 降级稳定 | 某子 agent 失败时，系统走 judge/fallback，不整图崩溃 |

### 8.2 性能验收

| 指标 | 标准 |
|------|------|
| 聊天 p95 延迟 | 较升级前基线 ≤ +20% |
| 代码场景 p95 延迟 | 较升级前基线 ≤ +30%（允许因 Supervisor 增加少量开销） |
| AUTO 场景平均 hop 数 | ≤ 2.5 |
| 每请求平均模型调用数 | ≤ 4 次 |

### 8.3 治理验收

| 验收项 | 标准 |
|--------|------|
| model node 控制字段隔离 | 0 个 case 允许 model node 直接控制关键路由 |
| committed_route_token 保护 | 0 个 agent 可以写 committed_route_token |
| 审计完整性 | 子图都有 subgraph_commit_summary，handoff 都有 trace |
| 误分流率 | 普通聊天被打进 EXEC_INTENT 的比例 < 5% |

---

## 九、风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| Supervisor 误判导致错误子 agent 选派 | 中 | allowlist 校验 + fallback 到默认场景链 |
| 聊天延迟因误路由升高 | 中 | 显式 SCENE_CHAT 保留快路径；pre_intent_classifier 优化 |
| 子 agent 失败导致整图崩溃 | 中 | 每个子 agent 有独立 fallback；JudgeAgent 有降级路径 |
| model node 绕过控制字段剥离 | 高 | 在 graph_executor.py 统一剥离，不依赖各节点自律 |
| 成本超预算（多 agent 增加调用次数） | 中 | 预算控制（max_hops）+ 灰度上线 + 监控告警 |
| 第一阶段 agent_runtime schema 不稳定 | 低 | 先放 payload，不急着扩 GraphState 顶层 |

---

## 十、与现有文档的关系

| 文档 | 关系说明 |
|------|---------|
| `AGENT_ORCHESTRATION_DETAIL_2026-03-09.md` | 本文档是其升级版，现有编排细节仍有效，本文档在其基础上叠加多 agent 语义 |
| `ModelPlane.md` | 本方案所有设计均在其治理约束内，升级后需补充 Multi-Agent Governed Extension 章节 |
| `LangGraph_Governed_Runtime_Solution_2026-03-06.md` | 本方案的理论基础，三层架构（Policy/Execution/Evidence Plane）在本方案中得到落地 |
| `DevSpec.md` | 实施阶段需同步更新 DevSpec 中的模块接口描述 |
| `DevTasks.md` | 实施阶段需按本方案拆解为具体 TASK 条目 |

---

---

## 十一、第五阶段：真正的多 Agent 运行时（M-MA5）

> 版本：v1.1 | 日期：2026-03-16
> 前四阶段完成了"语义多 agent 化基础设施"，本阶段实现各 Agent 的真实执行逻辑、Supervisor LLM 决策、handoff 循环驱动和 JudgeAgent。

### 11.1 当前缺口（M-MA4 完成后的真实状态）

| 缺失项 | 说明 |
|--------|------|
| **Agent 实体不存在** | `chat_agent`、`code_agent` 等只是字符串 ID，没有对应的执行 handler |
| **Supervisor 不调 LLM** | 当前是硬编码规则路由（`scene_type` → `agent_id`），不是真正的 Supervisor 决策 |
| **子图没有接入路由** | `compile_intent_agent_subgraph()` 已定义，但没有节点在 `active_agent_id` 驱动下调用它 |
| **没有 handoff 循环** | `handoff_history` 只写了一条，没有循环驱动机制 |
| **JudgeAgent 未实现** | `judge_result` 字段存在但没有任何节点写入它 |

### 11.2 第五阶段目标

**让系统真正按 `active_agent_id` 动态选择执行路径，而不是固定节点序列。**

具体目标：
1. **各 Agent Handler 实现**：`chat_agent_handler`、`code_agent_handler`、`translate_agent_handler`、`intent_agent_handler`，每个 handler 调用对应的节点/子图
2. **图执行器支持 agent 路由**：`graph_executor` 根据 `active_agent_id` 动态选择下一个执行的节点，而不是固定节点序列
3. **JudgeAgent Handler 实现**：读取 `agent_artifacts`，写入 `judge_result`
4. **Supervisor LLM 提案模式**（`SUPERVISOR_AUTO_ENABLED=true` 时）：调用 LLM 产出分派提案，handler 校验后写入路由

### 11.3 文件改动清单

#### 新增文件

| 文件路径 | 说明 |
|---------|------|
| `host_brain/app/handlers/chat_agent_handler.py` | ChatAgent handler，调用 `chat_direct_ja` 节点 |
| `host_brain/app/handlers/code_agent_handler.py` | CodeAgent handler，调用 `workbench_code_assist` 节点 |
| `host_brain/app/handlers/translate_agent_handler.py` | TranslateAgent handler，调用 `workbench_translate` 节点 |
| `host_brain/app/handlers/intent_agent_handler.py` | IntentAgent handler，调用 `intent_agent_subgraph` |
| `host_brain/app/handlers/judge_agent_handler.py` | JudgeAgent handler，读取 `agent_artifacts`，写入 `judge_result` |

#### 修改文件

| 文件路径 | 改动说明 |
|---------|---------|
| `host_brain/app/handlers/supervisor_dispatch_handler.py` | 新增 LLM 提案模式（`SUPERVISOR_AUTO_ENABLED=true` 时调用 LLM） |
| `host_brain/integrations/graph_executor.py` | 新增 agent 路由分发：根据 `active_agent_id` 选择对应 handler 执行 |
| `host_brain/app/graph_config_builder.py` | 注册各 agent handler 节点绑定 |
| `host_brain/app/dependencies.py` | 注册各 agent handler 到 `GraphRuntimeContract` |

### 11.4 Agent Handler 设计规范

每个 Agent Handler 必须遵守以下规范：

```python
def xxx_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    1. 从 payload 读取用户输入和上下文
    2. 调用对应的节点/子图执行
    3. 将输出写入 agent_artifacts（通过 write_agent_artifact_to_state）
    4. 不写 __next_node__（由 graph_executor 的 agent 路由分发决定）
    5. 不写 committed_route_token
    """
```

**关键约束**：
- Agent handler 只产出工件，不直接控图
- `__next_node__` 由 `graph_executor` 根据 `need_judge` 决定（有 judge → `judge_agent`，无 judge → `renderer`）
- Agent handler 失败时写入 `agent_artifacts[agent_id]["error"]`，不抛异常

### 11.5 JudgeAgent 设计规范

```python
def judge_agent_handler(
    payload: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    1. 读取 state["agent_artifacts"]（所有子 agent 的输出工件）
    2. 评估工件质量（置信度、风险标记）
    3. 选择最优工件或合并多个工件
    4. 写入 state["judge_result"]
    5. 不写 __next_node__（由 graph_executor 决定下一跳为 renderer）
    """
```

### 11.6 graph_executor agent 路由分发设计

在 `graph_executor.py` 中新增 `_dispatch_to_agent()` 方法：

```python
def _dispatch_to_agent(self, state: GraphState) -> str:
    """根据 active_agent_id 返回对应的节点 ID。

    这是 M-MA5 的核心：图执行器根据 GraphState.active_agent_id
    动态选择下一个执行的节点，而不是固定节点序列。
    """
    active_agent_id = state.get("active_agent_id") or ""
    agent_to_node = {
        "chat_agent": "chat_agent_node",
        "code_agent": "code_agent_node",
        "translate_agent": "translate_agent_node",
        "intent_agent": "intent_agent_node",
        "judge_agent": "judge_agent_node",
    }
    return agent_to_node.get(active_agent_id, CHAT_DIRECT_NODE_ID)
```

### 11.7 验收标准

| 验收项 | 标准 |
|--------|------|
| `active_agent_id` 驱动路由 | `graph_executor` 根据 `active_agent_id` 选择对应 handler，不走固定节点序列 |
| 各 Agent Handler 可调用 | 5 个 agent handler 均可独立 import 和调用 |
| `agent_artifacts` 有内容 | 每次 agent 执行后 `agent_artifacts[agent_id]` 有工件记录 |
| `judge_result` 有内容 | `need_judge=true` 的场景执行后 `judge_result` 不为 None |
| 验证脚本通过 | `_tmp_verify_ma5.py` 全部检查点通过 |

---

## 变更记录

- 2026-03-16 v1.0：初版，基于当前仓库代码审计与多轮架构讨论产出。
- 2026-03-16 v1.1：补充第五阶段（M-MA5）设计方案。M-MA1~M-MA4 已完成，M-MA5 待实施。
