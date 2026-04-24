# Natsume_Ai 工具调用链路审查报告

> SUPERSEDED, 2026-04-24: historical chain analysis only. Codex execution has hard-cut from legacy `codex_codegen` to Codex Runtime v2 WorkOrders and the new `codex_*` skill IDs.

**日期**：2026-04-21  
**状态**：审查完成  
**作者**：七海（代哥哥撰写）  
**目标分支**：codex  
**适用范围**：host_brain 工具调用链路

---

## 0. 读者引导

本文不描述"设计文档声称的样子"，只描述"代码里实际在发生的事"。关键文件及函数名均带绝对路径，下游实现节点可按文件直接定位。

---

## 1. 工具世界有几种"工具"

先纠正一个极易误解的点：**Natsume 里同时存在 5 套并列的"工具抽象"**，下游如果不分清，会混淆。

| 抽象名 | 所在目录 | 代表文件 | 定位 |
|---|---|---|---|
| **Skill** | `host_brain/skills/` | `registry.py` / `declarative.py` | 主生产调度单位，有 `SkillMeta` + `handler + executor`，由 Governor 治理 |
| **Tool (ToolMeta)** | `host_brain/tools/` | `catalog.py` / `broker.py` | 给"模型可见性选择"用的元数据，有 `side_effect_level + auth_level` |
| **MCP Tool** | `host_brain/mcp/` + `host_brain/tools/mcp_adapter.py` | `mcp_adapter.py` | 外部 MCP provider 的工具，桥接到 Skill 或 Tool |
| **ReAct Tool** | `host_brain/app/agents/` | `tool_registry.py` / `react_loop.py` | 供 sub-agent（intent_agent 等）用的 ReAct 工具集，含 `list_skills / run_skill / dispatch_sub_agent` 等 |
| **Sub-Agent** | `host_brain/app/sub_agent_protocol.py` + `host_brain/app/handlers/*.py` | `sub_agent_protocol.py` | `translate_agent / intent_agent / vision_agent / repair_agent` 四个托管 agent，作为"可被 delegate 的目标" |

**关键矛盾**：同一个"能力"在不同抽象里有不同名字和不同元数据。例如：
- `codex_codegen` 是 Skill（`host_brain/skills/ide_registry.py` 注册）
- 又被 `app/agents/tool_registry.py` 的 `_tool_run_skill` 作为 ReAct 工具入口封装过一次
- `MCPAdapter` 如果拉到同名 MCP tool，会再以 `mcp::<provider>::<tool>` 作为第三个名字注册进 SkillRegistry（`mcp_skill_bridge.py`）

**这是当前工具层最大的耦合问题**（详见 §10）。

---

## 2. 工具注册与发现

### 2.1 两个核心单例

- **`SkillRegistry`**（`host_brain/skills/registry.py`）：全局 Skill SSOT，字典 `{skill_name: SkillMeta}`
- **`ToolCatalog`**（`host_brain/tools/catalog.py`）：全局 ToolMeta SSOT，字典 `{tool_id: ToolMeta}`

两者**数据模型不同**：

```python
# SkillMeta（registry.py:50-68）
SkillMeta(
    skill_name: str,
    clearance: "LOW" | "MEDIUM" | "HIGH",
    risk_level: int,               # 1~5
    scope: "host" | "memory" | "system",
    reversible: bool,
    handler: SkillHandler,
    require_confirm: bool,
    family: str,                   # "browser" / "system" / "emotion" / ...
    capability_tags: tuple[str,...],
    parallelizable: bool,
    fallback_policy: str,
    scheduler_allowed: bool,
    access_level: AccessLevel,
)

# ToolMeta（catalog.py:29-40）
ToolMeta(
    id: str,
    name: str,
    desc: str,
    schema_hash: str,              # 必须 "sha256:" 开头
    side_effect_level: "READ" | "WRITE",
    auth_level: "NONE" | "APPROVAL" | "CONFIRM",
    domain_tags: tuple[str,...],
    version: str,
)
```

**结论**：Skill 是"生产调度视角"，Tool 是"模型可见性视角"。二者字段几乎不重叠，**没有自动同步机制**。

### 2.2 Skill 注册：声明式 + 装饰器双轨

**声明式路径**（`host_brain/skills/declarative.py`）：
- 13 个 `*_registry.py` 每个定义一个 `DeclarativeSkillProvider`
- Provider 类属性 `skill_specs: tuple[DeclarativeSkillSpec, ...]` 声明每个 skill 的元数据和归一化函数
- `build_handler()` 自动合成 `(args, ctx) -> {status, tool, action, **normalized_args}` 的 payload handler

**装饰器路径**（`registry.py:130-170`）：
- 用 `@skill(skill_name=..., clearance=...)` 装饰函数，属性上挂 `__skill_meta_config__`
- `SkillRegistry.register_decorated(provider)` 扫描 provider 的所有属性按装饰器配置注册

**发现与汇总**：`host_brain/skills/provider_catalog.py`
- `BUILTIN_SKILL_PROVIDER_SPECS` 列出 13 个内置 provider + 对应 `HOST_SKILL_*_ENABLED` 环境变量
- `register_configured_skill_providers(registry, ...)` 启动时按 env flag 注册
- 还额外扫描 `host_brain/skills/custom_providers/*_provider.py` 自定义 provider
- 支持 `HOST_SKILL_EXTRA_PROVIDERS` 环境变量追加 `module:Class` 列表

### 2.3 ToolCatalog 注册：只有 MCP 路径主动写

`ToolCatalog.register()` 目前**只有 MCPAdapter 调用**。也就是说：
- **SkillRegistry 里的 Skill 不会自动出现在 ToolCatalog 里**
- `ToolCatalog` 默认是空的，只有当外部 MCP provider sync 时才会填
- `host_brain/tools/selector.py::select_tool_meta_pack` 默认 `include_write=False` + `k=6`，所以即使有内容也只暴露 Top-K READ 工具

**潜在问题**：`selector.py` 在当前主链路里几乎没有调用方（搜索整个 host_brain/app/ 无直接调用）。这说明 ToolCatalog + Selector 这条线本是设计给"给 LLM 的工具列表"用的，但实际主链路用的是 **SkillRegistry.build_skill_manifest()**（见 §5）和 **sub-agent ReAct 工具集**（见 §6）。

### 2.4 MCP 工具双桥接

```
外部 MCP provider
    ↓ sync
MCPAdapter.sync_provider_tools()
    ↓ 同时写两处
    ├─→ ToolCatalog.register()     (给 selector / LLM 可见性用)
    └─→ mcp_skill_bridge.register_mcp_tools_into_skill_registry()
            ↓
        SkillRegistry（skill_name="mcp::<provider>::<tool>"）
```

**奇怪的地方**：同一个 MCP 工具会被注册成两套不同元数据（`ToolMeta` 和 `SkillMeta`），两者 risk/auth 映射规则不完全一致（`ToolMeta` 走 `READ/WRITE + NONE/APPROVAL/CONFIRM`，`SkillMeta` 走 `LOW/MEDIUM/HIGH + risk_level 1~5`）。下游如果要统计"一个工具危险不危险"，拿到哪一份都不完整。

### 2.5 热加载与版本

`host_brain/skills/hot_loader.py` 提供 `SkillHotLoader`：
- `reload_skill(new_meta, version)`：运行时替换 Skill，旧版本存入 `SkillVersionRegistry`
- `rollback_skill(skill_name, target_version)`：回滚到指定版本
- 有独立的 audit log 数组

这条路径当前**主链路不依赖**，是给内部运维端点用的。

---

## 3. 工具路由与决策（route_type 怎么出来）

### 3.1 五层决策漏斗

一次聊天请求决定"到底要不要调用工具"要经过五层，顺序不能颠倒：

```
Layer 0  Thalamus: classify_and_compile_intent_draft
            → intent_draft.route（可能为空）
Layer 1  Organ Gating (executive_runtime_core._executive_runtime_core_impl)
            → Immune / Proprioception / Limbic / Prefrontal 四路并行读状态
            → 任一触发强制 chat 覆盖
Layer 2  Prefrontal.plan_response（需 PREFRONTAL_PLAN_ENABLED）
            → action=skill/delegate/speak/wait，可再次覆盖路由
Layer 3  _hard_rule_route
            → intent_draft.route 提示 → 硬规则关键词 → RULE_ROUTE_MAP 精确匹配
            → 产出 _RouteSelection(route_type, target_id, reason_code)
Layer 4  _should_force_complex_plan + capability_gap_detector
            → 检测"用户要执行但 route=chat"的缺口
            → 可升格为 plan 或 delegate 到 intent_agent
```

### 3.2 主要决策点文件位置

| 决策点 | 文件 | 函数 |
|---|---|---|
| Thalamus 意图分类 | `host_brain/tribe/thalamus_agent.py` | `classify_and_compile_intent_draft` |
| 器官族并行读 | `host_brain/app/executive_runtime_core.py:1560-1655` | `_fetch_immune_state / _fetch_proprioception_state / _fetch_limbic_motive / _fetch_prefrontal_state` |
| Prefrontal 规划 | `host_brain/tribe/prefrontal_agent.py:311` | `plan_response` |
| 硬规则路由 | `host_brain/app/executive_runtime_core.py:315-382` | `_hard_rule_route` |
| RULE_ROUTE_MAP | `host_brain/agents/planning_agent.py:142-201` | 精确关键词映射表 |
| capability gap 检测 | `host_brain/app/capability_gap_detector.py:104-169` | `detect_capability_gap` |
| 强制多步规划 | `host_brain/app/executive_runtime_core.py:385-433` | `_should_force_complex_plan` |
| Skill 路由决策构建 | `host_brain/app/skill_family_router.py:123-218` | `build_skill_route_decision` |
| Skill DAG 规划 | `host_brain/app/skill_dag_planner.py:267-388` | `build_skill_dag_plan` |

### 3.3 route_type 的六种终态

`_RouteSelection.route_type` 最终只能是以下六种：

| route_type | 去哪执行 | 说明 |
|---|---|---|
| `chat` | `_stream_chat_with_context` → `chat_agent_sse_generator` | 闲聊，不走工具 |
| `skill` | `_execute_skill` → `skill_meta.handler` → `dispatch_executor_payload` | 单个 Skill |
| `delegate` | `_dispatch_to_sub_agent` → `sub_agent_protocol.dispatch` | 委托子 Agent |
| `plan` | `_execute_skill_dag` → `plan_graph.run_plan_graph` | LangGraph 父子图多步执行 |
| `recent_turn` | `try_handle_recent_turn_direct_route` | 回答"你刚才说什么"，不走工具 |
| `task_progress` | `try_handle_task_progress_direct_route` | 回答"进度怎样"，只读任务仓 |

### 3.4 capability_forge 的角色

当 route=chat 但检测到用户想执行动作（`_looks_like_action_intent`）时，会进入 `_maybe_synthesize_runtime_capability`：
1. `capability_gap_detector.detect_capability_gap` 判定是否存在能力缺口
2. `synthesize_runtime_capability` 合成一个临时 `RuntimeCapabilitySpec`（30min TTL）
3. `CapabilityForgeService.get_runtime_route_override` 查是否有现成覆盖项
4. 覆盖 `route` 成 `delegate`/`skill`，再走 §3.3 的分支

这是一条**自愈路径**：意图未命中已有 Skill 时会动态合成"虚拟能力"并桥到子 agent，避免空壳回复。

---

## 4. 工具执行链路

### 4.1 三条互不相同的执行路径

代码里 Skill/Tool 真正"被运行"的入口有**三条**，分别对应不同安全等级：

#### 路径 A：Executive Runtime 直接 Skill 执行（主路径）
```
_executive_runtime_core_impl
    → _execute_skill (executive_runtime_core.py:675)
        → SkillRegistry.require(skill_id)
        → skill_meta.handler(args, exec_context)   # 归一化
        → dispatch_executor_payload(payload)       # 路由到 impl executor
        → _log_skill_exec_row                      # 审计
    → _ExecutionOutcome
```
- 直接读 `SkillRegistry`，不经过 `ToolBroker`
- 审计走 `ledger.log_skill_exec`
- 用于主对话路由为 `skill` 时

#### 路径 B：SkillLoader Token-Gated 执行（治理最严）
```
SkillLoader.execute (skills/loader.py:44)
    → SkillRegistry.require
    → governor decision 检查
    → TokenService.validate_execution_tokens
        approval_token + (可选) confirm_token
    → 幂等性检查（ledger.has_skill_exec_for_intent）
    → skill_meta.handler → _dispatch_executor
    → 如 reversible，issue_undo_token
    → ledger.log_skill_exec（分层审计 LIGHT/STANDARD/FULL_AUDIT）
```
- 主链路目前**没有调用这条路径**
- 它是给"外部明确带 approval token 提交意图"的场景设计的
- 保留了完整的治理契约（幂等 + 撤销）

#### 路径 C：ToolBroker 单入口（给 MCP + ReAct 工具调用用）
```
ToolBroker.execute (tools/broker.py:60)
    → ToolCatalog.get(tool_id)
    → WRITE 工具验证 approval/confirm token
    → 注入 emotion_tag/mood_state 默认值
    → handler(safe_args, exec_context)
    → ledger.log_skill_exec（字段 skill_name 实为 tool_id）
```
- 被 `MCPAdapter.execute_mcp_tool` 包装（带 Airlock 出站净化）
- 被 `GovernedToolNode`（`integrations/graph_governed_tool_node.py`）在 ReAct 循环里调用
- **不走 SkillRegistry，走 ToolCatalog**

### 4.2 Executor Registry 分发层

`host_brain/skills/executor_registry.py:83-109` 定义了从归一化 payload 到真实 impl 的分发表：

```python
ToolExecutorSpec("file",          execute_file_skill),
ToolExecutorSpec("notification",  execute_notification_skill),
ToolExecutorSpec("shell",         execute_shell_skill),
ToolExecutorSpec("browser",       execute_browser_skill),
ToolExecutorSpec("cursor",        execute_ide_skill),
ToolExecutorSpec("pycharm",       execute_ide_skill),
ToolExecutorSpec("codex",         execute_ide_skill),
ToolExecutorSpec("open_file_in_editor", execute_ide_skill),
ToolExecutorSpec("diary_writer", _with_context(execute_diary_writer)),
ToolExecutorSpec("dream_generator", _with_context(execute_dream_generator)),
ToolExecutorSpec("desktop_note",  _with_context(execute_desktop_note)),
ToolExecutorSpec("focus_mode",    _focus_mode_with_mode_changer),
ToolExecutorSpec("proactive_talk",_with_context(execute_proactive_talk)),
ToolExecutorSpec("desktop",       _with_context(execute_desktop_skill)),
ToolExecutorSpec("memory",        _with_context(execute_memory_skill)),
ToolExecutorSpec("emotion",       _with_context(execute_emotion_skill)),
ToolExecutorSpec("scheduler",     _with_context(execute_scheduler_skill)),
ToolExecutorSpec("system",        _with_context(execute_system_skill)),
ToolExecutorSpec("web",           _with_context(execute_web_skill)),
ToolExecutorSpec("hub_skill",     _with_context(execute_hub_skill)),
```

`dispatch_executor_payload(payload)` 按 `payload["tool"]` 分发到对应 impl。

**关键位**：Skill handler 返回的是**归一化 payload**（`{status, tool, action, args}`），Executor impl 才真正执行副作用。这样可以做到"声明式 Spec 定接口，命令式 executor 定实现"，但也意味着 handler 和 executor **同时存在**，改一个要改另一个。

### 4.3 impl/ 下的 executor 全貌

`host_brain/skills/impl/` 下共 24 个文件，分三类：

| 类别 | 代表文件 | 说明 |
|---|---|---|
| **Declarative Skill 的 impl** | `browser_executor.py` / `shell_executor.py` / `file_executor.py` / `ide_executor.py` / `system_executor.py` / `web_executor.py` / `notification_executor.py` / `desktop_executor.py` / `memory_executor.py` / `emotion_executor.py` / `scheduler_executor.py` / `hub_skill_executor.py` | 接收归一化 payload，执行真实副作用 |
| **个性化行为 impl** | `diary_writer.py` / `dream_generator.py` / `desktop_note.py` / `focus_mode.py` / `proactive_talk.py` / `emergent_growth_skill.py` / `inner_world_skill.py` / `body_sense_skill.py` / `silence_skill.py` | Natsume 的"个性化行为"，和 Skill 是同一个抽象但语义更侧重情感表达 |
| **特殊 impl** | `gaming_companion_executor.py` / `clawd_ui_action.py` | 场景特化 |

---

## 5. Sub-Agent（delegate 路径）

### 5.1 四个已注册 Sub-Agent

`host_brain/app/sub_agent_protocol.py:54-69`：

| agent_id | handler 文件 | 能力描述 |
|---|---|---|
| `translate_agent` | `app/handlers/translate_agent_handler.py` | 翻译（中日英多语 + 校对 + 润色） |
| `intent_agent` | `app/handlers/intent_agent_handler.py` | 执行专家，调工具完成操作 |
| `vision_agent` | *（未独立实现，当前复用 intent_agent）* | 视觉分析，见 §5.3 |
| `repair_agent` | `app/handlers/repair_agent_handler.py` | 环境修复（缺浏览器/依赖时自动安装） |

### 5.2 intent_agent 是"真正的 ReAct Agent"

`app/handlers/intent_agent_handler.py:38` 是整个系统里**唯一走 ReAct 循环的 agent**：

```
intent_agent_handler
    → get_tools_for_agent(AGENT_ID_INTENT, allow_side_effects=True)
    → run_react_loop (app/agents/react_loop.py:307)
        → 构建 GovernedToolNode（LangGraph ToolNode + Governor 门控）
        → 循环 max_steps=5 次：
            1. gateway.complete(messages) → LLM 决策
            2. 解析 JSON 输出：action_type = final_answer / tool_call
            3. 若 tool_call：
                - GovernedToolNode 执行（Governor APPROVE 才真调）
                - 写 Evidence Plane 审计
                - 结果回塞 messages
            4. 若 final_answer：break
    → 写 agent_artifacts + intent_agent_trace
```

可调用的 ReAct 工具来自 `app/agents/tool_registry.py`，含这些关键入口：
- `list_skills`：列出 SkillRegistry 里的可用 skill
- `run_skill`：**经 governed 路径执行 skill**（通过 `_execute_skill`）
- `dispatch_sub_agent`：**agent 套 agent**，调用另一个 sub-agent
- `search_knowledge / search_by_keyword / search_by_vector`：语义检索
- `get_context / analyze_code / evaluate_artifact / compare_artifacts`：元工具
- `emotion_history_search / emotion_timeline_query`：情感相关只读

**关键事实**：intent_agent 通过 `run_skill` 可以**间接触发** Skill 执行，路径是：

```
chat request → route=delegate → intent_agent
    → ReAct loop → tool_call: run_skill
        → _tool_run_skill → _execute_skill (同 §4.1 路径 A)
```

也就是 §4.1 的路径 A 既能从 executive_runtime_core 直接进入，也能从 intent_agent ReAct 间接进入。

### 5.3 vision_agent 是"软降级"

`sub_agent_protocol.py:147-152` 有一个 TODO(P3-7)：

```python
# vision_agent 暂未实现独立 handler，当前临时复用 intent_agent
if AGENT_VISION not in self._handler_map and AGENT_INTENT in self._handler_map:
    self._handler_map[AGENT_VISION] = self._handler_map[AGENT_INTENT]
```

也就是说路由到 `vision_agent` 时实际跑的是 `intent_agent_handler`，多模态 LLM 尚未接。

### 5.4 repair_agent 的 success 语义特殊

`sub_agent_protocol.py:285-289`：

```python
if req.agent_id == AGENT_REPAIR:
    repair_trace = result_payload.get("repair_agent_trace") or {}
    repair_success = bool(repair_trace.get("can_retry", ...))
else:
    repair_success = bool(final_answer)
```

修复失败时也会有 summary 文本，所以不能用"有 final_answer"判定，必须读 `repair_agent_trace.can_retry`。

### 5.5 Sub-Agent 治理

`app/sub_agent_governor.py:78-121` 定义了 `sub_agent_pre_exec_check`：
- sys_mode 为 locked/maintenance → 全拒
- sys_mode 为 sleep + intent_agent → 拒（执行类拒绝）
- sys_mode 为 gaming + vision_agent → 拒（白皮书禁止视觉自动化）

同时 `chat_lite_governor`（同文件）给 chat 通路提供极轻量 Governor（仅 sys_mode + 速率限制）。

---

## 6. LangGraph Plan Graph 的并行工具执行

### 6.1 两套并行执行路径

**路径 I：`plan_graph.run_plan_graph`（LangGraph Send API 并行）**
- `plan_decompose_node`：`PlanningAgent.create_plan` 拆解成 sub_tasks
- 条件边 `_route_to_tasks`：每个 sub_task 一个 `Send("task_exec_subgraph", task_state)` → 真正并行
- `task_exec_subgraph.task_route_node`：按 `route_type` 分发
  - `skill` → `dispatch_executor_payload`（直接调 executor，**不走 `_execute_skill`**，也**不过 Governor**）
  - `delegate` → `SubAgentDispatcher.dispatch`
- `synthesize_node`：LLM 语义整合 sub_results（独立 LLM 调用，**不走人格管线**）

**路径 II：`_execute_skill_dag` 手动 batch gather（历史 fallback）**
- `build_skill_dag_plan` 做拓扑排序分批
- 对每 batch `asyncio.gather` 调 `_execute_plan_node`
- `_execute_plan_node` 对 `skill` 走 `_execute_skill`（经过 Governor），对 `delegate` 走 `_dispatch_to_sub_agent`
- 当前已被 P1-1 改为"plan_graph 失败时不降级"，此路径实际已废弃

**问题**：路径 I 的 skill 分支用 `dispatch_executor_payload` **跳过了** `_execute_skill` 里的 Governor 决策 + approval token 校验 + 审计写入。多步任务里 skill 执行**安全等级降了一档**。

### 6.2 plan_graph synthesize 独立 prompt

`plan_graph._synthesize_node` 自建 LLM 调用（`stream_llm_chunks` + `plan_graph.synthesize.prompt_templates`），不经过 `cortex_prompt_builder`，因此：
- 不注入人设（character_profile）
- 不注入风格（style_profile_v5）
- 不注入 bond/emotion/memory

多步任务最终回复会和 Natsume 的单轮回复人格不一致。

---

## 7. GovernedToolNode：ReAct 循环里的工具节点

`host_brain/integrations/graph_governed_tool_node.py:80-142` 是 ReAct 循环调用工具时的托管节点：

```
GovernedToolNode.__call__(state)
    1. Governor 门控（不可绕过）：
        governor.evaluate_tool_call(tool_calls, tribe_params)
        若非 GOV_APPROVE → 直接返回，不执行
    2. 直接执行工具（不经 ToolBroker）：
        for tc in tool_calls:
            tool = self._tool_map[tc["tool"]]
            tool.func(**tc["args"])   # LangChain StructuredTool.func
    3. Evidence Plane 审计（不可绕过）：
        evidence_plane.write_tool_execution(...)
```

**关键事实**：GovernedToolNode 执行工具时走的是 `tool.func(...)`，绕过了 `ToolBroker.execute`。这意味着 **WRITE 工具在 ReAct 循环里的 approval token 校验是 Governor 做，不是 ToolBroker 做**。两套机制并存。

`react_loop._tool_specs_to_lc_tools` 把 `ToolSpec` 列表转成 LangChain `StructuredTool` 传给 GovernedToolNode，fallback 走 `execute_tool(tool_id, args)` 直接同步调。

---

## 8. 完整调用链路（典型场景）

### 8.1 场景 A：用户说"打开浏览器查一下今天的天气"

```
POST /v1/chat
→ chat_handler.handle_chat
→ _unified_chat_sse_generator
    ├─ thalamus.classify_and_compile_intent_draft
    │   → intent_draft{route: 可能未定}
    ├─ Governor.evaluate_intent → APPROVE
    └─ cortex_orchestrator_sse_generator
        → _cortex_orchestrator_sse_generator_impl
            ├─ Airlock sanitize / PII scrub
            ├─ 4 条 fastpath skip
            ├─ persona prefetch (bond + motive)
            ├─ retrieval_task (RAG 并发启动)
            └─ executive_router_sse_generator
                → executive_runtime_core._executive_runtime_core_impl
                    ├─ [Layer 1] 器官族并行读（4 路 ThreadPoolExecutor）
                    │   若 immune.risk_level=high → 强制 chat
                    ├─ [Layer 2] prefrontal.plan_response（若启用）
                    ├─ [Layer 3] _hard_rule_route
                    │   RULE_ROUTE_MAP 无命中 "浏览器"
                    │   _looks_multi_step_request → False
                    │   → route=chat
                    ├─ [Layer 4] _should_force_complex_plan
                    │   capability_gap_detector: "打开" 匹配 action_intent
                    │   tags=("browser",) suggested_family=browser
                    │   → 触发 _maybe_synthesize_runtime_capability
                    │   → RuntimeCapability: executor_route=delegate, target_id=intent_agent
                    │   → route 被 override 为 delegate
                    ├─ record_executive_handoff
                    └─ _dispatch_to_sub_agent(target_id=intent_agent)
                        → sub_agent_protocol.dispatch
                            → intent_agent_handler
                                → run_react_loop
                                    ├─ Step 1: LLM 决策 → action=tool_call, tool=list_skills
                                    │   → GovernedToolNode → _tool_list_skills → 返回 browser_* 列表
                                    ├─ Step 2: LLM 决策 → action=tool_call, tool=run_skill{skill_name=browser_navigate, args={url}}
                                    │   → GovernedToolNode → _tool_run_skill
                                    │       → _execute_skill
                                    │           → SkillRegistry.require("browser_navigate")
                                    │           → handler(args, ctx) → payload={tool:"browser", action:"navigate", ...}
                                    │           → dispatch_executor_payload → execute_browser_skill
                                    │               → agent-browser CLI / Playwright
                                    │   → result=网页正文
                                    ├─ Step 3: LLM 决策 → final_answer="今天天气..."
                                    ├─ 写 agent_artifacts + intent_agent_trace
                                    └─ _log_dispatch → ledger.log_skill_exec
                        → SubAgentResponse{success=true, result="..."}
                    ├─ record_runtime_capability_execution（审计 gap → agent 执行闭环）
                    └─ _stream_chat_with_context (sub_agent_context 注入 chat_agent)
                        → chat_agent_sse_generator
                            → cortex_prompt_builder.build_cortex_prompt
                                （sub_agent.result 通过 extra_context_prompt 进 prompt）
                            → LLM 流式 → 反思链 → Airlock → COMMIT
                        → _async_post_process
```

### 8.2 场景 B：用户说"先帮我写段 Python 再打开浏览器"

Layer 3 的 `_looks_multi_step_request` 会匹配"先...再..."触发 `route=plan`：

```
...
→ route=plan
→ _maybe_build_multi_step_plan（读 intent_draft.planned_steps）
→ 若 planned_steps 缺失 → 降级 delegate intent_agent
→ 若存在 → _execute_skill_dag
    → run_plan_graph (LangGraph Send API 并行)
        ├─ plan_decompose_node（PlanningAgent.create_plan）
        ├─ Send × N 个 task_exec_subgraph
        │   └─ task_route_node：
        │       ├─ route=skill → dispatch_executor_payload（绕过 Governor）
        │       └─ route=delegate → SubAgentDispatcher.dispatch
        └─ synthesize_node（独立 LLM 整合，不走人格）
→ _build_plan_chat_outcome
→ _stream_chat_with_context (sub_agent_context=plan_execution, 注入 chat_agent)
→ 主 LLM 重渲染回复
```

---

## 9. 审计与治理全景

### 9.1 审计落点

| 工具调用路径 | 审计表 | 记录字段 |
|---|---|---|
| `_execute_skill` → `_log_skill_exec_row` | `skill_exec_log` | request_id/intent_id/skill_name/result_status/result_code/evidence_ref |
| `SkillLoader.execute` → `_log_and_return` | `skill_exec_log`（分层 LIGHT/STANDARD/FULL_AUDIT） | 同上 + approval_token/confirm_token/manifest_ref/undo_token |
| `ToolBroker.execute` → `_log_and_return` | `skill_exec_log`（skill_name 实为 tool_id） | 同上 |
| `SubAgentDispatcher._log_dispatch` | `skill_exec_log`（skill_name=`sub_agent:<agent_id>`） | 简化字段 |
| `GovernedToolNode` → `evidence_plane.write_tool_execution` | Evidence Plane（不同存储） | tool_calls + tool_results |

**四张入口都往 `skill_exec_log` 写，用 `skill_name` 区分来源**。观测时要按 `skill_name` 前缀筛选，否则混在一起。

### 9.2 Governor 入口

| 场景 | 调用点 | 特点 |
|---|---|---|
| chat 请求鉴权 | `chat_handler._evaluate_governor_with_timing` | `IntentDecisionEngine.evaluate_intent` |
| 闲聊 Lite | `agent_runtime_shared.chat_lite_governor` | 仅 sys_mode + 速率限制 |
| Sub-Agent 执行前 | `sub_agent_governor.sub_agent_pre_exec_check` | sys_mode 分级 |
| Tool 调用前 | `GovernedToolNode.__call__` Layer 1 | `governor.evaluate_tool_call` |
| WRITE 工具 token | `ToolBroker.execute` + `SkillLoader.execute` | `TokenService.validate_execution_tokens` |

**Governor 在链路上至少出现 3 次**（chat 鉴权 + 工具调用 + WRITE token），各自独立决策，彼此不共享结果缓存。

---

## 10. 问题清单（按严重度）

### P0 — 必修

**1. 同一能力三处注册，数据模型不一致**  
- `codex_codegen` 在 SkillRegistry、ToolCatalog（经 MCP 桥）、ReAct ToolSpec 里各有一份元数据
- `risk_level` (1~5) / `auth_level` (NONE/APPROVAL/CONFIRM) / `side_effect_level` (READ/WRITE) 三套风险分级并存
- 改一个不同步，观测和审计会对不上

**2. Plan Graph 的 skill 执行跳过 Governor**  
`plan_graph._task_route_node` 对 `route=skill` 直接调 `dispatch_executor_payload`，**不经过** `_execute_skill` 的 Governor decision 和 approval token 校验。  
这意味着：一个单步必须走 Governor 才能放行的 skill，在多步规划里就可能被白调用。  
修复方向：`_task_route_node` 的 skill 分支应走 `_execute_skill` 或至少补齐 Governor.evaluate_tool_call。

**3. `plan_graph._synthesize_node` 另起独立 LLM prompt**  
和 "对话历史重构方案"发现的问题同源：plan 最终回复不走 `cortex_prompt_builder`，因此没有人设、没有风格、没有 bond/emotion/memory。多步任务结束后她说的话和单轮不是同一个人。

**4. SkillRegistry 和 ToolCatalog 没自动同步**  
Skill 注册后不会自动出现在 ToolCatalog 里，导致 `selector.py` 按 `side_effect_level` 做模型可见性选择时，看到的只有 MCP 工具，看不到所有 Skill。要么废掉 selector 这条线，要么补 Skill → ToolMeta 自动桥接。

### P1 — 应修

**5. vision_agent 软降级到 intent_agent**  
`sub_agent_protocol.py` 有 TODO(P3-7)：vision_agent 没有独立 handler，当前直接复用 intent_agent。图片分析请求实际是在调 intent_agent 的 ReAct，没接多模态 LLM。

**6. MCPAdapter 和 Skill 桥接器各维护一份 MCP 视图**  
`MCPAdapter._provider_tools` 存 `ToolMeta` 列表，`mcp_skill_bridge` 又把它转成 `SkillMeta` 注册。两份缓存独立，MCP provider 同步失败时状态会不一致。

**7. GovernedToolNode 绕过 ToolBroker**  
ReAct 里工具执行走 `tool.func(...)` 直接调，不经过 `ToolBroker.execute`。这意味着：
- WRITE 工具的 token 校验依赖 Governor.evaluate_tool_call 的实现质量
- 审计走 Evidence Plane，不写 skill_exec_log
- 和 §4.1 路径 A 的审计口径不一致

**8. executor_registry 是 24 个硬编码列表**  
新增一个 skill family 要同时改：
- 一个 `*_registry.py`（声明 SkillSpec）
- 一个 `impl/*_executor.py`（实现副作用）
- `executor_registry.py` 里的 `build_builtin_tool_executor_registry`（加一条 ToolExecutorSpec）
- `provider_catalog.BUILTIN_SKILL_PROVIDER_SPECS`（加 env flag）
四处改漏任一处就断链。

**9. capability_forge 和 RULE_ROUTE_MAP 语义重叠**  
`RULE_ROUTE_MAP` 是预写的关键词 → route 映射；`capability_gap_detector` 是运行时从 user_message 推断 capability tag。两者都能产出"route 覆盖"，但优先级不明（前者在 Layer 3、后者在 Layer 4），复杂场景下两条规则可能冲突。

**10. PermissionGuard 和 Sub-Agent Governor 重复**  
`registry.PermissionGuard.check()` 做 USER/SCHEDULER 分层权限校验；`sub_agent_governor.sub_agent_pre_exec_check` 做 sys_mode 分层校验。两者都在"拒绝 agent 执行"语义上重叠，但没有统一入口。

### P2 — 潜在隐患

**11. ReAct 循环 `max_steps=5` 硬上限**  
环境变量 `INTENT_REACT_MAP_STEPS` 默认 5。复杂的多工具组合（例如"搜一下再总结再发邮件"）容易被截断。

**12. `_WRITE_TOOL_PREFIXES` 是字符串前缀判断**  
`react_loop._is_write_tool` 用 `startswith("write_" / "update_" / "delete_" / "create_" / ...)` 判定副作用，没接入 ToolSpec 的 `has_side_effects`。命名不符合前缀但有副作用的工具会被错误并发执行。

**13. `chat_lite_governor` 速率限制是进程级字典**  
`_rate_limit_state: dict[str, list[float]]` 是 module 级 dict，多进程部署不共享，`_RATE_LIMIT_MAX_REQUESTS=15` 在每个进程独立计数。

**14. `RuntimeCapabilityRegistry` TTL 30min 过长**  
合成的 runtime capability 缓存 30 分钟，期间用户 intent 可能已变，但 `get_runtime_route_override` 仍会命中旧 spec。应缩短到 session 内或带更强的 session_id 绑定。

**15. `voice_event_log` 被多处业务复用**  
Sub-Agent 审计、Skill 审计、Tool 审计都写 `skill_exec_log`，但 `voice_event_log` 仍承担"对话历史"（这是上一份方案要处理的问题）。Ledger 表职责不清。

---

## 11. 总调用链路有序列表

```
1.  POST /v1/chat
2.  chat_handler.handle_chat
3.  _unified_chat_sse_generator
      ├─ thalamus.classify_and_compile_intent_draft
      └─ Governor.evaluate_intent（主 Governor）
4.  cortex_orchestrator_sse_generator
5.  _cortex_orchestrator_sse_generator_impl
      ├─ Airlock ingress + PII
      ├─ 4 fastpath（task_progress / recent_turn / clarification / phatic）
      ├─ _prefetch_retrieval_persona_inputs
      └─ retrieval_task + recall_pack
6.  executive_router_sse_generator
7.  executive_runtime._executive_runtime_core_sse_generator_impl
8.  executive_runtime_core._executive_runtime_core_impl
      ├─ Organ Gating (Immune/Proprio/Limbic/Prefrontal 并行)
      ├─ Prefrontal.plan_response（条件启用）
      ├─ _hard_rule_route（RULE_ROUTE_MAP + 硬规则）
      ├─ _should_force_complex_plan + capability_gap_detector
      └─ runtime_capability_synthesizer（若 gap）
9.  路由分发：
      ├─ chat    → _stream_chat_with_context → chat_agent_sse_generator
      ├─ skill   → _execute_skill → handler → dispatch_executor_payload → impl/*_executor
      ├─ delegate → _dispatch_to_sub_agent → SubAgentDispatcher.dispatch
      │              → handler (translate/intent/vision/repair)
      │              → intent_agent 内部走 run_react_loop
      │                  → GovernedToolNode (Governor + tool.func + Evidence)
      │                  → _tool_run_skill → _execute_skill → impl
      │                  → _tool_dispatch_sub_agent → 套娃
      ├─ plan    → _execute_skill_dag → run_plan_graph (LangGraph Send API)
      │              → task_exec_subgraph → dispatch_executor_payload / SubAgentDispatcher
      │              → synthesize_node（独立 LLM）
      ├─ recent_turn    → try_handle_recent_turn_direct_route
      └─ task_progress  → try_handle_task_progress_direct_route
10. 结果注入 chat_agent（sub_agent_context）→ LLM 重渲染 → 反思链 → Airlock → COMMIT
11. _async_post_process：审计持久化 + 情感状态更新 + TTS 触发
```

---

## 12. 给下游实现节点的参考要点

本节仅用于将来"工具调用链路重构"任务的参考，不属于本次审查的修复动作：

- **不要新建第六套工具抽象**，优先合并 Skill/Tool/MCP/ReAct 四套
- **SkillRegistry 应该是唯一的 SSOT**，ToolCatalog 改为 "给 LLM 可见性用的只读视图"，自动从 SkillRegistry 派生
- **plan_graph 的 skill 分支必须走 `_execute_skill`**，不能直接 `dispatch_executor_payload`
- **`plan_graph._synthesize_node` 的 LLM 调用需要接入对话历史重构方案的 `build_chat_messages`**，保持人格一致
- **GovernedToolNode 应该改用 `ToolBroker` 做实际执行**，这样 WRITE token 校验和审计口径才能统一
- **vision_agent 独立 handler** 必须接多模态 LLM（GPT-4V / Qwen-VL / Gemini-Vision 任一）
- **executor_registry** 应改为从 SkillRegistry 自动派生，不再维护硬编码列表

---

_END OF ANALYSIS_
